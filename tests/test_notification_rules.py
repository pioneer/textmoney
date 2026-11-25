from datetime import date
from decimal import Decimal

import pytest

from app.domain.ledger import Account, Category, Ledger
from app.domain.notification_rules import (
    MAX_NOTIFICATION_LENGTH,
    NotificationRuleError,
    Pattern,
    Rule,
    TransactionMapping,
    Value,
    first_match,
    parse_amount,
    preview_transaction,
)


def test_ordered_rules_ignore_declines_before_suggesting_purchases() -> None:
    ignored = Rule(
        "Declined", "bank.app", "ignore", body=Pattern.parse("Payment {amount} declined")
    )
    purchase = Rule(
        "Purchase",
        "bank.app",
        "suggest",
        body=Pattern.parse("Paid {amount} {currency} at {payee}, card *1234"),
    )
    rules = (ignored, purchase)
    decline = first_match(rules, "bank.app", None, "Payment 12.34 EUR declined")
    assert decline is not None and decline.rule == ignored
    assert decline.captures == {"amount": "12.34 EUR"}
    match = first_match(rules, "bank.app", None, "Paid 12.34 EUR at Example Shop, card *1234")
    assert match is not None and match.rule == purchase
    assert match.captures == {"amount": "12.34", "currency": "EUR", "payee": "Example Shop"}
    assert first_match(rules, "other.app", None, "Payment 12.34 EUR declined") is None


def test_title_and_body_conditions_require_distinct_captures() -> None:
    with pytest.raises(NotificationRuleError, match="distinct"):
        Rule("Both", "bank", "suggest", Pattern.parse("{value}"), Pattern.parse("{value}"))
    rule = Rule("Both", "bank", "suggest", Pattern.parse("Card {card}"), Pattern.parse("{amount}"))
    assert first_match((rule,), "bank", "Card 1", None) is None
    match = first_match((rule,), "bank", "Card 1", "25")
    assert match is not None and match.captures == {"card": "1", "amount": "25"}


def test_disabled_and_overlapping_rules_use_first_enabled_match() -> None:
    pattern = Pattern.parse("{value}")
    disabled = Rule("Disabled", "bank", "ignore", body=pattern, enabled=False)
    first = Rule("First", "bank", "suggest", body=pattern)
    second = Rule("Second", "bank", "ignore", body=pattern)
    result = first_match((disabled, first, second), "bank", None, "message")
    assert result is not None and result.rule == first


def test_pattern_literal_braces_anchoring_and_size_limit() -> None:
    pattern = Pattern.parse("{{bank}} {amount} EUR")
    assert pattern.match("{bank} 5 EUR") == {"amount": "5"}
    assert pattern.match("Prefix {bank} 5 EUR") is None
    assert pattern.match("{bank} 5 EUR extra") is None
    assert pattern.match("{bank}  EUR") is None
    with pytest.raises(NotificationRuleError, match="size"):
        pattern.match("x" * (MAX_NOTIFICATION_LENGTH + 1))


@pytest.mark.parametrize(
    "source",
    ["", "{bad name}", "{unclosed", "extra}", "{a}{b}", "{a} and {a}", "{0}", "x" * 513],
)
def test_invalid_patterns_are_rejected(source: str) -> None:
    with pytest.raises(NotificationRuleError):
        Pattern.parse(source)


@pytest.mark.parametrize(
    ("text", "decimal", "group", "expected"),
    [
        ("1,234.56", ".", ",", Decimal("1234.56")),
        ("-1.234,56", ",", ".", Decimal("-1234.56")),
        ("+1 234,50", ",", " ", Decimal("1234.50")),
        ("12", ".", None, Decimal("12")),
        ("0,01", ",", None, Decimal("0.01")),
    ],
)
def test_explicit_amount_formats(
    text: str, decimal: str, group: str | None, expected: Decimal
) -> None:
    assert parse_amount(text, decimal_separator=decimal, grouping_separator=group) == expected


@pytest.mark.parametrize(
    ("text", "decimal", "group"),
    [
        ("1,23.45", ".", ","),
        ("1,234", ".", None),
        ("1.234", ".", ","),
        ("1.2.3", ".", ","),
        ("1.2", ".", "."),
        ("1e3", ".", None),
        ("9999999999999", ".", None),
        ("1.", ".", None),
    ],
)
def test_ambiguous_or_invalid_amounts_are_not_guessed(
    text: str, decimal: str, group: str | None
) -> None:
    with pytest.raises(NotificationRuleError):
        parse_amount(text, decimal_separator=decimal, grouping_separator=group)


def test_preview_maps_sample_without_writing_or_guessing() -> None:
    opened = date(2020, 1, 1)
    ledger = Ledger(
        accounts=(Account("Assets:Bank", "Bank", "EUR", opened),),
        categories=(Category("Expenses:Food", "Food", opened),),
    )
    rule = Rule(
        "Card",
        "bank.app",
        "suggest",
        body=Pattern.parse("Paid {amount} {currency} at {payee} on {date}"),
        mapping=TransactionMapping(
            kind=Value("expense"),
            amount=Value("amount", captured=True),
            currency=Value("currency", captured=True),
            account=Value("Assets:Bank"),
            category=Value("Expenses:Food"),
            payee=Value("payee", captured=True),
            date=Value("date", captured=True),
            date_format="%d.%m.%Y",
            currency_symbols=(("€", "EUR"),),
        ),
    )
    match = first_match((rule,), "bank.app", None, "Paid 12.34 € at Example Shop on 20.11.2026")
    assert match is not None
    draft = preview_transaction(match, ledger, today=date(2026, 11, 23))
    assert draft.errors == ()
    assert (draft.kind, draft.amount, draft.currency, draft.account, draft.category) == (
        "expense",
        Decimal("12.34"),
        "EUR",
        "Assets:Bank",
        "Expenses:Food",
    )
    assert draft.payee == "Example Shop"
    assert draft.date == date(2026, 11, 20)
    assert not ledger.transactions


def test_preview_reports_missing_and_unavailable_mappings() -> None:
    rule = Rule(
        "Card",
        "bank",
        "suggest",
        body=Pattern.parse("Paid {amount} {currency}"),
        mapping=TransactionMapping(
            kind=Value("expense"),
            amount=Value("amount", captured=True),
            currency=Value("currency", captured=True),
            account=Value("Assets:Missing"),
            category=Value("Expenses:Missing"),
            payee=Value("missing_capture", captured=True),
        ),
    )
    match = first_match((rule,), "bank", None, "Paid -5 $")
    assert match is not None
    draft = preview_transaction(match, Ledger(), today=date(2026, 11, 23))
    assert draft.amount is None and draft.currency is None and draft.date is None
    assert {field for field, _ in draft.errors} == {
        "amount",
        "currency",
        "date",
        "account",
        "category",
        "payee",
    }


def test_transfer_preview_checks_accounts_and_currency_without_guessing_destination() -> None:
    opened = date(2020, 1, 1)
    ledger = Ledger(accounts=(Account("Assets:Bank", "Bank", "EUR", opened),))
    rule = Rule(
        "Transfer",
        "bank",
        "suggest",
        body=Pattern.parse("{amount}"),
        mapping=TransactionMapping(
            kind=Value("transfer"),
            amount=Value("amount", captured=True),
            currency=Value("USD"),
            account=Value("Assets:Bank"),
            destination=Value("Assets:Bank"),
            date=Value("2026-11-20"),
        ),
    )
    match = first_match((rule,), "bank", None, "10.00")
    assert match is not None
    draft = preview_transaction(match, ledger, today=date(2026, 11, 23))
    assert {field for field, _ in draft.errors} == {"account", "destination"}


def test_exchange_preview_requires_an_explicit_destination_amount() -> None:
    opened = date(2020, 1, 1)
    ledger = Ledger(
        accounts=(
            Account("Assets:Euro", "Euro", "EUR", opened),
            Account("Assets:Dollar", "Dollar", "USD", opened),
        )
    )
    rule = Rule(
        "Exchange",
        "bank",
        "suggest",
        body=Pattern.parse("{amount}"),
        mapping=TransactionMapping(
            kind=Value("transfer"),
            amount=Value("amount", captured=True),
            currency=Value("EUR"),
            account=Value("Assets:Euro"),
            destination=Value("Assets:Dollar"),
            date=Value("2026-11-20"),
        ),
    )
    match = first_match((rule,), "bank", None, "10.00")
    assert match is not None
    draft = preview_transaction(match, ledger, today=date(2026, 11, 23))
    assert draft.destination_amount is None
    assert {field for field, _ in draft.errors} == {"destination_amount"}


def test_invalid_rule_mapping_and_ignore_preview_are_rejected() -> None:
    with pytest.raises(NotificationRuleError, match="separators"):
        TransactionMapping(decimal_separator=".", grouping_separator=".")
    with pytest.raises(NotificationRuleError, match="unique"):
        TransactionMapping(currency_symbols=(("$", "USD"), ("$", "CAD")))
    rule = Rule("Ignore", "bank", "ignore", body=Pattern.parse("{body}"))
    match = first_match((rule,), "bank", None, "declined")
    assert match is not None
    with pytest.raises(NotificationRuleError, match="ignored"):
        preview_transaction(match, Ledger(), today=date(2026, 11, 23))
