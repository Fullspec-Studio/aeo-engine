import pytest

from evals.run_evals import competitor_f1, score_presence


def test_score_presence():
    #                 (expected, actual)
    s = score_presence([(True, True), (True, False), (False, False), (False, True)])
    assert s["precision"] == pytest.approx(0.5)  # 1 TP / (1 TP + 1 FP)
    assert s["recall"] == pytest.approx(0.5)     # 1 TP / (1 TP + 1 FN)


def test_score_presence_perfect():
    s = score_presence([(True, True), (False, False)])
    assert s["precision"] == 1.0 and s["recall"] == 1.0


def test_competitor_f1_normalized():
    assert competitor_f1(["Merrell Moab 3"], ["the Merrell Moab III"]) == pytest.approx(1.0)
    assert competitor_f1(["Merrell Moab"], []) == 0.0
