"""Diagnosis + fix drafting behind Bedrock Guardrails (spec §4.3).
Drafts are suggestions only — the engine never applies them."""
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from aeo.config import JUDGE_MODEL_ID
from aeo.models import Product


class FixDraft(BaseModel):
    kind: Literal["copy", "schema", "qa", "attribute"]
    content: str


class Diagnosis(BaseModel):
    reasons: list[str] = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    fixes: list[FixDraft] = Field(default_factory=list)


@dataclass(frozen=True)
class Refusal:
    reason: str


DIAGNOSIS_TOOL = {
    "toolSpec": {
        "name": "record_diagnosis",
        "description": "Record why the product lost and draft fixes.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "reasons": {"type": "array", "items": {"type": "string"}},
                "priority": {"enum": ["high", "medium", "low"]},
                "fixes": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"kind": {"enum": ["copy", "schema", "qa", "attribute"]},
                                   "content": {"type": "string"}},
                    "required": ["kind", "content"],
                }},
            },
            "required": ["reasons", "priority"],
        }},
    }
}

_SYSTEM = (
    "You diagnose why an e-commerce product was not recommended by AI shopping answers, "
    "comparing what winning competitors offered against this product's actual data. "
    "HARD RULE: an 'attribute' fix may ONLY contain a key/value that already appears "
    "verbatim in the PRODUCT DATA attributes or description. If the winning competitors "
    "expose data (weight, ratings, certifications) that this product's data does NOT "
    "contain, you cannot know the value — state a reason prefixed 'insufficient_data:' "
    "naming the missing field, and propose NO attribute fix for it. Never invent, "
    "estimate, or placeholder a specification, certification, or claim. Recommending "
    "that the merchant ADD a missing field belongs in reasons, never in an attribute "
    "fix. Use the record_diagnosis tool."
)


def diagnose(bedrock, guardrail_id: str, guardrail_version: str, product: Product,
             losing_answers: list[str], winning_competitors: list[str]) -> Diagnosis | Refusal | None:
    user = (
        f"PRODUCT DATA:\n{product.model_dump_json(indent=1)}\n\n"
        f"WINNING COMPETITORS: {', '.join(winning_competitors)}\n\n"
        "ANSWERS WHERE THIS PRODUCT LOST:\n" + "\n---\n".join(losing_answers[:5])
    )
    messages = [{"role": "user", "content": [{"text": user}]}]
    for _ in range(2):  # initial + one reprompt
        kwargs: dict = {
            "modelId": JUDGE_MODEL_ID,
            "system": [{"text": _SYSTEM}],
            "messages": messages,
            "toolConfig": {"tools": [DIAGNOSIS_TOOL],
                           "toolChoice": {"tool": {"name": "record_diagnosis"}}},
            "inferenceConfig": {"temperature": 0.0},  # safety-critical: deterministic
        }
        # guardrail optional in v1 — provision and set AEO_GUARDRAIL_ID to enforce
        if guardrail_id:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": guardrail_id,
                "guardrailVersion": guardrail_version,
            }
        resp = bedrock.converse(**kwargs)
        if resp.get("stopReason") == "guardrail_intervened":
            return Refusal(reason="guardrail_intervened")
        tool_input = None
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                tool_input = block["toolUse"]["input"]
        if tool_input is None:
            return None
        try:
            return Diagnosis.model_validate(tool_input)
        except ValidationError as e:
            messages = messages + [
                {"role": "assistant", "content": [{"text": json.dumps(tool_input)}]},
                {"role": "user", "content": [{"text": f"Invalid output: {e}. Call the tool again correctly."}]},
            ]
    return None
