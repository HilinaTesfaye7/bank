"""API tests for beneficiary management: OTP verification, lockout and masking."""
import pytest
import requests

from app.core import MOCK_OTP

pytestmark = pytest.mark.usefixtures("reset")


def add(base_url, headers, who, account_id, nickname="Test"):
    return requests.post(f"{base_url}/api/beneficiaries", json={"account_id": account_id, "nickname": nickname},
                         headers=headers(who))


def verify(base_url, headers, who, beneficiary_id, otp):
    return requests.post(f"{base_url}/api/beneficiaries/{beneficiary_id}/verify", json={"otp": otp},
                         headers=headers(who))


def send(base_url, headers, who, from_account, to_account):
    return requests.post(f"{base_url}/api/transfers", headers=headers(who), json={
        "from_account": from_account, "to_account": to_account, "amount": "50.00", "otp": MOCK_OTP})


def test_new_beneficiary_starts_pending_and_masked(base_url, headers):
    r = add(base_url, headers, "sara", "1000000004", "Daniel")
    assert r.status_code == 201
    assert r.json()["status"] == "PENDING"
    assert r.json()["account_id"] == "******0004"
    assert "1000000004" not in r.text


@pytest.mark.parametrize("who, account_id, status", [
    ("abebe", "1000000001", 422),      # own account
    ("abebe", "9999999999", 404),      # account does not exist
    ("abebe", "1000000003", 409),      # already a beneficiary
    ("abebe", "", 400),                # missing
])
def test_add_beneficiary_errors(base_url, headers, who, account_id, status):
    assert add(base_url, headers, who, account_id).status_code == status


def test_money_only_flows_after_otp_verification(base_url, headers):
    ben = add(base_url, headers, "sara", "1000000001", "Abebe").json()
    assert send(base_url, headers, "sara", "1000000003", "1000000001").status_code == 422
    assert verify(base_url, headers, "sara", ben["id"], "999999").status_code == 400
    ok = verify(base_url, headers, "sara", ben["id"], MOCK_OTP)
    assert (ok.status_code, ok.json()["status"]) == (200, "ACTIVE")
    assert send(base_url, headers, "sara", "1000000003", "1000000001").status_code == 201


def test_three_wrong_otps_lock_the_beneficiary(base_url, headers):
    ben = add(base_url, headers, "sara", "1000000001").json()
    codes = [verify(base_url, headers, "sara", ben["id"], "000000").status_code for _ in range(3)]
    assert codes == [400, 400, 423]
    # Even the correct code no longer works once locked.
    assert verify(base_url, headers, "sara", ben["id"], MOCK_OTP).status_code == 423


def test_cannot_verify_another_customers_beneficiary(base_url, headers):
    ben = add(base_url, headers, "sara", "1000000001").json()
    assert verify(base_url, headers, "abebe", ben["id"], MOCK_OTP).status_code == 404


def test_beneficiary_list_never_shows_full_account_numbers(base_url, headers):
    r = requests.get(f"{base_url}/api/beneficiaries", headers=headers("abebe"))
    assert r.status_code == 200
    assert all(b["account_id"].startswith("******") for b in r.json())
    assert "100000000" not in r.text
