# Run analytics — demo store, run 36

30 auto-generated buyer-intent + brand prompts x 4 engines x 5 samples = 600 engine answers, judged by Claude Sonnet 4.5 with Comprehend cross-checks.

## Presence rate by engine (fictional brand 'Acme Outdoor')

| Engine | Recommended | Rate |
|---|---|---|
| Claude Haiku 4.5 | 0/150 | 0.0% |
| Llama 3.1 70B | 2/150 | 1.3% |
| Mistral Large | 1/150 | 0.7% |
| Perplexity sonar | 0/150 | 0.0% |

A fictional brand is (correctly) near-invisible in AI shopping answers. The engine responded with **110 diagnoses** and **444 drafted fixes** (0 guardrail refusals).

## Who owns the shelf instead

| Competitor | Mentions across answers |
|---|---|
| Salomon | 60 |
| Merrell | 59 |
| KEEN | 46 |
| MSR | 39 |
| La Sportiva | 35 |
| Jetboil | 29 |
| Coleman | 27 |
| Vasque | 21 |
| Columbia | 20 |
| Danner | 20 |

## Judge-quality delta (why evals matter)

Run 35 (pre-fix judge): Bedrock presence 20/450 — inflated by mention-as-presence misjudgments.
Run 36 (eval-hardened judge): 3/450. Same store, same prompts; the delta is measurement error, removed.
