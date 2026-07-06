"""API Gateway ingestion endpoint — accepts CatalogPush from any catalog connector."""
import json
import os

import boto3
from pydantic import ValidationError

from aeo.db import repo
from aeo.ingestion.contract import CatalogPush
from aeo.pipeline.handlers import _load_products
from aeo.prompts.generator import generate_prompts

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


def _admin(event: dict) -> dict:
    """Handle admin actions invoked directly via Lambda (not via API Gateway)."""
    conn = _conn_factory()
    action = event["admin_action"]

    if action == "apply_schema":
        repo.apply_schema(conn)
        return {"ok": True, "action": "apply_schema"}

    if action == "seed_prompts":
        store_key = event["store_key"]
        version = event.get("version", 1)
        per_category_cap = event.get("per_category_cap", 10)

        # Resolve store_id from store_key
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM store WHERE store_key = %s", (store_key,))
            store_id = cur.fetchone()[0]

        # Load products, brands, competitors — reuse pipeline helper
        products, brands, competitors = _load_products(conn, store_id)

        # Generate prompts via Bedrock and insert into DB
        bedrock_client = boto3.client("bedrock-runtime")
        prompts = generate_prompts(bedrock_client, products, brands, competitors,
                                   version=version, per_category_cap=per_category_cap)
        ids = repo.insert_prompts(conn, store_id, prompts)
        return {"ok": True, "action": "seed_prompts", "inserted": len(ids)}

    return {"ok": False, "error": f"unknown action {action}"}


def handler(event, context):
    # Admin actions are only reachable via direct Lambda invoke (no API Gateway wrapping).
    # Reject any attempt to reach them through the public API.
    if "admin_action" in event:
        if "requestContext" in event:
            return {"statusCode": 403,
                    "body": '{"error": "admin actions not available via API"}'}
        return _admin(event)

    try:
        push = CatalogPush.model_validate_json(event.get("body") or "")
    except ValidationError as e:
        return {"statusCode": 422, "body": json.dumps({"errors": e.errors(include_url=False)})}
    conn = _conn_factory()
    store_id = repo.upsert_store(conn, push.store_key, push.brand_names, push.competitors)
    repo.replace_products(conn, store_id, push.products)
    return {"statusCode": 200, "body": json.dumps({"store_id": store_id})}
