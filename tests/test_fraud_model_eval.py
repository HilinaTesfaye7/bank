"""Fraud model evaluation: precision/recall on labeled transactions and a release
gate that blocks a model version which misses real fraud."""
import json
from pathlib import Path

import pytest

from evals.metrics import evaluate_fraud
from evals.run_fraud_eval import THRESHOLDS, gate

DATA = Path(__file__).resolve().parent.parent / "data"
LABELED = json.loads((DATA / "labeled_transactions.json").read_text(encoding="utf-8"))
V1 = json.loads((DATA / "fraud_scores_model_v1.json").read_text(encoding="utf-8"))
V2 = json.loads((DATA / "fraud_scores_model_v2.json").read_text(encoding="utf-8"))


# ---- the metric itself must be right before we trust it ----

def test_confusion_counts_on_a_known_example():
    labeled = [
        {"txn_id": "a", "scenario": "", "amount": 10, "is_fraud": True},    # caught      -> TP
        {"txn_id": "b", "scenario": "", "amount": 10, "is_fraud": True},    # missed      -> FN
        {"txn_id": "c", "scenario": "", "amount": 10, "is_fraud": False},   # false alarm -> FP
        {"txn_id": "d", "scenario": "", "amount": 10, "is_fraud": False},   # ignored     -> TN
    ]
    scores = [{"txn_id": "a", "risk_score": 0.9}, {"txn_id": "b", "risk_score": 0.2},
              {"txn_id": "c", "risk_score": 0.5}, {"txn_id": "d", "risk_score": 0.1}]
    r = evaluate_fraud(labeled, scores)
    assert (r["tp"], r["fn"], r["fp"], r["tn"]) == (1, 1, 1, 1)
    assert (r["precision"], r["recall"]) == (0.5, 0.5)


def test_missing_or_out_of_range_scores_are_reported():
    labeled = [{"txn_id": t, "scenario": "", "amount": 1, "is_fraud": True} for t in ("a", "b", "c")]
    r = evaluate_fraud(labeled, [{"txn_id": "a", "risk_score": 1.7}, {"txn_id": "b", "risk_score": None}])
    assert [i["txn_id"] for i in r["invalid_scores"]] == ["a", "b", "c"]
    assert r["tp"] == 0          # an invalid score never counts as a catch


# ---- the data itself ----

def test_labeled_set_has_both_classes_and_unique_ids():
    ids = [t["txn_id"] for t in LABELED]
    assert len(ids) == len(set(ids)) == 20
    assert sum(t["is_fraud"] for t in LABELED) == 8


@pytest.mark.parametrize("outputs", [V1, V2], ids=["v1", "v2"])
def test_every_transaction_has_a_valid_score(outputs):
    assert evaluate_fraud(LABELED, outputs)["invalid_scores"] == []


# ---- the approved model meets the bar ----

def test_baseline_meets_thresholds():
    r = evaluate_fraud(LABELED, V1)
    assert r["recall"] >= THRESHOLDS["recall"]
    assert r["precision"] >= THRESHOLDS["precision"]
    assert r["high_value_missed"] == []
    assert gate(r, r) == []


# ---- a worse model is caught, even though it "looks better" ----

def test_v2_has_fewer_false_alarms_but_misses_fraud():
    v1, v2 = evaluate_fraud(LABELED, V1), evaluate_fraud(LABELED, V2)
    assert v2["precision"] > v1["precision"]            # fewer false alarms...
    assert v2["recall"] < v1["recall"]                  # ...because it stopped catching fraud
    assert {m["txn_id"] for m in v2["missed_fraud"]} == {"FT-005", "FT-007"}


def test_release_gate_blocks_v2():
    problems = gate(evaluate_fraud(LABELED, V2), evaluate_fraud(LABELED, V1))
    assert any(p.startswith("recall 0.75 <") for p in problems)
    assert any("high-value fraud missed" in p and "FT-005" in p for p in problems)
    assert any("recall regressed" in p for p in problems)
