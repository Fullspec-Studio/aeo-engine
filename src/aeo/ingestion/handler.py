"""API Gateway ingestion endpoint — accepts CatalogPush from any catalog connector."""
import json
import os

import boto3
from pydantic import ValidationError

from aeo.db import repo
from aeo.ingestion.contract import CatalogPush

_conn = None


def _build_dsn_from_secret(arn: str) -> str:
    """Fetch Aurora secret from Secrets Manager and build a postgresql:// DSN."""
    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=arn)
    secret = json.loads(resp["SecretString"])
    return (
        f"postgresql://{secret['username']}:{secret['password']}"
        f"@{secret['host']}:{secret['port']}/{secret['dbname']}"
    )


def _conn_factory():
    global _conn
    if _conn is None:
        dsn = os.environ.get("AEO_DSN")
        if not dsn:
            arn = os.environ["AEO_DSN_SECRET_ARN"]
            dsn = _build_dsn_from_secret(arn)
        _conn = repo.connect(dsn)
    return _conn


def handler(event, context):
    try:
        push = CatalogPush.model_validate_json(event.get("body") or "")
    except ValidationError as e:
        return {"statusCode": 422, "body": json.dumps({"errors": e.errors(include_url=False)})}
    conn = _conn_factory()
    store_id = repo.upsert_store(conn, push.store_key, push.brand_names, push.competitors)
    repo.replace_products(conn, store_id, push.products)
    return {"statusCode": 200, "body": json.dumps({"store_id": store_id})}
