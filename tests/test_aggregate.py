import os

import pytest

from aeo.db import repo
from aeo.metrics import aggregate
from aeo.models import Product, PromptSpec, PromptType

pytestmark = pytest.mark.integration
DSN = os.environ.get("AEO_TEST_DSN", "")


@pytest.fixture
def seeded(request):
    if not DSN:
        pytest.skip("AEO_TEST_DSN not set")
    conn = repo.connect(DSN)
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    repo.apply_schema(conn)
    sid = repo.upsert_store(conn, "demo", ["Acme"], ["Merrell", "Salomon"])
    repo.replace_products(conn, sid, [Product(sku="S1", title="Acme Trail 2")])
    pids = repo.insert_prompts(conn, sid, [
        PromptSpec(text="best boots", type=PromptType.product_intent),
        PromptSpec(text="light stove", type=PromptType.product_intent),
    ])
    runs = []
    for arn in ("arn:1", "arn:2", "arn:3"):
        rid = repo.create_run(conn, sid, arn)
        for pid in pids:
            repo.insert_observation(
                conn, rid, pid, engine="bedrock", model="m-a", samples_total=5, samples_present=3,
                rank=2.0, sentiment="positive", framing="", competitors_named=["Merrell Moab"],
                citations=[], confidence_flag="ok", raw_s3_keys=[])
        repo.finish_run(conn, rid, "complete", 1.0)
        runs.append(rid)
    yield conn, sid, pids, runs
    conn.close()


def test_visibility_pools_run_observations(seeded):
    conn, sid, _, runs = seeded
    ri = aggregate.visibility(conn, sid, runs[-1])
    assert ri.rate == pytest.approx(0.6)          # 6/10 in the run
    assert (ri.high - ri.low) < 0.6               # tighter than a single 3/5


def test_share_of_voice_includes_store_and_competitors(seeded):
    conn, sid, _, runs = seeded
    sov = aggregate.share_of_voice(conn, sid, runs[-1])
    assert sov["__store__"].rate == pytest.approx(0.6)
    assert sov["Merrell"].rate == 1.0             # named in every observation
    assert sov["Salomon"].rate == 0.0


def test_rolling_prompt_rate_pools_three_runs(seeded):
    conn, _, pids, _ = seeded
    ri = aggregate.rolling_prompt_rate(conn, pids[0], "bedrock", "m-a")
    assert ri.rate == pytest.approx(0.6)          # 9/15 pooled
    single = aggregate.rolling_prompt_rate(conn, pids[0], "bedrock", "m-a", last_n_runs=1)
    assert (ri.high - ri.low) < (single.high - single.low)


def test_engine_breakdown_keys(seeded):
    conn, sid, _, runs = seeded
    bd = aggregate.engine_breakdown(conn, sid, runs[-1])
    assert set(bd) == {"bedrock:m-a"}
