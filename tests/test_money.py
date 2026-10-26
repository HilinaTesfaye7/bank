"""Unit tests for money handling. Money is kept in integer cents so it never suffers float rounding."""
import pytest

from app.core import BankError, format_cents, mask, parse_amount


@pytest.mark.parametrize("value, cents", [
    ("1500", 150000),
    ("1500.5", 150050),
    ("0.10", 10),
    ("50000.00", 5000000),
    (100.5, 10050),       # JSON number
    (7, 700),
])
def test_parse_valid_amounts(value, cents):
    assert parse_amount(value) == cents


@pytest.mark.parametrize("value", [
    None, "", " ", "abc", "-5", "0", "0.00", "10.555", "1e3", "1,500", True, [100],
])
def test_parse_rejects_invalid_amounts(value):
    with pytest.raises(BankError) as e:
        parse_amount(value)
    assert e.value.status == 400


def test_float_rounding_does_not_leak_into_balances():
    # 0.1 + 0.2 != 0.3 in floating point; in cents it is exact.
    assert 0.1 + 0.2 != 0.3
    assert parse_amount("0.10") + parse_amount("0.20") == parse_amount("0.30")


@pytest.mark.parametrize("cents, text", [
    (0, "0.00"), (5, "0.05"), (150000, "1,500.00"), (14750000, "147,500.00"), (-285000, "-2,850.00"),
])
def test_format_cents(cents, text):
    assert format_cents(cents) == text


def test_mask_shows_only_last_four_digits():
    assert mask("1000000001") == "******0001"
