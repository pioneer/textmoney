import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.ledger import BANK_FEES, EPOCH, LedgerError, Posting, Transaction
from app.domain.storage import ConflictError
from app.infrastructure.beancount_journal import JournalCodec, transaction_text, transaction_time
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
        assert "textmoney-kind:" not in journal.source
        assert "textmoney-active:" not in journal.source

        await journal.edit_account("Assets:Cash", "Wallet", "100", True, journal.source)
        assert "textmoney-active:" not in journal.source

    asyncio.run(scenario())


def test_unmatched_transaction_stays_external_and_legacy_kind_is_uninterpreted(
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
            f'  time: "{transaction_time(expense.timestamp)}"',
            f'  time: "{transaction_time(expense.timestamp)}"\n'
            '  textmoney-kind: "external"\n  id: 123\n'
            "  timestamp: FALSE\n  textmoney-fee-mode: FALSE\n"
            "  textmoney-expense-id: 456",
        )
        assert codec.parse(legacy_source).transactions[-1].kind == "expense"

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
        assert changed_date.timestamp == ENTRY_DATE.isoformat() + original.timestamp[10:]
        assert journal.ledger.equity_opened == BALANCE_DATE

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("stamp", "clock"),
    [
        ("2026-01-03T23:45:00+14:00", "23:45"),
        ("2026-01-03T00:15:12-10:00", "00:15:12"),
        ("2026-01-03T12:34:56", "12:34:56"),
    ],
)
def test_native_name_time_and_source_positions_without_serialized_ids(
    tmp_path: Path, stamp: str, clock: str
) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", opened=ACCOUNT_OPENED)
        await journal.create_category("Food", "Groceries", opened=ACCOUNT_OPENED)
        prefix = journal.source
        await journal.add_expense(
            "Assets:Cash", "Expenses:Food", "3", "", ENTRY_DATE.isoformat(), timestamp=stamp
        )
        assert journal.source.startswith(prefix)
        assert 'name: "Wallet"' in journal.source
        assert 'name: "Groceries"' in journal.source
        assert f'time: "{clock}"' in journal.source
        assert "\n  id:" not in journal.source
        assert "\n  timestamp:" not in journal.source
        assert "\n  display-name:" not in journal.source
        transaction = journal.ledger.transactions[-1]
        assert (
            transaction.timestamp == datetime.fromisoformat(stamp).replace(tzinfo=None).isoformat()
        )
        assert journal.expense_for_edit(transaction.id) == transaction

    asyncio.run(scenario())


def test_handwritten_user_metadata_and_arbitrary_links_survive_linked_reimbursements(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source = (
            '2026-01-01 open Assets:Cash UAH\n  name: "Wallet"\n'
            '2026-01-01 open Expenses:Food\n  name: "Food"\n'
            '\n; keep this exactly\n2026-01-03 * "Shop" "Receipt" #food ^receipt\n'
            '  id: "owned-by-user"\n  opaque: "unchanged"\n'
            "  Expenses:Food 10 UAH ; item\n  Assets:Cash -10 UAH\n"
        )
        path = tmp_path / FILENAME
        path.write_text(source, newline="")
        journal = fixed_journal(tmp_path)
        await journal.load()
        expense = journal.ledger.transactions[0]
        assert expense.timestamp == "2026-01-03T00:00:00"
        await journal.add_incoming(
            "Assets:Cash",
            "Expenses:Food",
            "2",
            "",
            "2026-01-04",
            "reimbursement",
            related_expense=expense.id,
            expected_source=source,
        )
        # Reuse an unambiguous native link rather than inventing a second one.
        assert journal.source.startswith(source)
        refund = journal.ledger.transactions[-1]
        assert refund.related_expense == expense.id
        assert refund.links == ("receipt",)
        assert "textmoney-expense-id:" not in journal.source
        assert journal.source.count('id: "owned-by-user"') == 1
        expense = journal.ledger.transactions[0]
        await journal.edit_expense(
            expense.id,
            "Assets:Cash",
            "Expenses:Food",
            "11",
            "Shop",
            expense.date.isoformat(),
            expense.timestamp,
            "Edited",
            journal.source,
        )
        assert "#food ^receipt" in journal.source
        assert 'opaque: "unchanged"' in journal.source
        assert "; item" in journal.source
        assert journal.ledger.transactions[-1].related_expense == journal.ledger.transactions[0].id

    asyncio.run(scenario())


def test_link_is_generated_only_for_a_linked_refund_and_reused_losslessly(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", opened=ACCOUNT_OPENED)
        await journal.create_category("Food", "Food", opened=ACCOUNT_OPENED)
        await journal.add_expense("Assets:Cash", "Expenses:Food", "10", "", ENTRY_DATE.isoformat())
        assert "^textmoney-" not in journal.source
        expense = journal.ledger.transactions[-1]
        prefix = journal.source.split("2026-01-03 *")[0]
        for _ in range(2):
            await journal.add_incoming(
                "Assets:Cash",
                "Expenses:Food",
                "2",
                "",
                "2026-01-04",
                "reimbursement",
                related_expense=expense.id,
                expected_source=journal.source,
            )
        assert journal.source.startswith(prefix)
        assert journal.source.count("^textmoney-") == 3
        transactions = journal.ledger.transactions
        assert transactions[0].links == transactions[1].links == transactions[2].links
        assert all(t.related_expense == transactions[0].id for t in transactions[1:])
        assert "\n  id:" not in journal.source

    asyncio.run(scenario())


def test_fee_preference_is_local_and_survives_reload_without_touching_source(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_category("Fees", "Fees", opened=ACCOUNT_OPENED)
        source = journal.source
        path = tmp_path / FILENAME
        backups = tuple((tmp_path / "backups").glob("*"))
        assert await journal.set_default_fee_category("Expenses:Fees", source) is None
        assert path.read_text() == source
        assert tuple((tmp_path / "backups").glob("*")) == backups
        await journal.load()
        assert (
            journal.default_fee_category == journal.ledger.default_fee_category == "Expenses:Fees"
        )
        with pytest.raises(ConflictError, match="crud_stale"):
            await journal.set_default_fee_category(BANK_FEES, source + "\n")
        with pytest.raises(LedgerError):
            await journal.set_default_fee_category("Income:Missing", source)
        assert journal.source == source

    asyncio.run(scenario())


def test_external_comparison_reloads_and_preserves_stale_draft_guards(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", opened=ACCOUNT_OPENED)
        await journal.create_category("Food", "Food", opened=ACCOUNT_OPENED)
        await journal.add_expense("Assets:Cash", "Expenses:Food", "10", "", ENTRY_DATE.isoformat())
        before = journal.source
        identifier = journal.ledger.transactions[-1].id
        current = "; hand edit\n" + before.replace("Food 10 UAH", "Food 12 UAH").replace(
            "Cash -10 UAH", "Cash -12 UAH"
        )
        (tmp_path / FILENAME).write_text(current, newline="")
        assert await journal.poll()
        assert journal.external_before == before and journal.external_current == current
        assert any(
            line.startswith("-") and "Expenses:Food 10 UAH" in line
            for line in journal.external_changes.splitlines()
        )
        assert any(
            line.startswith("+") and "Expenses:Food 12 UAH" in line
            for line in journal.external_changes.splitlines()
        )
        assert journal.external_summary["transactions_changed"] == 1
        assert journal.external_summary["transactions_added"] == 0
        assert journal.external_observed_at is not None
        assert journal.source == current and journal.ready
        with pytest.raises(ConflictError, match="crud_stale"):
            await journal.delete_expense(identifier, before)
        assert (tmp_path / FILENAME).read_text() == current
        invalid = current + '\ninclude "unsupported-private-name.beancount"\n'
        (tmp_path / FILENAME).write_text(invalid, newline="")
        with pytest.raises(LedgerError, match=r"Line \d+ \(Include directive\)"):
            await journal.poll()
        assert journal.external_before == current and journal.external_current == invalid
        assert journal.external_summary == {}
        assert journal.external_changes and not journal.ready
        assert journal.source == current

    asyncio.run(scenario())


def test_parser_errors_have_location_without_raw_source_in_exception_chain() -> None:
    source = '2026-01-03 quantum "Private financial payload"\n'
    with pytest.raises(LedgerError, match="Line 1, column") as error:
        JournalCodec(today=lambda: TODAY).parse(source)
    assert "Private financial payload" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_ambiguous_user_links_get_a_unique_refund_link_without_losing_annotations(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source = (
            "2026-01-01 open Assets:Cash UAH\n2026-01-01 open Expenses:Food\n"
            '\n2026-01-03 * "First" #food ^shared ; receipt comment\n'
            '  id: "user-owned"\n  receipt: "keep"\n'
            "  Expenses:Food 10 UAH ; food note\n  Assets:Cash -10 UAH\n"
            '\n2026-01-03 * "Second" ^shared\n'
            "  Expenses:Food 4 UAH\n  Assets:Cash -4 UAH\n"
        )
        path = tmp_path / FILENAME
        path.write_text(source, newline="")
        journal = fixed_journal(tmp_path)
        await journal.load()
        first = journal.ledger.transactions[0]
        await journal.add_incoming(
            "Assets:Cash",
            "Expenses:Food",
            "1",
            "",
            "2026-01-04",
            "reimbursement",
            related_expense=first.id,
            expected_source=source,
        )
        first, second, refund = journal.ledger.transactions
        assert refund.related_expense == first.id
        assert refund.links[0].startswith("textmoney-")
        assert set(first.links) == {"shared", refund.links[0]}
        assert second.links == ("shared",)
        assert "#food" in journal.source and "; receipt comment" in journal.source
        assert 'id: "user-owned"\n  receipt: "keep"' in journal.source
        assert "Expenses:Food 10 UAH ; food note" in journal.source
        assert source.split('\n2026-01-03 * "Second"')[1] in journal.source

    asyncio.run(scenario())


def test_external_creation_after_missing_initial_load_gets_a_comparison(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = fixed_journal(tmp_path)
        await journal.load()
        observed = journal.loaded_at
        current = '2026-01-01 open Assets:Cash UAH\n  name: "External"\n'
        (tmp_path / FILENAME).write_text(current, newline="")
        assert await journal.poll()
        assert journal.external_before == "" and journal.external_current == current
        assert journal.external_previous_observed_at == observed
        assert journal.external_summary["accounts_added"] == 1

    asyncio.run(scenario())


def test_invalid_price_expression_reports_its_transaction_line() -> None:
    source = (
        "2026-01-01 open Assets:Cash USD\n2026-01-01 open Assets:Bank UAH\n"
        '\n2026-01-03 * "Exchange"\n'
        "  Assets:Cash -1 USD @@ (1 / 0) UAH\n  Assets:Bank 42 UAH\n"
    )
    with pytest.raises(LedgerError, match=r"Line 4 \(Transaction directive\).*monetary"):
        JournalCodec(today=lambda: TODAY).parse(source)


def test_legacy_metadata_is_preserved_but_not_interpreted_or_migrated(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = (
            '2026-01-01 open Assets:Cash UAH\n  display-name: "Old wallet"\n'
            "2026-01-01 open Expenses:Food\n  textmoney-default-transfer-fee: TRUE\n"
            '\n2026-01-03 * "Legacy"\n  id: "user-owned"\n'
            '  timestamp: "2026-01-03T21:45:00+09:00"\n'
            '  textmoney-kind: "income"\n  textmoney-expense-id: "unresolved"\n'
            '  textmoney-fee-mode: "deducted"\n'
            "  Expenses:Food 10 UAH\n  Assets:Cash -10 UAH\n"
        )
        path = tmp_path / FILENAME
        path.write_text(source, newline="")
        journal = fixed_journal(tmp_path)
        await journal.load()
        transaction = journal.ledger.transactions[0]
        assert transaction.kind == "expense"
        assert transaction.timestamp == "2026-01-03T00:00:00"
        assert transaction.related_expense is None and transaction.fee_mode == "added"
        assert journal.ledger.accounts[0].name == "Assets:Cash"
        assert journal.default_fee_category == BANK_FEES
        assert journal.source == source and path.read_text() == source
        await journal.edit_expense(
            transaction.id,
            "Assets:Cash",
            "Expenses:Food",
            "10",
            "",
            transaction.date.isoformat(),
            "2026-01-03T12:34:00+05:00",
            "Edited",
            source,
        )
        assert 'time: "12:34"' in journal.source
        assert 'timestamp: "2026-01-03T21:45:00+09:00"' in journal.source
        assert 'id: "user-owned"' in journal.source
        assert journal.ledger.transactions[0].timestamp == "2026-01-03T12:34:00"
        await journal.edit_account("Assets:Cash", "New wallet", None, True, journal.source)
        assert 'name: "New wallet"' in journal.source
        assert 'display-name: "Old wallet"' in journal.source

    asyncio.run(scenario())
