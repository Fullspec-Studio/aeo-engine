"""Catalog → buyer-intent + brand prompt sets (spec §4.1). Deduped, balanced, versioned."""
from pydantic import ValidationError

from aeo.config import JUDGE_MODEL_ID
from aeo.matching import normalize
from aeo.models import Product, PromptSpec, PromptType

PROPOSE_TOOL = {
    "toolSpec": {
        "name": "propose_prompts",
        "description": "Propose realistic shopper questions for testing AI answer engines.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"prompts": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": "string"},
                               "type": {"enum": ["product_intent", "brand_sov"]},
                               "category": {"type": "string"}},
                "required": ["text", "type"],
            }}},
            "required": ["prompts"],
        }},
    }
}


def dedup_and_balance(prompts: list[PromptSpec], per_category_cap: int = 10) -> list[PromptSpec]:
    seen: set[str] = set()
    counts: dict[str, int] = {}
    out = []
    for p in prompts:
        key = normalize(p.text)
        bucket = p.category if p.type == PromptType.product_intent else "__brand__"
        if key in seen or counts.get(bucket, 0) >= per_category_cap:
            continue
        seen.add(key)
        counts[bucket] = counts.get(bucket, 0) + 1
        out.append(p)
    return out


def generate_prompts(bedrock, products: list[Product], brand_names: list[str],
                     competitors: list[str], version: int, per_category_cap: int = 10) -> list[PromptSpec]:
    categories = sorted({p.category for p in products if p.category})
    sample = "\n".join(f"- [{p.category}] {p.title} (${p.price})" for p in products[:100])
    user = (
        f"Store brands: {', '.join(brand_names)}. Competitors: {', '.join(competitors)}.\n"
        f"Categories: {', '.join(categories)}.\nSample products:\n{sample}\n\n"
        f"Propose up to {per_category_cap} realistic buyer-intent questions per category "
        "(the kind a shopper asks an AI assistant — generic, never naming this store's brand) "
        "and up to 10 brand_sov questions about the store's brand and its competitors."
    )
    resp = bedrock.converse(
        modelId=JUDGE_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": user}]}],
        toolConfig={"tools": [PROPOSE_TOOL], "toolChoice": {"tool": {"name": "propose_prompts"}}},
        inferenceConfig={"temperature": 0.9},
    )
    raw = []
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            raw = block["toolUse"]["input"].get("prompts", [])
    specs = []
    for entry in raw:
        try:
            specs.append(PromptSpec(text=entry["text"], type=PromptType(entry["type"]),
                                    category=entry.get("category", ""), version=version))
        except (ValidationError, ValueError, KeyError):
            continue  # skip invalid entries, never fatal
    return dedup_and_balance(specs, per_category_cap)
