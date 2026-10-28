import asyncio
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from beancount import loader
from beancount.core import data
from beancount.ops import summarize
from form_controls import transaction_form

from app.domain.ledger import EPOCH, LedgerError
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.beancount_journal import JournalCodec
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import StorageError, WriteError
from app.ui.finance import FinanceView


async def seeded(tmp_path: Path) -> Journal:
    journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
    await journal.load()
    await journal.create_account(
        "Cash", "Готівка", "UAH", "100.00", opened=EPOCH, opening_date=EPOCH
    )
    await journal.create_category("Food:Groceries", "Продукти", opened=EPOCH)
    return journal


def test_end_to_end_accounts_categories_expense_reload_and_official_engine(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.create_account("Dollar", "Dollar wallet", "USD", "25")
        await journal.create_account("Empty", "No opening balance", "EUR")
        before = (tmp_path / FILENAME).read_bytes()
        backup = await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food:Groceries",
            "12.34",
            'Магазин "Shop"',
            "2026-10-01",
        )
        assert backup is not None
        assert (tmp_path / backup).read_bytes() == before
        assert (tmp_path / FILENAME).read_bytes().startswith(before)
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("87.66")
        assert journal.ledger.balances()["Assets:Dollar"] == Decimal("25")
        assert journal.ledger.balances()["Assets:Empty"] == Decimal("0")
        assert {c.id for c in journal.ledger.categories} == {
            "Expenses:Food",
            "Expenses:Food:Groceries",
        }
        for transaction in journal.ledger.transactions:
            assert transaction.id.startswith("source-")
            assert datetime.fromisoformat(transaction.timestamp).utcoffset() is None
        assert not any(line.lstrip().startswith("id:") for line in journal.source.splitlines())
        assert "timestamp:" not in journal.source
        assert 'name: "Готівка"' in journal.source and "time:" in journal.source
        reloaded = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await reloaded.load()
        assert reloaded.ledger == journal.ledger
        assert reloaded.ledger.accounts[0].name == "Готівка"
        entries, errors, _ = loader.load_file(str(tmp_path / FILENAME))
        assert not errors, [e.message for e in errors]
        accounts, _ = summarize.balance_by_account(entries)
        assert next(iter(accounts["Assets:Cash"])).units.number == Decimal("87.66")
        assert any(
            isinstance(e, data.Transaction) and "textmoney-kind" not in e.meta for e in entries
        )
        assert any(transaction.kind == "expense" for transaction in reloaded.ledger.transactions)
        assert not (tmp_path / "storage-test.beancount").exists()

    asyncio.run(scenario())


def test_invalid_expense_and_duplicates_do_not_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = (tmp_path / FILENAME).read_bytes()
        backups = set((tmp_path / "backups").iterdir())
        for amount in ("-1", "NaN", "0", "1.001"):
            with pytest.raises(LedgerError):
                await journal.add_expense(
                    "Assets:Cash", "Expenses:Food", amount, "Shop", "2026-10-01"
                )
        with pytest.raises(LedgerError):
            await journal.create_account("Cash", "Duplicate", "UAH")
        with pytest.raises(LedgerError):
            await journal.create_category("Food", "Duplicate")
        with pytest.raises(LedgerError):
            await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "bad-date")
        assert original == (tmp_path / FILENAME).read_bytes()
        assert backups == set((tmp_path / "backups").iterdir())

    asyncio.run(scenario())


@pytest.mark.parametrize("after_replacement", [False, True])
def test_atomic_write_failure_invalidates_journal_until_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_replacement: bool
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = (tmp_path / FILENAME).read_bytes()
        source = journal.source
        ledger = journal.ledger
        operation = "_sync_directory" if after_replacement else "_preserve_permissions"
        with monkeypatch.context() as patch:
            from app.infrastructure.desktop_storage import _sync_directory

            replace_file = os.replace
            committed = False

            def replace(source: Path, destination: Path) -> None:
                nonlocal committed
                replace_file(source, destination)
                committed = destination == tmp_path / FILENAME

            def sync(directory: Path) -> None:
                if committed and directory == tmp_path:
                    raise OSError("synthetic write failure")
                _sync_directory(directory)

            patch.setattr("app.infrastructure.desktop_storage.os.replace", replace)
            patch.setattr(
                f"app.infrastructure.desktop_storage.{operation}",
                sync
                if after_replacement
                else MagicMock(side_effect=OSError("synthetic write failure")),
            )
            with pytest.raises(WriteError) as error:
                await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "2026-10-01")
        assert error.value.committed is after_replacement
        assert not journal.ready and not journal.storage.loaded
        assert journal.source == source and journal.ledger == ledger
        assert journal.storage.recovery_backup is not None
        assert (tmp_path / journal.storage.recovery_backup).read_bytes() == original
        assert ((tmp_path / FILENAME).read_bytes() != original) is after_replacement
        with pytest.raises(LedgerError, match="Reload"):
            await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "2026-10-01")
        await journal.load()
        assert journal.ready
        assert len(journal.ledger.transactions) == len(ledger.transactions) + int(after_replacement)

    asyncio.run(scenario())


def test_append_preserves_comments_metadata_custom_directives_and_other_files(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = (tmp_path / FILENAME).read_text()
        suffix = (
            '\n; an unrelated comment\n2026-01-01 custom "unknown-to-app" "leave this alone"\n'
            '  opaque: "metadata untouched"\n'
        )
        original = (original + suffix).replace("\n", "\r\n")
        (tmp_path / FILENAME).write_bytes(original.encode())
        other = tmp_path / "unrelated.beancount"
        other.write_bytes(b"unsupported and private; do not modify")
        await journal.load()
        await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "2026-10-01")
        result = (tmp_path / FILENAME).read_bytes()
        assert result.startswith(original.encode())
        assert b"\r\n" in result[len(original.encode()) :]
        assert other.read_bytes() == b"unsupported and private; do not modify"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "suffix",
    [
        '\ninclude "other.beancount"\n',
        '\nplugin "beancount.plugins.auto_accounts"\n',
        "\n2026-01-01 pad Assets:Cash Equity:Opening-Balances\n",
        '\n2026-01-01 quantum "unsupported"\n',
        "\n2026-01-01 balance Assets:Cash 50 UAH\n",
    ],
)
def test_unsupported_content_blocks_writes_and_preserves_original(
    tmp_path: Path, suffix: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        with (tmp_path / FILENAME).open("ab") as stream:
            stream.write(suffix.encode())
        original = (tmp_path / FILENAME).read_bytes()
        with pytest.raises(LedgerError, match="Line"):
            await journal.load()
        assert not journal.ready
        with pytest.raises(LedgerError, match="Reload"):
            await journal.create_account("New", "New", "USD")
        assert (tmp_path / FILENAME).read_bytes() == original

    asyncio.run(scenario())


def test_conflict_blocks_save_then_reload_updates_balances_and_retains_external_entries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        external = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await external.load()
        await external.add_expense("Assets:Cash", "Expenses:Food", "90", "External", "2026-10-01")
        original = (tmp_path / FILENAME).read_bytes()
        with pytest.raises(ConflictError):
            await journal.add_expense("Assets:Cash", "Expenses:Food", "20", "Draft", "2026-10-01")
        assert not journal.ready
        assert (tmp_path / FILENAME).read_bytes() == original
        await journal.load()
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("10")
        await journal.add_expense("Assets:Cash", "Expenses:Food", "20", "Draft", "2026-10-01")
        assert journal.ledger.balance_warnings() == {"Assets:Cash"}

    asyncio.run(scenario())


def test_external_poll_recalculates_and_deleted_journal_never_becomes_empty(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        external = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await external.load()
        await external.add_expense("Assets:Cash", "Expenses:Food", "5", "External", "2026-10-01")
        assert await journal.poll()
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("95")
        assert not await journal.poll()
        (tmp_path / FILENAME).unlink()
        with pytest.raises(StorageError, match="disappeared"):
            await journal.poll()
        assert not journal.ready
        with pytest.raises(LedgerError):
            await journal.create_account("New", "New", "USD")
        assert not (tmp_path / FILENAME).exists()

    asyncio.run(scenario())


def test_backup_failure_prevents_write_and_uncertain_write_requires_reload(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        folder = journal.storage.folder
        original = (tmp_path / FILENAME).read_bytes()
        folder.backup = AsyncMock(side_effect=StorageError("Synthetic backup failure"))
        with pytest.raises(StorageError):
            await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "2026-10-01")
        assert (tmp_path / FILENAME).read_bytes() == original
        assert not journal.ready
        await journal.load()
        folder.backup = AsyncMock(return_value="backups/synthetic")
        folder.write = AsyncMock(side_effect=StorageError("Synthetic write failure"))
        with pytest.raises(StorageError, match="uncertain"):
            await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "2026-10-01")
        assert not journal.ready
        assert journal.ledger.balances()["Assets:Cash"] == Decimal("100")

    asyncio.run(scenario())


def test_android_adapter_allowlisted_filename_and_backup_namespace() -> None:
    async def scenario() -> None:
        bridge = MagicMock()
        bridge.call = AsyncMock(return_value={"ok": True, "value": "backups/textmoney-synthetic"})
        folder = AndroidFolder(bridge, "content://synthetic/tree/test", FILENAME)
        await folder.backup(b"journal")
        arguments = bridge.call.call_args.kwargs
        assert arguments["filename"] == FILENAME
        assert arguments["name"].startswith("textmoney-")
        with pytest.raises(StorageError):
            AndroidFolder(bridge, "content://synthetic/tree/test", "../other")
        with pytest.raises(StorageError):
            DesktopFolder("/synthetic", "private.beancount")

    asyncio.run(scenario())


def test_ui_success_conflict_reload_and_validation_retain_form_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.update = MagicMock()
        page = MagicMock(spec=ft.Page)
        page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view.journal = await seeded(tmp_path)
        view._refresh()
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, when, payee = (
            form.account,
            form.category,
            form.amount,
            form.when,
            form.payee,
        )
        account.value = "Assets:Cash"
        category.value = "Expenses:Food"
        amount.value = "1"
        payee.value = "Draft payee"
        when.occurred.value = "10/01/2026"
        external = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await external.load()
        await external.add_expense("Assets:Cash", "Expenses:Food", "90", "External", "2026-10-01")
        await dialog.actions[-1].on_click(MagicMock())
        assert "outside TextMoney" in (view.message.value or "")
        assert amount.value == "1" and payee.value == "Draft payee"
        assert page.show_dialog.call_count == 2
        assert view.fab.disabled
        await view._reload(MagicMock())
        assert amount.value == "1" and payee.value == "Draft payee"
        # A reloaded journal must not silently rebase the old dialog.
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "crud_stale"
        view._transaction_dialog(view.journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, when, payee = (
            form.account,
            form.category,
            form.amount,
            form.when,
            form.payee,
        )
        account.value = "Assets:Cash"
        category.value = "Expenses:Food"
        amount.value = "1"
        payee.value = "Draft payee"
        when.occurred.value = "10/01/2026"
        await dialog.actions[-1].on_click(MagicMock())
        page.pop_dialog.assert_called_once()
        assert view.journal.ledger.balances()["Assets:Cash"] == Decimal("9")
        view._transaction_dialog(view.journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, payee = form.account, form.category, form.amount, form.payee
        account.value = "Assets:Cash"
        category.value = "Expenses:Food"
        amount.value = "10"
        payee.value = "Still here"
        await dialog.actions[-1].on_click(MagicMock())
        assert amount.value == "10" and payee.value == "Still here"
        assert view.balance_warning.visible
        assert view.journal.ledger.balances()["Assets:Cash"] == -1
        view._navigate("transactions")
        assert view.screen_host.content is view.transactions_screen
        assert (
            sum(
                isinstance(control, ft.Container) and isinstance(control.content, ft.Row)
                for control in view.history.controls
            )
            == 4
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "transaction",
    [
        '2026-01-01 * "Unbalanced"\n  Assets:Cash -1 UAH\n  Expenses:Food 2 UAH\n',
        '2026-01-01 * "Implicit"\n  Assets:Cash -1 UAH\n  Expenses:Food\n',
        '2026-01-01 * "Cost"\n  Assets:Cash -1 UAH {1 USD}\n  Expenses:Food 1 USD\n',
        '2026-01-01 * "Price"\n  Assets:Cash -1 UAH @ 1 UAH\n  Expenses:Food 1 UAH\n',
        '2026-01-01 * "Unknown"\n  Assets:Missing -1 UAH\n  Expenses:Food 1 UAH\n',
        '2026-01-01 * "Currency"\n  Assets:Cash -1 USD\n  Expenses:Food 1 USD\n',
        '2026-01-01 * "Expression"\n  Assets:Cash (1 / 0) UAH\n  Expenses:Food 1 UAH\n',
        '2026-01-01 * "Timezone"\n  time: "12:00:00+02:00"\n'
        "  Assets:Cash -1 UAH\n  Expenses:Food 1 UAH\n",
        '9999-01-01 * "Future"\n  Assets:Cash -1 UAH\n  Expenses:Food 1 UAH\n',
    ],
)
def test_external_accounting_errors_are_explicitly_rejected(transaction: str) -> None:
    prefix = (
        "1970-01-01 open Assets:Cash UAH\n1970-01-01 open Expenses:Food\n"
        "1970-01-01 open Equity:Opening-Balances\n"
        '1970-01-01 * "Opening"\n  Assets:Cash 10 UAH\n  Equity:Opening-Balances -10 UAH\n'
    )
    with pytest.raises(LedgerError):
        JournalCodec().parse(prefix + transaction)


def test_change_during_backup_refuses_journal_save(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        target = tmp_path / FILENAME
        changed = target.read_bytes() + b"\n; external edit during backup\n"
        original_backup = journal.storage.folder.backup

        async def racing_backup(data: bytes) -> str:
            name = await original_backup(data)
            target.write_bytes(changed)
            return name

        journal.storage.folder.backup = racing_backup
        with pytest.raises(ConflictError):
            await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "Shop", "2026-10-01")
        assert target.read_bytes() == changed
        assert not journal.ready

    asyncio.run(scenario())
