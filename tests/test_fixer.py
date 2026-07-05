from unittest.mock import MagicMock

from aeo.diagnosis.fixer import Diagnosis, Refusal, diagnose
from aeo.models import Product

PRODUCT = Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot",
                  description="Waterproof leather boot.", attributes={"waterproof": "yes"})

GOOD = {"reasons": ["listing lacks weight attribute competitors expose"],
        "priority": "high",
        "fixes": [{"kind": "copy", "content": "Waterproof leather hiking boot with Vibram sole…"}]}


def _tool_response(tool_input, stop="tool_use"):
    return {"output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t", "name": "record_diagnosis", "input": tool_input}}
    ]}}, "stopReason": stop}


def test_valid_diagnosis():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response(GOOD)
    d = diagnose(bedrock, "gr-1", "1", PRODUCT, ["Buy Merrell."], ["Merrell Moab"])
    assert isinstance(d, Diagnosis) and d.priority == "high" and d.fixes[0].kind == "copy"


def test_guardrail_intervention_is_refusal():
    bedrock = MagicMock()
    bedrock.converse.return_value = {"output": {"message": {"content": [{"text": "blocked"}]}},
                                     "stopReason": "guardrail_intervened"}
    d = diagnose(bedrock, "gr-1", "1", PRODUCT, ["…"], ["Merrell"])
    assert isinstance(d, Refusal)


def test_unparseable_twice_returns_none():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response({"priority": "urgent"})
    d = diagnose(bedrock, "gr-1", "1", PRODUCT, ["…"], ["Merrell"])
    assert d is None and bedrock.converse.call_count == 2
