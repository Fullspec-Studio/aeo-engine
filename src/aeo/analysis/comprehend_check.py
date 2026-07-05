"""Independent Comprehend backstop for the LLM judge (spec §4.2)."""
from aeo.matching import normalize
from aeo.models import JudgeResult

_OPPOSITE = {"positive": "NEGATIVE", "negative": "POSITIVE"}


def cross_check(comprehend, answer_text: str, judge: JudgeResult, brand_names: list[str]) -> str:
    if judge.present and not any(normalize(b) in normalize(answer_text) for b in brand_names):
        return "low_confidence"
    opposite = _OPPOSITE.get(judge.sentiment)
    if opposite:
        resp = comprehend.detect_sentiment(Text=answer_text[:4900], LanguageCode="en")
        score = resp["SentimentScore"].get(opposite.capitalize(), 0.0)
        if resp["Sentiment"] == opposite and score > 0.7:
            return "low_confidence"
    return "ok"
