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
    fake_repo = MagicMock()
    with patch.object(handlers, "_load_store_and_prompts") as loader, \
         patch.object(handlers, "_conn", MagicMock()):
        loader.return_value = (1, [{"prompt_id": i, "prompt_text": f"q{i}"} for i in range(500)])
        with pytest.raises(CostCapExceeded):
            handlers.plan_run({"store_key": "demo"}, None)
        # 500 prompts × (3 bedrock models + 1 perplexity) × 5 samples = 10_000 > 4_000


def test_plan_run_returns_expected_observations():
    with patch.object(handlers, "_load_store_and_prompts") as loader, \
         patch.object(handlers, "repo") as mock_repo, \
         patch.object(handlers, "_conn", MagicMock()):
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
