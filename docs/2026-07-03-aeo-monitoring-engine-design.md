# AEO / Answer-Engine Monitoring Engine — Design

**Date:** 2026-07-03
**Status:** Approved design, pre-implementation
**Context:** A standalone, platform-agnostic engine for measuring how a store's
products and brand appear in AI answer engines. Thin catalog connectors act as
clients over the ingestion API.

## 1. Goals and framing

**Primary goal:** portfolio-leads-both. A demonstrable AWS AI showpiece (multi-model
orchestration, LLM-as-judge with measured accuracy, guardrailed generation with
refusal evals) architected so client integrations can wrap it later. When
portfolio polish and product readiness conflict, portfolio wins — but no decision
may block productizing.

**What it does:** For an e-commerce store, continuously measure how the store's
products and brand appear in AI answer engines, diagnose *why* products lose to
competitors, recommend prioritized fixes, and **draft** the corrected content
(copy, schema/attributes, Q&A) for merchant approval. The engine never applies
fixes to a store; applying is a connector's job, later.

**Target engines (hybrid):**
- **Bedrock backbone** — multiple foundation models (Claude, Llama, Titan, Mistral)
  via the Converse API. Reproducible, cheap, API-clean; the eval substrate.
- **One real surface** — Perplexity API as ground-truth comparison. Additional real
  surfaces (ChatGPT, Google AI Overviews) deferred.

**What we track (both, product-intent leads):**
- **Product/category intent** (core): buyer-intent queries auto-generated from the
  catalog ("best waterproof hiking boots under $150"); does THIS store's SKU get
  recommended, at what rank, framed how, vs. which competitors.
- **Brand share-of-voice** (lighter layer): brand-level reputation and
  brand-vs-competitor queries from a merchant-defined brand/competitor list.

## 2. Architecture (Approach A: serverless-native)

| Component | AWS service | Job |
|---|---|---|
| Ingestion API | API Gateway + Lambda | Receives catalog data (products, attributes, brand/competitor list) from any client. Platform-agnostic JSON contract — the seam connectors plug into. |
| Catalog store | Aurora Serverless v2 (Postgres) | Normalized products, attributes, brands/competitors, prompt sets, run results, time-series. Relational because SoV-over-time and competitor comparisons are relational analytics. |
| Prompt generator | Lambda + Bedrock | Catalog + categories → buyer-intent and brand prompts. Deduped, category-balanced, versioned. |
| Run orchestrator | Step Functions | Drives one monitoring run: Map-state fan-out over prompts × engines → analyze → diagnose → draft → persist. Retries and partial-failure handling. |
| Engine workers | Lambda | One worker calls Bedrock Converse across models; one calls Perplexity. Uniform response envelope. |
| Analysis layer | Lambda + Bedrock + Comprehend | Per response: presence, rank/prominence, sentiment, competitors named, citations. |
| Diagnosis + fix drafter | Lambda + Bedrock + Guardrails | Why the product lost → prioritized fixes → drafted copy/schema/Q&A. |
| Raw archive | S3 | Every raw engine response, keyed by run. Reproducibility + audit trail. |
| Scheduler | EventBridge Scheduler | Recurring runs per store (e.g., weekly). |
| Eval harness | Lambda + S3 golden sets | Scores judge accuracy against hand-labeled fixtures; gates CI. |
| Dashboard | React on CloudFront/Amplify + read API | Presence trends, share-of-voice, competitor gaps, drafted-fix review. |
| Reference connector | thin client | Proves the ingestion seam from a real store catalog. |

**Data flow for one run:**
1. EventBridge fires → Step Functions starts a run for store X.
2. Load catalog + prompt sets (regenerate prompts if catalog changed).
3. Map fan-out: each prompt × each engine × N samples → worker Lambda → raw
   response to S3 + envelope back.
4. Analyze each response (presence/rank/sentiment/competitors/citations).
5. Losses/weak showings → diagnose + draft fix (Guardrails-wrapped).
6. Persist structured results + trend deltas to Postgres.
7. Dashboard reads; drafted fixes await merchant approval.

One run = one Step Functions execution: visible, retryable, screenshotable.

## 3. Data model (Postgres)

- `store` — tenant. Brand name(s), competitor list, catalog sync metadata.
- `product` — SKU, title, description, price, attributes (JSONB), category.
- `prompt` — generated test query; `type` (`product_intent` | `brand_sov`),
  source category/product, `active`, `version`. Versioned so trends stay
  comparable when the set changes.
- `run` — one monitoring execution: timestamp, status
  (`complete` | `degraded` | `failed`), Step Functions execution ARN, coverage %.
- `observation` — **core row.** One per (prompt × engine × run). Fields: `engine`,
  `model`, `samples_total`, `samples_present`, `rank` (median when present),
  `sentiment`, `framing`, `competitors_named` (JSONB), `citations` (JSONB),
  `confidence_flag`, `raw_s3_keys`. Powers every trend and SoV number.
- `diagnosis` — for a losing observation: structured `reasons`, `priority`.
- `fix_draft` — `kind` (`copy` | `schema` | `qa` | `attribute`), `content`,
  `status` (`suggested` | `approved` | `rejected`). **Never auto-applied.**

Time-series and share-of-voice are aggregations over `observation`; no separate
metrics store at this scale.

## 4. The AI layer — three separated jobs

1. **Prompt generation** (Bedrock). Catalog → realistic buyer-intent + brand
   prompts. Deduped, category-balanced, versioned.
2. **Response analysis** — a **structured-output LLM judge** (Bedrock Converse with
   a forced JSON tool schema) extracts `{present, matched_sku, rank,
   total_recommended, sentiment, framing, competitors[], citations[]}` from each
   raw answer given the store's catalog context. **Comprehend backstop:**
   independent entity + sentiment extraction; judge/Comprehend disagreement sets
   `low_confidence` and the disagreement rate is a live quality metric. Entity
   *matching* ("the Acme Trail II" → `ACME-TRAIL-2`) uses normalized matching +
   the judge, never naive string equality.
3. **Diagnosis + fix drafting** (Bedrock + **Guardrails**). Given losing answers,
   the product's data, and the winning competitors: what did winners have that
   this product's data lacks → structured reasons → prioritized fixes → drafted
   content. Guardrails block invented specs/claims (a hallucinated "waterproof"
   is a liability). Guardrail interventions are recorded as `refused` with
   reason, never silently dropped.

Each job is independently testable.

## 5. Measurement methodology (the credibility core)

- **Sampling, not single-shot.** Models are stochastic; a single ask is not a
  measurement. Each prompt is asked **N times per engine** at a fixed
  temperature. Observations store a **presence rate** (e.g., 3/5 Claude
  samples), not a boolean. **N is a per-store/per-prompt configuration, not a
  constant** — v1 defaults to N=5 (demo tier); paid tiers raise it.
- **Statistical honesty at N=5.** A per-prompt rate at N=5 has a very wide
  confidence interval (3/5 ≈ 15%–85% at 95%), so **per-prompt, single-run
  deltas are never presented as findings.** What IS sound at N=5:
  store/category-level aggregates (e.g., 50 prompts × 5 samples = 250 samples
  per engine per run) and pooled trends. Rules:
  - **Per-prompt trends use rolling windows** — pool the last 3 runs of a
    same-version prompt (N=15) before showing a trend.
  - **Before/after fix deltas pool across the affected prompt set and runs**
    (e.g., 8 category prompts × 3 runs = 120 vs. 120 samples).
  - **The dashboard renders presence rates with Wilson confidence intervals**
    and does not badge a change as "improved"/"declined" unless the intervals
    separate. Honest uncertainty display is a deliberate differentiator.
- **Judged structured rows.** Raw answer → LLM judge with forced JSON schema →
  structured observation, cross-checked by Comprehend. Malformed judge output
  gets one reprompt, then is marked `unparseable` — never guessed.
- **Every number traces to raw.** Each observation links to its archived raw
  responses in S3; any headline metric can be audited down to the original text.
- **Versioned prompts, comparable trends.** Trend deltas only compare
  same-version prompts across runs.

**Derived analytics (SQL aggregations over `observation`):**
- **Visibility score** — % of samples across product-intent prompts mentioning
  the store's products.
- **Average rank / prominence** — when present, how high; headline pick vs.
  afterthought.
- **Share of voice** — per prompt and aggregate: store vs. each competitor's
  presence rate.
- **Engine breakdown** — e.g., strong on Bedrock models but invisible on
  Perplexity (which cites live web) → actionable web-presence vs. training-data
  insight.
- **Sentiment/framing distribution** — price vs. quality vs. durability framing.
- **Citation sources** — which domains engines lean on for the category; where
  the merchant should seek mentions.
- **Before/after deltas** — after a fix is applied, subsequent runs show presence
  movement, computed over pooled windows per the statistical rules above (never
  a single-run 3/5 → 4/5 comparison). This causal-improvement story is both
  merchant ROI and the portfolio case study.

## 6. Eval harness

- **Golden fixtures** (S3): hand-labeled (prompt, raw response → correct
  `{present, rank, competitors…}`). Judge scored for precision/recall on presence
  detection and competitor extraction. Regression fails CI.
- **Judge-vs-Comprehend agreement rate** tracked live on real runs.
- **Fix-safety evals:** adversarial fixtures where the correct answer is
  *"insufficient data — don't draft a claim."* Proves Guardrails + prompt refuse
  to hallucinate.

## 7. Error handling

- **Engine call failures** (throttling, timeouts, Perplexity 5xx): worker retries
  with backoff; Step Functions `Catch` routes dead prompts to
  `failed_observations`; run completes partial, marked `degraded` with coverage %.
- **Bedrock throttling under fan-out:** bounded Map concurrency + token-bucket
  pacing; per-model quotas respected.
- **Malformed judge JSON:** forced tool schema + one reprompt → else
  `unparseable`, raw archived, never guessed.
- **Guardrail intervention:** recorded as `refused` with reason (also eval
  material).
- **Cost blast radius:** per-run hard cap on total model calls; run halts and
  alerts if exceeded.

## 8. Testing

- **Unit:** prompt generation, entity matching, envelope parsing, aggregation
  SQL — mocked model responses.
- **Eval harness in CI:** golden fixtures; precision/recall thresholds gate merges.
- **Integration:** one real end-to-end run against a seeded test catalog through
  actual Step Functions + Bedrock, tiny prompt set.
- **Contract test:** ingestion JSON schema, so connectors and engine can't drift.

## 9. MVP scope

**In v1:**
- Ingestion API + Postgres + a reference catalog connector.
- Prompt gen for product-intent + light brand-SoV layer.
- Bedrock multi-model (2–3 models to start) + **Perplexity** as the one real surface.
- Full analysis → diagnosis → fix-draft pipeline with Guardrails.
- Eval harness with golden + fix-safety fixtures.
- Dashboard: presence trends, SoV, competitor gaps, drafted-fix review.

**Deferred (own spec cycles):**
- **Adaptive sampling** — the first product-tier upgrade. Allocate samples where
  they buy information: prompts near 0%/100% are stable (N=3 confirms), prompts
  in the contested 30–70% band and prompts with pending fixes get boosted N
  (15–25). Keeps cost roughly flat vs. naive high-N everywhere. No architecture
  change: variable N is already supported by `samples_total`/`samples_present`;
  allocation is orchestrator batching logic.
- Additional catalog connectors.
- Additional real surfaces (ChatGPT, Google AI Overviews).
- Auto-applying fixes to the store (connector responsibility).
- Multi-tenant billing/auth hardening.
- Hosted MCP/agent-feed pillar.

## 10. Portfolio talking points (why this design)

1. Multi-model orchestration on Bedrock (Converse) with a real serverless
   fan-out (Step Functions Map, throttle-aware).
2. LLM-as-judge with a **measured** accuracy harness (golden sets, CI-gated
   precision/recall) — not vibes.
3. Guardrailed generation with refusal evals — safety as a feature.
4. Defensible measurement methodology: sampled presence rates, cross-checks,
   raw-response auditability, versioned prompts.
5. Scales to zero: near-free to keep running as a living demo.
