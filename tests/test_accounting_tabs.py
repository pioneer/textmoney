import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click

from app.domain.ledger import EPOCH
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input
from app.ui.datetime_fields import DateTimeFields
from app.ui.finance import FinanceView


def select_tab(view: FinanceView, index: int) -> None:
    view.accounting_tabs.selected_index = index
    view._accounting_tab_changed(MagicMock(control=view.accounting_tabs, data=str(index)))


def setup_view(monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    return FinanceView(), page


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_subtabs_share_layout_and_preserve_selection(
    monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    view, _ = setup_view(monkeypatch)
    assert view.accounts_screen.controls == [view.accounting_tabs]
    assert view.accounting_tabs.length == 3
    assert view.accounts_panel.visible and not view.categories_panel.visible
    select_tab(view, 1)
    for width in [390, 800, 1366]:
        view.language = language
        view._refresh()
        view._apply_layout(width)
        view._navigate("settings")
        view._navigate("accounts")
        assert view.accounting_tabs.selected_index == 1
        assert not view.accounts_panel.visible and view.categories_panel.visible
        assert view.accounts_panel.width == view.categories_panel.width
        assert [tab.label for tab in view.accounting_tab_bar.tabs if isinstance(tab, ft.Tab)] == [
            view.tr("accounts_title"),
            view.tr("categories_title"),
            view.tr("budget_title"),
        ]
        for panel, button, listing in [
            (view.accounts_panel, view.account_save, view.account_list),
            (view.categories_panel, view.category_save, view.category_list),
        ]:
            assert panel.padding == 16 and panel.border_radius == 12
            assert panel.border == view.accounts_panel.border
            assert isinstance(panel.content, ft.Column)
            toolbar, content = panel.content.controls
            assert isinstance(toolbar, ft.Row)
            assert toolbar.controls == [button] and toolbar.wrap
            assert content is listing
            assert button.disabled
    select_tab(view, 0)
    assert view.accounts_panel.visible and not view.categories_panel.visible


def test_welcome_create_account_link_opens_accounts_subtab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = setup_view(monkeypatch)
    select_tab(view, 1)
    view._go_accounts(MagicMock())
    assert view.current_screen == "accounts"
    assert view.accounting_tabs.selected_index == 0
    assert view.accounts_panel.visible and not view.categories_panel.visible


def open_creation(view: FinanceView, page: MagicMock, category: bool) -> ft.AlertDialog:
    if category:
        view._create_category(MagicMock())
        view.category_root.value = "Income"
        view.category_id.value = "Work:Salary"
        view.category_name.value = "Salary"
    else:
        view._create_account(MagicMock())
        view.account_root.value = "Liabilities"
        view._account_type_changed(MagicMock())
        view.account_id.value = "Credit"
        view.account_name.value = "Credit card"
        view.currency.value = "UAH"
        view.opening.value = "200"
        assert view.opening.label == view.tr("transfer_owed")
    dialog = page.show_dialog.call_args.args[0]
    assert isinstance(dialog, ft.AlertDialog)
    assert dialog.modal and dialog.scrollable
    assert isinstance(dialog.content, ft.Column)
    assert dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    return dialog


async def submit(dialog: ft.AlertDialog) -> None:
    button = dialog.actions[-1]
    assert isinstance(button, ft.TextButton) and button.on_click is not None
    await cast(Callable[[object], Awaitable[None]], button.on_click)(MagicMock())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("width", [390, 800, 1366])
def test_account_creation_fields_have_matching_widths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, width: int
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view.journal = journal
        view.language = language
        view._refresh()
        view._apply_layout(width)
        view._create_account(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        assert dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        account_type_row = dialog.content.controls[0]
        assert isinstance(account_type_row, ft.Row)
        assert account_type_row.controls == [view.account_root]
        assert view.account_root.expand is True
        assert account_type_row.width is None and not account_type_row.wrap
        alternatives = view.account_suggestion_alternatives
        assert not alternatives.visible
        view.account_name.value = "Synthetic bank"
        view._account_attributes_changed(MagicMock())
        assert alternatives.visible and alternatives.options
        alternatives_row = next(
            control
            for control in dialog.content.controls
            if isinstance(control, ft.Row) and control.controls == [alternatives]
        )
        assert alternatives.expand is True and alternatives.width is None
        assert alternatives_row.width is None and not alternatives_row.wrap
        selected = alternatives.options[0].key
        alternatives.value = selected
        view._account_suggestion_selected(MagicMock(control=alternatives))
        assert view.account_id.value == selected and view.account_code_manual
        view.account_name.value = ""
        view._account_attributes_changed(MagicMock())
        assert not alternatives.visible
        fields = [
            view.account_name,
            view.account_id,
            view.currency,
            view.opening,
            *(
                control
                for control in dialog.content.controls
                if isinstance(control, DateTimeFields)
            ),
        ]
        assert len(fields) == 6
        assert all(field.width == view.account_name.width == 320 for field in fields)

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
def test_category_creation_dropdowns_fill_the_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, width: int
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view.journal = journal
        view.language = language
        view._refresh()
        view._apply_layout(width)
        view._create_category(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        assert dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        for dropdown in (view.category_root, view.category_parent):
            row = next(
                control
                for control in dialog.content.controls
                if isinstance(control, ft.Row) and control.controls == [dropdown]
            )
            assert row.width is None and not row.wrap
            assert dropdown.expand is True and dropdown.width is None

    asyncio.run(scenario())


@pytest.mark.parametrize("category", [False, True])
@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
def test_entity_edit_codes_are_bold_and_fields_fill_the_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: bool, language: str, width: int
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_category("Food", "Food", opened=EPOCH)
        await journal.create_account("Cash", "Cash", "UAH", opened=EPOCH)
        view.journal = journal
        view.language = language
        view._refresh()
        view._apply_layout(width)
        item = journal.ledger.categories[0] if category else journal.ledger.accounts[0]
        before = journal.source
        view._edit_entity(
            MagicMock(control=ft.IconButton(data=("category" if category else "account", item.id)))
        )
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        code = next(
            control
            for control in dialog.content.controls
            if isinstance(control, ft.Text) and control.value == item.id
        )
        assert code.selectable
        assert code.weight == ft.FontWeight.BOLD
        assert dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        name = next(
            control
            for control in dialog.content.controls
            if isinstance(control, ft.TextField)
            and control.label == view.tr("category_name" if category else "account_name")
        )
        assert name.width is None and name.value == item.name
        if not category:
            opening = next(
                control
                for control in dialog.content.controls
                if isinstance(control, ft.TextField) and control.label == view.tr("opening_balance")
            )
            assert opening.width is None
            when = next(
                control
                for control in dialog.content.controls
                if isinstance(control, DateTimeFields)
            )
            assert when.occurred.expand is True
            date_row = when.controls[0]
            assert isinstance(date_row, ft.Row) and date_row.width is None
        view._dismiss(MagicMock())
        assert journal.source == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("account", "opening"),
        ("account", "history"),
        ("category", "history"),
        ("category", "children"),
        ("category", "both"),
    ],
)
@pytest.mark.parametrize(
    "language,title",
    [("en", "Cannot delete"), ("uk", "Неможливо видалити"), ("ru", "Невозможно удалить")],
)
@pytest.mark.parametrize("edit", [False, True])
def test_protected_delete_explains_the_reason_before_explicit_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    reason: str,
    language: str,
    title: str,
    edit: bool,
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account(
            "Cash",
            "Cash",
            "UAH",
            "100" if reason == "opening" else "",
            opened=EPOCH,
            opening_date=EPOCH,
        )
        await journal.create_category("Food", "Food", opened=EPOCH)
        if reason in {"history", "both"}:
            await journal.add_expense("Assets:Cash", "Expenses:Food", "10", "", "2026-10-18")
        if reason in {"children", "both"}:
            await journal.create_category("Food:Meals", "Meals", opened=EPOCH)
        view.journal = journal
        view.language = language
        identifier = "Expenses:Food" if kind == "category" else "Assets:Cash"
        original = journal.source
        backups = set((tmp_path / "backups").iterdir())
        view._delete_entity(MagicMock(control=ft.IconButton(data=(kind, identifier))))
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == title and dialog.modal and dialog.scrollable
        assert isinstance(dialog.content, ft.Column)
        assert all(isinstance(control, ft.Text) for control in dialog.content.controls)
        text = [c.value for c in dialog.content.controls if isinstance(c, ft.Text)]
        assert identifier in text
        assert (view.tr("crud_has_history") in text) == (reason in {"opening", "history", "both"})
        assert (view.tr("crud_children") in text) == (reason in {"children", "both"})
        assert [button.content for button in dialog.actions] == [
            view.tr("crud_cancel"),
            view.tr("crud_edit"),
        ]
        page.show_dialog.assert_called_once()
        click(dialog.actions[1 if edit else 0])
        page.pop_dialog.assert_called_once()
        if edit:
            editor = page.show_dialog.call_args.args[0]
            assert editor is not dialog and editor.title == view.tr("crud_edit")
            assert isinstance(editor.content, ft.Column)
            active = next(c for c in editor.content.controls if isinstance(c, ft.Checkbox))
            assert active.value is True
        else:
            page.show_dialog.assert_called_once()
        assert journal.source == original == (tmp_path / FILENAME).read_text()
        assert set((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


@pytest.mark.parametrize("category", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_unused_entity_still_gets_a_real_delete_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: bool, cancel: bool
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Cash", "Cash", "UAH", opened=EPOCH)
        await journal.create_category("Food", "Food", opened=EPOCH)
        view.journal = journal
        identifier = "Expenses:Food" if category else "Assets:Cash"
        original = journal.source
        view._delete_entity(
            MagicMock(
                control=ft.IconButton(data=("category" if category else "account", identifier))
            )
        )
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == view.tr("crud_confirm")
        assert dialog.actions[-1].content == view.tr("crud_delete")
        if cancel:
            click(dialog.actions[0])
            assert journal.source == original
        else:
            await submit(dialog)
            items = journal.ledger.categories if category else journal.ledger.accounts
            assert all(item.id != identifier for item in items)
            assert any(p.read_text() == original for p in (tmp_path / "backups").iterdir())
        page.pop_dialog.assert_called_once()
        assert view.error_message is None

    asyncio.run(scenario())


@pytest.mark.parametrize("category", [False, True])
def test_creation_dialog_saves_and_clears_only_submitted_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: bool
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view.journal = journal
        view._refresh()
        assert not view.account_save.disabled and not view.category_save.disabled
        dialog = open_creation(view, page, category)
        assert isinstance(dialog.content, ft.Column)
        content = dialog.content
        date_fields = [
            control for control in content.controls if isinstance(control, DateTimeFields)
        ]
        assert len(date_fields) == (1 if category else 2)
        chosen_date = date(2026, 1, 10)
        if category:
            date_fields[0].occurred.value = date_input(chosen_date, view.language)
        else:
            date_fields[0].occurred.value = date_input(EPOCH, view.language)
            date_fields[1].occurred.value = date_input(chosen_date, view.language)
        if category:
            view.account_name.value = "Other account draft"
        else:
            view.category_name.value = "Other category draft"
        view._refresh()
        view._apply_layout(390)
        await submit(dialog)
        assert view.error_message is None
        page.pop_dialog.assert_called_once()
        if category:
            assert any(c.id == "Income:Work:Salary" for c in journal.ledger.categories)
            assert (
                next(c for c in journal.ledger.categories if c.id == "Income:Work:Salary").opened
                == chosen_date
            )
            assert view.category_id.value == view.category_name.value == ""
            assert view.account_name.value == "Other account draft"
        else:
            assert journal.ledger.accounts[0].id == "Liabilities:Credit"
            assert journal.ledger.accounts[0].opened == EPOCH
            assert journal.ledger.transactions[0].date == chosen_date
            assert journal.ledger.balances()["Liabilities:Credit"] == -200
            assert view.account_id.value == view.account_name.value == view.opening.value == ""
            assert view.category_name.value == "Other category draft"
        assert (tmp_path / FILENAME).read_text() == journal.source

    asyncio.run(scenario())


def test_account_suggestion_tracks_attributes_and_respects_manual_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, _ = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Monobank:UAH", "Монобанк гривнева", "UAH")
        view.journal = journal
        view._create_account(MagicMock())
        view.account_name.value = "Монобанк, доларова картка"
        view.currency.value = "USD"
        view._account_attributes_changed(MagicMock())
        assert view.account_id.value == "Assets:Monobank:USD"
        assert view.account_suggestion_status.value == view.tr("code_suggestion_status")

        view.account_root.value = "Liabilities"
        view._account_attributes_changed(MagicMock())
        assert view.account_id.value == "Liabilities:Loans:Monobank:USD"
        view.account_root.value = "Assets"
        view._account_attributes_changed(MagicMock())

        view.account_id.value = "Assets:Cash:USD"
        view._account_code_changed(MagicMock())
        view.account_name.value = "ПриватБанк картка"
        view.currency.value = "EUR"
        view._account_attributes_changed(MagicMock())
        assert view.account_id.value == "Assets:Cash:USD"
        assert view.account_suggestion_status.value == view.tr("code_suggestion_manual")

        view._regenerate_account_suggestion(MagicMock())
        assert view.account_id.value == "Assets:Bank:Privatbank:EUR"
        assert not view.account_code_manual

    asyncio.run(scenario())


def test_category_suggestion_tracks_parent_and_manual_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, _ = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_category("Food", "Food")
        await journal.create_category("Transport", "Transport")
        view.journal = journal
        view._create_category(MagicMock())
        view.category_parent.value = "Expenses:Food"
        view.category_name.value = "Кафе"
        view._category_attributes_changed(MagicMock())
        assert view.category_id.value == "Expenses:Food:Cafe"

        view.category_root.value = "Income"
        view._category_type_changed(MagicMock())
        assert view.category_id.value == "Income:Cafe"
        view.category_root.value = "Expenses"
        view._category_type_changed(MagicMock())
        view.category_parent.value = "Expenses:Food"
        view._category_attributes_changed(MagicMock())

        view.category_id.value = "Expenses:Food:Dining"
        view._category_code_changed(MagicMock())
        view.category_parent.value = "Expenses:Transport"
        view._category_attributes_changed(MagicMock())
        assert view.category_id.value == "Expenses:Food:Dining"
        assert view.category_suggestion_status.value == view.tr("code_suggestion_invalid")

        view._regenerate_category_suggestion(MagicMock())
        assert view.category_id.value == "Expenses:Transport:Cafe"
        assert not view.category_code_manual

    asyncio.run(scenario())


@pytest.mark.parametrize("category", [False, True])
@pytest.mark.parametrize("failure", ["storage", "validation", "source", "journal"])
def test_creation_failure_keeps_draft_and_does_not_close_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: bool, failure: str
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view.journal = journal
        dialog = open_creation(view, page, category)
        if failure == "storage":
            monkeypatch.setattr(
                journal,
                "create_category" if category else "create_account",
                AsyncMock(side_effect=OSError("synthetic write failure")),
            )
        elif failure == "validation":
            (view.category_id if category else view.account_id).value = "invalid path!"
        elif failure == "source":
            await journal.create_category("External", "External")
        else:
            view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
            await view.journal.load()
        original = journal.source
        await submit(dialog)
        assert view.error_message or view.message_key == "error_unexpected"
        assert page.show_dialog.call_count == 2
        assert journal.source == original
        page.pop_dialog.assert_not_called()
        assert (view.category_name if category else view.account_name).value == (
            "Salary" if category else "Credit card"
        )
        assert not dialog.actions[-1].disabled
        assert isinstance(dialog.content, ft.Column)
        assert all(not control.disabled for control in dialog.content.controls)

    asyncio.run(scenario())


@pytest.mark.parametrize("category", [False, True])
def test_cancel_preserves_creation_draft_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: bool
) -> None:
    async def scenario() -> None:
        view, page = setup_view(monkeypatch)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view.journal = journal
        original = journal.source
        dialog = open_creation(view, page, category)
        view._dismiss(MagicMock())
        page.pop_dialog.assert_called_once()
        assert journal.source == original
        assert not (tmp_path / FILENAME).exists()
        (view._create_category if category else view._create_account)(MagicMock())
        assert (view.category_name if category else view.account_name).value == (
            "Salary" if category else "Credit card"
        )
        assert page.show_dialog.call_args.args[0] is not dialog

    asyncio.run(scenario())
