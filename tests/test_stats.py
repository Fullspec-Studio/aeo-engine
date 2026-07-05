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
