from unittest.mock import MagicMock

from aeo.models import Product, PromptSpec, PromptType
from aeo.prompts.generator import dedup_and_balance, generate_prompts


def _ps(text, cat="hiking-boots", t=PromptType.product_intent):
    return PromptSpec(text=text, type=t, category=cat)


def test_dedup_normalized_duplicates():
    out = dedup_and_balance([_ps("Best boots?"), _ps("best   boots"), _ps("other")])
    assert len(out) == 2


def test_category_cap():
    prompts = [_ps(f"q{i}") for i in range(15)] + [_ps("camp q", cat="camp-kitchen")]
    out = dedup_and_balance(prompts, per_category_cap=10)
    assert sum(1 for p in out if p.category == "hiking-boots") == 10
    assert sum(1 for p in out if p.category == "camp-kitchen") == 1


def test_generate_parses_tool_output_and_skips_invalid():
    bedrock = MagicMock()
    bedrock.converse.return_value = {"output": {"message": {"content": [{"toolUse": {
        "toolUseId": "t", "name": "propose_prompts", "input": {"prompts": [
            {"text": "best waterproof hiking boots under $150", "type": "product_intent", "category": "hiking-boots"},
            {"text": "is Acme Outdoor a reputable brand?", "type": "brand_sov", "category": ""},
            {"text": "bad entry", "type": "not_a_type", "category": ""},
        ]}}}]}}}
    out = generate_prompts(bedrock, [Product(sku="S", title="T", category="hiking-boots")],
                           ["Acme Outdoor"], ["Merrell"], version=3)
    assert len(out) == 2 and all(p.version == 3 for p in out)
    assert {p.type for p in out} == {PromptType.product_intent, PromptType.brand_sov}
