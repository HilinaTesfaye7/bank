"""A small mock internet banking service:
- REST API for accounts, transfers and beneficiaries (with OTP verification)
- an internet banking page for making a transfer

It is intentionally simple so the tests, not the app, are the focus.
"""
import os
from contextlib import closing

from flask import Flask, jsonify, redirect, request

from app import core, db
from app.core import BankError

DB_PATH = os.environ.get("BANK_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "bank.db"))

# Mock sessions: a real bank would issue these at login.
TOKENS = {"demo-token-abebe": 1, "demo-token-sara": 2, "demo-token-daniel": 3}

app = Flask(__name__)


def _customer():
    auth = request.headers.get("Authorization", "")
    customer_id = TOKENS.get(auth[len("Bearer "):]) if auth.startswith("Bearer ") else None
    if customer_id is None:
        raise BankError(401, "unauthorized")
    return customer_id


def _db():
    return closing(db.connect(DB_PATH))


@app.errorhandler(BankError)
def bank_error(e):
    return jsonify(error=e.message), e.status


@app.get("/api/accounts")
def accounts():
    customer_id = _customer()
    with _db() as con:
        return jsonify(core.list_accounts(con, customer_id))


@app.get("/api/accounts/<account_id>/transactions")
def transactions(account_id):
    customer_id = _customer()
    with _db() as con:
        return jsonify(core.account_history(con, customer_id, account_id))


@app.post("/api/transfers")
def transfer():
    customer_id = _customer()
    with _db() as con:
        txn, created = core.make_transfer(con, customer_id, request.get_json(silent=True),
                                          request.headers.get("Idempotency-Key"))
    return jsonify(txn), 201 if created else 200


@app.get("/api/beneficiaries")
def beneficiaries():
    customer_id = _customer()
    with _db() as con:
        return jsonify(core.list_beneficiaries(con, customer_id))


@app.post("/api/beneficiaries")
def add_beneficiary():
    customer_id = _customer()
    with _db() as con:
        return jsonify(core.add_beneficiary(con, customer_id, request.get_json(silent=True))), 201


@app.post("/api/beneficiaries/<int:beneficiary_id>/verify")
def verify_beneficiary(beneficiary_id):
    customer_id = _customer()
    otp = (request.get_json(silent=True) or {}).get("otp")
    with _db() as con:
        return jsonify(core.verify_beneficiary(con, customer_id, beneficiary_id, otp))


@app.post("/api/_reset")
def reset():
    db.build(DB_PATH)
    return "", 204


@app.get("/")
def home():
    return redirect("/banking")


@app.get("/banking")
def banking():
    return app.send_static_file("banking.html")


if __name__ == "__main__":
    db.build(DB_PATH)
    app.run(port=5006)
