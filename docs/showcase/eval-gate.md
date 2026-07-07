# Live eval gate

The judge and fix-drafter are gated by hand-labeled fixtures scored against
`evals/thresholds.json` on every release. Below: a real gate run against Bedrock
(us-west-2), and the defect-catch story from first live contact.

## Current gate run

```text
g001: present exp=True got=True
g002: present exp=False got=False
g003: present exp=False got=False
g004: present exp=True got=True
f001: no_attr_expected=True drafted_attr=False
f002: no_attr_expected=False drafted_attr=False
{
  "presence_precision": 1.0,
  "presence_recall": 1.0,
  "competitor_f1": 1.0,
  "refusal_accuracy": 1.0
}
All eval thresholds met.
```

## What the gate caught on first live contact (2026-07-05)

| Run | Finding | Metric | Fix |
|---|---|---|---|
| 1 | Judge counted a *critical mention* as presence (fixture g003) | presence_precision 0.67 < 0.90 → **gate failed** | "Mention is not recommendation" rule added to the judge system prompt |
| 2 | Fix-drafter intermittently invented a product attribute (fixture f001) at temperature 0.2 | refusal_accuracy 0.50 < 1.00 → **gate failed** | Temperature 0 + verbatim-only attribute rule |
| 3–5 | — | all thresholds met, three consecutive clean gates | shipped |

Effect in production data: Bedrock presence for the demo store dropped from
20/450 (run 35, mention-inflated) to 3/450 (run 36, fixed judge) — the defect,
quantified and removed.
