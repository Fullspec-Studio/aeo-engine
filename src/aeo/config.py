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
