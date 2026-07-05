import json

import boto3
import httpx
import pytest
from moto import mock_aws
from unittest.mock import MagicMock

from aeo.engines.perplexity_worker import sample_perplexity


def _handler_ok(request):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "Try the Merrell Moab 3."}}],
        "citations": ["https://rei.com/x"],
    })


@pytest.fixture
def s3_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="aeo-raw")
        yield s3


def test_samples_and_archives_with_citations(s3_bucket):
    env = sample_perplexity("key", s3_bucket, "aeo-raw", 2, 9, "best boots", n=2,
                            transport=httpx.MockTransport(_handler_ok))
    assert env.engine == "perplexity" and len(env.samples) == 2 and env.errors == 0
    assert "SOURCES: https://rei.com/x" in env.samples[0].text
    raw = json.loads(s3_bucket.get_object(Bucket="aeo-raw", Key="runs/2/9/perplexity/0.json")["Body"].read())
    assert raw["citations"] == ["https://rei.com/x"]


def test_retries_on_429_then_gives_up(s3_bucket):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429)

    env = sample_perplexity("key", s3_bucket, "aeo-raw", 1, 1, "q", n=1,
                            transport=httpx.MockTransport(handler), sleeper=MagicMock())
    assert env.errors == 1 and env.samples == []
    assert calls["n"] == 5  # initial + 4 retries
