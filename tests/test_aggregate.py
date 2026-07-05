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


def test_rolling_rate_pools_all_rows_of_last_n_runs(seeded):
    conn, sid, pids, runs = seeded
    # second observation for the same (run, prompt, engine, model) in the last run
    repo.insert_observation(
        conn, runs[-1], pids[0], engine="bedrock", model="m-a", samples_total=5, samples_present=5,
        rank=1.0, sentiment="positive", framing="", competitors_named=[], citations=[],
        confidence_flag="ok", raw_s3_keys=[])
    ri = aggregate.rolling_prompt_rate(conn, pids[0], "bedrock", "m-a")
    # 3 runs x 3/5 plus the extra 5/5 row = 14/20
    assert ri.rate == pytest.approx(0.7)


def test_sov_escapes_ilike_wildcards(seeded):
    conn, sid, pids, runs = seeded
    # Add an observation with "Keen Targhee" so 'K_EN' ILIKE can wildcard-match it pre-fix
    repo.insert_observation(
        conn, runs[-1], pids[0], engine="bedrock", model="m-a", samples_total=5, samples_present=3,
        rank=1.0, sentiment="positive", framing="", competitors_named=["Keen Targhee"],
        citations=[], confidence_flag="ok", raw_s3_keys=[])
    with conn.cursor() as cur:
        cur.execute("UPDATE store SET competitors = %s WHERE id = %s", ('["K_EN"]', sid))
    sov = aggregate.share_of_voice(conn, sid, runs[-1])
    # "K_EN" must NOT wildcard-match "Keen Targhee" via '_'
    assert sov["K_EN"].rate == 0.0


def test_sov_always_has_store_key(seeded):
    conn, sid, _, _ = seeded
    empty_run = repo.create_run(conn, sid, "arn:empty")
    sov = aggregate.share_of_voice(conn, sid, empty_run)
    assert "__store__" in sov and sov["__store__"].rate == 0.0
