import os

import pytest

from aeo.db import repo
from aeo.models import Product, PromptSpec, PromptType

pytestmark = pytest.mark.integration

DSN = os.environ.get("AEO_TEST_DSN", "")


@pytest.fixture
def conn():
    if not DSN:
        pytest.skip("AEO_TEST_DSN not set")
    c = repo.connect(DSN)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    repo.apply_schema(c)
    yield c
    c.close()


def test_full_write_path(conn):
    sid = repo.upsert_store(conn, "demo", ["Acme Outdoor"], ["Merrell"])
    repo.replace_products(conn, sid, [Product(sku="ACME-TRAIL-2", title="Acme Trail 2")])
    [pid] = repo.insert_prompts(conn, sid, [PromptSpec(text="best boots", type=PromptType.product_intent)])
    rid = repo.create_run(conn, sid, "arn:test")
    oid = repo.insert_observation(
        conn, rid, pid, engine="bedrock", model="m", samples_total=5, samples_present=3,
        rank=2.0, sentiment="positive", framing="praised grip",
        competitors_named=["Merrell Moab"], citations=["rei.com"],
        confidence_flag="ok", raw_s3_keys=["runs/1/p/m/0.json"],
    )
    did = repo.insert_diagnosis(conn, oid, ["missing weight attribute"], "high")
    fid = repo.insert_fix_draft(conn, did, "attribute", '{"weight_g": "540"}')
    repo.finish_run(conn, rid, "complete", 1.0)
    assert all(isinstance(x, int) for x in (sid, pid, rid, oid, did, fid))


def test_upsert_store_is_idempotent(conn):
    a = repo.upsert_store(conn, "demo", ["Acme"], [])
    b = repo.upsert_store(conn, "demo", ["Acme Outdoor"], ["Merrell"])
    assert a == b


def test_replace_products_is_atomic(conn):
    sid = repo.upsert_store(conn, "demo", ["Acme"], [])
    repo.replace_products(conn, sid, [Product(sku="A", title="A one")])
    bad = [Product(sku="B", title="B one"), Product(sku="B", title="dup sku violates unique")]
    with pytest.raises(Exception):
        repo.replace_products(conn, sid, bad)
    with conn.cursor() as cur:
        cur.execute("SELECT sku FROM product WHERE store_id = %s", (sid,))
        assert [r[0] for r in cur.fetchall()] == ["A"]  # old row survived the failed replace
