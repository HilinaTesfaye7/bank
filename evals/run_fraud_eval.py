"""Release gate for a new fraud model version.

Usage:
    python -m evals.run_fraud_eval --candidate data/fraud_scores_model_v2.json

Fails (exit code 1) if the candidate misses too much fraud, misses any high-value
fraud, produces invalid scores, or catches less fraud than the approved model.
"""
import argparse
import json
import sys
from pathlib import Path

from evals.metrics import HIGH_VALUE_AMOUNT, evaluate_fraud

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = {"precision": 0.75, "recall": 0.90}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def gate(candidate, baseline):
    problems = []
    if candidate["recall"] < THRESHOLDS["recall"]:
        problems.append(f"recall {candidate['recall']} < {THRESHOLDS['recall']}")
    if candidate["precision"] < THRESHOLDS["precision"]:
        problems.append(f"precision {candidate['precision']} < {THRESHOLDS['precision']}")
    if candidate["high_value_missed"]:
        ids = ", ".join(m["txn_id"] for m in candidate["high_value_missed"])
        problems.append(f"high-value fraud missed (>= {HIGH_VALUE_AMOUNT:,.2f}): {ids}")
    if candidate["invalid_scores"]:
        problems.append(f"{len(candidate['invalid_scores'])} transaction(s) without a valid score")
    if candidate["recall"] < baseline["recall"]:
        problems.append(f"recall regressed: {baseline['recall']} -> {candidate['recall']}")
    return problems


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--labeled", default=ROOT / "data/labeled_transactions.json")
    p.add_argument("--baseline", default=ROOT / "data/fraud_scores_model_v1.json")
    p.add_argument("--candidate", default=ROOT / "data/fraud_scores_model_v1.json")
    p.add_argument("--report", default=ROOT / "fraud_report.json")
    a = p.parse_args(argv)

    labeled = load(a.labeled)
    base = evaluate_fraud(labeled, load(a.baseline))
    cand = evaluate_fraud(labeled, load(a.candidate))
    problems = gate(cand, base)

    Path(a.report).write_text(json.dumps({"baseline": base, "candidate": cand, "problems": problems}, indent=2),
                              encoding="utf-8")
    print(f"Baseline : precision={base['precision']} recall={base['recall']}")
    print(f"Candidate: precision={cand['precision']} recall={cand['recall']} "
          f"(TP={cand['tp']} FP={cand['fp']} FN={cand['fn']} TN={cand['tn']})")
    for m in cand["missed_fraud"]:
        print(f"  - MISSED  {m['txn_id']} {m['amount']:>10,.2f}  score={m['risk_score']}  {m['scenario']}")
    for f in cand["false_alarms"]:
        print(f"  - ALARM   {f['txn_id']} {f['amount']:>10,.2f}  score={f['risk_score']}  {f['scenario']}")
    if problems:
        print("\nBLOCKED:\n  " + "\n  ".join(problems))
        return 1
    print("\nPASSED release gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
