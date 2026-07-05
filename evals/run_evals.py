"""Eval harness (spec §6). Scoring is pure; --live judges golden fixtures on Bedrock.
Exit 1 if any metric falls below evals/thresholds.json — this gates releases."""
import argparse
import json
import sys
from pathlib import Path

from aeo.matching import normalize

ROOT = Path(__file__).parent


def score_presence(results: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for e, a in results if e and a)
    fp = sum(1 for e, a in results if not e and a)
    fn = sum(1 for e, a in results if e and not a)
    return {"precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 1.0}


def competitor_f1(expected: list[str], actual: list[str]) -> float:
    def keyset(names):
        return {frozenset(normalize(n).split()) for n in names}
    e, a = keyset(expected), keyset(actual)
    matched = sum(1 for x in a if any(x & y and (len(x & y) / len(x | y)) >= 0.5 for y in e))
    if not e and not a:
        return 1.0
    prec = matched / len(a) if a else 0.0
    rec = matched / len(e) if e else 0.0
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def run_live() -> dict:
    import boto3

    from aeo.diagnosis.fixer import Diagnosis, diagnose
    from aeo.analysis.judge import judge_answer
    from aeo.ingestion.contract import CatalogPush
    from aeo.models import Product

    bedrock = boto3.client("bedrock-runtime")
    presence_pairs, f1s = [], []
    for path in sorted((ROOT / "fixtures" / "golden").glob("*.json")):
        fx = json.loads(path.read_text())
        cat = CatalogPush.model_validate(fx["catalog"])
        jr = judge_answer(bedrock, fx["answer_text"], cat.products, cat.brand_names, cat.competitors)
        actual_present = bool(jr and jr.present)
        presence_pairs.append((fx["expected"]["present"], actual_present))
        f1s.append(competitor_f1(fx["expected"]["competitors_named"],
                                 jr.competitors_named if jr else []))
        print(f"{fx['id']}: present exp={fx['expected']['present']} got={actual_present}")

    refusal_hits, refusal_total = 0, 0
    for path in sorted((ROOT / "fixtures" / "fix_safety").glob("*.json")):
        fx = json.loads(path.read_text())
        result = diagnose(bedrock, "", "DRAFT", Product.model_validate(fx["product"]),
                          fx["losing_answers"], fx["winning_competitors"])
        drafted_attr = isinstance(result, Diagnosis) and any(f.kind == "attribute" for f in result.fixes)
        ok = (not drafted_attr) if fx["expect_no_attribute_fixes"] else True
        refusal_hits += ok
        refusal_total += 1
        print(f"{fx['id']}: no_attr_expected={fx['expect_no_attribute_fixes']} drafted_attr={drafted_attr}")

    p = score_presence(presence_pairs)
    return {"presence_precision": p["precision"], "presence_recall": p["recall"],
            "competitor_f1": sum(f1s) / len(f1s) if f1s else 1.0,
            "refusal_accuracy": refusal_hits / refusal_total if refusal_total else 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="call Bedrock (needs AWS creds)")
    args = ap.parse_args()
    if not args.live:
        print("Nothing to do without --live (scoring functions are unit-tested).")
        return
    metrics = run_live()
    thresholds = json.loads((ROOT / "thresholds.json").read_text())
    print(json.dumps(metrics, indent=2))
    failed = {k: (metrics[k], v) for k, v in thresholds.items() if metrics[k] < v}
    if failed:
        print(f"EVAL FAILURES: {failed}")
        sys.exit(1)
    print("All eval thresholds met.")


if __name__ == "__main__":
    main()
