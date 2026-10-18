from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.ledger import (
    EPOCH,
    EQUITY,
    Account,
    Category,
    Ledger,
    LedgerError,
    Posting,
    Transaction,
    account_path,
    currency_code,
    display_name,
    money,
)


@pytest.mark.parametrize(
    "value",
    ["", "-1", "NaN", "Infinity", "1e2", "1,20", ".5", "1.001", "1000000000000", "0.123", "+1"],
)
def test_money_rejects_unsafe_or_ambiguous_input(value: str) -> None:
    with pytest.raises(LedgerError):
        money(value)


def test_money_exact_and_identifiers_explicit() -> None:
    assert money(" 0.10 ") + money("0.20") == Decimal("0.30")
    assert account_path("Bank:Daily", "Assets") == "Assets:Bank:Daily"
    assert account_path("Expenses:Food:Groceries", "Expenses") == "Expenses:Food:Groceries"
    assert currency_code("uah") == "UAH"
    assert display_name(" Готівка ") == "Готівка"
    for invalid in ("cash", "../Cash", "Cash\n", "Cash:lower", 'Cash"'):
        with pytest.raises(LedgerError):
            account_path(invalid if invalid != "Cash\n" else "Ca\nsh", "Assets")
    with pytest.raises(LedgerError):
        currency_code("US")
    with pytest.raises(LedgerError):
        display_name("private\npayload")


def funded_ledger() -> Ledger:
    opening = Transaction(
        "opening",
        EPOCH,
        "2026-01-01T00:00:00+00:00",
        "",
        "Opening",
        (
            Posting("Assets:Cash", Decimal("10"), "UAH"),
            Posting(EQUITY, Decimal("-10"), "UAH"),
        ),
        "opening",
    )
    return Ledger(
        (Account("Assets:Cash", "Cash", "UAH"),),
        (Category("Expenses:Food", "Food"),),
        (opening,),
        EPOCH,
    )


def test_expense_balances_and_zero_allowed_but_not_negative() -> None:
    ledger = funded_ledger()
    expense = ledger.expense(
        "Assets:Cash",
        "Expenses:Food",
        Decimal("10"),
        "Shop",
        date.today(),
        datetime.now(UTC),
        "expense",
    )
    updated = Ledger(ledger.accounts, ledger.categories, (*ledger.transactions, expense), EPOCH)
    assert updated.balances() == {"Assets:Cash": Decimal("0")}
    with pytest.raises(LedgerError, match="Insufficient"):
        updated.expense(
            "Assets:Cash",
            "Expenses:Food",
            Decimal(".01"),
            "Shop",
            date.today(),
            datetime.now(UTC),
            "another",
        )
    with pytest.raises(LedgerError, match="explicitly"):
        ledger.expense(
            "Assets:Unknown",
            "Expenses:Food",
            Decimal("1"),
            "Shop",
            date.today(),
            datetime.now(UTC),
            "unknown",
        )


def test_backdated_expense_checks_full_chronological_balance() -> None:
    ledger = funded_ledger()
    later = Transaction(
        "later",
        date(2026, 1, 5),
        "2026-01-05T12:00:00+00:00",
        "",
        "",
        (
            Posting("Expenses:Food", Decimal("8"), "UAH"),
            Posting("Assets:Cash", Decimal("-8"), "UAH"),
        ),
        "expense",
    )
    ledger = Ledger(ledger.accounts, ledger.categories, (*ledger.transactions, later), EPOCH)
    with pytest.raises(LedgerError, match="Insufficient"):
        ledger.expense(
            "Assets:Cash",
            "Expenses:Food",
            Decimal("3"),
            "Shop",
            date(2026, 1, 2),
            datetime.now(UTC),
            "backdated",
        )


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "0.001"])
def test_expense_invalid_amounts_never_change_ledger(amount: str) -> None:
    ledger = funded_ledger()
    with pytest.raises(LedgerError):
        ledger.expense(
            "Assets:Cash",
            "Expenses:Food",
            Decimal(amount),
            "Shop",
            date.today(),
            datetime.now(UTC),
            "bad",
        )
    assert ledger.balances()["Assets:Cash"] == Decimal("10")


def test_same_day_order_compares_instants_not_timezone_strings() -> None:
    # 10:00+03 occurs before 08:00Z; lexical timestamp order is wrong.
    funding = Transaction(
        "fund",
        EPOCH,
        "2026-01-01T10:00:00+03:00",
        "",
        "",
        (
            Posting("Assets:Cash", Decimal("10"), "UAH"),
            Posting(EQUITY, Decimal("-10"), "UAH"),
        ),
        "opening",
    )
    spending = Transaction(
        "spend",
        EPOCH,
        "2026-01-01T08:00:00+00:00",
        "",
        "",
        (
            Posting("Assets:Cash", Decimal("-10"), "UAH"),
            Posting("Expenses:Food", Decimal("10"), "UAH"),
        ),
        "expense",
    )
    original = funded_ledger()
    ledger = Ledger(original.accounts, original.categories, (spending, funding), EPOCH)
    assert ledger.balances()["Assets:Cash"] == Decimal("0")
