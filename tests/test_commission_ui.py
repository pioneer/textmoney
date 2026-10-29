import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import change, click_async, transaction_form

from app.domain.ledger import BANK_FEES
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input, format_amount
from app.storage import StorageError
from app.ui.finance import FinanceView

SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
    name: "Synthetic cash"
2000-01-01 open Assets:Euro EUR
2000-01-01 open Expenses:Food
    name: "Synthetic food"
2000-01-01 open Income:Salary
    name: "Synthetic income"
2000-01-01 open Expenses:Other-Fees
    name: "Synthetic fees"
"""


async def setup(
    path: Path, monkeypatch: pytest.MonkeyPatch, language: str = "en"
) -> tuple[FinanceView, MagicMock, Journal]:
    (path / FILENAME).write_text(SOURCE)
    journal = Journal(DesktopFolder(str(path), FILENAME))
    await journal.load()
    await journal.set_default_fee_category("Expenses:Other-Fees", journal.source)
    page = MagicMock(spec=ft.Page)
    page.width = 390 if language == "uk" else 1366
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.journal, view.language = journal, language
    view._refresh()
    return view, page, journal


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize("mode", ["added", "deducted"])
def test_commission_form_creates_reopens_edits_and_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, kind: str, mode: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch, language)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.sign.selected = ["-" if kind == "expense" else "+"]
        change(form.sign)
        form.account.value = "Assets:Cash"
        form.category.value = "Expenses:Food" if kind == "expense" else "Income:Salary"
        form.amount.value = "100"
        form.when.occurred.value = date_input(date(2026, 1, 10), language)
        form.description.value = "Synthetic commission"
        form.fee_enabled.value = True
        form.fee.value, form.fee_mode.value = "5", mode
        change(form.fee_enabled)
        assert form.fee.visible and form.fee_mode.visible
        assert not form.fee_currency.visible and not form.second_account.visible
        fee_category = "Expenses:Other-Fees"
        assert form.fee_category.value == fee_category
        assert not form.fee_category.disabled
        assert {o.key for o in form.fee_category.options} >= {
            BANK_FEES,
            "Expenses:Food",
            fee_category,
        }
        principal = (
            Decimal("95" if mode == "deducted" else "100")
            if kind == "expense"
            else Decimal("105" if mode == "added" else "100")
        )
        movement = principal + 5 if kind == "expense" else principal - 5
        assert str(form.preview.value).endswith(
            view.tr(
                "commission_expense_preview" if kind == "expense" else "commission_income_preview",
                principal=format_amount(principal, language, "USD"),
                total=format_amount(movement, language, "USD"),
            )
        )
        await click_async(dialog.actions[-1])
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        details = journal.ledger.cashflow_details(saved)
        assert details.fee == 5 and details.fee_category == fee_category
        assert details.account.amount == (-movement if kind == "expense" else movement)
        view._edit_transaction(MagicMock(control=ft.IconButton(data=saved.id)))
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.fee_enabled.value and form.fee.value == "5"
        assert form.fee_mode.value == "added"
        assert Decimal(form.amount.value) == details.amount
        assert not form.advanced.expanded
        assert view.tr("transfer_fee") in str(form.advanced.subtitle)
        form.amount.value, form.fee.value = "200", "7"
        form.account.value, form.fee_category.value = "Assets:Euro", "Expenses:Food"
        change(form.account)
        change(form.fee)
        assert "EUR" in str(form.preview.value) and not form.fee_currency.visible
        await click_async(dialog.actions[-1])
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        details = journal.ledger.cashflow_details(saved)
        assert details.fee == 7 and details.amount == 200
        assert details.fee_category == "Expenses:Food" and details.fee_currency == "EUR"
        view._edit_transaction(MagicMock(control=ft.IconButton(data=saved.id)))
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.fee_enabled.value = False
        change(form.fee_enabled)
        await click_async(dialog.actions[-1])
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        assert len(saved.postings) == 2
        view._delete_transaction(MagicMock(control=ft.IconButton(data=saved.id)))
        await click_async(page.show_dialog.call_args.args[0].actions[-1])
        assert not journal.ledger.transactions
        assert view.error_message is None

    asyncio.run(scenario())


def test_commission_drafts_are_mode_local_and_special_receipts_ignore_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.account.value, form.amount.value = "Assets:Cash", "100"
        form.fee_enabled.value, form.fee.value, form.fee_mode.value = True, "5", "deducted"
        change(form.fee_enabled)
        form.sign.selected = ["+"]
        change(form.sign)
        assert not form.fee_enabled.value
        form.fee_enabled.value, form.fee.value = True, "9"
        change(form.fee_enabled)
        form.sign.selected = ["transfer"]
        change(form.sign)
        assert not form.fee_enabled.value
        form.sign.selected = ["-"]
        change(form.sign)
        assert form.fee_enabled.value and form.fee.value == "5"
        assert (
            form.fee_mode.value == "deducted" and form.fee_category.value == "Expenses:Other-Fees"
        )
        form.sign.selected = ["+"]
        change(form.sign)
        assert form.fee_enabled.value and form.fee.value == "9"
        form.special.value = "reimbursement"
        change(form.special)
        assert not form.fee.visible and not form.preview.visible
        form.category.value = "Expenses:Food"
        form.when.occurred.value = date_input(date(2026, 1, 10), view.language)
        await click_async(dialog.actions[-1])
        assert view.error_message is None
        assert journal.ledger.transactions[-1].kind == "reimbursement"
        assert len(journal.ledger.transactions[-1].postings) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["invalid", "conflict", "backup"])
def test_commission_save_failure_keeps_every_draft_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.account.value, form.category.value = "Assets:Cash", "Expenses:Food"
        form.amount.value, form.description.value = "100", "Unsaved synthetic draft"
        form.when.occurred.value = date_input(date(2026, 1, 10), view.language)
        form.fee_enabled.value, form.fee.value = True, "100" if failure == "invalid" else "5"
        form.fee_mode.value = "deducted"
        change(form.fee_enabled)
        before = journal.source
        disk = before
        if failure == "conflict":
            disk += "\n; external\n"
            (tmp_path / FILENAME).write_text(disk)
        elif failure == "backup":
            journal.storage.folder.backup = AsyncMock(side_effect=StorageError("Synthetic"))
        await click_async(dialog.actions[-1])
        assert view.error_message
        assert form.amount.value == "100"
        assert form.description.value == "Unsaved synthetic draft"
        assert form.fee.value == ("100" if failure == "invalid" else "5")
        assert form.fee_mode.value == "deducted" and form.fee_enabled.value
        assert journal.source == before
        assert (tmp_path / FILENAME).read_text() == disk
        page.pop_dialog.assert_not_called()

    asyncio.run(scenario())
