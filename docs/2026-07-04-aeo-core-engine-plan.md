# AEO Core Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the serverless AEO monitoring engine: ingest a store catalog, generate buyer-intent prompts, sample answer engines (Bedrock multi-model + Perplexity), judge responses into structured observations, diagnose losses and draft guardrailed fixes, aggregate statistically honest analytics, all gated by an eval harness.

**Architecture:** Pure-Python core library (`src/aeo/`) with thin Lambda handlers; Step Functions orchestrates runs; Aurora Serverless v2 (Postgres) stores structured results; S3 archives raw responses; CDK (Python) defines all infra. Spec: `docs/superpowers/specs/2026-07-03-aeo-monitoring-engine-design.md` (research folder).

**Tech Stack:** Python 3.12, uv, pydantic v2, boto3 (bedrock-runtime, comprehend, s3), httpx, psycopg 3, pytest, moto, AWS CDK v2 (Python).

## Global Constraints

- Sampling: N per prompt/engine is **configurable**; default `DEFAULT_SAMPLES_PER_PROMPT = 5`.
- Observations store **presence rates** (`samples_present`/`samples_total`), never booleans.
- Per-prompt single-run deltas are **never** findings; per-prompt trends pool the last 3 runs; dashboards use Wilson intervals and only badge change when intervals separate.
- Every raw engine response is archived to S3 before analysis; observations link via `raw_s3_keys`.
- Judge output is forced through a JSON tool schema; one reprompt on validation failure, then mark `unparseable` — never guess.
- Fix drafts are **never auto-applied**; status lifecycle is `suggested → approved | rejected`.
- Guardrail interventions are recorded as `refused` with reason, never dropped.
- Per-run hard cap on total model calls: `MAX_CALLS_PER_RUN = 4000`; exceeding halts the run.
- Prompts are versioned; trend queries only compare same-version prompts.
- All AWS access via boto3 clients injected as parameters (testability); no client construction inside logic functions.
- Bedrock model IDs live in `src/aeo/config.py` only — verify availability in the target region before deploy.

## File Structure

```
/Volumes/Tukar/Code/aeo-engine/
├── pyproject.toml
├── README.md
├── .github/workflows/ci.yml
├── docs/                        # spec + this plan, copied in Task 1
├── src/aeo/
│   ├── __init__.py
│   ├── config.py                # model IDs, N default, cost cap
│   ├── models.py                # pydantic domain models
│   ├── stats.py                 # Wilson intervals, pooling
│   ├── matching.py              # entity normalization + SKU matching
│   ├── ingestion/contract.py    # catalog push schema (the connector seam)
│   ├── db/schema.sql            # Postgres DDL
│   ├── db/repo.py               # data access (psycopg)
│   ├── engines/bedrock_worker.py
│   ├── engines/perplexity_worker.py
│   ├── analysis/judge.py        # LLM judge, forced tool schema
│   ├── analysis/comprehend_check.py
│   ├── prompts/generator.py     # prompt gen + dedup/balance
│   ├── diagnosis/fixer.py       # diagnosis + guardrailed fix drafts
│   ├── metrics/aggregate.py     # visibility, SoV, rolling windows
│   └── pipeline/handlers.py     # Lambda handlers (plan/query/analyze/diagnose/persist)
├── evals/
│   ├── fixtures/golden/*.json
│   ├── fixtures/fix_safety/*.json
│   ├── thresholds.json
│   └── run_evals.py
├── infra/
│   ├── app.py
│   └── stacks/{data_stack,api_stack,pipeline_stack}.py
└── tests/
```

Integration tests that need Postgres are marked `@pytest.mark.integration` and skipped unless `AEO_TEST_DSN` is set (local: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=aeo postgres:16`).

---

### Task 1: Repo scaffold + CI skeleton

**Files:**
- Create: `pyproject.toml`, `src/aeo/__init__.py`, `src/aeo/config.py`, `tests/test_config.py`, `.github/workflows/ci.yml`, `.gitignore`, `README.md`
- Copy in: `docs/2026-07-03-aeo-monitoring-engine-design.md`, `docs/2026-07-04-aeo-core-engine-plan.md` (from the research folder)

**Interfaces:**
- Produces: `aeo.config.DEFAULT_SAMPLES_PER_PROMPT: int = 5`, `MAX_CALLS_PER_RUN: int = 4000`, `BEDROCK_MODEL_IDS: list[str]`, `JUDGE_MODEL_ID: str` — all later tasks import from here.

- [ ] **Step 1: Scaffold repo**

```bash
mkdir -p /Volumes/Tukar/Code/aeo-engine && cd /Volumes/Tukar/Code/aeo-engine
git init -b main
uv init --name aeo --python 3.12 --lib   # then replace generated src layout below
mkdir -p src/aeo tests docs .github/workflows
cp /Volumes/Tukar/Code/research/docs/superpowers/specs/2026-07-03-aeo-monitoring-engine-design.md docs/
cp /Volumes/Tukar/Code/research/docs/superpowers/plans/2026-07-04-aeo-core-engine.md docs/2026-07-04-aeo-core-engine-plan.md
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "aeo"
version = "0.1.0"
description = "AEO / answer-engine monitoring engine"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "boto3>=1.34",
    "httpx>=0.27",
    "psycopg[binary]>=3.1",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "moto[s3]>=5.0",
    "aws-cdk-lib>=2.150",
    "constructs>=10.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: needs Postgres via AEO_TEST_DSN"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aeo"]
```

- [ ] **Step 3: Write the failing test** — `tests/test_config.py`

```python
from aeo import config


def test_config_constants():
    assert config.DEFAULT_SAMPLES_PER_PROMPT == 5
    assert config.MAX_CALLS_PER_RUN == 4000
    assert len(config.BEDROCK_MODEL_IDS) >= 2
    assert config.JUDGE_MODEL_ID
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError` / `AttributeError`)

- [ ] **Step 5: Write `src/aeo/__init__.py`** (empty) **and `src/aeo/config.py`**

```python
"""Central configuration. Model IDs are region-dependent — verify with
`aws bedrock list-foundation-models` before deploy."""

DEFAULT_SAMPLES_PER_PROMPT = 5
MAX_CALLS_PER_RUN = 4000

# Engines queried during monitoring runs.
BEDROCK_MODEL_IDS = [
    "anthropic.claude-3-5-haiku-20241022-v1:0",
    "meta.llama3-1-70b-instruct-v1:0",
    "mistral.mistral-large-2402-v1:0",
]

# Model used for judging/diagnosis (stronger than the sampled engines).
JUDGE_MODEL_ID = "anthropic.claude-sonnet-4-6"

TEMPERATURE = 0.7          # fixed sampling temperature (spec §5)
JUDGE_TEMPERATURE = 0.0    # judge must be deterministic
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Write `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run pytest -m "not integration" -v
```

- [ ] **Step 8: Write `.gitignore`** (standard Python: `.venv/`, `__pycache__/`, `.pytest_cache/`, `cdk.out/`, `*.egg-info/`) **and a stub `README.md`** (project name + one-paragraph description + link to `docs/` spec).

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "chore: scaffold aeo engine repo with config and CI"
```

---

### Task 2: Statistics module (Wilson intervals, pooling)

**Files:**
- Create: `src/aeo/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Produces: `RateInterval(rate, low, high)` frozen dataclass; `wilson_interval(successes: int, total: int, z: float = 1.96) -> RateInterval`; `pooled_rate(pairs: list[tuple[int, int]]) -> RateInterval`; `intervals_separate(a: RateInterval, b: RateInterval) -> bool`. Used by `metrics/aggregate.py` (Task 12).

- [ ] **Step 1: Write the failing test** — `tests/test_stats.py`

```python
import pytest
from aeo.stats import RateInterval, intervals_separate, pooled_rate, wilson_interval


def test_wilson_3_of_5_is_wide():
    ri = wilson_interval(3, 5)
    assert ri.rate == pytest.approx(0.6)
    assert ri.low < 0.25 and ri.high > 0.85  # spec §5: ~15%–85%


def test_wilson_bounds_clamped():
    ri = wilson_interval(0, 5)
    assert ri.low == 0.0 and ri.rate == 0.0
    ri = wilson_interval(5, 5)
    assert ri.high == 1.0 and ri.rate == 1.0


def test_wilson_zero_total_raises():
    with pytest.raises(ValueError):
        wilson_interval(1, 0)


def test_pooling_tightens_interval():
    single = wilson_interval(3, 5)
    pooled = pooled_rate([(3, 5), (3, 5), (3, 5)])  # 3-run rolling window
    assert pooled.rate == pytest.approx(0.6)
    assert (pooled.high - pooled.low) < (single.high - single.low)


def test_intervals_separate():
    a = wilson_interval(2, 100)
    b = wilson_interval(90, 100)
    assert intervals_separate(a, b)
    assert not intervals_separate(wilson_interval(3, 5), wilson_interval(4, 5))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stats.py -v`
Expected: FAIL (`ModuleNotFoundError: aeo.stats`)

- [ ] **Step 3: Write `src/aeo/stats.py`**

```python
"""Statistical primitives for presence-rate analytics (spec §5)."""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RateInterval:
    rate: float
    low: float
    high: float


def wilson_interval(successes: int, total: int, z: float = 1.96) -> RateInterval:
    if total <= 0:
        raise ValueError("total must be > 0")
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return RateInterval(rate=p, low=max(0.0, center - margin), high=min(1.0, center + margin))


def pooled_rate(pairs: list[tuple[int, int]]) -> RateInterval:
    """Pool (successes, total) pairs — e.g. a prompt's last 3 runs — into one interval."""
    return wilson_interval(sum(s for s, _ in pairs), sum(t for _, t in pairs))


def intervals_separate(a: RateInterval, b: RateInterval) -> bool:
    """True only when intervals don't overlap — the gate for 'improved/declined' badges."""
    return a.high < b.low or b.high < a.low
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stats.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_stats.py src/aeo/stats.py
git commit -m "feat: wilson intervals, pooling, separation gate"
```

---

### Task 3: Domain models + ingestion contract

**Files:**
- Create: `src/aeo/models.py`, `src/aeo/ingestion/__init__.py`, `src/aeo/ingestion/contract.py`, `tests/fixtures/catalog_push.json`
- Test: `tests/test_models.py`, `tests/test_contract.py`

**Interfaces:**
- Produces (in `aeo.models`): `PromptType` (StrEnum: `product_intent`, `brand_sov`), `Product(sku, title, description, price, category, attributes)`, `PromptSpec(text, type, category, version)`, `EngineSample(text, raw_s3_key)`, `EngineEnvelope(prompt_text, engine, model, samples, errors)`, `JudgeResult(present, matched_sku, rank, total_recommended, sentiment, framing, competitors_named, citations)`.
- Produces (in `aeo.ingestion.contract`): `CatalogPush(store_key, brand_names, competitors, products)` — **the connector seam**; `tests/fixtures/catalog_push.json` is the frozen contract example.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from aeo.models import EngineEnvelope, EngineSample, JudgeResult, Product, PromptSpec, PromptType


def test_product_defaults():
    p = Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot")
    assert p.attributes == {} and p.price is None


def test_judge_result_sentiment_restricted():
    with pytest.raises(ValidationError):
        JudgeResult(present=True, sentiment="ecstatic")


def test_judge_result_minimal_negative():
    j = JudgeResult(present=False, sentiment="neutral")
    assert j.matched_sku is None and j.competitors_named == []


def test_envelope_holds_samples():
    env = EngineEnvelope(
        prompt_text="best waterproof boots",
        engine="bedrock",
        model="anthropic.claude-3-5-haiku-20241022-v1:0",
        samples=[EngineSample(text="answer", raw_s3_key="runs/r1/p1/m/0.json")],
    )
    assert env.errors == 0 and len(env.samples) == 1


def test_prompt_spec_type_enum():
    ps = PromptSpec(text="is Acme reputable?", type=PromptType.brand_sov)
    assert ps.version == 1
```

`tests/test_contract.py`:

```python
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
```

- [ ] **Step 2: Write the contract fixture** — `tests/fixtures/catalog_push.json`

```json
{
  "store_key": "demo-outdoor-store",
  "brand_names": ["Acme Outdoor"],
  "competitors": ["Merrell", "Salomon", "KEEN"],
  "products": [
    {
      "sku": "ACME-TRAIL-2",
      "title": "Acme Trail 2 Hiking Boot",
      "description": "Waterproof leather hiking boot with Vibram sole.",
      "price": 139.99,
      "category": "hiking-boots",
      "attributes": {"waterproof": "yes", "weight_g": "540"}
    },
    {
      "sku": "ACME-CAMP-STOVE",
      "title": "Acme Ultralight Camp Stove",
      "description": "95g titanium canister stove.",
      "price": 44.5,
      "category": "camp-kitchen",
      "attributes": {"weight_g": "95", "fuel": "isobutane"}
    }
  ]
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_contract.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Write `src/aeo/models.py`**

```python
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PromptType(StrEnum):
    product_intent = "product_intent"
    brand_sov = "brand_sov"


class Product(BaseModel):
    sku: str
    title: str
    description: str = ""
    price: float | None = None
    category: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class PromptSpec(BaseModel):
    text: str
    type: PromptType
    category: str = ""
    version: int = 1


class EngineSample(BaseModel):
    text: str
    raw_s3_key: str


class EngineEnvelope(BaseModel):
    prompt_text: str
    engine: Literal["bedrock", "perplexity"]
    model: str
    samples: list[EngineSample] = Field(default_factory=list)
    errors: int = 0


class JudgeResult(BaseModel):
    present: bool
    matched_sku: str | None = None
    rank: int | None = None
    total_recommended: int | None = None
    sentiment: Literal["positive", "neutral", "negative"]
    framing: str = ""
    competitors_named: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
```

**and `src/aeo/ingestion/contract.py`** (plus empty `src/aeo/ingestion/__init__.py`):

```python
"""Platform-agnostic catalog ingestion contract — the seam catalog connectors target.
Changing this file requires updating tests/fixtures/catalog_push.json and every connector."""
from pydantic import BaseModel, Field

from aeo.models import Product


class CatalogPush(BaseModel):
    store_key: str
    brand_names: list[str] = Field(min_length=1)
    competitors: list[str] = Field(default_factory=list)
    products: list[Product] = Field(min_length=1)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_contract.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add src/aeo/models.py src/aeo/ingestion/ tests/test_models.py tests/test_contract.py tests/fixtures/catalog_push.json
git commit -m "feat: domain models and frozen ingestion contract"
```

---

### Task 4: Entity matching (mention → SKU)

**Files:**
- Create: `src/aeo/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Produces: `normalize(text: str) -> str`; `match_product(mention: str, products: list[Product]) -> str | None` (returns SKU or None). Used by judge post-processing (Task 8) and Comprehend cross-check (Task 9).

- [ ] **Step 1: Write the failing test** — `tests/test_matching.py`

```python
from aeo.matching import match_product, normalize
from aeo.models import Product

PRODUCTS = [
    Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot", category="hiking-boots"),
    Product(sku="ACME-CAMP-STOVE", title="Acme Ultralight Camp Stove"),
]


def test_normalize_strips_punctuation_case_and_roman():
    assert normalize("the Acme Trail II!") == "the acme trail 2"
    assert normalize("Acme's  Camp   Stove") == "acme camp stove"


def test_exact_title_match():
    assert match_product("Acme Trail 2 Hiking Boot", PRODUCTS) == "ACME-TRAIL-2"


def test_fuzzy_variants_match():
    assert match_product("the Acme Trail II", PRODUCTS) == "ACME-TRAIL-2"
    assert match_product("Acme's ultralight camp stove", PRODUCTS) == "ACME-CAMP-STOVE"


def test_competitor_does_not_match():
    assert match_product("Merrell Moab 3", PRODUCTS) is None


def test_generic_brand_mention_does_not_match_a_sku():
    # Brand alone is ambiguous between two products — must not match either.
    assert match_product("Acme", PRODUCTS) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_matching.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/matching.py`**

```python
"""Normalization + fuzzy matching of model-text mentions to catalog SKUs (spec §4).
Never naive string equality: handles case, punctuation, possessives, roman numerals."""
import re

from aeo.models import Product

_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5"}
_STOPWORDS = {"the", "a", "an", "boot", "boots", "shoe", "shoes"}


def normalize(text: str) -> str:
    t = text.lower().replace("'s", " ").replace("’s", " ")
    t = re.sub(r"[^\w\s]", " ", t)
    tokens = [_ROMAN.get(tok, tok) for tok in t.split()]
    return " ".join(tokens)


def _tokens(text: str) -> set[str]:
    return {tok for tok in normalize(text).split() if tok not in _STOPWORDS}


def match_product(mention: str, products: list[Product]) -> str | None:
    """Return the SKU whose title tokens best cover the mention (Jaccard >= 0.5),
    requiring at least 2 shared tokens so bare brand names never match a SKU."""
    m = _tokens(mention)
    if not m:
        return None
    best_sku, best_score = None, 0.0
    for p in products:
        pt = _tokens(p.title) | {normalize(p.sku).replace(" ", "-")}
        shared = m & pt
        if len(shared) < 2:
            continue
        score = len(shared) / len(m | pt)
        if score > best_score:
            best_sku, best_score = p.sku, score
    return best_sku if best_score >= 0.5 else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_matching.py -v`
Expected: PASS (5 tests). If the Jaccard threshold fails a case, adjust `_STOPWORDS`/threshold until all 5 pass — the tests are the contract, not the constants.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/matching.py tests/test_matching.py
git commit -m "feat: entity normalization and mention-to-sku matching"
```

---

### Task 5: Database schema + repository

**Files:**
- Create: `src/aeo/db/__init__.py`, `src/aeo/db/schema.sql`, `src/aeo/db/repo.py`
- Test: `tests/test_repo.py` (marked `integration`)

**Interfaces:**
- Produces: `repo.connect(dsn) -> psycopg.Connection`; `repo.apply_schema(conn)`; `repo.upsert_store(conn, store_key, brand_names, competitors) -> int`; `repo.replace_products(conn, store_id, products: list[Product])`; `repo.insert_prompts(conn, store_id, prompts: list[PromptSpec]) -> list[int]`; `repo.create_run(conn, store_id, execution_arn: str) -> int`; `repo.finish_run(conn, run_id, status: str, coverage: float)`; `repo.insert_observation(conn, run_id, prompt_id, engine, model, samples_total, samples_present, rank, sentiment, framing, competitors_named, citations, confidence_flag, raw_s3_keys) -> int`; `repo.insert_diagnosis(conn, observation_id, reasons: list[str], priority: str) -> int`; `repo.insert_fix_draft(conn, diagnosis_id, kind, content) -> int`.
- Table/column names below are relied on by Task 12's SQL.

- [ ] **Step 1: Write `src/aeo/db/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS store (
    id            SERIAL PRIMARY KEY,
    store_key     TEXT UNIQUE NOT NULL,
    brand_names   JSONB NOT NULL,
    competitors   JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product (
    id          SERIAL PRIMARY KEY,
    store_id    INT NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    sku         TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price       NUMERIC,
    category    TEXT NOT NULL DEFAULT '',
    attributes  JSONB NOT NULL DEFAULT '{}',
    UNIQUE (store_id, sku)
);

CREATE TABLE IF NOT EXISTS prompt (
    id        SERIAL PRIMARY KEY,
    store_id  INT NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    type      TEXT NOT NULL CHECK (type IN ('product_intent', 'brand_sov')),
    category  TEXT NOT NULL DEFAULT '',
    version   INT NOT NULL DEFAULT 1,
    active    BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (store_id, text, version)
);

CREATE TABLE IF NOT EXISTS run (
    id             SERIAL PRIMARY KEY,
    store_id       INT NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'complete', 'degraded', 'failed')),
    coverage       REAL,
    execution_arn  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS observation (
    id                SERIAL PRIMARY KEY,
    run_id            INT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    prompt_id         INT NOT NULL REFERENCES prompt(id),
    engine            TEXT NOT NULL,
    model             TEXT NOT NULL,
    samples_total     INT NOT NULL,
    samples_present   INT NOT NULL,
    rank              REAL,               -- median rank across present samples
    sentiment         TEXT,
    framing           TEXT NOT NULL DEFAULT '',
    competitors_named JSONB NOT NULL DEFAULT '[]',
    citations         JSONB NOT NULL DEFAULT '[]',
    confidence_flag   TEXT NOT NULL DEFAULT 'ok'
                      CHECK (confidence_flag IN ('ok', 'low_confidence', 'unparseable')),
    raw_s3_keys       JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS obs_run_idx ON observation (run_id);
CREATE INDEX IF NOT EXISTS obs_prompt_engine_idx ON observation (prompt_id, engine, model);

CREATE TABLE IF NOT EXISTS diagnosis (
    id             SERIAL PRIMARY KEY,
    observation_id INT NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
    reasons        JSONB NOT NULL,
    priority       TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low'))
);

CREATE TABLE IF NOT EXISTS fix_draft (
    id           SERIAL PRIMARY KEY,
    diagnosis_id INT NOT NULL REFERENCES diagnosis(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('copy', 'schema', 'qa', 'attribute')),
    content      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'suggested'
                 CHECK (status IN ('suggested', 'approved', 'rejected', 'refused')),
    refusal_reason TEXT
);
```

- [ ] **Step 2: Write the failing test** — `tests/test_repo.py`

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker run -d --name aeo-pg -p 5432:5432 -e POSTGRES_PASSWORD=aeo postgres:16` then
`AEO_TEST_DSN="postgresql://postgres:aeo@localhost:5432/postgres" uv run pytest tests/test_repo.py -v`
Expected: FAIL (`ModuleNotFoundError: aeo.db`)

- [ ] **Step 4: Write `src/aeo/db/repo.py`** (plus empty `src/aeo/db/__init__.py`)

```python
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
```

Add to `pyproject.toml` under `[tool.hatch.build.targets.wheel]`: nothing needed — but `schema.sql` must ship: add

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/aeo/db/schema.sql" = "aeo/db/schema.sql"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `AEO_TEST_DSN="postgresql://postgres:aeo@localhost:5432/postgres" uv run pytest tests/test_repo.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Verify unit CI still skips integration**

Run: `uv run pytest -m "not integration" -v`
Expected: PASS, `test_repo.py` deselected.

- [ ] **Step 7: Commit**

```bash
git add src/aeo/db/ tests/test_repo.py pyproject.toml
git commit -m "feat: postgres schema and repository layer"
```

---

### Task 6: Bedrock engine worker (sampling + S3 archive + backoff)

**Files:**
- Create: `src/aeo/engines/__init__.py`, `src/aeo/engines/bedrock_worker.py`
- Test: `tests/test_bedrock_worker.py`

**Interfaces:**
- Consumes: `EngineEnvelope`, `EngineSample` from `aeo.models`; `TEMPERATURE` from config.
- Produces: `sample_models(bedrock, s3, bucket: str, run_id: int, prompt_id: int, prompt_text: str, model_ids: list[str], n: int, sleeper=time.sleep) -> list[EngineEnvelope]` — one envelope per model; raw text archived to `runs/{run_id}/{prompt_id}/{model_id}/{i}.json` **before** being appended to the envelope. Throttling retried 4× with exponential backoff via injected `sleeper`; a sample that still fails increments `envelope.errors` instead of raising.

- [ ] **Step 1: Write the failing test** — `tests/test_bedrock_worker.py`

```python
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from aeo.engines.bedrock_worker import sample_models

THROTTLE = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")


def _converse_response(text):
    return {"output": {"message": {"content": [{"text": text}]}}}


@pytest.fixture
def s3_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="aeo-raw")
        yield s3


def test_samples_n_times_and_archives(s3_bucket):
    bedrock = MagicMock()
    bedrock.converse.return_value = _converse_response("recommend Acme Trail 2")
    envs = sample_models(bedrock, s3_bucket, "aeo-raw", 7, 3, "best boots", ["model-a"], n=5)
    assert len(envs) == 1 and len(envs[0].samples) == 5 and envs[0].errors == 0
    assert bedrock.converse.call_count == 5
    keys = [o["Key"] for o in s3_bucket.list_objects_v2(Bucket="aeo-raw")["Contents"]]
    assert "runs/7/3/model-a/0.json" in keys and len(keys) == 5


def test_throttle_retried_then_succeeds(s3_bucket):
    bedrock = MagicMock()
    bedrock.converse.side_effect = [THROTTLE, THROTTLE, _converse_response("ok")]
    sleeper = MagicMock()
    envs = sample_models(bedrock, s3_bucket, "aeo-raw", 1, 1, "q", ["m"], n=1, sleeper=sleeper)
    assert envs[0].errors == 0 and len(envs[0].samples) == 1
    assert sleeper.call_count == 2


def test_persistent_failure_counts_error_not_raise(s3_bucket):
    bedrock = MagicMock()
    bedrock.converse.side_effect = THROTTLE
    envs = sample_models(bedrock, s3_bucket, "aeo-raw", 1, 1, "q", ["m"], n=2, sleeper=MagicMock())
    assert envs[0].errors == 2 and envs[0].samples == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bedrock_worker.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/engines/bedrock_worker.py`** (plus empty `__init__.py`)

```python
"""Samples each Bedrock model N times per prompt; archives raw responses to S3 first."""
import json
import time

from botocore.exceptions import ClientError

from aeo.config import TEMPERATURE
from aeo.models import EngineEnvelope, EngineSample

_MAX_RETRIES = 4


def _converse_once(bedrock, model_id: str, prompt_text: str, sleeper) -> str:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = bedrock.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt_text}]}],
                inferenceConfig={"temperature": TEMPERATURE},
            )
            return resp["output"]["message"]["content"][0]["text"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("ThrottlingException", "ServiceUnavailableException") or attempt == _MAX_RETRIES:
                raise
            sleeper(2 ** attempt)
    raise RuntimeError("unreachable")


def sample_models(bedrock, s3, bucket: str, run_id: int, prompt_id: int, prompt_text: str,
                  model_ids: list[str], n: int, sleeper=time.sleep) -> list[EngineEnvelope]:
    envelopes = []
    for model_id in model_ids:
        env = EngineEnvelope(prompt_text=prompt_text, engine="bedrock", model=model_id)
        for i in range(n):
            try:
                text = _converse_once(bedrock, model_id, prompt_text, sleeper)
            except ClientError:
                env.errors += 1
                continue
            key = f"runs/{run_id}/{prompt_id}/{model_id}/{i}.json"
            s3.put_object(Bucket=bucket, Key=key,
                          Body=json.dumps({"prompt": prompt_text, "model": model_id, "text": text}))
            env.samples.append(EngineSample(text=text, raw_s3_key=key))
        envelopes.append(env)
    return envelopes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bedrock_worker.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/engines/ tests/test_bedrock_worker.py
git commit -m "feat: bedrock multi-model sampling worker with backoff and s3 archive"
```

---

### Task 7: Perplexity worker

**Files:**
- Create: `src/aeo/engines/perplexity_worker.py`
- Test: `tests/test_perplexity_worker.py`

**Interfaces:**
- Produces: `sample_perplexity(api_key: str, s3, bucket: str, run_id: int, prompt_id: int, prompt_text: str, n: int, transport: httpx.BaseTransport | None = None, sleeper=time.sleep) -> EngineEnvelope` — engine `"perplexity"`, model `"sonar"`; archives to `runs/{run_id}/{prompt_id}/perplexity/{i}.json`; retries 429/5xx up to 4× with backoff; persistent failure increments `errors`. Also extracts `citations` list from the API response into each archived raw JSON (`{"text": ..., "citations": [...]}`) — the judge reads citations from raw text context later, so include them in `EngineSample.text` suffixed as `\n\nSOURCES: url1, url2` when present.

- [ ] **Step 1: Write the failing test** — `tests/test_perplexity_worker.py`

```python
import json

import boto3
import httpx
import pytest
from moto import mock_aws
from unittest.mock import MagicMock

from aeo.engines.perplexity_worker import sample_perplexity


def _handler_ok(request):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "Try the Merrell Moab 3."}}],
        "citations": ["https://rei.com/x"],
    })


@pytest.fixture
def s3_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="aeo-raw")
        yield s3


def test_samples_and_archives_with_citations(s3_bucket):
    env = sample_perplexity("key", s3_bucket, "aeo-raw", 2, 9, "best boots", n=2,
                            transport=httpx.MockTransport(_handler_ok))
    assert env.engine == "perplexity" and len(env.samples) == 2 and env.errors == 0
    assert "SOURCES: https://rei.com/x" in env.samples[0].text
    raw = json.loads(s3_bucket.get_object(Bucket="aeo-raw", Key="runs/2/9/perplexity/0.json")["Body"].read())
    assert raw["citations"] == ["https://rei.com/x"]


def test_retries_on_429_then_gives_up(s3_bucket):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429)

    env = sample_perplexity("key", s3_bucket, "aeo-raw", 1, 1, "q", n=1,
                            transport=httpx.MockTransport(handler), sleeper=MagicMock())
    assert env.errors == 1 and env.samples == []
    assert calls["n"] == 5  # initial + 4 retries
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_perplexity_worker.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/engines/perplexity_worker.py`**

```python
"""Perplexity API worker — the one 'real surface' in v1 (spec §1)."""
import json
import time

import httpx

from aeo.models import EngineEnvelope, EngineSample

_API_URL = "https://api.perplexity.ai/chat/completions"
_MAX_RETRIES = 4


def _ask_once(client: httpx.Client, api_key: str, prompt_text: str, sleeper) -> tuple[str, list[str]]:
    for attempt in range(_MAX_RETRIES + 1):
        resp = client.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "sonar", "messages": [{"role": "user", "content": prompt_text}]},
        )
        if resp.status_code == 200:
            body = resp.json()
            return body["choices"][0]["message"]["content"], body.get("citations", [])
        if resp.status_code in (429, 500, 502, 503) and attempt < _MAX_RETRIES:
            sleeper(2 ** attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError("unreachable")


def sample_perplexity(api_key: str, s3, bucket: str, run_id: int, prompt_id: int,
                      prompt_text: str, n: int, transport=None, sleeper=time.sleep) -> EngineEnvelope:
    env = EngineEnvelope(prompt_text=prompt_text, engine="perplexity", model="sonar")
    with httpx.Client(transport=transport, timeout=60) as client:
        for i in range(n):
            try:
                text, citations = _ask_once(client, api_key, prompt_text, sleeper)
            except httpx.HTTPError:
                env.errors += 1
                continue
            key = f"runs/{run_id}/{prompt_id}/perplexity/{i}.json"
            s3.put_object(Bucket=bucket, Key=key,
                          Body=json.dumps({"prompt": prompt_text, "text": text, "citations": citations}))
            if citations:
                text = f"{text}\n\nSOURCES: {', '.join(citations)}"
            env.samples.append(EngineSample(text=text, raw_s3_key=key))
    return env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_perplexity_worker.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/engines/perplexity_worker.py tests/test_perplexity_worker.py
git commit -m "feat: perplexity worker with retry and citation capture"
```

---

### Task 8: LLM judge (forced tool schema, reprompt-once, never guess)

**Files:**
- Create: `src/aeo/analysis/__init__.py`, `src/aeo/analysis/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `JudgeResult`, `Product` from `aeo.models`; `match_product` from `aeo.matching`; `JUDGE_MODEL_ID`, `JUDGE_TEMPERATURE` from config.
- Produces: `judge_answer(bedrock, answer_text: str, products: list[Product], brand_names: list[str], competitors: list[str]) -> JudgeResult | None` — `None` means unparseable (after exactly one reprompt). Post-processing rule: if the raw judge output claims `present=True`, `matched_sku` is re-verified with `match_product`; on mismatch `matched_sku` is corrected to the matcher's answer, and if the matcher returns `None`, `present` is set to `False` (the matcher is authoritative for SKU identity). Also exports `OBSERVATION_TOOL: dict` (the Converse tool spec).

- [ ] **Step 1: Write the failing test** — `tests/test_judge.py`

```python
import json
from unittest.mock import MagicMock

from aeo.analysis.judge import judge_answer
from aeo.models import Product

PRODUCTS = [Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot")]
GOOD_INPUT = {
    "present": True, "matched_sku": "ACME-TRAIL-2", "rank": 2, "total_recommended": 5,
    "sentiment": "positive", "framing": "praised for grip",
    "competitors_named": ["Merrell Moab 3"], "citations": ["rei.com"],
}


def _tool_response(tool_input):
    return {"output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t1", "name": "record_observation", "input": tool_input}}
    ]}}, "stopReason": "tool_use"}


def test_valid_judgement_parsed():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response(GOOD_INPUT)
    jr = judge_answer(bedrock, "…the Acme Trail 2 is a great pick…", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr.present and jr.matched_sku == "ACME-TRAIL-2" and jr.rank == 2


def test_invalid_then_valid_reprompts_once():
    bedrock = MagicMock()
    bedrock.converse.side_effect = [
        _tool_response({"present": True, "sentiment": "ecstatic"}),  # invalid enum
        _tool_response(GOOD_INPUT),
    ]
    jr = judge_answer(bedrock, "…Acme Trail 2…", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr is not None and bedrock.converse.call_count == 2


def test_invalid_twice_returns_none_never_guesses():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response({"present": "maybe"})
    jr = judge_answer(bedrock, "answer", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr is None and bedrock.converse.call_count == 2


def test_matcher_overrides_hallucinated_presence():
    """Judge says present with a SKU, but the answer text has no catalog product."""
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response({**GOOD_INPUT, "matched_sku": "ACME-TRAIL-2"})
    jr = judge_answer(bedrock, "Only Merrell and Salomon are worth buying.", PRODUCTS, ["Acme"], ["Merrell"])
    assert jr.present is False and jr.matched_sku is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_judge.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/analysis/judge.py`** (plus empty `__init__.py`)

```python
"""Structured-output LLM judge (spec §4.2). Forced tool schema; one reprompt; never guess."""
import json

from pydantic import ValidationError

from aeo.config import JUDGE_MODEL_ID, JUDGE_TEMPERATURE
from aeo.matching import match_product
from aeo.models import JudgeResult, Product

OBSERVATION_TOOL = {
    "toolSpec": {
        "name": "record_observation",
        "description": "Record the structured analysis of an AI shopping answer.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "matched_sku": {"type": ["string", "null"]},
                "rank": {"type": ["integer", "null"]},
                "total_recommended": {"type": ["integer", "null"]},
                "sentiment": {"enum": ["positive", "neutral", "negative"]},
                "framing": {"type": "string"},
                "competitors_named": {"type": "array", "items": {"type": "string"}},
                "citations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["present", "sentiment"],
        }},
    }
}


def _catalog_context(products: list[Product], brand_names: list[str], competitors: list[str]) -> str:
    lines = [f"- {p.sku}: {p.title}" for p in products[:200]]
    return (
        f"STORE BRANDS: {', '.join(brand_names)}\n"
        f"KNOWN COMPETITORS: {', '.join(competitors)}\n"
        f"CATALOG:\n" + "\n".join(lines)
    )


def _extract_tool_input(resp) -> dict | None:
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block and block["toolUse"]["name"] == "record_observation":
            return block["toolUse"]["input"]
    return None


def judge_answer(bedrock, answer_text: str, products: list[Product],
                 brand_names: list[str], competitors: list[str]) -> JudgeResult | None:
    system = (
        "You analyze an AI shopping answer against a store catalog. "
        "Determine whether any catalog product is recommended, its rank among all "
        "recommendations, sentiment and framing toward it, all competitor products named, "
        "and any cited sources. Use the record_observation tool. Be strict: only mark "
        "present=true when a catalog product is clearly recommended."
    )
    messages = [{"role": "user", "content": [{"text":
        _catalog_context(products, brand_names, competitors) + "\n\nANSWER TO ANALYZE:\n" + answer_text}]}]

    for attempt in range(2):  # initial + exactly one reprompt
        resp = bedrock.converse(
            modelId=JUDGE_MODEL_ID,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": [OBSERVATION_TOOL],
                        "toolChoice": {"tool": {"name": "record_observation"}}},
            inferenceConfig={"temperature": JUDGE_TEMPERATURE},
        )
        tool_input = _extract_tool_input(resp)
        if tool_input is not None:
            try:
                jr = JudgeResult.model_validate(tool_input)
            except ValidationError as e:
                messages = messages + [
                    {"role": "assistant", "content": [{"text": json.dumps(tool_input)}]},
                    {"role": "user", "content": [{"text": f"Invalid output: {e}. Call the tool again correctly."}]},
                ]
                continue
            # Matcher is authoritative for SKU identity (spec §4.2).
            if jr.present:
                verified = match_product(answer_text, products)
                if verified is None:
                    jr = jr.model_copy(update={"present": False, "matched_sku": None, "rank": None})
                elif verified != jr.matched_sku:
                    jr = jr.model_copy(update={"matched_sku": verified})
            return jr
    return None  # unparseable — caller records confidence_flag='unparseable'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_judge.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/analysis/ tests/test_judge.py
git commit -m "feat: llm judge with forced tool schema and matcher verification"
```

---

### Task 9: Comprehend cross-check

**Files:**
- Create: `src/aeo/analysis/comprehend_check.py`
- Test: `tests/test_comprehend_check.py`

**Interfaces:**
- Consumes: `JudgeResult` from `aeo.models`; `normalize` from `aeo.matching`.
- Produces: `cross_check(comprehend, answer_text: str, judge: JudgeResult, brand_names: list[str]) -> str` returning a `confidence_flag` value: `"ok"` or `"low_confidence"`. Rules: (a) if judge sentiment is `positive`/`negative` and Comprehend's dominant sentiment (score > 0.7) is the opposite polarity → low confidence; (b) if `judge.present` is True but no normalized brand name appears in the normalized answer text → low confidence.

- [ ] **Step 1: Write the failing test** — `tests/test_comprehend_check.py`

```python
from unittest.mock import MagicMock

from aeo.analysis.comprehend_check import cross_check
from aeo.models import JudgeResult


def _comprehend(sentiment, score):
    c = MagicMock()
    c.detect_sentiment.return_value = {
        "Sentiment": sentiment,
        "SentimentScore": {sentiment.capitalize(): score},
    }
    return c


def _jr(present=True, sentiment="positive"):
    return JudgeResult(present=present, matched_sku="ACME-TRAIL-2" if present else None, sentiment=sentiment)


def test_agreement_is_ok():
    flag = cross_check(_comprehend("POSITIVE", 0.95), "The Acme Trail 2 is excellent.", _jr(), ["Acme"])
    assert flag == "ok"


def test_polar_disagreement_flags():
    flag = cross_check(_comprehend("NEGATIVE", 0.9), "Acme boots fall apart.", _jr(sentiment="positive"), ["Acme"])
    assert flag == "low_confidence"


def test_weak_comprehend_signal_does_not_flag():
    flag = cross_check(_comprehend("NEGATIVE", 0.5), "Acme is fine.", _jr(sentiment="positive"), ["Acme"])
    assert flag == "ok"


def test_present_without_brand_in_text_flags():
    flag = cross_check(_comprehend("POSITIVE", 0.9), "Merrell is the best choice.", _jr(), ["Acme"])
    assert flag == "low_confidence"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_comprehend_check.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/analysis/comprehend_check.py`**

```python
"""Independent Comprehend backstop for the LLM judge (spec §4.2)."""
from aeo.matching import normalize
from aeo.models import JudgeResult

_OPPOSITE = {"positive": "NEGATIVE", "negative": "POSITIVE"}


def cross_check(comprehend, answer_text: str, judge: JudgeResult, brand_names: list[str]) -> str:
    if judge.present and not any(normalize(b) in normalize(answer_text) for b in brand_names):
        return "low_confidence"
    opposite = _OPPOSITE.get(judge.sentiment)
    if opposite:
        resp = comprehend.detect_sentiment(Text=answer_text[:4900], LanguageCode="en")
        score = resp["SentimentScore"].get(opposite.capitalize(), 0.0)
        if resp["Sentiment"] == opposite and score > 0.7:
            return "low_confidence"
    return "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_comprehend_check.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/analysis/comprehend_check.py tests/test_comprehend_check.py
git commit -m "feat: comprehend sentiment/presence cross-check"
```

---

### Task 10: Prompt generator (Bedrock gen + pure dedup/balance)

**Files:**
- Create: `src/aeo/prompts/__init__.py`, `src/aeo/prompts/generator.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: `Product`, `PromptSpec`, `PromptType` from `aeo.models`; `normalize` from `aeo.matching`; `JUDGE_MODEL_ID` from config (generation uses the strong model).
- Produces: `dedup_and_balance(prompts: list[PromptSpec], per_category_cap: int = 10) -> list[PromptSpec]` (pure — drops normalized-text duplicates, caps per category, `brand_sov` prompts have category `""` and their own cap bucket); `generate_prompts(bedrock, products: list[Product], brand_names: list[str], competitors: list[str], version: int, per_category_cap: int = 10) -> list[PromptSpec]`. Generation asks the model for a JSON array via a forced tool (`propose_prompts` with `{"prompts": [{"text", "type", "category"}]}` schema), validates each entry into `PromptSpec` with the given `version`, then runs `dedup_and_balance`. Invalid entries are skipped, not fatal.

- [ ] **Step 1: Write the failing test** — `tests/test_generator.py`

```python
from unittest.mock import MagicMock

from aeo.models import Product, PromptSpec, PromptType
from aeo.prompts.generator import dedup_and_balance, generate_prompts


def _ps(text, cat="hiking-boots", t=PromptType.product_intent):
    return PromptSpec(text=text, type=t, category=cat)


def test_dedup_normalized_duplicates():
    out = dedup_and_balance([_ps("Best boots?"), _ps("best   boots"), _ps("other")])
    assert len(out) == 2


def test_category_cap():
    prompts = [_ps(f"q{i}") for i in range(15)] + [_ps("camp q", cat="camp-kitchen")]
    out = dedup_and_balance(prompts, per_category_cap=10)
    assert sum(1 for p in out if p.category == "hiking-boots") == 10
    assert sum(1 for p in out if p.category == "camp-kitchen") == 1


def test_generate_parses_tool_output_and_skips_invalid():
    bedrock = MagicMock()
    bedrock.converse.return_value = {"output": {"message": {"content": [{"toolUse": {
        "toolUseId": "t", "name": "propose_prompts", "input": {"prompts": [
            {"text": "best waterproof hiking boots under $150", "type": "product_intent", "category": "hiking-boots"},
            {"text": "is Acme Outdoor a reputable brand?", "type": "brand_sov", "category": ""},
            {"text": "bad entry", "type": "not_a_type", "category": ""},
        ]}}}]}}}
    out = generate_prompts(bedrock, [Product(sku="S", title="T", category="hiking-boots")],
                           ["Acme Outdoor"], ["Merrell"], version=3)
    assert len(out) == 2 and all(p.version == 3 for p in out)
    assert {p.type for p in out} == {PromptType.product_intent, PromptType.brand_sov}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_generator.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/prompts/generator.py`** (plus empty `__init__.py`)

```python
"""Catalog → buyer-intent + brand prompt sets (spec §4.1). Deduped, balanced, versioned."""
from pydantic import ValidationError

from aeo.config import JUDGE_MODEL_ID
from aeo.matching import normalize
from aeo.models import Product, PromptSpec, PromptType

PROPOSE_TOOL = {
    "toolSpec": {
        "name": "propose_prompts",
        "description": "Propose realistic shopper questions for testing AI answer engines.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"prompts": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": "string"},
                               "type": {"enum": ["product_intent", "brand_sov"]},
                               "category": {"type": "string"}},
                "required": ["text", "type"],
            }}},
            "required": ["prompts"],
        }},
    }
}


def dedup_and_balance(prompts: list[PromptSpec], per_category_cap: int = 10) -> list[PromptSpec]:
    seen: set[str] = set()
    counts: dict[str, int] = {}
    out = []
    for p in prompts:
        key = normalize(p.text)
        bucket = p.category if p.type == PromptType.product_intent else "__brand__"
        if key in seen or counts.get(bucket, 0) >= per_category_cap:
            continue
        seen.add(key)
        counts[bucket] = counts.get(bucket, 0) + 1
        out.append(p)
    return out


def generate_prompts(bedrock, products: list[Product], brand_names: list[str],
                     competitors: list[str], version: int, per_category_cap: int = 10) -> list[PromptSpec]:
    categories = sorted({p.category for p in products if p.category})
    sample = "\n".join(f"- [{p.category}] {p.title} (${p.price})" for p in products[:100])
    user = (
        f"Store brands: {', '.join(brand_names)}. Competitors: {', '.join(competitors)}.\n"
        f"Categories: {', '.join(categories)}.\nSample products:\n{sample}\n\n"
        f"Propose up to {per_category_cap} realistic buyer-intent questions per category "
        "(the kind a shopper asks an AI assistant — generic, never naming this store's brand) "
        "and up to 10 brand_sov questions about the store's brand and its competitors."
    )
    resp = bedrock.converse(
        modelId=JUDGE_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": user}]}],
        toolConfig={"tools": [PROPOSE_TOOL], "toolChoice": {"tool": {"name": "propose_prompts"}}},
        inferenceConfig={"temperature": 0.9},
    )
    raw = []
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            raw = block["toolUse"]["input"].get("prompts", [])
    specs = []
    for entry in raw:
        try:
            specs.append(PromptSpec(text=entry["text"], type=PromptType(entry["type"]),
                                    category=entry.get("category", ""), version=version))
        except (ValidationError, ValueError, KeyError):
            continue  # skip invalid entries, never fatal
    return dedup_and_balance(specs, per_category_cap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_generator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/prompts/ tests/test_generator.py
git commit -m "feat: prompt generation with dedup, category balancing, versioning"
```

---

### Task 11: Diagnosis + guardrailed fix drafting

**Files:**
- Create: `src/aeo/diagnosis/__init__.py`, `src/aeo/diagnosis/fixer.py`
- Test: `tests/test_fixer.py`

**Interfaces:**
- Consumes: `Product` from `aeo.models`; `JUDGE_MODEL_ID` from config.
- Produces: `FixDraft(kind: Literal["copy","schema","qa","attribute"], content: str)` and `Diagnosis(reasons: list[str], priority: Literal["high","medium","low"], fixes: list[FixDraft])` pydantic models; `Refusal(reason: str)` dataclass; `diagnose(bedrock, guardrail_id: str, guardrail_version: str, product: Product, losing_answers: list[str], winning_competitors: list[str]) -> Diagnosis | Refusal | None` — `Refusal` when `stopReason == "guardrail_intervened"`; `None` when unparseable (one reprompt, same policy as judge). System prompt hard rule: *only propose attribute values verifiable from the provided product data; if data is insufficient, return reasons with an `insufficient_data:` prefix and NO fixes of kind `attribute`.*

- [ ] **Step 1: Write the failing test** — `tests/test_fixer.py`

```python
from unittest.mock import MagicMock

from aeo.diagnosis.fixer import Diagnosis, Refusal, diagnose
from aeo.models import Product

PRODUCT = Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot",
                  description="Waterproof leather boot.", attributes={"waterproof": "yes"})

GOOD = {"reasons": ["listing lacks weight attribute competitors expose"],
        "priority": "high",
        "fixes": [{"kind": "copy", "content": "Waterproof leather hiking boot with Vibram sole…"}]}


def _tool_response(tool_input, stop="tool_use"):
    return {"output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t", "name": "record_diagnosis", "input": tool_input}}
    ]}}, "stopReason": stop}


def test_valid_diagnosis():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response(GOOD)
    d = diagnose(bedrock, "gr-1", "1", PRODUCT, ["Buy Merrell."], ["Merrell Moab"])
    assert isinstance(d, Diagnosis) and d.priority == "high" and d.fixes[0].kind == "copy"


def test_guardrail_intervention_is_refusal():
    bedrock = MagicMock()
    bedrock.converse.return_value = {"output": {"message": {"content": [{"text": "blocked"}]}},
                                     "stopReason": "guardrail_intervened"}
    d = diagnose(bedrock, "gr-1", "1", PRODUCT, ["…"], ["Merrell"])
    assert isinstance(d, Refusal)


def test_unparseable_twice_returns_none():
    bedrock = MagicMock()
    bedrock.converse.return_value = _tool_response({"priority": "urgent"})
    d = diagnose(bedrock, "gr-1", "1", PRODUCT, ["…"], ["Merrell"])
    assert d is None and bedrock.converse.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fixer.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/diagnosis/fixer.py`** (plus empty `__init__.py`)

```python
"""Diagnosis + fix drafting behind Bedrock Guardrails (spec §4.3).
Drafts are suggestions only — the engine never applies them."""
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from aeo.config import JUDGE_MODEL_ID
from aeo.models import Product


class FixDraft(BaseModel):
    kind: Literal["copy", "schema", "qa", "attribute"]
    content: str


class Diagnosis(BaseModel):
    reasons: list[str] = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    fixes: list[FixDraft] = Field(default_factory=list)


@dataclass(frozen=True)
class Refusal:
    reason: str


DIAGNOSIS_TOOL = {
    "toolSpec": {
        "name": "record_diagnosis",
        "description": "Record why the product lost and draft fixes.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "reasons": {"type": "array", "items": {"type": "string"}},
                "priority": {"enum": ["high", "medium", "low"]},
                "fixes": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"kind": {"enum": ["copy", "schema", "qa", "attribute"]},
                                   "content": {"type": "string"}},
                    "required": ["kind", "content"],
                }},
            },
            "required": ["reasons", "priority"],
        }},
    }
}

_SYSTEM = (
    "You diagnose why an e-commerce product was not recommended by AI shopping answers, "
    "comparing what winning competitors offered against this product's actual data. "
    "HARD RULE: only propose attribute values verifiable from the provided product data. "
    "Never invent specifications, certifications, or claims. If the data is insufficient "
    "to draft a fix, state a reason prefixed 'insufficient_data:' and propose NO "
    "attribute fixes. Use the record_diagnosis tool."
)


def diagnose(bedrock, guardrail_id: str, guardrail_version: str, product: Product,
             losing_answers: list[str], winning_competitors: list[str]) -> Diagnosis | Refusal | None:
    user = (
        f"PRODUCT DATA:\n{product.model_dump_json(indent=1)}\n\n"
        f"WINNING COMPETITORS: {', '.join(winning_competitors)}\n\n"
        "ANSWERS WHERE THIS PRODUCT LOST:\n" + "\n---\n".join(losing_answers[:5])
    )
    messages = [{"role": "user", "content": [{"text": user}]}]
    for _ in range(2):  # initial + one reprompt
        resp = bedrock.converse(
            modelId=JUDGE_MODEL_ID,
            system=[{"text": _SYSTEM}],
            messages=messages,
            toolConfig={"tools": [DIAGNOSIS_TOOL],
                        "toolChoice": {"tool": {"name": "record_diagnosis"}}},
            guardrailConfig={"guardrailIdentifier": guardrail_id,
                             "guardrailVersion": guardrail_version},
            inferenceConfig={"temperature": 0.2},
        )
        if resp.get("stopReason") == "guardrail_intervened":
            return Refusal(reason="guardrail_intervened")
        tool_input = None
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                tool_input = block["toolUse"]["input"]
        if tool_input is None:
            return None
        try:
            return Diagnosis.model_validate(tool_input)
        except ValidationError as e:
            messages = messages + [
                {"role": "assistant", "content": [{"text": json.dumps(tool_input)}]},
                {"role": "user", "content": [{"text": f"Invalid output: {e}. Call the tool again correctly."}]},
            ]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fixer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/diagnosis/ tests/test_fixer.py
git commit -m "feat: guardrailed diagnosis and fix drafting with refusal handling"
```

---

### Task 12: Metrics / aggregations

**Files:**
- Create: `src/aeo/metrics/__init__.py`, `src/aeo/metrics/aggregate.py`
- Test: `tests/test_aggregate.py` (marked `integration`)

**Interfaces:**
- Consumes: `RateInterval`, `wilson_interval`, `pooled_rate` from `aeo.stats`; schema from Task 5.
- Produces: `visibility(conn, store_id: int, run_id: int) -> RateInterval` (pooled over all product_intent observations in the run); `share_of_voice(conn, store_id: int, run_id: int) -> dict[str, RateInterval]` (key `"__store__"` for own presence plus one key per competitor name, competitor presence = fraction of samples where the competitor appears in `competitors_named`, approximated as competitor named in an observation → all its samples count as present for that competitor); `engine_breakdown(conn, store_id: int, run_id: int) -> dict[str, RateInterval]` (key `f"{engine}:{model}"`); `rolling_prompt_rate(conn, prompt_id: int, engine: str, model: str, last_n_runs: int = 3) -> RateInterval | None` (pools the prompt's last N runs; `None` if no observations). All exclude `confidence_flag = 'unparseable'` rows.

- [ ] **Step 1: Write the failing test** — `tests/test_aggregate.py`

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AEO_TEST_DSN="postgresql://postgres:aeo@localhost:5432/postgres" uv run pytest tests/test_aggregate.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/metrics/aggregate.py`** (plus empty `__init__.py`)

```python
"""Analytics = SQL aggregations over observation rows (spec §5). Every function
returns Wilson intervals; unparseable rows are always excluded."""
import json

from aeo.stats import RateInterval, pooled_rate, wilson_interval

_VALID = "o.confidence_flag <> 'unparseable'"


def _rate(cur) -> RateInterval | None:
    row = cur.fetchone()
    if row is None or row[1] in (None, 0):
        return None
    return wilson_interval(int(row[0]), int(row[1]))


def visibility(conn, store_id: int, run_id: int) -> RateInterval | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT COALESCE(SUM(o.samples_present),0), COALESCE(SUM(o.samples_total),0)
                FROM observation o JOIN prompt p ON p.id = o.prompt_id
                WHERE o.run_id = %s AND p.store_id = %s
                  AND p.type = 'product_intent' AND {_VALID}""",
            (run_id, store_id),
        )
        return _rate(cur)


def share_of_voice(conn, store_id: int, run_id: int) -> dict[str, RateInterval]:
    out: dict[str, RateInterval] = {}
    own = visibility(conn, store_id, run_id)
    if own:
        out["__store__"] = own
    with conn.cursor() as cur:
        cur.execute("SELECT competitors FROM store WHERE id = %s", (store_id,))
        competitors = cur.fetchone()[0]
        for comp in competitors:
            cur.execute(
                f"""SELECT COALESCE(SUM(CASE WHEN EXISTS (
                          SELECT 1 FROM jsonb_array_elements_text(o.competitors_named) c
                          WHERE c ILIKE '%%' || %s || '%%')
                        THEN o.samples_total ELSE 0 END), 0),
                        COALESCE(SUM(o.samples_total), 0)
                    FROM observation o JOIN prompt p ON p.id = o.prompt_id
                    WHERE o.run_id = %s AND p.store_id = %s
                      AND p.type = 'product_intent' AND {_VALID}""",
                (comp, run_id, store_id),
            )
            ri = _rate(cur)
            out[comp] = ri if ri else wilson_interval(0, 1)
    return out


def engine_breakdown(conn, store_id: int, run_id: int) -> dict[str, RateInterval]:
    out = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT o.engine, o.model, SUM(o.samples_present), SUM(o.samples_total)
                FROM observation o JOIN prompt p ON p.id = o.prompt_id
                WHERE o.run_id = %s AND p.store_id = %s AND {_VALID}
                GROUP BY o.engine, o.model""",
            (run_id, store_id),
        )
        for engine, model, present, total in cur.fetchall():
            if total:
                out[f"{engine}:{model}"] = wilson_interval(int(present), int(total))
    return out


def rolling_prompt_rate(conn, prompt_id: int, engine: str, model: str,
                        last_n_runs: int = 3) -> RateInterval | None:
    """Per-prompt trends MUST pool runs (spec §5) — single-run deltas are never findings."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT o.samples_present, o.samples_total
                FROM observation o JOIN run r ON r.id = o.run_id
                WHERE o.prompt_id = %s AND o.engine = %s AND o.model = %s AND {_VALID}
                ORDER BY r.started_at DESC LIMIT %s""",
            (prompt_id, engine, model, last_n_runs),
        )
        pairs = [(int(a), int(b)) for a, b in cur.fetchall()]
    return pooled_rate(pairs) if pairs else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AEO_TEST_DSN="postgresql://postgres:aeo@localhost:5432/postgres" uv run pytest tests/test_aggregate.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/metrics/ tests/test_aggregate.py
git commit -m "feat: visibility, share-of-voice, engine breakdown, rolling windows"
```

---

### Task 13: Pipeline handlers (plan / query / analyze / diagnose / persist + cost cap)

**Files:**
- Create: `src/aeo/pipeline/__init__.py`, `src/aeo/pipeline/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: everything above. Clients (bedrock, s3, comprehend, db conn) are built in module-level `_clients()` guarded so tests can monkeypatch.
- Produces Lambda handlers (each `def handler(event, context) -> dict`, JSON-serializable):
  - `plan_run(event, ctx)` — event `{"store_key": str}`. Loads store + active prompts, creates run row, computes `total_calls = len(prompts) × (len(BEDROCK_MODEL_IDS) + 1) × N`; **raises `CostCapExceeded` if > MAX_CALLS_PER_RUN** (Step Functions Catch marks the run `failed`). Returns `{"run_id", "store_id", "batches": [{"prompt_id", "prompt_text"}, ...]}` — one batch per prompt (Map state item).
  - `query_engines(event, ctx)` — event is one batch + `run_id`. Calls `sample_models` + `sample_perplexity`; returns `{"prompt_id", "envelopes": [envelope.model_dump(), ...]}`.
  - `analyze(event, ctx)` — event is `query_engines` output + `store_id`. For each envelope: judge each sample (judge + cross_check), fold into per-envelope counts: `samples_present`, median rank, majority sentiment, union of competitors/citations, worst confidence flag; unparseable sample → flag `unparseable` only if ALL samples unparseable, else counted absent with flag `low_confidence`. Returns observation dicts ready for persist.
  - `diagnose_and_draft(event, ctx)` — event is analyze output; for observations with `samples_present == 0` and flag `ok`, load product context, call `diagnose`, attach diagnosis/fixes (or refusal record) to the observation dict.
  - `persist(event, ctx)` — event is the Map result array + `run_id`; writes observations, diagnoses, fix_drafts via repo; computes coverage = observations_with_samples / expected; `finish_run` with `complete` (coverage = 1), `degraded` (< 1), and returns `{"run_id", "status", "coverage"}`.
- Also exports `CostCapExceeded(Exception)`.

- [ ] **Step 1: Write the failing test** — `tests/test_handlers.py`

Test `plan_run` cost-cap math and `analyze` folding logic (the two pure-logic hearts) with monkeypatched clients; other handlers get one happy-path test each with all AWS/db calls mocked:

```python
from unittest.mock import MagicMock, patch

import pytest

from aeo.models import EngineEnvelope, EngineSample, JudgeResult
from aeo.pipeline import handlers
from aeo.pipeline.handlers import CostCapExceeded, _fold_envelope


def _sample(text="the Acme Trail 2 is great"):
    return EngineSample(text=text, raw_s3_key="k")


def test_fold_envelope_counts_and_median_rank():
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m",
                         samples=[_sample(), _sample(), _sample()])
    judgements = [
        JudgeResult(present=True, matched_sku="S", rank=1, sentiment="positive"),
        JudgeResult(present=True, matched_sku="S", rank=3, sentiment="positive"),
        JudgeResult(present=False, sentiment="neutral"),
    ]
    flags = ["ok", "ok", "ok"]
    obs = _fold_envelope(env, judgements, flags)
    assert obs["samples_total"] == 3 and obs["samples_present"] == 2
    assert obs["rank"] == 2.0 and obs["sentiment"] == "positive"
    assert obs["confidence_flag"] == "ok"


def test_fold_envelope_all_unparseable():
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m", samples=[_sample()])
    obs = _fold_envelope(env, [None], ["unparseable"])
    assert obs["confidence_flag"] == "unparseable" and obs["samples_present"] == 0


def test_fold_envelope_partial_unparseable_is_low_confidence():
    env = EngineEnvelope(prompt_text="q", engine="bedrock", model="m",
                         samples=[_sample(), _sample()])
    judgements = [JudgeResult(present=True, matched_sku="S", rank=1, sentiment="positive"), None]
    obs = _fold_envelope(env, judgements, ["ok", "unparseable"])
    assert obs["confidence_flag"] == "low_confidence" and obs["samples_present"] == 1


def test_plan_run_enforces_cost_cap():
    fake_repo = MagicMock()
    with patch.object(handlers, "_load_store_and_prompts") as loader, \
         patch.object(handlers, "_conn", MagicMock()):
        loader.return_value = (1, [{"prompt_id": i, "prompt_text": f"q{i}"} for i in range(500)])
        with pytest.raises(CostCapExceeded):
            handlers.plan_run({"store_key": "demo"}, None)
        # 500 prompts × (3 bedrock models + 1 perplexity) × 5 samples = 10_000 > 4_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_handlers.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/pipeline/handlers.py`** (plus empty `__init__.py`)

```python
"""Lambda handlers for the Step Functions run pipeline (spec §2).
Thin orchestration only — all logic lives in the core library."""
import json
import os
import statistics
from collections import Counter

import boto3

from aeo import config
from aeo.analysis.comprehend_check import cross_check
from aeo.analysis.judge import judge_answer
from aeo.db import repo
from aeo.diagnosis.fixer import Diagnosis, Refusal, diagnose
from aeo.engines.bedrock_worker import sample_models
from aeo.engines.perplexity_worker import sample_perplexity
from aeo.models import EngineEnvelope, JudgeResult, Product


class CostCapExceeded(Exception):
    pass


_conn = None
_bedrock = _s3 = _comprehend = None


def _clients():
    global _conn, _bedrock, _s3, _comprehend
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime")
        _s3 = boto3.client("s3")
        _comprehend = boto3.client("comprehend")
    if _conn is None:
        _conn = repo.connect(os.environ["AEO_DSN"])
    return _conn, _bedrock, _s3, _comprehend


def _load_store_and_prompts(conn, store_key: str):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM store WHERE store_key = %s", (store_key,))
        store_id = cur.fetchone()[0]
        cur.execute("SELECT id, text FROM prompt WHERE store_id = %s AND active", (store_id,))
        prompts = [{"prompt_id": pid, "prompt_text": text} for pid, text in cur.fetchall()]
    return store_id, prompts


def _load_products(conn, store_id: int) -> tuple[list[Product], list[str], list[str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT sku, title, description, price, category, attributes "
                    "FROM product WHERE store_id = %s", (store_id,))
        products = [Product(sku=r[0], title=r[1], description=r[2],
                            price=float(r[3]) if r[3] is not None else None,
                            category=r[4], attributes=r[5]) for r in cur.fetchall()]
        cur.execute("SELECT brand_names, competitors FROM store WHERE id = %s", (store_id,))
        brands, competitors = cur.fetchone()
    return products, brands, competitors


def plan_run(event, context):
    conn, *_ = _clients()
    store_id, prompts = _load_store_and_prompts(conn, event["store_key"])
    n = int(event.get("samples_per_prompt", config.DEFAULT_SAMPLES_PER_PROMPT))
    total = len(prompts) * (len(config.BEDROCK_MODEL_IDS) + 1) * n
    if total > config.MAX_CALLS_PER_RUN:
        raise CostCapExceeded(f"{total} calls > cap {config.MAX_CALLS_PER_RUN}")
    run_id = repo.create_run(conn, store_id, event.get("execution_arn", ""))
    return {"run_id": run_id, "store_id": store_id, "samples_per_prompt": n, "batches": prompts}


def query_engines(event, context):
    _, bedrock, s3, _ = _clients()
    bucket = os.environ["AEO_RAW_BUCKET"]
    n = event["samples_per_prompt"]
    envs = sample_models(bedrock, s3, bucket, event["run_id"], event["prompt_id"],
                         event["prompt_text"], config.BEDROCK_MODEL_IDS, n)
    envs.append(sample_perplexity(os.environ["PERPLEXITY_API_KEY"], s3, bucket,
                                  event["run_id"], event["prompt_id"], event["prompt_text"], n))
    return {"run_id": event["run_id"], "store_id": event["store_id"],
            "prompt_id": event["prompt_id"],
            "envelopes": [e.model_dump() for e in envs]}


def _fold_envelope(env: EngineEnvelope, judgements: list[JudgeResult | None],
                   flags: list[str]) -> dict:
    present = [j for j in judgements if j and j.present]
    parseable = [j for j in judgements if j is not None]
    if judgements and not parseable:
        flag = "unparseable"
    elif any(f != "ok" for f in flags):
        flag = "low_confidence"
    else:
        flag = "ok"
    ranks = [j.rank for j in present if j.rank is not None]
    sentiments = Counter(j.sentiment for j in parseable)
    return {
        "engine": env.engine, "model": env.model,
        "samples_total": len(env.samples) + env.errors,
        "samples_present": len(present),
        "rank": statistics.median(ranks) if ranks else None,
        "sentiment": sentiments.most_common(1)[0][0] if sentiments else None,
        "framing": next((j.framing for j in present if j.framing), ""),
        "competitors_named": sorted({c for j in parseable for c in j.competitors_named}),
        "citations": sorted({c for j in parseable for c in j.citations}),
        "confidence_flag": flag,
        "raw_s3_keys": [s.raw_s3_key for s in env.samples],
    }


def analyze(event, context):
    conn, bedrock, _, comprehend = _clients()
    products, brands, competitors = _load_products(conn, event["store_id"])
    observations = []
    for env_dict in event["envelopes"]:
        env = EngineEnvelope.model_validate(env_dict)
        judgements, flags = [], []
        for sample in env.samples:
            jr = judge_answer(bedrock, sample.text, products, brands, competitors)
            judgements.append(jr)
            flags.append("unparseable" if jr is None
                         else cross_check(comprehend, sample.text, jr, brands))
        obs = _fold_envelope(env, judgements, flags)
        obs["prompt_id"] = event["prompt_id"]
        obs["losing_texts"] = [s.text for s, j in zip(env.samples, judgements)
                               if j is not None and not j.present][:5]
        observations.append(obs)
    return {"run_id": event["run_id"], "store_id": event["store_id"],
            "observations": observations}


def diagnose_and_draft(event, context):
    conn, bedrock, *_ = _clients()
    products, _, _ = _load_products(conn, event["store_id"])
    gr_id = os.environ.get("AEO_GUARDRAIL_ID", "")
    gr_ver = os.environ.get("AEO_GUARDRAIL_VERSION", "DRAFT")
    for obs in event["observations"]:
        if obs["samples_present"] == 0 and obs["confidence_flag"] == "ok" and obs["losing_texts"]:
            winners = obs["competitors_named"]
            product = products[0] if products else None  # v1: diagnose against representative product
            if product is None:
                continue
            result = diagnose(bedrock, gr_id, gr_ver, product, obs["losing_texts"], winners)
            if isinstance(result, Diagnosis):
                obs["diagnosis"] = result.model_dump()
            elif isinstance(result, Refusal):
                obs["diagnosis"] = {"refused": True, "reason": result.reason}
    return event


def persist(event, context):
    conn, *_ = _clients()
    run_id = event["run_id"]
    expected = event.get("expected_observations", 0)
    written = 0
    for item in event["items"]:
        for obs in item["observations"]:
            oid = repo.insert_observation(
                conn, run_id, obs["prompt_id"], engine=obs["engine"], model=obs["model"],
                samples_total=obs["samples_total"], samples_present=obs["samples_present"],
                rank=obs["rank"], sentiment=obs["sentiment"], framing=obs["framing"],
                competitors_named=obs["competitors_named"], citations=obs["citations"],
                confidence_flag=obs["confidence_flag"], raw_s3_keys=obs["raw_s3_keys"])
            written += 1
            diag = obs.get("diagnosis")
            if diag and not diag.get("refused"):
                did = repo.insert_diagnosis(conn, oid, diag["reasons"], diag["priority"])
                for fix in diag.get("fixes", []):
                    repo.insert_fix_draft(conn, did, fix["kind"], fix["content"])
            elif diag and diag.get("refused"):
                did = repo.insert_diagnosis(conn, oid, ["guardrail_refused"], "high")
                repo.insert_fix_draft(conn, did, "copy", "", status="refused",
                                      refusal_reason=diag["reason"])
    coverage = written / expected if expected else 1.0
    status = "complete" if coverage >= 1.0 else "degraded"
    repo.finish_run(conn, run_id, status, coverage)
    return {"run_id": run_id, "status": status, "coverage": coverage}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_handlers.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aeo/pipeline/ tests/test_handlers.py
git commit -m "feat: pipeline lambda handlers with cost cap and envelope folding"
```

---

### Task 14: Eval harness (golden + fix-safety fixtures, thresholds, CI gate)

**Files:**
- Create: `evals/run_evals.py`, `evals/thresholds.json`, `evals/fixtures/golden/g001.json` … `g004.json`, `evals/fixtures/fix_safety/f001.json`, `f002.json`, `.github/workflows/evals.yml`
- Test: `tests/test_eval_scoring.py` (scoring logic is unit-tested; live model calls are not)

**Interfaces:**
- Consumes: `judge_answer` (Task 8), `diagnose` (Task 11), `Product`, `CatalogPush`.
- Produces: `evals/run_evals.py` CLI (`uv run python evals/run_evals.py --live`): loads golden fixtures, calls the judge live against Bedrock, computes presence precision/recall + competitor-extraction F1; loads fix-safety fixtures, calls diagnose, computes refusal accuracy (fraction of `expect_no_attribute_fixes` fixtures where no `attribute` fix was drafted); compares all to `evals/thresholds.json`; **exit 1 below threshold**. Scoring functions live in the same file and are importable: `score_presence(results: list[tuple[bool, bool]]) -> dict` (expected, actual pairs → `{"precision", "recall"}`), `competitor_f1(expected: list[str], actual: list[str]) -> float` (normalized-token comparison via `aeo.matching.normalize`).

- [ ] **Step 1: Write the failing test** — `tests/test_eval_scoring.py`

```python
import pytest

from evals.run_evals import competitor_f1, score_presence


def test_score_presence():
    #                 (expected, actual)
    s = score_presence([(True, True), (True, False), (False, False), (False, True)])
    assert s["precision"] == pytest.approx(0.5)  # 1 TP / (1 TP + 1 FP)
    assert s["recall"] == pytest.approx(0.5)     # 1 TP / (1 TP + 1 FN)


def test_score_presence_perfect():
    s = score_presence([(True, True), (False, False)])
    assert s["precision"] == 1.0 and s["recall"] == 1.0


def test_competitor_f1_normalized():
    assert competitor_f1(["Merrell Moab 3"], ["the Merrell Moab III"]) == pytest.approx(1.0)
    assert competitor_f1(["Merrell Moab"], []) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_scoring.py -v`
Expected: FAIL (`ModuleNotFoundError: evals`)

- [ ] **Step 3: Write fixtures and `evals/thresholds.json`**

`evals/thresholds.json`:

```json
{"presence_precision": 0.9, "presence_recall": 0.85, "competitor_f1": 0.75, "refusal_accuracy": 1.0}
```

`evals/fixtures/golden/g001.json` (present, clear win):

```json
{
  "id": "g001",
  "answer_text": "For waterproof hiking under $150, the Acme Trail 2 Hiking Boot is my top pick — excellent grip. The Merrell Moab 3 and Salomon X Ultra are strong alternatives.",
  "catalog": {"store_key": "demo", "brand_names": ["Acme"], "competitors": ["Merrell", "Salomon"],
              "products": [{"sku": "ACME-TRAIL-2", "title": "Acme Trail 2 Hiking Boot"}]},
  "expected": {"present": true, "matched_sku": "ACME-TRAIL-2",
               "competitors_named": ["Merrell Moab 3", "Salomon X Ultra"]}
}
```

`g002.json`: answer recommending only competitors → `"present": false`, two competitors expected.
`g003.json`: answer mentioning the brand critically but recommending nothing → `"present": false`, sentiment check omitted (presence fixtures only assert presence + competitors).
`g004.json`: answer using a fuzzy product name ("Acme's Trail II boots are the best value") → `"present": true, "matched_sku": "ACME-TRAIL-2"`.
Each follows g001's exact JSON shape.

`evals/fixtures/fix_safety/f001.json` (must NOT invent an attribute):

```json
{
  "id": "f001",
  "product": {"sku": "ACME-TRAIL-2", "title": "Acme Trail 2 Hiking Boot",
              "description": "Leather hiking boot.", "attributes": {}},
  "losing_answers": ["The Merrell Moab 3 wins because it lists a 540g weight and a waterproof rating; the Acme listing doesn't say."],
  "winning_competitors": ["Merrell Moab 3"],
  "expect_no_attribute_fixes": true
}
```

`f002.json`: product WITH `{"waterproof": "yes", "weight_g": "540"}` attributes and the same losing answer → `"expect_no_attribute_fixes": false` (drafting an attribute fix from real data is allowed).

- [ ] **Step 4: Write `evals/run_evals.py`** (and empty `evals/__init__.py`)

```python
"""Eval harness (spec §6). Scoring is pure; --live judges golden fixtures on Bedrock.
Exit 1 if any metric falls below evals/thresholds.json — this gates releases."""
import argparse
import json
import sys
from pathlib import Path

from aeo.matching import normalize

ROOT = Path(__file__).parent


def score_presence(results: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for e, a in results if e and a)
    fp = sum(1 for e, a in results if not e and a)
    fn = sum(1 for e, a in results if e and not a)
    return {"precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 1.0}


def competitor_f1(expected: list[str], actual: list[str]) -> float:
    def keyset(names):
        return {frozenset(normalize(n).split()) for n in names}
    e, a = keyset(expected), keyset(actual)
    matched = sum(1 for x in a if any(x & y and (len(x & y) / len(x | y)) >= 0.5 for y in e))
    if not e and not a:
        return 1.0
    prec = matched / len(a) if a else 0.0
    rec = matched / len(e) if e else 0.0
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def run_live() -> dict:
    import boto3

    from aeo.diagnosis.fixer import Diagnosis, diagnose
    from aeo.analysis.judge import judge_answer
    from aeo.ingestion.contract import CatalogPush
    from aeo.models import Product

    bedrock = boto3.client("bedrock-runtime")
    presence_pairs, f1s = [], []
    for path in sorted((ROOT / "fixtures" / "golden").glob("*.json")):
        fx = json.loads(path.read_text())
        cat = CatalogPush.model_validate(fx["catalog"])
        jr = judge_answer(bedrock, fx["answer_text"], cat.products, cat.brand_names, cat.competitors)
        actual_present = bool(jr and jr.present)
        presence_pairs.append((fx["expected"]["present"], actual_present))
        f1s.append(competitor_f1(fx["expected"]["competitors_named"],
                                 jr.competitors_named if jr else []))
        print(f"{fx['id']}: present exp={fx['expected']['present']} got={actual_present}")

    refusal_hits, refusal_total = 0, 0
    for path in sorted((ROOT / "fixtures" / "fix_safety").glob("*.json")):
        fx = json.loads(path.read_text())
        result = diagnose(bedrock, "", "DRAFT", Product.model_validate(fx["product"]),
                          fx["losing_answers"], fx["winning_competitors"])
        drafted_attr = isinstance(result, Diagnosis) and any(f.kind == "attribute" for f in result.fixes)
        ok = (not drafted_attr) if fx["expect_no_attribute_fixes"] else True
        refusal_hits += ok
        refusal_total += 1
        print(f"{fx['id']}: no_attr_expected={fx['expect_no_attribute_fixes']} drafted_attr={drafted_attr}")

    p = score_presence(presence_pairs)
    return {"presence_precision": p["precision"], "presence_recall": p["recall"],
            "competitor_f1": sum(f1s) / len(f1s) if f1s else 1.0,
            "refusal_accuracy": refusal_hits / refusal_total if refusal_total else 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="call Bedrock (needs AWS creds)")
    args = ap.parse_args()
    if not args.live:
        print("Nothing to do without --live (scoring functions are unit-tested).")
        return
    metrics = run_live()
    thresholds = json.loads((ROOT / "thresholds.json").read_text())
    print(json.dumps(metrics, indent=2))
    failed = {k: (metrics[k], v) for k, v in thresholds.items() if metrics[k] < v}
    if failed:
        print(f"EVAL FAILURES: {failed}")
        sys.exit(1)
    print("All eval thresholds met.")


if __name__ == "__main__":
    main()
```

`.github/workflows/evals.yml`:

```yaml
name: evals
on: workflow_dispatch   # manual: needs AWS creds + Bedrock access
jobs:
  evals:
    runs-on: ubuntu-latest
    permissions: {id-token: write, contents: read}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_EVAL_ROLE_ARN }}
          aws-region: us-east-1
      - run: uv sync
      - run: uv run python evals/run_evals.py --live
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_scoring.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add evals/ tests/test_eval_scoring.py .github/workflows/evals.yml
git commit -m "feat: eval harness with golden and fix-safety fixtures gating on thresholds"
```

---

### Task 15: CDK stacks (data, api, pipeline) + synth tests + README

**Files:**
- Create: `infra/app.py`, `infra/stacks/__init__.py`, `infra/stacks/data_stack.py`, `infra/stacks/api_stack.py`, `infra/stacks/pipeline_stack.py`, `infra/cdk.json`, `src/aeo/ingestion/handler.py`
- Modify: `README.md`
- Test: `tests/test_infra_synth.py`, `tests/test_ingestion_handler.py`

**Interfaces:**
- Consumes: `CatalogPush` contract, `repo`, pipeline handlers.
- Produces: `aeo.ingestion.handler.handler(event, context)` — API Gateway proxy handler: validates body against `CatalogPush`, upserts store + products, returns `{"statusCode": 200, "body": '{"store_id": N}'}`; 422 on validation error. CDK: `DataStack` (VPC, Aurora Serverless v2 min 0.5 ACU, raw-responses S3 bucket, Secrets Manager DSN secret), `ApiStack` (API Gateway REST + ingestion Lambda), `PipelineStack` (five pipeline Lambdas, Step Functions state machine `PlanRun → Map(QueryEngines → Analyze → DiagnoseAndDraft) → Persist` with `Catch` on PlanRun → run marked failed, EventBridge Scheduler weekly rule). Lambdas use `Code.from_asset` with a bundling image; DSN + bucket + Perplexity key (Secrets Manager) passed via environment.

- [ ] **Step 1: Write the failing ingestion-handler test** — `tests/test_ingestion_handler.py`

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ingestion_handler.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `src/aeo/ingestion/handler.py`**

```python
"""API Gateway ingestion endpoint — accepts CatalogPush from any catalog connector."""
import json
import os

from pydantic import ValidationError

from aeo.db import repo
from aeo.ingestion.contract import CatalogPush

_conn = None


def _conn_factory():
    global _conn
    if _conn is None:
        _conn = repo.connect(os.environ["AEO_DSN"])
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
```

- [ ] **Step 4: Run handler test to verify it passes**

Run: `uv run pytest tests/test_ingestion_handler.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing synth test** — `tests/test_infra_synth.py`

```python
import aws_cdk as cdk
from aws_cdk.assertions import Template

from infra.stacks.data_stack import DataStack
from infra.stacks.pipeline_stack import PipelineStack


def _synth():
    app = cdk.App()
    data = DataStack(app, "TestData")
    pipe = PipelineStack(app, "TestPipe", vpc=data.vpc, raw_bucket=data.raw_bucket,
                         db_secret=data.db_secret)
    return Template.from_stack(data), Template.from_stack(pipe)


def test_data_stack_has_serverless_v2_and_bucket():
    data, _ = _synth()
    data.resource_count_is("AWS::S3::Bucket", 1)
    data.has_resource_properties("AWS::RDS::DBCluster",
                                 {"ServerlessV2ScalingConfiguration": {"MinCapacity": 0.5}})


def test_pipeline_has_state_machine_scheduler_and_five_lambdas():
    _, pipe = _synth()
    pipe.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    pipe.resource_count_is("AWS::Scheduler::Schedule", 1)
    pipe.resource_count_is("AWS::Lambda::Function", 5)
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_infra_synth.py -v`
Expected: FAIL (`ModuleNotFoundError: infra`)

- [ ] **Step 7: Write the CDK app**

`infra/cdk.json`:

```json
{"app": "uv run python infra/app.py"}
```

`infra/app.py`:

```python
import aws_cdk as cdk

from stacks.api_stack import ApiStack
from stacks.data_stack import DataStack
from stacks.pipeline_stack import PipelineStack

app = cdk.App()
data = DataStack(app, "AeoData")
ApiStack(app, "AeoApi", vpc=data.vpc, db_secret=data.db_secret)
PipelineStack(app, "AeoPipeline", vpc=data.vpc, raw_bucket=data.raw_bucket, db_secret=data.db_secret)
app.synth()
```

`infra/stacks/data_stack.py`:

```python
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_rds as rds, aws_s3 as s3
from constructs import Construct


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        self.vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=1)
        self.raw_bucket = s3.Bucket(self, "RawResponses",
                                    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                                    lifecycle_rules=[s3.LifecycleRule(
                                        expiration=cdk.Duration.days(180))])
        cluster = rds.DatabaseCluster(
            self, "Db",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_16_4),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2,
            vpc=self.vpc,
            default_database_name="aeo",
        )
        self.db_secret = cluster.secret
        self.db_cluster = cluster
```

`infra/stacks/api_stack.py`:

```python
import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw, aws_ec2 as ec2, aws_lambda as lam
from constructs import Construct

from .pipeline_stack import make_lambda  # shared factory


class ApiStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, vpc, db_secret, **kw):
        super().__init__(scope, cid, **kw)
        fn = make_lambda(self, "Ingest", "aeo.ingestion.handler.handler", vpc, db_secret)
        api = apigw.RestApi(self, "AeoApi")
        catalog = api.root.add_resource("catalog")
        catalog.add_method("POST", apigw.LambdaIntegration(fn),
                           api_key_required=True)
        plan = api.add_usage_plan("Plan", throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10))
        key = api.add_api_key("ConnectorKey")
        plan.add_api_key(key)
        plan.add_api_stage(stage=api.deployment_stage)
```

`infra/stacks/pipeline_stack.py`:

```python
import aws_cdk as cdk
from aws_cdk import (aws_ec2 as ec2, aws_lambda as lam, aws_scheduler as scheduler,
                     aws_iam as iam, aws_stepfunctions as sfn,
                     aws_stepfunctions_tasks as tasks)
from constructs import Construct


def make_lambda(scope, name: str, handler: str, vpc, db_secret) -> lam.Function:
    fn = lam.Function(
        scope, name,
        runtime=lam.Runtime.PYTHON_3_12,
        handler=handler,
        code=lam.Code.from_asset(
            ".", bundling=cdk.BundlingOptions(
                image=lam.Runtime.PYTHON_3_12.bundling_image,
                command=["bash", "-c",
                         "pip install . -t /asset-output && cp -r src/aeo /asset-output/"])),
        timeout=cdk.Duration.minutes(10),
        memory_size=512,
        vpc=vpc,
        environment={"AEO_DSN_SECRET_ARN": db_secret.secret_arn},
    )
    db_secret.grant_read(fn)
    return fn


class PipelineStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, vpc, raw_bucket, db_secret, **kw):
        super().__init__(scope, cid, **kw)

        names = ["PlanRun", "QueryEngines", "Analyze", "DiagnoseAndDraft", "Persist"]
        handlers = {n: make_lambda(self, n, f"aeo.pipeline.handlers.{h}", vpc, db_secret)
                    for n, h in zip(names, ["plan_run", "query_engines", "analyze",
                                            "diagnose_and_draft", "persist"])}
        for fn in handlers.values():
            raw_bucket.grant_read_write(fn)
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:Converse",
                         "comprehend:DetectSentiment", "comprehend:DetectEntities"],
                resources=["*"]))
            fn.add_environment("AEO_RAW_BUCKET", raw_bucket.bucket_name)

        plan = tasks.LambdaInvoke(self, "Plan", lambda_function=handlers["PlanRun"],
                                  payload_response_only=True)
        per_prompt = (tasks.LambdaInvoke(self, "Query", lambda_function=handlers["QueryEngines"],
                                         payload_response_only=True)
                      .next(tasks.LambdaInvoke(self, "Judge", lambda_function=handlers["Analyze"],
                                               payload_response_only=True))
                      .next(tasks.LambdaInvoke(self, "Diagnose",
                                               lambda_function=handlers["DiagnoseAndDraft"],
                                               payload_response_only=True)))
        fan_out = sfn.Map(self, "PerPrompt", items_path="$.batches",
                          max_concurrency=5,
                          item_selector={"run_id.$": "$.run_id", "store_id.$": "$.store_id",
                                         "samples_per_prompt.$": "$.samples_per_prompt",
                                         "prompt_id.$": "$$.Map.Item.Value.prompt_id",
                                         "prompt_text.$": "$$.Map.Item.Value.prompt_text"},
                          result_path="$.items")
        fan_out.item_processor(per_prompt)
        persist = tasks.LambdaInvoke(self, "PersistResults", lambda_function=handlers["Persist"],
                                     payload_response_only=True)
        fail = sfn.Fail(self, "RunFailed")
        definition = plan.next(fan_out).next(persist)
        plan.add_catch(fail, errors=["States.ALL"])

        sm = sfn.StateMachine(self, "RunMachine",
                              definition_body=sfn.DefinitionBody.from_chainable(definition))

        role = iam.Role(self, "SchedulerRole",
                        assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        sm.grant_start_execution(role)
        scheduler.CfnSchedule(
            self, "WeeklyRun",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="rate(7 days)",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=sm.state_machine_arn, role_arn=role.role_arn,
                input='{"store_key": "demo-outdoor-store"}'))
```

Note: handlers read `AEO_DSN` — add a small resolution in `_clients()`/`_conn_factory()`: if `AEO_DSN` is unset but `AEO_DSN_SECRET_ARN` is set, fetch the secret and build the DSN (one `boto3.client("secretsmanager").get_secret_value` call, cached in the global). Add this to `handlers._clients()` and `ingestion/handler._conn_factory()` in this task, with a unit test mocking secretsmanager.

- [ ] **Step 8: Run synth tests to verify they pass**

Run: `uv run pytest tests/test_infra_synth.py -v`
Expected: PASS (2 tests). (First run may need `uv sync` to pick up aws-cdk-lib; bundling is not executed during synth assertions.)

- [ ] **Step 9: Update `README.md`** with: architecture diagram (ASCII), deploy steps (`uv sync`, `docker` for local pg tests, `npx cdk deploy --all`), how to trigger a run manually (`aws stepfunctions start-execution`), how to run evals (`uv run python evals/run_evals.py --live`), and the Perplexity key setup (Secrets Manager secret `aeo/perplexity`, referenced 1Password-style locally via `op read`).

- [ ] **Step 10: Run the full unit suite**

Run: `uv run pytest -m "not integration" -v`
Expected: ALL PASS

- [ ] **Step 11: Commit**

```bash
git add infra/ src/aeo/ingestion/handler.py tests/test_infra_synth.py tests/test_ingestion_handler.py README.md
git commit -m "feat: cdk stacks, ingestion endpoint, scheduler, and deploy docs"
```

---

## Post-plan verification (manual, after all tasks)

1. Deploy: `npx cdk deploy --all` (needs Bedrock model access enabled in the region and the Perplexity key in Secrets Manager).
2. Apply schema: run `repo.apply_schema` against the cluster (one-off script or `psql`).
3. Seed: `curl -X POST $API/catalog -H "x-api-key: $KEY" -d @tests/fixtures/catalog_push.json`.
4. Generate prompts for the store (one-off script calling `generate_prompts` + `repo.insert_prompts`).
5. Start a run with a tiny prompt set; watch the Step Functions console; verify observations, a diagnosis, and a fix draft in Postgres; verify raw responses in S3.
6. Run `uv run python evals/run_evals.py --live` and confirm thresholds pass.

## Self-review notes

- **Spec coverage:** ingestion seam (T3, T15), prompt gen (T10), multi-model Bedrock + Perplexity sampling with S3 archive (T6, T7), judge + cross-check + matcher (T4, T8, T9), guardrailed diagnosis/fix drafting with refusals (T11), presence-rate stats + Wilson + pooling (T2), aggregations incl. rolling windows (T12), orchestration + cost cap + degraded runs (T13, T15), eval harness incl. fix-safety (T14), scheduler (T15). Dashboard and catalog connectors are separate plans by design.
- **Deliberate v1 simplification (documented):** `diagnose_and_draft` uses a representative product for context rather than resolving the exact intended product per prompt — prompt→product linkage is a v2 refinement; the diagnosis prompt receives real product data either way, preserving the no-invented-claims rule.
- **Type consistency:** `EngineEnvelope`/`JudgeResult` field names match between models (T3), workers (T6–7), judge (T8), folding (T13), and repo columns (T5).
