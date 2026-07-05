from aeo import config


def test_config_constants():
    assert config.DEFAULT_SAMPLES_PER_PROMPT == 5
    assert config.MAX_CALLS_PER_RUN == 4000
    assert len(config.BEDROCK_MODEL_IDS) >= 2
    assert config.JUDGE_MODEL_ID
