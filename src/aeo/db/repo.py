"""Thin data-access layer. All functions take an open psycopg connection."""
import json
from importlib import resources

import psycopg

from aeo.models import Product, PromptSpec


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def apply_schema(conn) -> None:
    sql = resources.files("aeo.db").joinpath("schema.sql").read_text()
    with conn.cursor() as cur:
        cur.execute(sql)


def upsert_store(conn, store_key: str, brand_names: list[str], competitors: list[str]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO store (store_key, brand_names, competitors)
               VALUES (%s, %s, %s)
               ON CONFLICT (store_key) DO UPDATE
                 SET brand_names = EXCLUDED.brand_names, competitors = EXCLUDED.competitors
               RETURNING id""",
            (store_key, json.dumps(brand_names), json.dumps(competitors)),
        )
        return cur.fetchone()[0]


def replace_products(conn, store_id: int, products: list[Product]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM product WHERE store_id = %s", (store_id,))
        for p in products:
            cur.execute(
                """INSERT INTO product (store_id, sku, title, description, price, category, attributes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (store_id, p.sku, p.title, p.description, p.price, p.category, json.dumps(p.attributes)),
            )


def insert_prompts(conn, store_id: int, prompts: list[PromptSpec]) -> list[int]:
    ids = []
    with conn.cursor() as cur:
        for ps in prompts:
            cur.execute(
                """INSERT INTO prompt (store_id, text, type, category, version)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (store_id, text, version) DO UPDATE SET active = TRUE
                   RETURNING id""",
                (store_id, ps.text, ps.type.value, ps.category, ps.version),
            )
            ids.append(cur.fetchone()[0])
    return ids


def create_run(conn, store_id: int, execution_arn: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run (store_id, execution_arn) VALUES (%s, %s) RETURNING id",
            (store_id, execution_arn),
        )
        return cur.fetchone()[0]


def finish_run(conn, run_id: int, status: str, coverage: float) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE run SET status = %s, coverage = %s WHERE id = %s", (status, coverage, run_id))


def insert_observation(conn, run_id, prompt_id, *, engine, model, samples_total, samples_present,
                       rank, sentiment, framing, competitors_named, citations,
                       confidence_flag, raw_s3_keys) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO observation
               (run_id, prompt_id, engine, model, samples_total, samples_present, rank,
                sentiment, framing, competitors_named, citations, confidence_flag, raw_s3_keys)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (run_id, prompt_id, engine, model, samples_total, samples_present, rank,
             sentiment, framing, json.dumps(competitors_named), json.dumps(citations),
             confidence_flag, json.dumps(raw_s3_keys)),
        )
        return cur.fetchone()[0]


def insert_diagnosis(conn, observation_id: int, reasons: list[str], priority: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO diagnosis (observation_id, reasons, priority) VALUES (%s, %s, %s) RETURNING id",
            (observation_id, json.dumps(reasons), priority),
        )
        return cur.fetchone()[0]


def insert_fix_draft(conn, diagnosis_id: int, kind: str, content: str,
                     status: str = "suggested", refusal_reason: str | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fix_draft (diagnosis_id, kind, content, status, refusal_reason)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (diagnosis_id, kind, content, status, refusal_reason),
        )
        return cur.fetchone()[0]
