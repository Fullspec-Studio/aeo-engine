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
