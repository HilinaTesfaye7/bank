"""Playwright UI tests for the internet banking transfer journey."""
import re

import pytest
import requests
from playwright.sync_api import Page, expect

from app.core import MOCK_OTP

pytestmark = [pytest.mark.ui, pytest.mark.usefixtures("reset")]


def open_transfer_page(page: Page, base_url):
    page.goto(f"{base_url}/banking")
    expect(page.locator("#bal-1000000001")).to_have_text("147,500.00")


def fill_details(page: Page, amount, to="ben:1"):
    page.select_option("#to", to)
    page.fill("#amount", amount)
    page.click("#continue")


def history_count(base_url, headers):
    return len(requests.get(f"{base_url}/api/accounts/1000000001/transactions", headers=headers()).json())


def test_happy_path_transfer_updates_balance(page: Page, base_url):
    open_transfer_page(page, base_url)
    fill_details(page, "1500")
    expect(page.locator("#review-amount")).to_have_text("1,500.00 ETB")
    page.fill("#otp", MOCK_OTP)
    page.click("#confirm")
    expect(page.locator("#result-title")).to_have_text("Transfer successful")
    expect(page.locator("#result-ref")).to_have_text(re.compile(r"^TRF[0-9A-F]{10}$"))
    expect(page.locator("#bal-1000000001")).to_have_text("146,000.00")
    expect(page.locator("#history tbody tr").first).to_contain_text("-1,500.00")


def test_confirm_stays_disabled_until_a_six_digit_code_is_entered(page: Page, base_url):
    open_transfer_page(page, base_url)
    fill_details(page, "100")
    confirm = page.locator("#confirm")
    expect(confirm).to_be_disabled()
    page.fill("#otp", "123")
    expect(confirm).to_be_disabled()
    page.fill("#otp", MOCK_OTP)
    expect(confirm).to_be_enabled()


@pytest.mark.parametrize("amount", ["abc", "-50", "0", "10.555"])
def test_invalid_amount_is_stopped_on_the_form(page: Page, base_url, amount):
    open_transfer_page(page, base_url)
    fill_details(page, amount)
    expect(page.locator("#form-error")).to_contain_text("valid amount")
    expect(page.locator("#step-review")).to_be_hidden()


def test_pending_beneficiary_cannot_be_selected(page: Page, base_url):
    open_transfer_page(page, base_url)
    expect(page.locator("#to option", has_text="pending verification")).to_be_disabled()


def test_server_limit_error_is_shown_to_the_customer(page: Page, base_url):
    open_transfer_page(page, base_url)
    fill_details(page, "60000")
    page.fill("#otp", MOCK_OTP)
    page.click("#confirm")
    expect(page.locator("#review-error")).to_contain_text("single transfer limit")
    expect(page.locator("#bal-1000000001")).to_have_text("147,500.00")


def test_force_enabling_confirm_without_otp_does_not_move_money(page: Page, base_url, balance):
    """A customer who enables the button in DevTools must still be stopped by the server."""
    open_transfer_page(page, base_url)
    fill_details(page, "1000")
    page.evaluate("document.querySelector('#confirm').disabled = false")
    page.click("#confirm")
    expect(page.locator("#review-error")).to_have_text("Invalid OTP")
    assert balance("1000000001") == 14750000


def test_double_click_on_confirm_pays_only_once(page: Page, base_url, headers, balance):
    open_transfer_page(page, base_url)
    before = history_count(base_url, headers)
    fill_details(page, "500")
    page.fill("#otp", MOCK_OTP)
    # Click, re-enable the button the way a slow or buggy page might, click again.
    page.evaluate("""() => {
        const b = document.querySelector('#confirm');
        b.click(); b.disabled = false; b.click();
    }""")
    expect(page.locator("#result-title")).to_have_text("Transfer successful")
    assert history_count(base_url, headers) == before + 1
    assert balance("1000000001") == 14750000 - 50000


def test_beneficiary_nickname_is_shown_as_text_not_html(page: Page, base_url, headers):
    """Stored XSS check: a nickname containing HTML must not run as code."""
    payload = '<img src=x onerror="window.hacked=1">'
    r = requests.post(f"{base_url}/api/beneficiaries", headers=headers("abebe"),
                      json={"account_id": "1000000006", "nickname": payload})
    assert r.status_code == 201
    open_transfer_page(page, base_url)
    expect(page.locator("#to option", has_text="pending verification").last).to_contain_text(payload)
    assert page.evaluate("window.hacked") is None
