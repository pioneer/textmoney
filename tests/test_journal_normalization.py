import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.ledger import EPOCH, LedgerError, Posting, Transaction
from app.infrastructure.beancount_journal import JournalCodec, transaction_text
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal

TODAY = date(2026, 3, 1)
ACCOUNT_OPENED = date(2026, 1, 1)
BALANCE_DATE = date(2026, 1, 2)
ENTRY_DATE = date(2026, 1, 3)


def fixed_journal(directory: Path) -> Journal:
    return Journal(
        DesktopFolder(str(directory), FILENAME),
        today=lambda: TODAY,
    )


def test_new_records_use_explicit_dates_and_infer_supported_transaction_kinds(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account(
            "Cash",
            "Wallet",
            "UAH",
            "100",
            opened=ACCOUNT_OPENED,
            opening_date=BALANCE_DATE,
        )
        await journal.create_category("Food", "Food", opened=ACCOUNT_OPENED)
        await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food",
            "10",
            "Synthetic",
            ENTRY_DATE.isoformat(),
        )

        assert journal.ledger.accounts[0].opened == ACCOUNT_OPENED
        assert journal.ledger.categories[0].opened == ACCOUNT_OPENED
        assert journal.ledger.equity_opened == BALANCE_DATE
        assert [transaction.kind for transaction in journal.ledger.transactions] == [
            "opening",
            "expense",
        ]
        assert "txncount-kind:" not in journal.source
        assert "txncount-active:" not in journal.source

        await journal.edit_account("Assets:Cash", "Wallet", "100", True, journal.source)
        assert "txncount-active:" not in journal.source

    asyncio.run(scenario())


def test_unmatched_new_transaction_stays_external_and_legacy_kind_takes_precedence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account(
            "Cash", "Wallet", "UAH", "100", opened=ACCOUNT_OPENED, opening_date=BALANCE_DATE
        )
        await journal.create_category("Food", "Food", opened=ACCOUNT_OPENED)
        await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food",
            "10",
            "Synthetic",
            ENTRY_DATE.isoformat(),
        )
        expense = journal.ledger.transactions[-1]
        split_entry = transaction_text(
            Transaction(
                "split-external",
                ENTRY_DATE,
                "2026-01-03T12:00:00+00:00",
                "",
                "Split category movement",
                (
                    Posting("Expenses:Food", Decimal("4"), "UAH"),
                    Posting("Expenses:Food", Decimal("6"), "UAH"),
                    Posting("Assets:Cash", Decimal("-10"), "UAH"),
                ),
                "external",
            )
        )
        codec = JournalCodec(today=lambda: TODAY)
        parsed = codec.parse(journal.source + "\n" + split_entry)
        assert parsed.transactions[-1].kind == "external"

        legacy_source = journal.source.replace(
            f'  timestamp: "{expense.timestamp}"',
            f'  timestamp: "{expense.timestamp}"\n  txncount-kind: "external"',
        )
        assert codec.parse(legacy_source).transactions[-1].kind == "external"

    asyncio.run(scenario())


def test_default_dates_are_today_and_invalid_date_order_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", "10")
        await journal.create_category("Food", "Food")
        assert journal.ledger.accounts[0].opened == TODAY
        assert journal.ledger.categories[0].opened == TODAY
        assert journal.ledger.transactions[0].date == TODAY

        source = journal.source
        with pytest.raises(LedgerError, match="crud_opening_before_account"):
            await journal.create_account(
                "EarlierBalance",
                "Earlier",
                "USD",
                "1",
                opened=ACCOUNT_OPENED,
                opening_date=EPOCH,
            )
        with pytest.raises(LedgerError, match="crud_category_before_parent"):
            await journal.create_category("Food:Child", "Child", opened=ACCOUNT_OPENED)
        assert journal.source == source

    asyncio.run(scenario())


def test_opening_date_changes_only_when_explicitly_requested(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account(
            "Cash",
            "Wallet",
            "UAH",
            "100",
            opened=ACCOUNT_OPENED,
            opening_date=BALANCE_DATE,
        )
        original = journal.ledger.transactions[0]
        await journal.edit_account("Assets:Cash", "Wallet", "120", True, journal.source)
        unchanged_date = journal.ledger.transactions[0]
        assert unchanged_date.date == BALANCE_DATE
        assert unchanged_date.timestamp == original.timestamp

        await journal.edit_account(
            "Assets:Cash",
            "Wallet",
            "130",
            True,
            journal.source,
            opening_date=ENTRY_DATE,
        )
        changed_date = journal.ledger.transactions[0]
        assert changed_date.id == original.id
        assert changed_date.date == ENTRY_DATE
        assert changed_date.timestamp == original.timestamp
        assert journal.ledger.equity_opened == BALANCE_DATE

    asyncio.run(scenario())
