import pytest
from pydantic import ValidationError

from aeo.models import EngineEnvelope, EngineSample, JudgeResult, Product, PromptSpec, PromptType


def test_product_defaults():
    p = Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot")
    assert p.attributes == {} and p.price is None


def test_judge_result_sentiment_restricted():
    with pytest.raises(ValidationError):
        JudgeResult(present=True, sentiment="ecstatic")


def test_judge_result_minimal_negative():
    j = JudgeResult(present=False, sentiment="neutral")
    assert j.matched_sku is None and j.competitors_named == []


def test_envelope_holds_samples():
    env = EngineEnvelope(
        prompt_text="best waterproof boots",
        engine="bedrock",
        model="anthropic.claude-3-5-haiku-20241022-v1:0",
        samples=[EngineSample(text="answer", raw_s3_key="runs/r1/p1/m/0.json")],
    )
    assert env.errors == 0 and len(env.samples) == 1


def test_prompt_spec_type_enum():
    ps = PromptSpec(text="is Acme reputable?", type=PromptType.brand_sov)
    assert ps.version == 1
