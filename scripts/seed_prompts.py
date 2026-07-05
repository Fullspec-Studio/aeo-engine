#!/usr/bin/env python3
"""Seed prompts for a store into the AEO database.

Usage:
    uv run python scripts/seed_prompts.py demo-outdoor-store
    uv run python scripts/seed_prompts.py demo-outdoor-store --version 2 --cap 15
"""
import argparse
import boto3

from aeo.db import repo
from aeo.pipeline.handlers import _resolve_dsn
from aeo.prompts.generator import generate_prompts


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed prompts for a store into AEO database.")
    parser.add_argument("store_key", help="Store key (e.g. demo-outdoor-store)")
    parser.add_argument("--version", type=int, default=1, help="Prompt version (default: 1)")
    parser.add_argument("--cap", type=int, default=10,
                        help="Max prompts per category (default: 10)")
    args = parser.parse_args()

    dsn = _resolve_dsn()
    conn = repo.connect(dsn)

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM store WHERE store_key = %s", (args.store_key,))
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"Store '{args.store_key}' not found. Ingest a catalog first.")
        store_id = row[0]

        cur.execute("SELECT sku, title, description, price, category, attributes "
                    "FROM product WHERE store_id = %s", (store_id,))
        from aeo.models import Product
        products = [Product(sku=r[0], title=r[1], description=r[2],
                            price=float(r[3]) if r[3] is not None else None,
                            category=r[4], attributes=r[5]) for r in cur.fetchall()]

        cur.execute("SELECT brand_names, competitors FROM store WHERE id = %s", (store_id,))
        brands, competitors = cur.fetchone()

    bedrock = boto3.client("bedrock-runtime")
    prompts = generate_prompts(bedrock, products, brands, competitors,
                               version=args.version, per_category_cap=args.cap)
    ids = repo.insert_prompts(conn, store_id, prompts)
    print(f"Inserted {len(ids)} prompts for store '{args.store_key}' (version {args.version})")


if __name__ == "__main__":
    main()
