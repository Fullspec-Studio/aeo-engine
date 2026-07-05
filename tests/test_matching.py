from aeo.matching import match_product, normalize
from aeo.models import Product

PRODUCTS = [
    Product(sku="ACME-TRAIL-2", title="Acme Trail 2 Hiking Boot", category="hiking-boots"),
    Product(sku="ACME-CAMP-STOVE", title="Acme Ultralight Camp Stove"),
]


def test_normalize_strips_punctuation_case_and_roman():
    assert normalize("the Acme Trail II!") == "the acme trail 2"
    assert normalize("Acme's  Camp   Stove") == "acme camp stove"


def test_exact_title_match():
    assert match_product("Acme Trail 2 Hiking Boot", PRODUCTS) == "ACME-TRAIL-2"


def test_fuzzy_variants_match():
    assert match_product("the Acme Trail II", PRODUCTS) == "ACME-TRAIL-2"
    assert match_product("Acme's ultralight camp stove", PRODUCTS) == "ACME-CAMP-STOVE"


def test_competitor_does_not_match():
    assert match_product("Merrell Moab 3", PRODUCTS) is None


def test_generic_brand_mention_does_not_match_a_sku():
    # Brand alone is ambiguous between two products — must not match either.
    assert match_product("Acme", PRODUCTS) is None


def test_single_token_title_matches():
    products = [Product(sku="ACME-WIDGET", title="Widget")]
    assert match_product("the Widget", products) == "ACME-WIDGET"


def test_sku_mention_matches():
    assert match_product("I recommend the ACME-TRAIL-2", PRODUCTS) == "ACME-TRAIL-2"
