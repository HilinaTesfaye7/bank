"""Banking rules: amount parsing, transfers and beneficiaries.

Every rule is enforced here, on the server. The web page also validates input,
but only for convenience: anything the page checks can be bypassed.
"""
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

MOCK_OTP = "123456"  # stands in for the one-time code a real bank sends by SMS
SINGLE_LIMIT_CENTS = 50_000_00
DAILY_LIMIT_CENTS = 100_000_00
MAX_OTP_ATTEMPTS = 3
AMOUNT = re.compile(r"^\d+(\.\d{1,2})?$")


class BankError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- money helpers ----------

def parse_amount(value):
    """Amount in cents. Rejects text, signs, scientific notation, zero and more than 2 decimals."""
    if value is None or value == "":
        raise BankError(400, "amount is required")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BankError(400, "amount must be a number")
    text = str(value).strip()
    if not AMOUNT.match(text):
        raise BankError(400, "amount must be a positive number with at most 2 decimal places")
    cents = int(Decimal(text) * 100)
    if cents <= 0:
        raise BankError(400, "amount must be greater than zero")
    return cents


def format_cents(cents):
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def mask(account_id):
    """Show only the last 4 digits of an account number."""
    return "*" * (len(account_id) - 4) + account_id[-4:]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- views ----------

def account_view(row):
    return {"id": row["id"], "currency": row["currency"], "status": row["status"],
            "balance": format_cents(row["balance_cents"]), "balance_cents": row["balance_cents"]}


def transaction_view(row):
    return {"reference": row["reference"], "from_account": row["from_account"],
            "to_account": mask(row["to_account"]), "amount": format_cents(row["amount_cents"]),
            "amount_cents": row["amount_cents"], "currency": row["currency"],
            "narrative": row["narrative"], "status": "COMPLETED", "created_at": row["created_at"]}


def beneficiary_view(row):
    return {"id": row["id"], "account_id": mask(row["account_id"]),
            "nickname": row["nickname"], "status": row["status"]}


# ---------- queries ----------

def _account(con, account_id):
    return con.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def list_accounts(con, customer_id):
    rows = con.execute("SELECT * FROM accounts WHERE customer_id = ? ORDER BY id", (customer_id,)).fetchall()
    return [account_view(r) for r in rows]


def account_history(con, customer_id, account_id):
    acc = _account(con, account_id)
    # Same answer for "doesn't exist" and "not yours", so account numbers can't be probed.
    if acc is None or acc["customer_id"] != customer_id:
        raise BankError(404, "account not found")
    rows = con.execute("""
        SELECT * FROM transactions
        WHERE type = 'TRANSFER' AND (from_account = ? OR to_account = ?)
        ORDER BY id DESC LIMIT 20
    """, (account_id, account_id)).fetchall()
    history = []
    for r in rows:
        debit = r["from_account"] == account_id
        history.append({"reference": r["reference"], "date": r["created_at"][:10],
                        "direction": "DEBIT" if debit else "CREDIT",
                        "counterparty": mask(r["to_account"] if debit else r["from_account"]),
                        "amount": format_cents(r["amount_cents"]), "amount_cents": r["amount_cents"],
                        "narrative": r["narrative"]})
    return history


# ---------- transfers ----------

def make_transfer(con, customer_id, body, idempotency_key=None):
    """Validate and post a transfer. Returns (transaction, created)."""
    body = body if isinstance(body, dict) else {}
    if body.get("from_account") in (None, ""):
        raise BankError(400, "from_account is required")
    amount = parse_amount(body.get("amount"))
    from_id = str(body["from_account"])

    if body.get("beneficiary_id") not in (None, ""):
        ben = con.execute("SELECT * FROM beneficiaries WHERE id = ? AND customer_id = ?",
                          (body["beneficiary_id"], customer_id)).fetchone()
        if ben is None:
            raise BankError(404, "beneficiary not found")
        to_id = ben["account_id"]
    elif body.get("to_account") not in (None, ""):
        to_id = str(body["to_account"])
    else:
        raise BankError(400, "to_account or beneficiary_id is required")

    if idempotency_key:
        prev = con.execute("SELECT * FROM transactions WHERE customer_id = ? AND idempotency_key = ?",
                           (customer_id, idempotency_key)).fetchone()
        if prev:
            if (prev["from_account"], prev["to_account"], prev["amount_cents"]) != (from_id, to_id, amount):
                raise BankError(409, "Idempotency-Key was already used for a different transfer")
            return transaction_view(prev), False

    src = _account(con, from_id)
    if src is None or src["customer_id"] != customer_id:
        raise BankError(403, "from_account does not belong to you")
    dst = _account(con, to_id)
    if dst is None:
        raise BankError(404, "to_account not found")
    if from_id == to_id:
        raise BankError(422, "cannot transfer to the same account")
    if dst["customer_id"] != customer_id:
        ben = con.execute("SELECT status FROM beneficiaries WHERE customer_id = ? AND account_id = ?",
                          (customer_id, to_id)).fetchone()
        if ben is None or ben["status"] != "ACTIVE":
            raise BankError(422, "to_account is not a verified beneficiary")
    if "FROZEN" in (src["status"], dst["status"]):
        raise BankError(422, "account is frozen")
    if src["currency"] != dst["currency"]:
        raise BankError(422, "currency mismatch between accounts")
    if amount > SINGLE_LIMIT_CENTS:
        raise BankError(422, f"amount exceeds the single transfer limit of {format_cents(SINGLE_LIMIT_CENTS)}")
    sent_today = con.execute("""
        SELECT COALESCE(SUM(amount_cents), 0) FROM transactions
        WHERE type = 'TRANSFER' AND customer_id = ? AND substr(created_at, 1, 10) = ?
    """, (customer_id, now_iso()[:10])).fetchone()[0]
    if sent_today + amount > DAILY_LIMIT_CENTS:
        raise BankError(422, f"amount exceeds the daily transfer limit of {format_cents(DAILY_LIMIT_CENTS)}")
    if body.get("otp") != MOCK_OTP:
        raise BankError(403, "invalid OTP")

    narrative = str(body.get("narrative") or "")[:140]
    return _post(con, customer_id, src, dst, amount, narrative, idempotency_key), True


def _post(con, customer_id, src, dst, amount, narrative, idempotency_key):
    """Debit, credit and both ledger entries in one database transaction: all or nothing."""
    with con:
        # The balance check and the debit are one statement, so two requests can't both spend the same money.
        debited = con.execute("UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ? AND balance_cents >= ?",
                              (amount, src["id"], amount)).rowcount
        if not debited:
            raise BankError(422, "insufficient funds")
        con.execute("UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?", (amount, dst["id"]))
        tid = con.execute(
            "INSERT INTO transactions (reference, type, customer_id, from_account, to_account, amount_cents,"
            " currency, narrative, idempotency_key, created_at) VALUES (?, 'TRANSFER', ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TRF" + uuid.uuid4().hex[:10].upper(), customer_id, src["id"], dst["id"], amount,
             src["currency"], narrative, idempotency_key, now_iso())).lastrowid
        con.executemany("INSERT INTO ledger_entries (transaction_id, account_id, amount_cents) VALUES (?, ?, ?)",
                        [(tid, src["id"], -amount), (tid, dst["id"], amount)])
    return transaction_view(con.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone())


# ---------- beneficiaries ----------

def list_beneficiaries(con, customer_id):
    rows = con.execute("SELECT * FROM beneficiaries WHERE customer_id = ? ORDER BY id", (customer_id,)).fetchall()
    return [beneficiary_view(r) for r in rows]


def add_beneficiary(con, customer_id, body):
    body = body if isinstance(body, dict) else {}
    account_id = str(body.get("account_id") or "").strip()
    nickname = str(body.get("nickname") or "").strip()[:60]
    if not account_id:
        raise BankError(400, "account_id is required")
    if not nickname:
        raise BankError(400, "nickname is required")
    acc = _account(con, account_id)
    if acc is None:
        raise BankError(404, "account not found")
    if acc["customer_id"] == customer_id:
        raise BankError(422, "cannot add your own account as a beneficiary")
    if con.execute("SELECT 1 FROM beneficiaries WHERE customer_id = ? AND account_id = ?",
                   (customer_id, account_id)).fetchone():
        raise BankError(409, "beneficiary already exists")
    with con:
        bid = con.execute("INSERT INTO beneficiaries (customer_id, account_id, nickname, status) VALUES (?, ?, ?, 'PENDING')",
                          (customer_id, account_id, nickname)).lastrowid
    return beneficiary_view(con.execute("SELECT * FROM beneficiaries WHERE id = ?", (bid,)).fetchone())


def verify_beneficiary(con, customer_id, beneficiary_id, otp):
    row = con.execute("SELECT * FROM beneficiaries WHERE id = ? AND customer_id = ?",
                      (beneficiary_id, customer_id)).fetchone()
    if row is None:
        raise BankError(404, "beneficiary not found")
    if row["status"] == "LOCKED":
        raise BankError(423, "beneficiary is locked after too many failed OTP attempts")
    if row["status"] == "ACTIVE":
        return beneficiary_view(row)
    if otp != MOCK_OTP:
        attempts = row["failed_attempts"] + 1
        status = "LOCKED" if attempts >= MAX_OTP_ATTEMPTS else "PENDING"
        with con:
            con.execute("UPDATE beneficiaries SET failed_attempts = ?, status = ? WHERE id = ?",
                        (attempts, status, beneficiary_id))
        if status == "LOCKED":
            raise BankError(423, "beneficiary is locked after too many failed OTP attempts")
        raise BankError(400, f"invalid OTP, {MAX_OTP_ATTEMPTS - attempts} attempt(s) left")
    with con:
        con.execute("UPDATE beneficiaries SET status = 'ACTIVE', failed_attempts = 0 WHERE id = ?", (beneficiary_id,))
    return beneficiary_view(con.execute("SELECT * FROM beneficiaries WHERE id = ?", (beneficiary_id,)).fetchone())
