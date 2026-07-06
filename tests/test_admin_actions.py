"""TDD tests for admin actions dispatched via direct Lambda invoke.

Security contract: events containing `admin_action` AND `requestContext` (API Gateway
proxy shape) are rejected 403 — admin actions must never be reachable through the
public API.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from aeo.ingestion import handler as h
from aeo.models import Product, PromptSpec, PromptType


def _make_product():
    return Product(sku="SKU-1", title="Trail Shoe", description="Good shoe",
                   price=100.0, category="footwear", attributes={})


def _make_prompt():
    return PromptSpec(text="best trail shoes?", type=PromptType.product_intent,
                      category="footwear", version=1)


def test_apply_schema_direct_invoke():
    """Direct invoke: apply_schema dispatches to repo.apply_schema and returns ok."""
    mock_conn = MagicMock()
    with patch.object(h, "_conn_factory", return_value=mock_conn), \
         patch.object(h.repo, "apply_schema") as mock_apply:
        result = h.handler({"admin_action": "apply_schema"}, None)
    assert result == {"ok": True, "action": "apply_schema"}
    mock_apply.assert_called_once_with(mock_conn)


def test_seed_prompts_direct_invoke():
    """Direct invoke: seed_prompts calls generate_prompts + insert_prompts, returns count."""
    fake_products = [_make_product()]
    fake_prompts = [_make_prompt(), _make_prompt()]
    fake_conn = MagicMock()

    with patch.object(h, "_conn_factory", return_value=fake_conn), \
         patch.object(h, "_load_products", return_value=(fake_products, ["BrandA"], ["CompA"])), \
         patch.object(h, "generate_prompts", return_value=fake_prompts) as mock_gen, \
         patch.object(h.repo, "insert_prompts", return_value=[101, 102]) as mock_insert, \
         patch.object(h.boto3, "client", return_value=MagicMock()):
        result = h.handler({
            "admin_action": "seed_prompts",
            "store_key": "demo-outdoor-store",
            "version": 1,
            "per_category_cap": 10,
        }, None)

    assert result == {"ok": True, "action": "seed_prompts", "inserted": 2}
    mock_gen.assert_called_once()
    mock_insert.assert_called_once()


def test_admin_action_via_api_gateway_returns_403():
    """admin_action with requestContext (API Gateway shape) → 403, no side effects."""
    mock_conn = MagicMock()
    with patch.object(h, "_conn_factory", return_value=mock_conn), \
         patch.object(h.repo, "apply_schema") as mock_apply:
        result = h.handler({
            "admin_action": "apply_schema",
            "requestContext": {"resourcePath": "/catalog", "httpMethod": "POST"},
        }, None)

    assert result["statusCode"] == 403
    assert json.loads(result["body"]) == {"error": "admin actions not available via API"}
    mock_apply.assert_not_called()


def test_admin_unknown_action_returns_error():
    """Unknown admin action → ok=False with descriptive error message."""
    with patch.object(h, "_conn_factory", return_value=MagicMock()):
        result = h.handler({"admin_action": "reticulate_splines"}, None)
    assert result == {"ok": False, "error": "unknown action reticulate_splines"}


def test_normal_catalog_push_unaffected():
    """Standard CatalogPush flow still works; admin guard does not interfere."""
    fixture = (Path(__file__).parent / "fixtures" / "catalog_push.json").read_text()

    with patch.object(h, "_conn_factory", return_value=MagicMock()), \
         patch.object(h.repo, "upsert_store", return_value=55), \
         patch.object(h.repo, "replace_products") as rp:
        resp = h.handler({"body": fixture}, None)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["store_id"] == 55
    rp.assert_called_once()
