"""SQL reconciliation checks.

Each check runs twice: on clean data it must find nothing, and on data with
planted defects it must find exactly those defects. The second half proves the
check actually works instead of passing silently.
"""
import pytest

from app import recon_checks as rc


def test_row_counts(clean_db):
    counts = {t: clean_db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("customers", "accounts", "transactions", "ledger_entries", "beneficiaries", "switch_settlement")}
    assert counts == {"customers": 3, "accounts": 6, "transactions": 9, "ledger_entries": 12,
                      "beneficiaries": 4, "switch_settlement": 3}


def test_total_money_in_the_bank_equals_opening_balances(clean_db):
    """Transfers move money between accounts; they must never create or destroy it."""
    opening = clean_db.execute("SELECT SUM(amount_cents) FROM transactions WHERE type = 'OPENING'").fetchone()[0]
    balances = clean_db.execute("SELECT SUM(balance_cents) FROM accounts").fetchone()[0]
    assert balances == opening


@pytest.mark.parametrize("check", rc.ALL_CHECKS, ids=lambda c: c.__name__)
def test_clean_data_passes(clean_db, check):
    assert check(clean_db) == []


def test_detects_unbalanced_transfer(dirty_db):
    # 500.00 debited, 400.00 credited: 100.00 vanished.
    assert rc.unbalanced_transfers(dirty_db) == [("TRF-BAD-001", 2, -10000)]


def test_detects_balance_edited_outside_the_ledger(dirty_db):
    assert rc.balance_mismatches(dirty_db) == [("1000000002", 220000, 120000)]


def test_detects_overdrawn_account(dirty_db):
    assert rc.negative_balances(dirty_db) == [("1000000004", -285000)]


def test_detects_settlement_record_missing_in_core(dirty_db):
    assert rc.settlement_missing_in_core(dirty_db) == [("TRF-GHOST-001", 300000)]


def test_detects_settlement_amount_mismatch(dirty_db):
    assert rc.settlement_amount_mismatches(dirty_db) == [("TRF-SEED-002", 75000, 750000)]


def test_detects_duplicate_settlement_record(dirty_db):
    assert rc.duplicate_settlement_records(dirty_db) == [("TRF-SEED-003", 2)]
