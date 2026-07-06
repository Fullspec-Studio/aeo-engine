import json
import os

# Prevent NoRegionError / credential-chain errors when boto3 clients are constructed during tests.
# setdefault is safe: real credentials override these in production; static creds short-circuit
# the provider chain so LoginProvider (which needs botocore[crt]) is never reached.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from unittest.mock import MagicMock, patch

import pytest

from aeo.models import EngineEnvelope, EngineSample, JudgeResult
from aeo.pipeline import handlers
from aeo.pipeline.handlers import CostCapExceeded, _fold_envelope


def _sample(text="the Acme Trail 2 is great"):
    return EngineSample(text=text, raw_s3_key="k")


def test_fold_envelope_counts_and_median_rank():
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m",
                         samples=[_sample(), _sample(), _sample()])
    judgements = [
        JudgeResult(present=True, matched_sku="S", rank=1, sentiment="positive"),
        JudgeResult(present=True, matched_sku="S", rank=3, sentiment="positive"),
        JudgeResult(present=False, sentiment="neutral"),
    ]
    flags = ["ok", "ok", "ok"]
    obs = _fold_envelope(env, judgements, flags)
    assert obs["samples_total"] == 3 and obs["samples_present"] == 2
    assert obs["rank"] == 2.0 and obs["sentiment"] == "positive"
    assert obs["confidence_flag"] == "ok"


def test_fold_envelope_all_unparseable():
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m", samples=[_sample()])
    obs = _fold_envelope(env, [None], ["unparseable"])
    assert obs["confidence_flag"] == "unparseable" and obs["samples_present"] == 0


def test_fold_envelope_partial_unparseable_is_low_confidence():
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m",
                         samples=[_sample(), _sample()])
    judgements = [JudgeResult(present=True, matched_sku="S", rank=1, sentiment="positive"), None]
    obs = _fold_envelope(env, judgements, ["ok", "unparseable"])
    assert obs["confidence_flag"] == "low_confidence" and obs["samples_present"] == 1


def test_plan_run_enforces_cost_cap():
    # _resolve_dsn is now called unconditionally in _clients() so that ensure_alive
    # has the DSN available for reconnect — stub it to avoid KeyError in test env.
    with patch.object(handlers, "_load_store_and_prompts") as loader, \
         patch.object(handlers, "_conn", MagicMock()), \
         patch.object(handlers, "_resolve_dsn", return_value="postgresql://fake/test"):
        loader.return_value = (1, [{"prompt_id": i, "prompt_text": f"q{i}"} for i in range(500)])
        with pytest.raises(CostCapExceeded):
            handlers.plan_run({"store_key": "demo"}, None)
        # 500 prompts × (3 bedrock models + 1 perplexity) × 5 samples = 10_000 > 4_000


def test_plan_run_returns_expected_observations():
    # _resolve_dsn is now called unconditionally in _clients() so that ensure_alive
    # has the DSN available for reconnect — stub it to avoid KeyError in test env.
    with patch.object(handlers, "_load_store_and_prompts") as loader, \
         patch.object(handlers, "repo") as mock_repo, \
         patch.object(handlers, "_conn", MagicMock()), \
         patch.object(handlers, "_resolve_dsn", return_value="postgresql://fake/test"):
        loader.return_value = (1, [{"prompt_id": 1, "prompt_text": "q1"},
                                   {"prompt_id": 2, "prompt_text": "q2"}])
        mock_repo.create_run.return_value = 42
        result = handlers.plan_run({"store_key": "demo"}, None)
    # 2 prompts × (3 bedrock models + 1 perplexity) = 8
    assert result["expected_observations"] == 8


def test_fold_envelope_errors_excluded_from_samples_total():
    """Spec §5: engine errors must NOT inflate samples_total."""
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m",
                         samples=[_sample(), _sample()], errors=3)
    judgements = [
        JudgeResult(present=True, matched_sku="S", rank=1, sentiment="positive"),
        JudgeResult(present=False, sentiment="neutral"),
    ]
    obs = _fold_envelope(env, judgements, ["ok", "ok"])
    assert obs["samples_total"] == 2  # errors NOT counted


def test_perplexity_key_resolved_from_secret_arn(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:aeo/perplexity")
    handlers._perplexity_key = None
    fake_sm = MagicMock()
    fake_sm.get_secret_value.return_value = {"SecretString": "pplx-test-key"}
    with patch.object(handlers.boto3, "client", return_value=fake_sm):
        assert handlers._get_perplexity_key() == "pplx-test-key"
        assert handlers._get_perplexity_key() == "pplx-test-key"  # cached
    fake_sm.get_secret_value.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers shared by diagnose_and_draft / persist tests
# ---------------------------------------------------------------------------

def _make_obs(prompt_id=42, *, samples_present=0, losing_texts=None):
    """Return a minimal observation dict as produced by analyze()."""
    return {
        "engine": "bedrock",
        "model": "test-model",
        "prompt_id": prompt_id,
        "samples_total": 3,
        "samples_present": samples_present,
        "rank": None,
        "sentiment": "neutral",
        "framing": "",
        "competitors_named": ["CompX"],
        "citations": [],
        "confidence_flag": "ok",
        "raw_s3_keys": ["k1", "k2", "k3"],
        "losing_texts": losing_texts if losing_texts is not None else ["bad answer"],
    }


def _make_clients(*, mock_s3=None):
    """Return a (conn, bedrock, s3, comprehend) tuple of MagicMocks."""
    return (MagicMock(), MagicMock(), mock_s3 or MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# diagnose_and_draft — claim-check tests
# ---------------------------------------------------------------------------

def test_diagnose_and_draft_strips_losing_texts_and_returns_pointer(monkeypatch):
    """diagnose_and_draft must remove losing_texts from every obs and return only the slim pointer."""
    monkeypatch.setenv("AEO_RAW_BUCKET", "test-bucket")

    mock_s3 = MagicMock()
    event = {
        "run_id": 1,
        "store_id": 7,
        "observations": [_make_obs(42), _make_obs(42)],
    }

    with patch.object(handlers, "_clients", return_value=_make_clients(mock_s3=mock_s3)), \
         patch.object(handlers, "_load_products", return_value=([], [], [])):
        result = handlers.diagnose_and_draft(event, None)

    # Return is the slim pointer — no observation content.
    assert set(result.keys()) == {"run_id", "store_id", "prompt_id", "result_s3_key"}
    assert result["run_id"] == 1
    assert result["store_id"] == 7
    assert result["prompt_id"] == 42
    assert result["result_s3_key"] == "runs/1/results/42.json"

    # S3 put_object was called exactly once with correct bucket / key.
    mock_s3.put_object.assert_called_once()
    call_kw = mock_s3.put_object.call_args[1]
    assert call_kw["Bucket"] == "test-bucket"
    assert call_kw["Key"] == "runs/1/results/42.json"

    # Payload written to S3 has the full observations but NO losing_texts.
    payload = json.loads(call_kw["Body"])
    assert payload["run_id"] == 1
    assert len(payload["observations"]) == 2
    for obs in payload["observations"]:
        assert "losing_texts" not in obs


def test_diagnose_and_draft_no_losing_texts_obs_still_written(monkeypatch):
    """Even when no obs qualifies for diagnosis, the claim-check write still happens."""
    monkeypatch.setenv("AEO_RAW_BUCKET", "test-bucket")

    mock_s3 = MagicMock()
    # obs has samples_present=2 so diagnosis is skipped, but losing_texts must still be stripped.
    obs = _make_obs(10, samples_present=2, losing_texts=["some text"])
    event = {"run_id": 5, "store_id": 3, "observations": [obs]}

    with patch.object(handlers, "_clients", return_value=_make_clients(mock_s3=mock_s3)), \
         patch.object(handlers, "_load_products", return_value=([], [], [])):
        result = handlers.diagnose_and_draft(event, None)

    mock_s3.put_object.assert_called_once()
    payload = json.loads(mock_s3.put_object.call_args[1]["Body"])
    assert "losing_texts" not in payload["observations"][0]
    assert result["result_s3_key"] == "runs/5/results/10.json"


# ---------------------------------------------------------------------------
# persist — claim-check round-trip tests
# ---------------------------------------------------------------------------

def _s3_body_for(observations, *, run_id=1, store_id=7, prompt_id=42):
    """Return a MagicMock that mimics an S3 get_object response body."""
    raw = json.dumps({
        "run_id": run_id, "store_id": store_id, "prompt_id": prompt_id,
        "observations": observations,
    }).encode()
    body = MagicMock()
    body.read.return_value = raw
    return {"Body": body}


def test_persist_reads_from_s3_and_writes_to_db(monkeypatch):
    """persist must fetch observations from S3 and call repo insert functions."""
    monkeypatch.setenv("AEO_RAW_BUCKET", "test-bucket")

    obs = _make_obs(42, samples_present=2)
    obs.pop("losing_texts", None)  # already stripped by diagnose_and_draft

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = _s3_body_for([obs])
    mock_repo = MagicMock()
    mock_repo.insert_observation.return_value = 100

    event = {
        "run_id": 1,
        "store_id": 7,
        "expected_observations": 1,
        "items": [{"run_id": 1, "store_id": 7, "prompt_id": 42,
                   "result_s3_key": "runs/1/results/42.json"}],
    }

    with patch.object(handlers, "_clients", return_value=_make_clients(mock_s3=mock_s3)), \
         patch.object(handlers, "repo", mock_repo):
        result = handlers.persist(event, None)

    assert result["status"] == "complete"
    assert result["coverage"] == 1.0
    mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="runs/1/results/42.json")
    mock_repo.insert_observation.assert_called_once()
    mock_repo.finish_run.assert_called_once_with(mock_repo.finish_run.call_args[0][0], 1, "complete", 1.0)


def test_persist_writes_diagnosis_and_fix_draft_rows(monkeypatch):
    """persist must persist diagnosis + fix_draft rows when obs carries a diagnosis."""
    monkeypatch.setenv("AEO_RAW_BUCKET", "test-bucket")

    obs = _make_obs(42, samples_present=0)
    obs.pop("losing_texts", None)
    obs["diagnosis"] = {
        "reasons": ["no_brand_mention"], "priority": "high",
        "fixes": [{"kind": "copy", "content": "mention the brand"}],
    }

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = _s3_body_for([obs])
    mock_repo = MagicMock()
    mock_repo.insert_observation.return_value = 200
    mock_repo.insert_diagnosis.return_value = 300

    event = {
        "run_id": 1, "store_id": 7, "expected_observations": 1,
        "items": [{"run_id": 1, "store_id": 7, "prompt_id": 42,
                   "result_s3_key": "runs/1/results/42.json"}],
    }

    with patch.object(handlers, "_clients", return_value=_make_clients(mock_s3=mock_s3)), \
         patch.object(handlers, "repo", mock_repo):
        handlers.persist(event, None)

    mock_repo.insert_diagnosis.assert_called_once_with(
        mock_repo.insert_diagnosis.call_args[0][0], 200, ["no_brand_mention"], "high")
    mock_repo.insert_fix_draft.assert_called_once_with(
        mock_repo.insert_fix_draft.call_args[0][0], 300, "copy", "mention the brand")


def test_persist_skips_items_without_result_s3_key(monkeypatch):
    """Items that lack result_s3_key (defensive path) are silently skipped."""
    monkeypatch.setenv("AEO_RAW_BUCKET", "test-bucket")

    mock_s3 = MagicMock()
    mock_repo = MagicMock()

    # Two items: one valid pointer, one without result_s3_key.
    obs = _make_obs(42, samples_present=1)
    obs.pop("losing_texts", None)
    mock_s3.get_object.return_value = _s3_body_for([obs])

    event = {
        "run_id": 1, "store_id": 7, "expected_observations": 2,
        "items": [
            {"run_id": 1, "store_id": 7, "prompt_id": 42, "result_s3_key": "runs/1/results/42.json"},
            {"run_id": 1, "store_id": 7, "prompt_id": 99},  # no result_s3_key
        ],
    }

    with patch.object(handlers, "_clients", return_value=_make_clients(mock_s3=mock_s3)), \
         patch.object(handlers, "repo", mock_repo):
        result = handlers.persist(event, None)

    # Only 1 of 2 expected observations present → degraded
    assert result["status"] == "degraded"
    mock_s3.get_object.assert_called_once()


# ---------------------------------------------------------------------------
# persist — failure branch (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_persist_failure_branch_marks_run_failed():
    """persist({failed: True, ...}) must call finish_run('failed') without touching S3."""
    mock_s3 = MagicMock()
    mock_repo = MagicMock()

    event = {"run_id": 5, "failed": True}

    with patch.object(handlers, "_clients", return_value=_make_clients(mock_s3=mock_s3)), \
         patch.object(handlers, "repo", mock_repo):
        result = handlers.persist(event, None)

    assert result == {"run_id": 5, "status": "failed", "coverage": 0.0}
    # finish_run called with "failed" status
    args = mock_repo.finish_run.call_args[0]
    assert args[1] == 5 and args[2] == "failed" and args[3] == 0.0
    # S3 must not be touched in the failure branch
    mock_s3.get_object.assert_not_called()
