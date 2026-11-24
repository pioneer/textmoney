import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import change, click, click_async, dropdown_field, transaction_form
from test_commission_ui import setup
from test_dialog_keyboard import keyboard_session, press

from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input
from app.storage import WriteError
from app.ui.datetime_fields import DateTimeFields
from app.ui.dialogs import EditorDialog, _controls
from app.ui.finance import FinanceView


def field(root: ft.Control, name: str) -> ft.Control:
    return next(control for control in _controls(root) if control.data == name)


@pytest.fixture(autouse=True)
def mock_batch_scroll(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    scroll = AsyncMock()
    monkeypatch.setattr(ft.Column, "scroll_to", scroll)
    return scroll


def test_batch_add_scrolls_to_new_rows_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_batch_scroll: AsyncMock
) -> None:
    async def scenario() -> None:
        view, page, _ = await setup(tmp_path, monkeypatch)
        focus = AsyncMock()
        monkeypatch.setattr(ft.TextField, "focus", focus)
        calls = MagicMock()
        calls.attach_mock(focus, "focus")
        calls.attach_mock(mock_batch_scroll, "scroll")
        view._new_transaction(MagicMock())
        original_dialog = page.show_dialog.call_args.args[0]
        start = field(original_dialog.content, "batch_start")
        assert isinstance(start, ft.TextButton)
        click(start)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, EditorDialog)
        add = field(dialog.form, "batch_add_row")
        assert isinstance(add, ft.TextButton)

        keys = []
        for _ in range(2):
            await click_async(add)
            rows = [control for control in _controls(dialog.form) if control.data == "batch_row"]
            details = field(rows[-1], "batch_row_details")
            assert isinstance(details, ft.TextButton)
            assert isinstance(details.key, ft.ScrollKey)
            keys.append(details.key)
            mock_batch_scroll.assert_awaited_with(scroll_key=details.key, duration=350)
        assert keys[0] != keys[1]
        assert [call[0] for call in calls.mock_calls] == ["focus", "scroll", "focus", "scroll"]

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_batch_add_button_is_beside_each_rows_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        view, page, _ = await setup(tmp_path, monkeypatch, language)
        monkeypatch.setattr(ft.TextField, "focus", AsyncMock())
        view._new_transaction(MagicMock())
        original_dialog = page.show_dialog.call_args.args[0]
        start = field(original_dialog.content, "batch_start")
        assert isinstance(start, ft.TextButton)
        click(start)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, EditorDialog)

        def assert_actions() -> list[ft.Control]:
            rows = [control for control in _controls(dialog.form) if control.data == "batch_row"]
            for row in rows:
                details = field(row, "batch_row_details")
                add = field(row, "batch_add_row")
                assert isinstance(details, ft.TextButton) and details.expand
                assert isinstance(add, ft.TextButton)
                assert any(
                    isinstance(control, ft.Row)
                    and control.controls == [details, add]
                    and not control.wrap
                    for control in _controls(row)
                )
            return rows

        rows = assert_actions()
        assert len(rows) == 1
        add = field(rows[0], "batch_add_row")
        assert isinstance(add, ft.TextButton)
        await click_async(add)
        rows = assert_actions()
        assert len(rows) == 2
        add = field(rows[1], "batch_add_row")
        assert isinstance(add, ft.TextButton)
        await click_async(add)
        rows = assert_actions()
        assert len(rows) == 3
        remove = next(
            control
            for control in _controls(rows[1])
            if isinstance(control, ft.IconButton) and control.icon == ft.Icons.DELETE_OUTLINE
        )
        click(remove)
        assert len(assert_actions()) == 2

    asyncio.run(scenario())


def test_batch_preserves_draft_overrides_and_validates_before_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        monkeypatch.setattr(ft.TextField, "focus", AsyncMock())
        view._new_transaction(MagicMock())
        original_dialog = page.show_dialog.call_args.args[0]
        original = transaction_form(original_dialog)
        original.account.value = "Assets:Cash"
        original.category.value = "Expenses:Food"
        original.amount.value = "12.40"
        original.description.value = "First"
        original.payee.value = "Shop"
        original.when.occurred.value = date_input(date(2000, 1, 2), "en")
        original.when.clock.value = "13:45"
        start = field(original_dialog.content, "batch_start")
        assert isinstance(start, ft.TextButton)
        assert start.icon == ft.Icons.PLAYLIST_ADD and start.content == view.tr("batch_add")
        click(start)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, EditorDialog)
        submit = dialog.actions[-1]
        assert isinstance(submit, ft.TextButton) and isinstance(submit.content, ft.Column)
        label, summary_container = submit.content.controls
        assert isinstance(label, ft.Text) and label.text_align == ft.TextAlign.CENTER
        assert isinstance(summary_container, ft.Container)
        assert summary_container.alignment == ft.Alignment.CENTER
        assert isinstance(summary_container.content, ft.Text)
        assert summary_container.content.text_align == ft.TextAlign.CENTER
        form = dialog.form
        for name in ("batch_kind", "batch_account", "batch_category"):
            dropdown = field(form, name)
            assert isinstance(dropdown, ft.Dropdown) and dropdown.expand
            assert any(
                isinstance(control, ft.Row)
                and len(control.controls) == 1
                and dropdown_field(control.controls[0]) is dropdown
                for control in _controls(form)
            )
        advanced = field(form, "batch_advanced")
        shared_payee = field(form, "batch_payee")
        assert isinstance(advanced, ft.ExpansionTile) and not advanced.expanded
        assert shared_payee in list(_controls(advanced))
        assert isinstance(shared_payee, ft.TextField) and shared_payee.value == "Shop"
        account = field(form, "batch_account")
        assert isinstance(account, ft.Dropdown)
        assert account.value == "Assets:Cash"
        amount = field(form, "batch_row_amount")
        assert isinstance(amount, ft.TextField) and amount.value == "12.40" and amount.autofocus
        add = field(form, "batch_add_row")
        assert isinstance(add, ft.TextButton)
        assert add.icon == ft.Icons.ADD and add.content == view.tr("batch_add_row")
        await click_async(add)
        await click_async(add)
        row_controls = [control for control in _controls(form) if control.data == "batch_row"]
        assert len(row_controls) == 3
        descriptions = [field(row, "batch_row_description") for row in row_controls]
        assert all(
            isinstance(item, ft.TextField) and item.value == "First" for item in descriptions
        )
        assert isinstance(descriptions[1], ft.TextField)
        descriptions[1].value = "Second"
        for name in ("batch_row_account", "batch_row_category"):
            dropdown = field(row_controls[1], name)
            assert isinstance(dropdown, ft.Dropdown) and dropdown.expand
            assert any(
                isinstance(control, ft.Row)
                and len(control.controls) == 1
                and dropdown_field(control.controls[0]) is dropdown
                for control in _controls(row_controls[1])
            )
        amounts = [field(row, "batch_row_amount") for row in row_controls]
        assert all(isinstance(item, ft.TextField) for item in amounts)
        assert isinstance(amounts[1], ft.TextField) and isinstance(amounts[2], ft.TextField)
        amounts[1].value, amounts[2].value = "8.75", "bad"
        override = field(row_controls[1], "batch_row_account")
        assert isinstance(override, ft.Dropdown)
        override.value = "Assets:Euro"
        change(override)
        details = field(row_controls[1], "batch_row_details")
        assert isinstance(details, ft.TextButton)
        assert details.icon == ft.Icons.TUNE
        assert view.tr("batch_row_details") in str(details.content)
        assert view.tr("batch_overridden") in str(details.content)
        row = row_controls[1]
        assert isinstance(row, ft.Column)
        details_body = next(
            control
            for control in row.controls
            if isinstance(control, ft.Column)
            and any(child is override for child in _controls(control))
        )
        assert not details_body.visible
        click(details)
        assert details_body.visible
        click(details)
        assert not details_body.visible
        override.value = ""
        change(override)
        assert view.tr("batch_overridden") not in str(details.content)
        assert details.icon == ft.Icons.TUNE
        override.value = "Assets:Euro"
        change(override)
        override_payee = field(row_controls[1], "batch_override_payee")
        row_payee = field(row_controls[1], "batch_row_payee")
        assert isinstance(override_payee, ft.Checkbox) and isinstance(row_payee, ft.TextField)
        override_payee.value = True
        change(override_payee)
        row_payee.value = "Other shop"
        category_override = field(row_controls[2], "batch_row_category")
        assert isinstance(category_override, ft.Dropdown)
        category_override.value = "Expenses:Other-Fees"
        change(category_override)
        account.value = "Assets:Cash"
        change(account)
        assert override.value == "Assets:Euro"
        first_date = next(c for c in _controls(row_controls[0]) if isinstance(c, DateTimeFields))
        second_date = next(c for c in _controls(row_controls[1]) if isinstance(c, DateTimeFields))
        assert first_date.clock.value == "13:45"
        assert second_date.occurred.value == first_date.occurred.value
        second_date.occurred.value = "invalid"

        submit = dialog.actions[-1]
        assert isinstance(submit, ft.TextButton)
        before = journal.source
        await click_async(submit)
        assert journal.source == before
        error = field(row_controls[2], "batch_row_error")
        assert isinstance(error, ft.Text) and error.visible
        date_error = field(row_controls[1], "batch_row_error")
        assert isinstance(date_error, ft.Text) and date_error.visible
        second_date.occurred.value = first_date.occurred.value
        amounts[2].value = "31.20"
        change(amounts[2])
        summary = field(submit, "batch_summary")
        assert isinstance(summary, ft.Text) and "USD" in summary.value and "EUR" in summary.value
        await click_async(submit)
        assert journal.source != before
        assert [item.narration for item in journal.ledger.transactions] == [
            "First",
            "Second",
            "First",
        ]
        assert [
            journal.ledger.cashflow_details(item).account.account
            for item in journal.ledger.transactions
        ] == [
            "Assets:Cash",
            "Assets:Euro",
            "Assets:Cash",
        ]
        assert journal.ledger.cashflow_details(journal.ledger.transactions[2]).category.account == (
            "Expenses:Other-Fees"
        )
        assert [item.payee for item in journal.ledger.transactions] == [
            "Shop",
            "Other shop",
            "Shop",
        ]
        assert journal.ledger.transactions[0].timestamp.endswith("13:45:00")
        assert page.show_dialog.call_args.args[0] is dialog
        assert page.pop_dialog.call_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", [ft.PagePlatform.LINUX, ft.PagePlatform.ANDROID])
def test_batch_editor_mounts_dynamic_fields_and_desktop_keyboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: ft.PagePlatform
) -> None:
    async def scenario() -> None:
        (tmp_path / FILENAME).write_text(
            "2000-01-01 open Assets:Cash USD\n2000-01-01 open Expenses:Food\n"
        )
        session = keyboard_session(platform)
        session.page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda _: session.page))
        focus = AsyncMock()
        monkeypatch.setattr(ft.TextField, "focus", focus)
        view = FinanceView()
        view._apply_layout(390)
        journal = view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view._new_transaction(MagicMock())
        original_dialog = session.page._dialogs.controls[-1]
        assert isinstance(original_dialog, ft.AlertDialog)
        original = transaction_form(original_dialog)
        original.account.value = "Assets:Cash"
        original.category.value = "Expenses:Food"
        original.amount.value = "2"
        original.when.occurred.value = date_input(date(2000, 1, 2), "en")
        start = field(original_dialog, "batch_start")
        await session.dispatch_event(start._i, "click", None)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, EditorDialog)
        first_amount = field(dialog.form, "batch_row_amount")
        assert isinstance(first_amount, ft.TextField) and first_amount.autofocus
        assert not any(
            isinstance(control, ft.TextField) and control is not first_amount and control.autofocus
            for control in _controls(dialog.form)
        )
        add = field(dialog.form, "batch_add_row")
        await session.dispatch_event(add._i, "click", None)
        row_controls = [
            control for control in _controls(dialog.form) if control.data == "batch_row"
        ]
        second_amount = field(row_controls[1], "batch_row_amount")
        assert isinstance(second_amount, ft.TextField)
        focus.assert_awaited_once()
        second_amount.value = "3"
        session.page.update()
        if platform == ft.PagePlatform.LINUX:
            await session.dispatch_event(second_amount._i, "focus", None)
            await press(session, "Enter")
            assert len(journal.ledger.transactions) == 2
            assert not dialog.open
            assert not any(
                isinstance(item, ft.AlertDialog) and item.open
                for item in session.page._dialogs.controls
            )
        else:
            assert len(row_controls) == 2

    asyncio.run(scenario())


def test_batch_entry_rejects_unsupported_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, _ = await setup(tmp_path, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        original = transaction_form(dialog)
        button = field(dialog.content, "batch_start")
        assert isinstance(button, ft.TextButton) and not button.disabled
        original.fee_enabled.value = True
        change(original.fee_enabled)
        assert button.disabled
        click(button)
        assert page.show_dialog.call_args.args[0] is dialog
        original.fee_enabled.value = False
        change(original.fee_enabled)
        original.sign.selected = ["transfer"]
        change(original.sign)
        original.second_account.value = "Assets:Euro"
        change(original.second_account)
        original.sign.selected = ["-"]
        change(original.sign)
        assert button.disabled

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_income_batch_and_cancel_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch, language)
        monkeypatch.setattr(ft.TextField, "focus", AsyncMock())
        view._new_transaction(MagicMock())
        original_dialog = page.show_dialog.call_args.args[0]
        original = transaction_form(original_dialog)
        original.sign.selected = ["+"]
        change(original.sign)
        original.account.value = "Assets:Cash"
        original.category.value = "Income:Salary"
        original.amount.value = "7"
        original.when.occurred.value = date_input(date(2000, 1, 2), language)
        start = field(original_dialog.content, "batch_start")
        assert isinstance(start, ft.TextButton) and not start.disabled
        click(start)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, EditorDialog)
        kind = field(dialog.form, "batch_kind")
        category = field(dialog.form, "batch_category")
        assert isinstance(kind, ft.Dropdown) and isinstance(category, ft.Dropdown)
        assert {option.key for option in category.options} == {"Income:Salary"}
        kind.value = "expense"
        change(kind)
        assert category.value == "Income:Salary"
        assert {option.key for option in category.options} >= {"Income:Salary", "Expenses:Food"}
        kind.value = "income"
        change(kind)
        cancel = dialog.actions[0]
        assert isinstance(cancel, ft.TextButton)
        click(cancel)
        confirmation = page.show_dialog.call_args.args[0]
        assert isinstance(confirmation, ft.AlertDialog)
        assert confirmation.title == view.tr("batch_discard_title")
        assert not journal.ledger.transactions
        keep = confirmation.actions[0]
        assert isinstance(keep, ft.TextButton)
        click(keep)
        submit = dialog.actions[-1]
        assert isinstance(submit, ft.TextButton)
        await click_async(submit)
        assert journal.ledger.transactions[-1].kind == "income"

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_batch_write_requires_reconciliation_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        monkeypatch.setattr(ft.TextField, "focus", AsyncMock())
        view._new_transaction(MagicMock())
        original_dialog = page.show_dialog.call_args.args[0]
        original = transaction_form(original_dialog)
        original.account.value = "Assets:Cash"
        original.category.value = "Expenses:Food"
        original.amount.value = "2"
        original.when.occurred.value = date_input(date(2000, 1, 2), "en")
        start = field(original_dialog.content, "batch_start")
        assert isinstance(start, ft.TextButton)
        click(start)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, EditorDialog)
        submit = dialog.actions[-1]
        assert isinstance(submit, ft.TextButton)
        folder = journal.storage.folder
        write = folder.write

        async def fail(data: bytes, *, expected_hash: str | None) -> None:
            if committed:
                await write(data, expected_hash=expected_hash)
            raise WriteError("Synthetic write failure", committed=committed)

        monkeypatch.setattr(folder, "write", fail)
        await click_async(submit)
        assert submit.disabled
        reload_button = field(dialog.form, "batch_reload")
        assert isinstance(reload_button, ft.TextButton) and reload_button.visible
        monkeypatch.setattr(folder, "write", write)
        await click_async(reload_button)
        assert submit.disabled is committed
        assert len(journal.ledger.transactions) == int(committed)
        if not committed:
            await click_async(submit)
            assert len(journal.ledger.transactions) == 1

    asyncio.run(scenario())
