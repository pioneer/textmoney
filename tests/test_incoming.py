import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import TZPATH, reset_tzpath

import flet as ft
import pytest
from beancount import loader
from fava.core import FavaLedger
from form_controls import change, transaction_form

from app.domain.ledger import ADJUSTMENTS, EPOCH, LedgerError
from app.domain.storage import ConflictError
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import translate
from app.storage import StorageError
from app.ui.finance import FinanceView, ScreenClass
from tools.check_beancount_apk import MVP_SMOKE

DAY = "2026-01-10"
STAMP = "2026-01-10T12:00:00.123456+02:00"


def test_packaging_smoke_script_against_current_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    try:
        exec(compile(MVP_SMOKE, "<APK accounting smoke>", "exec"), {})
    finally:
        reset_tzpath(TZPATH)


async def seeded(directory: Path) -> Journal:
    journal = Journal(DesktopFolder(str(directory), FILENAME), today=lambda: date(2026, 3, 1))
    await journal.load()
    await journal.create_account("Bank", "Bank", "UAH", "100")
    await journal.create_account("Cash", "Cash", "UAH")
    await journal.create_account("Euro", "Euro", "EUR")
    await journal.create_category("Food", "Food")
    await journal.create_category("Work:Salary", "Salary", root="Income")
    await journal.add_expense(
        "Assets:Bank", "Expenses:Food", "30", "", DAY, "Groceries and cola", STAMP
    )
    return journal


def totals(journal: Journal, start: str = "2026-01-01", end: str = "2026-03-01"):
    return journal.ledger.period_totals(date.fromisoformat(start), date.fromisoformat(end))


def test_income_partial_reimbursements_other_account_and_pretracking_adjustment(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        expense = next(t for t in journal.ledger.transactions if t.kind == "expense")
        await journal.add_incoming(
            "Assets:Bank",
            "Income:Work:Salary",
            "500",
            "Employer",
            DAY,
            "income",
            "Salary",
            STAMP,
        )
        for value in ("4", "6"):
            await journal.add_incoming(
                "Assets:Cash",
                "Expenses:Food",
                value,
                "Friend",
                "2026-02-01",
                "reimbursement",
                "Cola",
                STAMP,
                related_expense=expense.id,
            )
        await journal.add_incoming(
            "Assets:Cash",
            ADJUSTMENTS,
            "25",
            "",
            "2026-02-01",
            "adjustment",
            "Recovery of an old purchase",
            STAMP,
        )
        # An unlinked reimbursement can legitimately exceed this period's expenses.
        await journal.add_incoming(
            "Assets:Cash",
            "Expenses:Food",
            "50",
            "",
            "2026-02-02",
            "reimbursement",
        )
        await journal.load()
        assert journal.ledger.balances() == {
            "Assets:Bank": Decimal("570"),
            "Assets:Cash": Decimal("85"),
            "Assets:Euro": Decimal("0"),
        }
        assert totals(journal) == {
            "income": {"UAH": Decimal("500")},
            "expenses": {"UAH": Decimal("-30")},
        }
        assert totals(journal, "2026-01-01", "2026-01-31")["expenses"] == {"UAH": Decimal("30")}
        assert totals(journal, "2026-02-01", "2026-02-28") == {
            "income": {},
            "expenses": {"UAH": Decimal("-60")},
        }
        assert journal.opening_balance("Assets:Cash") == 0
        assert journal.ledger.adjustments_opened == EPOCH
        assert journal.source.count(f"open {ADJUSTMENTS}") == 1
        linked = [t for t in journal.ledger.transactions if t.related_expense]
        assert len(linked) == 2 and all(t.related_expense == expense.id for t in linked)
        entries, errors, _ = loader.load_string(journal.source)
        assert not errors
        assert entries
        fava = FavaLedger(str(tmp_path / FILENAME))
        assert not list(fava.errors)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,category",
    [
        ("income", "Income:Work:Salary"),
        ("reimbursement", "Expenses:Food"),
        ("adjustment", ADJUSTMENTS),
    ],
)
def test_incoming_edit_delete_preserves_ids_metadata_comments_and_backup(
    tmp_path: Path,
    kind: str,
    category: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        expense = next(t for t in journal.ledger.transactions if t.kind == "expense")
        backup = await journal.add_incoming(
            "Assets:Cash",
            category,
            "10",
            "",
            DAY,
            kind,
            "First",
            STAMP,
            related_expense=expense.id if kind == "reimbursement" else None,
        )
        assert backup and (tmp_path / backup).exists()
        original = journal.ledger.transactions[-1]
        path = tmp_path / FILENAME
        # Target one directive, retaining unrelated source and unsupported safe directives.
        decorated = journal.source.replace(
            f'  id: "{original.id}"',
            f'  id: "{original.id}"\n  receipt: "Synthetic"\n  ; keep transaction comment',
        )
        decorated += '\n2026-01-01 event "test" "Preserve me"\n; unrelated tail\n'
        path.write_text(decorated, newline="")
        await journal.load()
        before = journal.source
        await journal.edit_transaction(
            original.id,
            "Assets:Bank",
            category,
            "12",
            "Sender",
            "2026-02-01",
            STAMP,
            'Updated\n"detail"',
            before,
        )
        updated = journal.transaction_for_edit(original.id)
        assert updated.timestamp == STAMP
        assert updated.related_expense == original.related_expense
        assert updated.narration == 'Updated\n"detail"'
        assert 'receipt: "Synthetic"' in journal.source
        assert "; keep transaction comment" in journal.source
        assert journal.source.endswith("; unrelated tail\n")
        assert not loader.load_string(journal.source)[1]
        await journal.delete_transaction(original.id, journal.source)
        assert not any(t.id == original.id for t in journal.ledger.transactions)
        assert "; keep transaction comment" in journal.source
        assert totals(journal) == {"income": {}, "expenses": {"UAH": Decimal("30")}}
        assert journal.ledger.balances()["Assets:Bank"] == Decimal("70")

    asyncio.run(scenario())


def test_no_currency_conversion_and_income_categories_do_not_accept_expenses(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        expense = next(t for t in journal.ledger.transactions if t.kind == "expense")
        before = journal.source
        with pytest.raises(LedgerError, match="incoming_reference"):
            await journal.add_incoming(
                "Assets:Euro",
                "Expenses:Food",
                "5",
                "",
                DAY,
                "reimbursement",
                related_expense=expense.id,
            )
        with pytest.raises(LedgerError):
            await journal.add_expense("Assets:Bank", "Income:Work:Salary", "5", "", DAY)
        with pytest.raises(LedgerError, match="incoming_category"):
            await journal.add_incoming("Assets:Bank", "Expenses:Food", "5", "", DAY, "income")
        assert journal.source == before
        await journal.add_incoming("Assets:Euro", "Income:Work:Salary", "5", "", DAY, "income")
        await journal.add_incoming("Assets:Bank", "Income:Work:Salary", "7", "", DAY, "income")
        assert totals(journal)["income"] == {"EUR": Decimal("5"), "UAH": Decimal("7")}

    asyncio.run(scenario())


def test_spent_income_edits_warn_and_inactive_category_lifecycle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.add_incoming(
            "Assets:Cash", "Income:Work:Salary", "10", "", DAY, "income", timestamp=STAMP
        )
        incoming = journal.ledger.transactions[-1]
        await journal.add_expense("Assets:Cash", "Expenses:Food", "9", "", "2026-02-01")
        before = journal.source
        await journal.edit_transaction(
            incoming.id, "Assets:Cash", "Income:Work:Salary", "8", "", DAY, STAMP, "", before
        )
        assert journal.ledger.balance_warnings() == {"Assets:Cash"}
        with pytest.raises(LedgerError, match="crud_has_history"):
            await journal.delete_category("Income:Work:Salary", journal.source)
        await journal.delete_transaction(incoming.id, journal.source)
        assert journal.ledger.balances()["Assets:Cash"] == -9
        before = journal.source
        await journal.edit_category("Income:Work", "Work", False, before)
        with pytest.raises(LedgerError, match="incoming_category"):
            await journal.add_incoming("Assets:Cash", "Income:Work:Salary", "1", "", DAY, "income")
        await journal.edit_category("Income:Work", "Work", True, journal.source)
        await journal.create_category("Unused", "Unused", root="Income")
        await journal.edit_category("Income:Unused", "Renamed", True, journal.source)
        await journal.delete_category("Income:Unused", journal.source)
        assert not any(c.id == "Income:Unused" for c in journal.ledger.categories)

    asyncio.run(scenario())


def test_incoming_conflict_and_backup_failure_preserve_original(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        with pytest.raises(ConflictError, match="crud_stale"):
            await journal.add_incoming(
                "Assets:Cash",
                ADJUSTMENTS,
                "5",
                "",
                DAY,
                "adjustment",
                expected_source="stale",
            )
        assert journal.source == before
        path = tmp_path / FILENAME
        path.write_text(before + "; external\n", newline="")
        with pytest.raises(ConflictError):
            await journal.add_incoming("Assets:Cash", ADJUSTMENTS, "5", "", DAY, "adjustment")
        assert path.read_text(newline="") == before + "; external\n"
        await journal.load()
        folder = journal.storage.folder
        assert isinstance(folder, DesktopFolder)
        original_backup = folder.backup
        folder.backup = AsyncMock(side_effect=StorageError("Synthetic backup failure"))
        with pytest.raises(StorageError, match="backup failure"):
            await journal.add_incoming("Assets:Cash", ADJUSTMENTS, "5", "", DAY, "adjustment")
        assert path.read_text(newline="") == before + "; external\n"
        folder.backup = original_backup

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["read", "write"])
def test_incoming_storage_errors_do_not_claim_success(
    tmp_path: Path,
    operation: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        folder = journal.storage.folder
        assert isinstance(folder, DesktopFolder)
        before = journal.source
        failure = AsyncMock(side_effect=StorageError("Synthetic storage failure"))
        if operation == "read":
            folder.read = failure
        else:
            folder.write = failure
        with pytest.raises(StorageError):
            await journal.add_incoming("Assets:Cash", ADJUSTMENTS, "5", "", DAY, "adjustment")
        assert (tmp_path / FILENAME).read_text(newline="") == before
        assert journal.source == before
        assert not journal.ready
        assert not journal.storage.loaded

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "amount,day,kind,category",
    [
        ("0", DAY, "income", "Income:Work:Salary"),
        ("-1", DAY, "income", "Income:Work:Salary"),
        ("NaN", DAY, "income", "Income:Work:Salary"),
        ("1", "2026-03-02", "income", "Income:Work:Salary"),
        ("1", DAY, "unknown", "Expenses:Food"),
        ("1", DAY, "adjustment", "Equity:Opening-Balances"),
    ],
)
def test_invalid_incoming_preserves_source_and_backups(
    tmp_path: Path,
    amount: str,
    day: str,
    kind: str,
    category: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        backups = set((tmp_path / "backups").iterdir())
        with pytest.raises(LedgerError):
            await journal.add_incoming("Assets:Cash", category, amount, "", day, kind)
        assert journal.source == before
        assert (tmp_path / FILENAME).read_text(newline="") == before
        assert set((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


def test_reference_is_not_a_receivable_and_survives_original_expense_deletion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = next(t for t in journal.ledger.transactions if t.kind == "expense")
        # No cap, outstanding amount, automatic offset, or cascade deletion.
        await journal.add_incoming(
            "Assets:Cash",
            "Expenses:Food",
            "40",
            "",
            DAY,
            "reimbursement",
            related_expense=original.id,
        )
        incoming = journal.ledger.transactions[-1]
        await journal.delete_expense(original.id, journal.source)
        assert journal.transaction_for_edit(incoming.id).related_expense == original.id
        assert totals(journal)["income"] == {}
        assert totals(journal)["expenses"] == {"UAH": Decimal("-40")}
        await journal.delete_transaction(incoming.id, journal.source)
        assert journal.ledger.balances()["Assets:Cash"] == 0

    asyncio.run(scenario())


def test_income_ui_failed_save_retains_draft_and_stale_journal_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._transaction_dialog(journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.choose("income")
        account, category, amount, description = (
            form.account,
            form.category,
            form.amount,
            form.description,
        )
        account.value = "Assets:Cash"
        category.value = "Income:Work:Salary"
        amount.value = "12.34"
        description.value = "Keep my draft"
        journal.source += "; polled change\n"
        submit = dialog.actions[-1]
        await submit.on_click(MagicMock())
        assert view.error_message == "crud_stale"
        assert amount.value == "12.34"
        assert description.value == "Keep my draft"
        assert not submit.disabled
        page.pop_dialog.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("layout", list(ScreenClass))
def test_unified_action_survives_home_layout_rebuild(layout: ScreenClass) -> None:
    view = FinanceView()
    view.screen_class = layout
    view._build_home_layout()
    view._build_home_layout()
    assert view.fab.content == translate("en", "add_transaction")
    for screen in (view.home_screen, view.transactions_screen):
        assert not any(isinstance(c, ft.TextButton) for c in screen.controls)


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_incoming_ui_and_reference_prefill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.language = language
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._transaction_dialog(journal)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == translate(language, "add_transaction")
        form = transaction_form(dialog)
        account, reference, category, amount = (
            form.account,
            form.reference,
            form.category,
            form.amount,
        )
        form.choose("reimbursement")
        assert reference.visible
        assert all((option.key or "").startswith("Expenses:") for option in category.options)
        expense = next(t for t in journal.ledger.transactions if t.kind == "expense")
        reference.value = expense.id
        change(reference)
        assert category.value == "Expenses:Food"
        assert amount.value == "30"
        assert account.value is None  # Never assumes the original paying account receives it.
        form.choose("adjustment")
        assert not category.visible and category.value == ADJUSTMENTS
        assert reference.value == expense.id
        assert not reference.visible
        amount.value = "9"
        reference.value = expense.id
        journal.source += "; external reload\n"
        report_error = MagicMock()
        view._error = report_error
        change(reference)
        assert amount.value == "9"
        assert isinstance(report_error.call_args.args[0], ConflictError)

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("kind", ["expense", "income", "reimbursement", "adjustment"])
def test_unified_form_creates_edits_and_deletes_each_supported_movement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, language: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.language = language
        view.timezone_name = "UTC"
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.scrollable and dialog.modal
        form = transaction_form(dialog)
        account, reference, category, amount = (
            form.account,
            form.reference,
            form.category,
            form.amount,
        )
        description, when = form.description, form.when
        assert form.sign.selected == ["-"]
        assert not form.advanced.expanded
        form.choose(kind)
        assert account.label == translate(
            language, "source_account" if kind == "expense" else "destination_account"
        )
        assert reference.visible == (kind == "reimbursement")
        assert category.visible == (kind != "adjustment")
        assert category.label == translate(
            language, "income_category" if kind == "income" else "expense_category"
        )
        account.value = "Assets:Bank"
        category.value = (
            ADJUSTMENTS
            if kind == "adjustment"
            else "Income:Work:Salary"
            if kind == "income"
            else "Expenses:Food"
        )
        amount.value = "5"
        description.value = "Synthetic unified form"
        when.occurred.value = "01/10/2026" if language == "en" else "10.01.2026"
        when.clock.value = "12:00"
        form.payee.value = "Synthetic counterparty"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        created = journal.ledger.transactions[-1]
        assert created.kind == kind
        assert created.payee == "Synthetic counterparty"
        assert created.narration == "Synthetic unified form"
        asset = next(p for p in created.postings if p.account == "Assets:Bank")
        counterpart = next(p for p in created.postings if p.account != "Assets:Bank")
        assert asset.amount == (-5 if kind == "expense" else 5)
        original_totals = totals(journal)
        assert original_totals["income"] == ({"UAH": Decimal("5")} if kind == "income" else {})
        assert original_totals["expenses"] == {
            "UAH": Decimal("35" if kind == "expense" else "25" if kind == "reimbursement" else "30")
        }
        event = MagicMock()
        event.control.data = created.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, reference, category, amount = (
            form.account,
            form.reference,
            form.category,
            form.amount,
        )
        assert form.sign.disabled and form.second_account.disabled
        assert isinstance(form.advanced.subtitle, str)
        assert translate(language, f"kind_{kind}") in form.advanced.subtitle
        assert not reference.visible
        assert amount.value == "5"
        assert account.value == "Assets:Bank"
        assert category.value == counterpart.account
        assert not any(
            isinstance(c, ft.Dropdown) and c.label == translate(language, "incoming_type")
            for c in dialog.content.controls
        )
        amount.value = "7"
        await dialog.actions[-1].on_click(MagicMock())
        edited = journal.transaction_for_edit(created.id)
        assert edited.kind == kind and edited.timestamp == created.timestamp
        assert next(p.amount for p in edited.postings if p.account == "Assets:Bank") == (
            -7 if kind == "expense" else 7
        )
        view._delete_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == translate(language, "crud_confirm")
        await dialog.actions[-1].on_click(MagicMock())
        assert all(t.id != created.id for t in journal.ledger.transactions)
        assert journal.ledger.balances()["Assets:Bank"] == 70
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


def test_switching_modes_preserves_common_draft_and_each_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._transaction_dialog(journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, description, when, tile = (
            form.account,
            form.category,
            form.amount,
            form.description,
            form.when,
            form.advanced,
        )
        account.value = "Assets:Bank"
        category.value = "Expenses:Food"
        amount.value = "12.34"
        description.value = "Unsaved\ntext"
        form.payee.value = "Synthetic person"
        tile.expanded = True
        original_when = when.values()
        form.choose("income")
        assert category.value is None
        assert {o.key for o in category.options} == {"Income:Work", "Income:Work:Salary"}
        category.value = "Income:Work:Salary"
        form.choose("adjustment")
        assert not category.visible and category.value == ADJUSTMENTS
        form.choose("expense")
        assert category.visible and category.value == "Expenses:Food"
        form.choose("income")
        assert category.value == "Income:Work:Salary"
        assert account.value == "Assets:Bank"
        assert amount.value == "12.34" and description.value == "Unsaved\ntext"
        assert form.payee.value == "Synthetic person" and tile.expanded
        assert when.values() == original_when
        before = journal.source
        dialog.actions[0].on_click(MagicMock())
        assert journal.source == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "postings", "expected"),
    [
        (
            "expense",
            "  Assets:Bank -8 UAH\n  Expenses:Food 5 UAH\n  Expenses:Food 3 UAH\n",
            ["Bank: −8.00 UAH", "Food: 5.00 UAH", "Food: 3.00 UAH"],
        ),
        (
            "external",
            "  Assets:Bank -8 UAH\n  Assets:Cash 8 UAH\n",
            ["Bank: −8.00 UAH", "Cash: 8.00 UAH"],
        ),
    ],
)
def test_unified_editor_keeps_complex_and_external_movements_readonly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    postings: str,
    expected: list[str],
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        source = journal.source + (
            '\n2026-01-10 * "Synthetic protected movement"\n'
            '  id: "protected"\n'
            f'  txncount-kind: "{kind}"\n' + postings
        )
        (tmp_path / FILENAME).write_text(source, newline="")
        await journal.load()
        view = FinanceView()
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        event = MagicMock()
        event.control.data = "protected"
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == "Details"
        assert len(dialog.actions) == 1
        assert not any(isinstance(c, ft.TextField) for c in dialog.content.controls)
        text = [c.value for c in dialog.content.controls if isinstance(c, ft.Text)]
        assert all(value in text for value in expected)
        assert journal.source == source
        assert (tmp_path / FILENAME).read_text(newline="") == source

    asyncio.run(scenario())
