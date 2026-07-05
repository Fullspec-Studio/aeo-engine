from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PromptType(StrEnum):
    product_intent = "product_intent"
    brand_sov = "brand_sov"


class Product(BaseModel):
    sku: str
    title: str
    description: str = ""
    price: float | None = None
    category: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class PromptSpec(BaseModel):
    text: str
    type: PromptType
    category: str = ""
    version: int = 1


class EngineSample(BaseModel):
    text: str
    raw_s3_key: str


class EngineEnvelope(BaseModel):
    prompt_text: str
    engine: Literal["bedrock", "perplexity"]
    model: str
    samples: list[EngineSample] = Field(default_factory=list)
    errors: int = 0


class JudgeResult(BaseModel):
    present: bool
    matched_sku: str | None = None
    rank: int | None = None
    total_recommended: int | None = None
    sentiment: Literal["positive", "neutral", "negative"]
    framing: str = ""
    competitors_named: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
