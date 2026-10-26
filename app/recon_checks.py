"""SQL reconciliation checks. Each returns a list of problem rows; empty means pass."""


def unbalanced_transfers(con):
    """Double-entry rule: every transfer has exactly one debit and one credit that sum to zero."""
    return [tuple(r) for r in con.execute("""
        SELECT t.reference, COUNT(l.id) AS entries, COALESCE(SUM(l.amount_cents), 0) AS net
        FROM transactions t
        LEFT JOIN ledger_entries l ON l.transaction_id = t.id
        WHERE t.type = 'TRANSFER'
        GROUP BY t.id
        HAVING COUNT(l.id) != 2 OR COALESCE(SUM(l.amount_cents), 0) != 0
        ORDER BY t.reference
    """)]


def balance_mismatches(con):
    """Stored balance must equal the sum of the account's ledger entries."""
    return [tuple(r) for r in con.execute("""
        SELECT a.id, a.balance_cents, COALESCE(SUM(l.amount_cents), 0) AS ledger_total
        FROM accounts a
        LEFT JOIN ledger_entries l ON l.account_id = a.id
        GROUP BY a.id
        HAVING a.balance_cents != COALESCE(SUM(l.amount_cents), 0)
        ORDER BY a.id
    """)]


def negative_balances(con):
    """Customer accounts have no overdraft facility, so no balance may go below zero."""
    return [tuple(r) for r in con.execute("""
        SELECT id, balance_cents FROM accounts WHERE balance_cents < 0 ORDER BY id
    """)]


def settlement_missing_in_core(con):
    """The switch says a payment happened, but the core banking system has no record of it."""
    return [tuple(r) for r in con.execute("""
        SELECT s.reference, s.amount_cents
        FROM switch_settlement s
        LEFT JOIN transactions t ON t.reference = s.reference
        WHERE t.id IS NULL
        ORDER BY s.reference
    """)]


def settlement_amount_mismatches(con):
    """The switch settled a different amount than the core posted."""
    return [tuple(r) for r in con.execute("""
        SELECT s.reference, t.amount_cents AS core_amount, s.amount_cents AS switch_amount
        FROM switch_settlement s
        JOIN transactions t ON t.reference = s.reference
        WHERE s.amount_cents != t.amount_cents
        ORDER BY s.reference
    """)]


def duplicate_settlement_records(con):
    """The same settlement record received more than once would be paid twice."""
    return [tuple(r) for r in con.execute("""
        SELECT reference, COUNT(*) AS copies
        FROM switch_settlement
        GROUP BY reference
        HAVING COUNT(*) > 1
        ORDER BY reference
    """)]


ALL_CHECKS = [unbalanced_transfers, balance_mismatches, negative_balances,
              settlement_missing_in_core, settlement_amount_mismatches, duplicate_settlement_records]
