"""Platform-agnostic catalog ingestion contract — the seam catalog connectors target.
Changing this file requires updating tests/fixtures/catalog_push.json and every connector."""
from pydantic import BaseModel, Field

from aeo.models import Product


class CatalogPush(BaseModel):
    store_key: str
    brand_names: list[str] = Field(min_length=1)
    competitors: list[str] = Field(default_factory=list)
    products: list[Product] = Field(min_length=1)
