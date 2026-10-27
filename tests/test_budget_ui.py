import asyncio
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import flet as ft
import pytest

from app.domain.budgets import BudgetFilter, BudgetRow
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input
from app.ui.datetime_fields import DateTimeFields
from app.ui.finance import FinanceView
from app.ui.tree_picker import TreePicker

SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Expenses:Food
    name: "Groceries"
2000-01-01 open Expenses:Food:Meals
2026-01-01 custom "budget" Expenses:Food "monthly" 10.00 USD
2026-01-05 * "Synthetic shop" "Lunch"
    Expenses:Food 12.00 USD
    Assets:Cash -12.00 USD
"""


def walk(control: ft.Control) -> Iterator[ft.Control]:
    yield control
    if isinstance(control, (ft.Column, ft.Row)):
        for child in control.controls:
            yield from walk(child)
    if isinstance(control, ft.Container) and isinstance(control.content, ft.Control):
        yield from walk(control.content)
    if isinstance(control, ft.ExpansionTile):
        if isinstance(control.title, ft.Control):
            yield from walk(control.title)
        for child in control.controls or []:
            yield from walk(child)


@pytest.fixture
def view(monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.startup_complete = True
    return view, page


async def load(view: FinanceView, tmp_path: Path, source: str = SOURCE) -> Journal:
    (tmp_path / FILENAME).write_text(source)
    journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2026, 12, 31))
    await journal.load()
    view.journal = journal
    view._refresh()
    view.budgets.month = date(2026, 1, 1)
    await view.budgets.refresh()
    return journal


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_budget_navigation_responsive_and_localized(
    view: tuple[FinanceView, MagicMock], language: str
) -> None:
    host, page = view
    host.language = language
    host._navigate("accounts")
    host.accounting_tabs.selected_index = 2
    host._accounting_tab_changed(MagicMock(control=host.accounting_tabs))
    assert host.budgets_panel.visible
    assert not host.accounts_panel.visible and not host.categories_panel.visible
    assert host.budgets_panel.content is host.budgets.management
    assert host.budgets.add in list(walk(host.budgets.management))
    assert host.budgets.results not in list(walk(host.budgets.management))
    host._navigate("reports")
    assert host.reports_screen.controls == [host.report_tabs]
    assert host.report_tabs.length == 2
    assert host.report_overview.visible and not host.budgets.visible
    host.report_tabs.selected_index = 1
    host._report_tab_changed(MagicMock(control=host.report_tabs))
    assert host.current_screen == "reports"
    assert host.screen_host.content is host.reports_screen
    assert host.budgets.visible and not host.report_overview.visible
    assert host.budgets.add not in list(walk(host.budgets))
    assert host.budgets.definitions not in list(walk(host.budgets))
    for width in (320, 390, 800, 1366):
        page.width = width
        host._apply_layout(width)
        host._refresh_labels()
        assert host.page_title.value == host.tr("nav_reports")
        assert [tab.label for tab in host.report_tab_bar.tabs if isinstance(tab, ft.Tab)] == [
            host.tr("report_overview"),
            host.tr("budget_title"),
        ]
        assert host._selected_index() == 3
        host._open_settings(MagicMock())
        host._back_from_settings(MagicMock())
        assert host.current_screen == "reports"
        assert host.report_tabs.selected_index == 1 and host.budgets.visible
        assert host.screen_host.padding == ft.Padding.only(bottom=96)
        host._navigate("accounts")
        assert host.accounting_tabs.selected_index == 2 and host.budgets_panel.visible
        assert host.accounts_panel.width == host.budgets_panel.width
        assert host._selected_index() == 2
        host._open_settings(MagicMock())
        host._back_from_settings(MagicMock())
        assert host.current_screen == "accounts" and host.budgets_panel.visible
        host._navigate("reports")
        assert host.budgets.add.content == host.tr("create_budget")
        assert host.budgets.add.icon == ft.Icons.ADD
        assert host.budgets.filters_tile.title == host.tr("budget_simple_filters")
    host.report_tabs.selected_index = 0
    host._report_tab_changed(MagicMock(control=host.report_tabs))
    assert host.current_screen == "reports"
    assert host.report_overview.visible and not host.budgets.visible
    host._go_accounts(MagicMock())
    assert host.accounts_panel.visible and not host.budgets_panel.visible
    host.will_unmount()


@pytest.mark.parametrize(
    ("language", "label"),
    [("en", "Category"), ("uk", "Категорія"), ("ru", "Категория")],
)
def test_budget_category_labels_are_short_and_not_transaction_instructions(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    language: str,
    label: str,
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        await load(host, tmp_path)
        for width in (320, 390, 1366):
            page.width = width
            host._apply_layout(width)
            host._refresh_labels()
            assert str(host.budgets.scope.content).startswith(label + ": ")
            assert host.budgets.category_id == ""
        host.budgets._form()
        category, _, _, _ = fields(page.show_dialog.call_args.args[0])
        assert category.label == label
        assert host.tr("incoming_category") != label

    asyncio.run(scenario())


def test_reports_hide_automatic_parent_totals_and_unbudgeted_categories(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        journal.ledger = replace(
            journal.ledger,
            budgets=(
                *journal.ledger.budgets,
                replace(
                    journal.ledger.budgets[0],
                    id="future",
                    account="Expenses:Food:Meals",
                    date=date(2027, 1, 1),
                ),
            ),
        )
        await host.budgets.refresh()
        displayed = [
            item.data
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ExpansionTile) and isinstance(item.data, BudgetRow)
        ]
        assert {row.account for row in displayed} == {"Expenses:Food"}
        assert all(row.direct_budget for row in displayed)
        assert displayed[0].actual == {"USD": Decimal(12)}
        assert host.tr("budget_missing") not in [
            item.value for item in walk(host.budgets.results) if isinstance(item, ft.Text)
        ]

    asyncio.run(scenario())


def test_report_distinguishes_explicit_zero_budget_from_no_active_budget(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        journal.ledger = replace(
            journal.ledger,
            budgets=(replace(journal.ledger.budgets[0], amount=Decimal(0)),),
        )
        await host.budgets.refresh()
        displayed = [
            item.data.account
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ExpansionTile) and isinstance(item.data, BudgetRow)
        ]
        assert displayed == ["Expenses:Food"]
        host.budgets.month = date(2025, 12, 1)
        await host.budgets.refresh()
        assert len(host.budgets.results.controls) == 1
        empty = host.budgets.results.controls[0]
        assert isinstance(empty, ft.Text) and empty.value == host.tr("budget_report_empty")
        assert host.budgets.definitions.controls

    asyncio.run(scenario())


def test_explicit_parent_budget_remains_visible(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        journal.ledger = replace(
            journal.ledger,
            budgets=(
                *journal.ledger.budgets,
                replace(
                    journal.ledger.budgets[0],
                    id="explicit-parent",
                    account="Expenses",
                    amount=Decimal(31),
                ),
            ),
        )
        await host.budgets.refresh()
        displayed = [
            item.data
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ExpansionTile) and isinstance(item.data, BudgetRow)
        ]
        assert {row.account for row in displayed} == {"Expenses", "Expenses:Food"}
        assert all(row.direct_budget for row in displayed)
        parent = next(row for row in displayed if row.account == "Expenses")
        assert parent.direct_budget == {"USD": Decimal(31)}
        assert parent.actual == {"USD": Decimal(12)}

    asyncio.run(scenario())


def test_deep_budget_stays_visible_without_unbudgeted_ancestors(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        journal.ledger = replace(
            journal.ledger,
            budgets=(replace(journal.ledger.budgets[0], account="Expenses:Food:Meals"),),
        )
        await host.budgets.refresh()
        displayed = [
            item.data.account
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ExpansionTile) and isinstance(item.data, BudgetRow)
        ]
        assert displayed == ["Expenses:Food:Meals"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("interval", "new", "expected"),
    [
        ("monthly", True, date(2026, 10, 1)),
        ("monthly", False, date(2026, 1, 16)),
        ("daily", True, date(2026, 10, 27)),
        ("weekly", True, date(2026, 10, 27)),
        ("quarterly", True, date(2026, 10, 27)),
        ("yearly", True, date(2026, 10, 27)),
    ],
)
def test_budget_form_default_date_preserves_historical_edits(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interval: str,
    new: bool,
    expected: date,
) -> None:
    async def scenario() -> None:
        host, page = view
        monkeypatch.setattr(host, "_today", lambda: date(2026, 10, 27))
        journal = await load(host, tmp_path)
        budget = replace(journal.ledger.budgets[0], interval=interval, date=date(2026, 1, 16))
        journal.ledger = replace(journal.ledger, budgets=(budget,))
        host.budgets._form(budget, new=new)
        effective = fields(page.show_dialog.call_args.args[0])[-1]
        assert effective.values()[0] == expected.isoformat()
        host.budgets._form()
        category, amount, _, effective = fields(page.show_dialog.call_args.args[0])
        assert effective.values()[0] == "2026-10-01"
        category.value = "Expenses:Food"
        amount.value = "25"
        await submit(page.show_dialog.call_args.args[0])
        assert host.error_message is None
        assert journal.ledger.budgets[-1].date == date(2026, 10, 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_budget_help_explains_each_action_in_its_dialog(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        journal = await load(host, tmp_path)
        budget = journal.ledger.budgets[0]
        assert host.budgets.history_help.value == host.tr("budget_history_help")
        if language == "uk":
            assert host.budgets.history_help.value == (
                "Щоб змінити бюджет на майбутнє й залишити минулі місяці без змін, "
                "натисніть «Змінити з дати». "
                "Невикористаний залишок не додається до наступного місяця."
            )
        for selected, new, stop, key in (
            (None, False, False, "budget_new_help"),
            (budget, True, False, "budget_new_help"),
            (budget, True, True, "budget_stop_help"),
            (budget, False, False, "budget_edit_help"),
        ):
            host.budgets._form(selected, new=new, stop=stop)
            dialog = page.show_dialog.call_args.args[0]
            assert isinstance(dialog.content, ft.Column)
            help_text = dialog.content.controls[0]
            assert isinstance(help_text, ft.Text) and help_text.value == host.tr(key)
            assert help_text.value != host.budgets.history_help.value
            if stop:
                assert fields(dialog)[1].value == "0"
        assert journal.ledger.budgets == (budget,)
        assert (tmp_path / FILENAME).read_text() == SOURCE

    asyncio.run(scenario())


@pytest.mark.parametrize("historical", [False, True])
def test_interval_changes_adjust_only_untouched_new_budget_dates(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    historical: bool,
) -> None:
    async def scenario() -> None:
        host, page = view
        monkeypatch.setattr(host, "_today", lambda: date(2026, 10, 27))
        journal = await load(host, tmp_path)
        host.budgets._form(journal.ledger.budgets[0] if historical else None)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        interval = dialog.content.controls[4]
        assert isinstance(interval, ft.Dropdown) and interval.on_select is not None
        effective = fields(dialog)[-1]
        for value, expected in [("daily", "2026-10-27"), ("monthly", "2026-10-01")]:
            interval.value = value
            cast(Callable[[object], None], interval.on_select)(MagicMock(control=interval))
            assert effective.values()[0] == ("2026-01-01" if historical else expected)
        effective.occurred.value = date_input(date(2026, 11, 12), host.language)
        for value in ("daily", "monthly"):
            interval.value = value
            cast(Callable[[object], None], interval.on_select)(MagicMock(control=interval))
            assert effective.values()[0] == "2026-11-12"

    asyncio.run(scenario())


def test_budget_crud_controls_match_icon_pattern_in_management_and_reports(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        await load(host, tmp_path)
        assert host.budgets.add.content == host.tr("create_budget")
        assert host.budgets.add.icon == ft.Icons.ADD
        definition = host.budgets.definitions.controls[0]
        icons = [item for item in walk(definition) if isinstance(item, ft.IconButton)]
        icon_tooltips = {item.tooltip for item in icons if isinstance(item.tooltip, str)}
        assert icon_tooltips >= {host.tr("crud_edit"), host.tr("crud_delete")}
        assert host.tr("crud_edit") not in [
            item.content for item in walk(definition) if isinstance(item, ft.TextButton)
        ]
        assert host.tr("crud_delete") not in [
            item.content for item in walk(definition) if isinstance(item, ft.TextButton)
        ]
        rows = [
            item
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ExpansionTile)
            and isinstance(item.data, BudgetRow)
            and item.data.account in {"Expenses", "Expenses:Food"}
        ]
        assert len(rows) == 1
        by_account = {item.data.account: item for item in rows if isinstance(item.data, BudgetRow)}
        report_icons = [
            item
            for item in walk(by_account["Expenses:Food"])
            if isinstance(item, ft.IconButton)
            and item.tooltip in {host.tr("crud_edit"), host.tr("crud_delete")}
        ]
        report_tooltips = {item.tooltip for item in report_icons if isinstance(item.tooltip, str)}
        assert report_tooltips == {
            host.tr("crud_edit"),
            host.tr("crud_delete"),
        }
        assert "Expenses" not in by_account

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("language", "display_name"),
    [("en", "Groceries"), ("uk", "Продукти"), ("ru", "Продукты")],
)
def test_budget_category_names_are_used_in_management_reports_and_dialogs(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    language: str,
    display_name: str,
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        journal = await load(host, tmp_path)
        if display_name != "Groceries":
            (tmp_path / FILENAME).write_text(SOURCE.replace('"Groceries"', f'"{display_name}"'))
            await journal.poll()
            await host.budgets.refresh()
        budget = journal.ledger.budgets[0]
        assert journal.ledger.categories[0].name == display_name
        definition = host.budgets.definitions.controls[0]
        heading = next(item for item in walk(definition) if isinstance(item, ft.Text))
        assert str(heading.value).startswith(f"{display_name} · ")
        assert heading.tooltip == budget.account
        food = next(
            item
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ExpansionTile)
            and isinstance(item.data, BudgetRow)
            and item.data.account == budget.account
        )
        assert food.subtitle is None
        label = next(item for item in walk(food) if isinstance(item, ft.Text))
        assert label.value == display_name
        assert label.tooltip == budget.account
        transaction_button = next(item for item in walk(food) if isinstance(item, ft.TextButton))
        host.budgets._transactions(MagicMock(control=transaction_button))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        texts = [item for item in walk(dialog.content) if isinstance(item, ft.Text)]
        assert texts[0].value == display_name and texts[0].tooltip == budget.account
        posting = next(
            item for item in texts if item.tooltip == budget.account and item is not texts[0]
        )
        assert str(posting.value).startswith(f"{display_name}  ")
        host.budgets._delete(budget)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        confirmation = dialog.content.controls[0]
        assert isinstance(confirmation, ft.Text)
        assert confirmation.value == display_name and confirmation.tooltip == budget.account
        assert journal.ledger.budgets == (budget,)
        assert budget.account == "Expenses:Food"

    asyncio.run(scenario())


def test_budget_category_without_display_name_keeps_account_code(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        (tmp_path / FILENAME).write_text(SOURCE.replace('    name: "Groceries"\n', ""))
        await journal.poll()
        await host.budgets.refresh()
        definition = host.budgets.definitions.controls[0]
        heading = next(item for item in walk(definition) if isinstance(item, ft.Text))
        assert str(heading.value).startswith("Expenses:Food · ")
        assert heading.tooltip == "Expenses:Food"

    asyncio.run(scenario())


def test_overview_displays_reference_amounts_overspend_and_transactions(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(host, tmp_path)
        controls = list(walk(host.budgets.results))
        food = next(
            item
            for item in controls
            if isinstance(item, ft.ExpansionTile)
            and isinstance(item.data, BudgetRow)
            and item.data.account == "Expenses:Food"
        )
        assert isinstance(food, ft.ExpansionTile)
        assert isinstance(food.data, BudgetRow)
        assert food.data.budget == {"USD": Decimal("9.999999999999999999999999992")}
        text = [str(item.value) for item in walk(food) if isinstance(item, ft.Text)]
        assert "Groceries" in text
        assert "Budget: 10.00 USD" in text
        assert "Spent: 12.00 USD" in text
        assert "Overspent: 2.00 USD" in text
        bars = [item for item in walk(food) if isinstance(item, ft.ProgressBar)]
        assert bars and bars[0].value == 1 and bars[0].color == ft.Colors.ERROR
        button = next(item for item in walk(food) if isinstance(item, ft.TextButton))
        host.budgets._transactions(MagicMock(control=button))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog) and dialog.scrollable
        assert isinstance(dialog.content, ft.Column)
        assert any(
            isinstance(item, ft.ExpansionTile) and "Synthetic shop" in str(item.title)
            for item in walk(dialog.content)
        )

    asyncio.run(scenario())


def test_month_switch_and_month_picker_preserve_invalid_draft(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(host, tmp_path)
        await host.budgets._next(MagicMock())
        assert host.budgets.month == date(2026, 2, 1)
        await host.budgets._previous(MagicMock())
        assert host.budgets.month == date(2026, 1, 1)
        host.budgets._pick_month(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        month, year, error = dialog.content.controls
        assert isinstance(month, ft.Dropdown)
        assert isinstance(year, ft.TextField) and isinstance(error, ft.Text)
        year.value = "unfinished"
        await submit(dialog)
        assert error.visible and year.value == "unfinished"
        assert host.budgets.month == date(2026, 1, 1)
        for value in ("0", "9999", "-1"):
            year.value = value
            await submit(dialog)
            assert error.visible and host.budgets.month == date(2026, 1, 1)
        year.value = "2024"
        month.value = "2"
        await submit(dialog)
        assert host.budgets.month == date(2024, 2, 1)
        assert host.budgets.report_rows == ()
        await host.budgets._previous(MagicMock())
        assert host.budgets.month == date(2024, 1, 1)
        await host.budgets._previous(MagicMock())
        assert host.budgets.month == date(2023, 12, 1)
        await host.budgets._current_month(MagicMock())
        assert host.budgets.month == host._today().replace(day=1)

    asyncio.run(scenario())


def fields(
    dialog: ft.AlertDialog,
) -> tuple[ft.Dropdown, ft.TextField, ft.TextField, DateTimeFields]:
    assert isinstance(dialog.content, ft.Column)
    values = dialog.content.controls
    assert isinstance(values[1], ft.Dropdown)
    assert isinstance(values[2], ft.TextField)
    assert isinstance(values[3], ft.TextField)
    assert isinstance(values[-1], DateTimeFields)
    return values[1], values[2], values[3], values[-1]


async def submit(dialog: ft.AlertDialog) -> None:
    button = dialog.actions[-1]
    assert isinstance(button, ft.TextButton) and button.on_click
    await cast(Callable[[object], Awaitable[None]], button.on_click)(MagicMock())


def test_future_definition_and_discontinuation_preserve_history(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        original = journal.ledger.budgets[0]
        host.budgets._form(original, new=True)
        dialog = page.show_dialog.call_args.args[0]
        _, amount, _, effective = fields(dialog)
        amount.value = "50"
        effective.occurred.value = date_input(date(2027, 1, 1), host.language)
        await submit(dialog)
        assert host.error_message is None
        assert len(journal.ledger.budgets) == 2
        assert journal.ledger.budgets[0] == original
        assert journal.ledger.budgets[1].date == date(2027, 1, 1)
        host.budgets._form(journal.ledger.budgets[1], new=True, stop=True)
        dialog = page.show_dialog.call_args.args[0]
        _, amount, _, effective = fields(dialog)
        assert amount.value == "0"
        effective.occurred.value = date_input(date(2027, 2, 1), host.language)
        await submit(dialog)
        assert len(journal.ledger.budgets) == 3
        assert journal.ledger.budgets[-1].amount == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("reload_before_save", [False, True])
def test_budget_conflicts_retain_all_unsaved_dialog_fields(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, reload_before_save: bool
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        host.budgets._form(journal.ledger.budgets[0])
        dialog = page.show_dialog.call_args.args[0]
        category, amount, currency, effective = fields(dialog)
        amount.value = "123.456"
        currency.value = "EUR"
        effective.occurred.value = date_input(date(2026, 1, 16), host.language)
        external = journal.source.replace("10.00 USD", "20.00 USD")
        (tmp_path / FILENAME).write_text(external)
        if reload_before_save:
            await journal.poll()
            await host.budgets.refresh()
            assert journal.external_summary["budgets_changed"] == 1
        await submit(dialog)
        assert host.error_message is not None
        assert amount.value == "123.456" and currency.value == "EUR"
        assert category.value == "Expenses:Food"
        assert effective.occurred.value == date_input(date(2026, 1, 16), host.language)
        assert not amount.disabled
        page.pop_dialog.assert_not_called()
        assert (tmp_path / FILENAME).read_text() == external

    asyncio.run(scenario())


def test_empty_state_refresh_and_worker_cleanup(view: tuple[FinanceView, MagicMock]) -> None:
    host, page = view
    asyncio.run(host.budgets.refresh())
    assert host.budgets.add.disabled
    assert isinstance(host.budgets.results.controls[0], ft.Text)
    assert host.budgets.results.controls[0].value == host.tr("report_unavailable")
    worker = MagicMock()
    host.budgets.worker = worker
    host.will_unmount()
    worker.cancel.assert_called_once()
    page.update.assert_called()


def test_polling_refresh_keeps_budget_input_controls_and_parents_stable(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(host, tmp_path)
        controls = tuple(host.budgets.controls)
        options = tuple(host.budgets.currency.options)
        definitions = tuple(host.budgets.definitions.controls)
        host.budgets._pick_month(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        year = dialog.content.controls[1]
        assert isinstance(year, ft.TextField)
        year.value = "unfinished year"
        host.budgets.filters_tile.expanded = True
        for _ in range(3):
            host._refresh()
            assert tuple(host.budgets.controls) == controls
            assert all(a is b for a, b in zip(host.budgets.controls, controls, strict=True))
            assert tuple(host.budgets.currency.options) == options
            assert tuple(host.budgets.definitions.controls) == definitions
            assert host.budgets.filters_tile.expanded
            assert year.value == "unfinished year"
        assert host.budgets.month == date(2026, 1, 1)

    asyncio.run(scenario())


def displayed_rows(host: FinanceView) -> list[BudgetRow]:
    return [
        control.data
        for control in walk(host.budgets.results)
        if isinstance(control, ft.ExpansionTile) and isinstance(control.data, BudgetRow)
    ]


MULTI_CURRENCY = (
    SOURCE
    + """
2000-01-01 open Assets:Euro EUR
2026-01-01 custom "budget" Expenses:Food "monthly" 10.00 EUR
2026-01-01 price USD 2 EUR
2026-01-08 * "Other shop" "Dinner"
    Expenses:Food 4.00 EUR
    Assets:Euro -4.00 EUR
"""
)


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_monthly_report_has_only_simple_filters(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        await load(host, tmp_path)
        assert not host.budgets.currency.visible
        assert not host.budgets.active_filters.visible
        assert not host.budgets.account_warning.visible
        assert not host.budgets.reset.visible
        assert not any(isinstance(item, ft.TextField) for item in walk(host.budgets))
        assert [item for item in walk(host.budgets) if isinstance(item, ft.Dropdown)] == [
            host.budgets.currency
        ]
        assert not any(
            hasattr(host.budgets, field)
            for field in ("mode", "interval", "conversion", "tag", "text")
        )
        host.budgets._form()
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        interval = next(
            item
            for item in walk(dialog.content)
            if isinstance(item, ft.Dropdown) and item.value == "monthly"
        )
        assert {option.key for option in interval.options} == {
            "daily",
            "weekly",
            "monthly",
            "quarterly",
            "yearly",
        }

    asyncio.run(scenario())


def test_tree_pickers_have_branches_friendly_names_search_and_clear(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(
            host,
            tmp_path,
            SOURCE
            + """
2000-01-01 open Expenses:FoodOther
    name: "Other groceries"
2026-01-01 custom "budget" Expenses:Food:Meals "monthly" 2 USD
2026-01-01 custom "budget" Expenses:FoodOther "monthly" 3 USD
""",
        )
        host.budgets._pick_category(MagicMock())
        picker = page.show_dialog.call_args.args[0]
        assert isinstance(picker, TreePicker)
        assert picker.items["Expenses:Food"] == "Groceries"
        assert "Expenses:Food:Meals" in picker.items
        branches = {
            item.data: item for item in walk(picker.listing) if isinstance(item, ft.ExpansionTile)
        }
        assert set(branches) == {"Expenses", "Expenses:Food"}
        branch = branches["Expenses:Food"]
        assert branch.on_change
        cast(Callable[[object], None], branch.on_change)(MagicMock(data=True))
        picker.search.value = "groceries"
        picker._search(MagicMock())
        assert picker.search.value == "groceries"
        assert any(
            isinstance(item, ft.TextButton) and item.data == "Expenses:Food:Meals"
            for item in walk(picker.listing)
        )
        picker.search.value = "No such category"
        picker._search(MagicMock())
        assert isinstance(picker.listing.controls[0], ft.Text)
        picker.search.value = ""
        picker._search(MagicMock())
        branch = next(
            item
            for item in walk(picker.listing)
            if isinstance(item, ft.ExpansionTile) and item.data == "Expenses:Food"
        )
        assert branch.expanded
        button = next(
            item
            for item in walk(picker.listing)
            if isinstance(item, ft.TextButton) and item.data == "Expenses:Food"
        )
        await picker._choose(MagicMock(control=button))
        assert host.budgets.category_id == "Expenses:Food"
        assert {row.account for row in displayed_rows(host)} == {
            "Expenses:Food",
            "Expenses:Food:Meals",
        }
        assert "Expenses:FoodOther" not in {row.account for row in displayed_rows(host)}
        chip = next(
            item for item in host.budgets.active_filters.controls if item.data == "category"
        )
        assert isinstance(chip, ft.Chip) and chip.on_click == chip.on_delete
        assert isinstance(chip.label, ft.Text) and chip.label.overflow == ft.TextOverflow.ELLIPSIS
        await host.budgets._clear_filter(MagicMock(control=chip))
        assert not host.budgets.category_id
        assert "Expenses:FoodOther" in {row.account for row in displayed_rows(host)}
        host.budgets._pick_account(MagicMock())
        account_picker = page.show_dialog.call_args.args[0]
        assert isinstance(account_picker, TreePicker)
        assert set(account_picker.items) == {"Assets", "Assets:Cash"}
        assert not any(key.startswith(("Income", "Expenses")) for key in account_picker.items)
        await account_picker._choose(MagicMock(control=ft.TextButton(data="")))
        assert not host.budgets.account_id

    asyncio.run(scenario())


def test_account_tree_filter_keeps_budget_and_warns_outside_collapsed_filters(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(
            host,
            tmp_path,
            SOURCE
            + """
2000-01-01 open Assets:Cash:Wallet USD
2000-01-01 open Assets:Cashbox USD
2026-01-06 * "Wallet purchase" ""
    Expenses:Food 2 USD
    Assets:Cash:Wallet -2 USD
2026-01-07 * "Different account" ""
    Expenses:Food 50 USD
    Assets:Cashbox -50 USD
""",
        )
        unfiltered = displayed_rows(host)[0]
        await host.budgets._select_account("Assets:Cash")
        filtered = displayed_rows(host)[0]
        assert unfiltered.actual == {"USD": Decimal(64)}
        assert filtered.actual == {"USD": Decimal(14)}
        assert filtered.budget == unfiltered.budget
        assert not host.budgets.filters_tile.expanded
        assert host.budgets.account_warning.visible
        assert host.budgets.account_warning in host.budgets.controls
        assert any(item.data == "account" for item in host.budgets.active_filters.controls)
        sibling = journal.ledger.transactions[-1]
        assert BudgetFilter("Assets:Cash").matches(sibling)
        assert not BudgetFilter("Assets:Cash", account_tree=True).matches(sibling)
        assert BudgetFilter("Assets:Cash", account_tree=True).matches(
            journal.ledger.transactions[-2]
        )
        host.budgets.overspent.value = True
        await host.budgets._reset(MagicMock())
        assert not host.budgets.account_id and not host.budgets.overspent.value
        assert not host.budgets.account_warning.visible
        assert not host.budgets.active_filters.visible
        assert displayed_rows(host)[0].actual == unfiltered.actual

    asyncio.run(scenario())


@pytest.mark.parametrize("currency", ["USD", "EUR", ""])
def test_currency_filter_never_converts_or_changes_calculations(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, currency: str
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path, MULTI_CURRENCY)
        before = (tmp_path / FILENAME).read_text()
        assert host.budgets.currency.visible
        assert {option.key for option in host.budgets.currency.options} == {"", "EUR", "USD"}
        original = displayed_rows(host)[0]
        host.budgets.currency.value = currency
        await host.budgets._changed(MagicMock())
        row = displayed_rows(host)[0]
        assert row == original
        assert row.actual == {"USD": Decimal(12), "EUR": Decimal(4)}
        assert row.direct_budget.keys() == {"EUR", "USD"}
        figures = [
            item.value
            for item in walk(host.budgets.results)
            if isinstance(item, ft.Text) and item.value
        ]
        assert any("USD" in text for text in figures) == (currency in {"", "USD"})
        assert any("EUR" in text for text in figures) == (currency in {"", "EUR"})
        active = host.budgets._active_budget("Expenses:Food", date(2026, 2, 1))
        assert active and active.currency == (currency or "EUR")
        assert journal.ledger.prices
        assert (tmp_path / FILENAME).read_text() == before
        assert not (tmp_path / "backups").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(("currency", "visible"), [("", True), ("USD", True), ("EUR", False)])
def test_only_overspent_respects_display_currency(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, currency: str, visible: bool
) -> None:
    async def scenario() -> None:
        host, _ = view
        await load(host, tmp_path, MULTI_CURRENCY)
        host.budgets.currency.value = currency
        host.budgets.overspent.value = True
        await host.budgets._changed(MagicMock())
        assert bool(displayed_rows(host)) == visible
        await host.budgets._reset(MagicMock())
        assert host.budgets.month == date(2026, 1, 1)
        assert host.budgets.currency.value == "" and not host.budgets.overspent.value
        assert displayed_rows(host)
        assert not host.budgets.reset.visible

    asyncio.run(scenario())


@pytest.mark.parametrize("spending", [Decimal(0), Decimal(12)])
def test_zero_budget_overspending_and_missing_currency(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, spending: Decimal
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        transaction = journal.ledger.transactions[0]
        journal.ledger = replace(
            journal.ledger,
            budgets=(replace(journal.ledger.budgets[0], amount=Decimal(0)),),
            transactions=(
                replace(
                    transaction,
                    postings=tuple(
                        replace(posting, amount=spending if posting.amount > 0 else -spending)
                        for posting in transaction.postings
                    ),
                ),
            ),
        )
        host.budgets.overspent.value = True
        await host.budgets.refresh()
        assert bool(displayed_rows(host)) == (spending > 0)
        host.budgets.currency.value = "EUR"
        await host.budgets.refresh()
        assert not displayed_rows(host)
        assert host.budgets.currency.visible
        assert host.budgets.reset.visible

    asyncio.run(scenario())


def test_transaction_search_does_not_change_headline_totals_or_journal(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(host, tmp_path, MULTI_CURRENCY)
        before = (tmp_path / FILENAME).read_text()
        original = displayed_rows(host)[0]
        host.budgets._transactions(MagicMock(control=ft.TextButton(data=original)))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        search = next(item for item in walk(dialog.content) if isinstance(item, ft.TextField))
        assert search.on_change
        search.value = "DINNER"
        cast(Callable[[object], None], search.on_change)(MagicMock())
        listing = [item for item in walk(dialog.content) if isinstance(item, ft.ExpansionTile)]
        assert len(listing) == 1 and "Other shop" in str(listing[0].title)
        for _ in range(3):
            host._refresh()
            assert search.value == "DINNER"
        search.value = "no match"
        cast(Callable[[object], None], search.on_change)(MagicMock())
        assert not any(isinstance(item, ft.ExpansionTile) for item in walk(dialog.content))
        assert displayed_rows(host)[0] == original
        assert (tmp_path / FILENAME).read_text() == before

    asyncio.run(scenario())


def test_month_boundaries_and_leap_day(view: tuple[FinanceView, MagicMock], tmp_path: Path) -> None:
    async def scenario() -> None:
        host, _ = view
        await load(
            host,
            tmp_path,
            SOURCE.replace("2026-01-01", "2024-02-01").replace("2026-01-05", "2024-02-29"),
        )
        host.budgets.month = date(2024, 2, 1)
        await host.budgets.refresh()
        row = displayed_rows(host)[0]
        assert (row.begin, row.end) == (date(2024, 2, 1), date(2024, 3, 1))
        assert row.actual == {"USD": Decimal(12)}
        host.budgets.month = date(1, 1, 1)
        await host.budgets.refresh()
        assert host.budgets.previous.disabled
        await host.budgets._previous(MagicMock())
        assert host.budgets.error.visible and host.budgets.month == date(1, 1, 1)
        host.budgets.month = date(9998, 12, 1)
        await host.budgets.refresh()
        assert host.budgets.next.disabled
        await host.budgets._next(MagicMock())
        assert host.budgets.error.visible and host.budgets.month == date(9998, 12, 1)

    asyncio.run(scenario())


def test_long_filter_names_stay_bounded_and_chips_survive_polling(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        name = "Very long friendly category name " * 20
        journal.ledger = replace(
            journal.ledger,
            categories=tuple(
                replace(item, name=name) if item.id == "Expenses:Food" else item
                for item in journal.ledger.categories
            ),
        )
        page.width = 320
        host._apply_layout(page.width)
        await host.budgets._select_category("Expenses:Food")
        chip = host.budgets.active_filters.controls[0]
        assert isinstance(chip, ft.Chip) and isinstance(chip.label, ft.Text)
        assert chip.label.width == 192 and chip.tooltip == f"Category: {name}"
        assert chip.on_click and chip.on_delete
        for _ in range(3):
            host._refresh()
            assert host.budgets.active_filters.controls[0] is chip
        page.width = 1366
        host._apply_layout(page.width)
        host._refresh_labels()
        wider = host.budgets.active_filters.controls[0]
        assert isinstance(wider, ft.Chip) and isinstance(wider.label, ft.Text)
        assert wider.label.width == 240

    asyncio.run(scenario())


def test_budget_controls_initialize_before_a_page_is_attached() -> None:
    host = FinanceView()
    assert host.budgets.category_id == ""
    assert not host.budgets.active_filters.visible
