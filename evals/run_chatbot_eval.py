"""Release gate for a new version of the banking chatbot.

Usage:
    python -m evals.run_chatbot_eval --candidate data/chatbot_answers_v2.json

Fails (exit code 1) on any safety violation: a full account number shown, a
number not found in the approved source, or a request for an OTP, PIN or password.
"""
import argparse
import json
import sys
from pathlib import Path

from evals.metrics import evaluate_chatbot

ROOT = Path(__file__).resolve().parent.parent


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def gate(candidate):
    return [f"{v['case_id']}: {v['check']}" + (f" {v['detail']}" if "detail" in v else "")
            for v in candidate["violations"]]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--golden", default=ROOT / "data/chatbot_golden.json")
    p.add_argument("--candidate", default=ROOT / "data/chatbot_answers_v1.json")
    p.add_argument("--report", default=ROOT / "chatbot_report.json")
    a = p.parse_args(argv)

    result = evaluate_chatbot(load(a.golden), load(a.candidate))
    problems = gate(result)
    Path(a.report).write_text(json.dumps({"result": result, "problems": problems}, indent=2), encoding="utf-8")
    print(f"Chatbot: {result['passed']}/{result['cases']} answers passed (pass rate {result['pass_rate']})")
    if problems:
        print("\nBLOCKED:\n  " + "\n  ".join(problems))
        return 1
    print("\nPASSED release gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
