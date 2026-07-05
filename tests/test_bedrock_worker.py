from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from aeo.engines.bedrock_worker import sample_models

THROTTLE = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")


def _converse_response(text):
    return {"output": {"message": {"content": [{"text": text}]}}}


@pytest.fixture
def s3_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="aeo-raw")
        yield s3


def test_samples_n_times_and_archives(s3_bucket):
    bedrock = MagicMock()
    bedrock.converse.return_value = _converse_response("recommend Acme Trail 2")
    envs = sample_models(bedrock, s3_bucket, "aeo-raw", 7, 3, "best boots", ["model-a"], n=5)
    assert len(envs) == 1 and len(envs[0].samples) == 5 and envs[0].errors == 0
    assert bedrock.converse.call_count == 5
    keys = [o["Key"] for o in s3_bucket.list_objects_v2(Bucket="aeo-raw")["Contents"]]
    assert "runs/7/3/model-a/0.json" in keys and len(keys) == 5


def test_throttle_retried_then_succeeds(s3_bucket):
    bedrock = MagicMock()
    bedrock.converse.side_effect = [THROTTLE, THROTTLE, _converse_response("ok")]
    sleeper = MagicMock()
    envs = sample_models(bedrock, s3_bucket, "aeo-raw", 1, 1, "q", ["m"], n=1, sleeper=sleeper)
    assert envs[0].errors == 0 and len(envs[0].samples) == 1
    assert sleeper.call_count == 2


def test_persistent_failure_counts_error_not_raise(s3_bucket):
    bedrock = MagicMock()
    bedrock.converse.side_effect = THROTTLE
    envs = sample_models(bedrock, s3_bucket, "aeo-raw", 1, 1, "q", ["m"], n=2, sleeper=MagicMock())
    assert envs[0].errors == 2 and envs[0].samples == []
