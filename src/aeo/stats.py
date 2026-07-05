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
