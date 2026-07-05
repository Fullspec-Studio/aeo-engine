import json
from unittest.mock import MagicMock

from aeo.analysis.judge import judge_answer
from aeo.models import Product

PRODUCTS = [Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot")]
GOOD_INPUT = {
    "present": True, "matched_sku": "ACME-TRAIL-2", "rank": 2, "total_recommended": 5,
    "sentiment": "positive", "framing": "praised for grip",
    "competitors_named": ["Merrell Moab 3"], "citations": ["rei.com"],
}


def _tool_response(tool_input):
    return {"output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t1", "name": "record_observation", "input": tool_input}}
    ]}}, "stopReason": "tool_use"}


def test_valid_judgement_parsed():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response(GOOD_INPUT)
    jr = judge_answer(bedrock, "…the Acme Trail 2 is a great pick…", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr.present and jr.matched_sku == "ACME-TRAIL-2" and jr.rank == 2


def test_invalid_then_valid_reprompts_once():
    bedrock = MagicMock()
    bedrock.converse.side_effect = [
        _tool_response({"present": True, "sentiment": "ecstatic"}),  # invalid enum
        _tool_response(GOOD_INPUT),
    ]
    jr = judge_answer(bedrock, "…Acme Trail 2…", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr is not None and bedrock.converse.call_count == 2


def test_invalid_twice_returns_none_never_guesses():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response({"present": "maybe"})
    jr = judge_answer(bedrock, "answer", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr is None and bedrock.converse.call_count == 2


def test_matcher_overrides_hallucinated_presence():
    """Judge says present with a SKU, but the answer text has no catalog product."""
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response({**GOOD_INPUT, "matched_sku": "ACME-TRAIL-2"})
    jr = judge_answer(bedrock, "Only Merrell and Salomon are worth buying.", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr.present is False and jr.matched_sku is None
