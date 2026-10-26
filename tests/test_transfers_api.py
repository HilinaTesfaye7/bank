"""API tests for transfers: happy path, auth, input validation, business rules,
limits, atomicity, idempotent retries and ledger integrity."""
import re

import pytest
import requests

from app import db
from app import recon_checks as rc
from app.core import MOCK_OTP

pytestmark = pytest.mark.usefixtures("reset")

ABEBE_ETB, ABEBE_USD, SARA, DANIEL, DANIEL_FROZEN = (
    "1000000001", "1000000002", "1000000003", "1000000004", "1000000005")


def transfer(base_url, headers, who="abebe", key=None, **overrides):
    payload = {"from_account": ABEBE_ETB, "to_account": SARA, "amount": "100.00", "otp": MOCK_OTP}
    payload.update(overrides)
    extra = {"Idempotency-Key": key} if key else {}
    return requests.post(f"{base_url}/api/transfers", json=payload, headers=headers(who, **extra))


def test_transfer_moves_money_and_returns_receipt(base_url, headers, balance):
    before_from, before_to = balance(ABEBE_ETB), balance(SARA, who="sara")
    r = transfer(base_url, headers, amount="1500.00", narrative="Rent October")
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"reference", "from_account", "to_account", "amount", "amount_cents",
                         "currency", "narrative", "status", "created_at"}
    assert re.fullmatch(r"TRF[0-9A-F]{10}", body["reference"])
    assert body["to_account"] == "******0003"          # recipient's number is masked
    assert (body["amount"], body["currency"], body["status"]) == ("1,500.00", "ETB", "COMPLETED")
    assert balance(ABEBE_ETB) == before_from - 150000
    assert balance(SARA, who="sara") == before_to + 150000


def test_transfer_by_beneficiary_id(base_url, headers):
    r = transfer(base_url, headers, to_account=None, beneficiary_id=1)
    assert r.status_code == 201
    assert r.json()["to_account"] == "******0003"


@pytest.mark.parametrize("auth", [
    {},                                              # no token
    {"Authorization": "Bearer wrong-token"},         # unknown token
    {"Authorization": "demo-token-abebe"},           # missing "Bearer" scheme
])
def test_rejects_missing_or_bad_token(base_url, auth):
    r = requests.post(f"{base_url}/api/transfers", json={"amount": "1"}, headers=auth)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


@pytest.mark.parametrize("missing", ["from_account", "amount", "to_account"])
def test_rejects_missing_fields(base_url, headers, missing):
    r = transfer(base_url, headers, **{missing: None})
    assert r.status_code == 400


def test_rejects_empty_body(base_url, headers):
    r = requests.post(f"{base_url}/api/transfers", data="not json", headers=headers())
    assert r.status_code == 400


@pytest.mark.parametrize("amount", ["abc", "-5", "0", "0.00", "10.555", "1e3", True, [100]])
def test_rejects_invalid_amount(base_url, headers, amount):
    r = transfer(base_url, headers, amount=amount)
    assert r.status_code == 400


@pytest.mark.parametrize("who, overrides, status, message", [
    ("abebe", {"to_account": ABEBE_ETB}, 422, "same account"),
    ("abebe", {"from_account": ABEBE_USD}, 422, "currency mismatch"),
    ("abebe", {"to_account": DANIEL_FROZEN}, 422, "frozen"),
    ("abebe", {"to_account": DANIEL}, 422, "not a verified beneficiary"),          # beneficiary still pending
    ("sara", {"from_account": SARA, "to_account": ABEBE_ETB}, 422, "not a verified beneficiary"),
    ("abebe", {"from_account": SARA}, 403, "does not belong to you"),             # someone else's account
    ("abebe", {"to_account": "9999999999"}, 404, "not found"),
    ("abebe", {"amount": "50000.01"}, 422, "single transfer limit"),
    ("abebe", {"otp": "000000"}, 403, "invalid OTP"),
    ("abebe", {"otp": None}, 403, "invalid OTP"),
], ids=["same-account", "currency", "frozen", "pending-beneficiary", "not-beneficiary",
        "not-owner", "unknown-account", "single-limit", "wrong-otp", "missing-otp"])
def test_business_rules(base_url, headers, who, overrides, status, message):
    r = transfer(base_url, headers, who=who, **overrides)
    assert r.status_code == status
    assert message in r.json()["error"]


def test_failed_transfer_changes_nothing(base_url, headers, balance):
    before = balance(ABEBE_ETB), balance(SARA, who="sara")
    assert transfer(base_url, headers, otp="000000").status_code == 403
    assert (balance(ABEBE_ETB), balance(SARA, who="sara")) == before


def test_insufficient_funds(base_url, headers, balance):
    before = balance(DANIEL, who="daniel")                     # 6,750.00
    r = transfer(base_url, headers, who="daniel", from_account=DANIEL, amount="10000.00")
    assert r.status_code == 422
    assert r.json()["error"] == "insufficient funds"
    assert balance(DANIEL, who="daniel") == before


def test_exact_balance_can_be_sent(base_url, headers, balance):
    """Boundary: sending the whole balance leaves exactly zero."""
    r = transfer(base_url, headers, who="daniel", from_account=DANIEL, amount="6750.00")
    assert r.status_code == 201
    assert balance(DANIEL, who="daniel") == 0


def test_daily_limit(base_url, headers):
    assert transfer(base_url, headers, amount="40000.00").status_code == 201
    assert transfer(base_url, headers, amount="40000.00").status_code == 201
    r = transfer(base_url, headers, amount="40000.00")             # 120,000 > 100,000 daily
    assert r.status_code == 422
    assert "daily transfer limit" in r.json()["error"]


def test_cents_add_up_exactly(base_url, headers, balance):
    before = balance(ABEBE_ETB)
    transfer(base_url, headers, amount="0.10")
    transfer(base_url, headers, amount="0.20")
    assert before - balance(ABEBE_ETB) == 30


def test_retry_with_same_idempotency_key_does_not_pay_twice(base_url, headers, balance):
    before = balance(ABEBE_ETB)
    first = transfer(base_url, headers, key="retry-1", amount="250.00")
    second = transfer(base_url, headers, key="retry-1", amount="250.00")
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["reference"] == first.json()["reference"]
    assert balance(ABEBE_ETB) == before - 25000


def test_idempotency_key_reused_for_a_different_transfer(base_url, headers):
    assert transfer(base_url, headers, key="k-2", amount="10.00").status_code == 201
    r = transfer(base_url, headers, key="k-2", amount="99.00")
    assert r.status_code == 409


def test_cannot_read_another_customers_history(base_url, headers):
    r = requests.get(f"{base_url}/api/accounts/{SARA}/transactions", headers=headers("abebe"))
    assert r.status_code == 404          # same answer as a missing account: nothing to probe


def test_books_stay_balanced_after_api_transfers(base_url, headers, db_path):
    """Integration: after successful and failed transfers, every reconciliation check still passes."""
    transfer(base_url, headers, amount="1200.00")
    transfer(base_url, headers, amount="0.01")
    transfer(base_url, headers, otp="000000")
    transfer(base_url, headers, who="daniel", from_account=DANIEL, amount="99999.00")
    transfer(base_url, headers, who="sara", from_account=SARA, to_account=SARA)
    con = db.connect(db_path)
    try:
        for check in rc.ALL_CHECKS:
            assert check(con) == [], check.__name__
    finally:
        con.close()
