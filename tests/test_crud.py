import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from beancount import loader
from beancount.core.data import Transaction as OfficialTransaction
from fava.core import FavaLedger
from form_controls import transaction_form
from journal_fixtures import decorate_transaction, review_external_fixture

from app.domain.ledger import EPOCH, LedgerError
from app.domain.storage import ConflictError
from app.infrastructure.beancount_journal import transaction_text, transaction_time
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import StorageError
from app.ui.finance import FinanceView


async def seeded(directory: Path) -> Journal:
    journal = Journal(DesktopFolder(str(directory), FILENAME))
    await journal.load()
    await journal.create_account("Cash", "Wallet", "UAH", "100", opened=EPOCH, opening_date=EPOCH)
    await journal.create_category("Food:Groceries", "Groceries", opened=EPOCH)
    await journal.add_expense(
        "Assets:Cash", "Expenses:Food:Groceries", "10", "Synthetic", date.today().isoformat()
    )
    return journal


def assert_official(source: str, currencies: tuple[str, ...] = ("UAH",)) -> None:
    _, errors, options = loader.load_string(source)
    assert not errors
    assert tuple(options["operating_currency"]) == currencies


def test_empty_payee_and_multiline_description_roundtrip_without_directive_injection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        description = 'First "line" \\ receipt\r\n2026-01-01 open Assets:Injected UAH'
        await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food:Groceries",
            "1",
            " \t ",
            "2026-01-01",
            description=description,
        )
        transaction = next(
            t for t in journal.ledger.transactions if t.narration.startswith("First")
        )
        identifier = transaction.id
        assert transaction.payee == ""
        assert transaction.narration == description.replace("\r\n", "\n")
        assert "Assets:Injected" not in {a.id for a in journal.ledger.accounts}
        assert_official(journal.source)
        entries, _, _ = loader.load_string(journal.source)
        official = next(
            e
            for e in entries
            if isinstance(e, OfficialTransaction) and e.narration == transaction.narration
        )
        assert isinstance(official, OfficialTransaction)
        assert official.narration == transaction.narration
        assert not official.payee
        await journal.edit_expense(
            identifier,
            "Assets:Cash",
            "Expenses:Food:Groceries",
            "2",
            "Shop",
            "2026-01-01",
            transaction.timestamp,
            "Edited\nsecond line",
            journal.source,
        )
        await journal.edit_expense(
            identifier,
            "Assets:Cash",
            "Expenses:Food:Groceries",
            "2",
            "",
            "2026-01-01",
            transaction.timestamp,
            "Edited\nsecond line",
            journal.source,
        )
        await journal.load()
        saved = next(t for t in journal.ledger.transactions if t.id == identifier)
        assert saved.payee == ""
        assert saved.narration == "Edited\nsecond line"
        assert_official(journal.source)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "description", ["x" * 501, "not\tallowed", "not\rallowed", "not\x00allowed"]
)
def test_invalid_description_leaves_source_and_backups_unchanged(
    tmp_path: Path,
    description: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        backups = list((tmp_path / "backups").iterdir())
        transaction = journal.ledger.transactions[-1]
        with pytest.raises(LedgerError, match="crud_description"):
            await journal.add_expense(
                "Assets:Cash",
                "Expenses:Food:Groceries",
                "1",
                "",
                "2026-01-01",
                description=description,
            )
        with pytest.raises(LedgerError, match="crud_description"):
            await journal.edit_expense(
                transaction.id,
                "Assets:Cash",
                "Expenses:Food:Groceries",
                "1",
                "",
                "2026-01-01",
                transaction.timestamp,
                description,
                before,
            )
        assert journal.source == (tmp_path / FILENAME).read_text(newline="") == before
        assert list((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


def test_new_account_appends_missing_display_currency_preserving_existing_configuration(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.set_operating_currencies("UAH EUR", journal.source)
        original = journal.source + '\n; keep this configuration\noption "title" "Synthetic"\n'
        (tmp_path / FILENAME).write_text(original, newline="")
        await journal.load()
        assert journal.source == original
        await journal.create_account("Usd", "Dollar wallet", "USD")
        assert journal.source.startswith(original)
        assert_official(journal.source, ("UAH", "EUR", "USD"))
        await journal.create_account("OtherUsd", "Other dollar wallet", "USD")
        assert journal.source.count('option "operating_currency" "USD"') == 1
        assert journal.source.startswith(original)
        assert any(p.read_text(newline="") == original for p in (tmp_path / "backups").iterdir())

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["backup", "conflict", "validation"])
def test_failed_new_account_never_saves_currency_separately(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = journal.source
        expected = original
        error: type[Exception] = LedgerError
        if failure == "backup":
            journal.storage.folder.backup = AsyncMock(side_effect=StorageError("Synthetic"))
            error = StorageError
        elif failure == "conflict":
            expected += "\n; external change\n"
            (tmp_path / FILENAME).write_text(expected, newline="")
            error = ConflictError
        with pytest.raises(error):
            await journal.create_account(
                "Usd", "Dollar wallet", "USD", "-1" if failure == "validation" else ""
            )
        assert (tmp_path / FILENAME).read_text(newline="") == expected
        assert journal.source == original
        assert "USD" not in journal.ledger.operating_currencies

    asyncio.run(scenario())


def test_expense_edit_preserves_id_metadata_tags_comments_and_unrelated_bytes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        transaction = journal.ledger.transactions[-1]
        source = decorate_transaction(
            journal,
            transaction.id,
            lambda text: text.replace(
                f'  time: "{transaction_time(transaction.timestamp)}"',
                f'  time: "{transaction_time(transaction.timestamp)}"\n'
                '  id: "user-owned-id"\n'
                '  txncount-kind: "expense"\n'
                '  receipt: "synthetic"\n'
                "  ; private annotation",
            ).replace(' "Expense"\n', ' "Expense" #food ^receipt ; inline\n'),
        )
        source += '\n; unrelated tail\n2026-01-01 custom "synthetic" "keep"\n'
        source = source.replace("\n", "\r\n")
        target = tmp_path / FILENAME
        target.write_bytes(source.encode())
        await review_external_fixture(journal)
        transaction = journal.ledger.transactions[-1]
        await journal.edit_expense(
            transaction.id,
            "Assets:Cash",
            "Expenses:Food:Groceries",
            "12.34",
            "Changed",
            "2026-01-02",
            "2026-01-02T14:15:16+02:00",
            "Description",
            journal.source,
        )
        current = journal.ledger.transactions[-1]
        assert current.id == transaction.id
        assert current.timestamp == "2026-01-02T14:15:16"
        assert 'id: "user-owned-id"' in journal.source
        assert current.narration == "Description"
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("87.66")
        assert 'receipt: "synthetic"' in journal.source
        assert "#food ^receipt ; inline" in journal.source
        assert "; private annotation" in journal.source
        assert journal.source.endswith(
            '; unrelated tail\r\n2026-01-01 custom "synthetic" "keep"\r\n'
        )
        assert (
            source.split(transaction.date.isoformat() + " *")[0]
            == (journal.source.split("2026-01-02 *")[0])
        )
        assert target.read_bytes() == journal.source.encode()
        assert any(p.read_bytes() == source.encode() for p in (tmp_path / "backups").iterdir())
        assert_official(journal.source)

    asyncio.run(scenario())


def test_expense_delete_physically_removes_entry_but_keeps_comments(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        transaction = journal.ledger.transactions[-1]
        source = journal.source.replace(
            f'{transaction.date} * "Synthetic"',
            f'; keep this comment\n{transaction.date} * "Synthetic"',
        ).replace(
            f'  time: "{transaction_time(transaction.timestamp)}"',
            f'  time: "{transaction_time(transaction.timestamp)}"\n'
            '  txncount-kind: "expense" ; keep inline',
        )
        (tmp_path / FILENAME).write_text(source, newline="")
        await review_external_fixture(journal)
        transaction = journal.ledger.transactions[-1]
        await journal.delete_expense(transaction.id, journal.source)
        assert transaction.id not in journal.source
        assert "; keep this comment" in journal.source
        assert "; keep inline" in journal.source
        assert len(journal.ledger.transactions) == 1
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("100")
        assert_official(journal.source)

    asyncio.run(scenario())


def test_operating_currencies_new_existing_configuration_and_comments(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        assert journal.ledger.operating_currencies == ("UAH",)
        await journal.set_operating_currencies("UAH USD EUR USD", journal.source)
        assert journal.ledger.operating_currencies == ("UAH", "USD", "EUR")
        assert_official(journal.source, ("UAH", "USD", "EUR"))
        source = journal.source.replace(
            'option "operating_currency" "EUR"', 'option "operating_currency" "EUR" ; keep option'
        )
        (tmp_path / FILENAME).write_text(source, newline="")
        await review_external_fixture(journal)
        await journal.set_operating_currencies("EUR", journal.source)
        assert "; keep option" in journal.source
        assert journal.ledger.operating_currencies == ("EUR",)
        assert_official(journal.source, ("EUR",))
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("90")
        # An existing file without options is never silently changed on load.
        source = journal.source.replace('option "operating_currency" "EUR"', "")
        (tmp_path / FILENAME).write_text(source, newline="")
        await review_external_fixture(journal)
        assert journal.source == source
        assert journal.ledger.operating_currencies == ()
        await journal.set_operating_currencies("UAH USD", journal.source)
        assert_official(journal.source, ("UAH", "USD"))

    asyncio.run(scenario())


def test_account_opening_changes_keep_economics_and_warn_for_negative_running_balances(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        opening = journal.ledger.transactions[0]
        source = journal.source
        await journal.edit_account("Assets:Cash", "Wallet", "5", True, source)
        assert journal.ledger.balance_warnings() == {"Assets:Cash"}
        await journal.edit_account("Assets:Cash", "Renamed", "120", False, journal.source)
        assert journal.ledger.transactions[0].kind == opening.kind
        assert journal.ledger.transactions[0].timestamp == opening.timestamp
        assert journal.ledger.accounts[0].name == "Renamed"
        assert not journal.ledger.accounts[0].active
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("110")
        with pytest.raises(LedgerError, match="crud_inactive"):
            await journal.add_expense(
                "Assets:Cash", "Expenses:Food", "1", "Synthetic", date.today().isoformat()
            )
        with pytest.raises(LedgerError, match="crud_has_history"):
            await journal.delete_account("Assets:Cash", journal.source)
        await journal.edit_account("Assets:Cash", "Renamed", "120", True, journal.source)
        assert journal.ledger.accounts[0].active
        assert "txncount-active: TRUE" in journal.source
        assert_official(journal.source)

    asyncio.run(scenario())


def test_category_hierarchy_lifecycle_and_unused_deletion(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.edit_category("Expenses:Food", "Meals", False, journal.source)
        assert not journal.ledger.category_active("Expenses:Food:Groceries")
        with pytest.raises(LedgerError, match="crud_inactive"):
            await journal.add_expense(
                "Assets:Cash", "Expenses:Food:Groceries", "1", "Shop", date.today().isoformat()
            )
        with pytest.raises(LedgerError, match="crud_children"):
            await journal.delete_category("Expenses:Food", journal.source)
        with pytest.raises(LedgerError, match="crud_has_history"):
            await journal.delete_category("Expenses:Food:Groceries", journal.source)
        await journal.edit_category("Expenses:Food", "Meals", True, journal.source)
        assert journal.ledger.category_active("Expenses:Food:Groceries")
        assert "txncount-active: TRUE" in journal.source
        await journal.create_account("Unused", "Unused", "USD")
        await journal.delete_account("Assets:Unused", journal.source)
        await journal.create_category("Unused", "Unused")
        await journal.delete_category("Expenses:Unused", journal.source)
        assert "Assets:Unused" not in journal.source
        assert "Expenses:Unused" not in journal.source
        assert_official(journal.source, ("UAH", "USD"))

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["expense", "account", "category", "currencies", "delete"])
def test_crud_conflicts_never_overwrite_external_edits(tmp_path: Path, operation: str) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        expected = journal.source
        target = tmp_path / FILENAME
        external = expected + "\n; external synthetic change\n"
        target.write_text(external, newline="")
        identifier = journal.ledger.transactions[-1].id
        with pytest.raises(ConflictError):
            if operation == "expense":
                await journal.edit_expense(
                    identifier,
                    "Assets:Cash",
                    "Expenses:Food",
                    "2",
                    "Shop",
                    "2026-01-01",
                    "2026-01-01T00:00:00+00:00",
                    "Edited",
                    expected,
                )
            elif operation == "account":
                await journal.edit_account("Assets:Cash", "Other", "100", True, expected)
            elif operation == "category":
                await journal.edit_category("Expenses:Food", "Other", True, expected)
            elif operation == "currencies":
                await journal.set_operating_currencies("USD", expected)
            else:
                await journal.delete_expense(identifier, expected)
        assert target.read_text(newline="") == external
        assert not journal.ready

    asyncio.run(scenario())


def test_edit_draft_after_poll_reload_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        baseline = journal.source
        (tmp_path / FILENAME).write_text(baseline + "\n; external\n", newline="")
        assert await journal.poll()
        with pytest.raises(ConflictError, match="crud_stale"):
            await journal.edit_category("Expenses:Food", "Stale name", True, baseline)
        assert journal.source.endswith("; external\n")

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["backup", "write"])
def test_crud_integration_failures_keep_last_verified_ledger(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        source = journal.source
        setattr(
            journal.storage.folder,
            failure,
            AsyncMock(side_effect=StorageError("Synthetic failure")),
        )
        with pytest.raises(StorageError):
            await journal.edit_category("Expenses:Food", "Other", True, source)
        assert journal.source == source
        assert (tmp_path / FILENAME).read_text(newline="") == source
        assert not journal.ready

    asyncio.run(scenario())


def test_referenced_unused_account_cannot_be_deleted(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.create_account("Unused", "Unused", "USD")
        source = journal.source + '\n2026-01-01 note Assets:Unused "keep"\n'
        (tmp_path / FILENAME).write_text(source, newline="")
        await journal.load()
        with pytest.raises(LedgerError, match="crud_referenced"):
            await journal.delete_account("Assets:Unused", source)
        assert (tmp_path / FILENAME).read_text(newline="") == source

    asyncio.run(scenario())


def test_split_and_opening_entries_are_readonly_in_expense_editor(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        with pytest.raises(LedgerError, match="crud_transaction_readonly"):
            journal.expense_for_edit(journal.ledger.transactions[0].id)
        source = journal.source.replace(
            "Expenses:Food:Groceries 10 UAH",
            "Expenses:Food:Groceries 5 UAH\n    Expenses:Food 5 UAH",
        )
        assert source != journal.source
        (tmp_path / FILENAME).write_text(source, newline="")
        await review_external_fixture(journal)
        with pytest.raises(LedgerError, match="crud_transaction_readonly"):
            await journal.delete_expense(journal.ledger.transactions[-1].id, source)
        assert journal.source == source

    asyncio.run(scenario())


def test_fava_missing_operating_currency_error_is_resolved_in_existing_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        path = tmp_path / FILENAME
        legacy = journal.source.replace('option "operating_currency" "UAH"', "")
        path.write_text(legacy, newline="")
        fava = FavaLedger(str(path), poll_watcher=True)
        assert any("No operating currency specified" in error.message for error in fava.errors)
        await review_external_fixture(journal)
        await journal.set_operating_currencies("UAH USD EUR", journal.source)
        await journal.edit_account("Assets:Cash", "Renamed", "120", False, journal.source)
        await journal.edit_category("Expenses:Food", "Renamed", False, journal.source)
        fava.load_file()
        assert not fava.errors
        assert fava.options["operating_currency"] == ["UAH", "USD", "EUR"]

    asyncio.run(scenario())


def test_expense_can_change_account_currency_category_and_year_without_conversion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.create_account(
            "Usd", "USD wallet", "USD", "20", opened=EPOCH, opening_date=EPOCH
        )
        original = next(t for t in journal.ledger.transactions if t.kind == "expense")
        await journal.edit_expense(
            original.id,
            "Assets:Usd",
            "Expenses:Food",
            "7.50",
            "Other",
            "2025-12-31",
            "2025-12-31T12:30:00+02:00",
            "Changed year",
            journal.source,
        )
        balances = journal.ledger.balances()
        assert balances["Assets:Cash"] == Decimal("100")
        assert balances["Assets:Usd"] == Decimal("12.50")
        changed = next(t for t in journal.ledger.transactions if t.id == original.id)
        assert changed.date == date(2025, 12, 31)
        assert {p.currency for p in changed.postings} == {"USD"}
        assert_official(journal.source, ("UAH", "USD"))

    asyncio.run(scenario())


def test_creating_opening_for_zero_account_then_changing_to_zero_preserves_identity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH")
        await journal.edit_account("Assets:Cash", "Wallet", "10", True, journal.source)
        identifier = journal.ledger.transactions[0].id
        await journal.edit_account("Assets:Cash", "Wallet", "0", True, journal.source)
        assert journal.ledger.transactions[0].id == identifier
        assert journal.ledger.balances()["Assets:Cash"] == 0
        assert_official(journal.source)

    asyncio.run(scenario())


@pytest.mark.parametrize("currencies", ["", "UA", "UAH EUR USDX"])
def test_invalid_operating_currencies_leave_original_untouched(
    tmp_path: Path, currencies: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        source = journal.source
        with pytest.raises(LedgerError):
            await journal.set_operating_currencies(currencies, source)
        assert journal.source == source
        assert (tmp_path / FILENAME).read_text(newline="") == source

    asyncio.run(scenario())


def test_ui_edit_dialog_preserves_input_after_poll_conflict_and_cancel_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.journal = await seeded(tmp_path)
        view._refresh()
        event = MagicMock()
        event.control.data = view.journal.ledger.transactions[-1].id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        submit_handler = dialog.actions[-1].on_click
        assert isinstance(dialog, ft.AlertDialog)
        assert isinstance(dialog.content, ft.Column)
        amount = transaction_form(dialog).amount
        assert isinstance(amount, ft.TextField)
        amount.value = "13.50"
        source = view.journal.source + "\n; external edit while dialog open\n"
        (tmp_path / FILENAME).write_text(source, newline="")
        assert await view.journal.poll()
        submit = dialog.actions[-1]
        assert isinstance(submit, ft.TextButton)
        assert submit.on_click is not None
        await submit_handler(MagicMock())
        assert amount.value == "13.50"
        assert not amount.disabled
        assert (tmp_path / FILENAME).read_text(newline="") == source
        assert page.pop_dialog.call_count == 0
        assert "Data changed" in view.message.value
        assert page.show_dialog.call_count == 2

    asyncio.run(scenario())


def test_ui_deletion_requires_confirmation_and_updates_balances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.journal = await seeded(tmp_path)
        event = MagicMock()
        event.control.data = view.journal.ledger.transactions[-1].id
        source = view.journal.source
        view._delete_transaction(event)
        assert view.journal.source == source
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == "Permanently delete?"
        cancel = dialog.actions[0]
        cancel.on_click(MagicMock())
        assert view.journal.source == source
        view._delete_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        await dialog.actions[-1].on_click(MagicMock())
        assert view.journal.ledger.balances()["Assets:Cash"] == 100
        assert len(view.journal.ledger.transactions) == 1
        assert view.message_key == "saved_backup"

    asyncio.run(scenario())


def test_currency_draft_survives_poll_and_requires_reopening_after_external_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.journal = await seeded(tmp_path)
        view._refresh()
        view.operating_currencies.value = "UAH USD"
        baseline = view.operating_source
        source = view.journal.source + "\n; externally updated\n"
        (tmp_path / FILENAME).write_text(source, newline="")
        assert await view.journal.poll()
        view._refresh()
        assert view.operating_currencies.value == "UAH USD"
        assert view.operating_source == baseline
        await view._save_operating_currencies(MagicMock())
        assert view.operating_currencies.value == "UAH USD"
        assert (tmp_path / FILENAME).read_text(newline="") == source
        assert "Data changed" in view.message.value
        view._reset_operating_currencies(MagicMock())
        assert view.operating_currencies.value == "UAH"
        assert view.operating_source == source
        view.operating_currencies.value = "UAH USD"
        await view._save_operating_currencies(MagicMock())
        assert view.journal.ledger.operating_currencies == ("UAH", "USD")

    asyncio.run(scenario())


def test_partial_crud_write_is_reported_and_preserves_verified_backup(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = journal.source

        async def corrupt(data: bytes, *, expected_hash: str | None) -> None:
            (tmp_path / FILENAME).write_bytes(b"; synthetic partial write")

        journal.storage.folder.write = corrupt
        with pytest.raises(StorageError, match="uncertain"):
            await journal.edit_category("Expenses:Food", "Edited", True, original)
        assert not journal.ready
        assert not journal.storage.loaded
        assert journal.source == original
        assert any(p.read_text(newline="") == original for p in (tmp_path / "backups").iterdir())

    asyncio.run(scenario())


def test_invalid_expense_edits_never_modify_original(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = journal.source
        identifier = journal.ledger.transactions[-1].id
        for field, value in [
            ("timestamp", "2026-01-01T25:00:00"),
            ("occurred", "not-a-date"),
            ("amount", "0"),
            ("description", "a" * 501),
        ]:
            parameters = dict(
                account="Assets:Cash",
                category="Expenses:Food",
                amount="10",
                payee="Shop",
                occurred="2026-01-01",
                timestamp="2026-01-01T12:00:00+00:00",
                description="",
            )
            parameters[field] = value
            with pytest.raises(LedgerError):
                await journal.edit_expense(identifier, **parameters, expected_source=original)
            assert journal.source == original
            assert (tmp_path / FILENAME).read_text(newline="") == original

    asyncio.run(scenario())


def test_delete_with_history_offers_deactivation_instead_of_destroying_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.journal = await seeded(tmp_path)
        event = MagicMock()
        event.control.data = ("account", "Assets:Cash")
        source = view.journal.source
        view._delete_entity(event)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == "Edit"
        assert "cannot change" in dialog.content.controls[1].value
        active = dialog.content.controls[3]
        assert isinstance(active, ft.Checkbox)
        active.value = False
        await dialog.actions[-1].on_click(MagicMock())
        assert not view.journal.ledger.accounts[0].active
        assert len(view.journal.ledger.transactions) == 2
        assert view.journal.ledger.balances()["Assets:Cash"] == 90
        assert any(p.read_text(newline="") == source for p in (tmp_path / "backups").iterdir())
        view._refresh()
        view._transaction_dialog(view.journal)
        dialog = page.show_dialog.call_args.args[0]
        account = transaction_form(dialog).account
        assert not any(option.key == "Assets:Cash" for option in account.options)

    asyncio.run(scenario())


def test_handwritten_expense_can_be_edited_without_serialized_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        source = journal.source
        (tmp_path / FILENAME).write_text(source, newline="")
        await review_external_fixture(journal)
        external = journal.ledger.transactions[-1]
        assert journal.expense_for_edit(external.id) == external
        assert journal.source == source
        await journal.edit_expense(
            external.id,
            "Assets:Cash",
            "Expenses:Food",
            "11",
            "",
            external.date.isoformat(),
            external.timestamp,
            "Edited handwritten",
            source,
        )
        assert journal.ledger.transactions[-1].narration == "Edited handwritten"
        assert "\n  id:" not in journal.source

    asyncio.run(scenario())


def test_complex_opening_still_allows_safe_rename_and_deactivation(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        first = journal.ledger.transactions[0]
        source = journal.source + "\n" + transaction_text(replace(first, id="second-opening"))
        (tmp_path / FILENAME).write_text(source, newline="")
        await journal.load()
        with pytest.raises(LedgerError, match="crud_opening_complex"):
            journal.opening_balance("Assets:Cash")
        await journal.edit_account("Assets:Cash", "Renamed", None, False, source)
        assert not journal.ledger.accounts[0].active
        assert len(journal.ledger.transactions) == 3
        assert journal.ledger.balances()["Assets:Cash"] == 190
        assert_official(journal.source)

    asyncio.run(scenario())
