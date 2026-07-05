"""Normalization + fuzzy matching of model-text mentions to catalog SKUs (spec §4).
Never naive string equality: handles case, punctuation, possessives, roman numerals."""
import re

from aeo.models import Product

_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5"}
_STOPWORDS = {"the", "a", "an", "boot", "boots", "shoe", "shoes"}


def normalize(text: str) -> str:
    t = text.lower().replace("'s", " ").replace("'s", " ")
    t = re.sub(r"[^\w\s]", " ", t)
    tokens = [_ROMAN.get(tok, tok) for tok in t.split()]
    return " ".join(tokens)


def _tokens(text: str) -> set[str]:
    return {tok for tok in normalize(text).split() if tok not in _STOPWORDS}


def match_product(mention: str, products: list[Product]) -> str | None:
    """Return the SKU whose title tokens best cover the mention (Jaccard >= 0.5),
    requiring at least 2 shared tokens so bare brand names never match a SKU."""
    m = _tokens(mention)
    if not m:
        return None
    best_sku, best_score = None, 0.0
    for p in products:
        pt = _tokens(p.title) | {normalize(p.sku).replace(" ", "-")}
        shared = m & pt
        if len(shared) < 2:
            continue
        score = len(shared) / len(m | pt)
        if score > best_score:
            best_sku, best_score = p.sku, score
    return best_sku if best_score >= 0.5 else None
