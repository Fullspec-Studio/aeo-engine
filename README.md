# AEO Monitoring Engine

AEO is an answer-engine monitoring system that tracks how well your products appear
across LLM-backed search engines (Amazon Bedrock models, Perplexity). It
continuously samples prompts, judges responses for brand presence and quality, and
surfaces regressions through statistical trend analysis and guardrailed fix drafts.

See the [design specification](docs/2026-07-03-aeo-monitoring-engine-design.md) and
[core engine plan](docs/2026-07-04-aeo-core-engine-plan.md) for full details.

---

## Architecture

```
                        ┌─────────────────────────────────────────────────┐
                        │  AWS Account                                    │
                        │                                                 │
 Connector / curl          │  API Gateway (REST)                             │
 ─────────────────────► │   POST /catalog  ──► Ingest Lambda              │
 (x-api-key required)   │                      (aeo.ingestion.handler)    │
                        │                            │ upsert store/prods  │
                        │                            ▼                    │
                        │                      Aurora Serverless v2       │
                        │                      (PostgreSQL 16.4)          │
                        │                            ▲                    │
                        │  EventBridge Scheduler     │                    │
                        │  (rate 7 days)             │                    │
                        │       │                    │                    │
                        │       ▼                    │                    │
                        │  Step Functions            │                    │
                        │  ┌─────────────┐           │                    │
                        │  │  PlanRun    │ ──────────┘                    │
                        │  └──────┬──────┘                                │
                        │         │ $.batches (one per prompt)            │
                        │  ┌──────▼──────┐  Map (max 5 concurrent)       │
                        │  │PerPrompt Map│                                │
                        │  │  ┌────────────────────────────────────┐     │
                        │  │  │ QueryEngines ► Analyze ► Diagnose  │     │
                        │  │  └────────────────────────────────────┘     │
                        │  └──────┬──────┘                                │
                        │         │                                        │
                        │  ┌──────▼──────┐                                │
                        │  │   Persist   │                                │
                        │  └─────────────┘                                │
                        │         │                                        │
                        │         ▼                                        │
                        │  Aurora (observations, diagnoses, fix_drafts)   │
                        │  S3  (raw-responses bucket, 180-day lifecycle)   │
                        └─────────────────────────────────────────────────┘

Secrets Manager secrets
  aeo/aurora-dsn   — auto-created by RDS; JSON with username/password/host/port/dbname
  aeo/perplexity   — PERPLEXITY_API_KEY (set manually, see below)
```

---

## Prerequisites

- Python 3.12, [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Node.js 20+ and npm (for CDK CLI)
- Docker (for local Postgres tests and CDK bundling)
- An AWS account with Bedrock model access enabled in your target region

---

## Local development

```bash
# Install dependencies (creates .venv)
uv sync

# Run unit tests (no AWS needed)
uv run pytest -m "not integration" -v

# Run integration tests (needs a local Postgres)
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:16
export AEO_TEST_DSN="postgresql://postgres:test@localhost:5432/postgres"
uv run pytest -v
```

---

## Deploy to AWS

```bash
# 1. Install dependencies
uv sync

# 2. Install CDK (once)
npm install -g aws-cdk

# 3. Bootstrap CDK in your account/region (once per account/region)
npx cdk bootstrap

# 4. Deploy all stacks (DataStack → ApiStack + PipelineStack)
npx cdk deploy --all --app "uv run python infra/app.py"
```

CDK deploys in dependency order: `AeoData` first (VPC, Aurora, S3), then
`AeoApi` and `AeoPipeline` which reference its outputs.

After the first deploy:

```bash
# 5. Apply the database schema (one-off)
export AEO_DSN="postgresql://..."   # grab from Secrets Manager or cdk output
uv run python -c "from aeo.db import repo; conn = repo.connect('$AEO_DSN'); repo.apply_schema(conn)"

# 6. Ingest your first store catalog
API_URL=$(aws cloudformation describe-stacks --stack-name AeoApi \
  --query "Stacks[0].Outputs[?OutputKey=='AeoApiEndpoint'].OutputValue" --output text)
API_KEY=$(aws apigateway get-api-keys --include-values \
  --query "items[?name=='ConnectorKey'].value" --output text)
curl -X POST "$API_URL/catalog" \
     -H "x-api-key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/catalog_push.json
```

---

## Trigger a run manually

```bash
# Get the state machine ARN
SM_ARN=$(aws cloudformation describe-stacks --stack-name AeoPipeline \
  --query "Stacks[0].Outputs[?OutputKey=='RunMachineArn'].OutputValue" --output text)

# Start execution
aws stepfunctions start-execution \
  --state-machine-arn "$SM_ARN" \
  --input '{"store_key": "demo-outdoor-store"}'

# Watch progress in the console
aws stepfunctions get-execution-history \
  --execution-arn <arn-from-above> --query "events[-5:]"
```

---

## Perplexity API key setup

The `QueryEngines` Lambda needs a Perplexity API key. Store it in Secrets Manager
under the path `aeo/perplexity` as a plaintext string:

```bash
aws secretsmanager create-secret \
  --name aeo/perplexity \
  --secret-string "pplx-..."
```

The PipelineStack passes `PERPLEXITY_API_KEY` to the Lambda via an environment
variable sourced from this secret (add `fn.add_environment` referencing the secret
or use Parameter Store; see `infra/stacks/pipeline_stack.py`).

**For local development with 1Password**, resolve the key at runtime without
embedding it in config files:

```bash
export PERPLEXITY_API_KEY=$(op read "op://Private/Perplexity/credential")
```

---

## Run evals

The eval harness (`evals/run_evals.py`) validates judge accuracy, presence-rate
statistics, fix-safety guardrails, and aggregation correctness against golden
fixtures.

```bash
# Offline golden-fixture tests (fast, no AWS)
uv run pytest evals/ -v

# Live end-to-end sweep against real Bedrock + Perplexity
export AEO_DSN="postgresql://..."
export PERPLEXITY_API_KEY=$(op read "op://Private/Perplexity/credential")
uv run python evals/run_evals.py --live
```

The `--live` flag runs a real sample sweep and asserts that presence rates, judge
F1, and fix-safety thresholds all pass. It requires a seeded database with at least
one store and active prompts.
