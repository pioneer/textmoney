import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import flet.canvas as fc
import pytest
from form_controls import (
    assert_deletion_title,
    category_content,
    click,
    click_async,
    dropdown_field,
)

from app.domain.budgets import Budget, BudgetRow
from app.domain.ledger import LedgerError
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input, format_date
from app.shared_defaults import SharedDefaultsError
from app.ui.datetime_fields import DateTimeFields
from app.ui.finance import FinanceView
from app.ui.layout import HoverRow

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
    if isinstance(control, ft.Tabs):
        yield from walk(control.content)
    if isinstance(control, (ft.Column, ft.Row, ft.ListView)):
        for child in control.controls:
            yield from walk(child)
    if isinstance(control, (ft.Container, fc.Canvas)) and isinstance(control.content, ft.Control):
        yield from walk(control.content)
    if isinstance(control, ft.ListTile) and isinstance(control.title, ft.Control):
        yield from walk(control.title)
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
    (tmp_path / FILENAME).write_text(source, encoding="utf-8", newline="")
    journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2026, 12, 31))
    await journal.load()
    view.journal = journal
    view._refresh()
    view.budgets.month = date(2026, 1, 1)
    await view.budgets.refresh()
    return journal


@pytest.mark.parametrize("width", [390, 1366])
def test_unchanged_poll_preserves_rendered_controls(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, width: int
) -> None:
    async def scenario() -> None:
        host, page = view
        page.width = width
        journal = await load(host, tmp_path)
        await host._load_shared_defaults()
        host._apply_layout(width)
        host._refresh()
        columns = (
            host.history,
            host.home_balances,
            host.home_activity,
            host.account_list,
            host.category_list,
        )
        previous = [tuple(column.controls) for column in columns]
        ledger = journal.ledger
        for _ in range(3):
            host.next_icon_poll = 0
            await host._poll_journal()
            assert journal.ledger is ledger
            for column, controls in zip(columns, previous, strict=True):
                assert len(column.controls) == len(controls)
                assert all(
                    current is original
                    for current, original in zip(column.controls, controls, strict=True)
                )

    asyncio.run(scenario())


def test_poll_refreshes_current_report_when_local_day_changes(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, _ = view
        today = date(2026, 1, 31)
        monkeypatch.setattr(host, "_today", lambda: today)
        journal = await load(host, tmp_path)
        journal.today = host._today
        await host._load_shared_defaults()
        host.current_screen = "reports"
        host._refresh()
        assert host._report_range == (date(2026, 1, 1), today)
        january = host._report_data
        today = date(2026, 2, 1)
        host.next_icon_poll = float("inf")
        await host._poll_journal()
        assert host._report_range == (today, today)
        assert host._report_data is not january

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True], ids=["updated", "failed"])
def test_budget_refresh_keeps_cards_until_calculation_finishes(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, failure: bool
) -> None:
    async def scenario() -> None:
        host, _ = view
        await load(host, tmp_path)
        report = host.budgets
        original = tuple(report.results.controls)
        started, release = asyncio.Event(), asyncio.Event()

        async def wait_for_order() -> None:
            started.set()
            await release.wait()
            if failure:
                raise LedgerError("budget_month_error")

        report.load_order = AsyncMock(side_effect=wait_for_order)
        worker = asyncio.create_task(report.refresh())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert len(report.results.controls) == len(original)
            assert all(
                current is previous
                for current, previous in zip(report.results.controls, original, strict=True)
            )
            assert not report.loading.visible
        finally:
            release.set()
            await worker
        assert not report.loading.visible
        assert report.error.visible is failure
        if failure:
            assert not report.results.controls
        else:
            assert displayed_rows(host)

    asyncio.run(scenario())


@pytest.mark.parametrize("selection", ["month", "currency"])
def test_budget_selection_change_does_not_show_previous_results(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, selection: str
) -> None:
    async def scenario() -> None:
        host, _ = view
        await load(host, tmp_path)
        report = host.budgets
        assert report.results.controls
        started, release = asyncio.Event(), asyncio.Event()

        async def wait_for_order() -> None:
            started.set()
            await release.wait()

        report.load_order = AsyncMock(side_effect=wait_for_order)
        if selection == "month":
            refresh = report._month(1)
        else:
            report.currency.value = "USD"
            refresh = report._refresh_filters()
        worker = asyncio.create_task(refresh)
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert not report.results.controls
            assert not report.report_rows
            assert report.loading.visible
        finally:
            release.set()
            await worker
        assert not report.loading.visible
        assert not report.error.visible

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 320)])
def test_budget_order_dialog_saves_and_resets_without_changing_filters_or_journal(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str, width: int
) -> None:
    async def scenario() -> None:
        host, page = view
        page.width, host.language = width, language
        source = SOURCE + '2026-01-01 custom "budget" Expenses:Food:Meals "monthly" 5 USD\n'
        source += "2000-01-01 open Expenses:Home\n"
        source += '2026-02-01 custom "budget" Expenses:Home "monthly" 100 USD\n'
        journal = await load(host, tmp_path, source)
        report = host.budgets
        report.category_id = "Expenses:Food"
        report.overspent.value = True
        report.currency.value = "USD"
        report.filters.visible = True
        await report.refresh()
        assert report.order_button.visible
        await click_async(report.order_button)
        dialog = page.show_dialog.call_args.args[0]
        listing = next(
            item for item in walk(dialog.content) if isinstance(item, ft.ReorderableListView)
        )
        assert [item.data for item in listing.controls] == [
            "Expenses:Food",
            "Expenses:Food:Meals",
            "Expenses:Home",
        ]
        assert listing.on_reorder
        cast(Callable[[object], None], listing.on_reorder)(MagicMock(old_index=0, new_index=2))
        assert [item.data for item in listing.controls] == [
            "Expenses:Home",
            "Expenses:Food",
            "Expenses:Food:Meals",
        ]
        await submit(dialog)
        assert host.error_message is None
        expected = ("Expenses:Home", "Expenses:Food", "Expenses:Food:Meals")
        assert report.order == expected
        assert report.month == date(2026, 1, 1)
        assert report.category_id == "Expenses:Food" and report.currency.value == "USD"
        assert report.filters.visible and report.overspent.value
        report.category_id, report.currency.value, report.overspent.value = "", "", False
        report.month = date(2026, 2, 1)
        await report.refresh()
        assert [row.account for row in displayed_rows(host)] == list(expected)
        report.order_store = None
        report.order = ()
        await report.refresh()
        assert report.order == expected
        assert [row.account for row in displayed_rows(host)] == list(expected)
        await click_async(report.order_button)
        dialog = page.show_dialog.call_args.args[0]
        reset = next(
            item
            for item in walk(dialog.content)
            if isinstance(item, ft.TextButton) and item.content == host.tr("budget_order_reset")
        )
        click(reset)
        await submit(dialog)
        assert report.order == ()
        assert [row.account for row in displayed_rows(host)] == [
            "Expenses:Food",
            "Expenses:Food:Meals",
            "Expenses:Home",
        ]
        assert journal.source == source
        assert (tmp_path / FILENAME).read_bytes() == source.encode()
        assert not (tmp_path / "textmoney-settings").exists()

    asyncio.run(scenario())


def test_budget_order_draft_cancel_arrows_and_stale_save(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        source = SOURCE + "2000-01-01 open Expenses:Home\n"
        source += '2026-01-01 custom "budget" Expenses:Home "monthly" 100 USD\n'
        await load(host, tmp_path, source)
        report = host.budgets
        await click_async(report.order_button)
        dialog = page.show_dialog.call_args.args[0]
        listing = next(
            item for item in walk(dialog.content) if isinstance(item, ft.ReorderableListView)
        )
        home = listing.controls[-1]
        up = next(
            item
            for item in walk(home)
            if isinstance(item, ft.IconButton) and item.data == ("Expenses:Home", -1)
        )
        assert up.on_click
        cast(Callable[[object], None], up.on_click)(MagicMock(control=up))
        assert [item.data for item in listing.controls] == ["Expenses:Home", "Expenses:Food"]
        click(dialog.actions[0])
        assert report.order == ()
        assert not (tmp_path / "textmoney-budget-order").exists()
        await click_async(report.order_button)
        dialog = page.show_dialog.call_args.args[0]
        assert report.order_store
        await report.order_store.save("budget_order", ("Expenses:Home", "Expenses:Food"), ())
        await submit(dialog)
        assert host.error_message == "budget_order_stale"
        assert report.order == ()
        assert page.pop_dialog.call_count == 1
        await report.refresh()
        assert report.order == ("Expenses:Home", "Expenses:Food")

    asyncio.run(scenario())


def test_budget_order_conflicts_read_failures_and_folder_switch_preserve_journal(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        source = SOURCE + "2000-01-01 open Expenses:Home\n"
        source += '2026-01-01 custom "budget" Expenses:Home "monthly" 100 USD\n'
        journal = await load(host, tmp_path, source)
        report = host.budgets
        assert report.order_store
        store = report.order_store
        directory = tmp_path / "textmoney-budget-order"
        directory.mkdir()
        for choice in (("Expenses:Home", "Expenses:Food"), ("Expenses:Food", "Expenses:Home")):
            data = json.dumps(
                {
                    "version": 1,
                    "changes": {"budget_order": {"parents": [], "value": choice}},
                }
            ).encode()
            (directory / (sha256(data).hexdigest() + ".json")).write_bytes(data)
        await report.load_order()
        assert report.order_notice.visible and store.conflicts
        await click_async(report.order_button)
        dialog = page.show_dialog.call_args.args[0]
        await submit(dialog)
        assert not store.conflicts and not report.order_notice.visible
        previous = report.order
        (directory / "broken.json").write_text("bad", encoding="utf-8")
        await report.refresh()
        assert journal.ready and report.order == previous
        assert report.order_notice.visible and report.order_error
        await click_async(report.order_button)
        assert host.error_message == "budget_order_unavailable"
        other = tmp_path / "other"
        other.mkdir()
        await load(host, other, source)
        assert report.order == () and not report.order_notice.visible
        assert report.order_journal is host.journal and report.order_journal is not journal
        assert journal.source == source

    asyncio.run(scenario())


def test_budget_order_failed_save_retains_draft_and_requires_reload(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, page = view
        source = SOURCE + "2000-01-01 open Expenses:Home\n"
        source += '2026-01-01 custom "budget" Expenses:Home "monthly" 100 USD\n'
        journal = await load(host, tmp_path, source)
        report = host.budgets
        await click_async(report.order_button)
        dialog = page.show_dialog.call_args.args[0]
        assert report.order_store
        save = AsyncMock(side_effect=SharedDefaultsError("write"))
        monkeypatch.setattr(report.order_store, "save", save)
        await submit(dialog)
        assert report.order_error and report.order_notice.visible
        assert host.error_message == "budget_order_save_failed"
        assert dialog.actions[-1].disabled
        page.pop_dialog.assert_not_called()
        await submit(dialog)
        save.assert_awaited_once()
        assert report.order == () and journal.source == source and journal.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [390, 599, 600, 1366])
@pytest.mark.parametrize("theme", [ft.ThemeMode.LIGHT, ft.ThemeMode.DARK])
def test_editable_rows_highlight_together_with_their_actions_only_on_larger_layouts(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    theme: ft.ThemeMode,
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        page.theme_mode = theme
        page.width = width
        host._apply_layout(width)
        host.budgets.highlighted = (date(2026, 1, 1), "Expenses:Food")
        host.budgets._render(host.budgets.report_rows)
        groups = (
            host.account_list,
            host.category_list,
            host.history,
            host.home_activity,
            host.budgets.results,
            host.budgets.definitions,
        )
        hovered = [
            next(item for item in walk(group) if isinstance(item, HoverRow)) for group in groups
        ]
        monkeypatch.setattr(HoverRow, "update", MagicMock())
        color = ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE) if width > 599 else None
        for row in hovered:
            actions = [item for item in walk(row) if isinstance(item, ft.IconButton)]
            assert any(item.tooltip == host.tr("crud_edit") for item in actions)
            assert row.on_click is None and row.on_hover is not None
            hover = cast(Callable[[object], object], row.on_hover)
            hover(MagicMock(control=row, data=True))
            assert row.bgcolor == color
            assert all(other.bgcolor is None for other in hovered if other is not row)
            hover(MagicMock(control=row, data=False))
            assert row.bgcolor is None
        budget = next(item for item in walk(host.budgets.results) if isinstance(item, HoverRow))
        assert isinstance(budget.content, ft.ListTile)
        assert budget.content.bgcolor == ft.Colors.SECONDARY_CONTAINER
        read_only = host._transaction_card(journal.ledger.transactions[0], {}, read_only=True)
        assert isinstance(read_only, ft.Container) and read_only.on_hover is None
        assert not any(isinstance(item, ft.IconButton) for item in walk(read_only))
        for row in hovered:
            assert row.on_hover is not None
            cast(Callable[[object], object], row.on_hover)(MagicMock(control=row, data=True))
        host._apply_layout(390)
        for row in hovered:
            row.before_update()
            assert row.bgcolor is None
        assert budget.content.bgcolor == ft.Colors.SECONDARY_CONTAINER

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_budget_navigation_responsive_and_localized(
    view: tuple[FinanceView, MagicMock], language: str
) -> None:
    host, page = view
    assert isinstance(host.budgets.period.icon, ft.Icon)
    assert host.budgets.period.icon.icon == ft.Icons.CALENDAR_TODAY
    assert host.budgets.period.icon.size == 16
    host.language = language
    host._navigate("categories")
    assert host.screen_host.content is host.categories_screen
    assert host.budgets not in list(walk(host.accounts_screen))
    host._navigate("reports")
    assert host.report_overview in host.reports_screen.controls
    assert host.budgets not in list(walk(host.reports_screen))
    host._navigate("budgets")
    assert host.current_screen == "budgets"
    assert host.screen_host.content is host.budgets_screen
    assert host.budgets_screen.controls == [host.budgets]
    tree = list(walk(host.budgets_screen))
    assert sum(item is host.budgets for item in tree) == 1
    assert sum(item is host.budgets.add for item in tree) == 1
    assert host.budgets.definitions not in tree
    assert not any(isinstance(item, ft.ExpansionTile) for item in tree)
    toolbar = host.budgets.controls[0]
    assert isinstance(toolbar, ft.Container)
    assert toolbar.alignment == ft.Alignment.CENTER_RIGHT
    toolbar = toolbar.content
    assert isinstance(toolbar, ft.Row) and toolbar.wrap
    assert toolbar.alignment == ft.MainAxisAlignment.END
    assert toolbar.controls == [host.budgets.add, host.budgets.history]
    for width in (320, 390, 800, 1366):
        page.width = width
        host._apply_layout(width)
        host._refresh_labels()
        assert host.budgets.add.width == host.budgets.history.width == 240
        assert host.page_title.value == host.tr("budget_title")
        assert not any(isinstance(item, ft.Tabs) for item in walk(host.budgets_screen))
        assert host.budgets.history.content == host.tr("budget_limit_history")
        assert host._selected_index() == 2
        host._open_settings(MagicMock())
        host._back_from_settings(MagicMock())
        assert host.current_screen == "budgets"
        assert host.screen_host.content is host.budgets_screen
        assert host.screen_host.padding == ft.Padding.only(bottom=96)
        host._navigate("categories")
        assert host.screen_host.content is host.categories_screen
        assert host.accounts_panel.width == host.categories_panel.width
        assert host._selected_index() == (None if width <= 599 else 5)
        host._open_settings(MagicMock())
        host._back_from_settings(MagicMock())
        assert host.current_screen == "categories"
        host._navigate("budgets")
        assert host.budgets.add.content == host.tr("create_budget")
        assert host.budgets.add.icon == ft.Icons.ADD
        assert host.budgets.filter_toggle.tooltip == host.tr("operations_filters")
    host._navigate("reports")
    assert host.current_screen == "reports"
    assert host.screen_host.content is host.reports_screen
    host._go_accounts(MagicMock())
    assert host.screen_host.content is host.accounts_screen
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
            assert host.budgets.scope.label == label
            assert host.budgets.category_id == ""
        host.budgets._form()
        category, _, _, _ = fields(page.show_dialog.call_args.args[0])
        assert category.label == label
        assert host.tr("incoming_category") != label

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
@pytest.mark.parametrize("mode", ["add", "edit", "change", "stop"])
def test_budget_category_dropdown_is_parent_first_and_indented(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    language: str,
    width: int,
    mode: str,
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        host._apply_layout(width)
        source = SOURCE.replace(
            "2000-01-01 open Expenses:Food\n",
            '2000-01-01 open Income:Work:Bonus\n    name: "Bonus"\n'
            '2000-01-01 open Expenses:Food:Meals:Lunch\n    name: "Lunch"\n'
            '2000-01-01 open Expenses:Travel\n    name: "Travel"\n'
            '2000-01-01 open Income:Work\n    name: "Work"\n'
            "2000-01-01 open Expenses:Food\n",
        )
        journal = await load(host, tmp_path, source)
        before = journal.source
        host.budgets._form(
            None if mode == "add" else journal.ledger.budgets[0],
            new=mode in {"change", "stop"},
            stop=mode == "stop",
        )
        dialog = page.show_dialog.call_args.args[0]
        category, amount, _, _ = fields(dialog)
        assert [(option.key, option.text) for option in category.options] == [
            ("Expenses:Food", "Groceries"),
            ("Expenses:Food:Meals", "Meals"),
            ("Expenses:Food:Meals:Lunch", "Lunch"),
            ("Expenses:Travel", "Travel"),
            ("Income:Work", "Work"),
            ("Income:Work:Bonus", "Bonus"),
        ]
        assert category.menu_height == (672 if width <= 599 else None)
        if width <= 599:
            assert category.menu_style and category.menu_style.alignment
            assert category.menu_style.alignment.y < 0
        else:
            assert category.menu_style is None
        for option, depth in zip(category.options, [0, 1, 2, 0, 0, 1], strict=True):
            content = category_content(option)
            assert content.padding == ft.Padding.only(left=16 * depth)
            assert isinstance(content.content, ft.Text)
            assert content.content.value == option.text
            assert not content.content.spans
        assert category.value == (None if mode == "add" else "Expenses:Food")
        assert journal.source == before
        category.value = "Expenses:Food:Meals:Lunch"
        amount.value = "15"
        await submit(dialog)
        assert host.error_message is None
        assert any(
            budget.account == category.value and budget.amount == Decimal("15")
            for budget in journal.ledger.budgets
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
@pytest.mark.parametrize("mode", ["add", "edit", "change", "stop"])
def test_budget_form_dropdowns_fill_the_form(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    language: str,
    width: int,
    mode: str,
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        journal = await load(host, tmp_path)
        host._apply_layout(width)
        before = journal.source
        host.budgets._form(
            None if mode == "add" else journal.ledger.budgets[0],
            new=mode in {"change", "stop"},
            stop=mode == "stop",
        )
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        assert dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        dropdowns = [item for item in walk(dialog.content) if isinstance(item, ft.Dropdown)]
        assert [item.label for item in dropdowns] == [
            host.tr("budget_category"),
            host.tr("budget_interval"),
        ]
        for dropdown in dropdowns:
            row = next(
                control
                for control in dialog.content.controls
                if isinstance(control, ft.Row)
                and [dropdown_field(child) for child in control.controls] == [dropdown]
            )
            assert row.width is None and not row.wrap
            assert dropdown.expand is True and dropdown.width is None
        host._dismiss(MagicMock())
        assert journal.source == before
        assert (tmp_path / FILENAME).read_text() == SOURCE

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
@pytest.mark.parametrize("source", [SOURCE, SOURCE.replace("\n", "\r\n")], ids=["lf", "crlf"])
def test_budget_report_non_expanding_rows_have_transaction_actions(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str, width: int, source: str
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        page.width = width
        journal = await load(host, tmp_path, source)
        journal.ledger = replace(
            journal.ledger,
            budgets=(
                *journal.ledger.budgets,
                replace(journal.ledger.budgets[0], id="child", account="Expenses:Food:Meals"),
            ),
        )
        await host.budgets.refresh()
        assert not any(
            isinstance(control, ft.ExpansionTile) for control in walk(host.budgets.results)
        )
        tiles = [
            control for control in walk(host.budgets.results) if isinstance(control, ft.ListTile)
        ]
        assert [tile.data.account for tile in tiles] == ["Expenses:Food", "Expenses:Food:Meals"]
        assert [tile.content_padding for tile in tiles] == [
            ft.Padding.all(16),
            ft.Padding(left=32, top=16, right=16, bottom=16),
        ]
        for tile in tiles:
            assert tile.on_click is None
            assert isinstance(tile.title, ft.Column)
            header = tile.title.controls[0]
            assert isinstance(header, ft.Row)
            assert header.vertical_alignment == ft.CrossAxisAlignment.START
            heading = header.controls[0]
            assert isinstance(heading, ft.Row) and heading.expand
            assert heading.vertical_alignment == ft.CrossAxisAlignment.CENTER
            inset = heading.controls[0]
            assert isinstance(inset, ft.Container) and inset.width == 14 and inset.height == 48
            assert isinstance(heading.controls[-1], ft.Text) and heading.controls[-1].expand
            action_row = header.controls[-1]
            assert isinstance(action_row, ft.Row) and action_row.wrap
            assert action_row.alignment == ft.MainAxisAlignment.END
            assert action_row.expand == 2
            assert header.controls[-2].expand
            actions = [
                control for control in action_row.controls if isinstance(control, ft.IconButton)
            ]
            assert [action.tooltip for action in actions] == [
                host.tr("budget_transactions"),
                host.tr("crud_edit"),
                host.tr("crud_delete"),
            ]
            for action in actions[:1]:
                assert action.alignment is None
                assert action.padding == 14
                assert action.visual_density == ft.VisualDensity.STANDARD
                assert action.size_constraints == ft.BoxConstraints(min_width=48, min_height=48)
            bars = []
            for section in tile.title.controls[1:]:
                if section is action_row:
                    continue
                if isinstance(section, ft.Container) and isinstance(section.content, ft.Column):
                    (caption,) = section.content.controls
                    assert isinstance(caption, ft.Text)
                    continue
                lines = section.controls if isinstance(section, ft.Column) else [section]
                for line in lines:
                    if isinstance(line, ft.ProgressBar):
                        bars.append(line)
                    else:
                        assert isinstance(line, ft.Container)
                        assert isinstance(line.content, ft.Text)
                        progress = str(line.content.value).startswith(host.tr("budget_consumed"))
                        assert line.padding == ft.Padding(
                            left=14, right=14, bottom=8 if progress else 0
                        )
                        assert line.content.tooltip is None
                        if progress:
                            assert isinstance(lines[lines.index(line) + 1], ft.ProgressBar)
                            assert isinstance(section, ft.Column) and section.spacing == 4
            assert bars
            transactions = actions[0]
            assert transactions.icon == ft.Icons.RECEIPT_LONG
            assert transactions.data is tile.data
            assert transactions.on_click is not None
            cast(Callable[[object], None], transactions.on_click)(MagicMock(control=transactions))
            dialog = page.show_dialog.call_args.args[0]
            assert isinstance(dialog, ft.AlertDialog) and dialog.scrollable
        assert journal.source == source
        assert (tmp_path / FILENAME).read_bytes() == source.encode("utf-8")

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
            if isinstance(item, ft.ListTile) and isinstance(item.data, BudgetRow)
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
            if isinstance(item, ft.ListTile) and isinstance(item.data, BudgetRow)
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
            if isinstance(item, ft.ListTile) and isinstance(item.data, BudgetRow)
        ]
        assert {row.account for row in displayed} == {"Expenses", "Expenses:Food"}
        assert all(row.direct_budget for row in displayed)
        parent = next(row for row in displayed if row.account == "Expenses")
        assert parent.direct_budget == {"USD": Decimal(31)}
        assert parent.actual == {"USD": Decimal(12)}
        listing = host.budgets.controls[-1]
        assert isinstance(listing, ft.Container) and listing.content is host.budgets.results
        assert listing.theme is not None
        assert listing.theme.list_tile_theme == ft.ListTileTheme(
            horizontal_spacing=12, min_leading_width=16, min_vertical_padding=0
        )
        assert listing.theme.icon_theme == ft.IconTheme(size=16)
        tiles = [tile for tile in walk(host.budgets.results) if isinstance(tile, ft.ListTile)]
        assert [tile.content_padding for tile in tiles] == [
            ft.Padding.all(16),
            ft.Padding(left=32, top=16, right=16, bottom=16),
        ]
        for tile in tiles:
            assert tile.dense and tile.min_height == 40
            assert tile.on_click is None
            assert tile.shape == ft.RoundedRectangleBorder()

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
            if isinstance(item, ft.ListTile) and isinstance(item.data, BudgetRow)
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
        interval = next(
            control
            for control in walk(dialog.content)
            if isinstance(control, ft.Dropdown) and control.label == host.tr("budget_interval")
        )
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
            if isinstance(item, ft.ListTile)
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
            (tmp_path / FILENAME).write_text(
                SOURCE.replace('"Groceries"', f'"{display_name}"'), encoding="utf-8"
            )
            await journal.poll()
            await host.budgets.refresh()
        budget = journal.ledger.budgets[0]
        assert journal.ledger.categories[0].name == display_name
        definition = host.budgets.definitions.controls[0]
        heading = next(item for item in walk(definition) if isinstance(item, ft.Text))
        assert str(heading.value).startswith(f"{display_name} · ")
        assert heading.tooltip == budget.account
        assert heading.weight == ft.FontWeight.BOLD
        food = next(
            item
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ListTile)
            and isinstance(item.data, BudgetRow)
            and item.data.account == budget.account
        )
        assert food.subtitle is None
        label = next(item for item in walk(food) if isinstance(item, ft.Text))
        assert label.value == display_name
        assert label.tooltip == budget.account
        assert label.weight == ft.FontWeight.BOLD
        transaction_button = next(
            item
            for item in walk(food)
            if isinstance(item, ft.IconButton) and item.tooltip == host.tr("budget_transactions")
        )
        host.budgets._transactions(MagicMock(control=transaction_button))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        texts = [item for item in walk(dialog.content) if isinstance(item, ft.Text)]
        assert texts[0].value == display_name and texts[0].tooltip == budget.account
        assert any(str(item.value).endswith(f"→ {display_name}") for item in texts[1:])
        host.budgets._delete(budget)
        dialog = page.show_dialog.call_args.args[0]
        assert_deletion_title(dialog, host.tr("crud_delete"))
        assert isinstance(dialog.content, ft.Column)
        confirmation = dialog.content.controls[0]
        assert isinstance(confirmation, ft.Text)
        assert confirmation.value == display_name and confirmation.tooltip == budget.account
        assert confirmation.weight == ft.FontWeight.BOLD
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
        assert heading.weight == ft.FontWeight.BOLD

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
            if isinstance(item, ft.ListTile)
            and isinstance(item.data, BudgetRow)
            and item.data.account == "Expenses:Food"
        )
        assert isinstance(food, ft.ListTile)
        assert isinstance(food.data, BudgetRow)
        assert food.data.budget == {"USD": Decimal("9.999999999999999999999999992")}
        text = [str(item.value) for item in walk(food) if isinstance(item, ft.Text)]
        assert "Groceries" in text
        assert "Budget: 10.00 USD" in text
        assert "Spent: 12.00 USD" in text
        assert "Overspent: 2.00 USD" in text
        bars = [item for item in walk(food) if isinstance(item, ft.ProgressBar)]
        assert bars and bars[0].value == 1 and bars[0].color == ft.Colors.ERROR
        button = next(
            item
            for item in walk(food)
            if isinstance(item, ft.IconButton) and item.tooltip == host.tr("budget_transactions")
        )
        host.budgets._transactions(MagicMock(control=button))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog) and dialog.scrollable
        assert isinstance(dialog.content, ft.Column)
        assert not any(isinstance(item, ft.ExpansionTile) for item in walk(dialog.content))
        assert any(
            isinstance(item, ft.Text) and item.value == "Lunch" for item in walk(dialog.content)
        )
        assert not any(isinstance(item, ft.IconButton) for item in walk(dialog.content))

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
    category = next(item for item in walk(dialog.content) if isinstance(item, ft.Dropdown))
    amount, currency = [item for item in dialog.content.controls if isinstance(item, ft.TextField)]
    effective = next(item for item in dialog.content.controls if isinstance(item, DateTimeFields))
    return category, amount, currency, effective


async def submit(dialog: ft.AlertDialog) -> None:
    button = dialog.actions[-1]
    assert isinstance(button, ft.TextButton) and button.on_click
    await cast(Callable[[object], Awaitable[None]], button.on_click)(MagicMock())


@pytest.mark.parametrize("width", [390, 1366])
def test_budget_draft_survives_navigation_and_resize(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, width: int
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        host._navigate("budgets")
        click(host.budgets.add)
        dialog = page.show_dialog.call_args.args[0]
        category, amount, currency, effective = fields(dialog)
        category.value = "Expenses:Food"
        amount.value = "123.456"
        currency.value = "EUR"
        effective.occurred.value = "unfinished date"
        controls = tuple(host.budgets.controls)
        history = host.budgets.history
        page.show_dialog.reset_mock()
        for resized_width in (width, 800, 390, 1366):
            page.width = resized_width
            host._apply_layout(resized_width)
            host._refresh()
            host._navigate("reports")
            host._navigate("budgets")
            assert tuple(host.budgets.controls) == controls
            assert host.budgets.history is history
            assert category.value == "Expenses:Food"
            assert amount.value == "123.456" and currency.value == "EUR"
            assert effective.occurred.value == "unfinished date"
            assert host.journal is journal
        page.show_dialog.assert_not_called()
        page.pop_dialog.assert_not_called()
        assert journal.source == SOURCE

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["card", "history"])
def test_budget_cards_and_history_share_crud_refresh(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, surface: str
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        host._navigate("budgets")
        report = host.budgets
        history = report.history
        for operation in ("edit", "delete", "create"):
            if operation == "create":
                click(report.add)
            else:
                root = report.results if surface == "card" else report.definitions
                button = next(
                    item
                    for item in walk(root)
                    if isinstance(item, ft.IconButton)
                    and item.tooltip == host.tr(f"crud_{operation}")
                )
                click(button)
            dialog = page.show_dialog.call_args.args[0]
            if operation != "delete":
                category, amount, currency, effective = fields(dialog)
                category.value = "Expenses:Food"
                amount.value = "25" if operation == "edit" else "30"
                currency.value = "USD"
                effective.occurred.value = date_input(date(2026, 1, 1), host.language)
            page.run_task.reset_mock()
            await submit(dialog)
            assert host.error_message is None
            assert host.journal is journal and host.budgets is report
            assert report.history is history
            page.run_task.assert_called_once_with(report.refresh)
            await report.refresh()
            expected = [] if operation == "delete" else [Decimal(25 if operation == "edit" else 30)]
            assert [budget.amount for budget in journal.ledger.budgets] == expected
            assert [
                row.budget["USD"].quantize(Decimal("0.01"))
                for row in displayed_rows(host)
                if row.account == "Expenses:Food"
            ] == expected
            texts = [
                str(item.value) for item in walk(report.definitions) if isinstance(item, ft.Text)
            ]
            if expected:
                assert any(f"{expected[0]} USD" in text for text in texts)
            else:
                assert texts == [host.tr("budget_empty")]
            assert host.current_screen == "budgets"

    asyncio.run(scenario())


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


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_limit_history_edits_superseded_record_and_close_preserves_report_state(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        replacement = '2026-01-15 custom "budget" Expenses:Food "monthly" 25 USD\n'
        journal = await load(host, tmp_path, SOURCE + replacement)
        host._navigate("budgets")
        report = host.budgets
        report.category_id = "Expenses:Food"
        report.currency.value = "USD"
        report.overspent.value = True
        report.filters.visible = True
        await report.refresh()
        original, later = journal.ledger.budgets
        assert report._active_budget(original.account, date(2026, 2, 1)) == later
        assert report.history.visible and not report.history.disabled
        click(report.history)
        history_dialog = page.show_dialog.call_args.args[0]
        assert history_dialog.title == host.tr("budget_limit_history")
        assert isinstance(history_dialog.content, ft.Column)
        assert sum(item is report.definitions for item in walk(history_dialog.content)) == 1
        assert report.definitions not in tuple(walk(report))
        edit = next(
            item
            for item in walk(report.definitions)
            if isinstance(item, ft.IconButton)
            and item.tooltip == host.tr("crud_edit")
            and item.data == original
        )
        click(edit)
        edit_dialog = page.show_dialog.call_args.args[0]
        _, amount, _, effective = fields(edit_dialog)
        assert amount.value == "10.00"
        assert effective.occurred.value == date_input(original.date, language)
        amount.value = "9"
        await submit(edit_dialog)
        await report.refresh()
        assert len(journal.ledger.budgets) == 2
        assert journal.ledger.budgets[0].amount == Decimal(9)
        assert journal.ledger.budgets[0].date == original.date
        assert journal.ledger.budgets[1].amount == later.amount
        assert journal.ledger.budgets[1].date == later.date
        assert journal.source.endswith(replacement)
        assert any(
            item.data == journal.ledger.budgets[0]
            for item in walk(report.definitions)
            if isinstance(item, ft.IconButton)
        )
        page.pop_dialog.reset_mock()
        close = history_dialog.actions[0]
        assert isinstance(close, ft.TextButton)
        assert close.content == host.tr("daily_report_close")
        click(close)
        page.pop_dialog.assert_called_once()
        assert host.current_screen == "budgets"
        assert report.month == date(2026, 1, 1)
        assert report.category_id == report.scope.value == "Expenses:Food"
        assert report.currency.value == "USD"
        assert report.overspent.value and report.filters.visible
        click(report.history)
        reopened = page.show_dialog.call_args.args[0]
        assert isinstance(reopened.content, ft.Column)
        assert reopened.content.controls[-1] is report.definitions

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
def test_card_limit_dates_and_actions_match_each_currency(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str, width: int
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        page.width = width
        journal = await load(
            host,
            tmp_path,
            MULTI_CURRENCY
            + '\n2026-01-15 custom "budget" Expenses:Food "monthly" 25 USD\n'
            + '2027-01-01 custom "budget" Expenses:Food "monthly" 99 EUR\n',
        )
        host._navigate("budgets")
        tile = next(item for item in walk(host.budgets.results) if isinstance(item, ft.ListTile))
        latest = {
            "EUR": journal.ledger.budgets[1],
            "USD": journal.ledger.budgets[2],
        }
        assert isinstance(tile.title, ft.Column)
        limits = [
            item.content
            for item in tile.title.controls
            if isinstance(item, ft.Container) and isinstance(item.content, ft.Column)
        ]
        assert len(limits) == 2
        for section, currency in zip(limits, ("EUR", "USD"), strict=True):
            budget = latest[currency]
            (caption,) = section.controls
            assert isinstance(caption, ft.Text)
            assert caption.value == host.tr(
                "budget_limit_effective",
                amount=str(budget.amount),
                currency=currency,
                interval=host.tr("budget_monthly"),
                date=format_date(budget.date, language),
            )
            header = tile.title.controls[0]
            assert isinstance(header, ft.Row)
            actions = header.controls[-1]
            assert isinstance(actions, ft.Row) and actions.alignment == ft.MainAxisAlignment.END
            buttons = [item for item in actions.controls if item.data == budget]
            assert len(buttons) == 4
            edit, delete, change, stop = buttons
            assert isinstance(edit, ft.IconButton) and edit.tooltip == host.tr("crud_edit")
            assert isinstance(delete, ft.IconButton) and delete.tooltip == host.tr("crud_delete")
            assert isinstance(change, ft.TextButton) and change.content == host.tr("budget_change")
            assert isinstance(stop, ft.TextButton) and stop.content == host.tr("budget_stop")
            click(change)
            category, amount, chosen_currency, _ = fields(page.show_dialog.call_args.args[0])
            assert category.value == budget.account
            assert amount.value == str(budget.amount)
            assert chosen_currency.value == currency
        assert journal.source.endswith(
            '2027-01-01 custom "budget" Expenses:Food "monthly" 99 EUR\n'
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [390, 1366])
def test_card_changes_and_stops_preserve_history_month_and_filters(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, width: int
) -> None:
    async def scenario() -> None:
        host, page = view
        page.width = width
        journal = await load(host, tmp_path)
        original = journal.ledger.budgets[0]
        host._navigate("budgets")
        report = host.budgets
        report.category_id = "Expenses:Food"
        report.currency.value = "USD"
        report.overspent.value = True
        report.filters.visible = True
        await report.refresh()
        for action, effective_date, expected_amount in (
            ("budget_change", date(2026, 1, 15), Decimal(5)),
            ("budget_stop", date(2026, 1, 20), Decimal(0)),
        ):
            button = next(
                item
                for item in walk(report.results)
                if isinstance(item, ft.TextButton) and item.content == host.tr(action)
            )
            click(button)
            dialog = page.show_dialog.call_args.args[0]
            _, amount, _, effective = fields(dialog)
            if action == "budget_stop":
                assert amount.value == "0"
            else:
                amount.value = str(expected_amount)
            effective.occurred.value = date_input(effective_date, host.language)
            await submit(dialog)
            assert host.error_message is None
            await report.refresh()
            assert journal.ledger.budgets[0] == original
            assert journal.ledger.budgets[-1].date == effective_date
            assert journal.ledger.budgets[-1].amount == expected_amount
            assert report.month == date(2026, 1, 1)
            assert report.category_id == "Expenses:Food"
            assert report.currency.value == "USD" and report.filters.visible
            assert report.overspent.value
            assert host.current_screen == "budgets"
        assert len(journal.ledger.budgets) == 3
        assert SOURCE.split("2026-01-05")[0] in journal.source
        captions = [item.value for item in walk(report.results) if isinstance(item, ft.Text)]
        assert (
            host.tr(
                "budget_limit_effective",
                amount="0",
                currency="USD",
                interval=host.tr("budget_monthly"),
                date=format_date(date(2026, 1, 20), host.language),
            )
            in captions
        )
        assert (
            sum(
                isinstance(item, ft.IconButton) and item.tooltip == host.tr("crud_edit")
                for item in walk(report.definitions)
            )
            == 3
        )

    asyncio.run(scenario())


def test_empty_budget_screen_keeps_creation_and_future_stopped_history_accessible(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        source = SOURCE.replace(
            '2026-01-01 custom "budget" Expenses:Food "monthly" 10.00 USD\n', ""
        )
        journal = await load(host, tmp_path, source)
        host._navigate("budgets")
        report = host.budgets
        toolbar = report.controls[0]
        assert isinstance(toolbar, ft.Container)
        assert toolbar.alignment == ft.Alignment.CENTER_RIGHT
        toolbar = toolbar.content
        assert isinstance(toolbar, ft.Row) and toolbar.controls == [report.add, report.history]
        assert not report.add.disabled
        assert not report.history.disabled
        assert not displayed_rows(host)
        click(report.history)
        history_dialog = page.show_dialog.call_args.args[0]
        assert isinstance(history_dialog, ft.AlertDialog) and history_dialog.scrollable
        assert isinstance(history_dialog.content, ft.Column)
        assert history_dialog.content.controls == [report.history_help, report.definitions]
        empty = report.definitions.controls[0]
        assert isinstance(empty, ft.Text) and empty.value == host.tr("budget_empty")
        close = history_dialog.actions[0]
        assert isinstance(close, ft.TextButton)
        click(close)
        click(report.add)
        dialog = page.show_dialog.call_args.args[0]
        category, amount, _, effective = fields(dialog)
        category.value = "Expenses:Food"
        amount.value = "25"
        effective.occurred.value = date_input(date(2027, 1, 1), host.language)
        await submit(dialog)
        await report.refresh()
        assert report.month == date(2026, 1, 1) and not displayed_rows(host)
        future = journal.ledger.budgets[0]
        report._form(future, new=True, stop=True)
        dialog = page.show_dialog.call_args.args[0]
        _, _, _, effective = fields(dialog)
        effective.occurred.value = date_input(date(2027, 2, 1), host.language)
        await submit(dialog)
        await report.refresh()
        assert [item.amount for item in journal.ledger.budgets] == [Decimal(25), Decimal(0)]
        click(report.history)
        history_dialog = page.show_dialog.call_args.args[0]
        assert history_dialog.title == host.tr("budget_limit_history")
        assert report.definitions not in tuple(walk(report))
        assert isinstance(history_dialog.content, ft.Column)
        assert history_dialog.content.controls == [report.history_help, report.definitions]
        assert len(report.definitions.controls) == 2
        for definition, budget in zip(
            report.definitions.controls, journal.ledger.budgets, strict=True
        ):
            edit = next(
                item
                for item in walk(definition)
                if isinstance(item, ft.IconButton) and item.tooltip == host.tr("crud_edit")
            )
            click(edit)
            _, amount, _, effective = fields(page.show_dialog.call_args.args[0])
            assert amount.value == str(budget.amount)
            assert effective.occurred.value == date_input(budget.date, host.language)
        assert not displayed_rows(host)

    asyncio.run(scenario())


@pytest.mark.parametrize("reload_before_save", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_budget_conflicts_retain_all_unsaved_dialog_fields(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, reload_before_save: bool, newline: str
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
        external = (
            journal.source.replace("\r\n", "\n")
            .replace("10.00 USD", "20.00 USD")
            .replace("\n", newline)
            .encode("utf-8")
        )
        (tmp_path / FILENAME).write_bytes(external)
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
        assert (tmp_path / FILENAME).read_bytes() == external

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
        category_options = tuple(host.budgets.scope.options)
        definitions = tuple(host.budgets.definitions.controls)
        host.budgets._pick_month(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        year = dialog.content.controls[1]
        assert isinstance(year, ft.TextField)
        year.value = "unfinished year"
        host.budgets.filters.visible = True
        for _ in range(3):
            host._refresh()
            assert tuple(host.budgets.controls) == controls
            assert all(a is b for a, b in zip(host.budgets.controls, controls, strict=True))
            assert tuple(host.budgets.currency.options) == options
            assert all(
                a is b for a, b in zip(host.budgets.scope.options, category_options, strict=True)
            )
            assert tuple(host.budgets.definitions.controls) == definitions
            assert host.budgets.filters.visible
            assert year.value == "unfinished year"
        assert host.budgets.month == date(2026, 1, 1)

    asyncio.run(scenario())


def displayed_rows(host: FinanceView) -> list[BudgetRow]:
    return [
        control.data
        for control in walk(host.budgets.results)
        if isinstance(control, ft.ListTile) and isinstance(control.data, BudgetRow)
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
        assert not host.budgets.reset.visible
        assert not any(isinstance(item, ft.TextField) for item in walk(host.budgets))
        assert [item for item in walk(host.budgets) if isinstance(item, ft.Dropdown)] == [
            host.budgets.scope,
            host.budgets.currency,
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


def test_category_dropdown_has_budget_branches_friendly_names_and_clear(
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
2000-01-01 open Expenses:Unbudgeted
2000-01-01 open Expenses:Future
2026-02-01 custom "budget" Expenses:Future "monthly" 5 USD
2026-01-01 custom "budget" Expenses:Food:Meals "monthly" 2 USD
2026-01-01 custom "budget" Expenses:FoodOther "monthly" 3 USD
""",
        )
        category = host.budgets.scope
        assert isinstance(category, ft.Dropdown)
        assert category.text_style == host.operations_category.text_style
        assert [(option.key, option.text) for option in category.options] == [
            ("", host.tr("operations_all_categories")),
            ("Expenses", host.tr("categories_expenses")),
            ("Expenses:Food", "Groceries"),
            ("Expenses:Food:Meals", "Meals"),
            ("Expenses:FoodOther", "Other groceries"),
        ]
        assert "Assets:Cash" not in {option.key for option in category.options}
        assert category.options[3].content is not None
        category.value = "Expenses:Food"
        await host.budgets._category_changed(MagicMock(control=category))
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
        assert category.value == ""
        assert "Expenses:FoodOther" in {row.account for row in displayed_rows(host)}
        category.value = "Expenses:Food"
        await host.budgets._category_changed(MagicMock(control=category))
        category.value = ""
        await host.budgets._category_changed(MagicMock(control=category))
        assert not host.budgets.category_id
        assert "Expenses:FoodOther" in {row.account for row in displayed_rows(host)}

    asyncio.run(scenario())


def test_hidden_filters_keep_selections_across_navigation_and_resize(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(host, tmp_path, MULTI_CURRENCY)
        report = host.budgets
        host._navigate("budgets")
        assert not report.filters.visible
        assert not report.filter_toggle.selected
        assert report.filter_toggle.icon_color is None
        assert report.scope in tuple(walk(report.filters))
        for control in (
            report.currency,
            report.overspent,
            report.active_filters,
            report.reset,
        ):
            assert control in report.filters.controls
        for control in (report.period, report.current, report.previous, report.next):
            assert control in tuple(walk(report))
            assert control not in tuple(walk(report.filters))
        click(report.filter_toggle)
        assert report.filters.visible and report.filter_toggle.selected
        report.scope.value = "Expenses:Food"
        await report._category_changed(MagicMock(control=report.scope))
        report.currency.value = "USD"
        report.overspent.value = True
        await report._changed(MagicMock(control=report.overspent))
        assert report.filter_toggle.icon_color == ft.Colors.PRIMARY
        click(report.filter_toggle)
        assert not report.filters.visible and not report.filter_toggle.selected
        for width in (1366, 390):
            page.width = width
            host._apply_layout(width)
            host._navigate("reports")
            host._navigate("budgets")
            host._refresh()
            assert not report.filters.visible
            assert report.filter_toggle.icon_color == ft.Colors.PRIMARY
            assert report.month == date(2026, 1, 1)
            assert report.scope.value == report.category_id == "Expenses:Food"
            assert report.currency.value == "USD" and report.overspent.value
        click(report.filter_toggle)
        assert report.filters.visible and report.filter_toggle.selected
        await report._reset(MagicMock())
        assert report.filters.visible
        assert report.filter_toggle.icon_color is None
        assert not report.scope.value and not report.overspent.value

    asyncio.run(scenario())


def test_budget_report_has_no_account_filter_and_includes_all_accounts(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _ = view
        await load(
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
        assert not any(
            hasattr(host.budgets, field)
            for field in ("account", "account_id", "account_choices", "account_warning")
        )
        assert displayed_rows(host)[0].actual == {"USD": Decimal(64)}
        assert not host.budgets.filters.visible
        await host.budgets._select_category("Expenses:Food")
        assert {item.data for item in host.budgets.active_filters.controls} == {"category"}
        assert displayed_rows(host)[0].actual == {"USD": Decimal(64)}
        host.budgets.overspent.value = True
        await host.budgets._reset(MagicMock())
        assert not host.budgets.overspent.value
        assert not host.budgets.active_filters.visible
        assert displayed_rows(host)[0].actual == {"USD": Decimal(64)}

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
        listing = dialog.content.controls[-1]
        assert isinstance(listing, ft.Column) and listing.spacing == 0
        cards = [
            item
            for item in listing.controls
            if isinstance(item, ft.Container)
            and isinstance(item.content, ft.Row)
            and item.data != "transaction-date"
        ]
        assert len(cards) == 1
        assert any(isinstance(item, ft.Text) and item.value == "Dinner" for item in walk(cards[0]))
        for _ in range(3):
            host._refresh()
            assert search.value == "DINNER"
        search.value = "no match"
        cast(Callable[[object], None], search.on_change)(MagicMock())
        assert len(listing.controls) == 1
        empty = listing.controls[0]
        assert isinstance(empty, ft.Text) and empty.value == host.tr("budget_no_matches")
        assert displayed_rows(host)[0] == original
        assert (tmp_path / FILENAME).read_text() == before

    asyncio.run(scenario())


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
def test_budget_transactions_reuse_operations_rows_readonly_newest_first(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, language: str, width: int
) -> None:
    async def scenario() -> None:
        host, page = view
        host.language = language
        page.width = width
        host._apply_layout(width)
        journal = await load(
            host,
            tmp_path,
            SOURCE
            + """
2026-01-05 * "Late shop" "Evening"
    time: "20:30"
    Expenses:Food 2 USD
    Assets:Cash -2 USD
2026-01-06 * "Latest shop" "Newest day"
    time: "08:00"
    Expenses:Food:Meals 3 USD
    Assets:Cash -3 USD
2026-01-05 * "Early shop" "Morning"
    time: "09:00"
    Expenses:Food 1 USD
    Assets:Cash -1 USD
""",
        )
        before = (tmp_path / FILENAME).read_bytes()
        original = displayed_rows(host)[0]
        host.budgets._transactions(MagicMock(control=ft.TextButton(data=original)))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        listing = dialog.content.controls[-1]
        assert isinstance(listing, ft.Column) and listing.spacing == 0
        assert not any(
            isinstance(item, (ft.IconButton, ft.ExpansionTile)) for item in walk(listing)
        )
        ordered = [journal.ledger.transactions[index] for index in (2, 1, 3, 0)]
        names = host.budgets._account_names()
        expected = host._transaction_groups(ordered, names, read_only=True)
        actual_text = [item.value for item in walk(listing) if isinstance(item, ft.Text)]
        expected_text = [
            item.value for item in walk(ft.Column(expected)) if isinstance(item, ft.Text)
        ]
        assert actual_text == expected_text
        cards = [
            item
            for item in listing.controls
            if isinstance(item, ft.Container)
            and isinstance(item.content, ft.Row)
            and item.data != "transaction-date"
        ]
        assert len(cards) == 4
        assert [
            next(item.value for item in walk(card) if isinstance(item, ft.Text)) for card in cards
        ] == ["Newest day", "Evening", "Morning", "Lunch"]
        assert all(
            isinstance(card.content, ft.Row) and len(card.content.controls) == 1 for card in cards
        )
        assert (tmp_path / FILENAME).read_bytes() == before
        assert displayed_rows(host)[0] == original

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "currency,expected", [("", ["Dinner", "Lunch"]), ("USD", ["Lunch"]), ("EUR", ["Dinner"])]
)
def test_budget_transaction_rows_preserve_currency_and_payee_filters(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    currency: str,
    expected: list[str],
) -> None:
    async def scenario() -> None:
        host, page = view
        await load(host, tmp_path, MULTI_CURRENCY)
        original = displayed_rows(host)[0]
        before = (tmp_path / FILENAME).read_bytes()
        host.budgets.currency.value = currency
        host.budgets._transactions(MagicMock(control=ft.TextButton(data=original)))
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog.content, ft.Column)
        search, listing = dialog.content.controls[1:]
        assert isinstance(search, ft.TextField) and search.on_change
        assert isinstance(listing, ft.Column)

        def narrations() -> list[str | None]:
            return [
                next(item.value for item in walk(card) if isinstance(item, ft.Text))
                for card in listing.controls
                if isinstance(card, ft.Container)
                and isinstance(card.content, ft.Row)
                and card.data != "transaction-date"
            ]

        assert narrations() == expected
        search.value = "OTHER SHOP"
        cast(Callable[[object], None], search.on_change)(MagicMock())
        assert narrations() == (["Dinner"] if currency != "USD" else [])
        assert not any(isinstance(item, ft.IconButton) for item in walk(listing))
        assert (tmp_path / FILENAME).read_bytes() == before
        assert displayed_rows(host)[0] == original

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


def test_budget_controls_initialize_before_a_page_is_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FinanceView, "_today", lambda _: date(2026, 11, 5))
    host = FinanceView()
    assert host.budgets.category_id == ""
    assert not host.budgets.active_filters.visible
    assert host.budgets.month == date(2026, 11, 1)


def report_button(host: FinanceView, budget: Budget) -> ft.TextButton:
    return next(
        item
        for item in walk(host.budgets.definitions)
        if isinstance(item, ft.TextButton)
        and item.data == budget
        and item.content == host.tr("budget_view_report")
    )


def click_report(host: FinanceView, budget: Budget) -> None:
    button = report_button(host, budget)
    assert button.on_click is not None
    cast(Callable[[object], None], button.on_click)(MagicMock(control=button))


@pytest.mark.parametrize("language,width", [("en", 1366), ("uk", 390), ("ru", 800)])
@pytest.mark.parametrize(
    "effective,amount",
    [
        (date(2024, 2, 15), Decimal(20)),
        (date(2026, 1, 1), Decimal(0)),
        (date(2027, 5, 9), Decimal(30)),
    ],
)
def test_definition_opens_current_month_when_applicable_and_reveals_nested_report(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    width: int,
    effective: date,
    amount: Decimal,
) -> None:
    async def scenario() -> None:
        host, page = view
        monkeypatch.setattr(host, "_today", lambda: date(2026, 11, 5))
        expected_month = date(2026, 11, 1) if effective.year < 2027 else effective.replace(day=1)
        host.language = language
        page.width = width
        journal = await load(host, tmp_path, MULTI_CURRENCY)
        original = journal.ledger.budgets[0]
        budget = replace(
            original,
            id="child",
            account="Expenses:Food:Meals",
            date=effective,
            currency="EUR",
            amount=amount,
        )
        parent = replace(original, date=date(2000, 1, 1), currency="EUR")
        journal.ledger = replace(journal.ledger, budgets=(parent, budget))
        host._refresh()
        host._navigate("budgets")
        host.budgets.category_id = "Income:Other"
        host.budgets.currency.value = "USD"
        host.budgets.overspent.value = True
        scroll = AsyncMock()
        monkeypatch.setattr(host.body, "scroll_to", scroll)
        worker = MagicMock()
        host.budgets.worker = worker
        page.run_task.reset_mock()
        click_report(host, budget)
        worker.cancel.assert_called_once()
        page.run_task.assert_called_once_with(host.budgets.refresh)
        assert host.current_screen == "budgets"
        assert host._selected_index() == 2
        assert host.screen_host.content is host.budgets_screen
        assert host.budgets_screen.controls == [host.budgets]
        assert host.budgets.month == expected_month
        assert host.budgets.category_id == ""
        assert host.budgets.currency.value == "EUR" and not host.budgets.overspent.value
        await host.budgets.refresh()
        tiles = [item for item in walk(host.budgets.results) if isinstance(item, ft.ListTile)]
        target = next(item for item in tiles if item.data.account == budget.account)
        ancestor = next(item for item in tiles if item.data.account == parent.account)
        for tile in (ancestor, target):
            assert isinstance(tile.title, ft.Column)
            label = next(item for item in walk(tile.title) if isinstance(item, ft.Text))
            assert label.tooltip == tile.data.account
            assert label.weight == ft.FontWeight.BOLD
        assert ancestor.content_padding == ft.Padding.all(16)
        assert target.content_padding == ft.Padding(left=32, top=16, right=16, bottom=16)
        assert ancestor.bgcolor is None
        assert target.bgcolor == ft.Colors.SECONDARY_CONTAINER
        assert target.key == ft.ScrollKey(
            f"budget-report:{expected_month.isoformat()}:{budget.account}"
        )
        assert all(
            not isinstance(control, ft.Text) or control.value != host.tr("budget_direct")
            for control in walk(host.budgets.results)
        )
        assert any(
            isinstance(control, ft.IconButton) and control.tooltip == host.tr("budget_transactions")
            for control in walk(ancestor)
        )
        assert target.on_size_change is not None
        scroll.assert_not_awaited()
        await host.budgets._reveal_report(MagicMock(control=ancestor))
        scroll.assert_not_awaited()
        await host.budgets._reveal_report(MagicMock(control=target))
        scroll.assert_awaited_once_with(scroll_key=target.key, duration=350)
        await host.budgets._reveal_report(MagicMock(control=target))
        scroll.assert_awaited_once()
        assert not host.budgets.reveal_requested
        await host.budgets.refresh()
        refreshed = next(
            item
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ListTile) and item.data.account == budget.account
        )
        assert isinstance(refreshed.title, ft.Column)
        label = next(item for item in walk(refreshed.title) if isinstance(item, ft.Text))
        assert label.weight == ft.FontWeight.BOLD
        assert refreshed.bgcolor == ft.Colors.SECONDARY_CONTAINER
        assert refreshed.on_size_change is None and host.budgets.scroll_target is None
        scroll.assert_awaited_once()
        assert {item.data for item in host.budgets.active_filters.controls} == {"currency"}
        assert (tmp_path / FILENAME).read_text() == MULTI_CURRENCY

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "replacement_date,replacement_currency,expected",
    [
        (date(2026, 1, 1), "USD", date(2026, 1, 1)),
        (date(2026, 10, 1), "USD", date(2026, 1, 1)),
        (date(2026, 11, 1), "USD", date(2026, 1, 1)),
        (date(2026, 11, 15), "USD", date(2026, 11, 1)),
        (date(2026, 12, 1), "USD", date(2026, 11, 1)),
        (date(2026, 10, 1), "EUR", date(2026, 11, 1)),
    ],
)
def test_definition_report_default_month_respects_replacements_and_currency(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_date: date,
    replacement_currency: str,
    expected: date,
) -> None:
    async def scenario() -> None:
        host, _ = view
        monkeypatch.setattr(host, "_today", lambda: date(2026, 11, 5))
        journal = await load(host, tmp_path)
        budget = journal.ledger.budgets[0]
        replacement = replace(
            budget,
            id="replacement",
            date=replacement_date,
            currency=replacement_currency,
            amount=Decimal(0),
        )
        journal.ledger = replace(journal.ledger, budgets=(budget, replacement))
        host._refresh()
        click_report(host, budget)
        assert host.budgets.month == expected
        assert host.budgets.currency.value == budget.currency
        assert host.budgets.highlighted == (expected, budget.account)
        assert journal.source == SOURCE

    asyncio.run(scenario())


def test_reopening_same_budget_rebuilds_target_and_ignores_old_layout_events(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        budget = journal.ledger.budgets[0]
        scroll = AsyncMock()
        monkeypatch.setattr(host.body, "scroll_to", scroll)
        click_report(host, budget)
        await host.budgets.refresh()
        old_target = host.budgets.scroll_target
        assert old_target is not None
        host._navigate("accounts")
        await host.budgets._reveal_report(MagicMock(control=old_target))
        scroll.assert_not_awaited()
        click_report(host, budget)
        await host.budgets.refresh()
        target = host.budgets.scroll_target
        assert target is not None and target is not old_target
        await host.budgets._reveal_report(MagicMock(control=old_target))
        scroll.assert_not_awaited()
        await host.budgets._reveal_report(MagicMock(control=target))
        scroll.assert_awaited_once()
        host._navigate("accounts")
        click_report(host, budget)
        await host.budgets.refresh()
        assert host.budgets.scroll_target is not None
        await host.budgets._reveal_report(MagicMock(control=host.budgets.scroll_target))
        assert scroll.await_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["month", "filters"])
def test_changing_report_context_clears_the_highlight_and_pending_scroll(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        scroll = AsyncMock()
        monkeypatch.setattr(host.body, "scroll_to", scroll)
        click_report(host, journal.ledger.budgets[0])
        await host.budgets.refresh()
        target = host.budgets.scroll_target
        assert target is not None
        if change == "month":
            await host.budgets._next(MagicMock())
        else:
            await host.budgets._changed(MagicMock())
        assert host.budgets.highlighted is None
        assert not host.budgets.reveal_requested and host.budgets.scroll_target is None
        await host.budgets._reveal_report(MagicMock(control=target))
        scroll.assert_not_awaited()
        assert all(
            item.bgcolor is None
            for item in walk(host.budgets.results)
            if isinstance(item, ft.ListTile)
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", ["removed", "not-ready", "no-journal"])
def test_stale_definition_action_reports_error_without_changing_screen_or_filters(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, unavailable: str
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        budget = journal.ledger.budgets[0]
        host._navigate("accounts")
        host.budgets.category_id = "Expenses:Food"
        if unavailable == "removed":
            journal.ledger = replace(journal.ledger, budgets=())
        elif unavailable == "not-ready":
            journal.ready = False
        else:
            host.journal = None
        click_report(host, budget)
        assert host.current_screen == "accounts"
        assert host.budgets.category_id == "Expenses:Food"
        assert host.error_message == "crud_stale"
        page.show_dialog.assert_called_once()
        assert not host.budgets.reveal_requested

    asyncio.run(scenario())


def test_failed_report_calculation_never_schedules_a_scroll(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, _ = view
        journal = await load(host, tmp_path)
        click_report(host, journal.ledger.budgets[0])
        monkeypatch.setattr(
            "app.ui.budgets.rows", MagicMock(side_effect=LedgerError("budget_month_error"))
        )
        await host.budgets.refresh()
        assert host.budgets.error.visible
        assert not host.budgets.reveal_requested and host.budgets.scroll_target is None

    asyncio.run(scenario())


def test_scroll_failure_is_reported_without_discarding_the_budget_highlight(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, page = view
        journal = await load(host, tmp_path)
        click_report(host, journal.ledger.budgets[0])
        await host.budgets.refresh()
        target = host.budgets.scroll_target
        assert target is not None
        monkeypatch.setattr(
            host.body, "scroll_to", AsyncMock(side_effect=LedgerError("budget_month_error"))
        )
        await host.budgets._reveal_report(MagicMock(control=target))
        assert host.error_message == "budget_month_error"
        page.show_dialog.assert_called_once()
        assert target.bgcolor == ft.Colors.SECONDARY_CONTAINER
        assert not host.budgets.reveal_requested and host.budgets.scroll_target is None

    asyncio.run(scenario())
