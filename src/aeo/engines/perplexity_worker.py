"""Perplexity API worker — the one 'real surface' in v1 (spec §1)."""
import json
import time

import httpx

from aeo.models import EngineEnvelope, EngineSample

_API_URL = "https://api.perplexity.ai/chat/completions"
_MAX_RETRIES = 4


def _ask_once(client: httpx.Client, api_key: str, prompt_text: str, sleeper) -> tuple[str, list[str]]:
    for attempt in range(_MAX_RETRIES + 1):
        resp = client.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "sonar", "messages": [{"role": "user", "content": prompt_text}]},
        )
        if resp.status_code == 200:
            body = resp.json()
            return body["choices"][0]["message"]["content"], body.get("citations", [])
        if resp.status_code in (429, 500, 502, 503) and attempt < _MAX_RETRIES:
            sleeper(2 ** attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError("unreachable")


def sample_perplexity(api_key: str, s3, bucket: str, run_id: int, prompt_id: int,
                      prompt_text: str, n: int, transport=None, sleeper=time.sleep) -> EngineEnvelope:
    env = EngineEnvelope(prompt_text=prompt_text, engine="perplexity", model="sonar")
    with httpx.Client(transport=transport, timeout=60) as client:
        for i in range(n):
            try:
                text, citations = _ask_once(client, api_key, prompt_text, sleeper)
            except httpx.HTTPError:
                env.errors += 1
                continue
            key = f"runs/{run_id}/{prompt_id}/perplexity/{i}.json"
            s3.put_object(Bucket=bucket, Key=key,
                          Body=json.dumps({"prompt": prompt_text, "text": text, "citations": citations}))
            if citations:
                text = f"{text}\n\nSOURCES: {', '.join(citations)}"
            env.samples.append(EngineSample(text=text, raw_s3_key=key))
    return env
