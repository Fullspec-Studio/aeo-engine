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

# Optional: attach a Bedrock Guardrail to the DiagnoseAndDraft Lambda
npx cdk deploy --all --app "uv run python infra/app.py" \
  -c guardrailId=<your-guardrail-id> \
  -c guardrailVersion=DRAFT
```

CDK deploys in dependency order: `AeoData` first (VPC, Aurora, S3), then
`AeoApi` and `AeoPipeline` which reference its outputs.

After the first deploy:

```bash
# 5. Apply the database schema (one-off, runs inside the VPC via the Ingest Lambda)
INGEST_FN=$(aws cloudformation describe-stack-resources --stack-name AeoApi \
  --query "StackResources[?LogicalResourceId=='Ingest'].PhysicalResourceId" --output text)
aws lambda invoke --function-name "$INGEST_FN" \
  --payload '{"admin_action": "apply_schema"}' \
  --cli-binary-format raw-in-base64-out /dev/stdout

# 6. Ingest your first store catalog
API_URL=$(aws cloudformation describe-stacks --stack-name AeoApi \
  --query "Stacks[0].Outputs[?OutputKey=='AeoApiEndpoint'].OutputValue" --output text)
API_KEY=$(aws apigateway get-api-keys --include-values \
  --query "items[?name=='ConnectorKey'].value" --output text)
curl -X POST "$API_URL/catalog" \
     -H "x-api-key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/catalog_push.json

# 7. Seed prompts (generates AI-powered buyer-intent questions for your catalog)
aws lambda invoke --function-name "$INGEST_FN" \
  --payload '{"admin_action": "seed_prompts", "store_key": "demo-outdoor-store"}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
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

The `QueryEngines` Lambda needs a Perplexity API key. The PipelineStack reads it
automatically from AWS Secrets Manager under the name `aeo/perplexity` — the
operator just needs to create the secret once:

```bash
aws secretsmanager create-secret \
  --name aeo/perplexity \
  --secret-string "$(op read 'op://Private/Perplexity/credential')"
```

At deploy time, `PipelineStack` wires `PERPLEXITY_SECRET_ARN` into the
`QueryEngines` Lambda and grants it `secretsmanager:GetSecretValue`. The handler
resolves the key on first invocation and caches it for the lifetime of the
container — no manual environment variable injection required.

**For local development with 1Password**, resolve the key at runtime without
embedding it in config files:

```bash
export PERPLEXITY_API_KEY=$(op read "op://Private/Perplexity/credential")
```

---

## Run evals

The eval harness (`evals/run_evals.py`) judges 4 golden fixture answers and
2 fix-safety fixture answers live against Amazon Bedrock, then compares the
results to `evals/thresholds.json`. Exit code 1 means at least one threshold
was breached.

```bash
# Requires only AWS credentials with Bedrock access — no DSN, no Perplexity key needed.
uv run python evals/run_evals.py
```

To gate on different thresholds, edit `evals/thresholds.json` before running.
