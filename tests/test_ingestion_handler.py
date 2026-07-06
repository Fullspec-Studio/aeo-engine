import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from aeo.ingestion import handler as h

FIXTURE = (Path(__file__).parent / "fixtures" / "catalog_push.json").read_text()


def test_valid_push_returns_200():
    with patch.object(h, "_conn_factory", return_value=MagicMock()), \
         patch.object(h.repo, "upsert_store", return_value=42), \
         patch.object(h.repo, "replace_products") as rp:
        resp = h.handler({"body": FIXTURE}, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["store_id"] == 42
    rp.assert_called_once()


def test_invalid_push_returns_422():
    resp = h.handler({"body": json.dumps({"store_key": "x"})}, None)
    assert resp["statusCode"] == 422


def test_conn_factory_uses_secret_arn_when_dsn_unset():
    """_conn_factory resolves AEO_DSN from Secrets Manager when only AEO_DSN_SECRET_ARN is set."""
    import os
    import importlib

    secret_payload = json.dumps({
        "username": "aeo",
        "password": "s3cr3t",
        "host": "cluster.us-east-1.rds.amazonaws.com",
        "port": "5432",
        "dbname": "aeo",
    })

    # Reload module to reset module-level _conn global
    import aeo.ingestion.handler as mod
    importlib.reload(mod)

    fake_sm = MagicMock()
    fake_sm.get_secret_value.return_value = {"SecretString": secret_payload}

    env = {k: v for k, v in os.environ.items() if k != "AEO_DSN"}
    env["AEO_DSN_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:aeo-dsn"

    # _conn_factory now calls repo.connect_with_retry (not repo.connect directly);
    # patch target updated to match the new call path.
    with patch.dict(os.environ, env, clear=True), \
         patch("boto3.client", return_value=fake_sm) as mock_client, \
         patch.object(mod.repo, "connect_with_retry", return_value=MagicMock()) as mock_connect:
        mod._conn_factory()

    mock_client.assert_called_once_with("secretsmanager")
    fake_sm.get_secret_value.assert_called_once_with(
        SecretId="arn:aws:secretsmanager:us-east-1:123:secret:aeo-dsn"
    )
    expected_dsn = "postgresql://aeo:s3cr3t@cluster.us-east-1.rds.amazonaws.com:5432/aeo"
    mock_connect.assert_called_once_with(expected_dsn)
