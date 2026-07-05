"""Structured-output LLM judge (spec §4.2). Forced tool schema; one reprompt; never guess."""
import json

from pydantic import ValidationError

from aeo.config import JUDGE_MODEL_ID, JUDGE_TEMPERATURE
from aeo.matching import match_product
from aeo.models import JudgeResult, Product

OBSERVATION_TOOL = {
    "toolSpec": {
        "name": "record_observation",
        "description": "Record the structured analysis of an AI shopping answer.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "matched_sku": {"type": ["string", "null"]},
                "rank": {"type": ["integer", "null"]},
                "total_recommended": {"type": ["integer", "null"]},
                "sentiment": {"enum": ["positive", "neutral", "negative"]},
                "framing": {"type": "string"},
                "competitors_named": {"type": "array", "items": {"type": "string"}},
                "citations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["present", "sentiment"],
        }},
    }
}


def _catalog_context(products: list[Product], brand_names: list[str], competitors: list[str]) -> str:
    lines = [f"- {p.sku}: {p.title}" for p in products[:200]]
    return (
        f"STORE BRANDS: {', '.join(brand_names)}\n"
        f"KNOWN COMPETITORS: {', '.join(competitors)}\n"
        f"CATALOG:\n" + "\n".join(lines)
    )


def _extract_tool_input(resp) -> dict | None:
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block and block["toolUse"]["name"] == "record_observation":
            return block["toolUse"]["input"]
    return None


def judge_answer(bedrock, answer_text: str, products: list[Product],
                 brand_names: list[str], competitors: list[str]) -> JudgeResult | None:
    system = (
        "You analyze an AI shopping answer against a store catalog. "
        "Determine whether any catalog product is recommended, its rank among all "
        "recommendations, sentiment and framing toward it, all competitor products named, "
        "and any cited sources. Use the record_observation tool. Be strict: only mark "
        "present=true when a catalog product is clearly recommended."
    )
    messages = [{"role": "user", "content": [{"text":
        _catalog_context(products, brand_names, competitors) + "\n\nANSWER TO ANALYZE:\n" + answer_text}]}]

    for attempt in range(2):  # initial + exactly one reprompt
        resp = bedrock.converse(
            modelId=JUDGE_MODEL_ID,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": [OBSERVATION_TOOL],
                        "toolChoice": {"tool": {"name": "record_observation"}}},
            inferenceConfig={"temperature": JUDGE_TEMPERATURE},
        )
        tool_input = _extract_tool_input(resp)
        if tool_input is not None:
            try:
                jr = JudgeResult.model_validate(tool_input)
            except ValidationError as e:
                messages = messages + [
                    {"role": "assistant", "content": [{"text": json.dumps(tool_input)}]},
                    {"role": "user", "content": [{"text": f"Invalid output: {e}. Call the tool again correctly."}]},
                ]
                continue
            # Matcher is authoritative for SKU identity (spec §4.2).
            if jr.present:
                verified = match_product(answer_text, products)
                if verified is None:
                    jr = jr.model_copy(update={"present": False, "matched_sku": None, "rank": None})
                elif verified != jr.matched_sku:
                    jr = jr.model_copy(update={"matched_sku": verified})
            return jr
    return None  # unparseable — caller records confidence_flag='unparseable'
