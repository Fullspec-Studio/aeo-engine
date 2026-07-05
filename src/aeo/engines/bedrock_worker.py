"""Samples each Bedrock model N times per prompt; archives raw responses to S3 first."""
import json
import time

from botocore.exceptions import ClientError

from aeo.config import TEMPERATURE
from aeo.models import EngineEnvelope, EngineSample

_MAX_RETRIES = 4


def _converse_once(bedrock, model_id: str, prompt_text: str, sleeper) -> str:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = bedrock.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt_text}]}],
                inferenceConfig={"temperature": TEMPERATURE},
            )
            return resp["output"]["message"]["content"][0]["text"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("ThrottlingException", "ServiceUnavailableException") or attempt == _MAX_RETRIES:
                raise
            sleeper(2 ** attempt)
    raise RuntimeError("unreachable")


def sample_models(bedrock, s3, bucket: str, run_id: int, prompt_id: int, prompt_text: str,
                  model_ids: list[str], n: int, sleeper=time.sleep) -> list[EngineEnvelope]:
    envelopes = []
    for model_id in model_ids:
        env = EngineEnvelope(prompt_text=prompt_text, engine="bedrock", model=model_id)
        for i in range(n):
            try:
                text = _converse_once(bedrock, model_id, prompt_text, sleeper)
            except ClientError:
                env.errors += 1
                continue
            key = f"runs/{run_id}/{prompt_id}/{model_id}/{i}.json"
            s3.put_object(Bucket=bucket, Key=key,
                          Body=json.dumps({"prompt": prompt_text, "model": model_id, "text": text}))
            env.samples.append(EngineSample(text=text, raw_s3_key=key))
        envelopes.append(env)
    return envelopes
