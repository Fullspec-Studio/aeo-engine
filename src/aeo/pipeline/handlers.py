"""Lambda handlers for the Step Functions run pipeline (spec §2).
Thin orchestration only — all logic lives in the core library."""
import json
import os
import statistics
from collections import Counter

import boto3

from aeo import config
from aeo.analysis.comprehend_check import cross_check
from aeo.analysis.judge import judge_answer
from aeo.db import repo
from aeo.diagnosis.fixer import Diagnosis, Refusal, diagnose
from aeo.engines.bedrock_worker import sample_models
from aeo.engines.perplexity_worker import sample_perplexity
from aeo.models import EngineEnvelope, JudgeResult, Product


class CostCapExceeded(Exception):
    pass


_conn = None
_bedrock = _s3 = _comprehend = None
_dsn_cache: str | None = None
_perplexity_key: str | None = None


def _resolve_dsn() -> str:
    """Return AEO_DSN; if unset, fetch from Secrets Manager via AEO_DSN_SECRET_ARN (cached)."""
    global _dsn_cache
    dsn = os.environ.get("AEO_DSN")
    if dsn:
        return dsn
    if _dsn_cache:
        return _dsn_cache
    arn = os.environ["AEO_DSN_SECRET_ARN"]
    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=arn)
    secret = json.loads(resp["SecretString"])
    _dsn_cache = (
        f"postgresql://{secret['username']}:{secret['password']}"
        f"@{secret['host']}:{secret['port']}/{secret['dbname']}"
    )
    return _dsn_cache


def _get_perplexity_key() -> str:
    """Return PERPLEXITY_API_KEY; if unset, fetch from Secrets Manager via PERPLEXITY_SECRET_ARN (cached)."""
    global _perplexity_key
    if _perplexity_key is None:
        key = os.environ.get("PERPLEXITY_API_KEY")
        if not key:
            arn = os.environ["PERPLEXITY_SECRET_ARN"]
            resp = boto3.client("secretsmanager").get_secret_value(SecretId=arn)
            key = resp["SecretString"]
        _perplexity_key = key
    return _perplexity_key


def _clients():
    global _conn, _bedrock, _s3, _comprehend
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime")
        _s3 = boto3.client("s3")
        _comprehend = boto3.client("comprehend")
    if _conn is None:
        _conn = repo.connect(_resolve_dsn())
    return _conn, _bedrock, _s3, _comprehend


def _load_store_and_prompts(conn, store_key: str):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM store WHERE store_key = %s", (store_key,))
        store_id = cur.fetchone()[0]
        cur.execute("SELECT id, text FROM prompt WHERE store_id = %s AND active", (store_id,))
        prompts = [{"prompt_id": pid, "prompt_text": text} for pid, text in cur.fetchall()]
    return store_id, prompts


def _load_products(conn, store_id: int) -> tuple[list[Product], list[str], list[str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT sku, title, description, price, category, attributes "
                    "FROM product WHERE store_id = %s", (store_id,))
        products = [Product(sku=r[0], title=r[1], description=r[2],
                            price=float(r[3]) if r[3] is not None else None,
                            category=r[4], attributes=r[5]) for r in cur.fetchall()]
        cur.execute("SELECT brand_names, competitors FROM store WHERE id = %s", (store_id,))
        brands, competitors = cur.fetchone()
    return products, brands, competitors


def plan_run(event, context):
    conn, *_ = _clients()
    store_id, prompts = _load_store_and_prompts(conn, event["store_key"])
    n = int(event.get("samples_per_prompt", config.DEFAULT_SAMPLES_PER_PROMPT))
    total = len(prompts) * (len(config.BEDROCK_MODEL_IDS) + 1) * n
    if total > config.MAX_CALLS_PER_RUN:
        raise CostCapExceeded(f"{total} calls > cap {config.MAX_CALLS_PER_RUN}")
    run_id = repo.create_run(conn, store_id, event.get("execution_arn", ""))
    return {
        "run_id": run_id,
        "store_id": store_id,
        "samples_per_prompt": n,
        "batches": prompts,
        "expected_observations": len(prompts) * (len(config.BEDROCK_MODEL_IDS) + 1),
    }


def query_engines(event, context):
    _, bedrock, s3, _ = _clients()
    bucket = os.environ["AEO_RAW_BUCKET"]
    n = event["samples_per_prompt"]
    envs = sample_models(bedrock, s3, bucket, event["run_id"], event["prompt_id"],
                         event["prompt_text"], config.BEDROCK_MODEL_IDS, n)
    envs.append(sample_perplexity(_get_perplexity_key(), s3, bucket,
                                  event["run_id"], event["prompt_id"], event["prompt_text"], n))
    return {"run_id": event["run_id"], "store_id": event["store_id"],
            "prompt_id": event["prompt_id"],
            "envelopes": [e.model_dump() for e in envs]}


def _fold_envelope(env: EngineEnvelope, judgements: list[JudgeResult | None],
                   flags: list[str]) -> dict:
    present = [j for j in judgements if j and j.present]
    parseable = [j for j in judgements if j is not None]
    if judgements and not parseable:
        flag = "unparseable"
    elif any(f != "ok" for f in flags):
        flag = "low_confidence"
    else:
        flag = "ok"
    ranks = [j.rank for j in present if j.rank is not None]
    sentiments = Counter(j.sentiment for j in parseable)
    return {
        "engine": env.engine, "model": env.model,
        "samples_total": len(env.samples),
        "samples_present": len(present),
        "rank": statistics.median(ranks) if ranks else None,
        "sentiment": sentiments.most_common(1)[0][0] if sentiments else None,
        "framing": next((j.framing for j in present if j.framing), ""),
        "competitors_named": sorted({c for j in parseable for c in j.competitors_named}),
        "citations": sorted({c for j in parseable for c in j.citations}),
        "confidence_flag": flag,
        "raw_s3_keys": [s.raw_s3_key for s in env.samples],
    }


def analyze(event, context):
    conn, bedrock, _, comprehend = _clients()
    products, brands, competitors = _load_products(conn, event["store_id"])
    observations = []
    for env_dict in event["envelopes"]:
        env = EngineEnvelope.model_validate(env_dict)
        judgements, flags = [], []
        for sample in env.samples:
            jr = judge_answer(bedrock, sample.text, products, brands, competitors)
            judgements.append(jr)
            flags.append("unparseable" if jr is None
                         else cross_check(comprehend, sample.text, jr, brands))
        obs = _fold_envelope(env, judgements, flags)
        obs["prompt_id"] = event["prompt_id"]
        obs["losing_texts"] = [s.text for s, j in zip(env.samples, judgements)
                               if j is not None and not j.present][:5]
        observations.append(obs)
    return {"run_id": event["run_id"], "store_id": event["store_id"],
            "observations": observations}


def diagnose_and_draft(event, context):
    conn, bedrock, *_ = _clients()
    products, _, _ = _load_products(conn, event["store_id"])
    gr_id = os.environ.get("AEO_GUARDRAIL_ID", "")
    gr_ver = os.environ.get("AEO_GUARDRAIL_VERSION", "DRAFT")
    for obs in event["observations"]:
        if obs["samples_present"] == 0 and obs["confidence_flag"] == "ok" and obs["losing_texts"]:
            winners = obs["competitors_named"]
            product = products[0] if products else None  # v1: diagnose against representative product
            if product is None:
                continue
            result = diagnose(bedrock, gr_id, gr_ver, product, obs["losing_texts"], winners)
            if isinstance(result, Diagnosis):
                obs["diagnosis"] = result.model_dump()
            elif isinstance(result, Refusal):
                obs["diagnosis"] = {"refused": True, "reason": result.reason}
    return event


def persist(event, context):
    if event.get("failed"):
        conn, *_ = _clients()
        repo.finish_run(conn, event["run_id"], "failed", 0.0)
        return {"run_id": event["run_id"], "status": "failed", "coverage": 0.0}
    conn, *_ = _clients()
    run_id = event["run_id"]
    expected = event.get("expected_observations", 0)
    for item in event["items"]:
        for obs in item["observations"]:
            oid = repo.insert_observation(
                conn, run_id, obs["prompt_id"], engine=obs["engine"], model=obs["model"],
                samples_total=obs["samples_total"], samples_present=obs["samples_present"],
                rank=obs["rank"], sentiment=obs["sentiment"], framing=obs["framing"],
                competitors_named=obs["competitors_named"], citations=obs["citations"],
                confidence_flag=obs["confidence_flag"], raw_s3_keys=obs["raw_s3_keys"])
            diag = obs.get("diagnosis")
            if diag and not diag.get("refused"):
                did = repo.insert_diagnosis(conn, oid, diag["reasons"], diag["priority"])
                for fix in diag.get("fixes", []):
                    repo.insert_fix_draft(conn, did, fix["kind"], fix["content"])
            elif diag and diag.get("refused"):
                did = repo.insert_diagnosis(conn, oid, ["guardrail_refused"], "high")
                repo.insert_fix_draft(conn, did, "copy", "", status="refused",
                                      refusal_reason=diag["reason"])
    obs_with_samples = sum(
        1 for item in event["items"]
        for obs in item["observations"]
        if obs["samples_total"] > 0
    )
    coverage = obs_with_samples / expected if expected else 1.0
    status = "complete" if coverage >= 1.0 else "degraded"
    repo.finish_run(conn, run_id, status, coverage)
    return {"run_id": run_id, "status": status, "coverage": coverage}
