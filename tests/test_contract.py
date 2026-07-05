import json
from pathlib import Path

from aeo.ingestion.contract import CatalogPush

FIXTURE = Path(__file__).parent / "fixtures" / "catalog_push.json"


def test_contract_fixture_parses():
    """The frozen contract example — catalog connectors must produce this shape."""
    push = CatalogPush.model_validate_json(FIXTURE.read_text())
    assert push.store_key == "demo-outdoor-store"
    assert len(push.products) == 2
    assert "Merrell" in push.competitors


def test_contract_rejects_missing_store_key():
    data = json.loads(FIXTURE.read_text())
    del data["store_key"]
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CatalogPush.model_validate(data)
