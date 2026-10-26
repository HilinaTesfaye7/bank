"""SQLite schema and seed data for the mock bank.

Money is stored as integer cents, never as floats.
`build(path)` creates clean data. `build(path, dirty=True)` plants the defects
the reconciliation checks must catch: an unbalanced transfer, a balance edited
directly in the database, an overdrawn account, and three switch settlement
problems (a record the core never saw, a wrong amount and a duplicate).
"""
import sqlite3
import sys

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    currency TEXT NOT NULL,
    balance_cents INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    reference TEXT NOT NULL,
    type TEXT NOT NULL,
    customer_id INTEGER,
    from_account TEXT,
    to_account TEXT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    narrative TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE ledger_entries (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    amount_cents INTEGER NOT NULL
);
CREATE TABLE beneficiaries (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    nickname TEXT NOT NULL,
    status TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0
);
-- Records reported by the payment switch for end-of-day settlement.
CREATE TABLE switch_settlement (
    id INTEGER PRIMARY KEY,
    reference TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    settlement_date TEXT NOT NULL
);
"""

HISTORY_DATE = "2024-01-15T09:30:00+00:00"

CUSTOMERS = [(1, "Abebe Kebede"), (2, "Sara Haile"), (3, "Daniel Alemu")]

# (account number, customer, currency, opening balance in cents, status)
ACCOUNTS = [
    ("1000000001", 1, "ETB", 150_000_00, "ACTIVE"),
    ("1000000002", 1, "USD", 1_200_00, "ACTIVE"),
    ("1000000003", 2, "ETB", 40_000_00, "ACTIVE"),
    ("1000000004", 3, "ETB", 5_000_00, "ACTIVE"),
    ("1000000005", 3, "ETB", 12_000_00, "FROZEN"),
    ("1000000006", 2, "ETB", 8_000_00, "ACTIVE"),
]

# (id, customer, beneficiary account, nickname, status)
BENEFICIARIES = [
    (1, 1, "1000000003", "Sara Haile", "ACTIVE"),
    (2, 1, "1000000004", "Daniel Alemu", "PENDING"),
    (3, 1, "1000000005", "Daniel (old account)", "ACTIVE"),
    (4, 3, "1000000003", "Sara", "ACTIVE"),
]

# (reference, customer, from, to, amount in cents)
HISTORY = [
    ("TRF-SEED-001", 1, "1000000001", "1000000003", 2_500_00),
    ("TRF-SEED-002", 2, "1000000003", "1000000004", 750_00),
    ("TRF-SEED-003", 2, "1000000003", "1000000004", 1_000_00),
]


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _record_transfer(con, reference, customer_id, from_acc, to_acc, debit, credit=None):
    """Write a transfer straight to the ledger. `credit` differs from `debit` only when planting a defect."""
    credit = debit if credit is None else credit
    tid = con.execute(
        "INSERT INTO transactions (reference, type, customer_id, from_account, to_account, amount_cents, currency, created_at)"
        " VALUES (?, 'TRANSFER', ?, ?, ?, ?, 'ETB', ?)",
        (reference, customer_id, from_acc, to_acc, debit, HISTORY_DATE)).lastrowid
    con.executemany("INSERT INTO ledger_entries (transaction_id, account_id, amount_cents) VALUES (?, ?, ?)",
                    [(tid, from_acc, -debit), (tid, to_acc, credit)])
    con.execute("UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?", (debit, from_acc))
    con.execute("UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?", (credit, to_acc))


def build(path, dirty=False):
    con = connect(path)
    con.executescript("""
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS ledger_entries; DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS beneficiaries; DROP TABLE IF EXISTS switch_settlement;
        DROP TABLE IF EXISTS accounts; DROP TABLE IF EXISTS customers;
        PRAGMA foreign_keys = ON;
    """)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO customers VALUES (?, ?)", CUSTOMERS)
    for acc_id, customer_id, currency, opening, status in ACCOUNTS:
        con.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", (acc_id, customer_id, currency, opening, status))
        tid = con.execute(
            "INSERT INTO transactions (reference, type, customer_id, to_account, amount_cents, currency, created_at)"
            " VALUES (?, 'OPENING', ?, ?, ?, ?, ?)",
            (f"OPN-{acc_id}", customer_id, acc_id, opening, currency, HISTORY_DATE)).lastrowid
        con.execute("INSERT INTO ledger_entries (transaction_id, account_id, amount_cents) VALUES (?, ?, ?)",
                    (tid, acc_id, opening))
    con.executemany("INSERT INTO beneficiaries (id, customer_id, account_id, nickname, status) VALUES (?, ?, ?, ?, ?)",
                    BENEFICIARIES)
    for reference, customer_id, from_acc, to_acc, amount in HISTORY:
        _record_transfer(con, reference, customer_id, from_acc, to_acc, amount)
        con.execute("INSERT INTO switch_settlement (reference, amount_cents, settlement_date) VALUES (?, ?, '2024-01-15')",
                    (reference, amount))

    if dirty:
        # Debit and credit legs don't match: 500.00 left one account, only 400.00 arrived.
        _record_transfer(con, "TRF-BAD-001", 2, "1000000003", "1000000004", 500_00, credit=400_00)
        # Someone edited a balance directly, bypassing the ledger.
        con.execute("UPDATE accounts SET balance_cents = balance_cents + 1000_00 WHERE id = '1000000002'")
        # A transfer that pushed an account below zero.
        _record_transfer(con, "TRF-BAD-002", 3, "1000000004", "1000000001", 10_000_00)
        # The switch reports a payment the core banking system has no record of.
        con.execute("INSERT INTO switch_settlement (reference, amount_cents, settlement_date)"
                    " VALUES ('TRF-GHOST-001', 3000_00, '2024-01-15')")
        # The switch settled a different amount than the core posted.
        con.execute("UPDATE switch_settlement SET amount_cents = 7500_00 WHERE reference = 'TRF-SEED-002'")
        # The same settlement record arrived twice.
        con.execute("INSERT INTO switch_settlement (reference, amount_cents, settlement_date)"
                    " VALUES ('TRF-SEED-003', 1000_00, '2024-01-15')")
    con.commit()
    con.close()


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "data/bank.db")
