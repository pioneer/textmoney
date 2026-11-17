import asyncio
import inspect
from collections.abc import Callable, Iterator
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click_async, transaction_form

from app.domain.budgets import Budget
from app.domain.ledger import LedgerError
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import format_date
from app.ui.finance import FinanceView

SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Expenses:Food
    name: "Food"
2000-01-01 open Expenses:Food:Meals
2000-01-01 open Income:Salary
2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD
2026-01-02 custom "budget" Expenses:Food "monthly" 90 EUR
"""
SOURCE += "".join(
    f'2026-01-05 * "Synthetic lunch {index:02}"\n    Expenses:Food 1 USD\n    Assets:Cash -1 USD\n'
    for index in range(60)
)


def walk(control: ft.Control) -> Iterator[ft.Control]:
    yield control
    for field in fields(control):
        if field.metadata.get("skip") or field.name.startswith("_"):
            continue
        value = getattr(control, field.name)
        if isinstance(value, ft.Control):
            yield from walk(value)
        elif isinstance(value, (list, tuple)):
            for child in value:
                if isinstance(child, ft.Control):
                    yield from walk(child)


def menus(control: ft.Control) -> list[ft.ContextMenu]:
    return [child for child in walk(control) if isinstance(child, ft.ContextMenu)]


async def fire(control: ft.Control, name: str) -> None:
    if isinstance(control, ft.ContextMenu) and name == "open":
        target = next(child for child in walk(control.content) if isinstance(child, ft.Container))
        pressed = cast(Callable[..., None], target.on_tap_down)
        pressed(ft.TapEvent(control=target, name="tap_down", global_position=ft.Offset(120, 200)))
        await fire(target, "click")
        return
    if isinstance(control, ft.ContextMenu) and name == "cancel":
        name = "dismiss"
    callback = cast(Callable[..., object], getattr(control, f"on_{name}"))
    assert callback is not None
    result = callback(ft.Event(control=control, name=name, data=None))
    if inspect.isawaitable(result):
        await result


async def choose(menu: ft.ContextMenu, label: str) -> None:
    await fire(menu, "open")
    await fire(next(item for item in menu.items if menu_label(item) == label), "click")


def menu_label(item: ft.PopupMenuItem) -> str:
    if isinstance(item.content, str):
        return item.content
    assert isinstance(item.content, ft.Control)
    return next(child.value for child in walk(item.content) if isinstance(child, ft.Text))


@pytest.fixture
def view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    page.platform = ft.PagePlatform.ANDROID
    page.web = False
    page._dialogs = MagicMock(controls=[])
    page.views = [MagicMock(spec=ft.View)]
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    monkeypatch.setattr(FinanceView, "_today", lambda _: date(2026, 1, 31))
    monkeypatch.setattr(ft.ContextMenu, "open", AsyncMock())
    host = FinanceView()
    host.body.scroll_to = AsyncMock()
    host.startup_complete = True
    host.did_mount()
    (tmp_path / FILENAME).write_text(SOURCE, encoding="utf-8")
    host.journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=host._today)
    asyncio.run(host.journal.load())
    host._refresh()
    return host, page


@pytest.mark.parametrize(
    ("platform", "mobile"),
    [
        (ft.PagePlatform.ANDROID, True),
        (ft.PagePlatform.IOS, True),
        (ft.PagePlatform.LINUX, False),
        (ft.PagePlatform.WINDOWS, False),
        (ft.PagePlatform.MACOS, False),
    ],
)
@pytest.mark.parametrize("width", [390, 1366])
def test_item_actions_follow_platform_not_width(
    view: tuple[FinanceView, MagicMock], platform: ft.PagePlatform, mobile: bool, width: int
) -> None:
    host, page = view
    page.platform, page.width = platform, width
    host.did_mount()
    host._apply_layout(width)
    host._refresh()
    assert host._mobile_item_actions is mobile
    assert host.journal is not None
    transaction = host.journal.ledger.transactions[-1]
    for compact in (False, True):
        card = host._transaction_card(transaction, {}, compact)
        assert bool(menus(card)) is mobile
        buttons = [child for child in walk(card) if isinstance(child, ft.IconButton)]
        assert len(buttons) == (0 if mobile else 1 if compact else 2)
        if mobile:
            assert [menu_label(item) for item in menus(card)[0].items[1:]] == [
                host.tr("crud_edit"),
                host.tr("crud_delete"),
            ]
    assert bool(menus(host.account_list)) is mobile
    assert bool(menus(host.category_list)) is mobile
    assert len(host.home_activity.controls) > 0


def test_mobile_category_expansion_has_a_separate_action_target(
    view: tuple[FinanceView, MagicMock],
) -> None:
    host, _ = view
    root = host.category_list.controls[0]
    assert isinstance(root, ft.ExpansionTile)
    assert not menus(cast(ft.Control, root.title))
    parent = next(
        child
        for child in walk(host.category_list)
        if isinstance(child, ft.ExpansionTile) and child.data == "Expenses:Food"
    )
    assert isinstance(parent.title, ft.ContextMenu)
    assert parent.on_change is not None
    cast(Callable[..., object], parent.on_change)(
        ft.Event(control=parent, name="change", data=True)
    )
    assert host.category_expansion["Expenses:Food"]
    target = next(child for child in walk(parent.title.content) if isinstance(child, ft.Container))
    assert target.on_click is not parent.on_change
    assert not any(isinstance(child, ft.IconButton) for child in walk(host.category_list))


def test_mobile_menu_preserves_edit_and_confirmed_delete(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, page = view
        assert host.journal is not None
        journal = host.journal
        transaction = journal.ledger.transactions[-1]
        menu = menus(host._transaction_card(transaction, {}))[0]
        await choose(menu, host.tr("crud_edit"))
        dialog = page.show_dialog.call_args.args[0]
        assert transaction_form(dialog).description.value == transaction.narration
        page.pop_dialog()
        original = journal.source
        await choose(menu, host.tr("crud_delete"))
        confirmation = page.show_dialog.call_args.args[0]
        assert journal.source == original
        await click_async(confirmation.actions[-1])
        assert transaction.id not in {entry.id for entry in journal.ledger.transactions}
        assert len(journal.ledger.transactions) == 59

    asyncio.run(scenario())


def test_account_delete_with_history_keeps_existing_block_and_edit_path(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, page = view
        assert host.journal is not None
        original = host.journal.source
        menu = menus(host.account_list)[0]
        await choose(menu, host.tr("crud_delete"))
        dialog = page.show_dialog.call_args.args[0]
        assert any(
            isinstance(child, ft.Text) and child.value == host.tr("crud_has_history")
            for child in walk(dialog)
        )
        assert host.journal.source == original
        await choose(menu, host.tr("crud_edit"))
        editor = page.show_dialog.call_args.args[0]
        assert any(
            isinstance(child, ft.Checkbox) and child.label == host.tr("crud_active")
            for child in walk(editor)
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["page", "filter", "source", "journal", "screen", "cancel"])
def test_open_menu_cannot_execute_after_context_changes(
    view: tuple[FinanceView, MagicMock], change: str
) -> None:
    async def scenario() -> None:
        host, page = view
        host._navigate("transactions")
        host._transaction_dialog = MagicMock()
        host._refresh()
        menu = menus(host.history)[0]
        await fire(menu, "open")
        if change == "page":
            await host.operations.move("older")
        elif change == "filter":
            host.operations.search.value = "lunch 01"
            host.operations._search_changed(MagicMock())
        elif change == "source":
            assert host.journal is not None
            host.journal.source += "\n; Reloaded\n"
        elif change == "journal":
            host.journal = None
        elif change == "screen":
            host._navigate("accounts")
        else:
            await fire(menu, "cancel")
        await fire(menu.items[1], "click")
        host._transaction_dialog.assert_not_called()
        dialog = page.show_dialog.call_args.args[0]
        assert host.tr("action_failed") == dialog.title
        assert isinstance(dialog.content, ft.Text) and "changed" in dialog.content.value.lower()
        if change in {"page", "filter"}:
            current_menu = menus(host.history)[0]
            await choose(current_menu, host.tr("crud_edit"))
            assert (
                host._transaction_dialog.call_args.args[1].id
                == host.operations.page.transactions[0].id
            )

    asyncio.run(scenario())


def test_mobile_readonly_preview_and_unsupported_transaction_remain_safe(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        host, page = view
        assert host.journal is not None
        transaction = host.journal.ledger.transactions[-1]
        preview = host._transaction_card(transaction, {}, read_only=True)
        assert not menus(preview)
        assert not any(isinstance(child, ft.IconButton) for child in walk(preview))
        # Three expense postings exceed the purchase-plus-fee editing subset.
        path = tmp_path / FILENAME
        path.write_text(
            SOURCE
            + '2026-01-06 * "External split"\n'
            + "    Expenses:Food 1 USD\n"
            + "    Expenses:Food:Meals 1 USD\n"
            + "    Expenses:Food 1 USD\n"
            + "    Assets:Cash -3 USD\n",
            encoding="utf-8",
        )
        await host.journal.load()
        split = host.journal.ledger.transactions[-1]
        menu = menus(host._transaction_card(split, {}))[0]
        assert menu_label(menu.items[1]) == host.tr("details")
        assert menu.items[2].disabled
        original = host.journal.source
        await choose(menu, host.tr("details"))
        assert page.show_dialog.call_args.args[0].title == host.tr("details")
        assert host.journal.source == original

    asyncio.run(scenario())


def test_budget_menus_cover_entire_item_and_keep_currency_targets(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, _ = view
        await host.budgets.refresh()
        host.budgets._form = MagicMock()
        menu = menus(host.budgets.results)[0]
        headers = [item.content for item in menu.items if item.disabled]
        assert any(isinstance(text, str) and text.startswith("USD") for text in headers)
        assert any(isinstance(text, str) and text.startswith("EUR") for text in headers)
        assert not any(
            isinstance(child, (ft.IconButton, ft.TextButton))
            for child in walk(host.budgets.results)
        )
        tile = next(child for child in walk(menu.content) if isinstance(child, ft.ListTile))
        assert tile.data is not None
        assert any(isinstance(child, ft.ProgressBar) for child in walk(menu.content))
        assert any(
            isinstance(child, ft.Semantics)
            and child.exclude_semantics
            and isinstance(child.content, ft.ProgressBar)
            for child in walk(menu.content)
        )
        assert any(
            isinstance(child, ft.Text) and host.tr("budget_planned") in child.value
            for child in walk(menu.content)
        )
        assert isinstance(menu.content, ft.Control)
        assert not any(
            isinstance(child, ft.Text) and child.selectable for child in walk(menu.content)
        )
        assert host.journal is not None
        for budget in host.journal.ledger.budgets:
            header = next(
                index
                for index, item in enumerate(menu.items)
                if item.disabled
                and isinstance(item.content, str)
                and item.content.startswith(budget.currency)
            )
            for offset, kwargs in [(1, {}), (3, {"new": True}), (4, {"new": True, "stop": True})]:
                await fire(menu, "open")
                await fire(menu.items[header + offset], "click")
                host.budgets._form.assert_called_with(budget, **kwargs)

    asyncio.run(scenario())


def test_budget_history_menu_preserves_popup_handoff(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, page = view
        host.budgets._show_history(MagicMock())
        menu = menus(host.budgets.definitions)[0]
        assert host.journal is not None
        budget = menu.items[1].data
        assert isinstance(budget, Budget)
        assert budget.currency in str(menu.items[0].content)
        assert format_date(budget.date, host.language) in str(menu.items[0].content)
        assert any(
            isinstance(child, ft.Text) and f"{budget.amount} {budget.currency}" in child.value
            for child in walk(menu.content)
        )
        assert not any(
            isinstance(child, ft.Text) and child.selectable for child in walk(menu.content)
        )
        await choose(menu, host.tr("budget_view_report"))
        page.pop_dialog.assert_called_once()
        assert host.current_screen == "budgets"
        assert host.budgets.currency.value == budget.currency

    asyncio.run(scenario())


def test_mobile_report_menu_preserves_drilldown_scope(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, _ = view
        host._navigate("reports")
        menu = next(
            menu for menu in menus(host.report_categories) if menu.items[0].content == "Food"
        )
        assert not any(isinstance(child, ft.IconButton) for child in walk(host.report_categories))
        await choose(menu, f"{host.tr('show_operations')}: Food")
        assert host.current_screen == "transactions"
        assert host.operations_category.value == "Expenses:Food"
        assert host.operations.scope.currency == "USD"
        assert host.operations.scope.start == date(2026, 1, 1)
        assert host.operations.scope.end == date(2026, 1, 31)

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_menu_has_localized_accessible_name_and_touch_sized_entries(
    view: tuple[FinanceView, MagicMock], language: str
) -> None:
    host, _ = view
    host.language = language
    assert host.journal is not None
    transaction = host.journal.ledger.transactions[-1]
    menu = menus(host._transaction_card(transaction, {}))[0]
    assert isinstance(menu.content, ft.MergeSemantics)
    semantics = menu.content.content
    assert isinstance(semantics, ft.Semantics)
    assert semantics.label == host.tr("item_actions", name=transaction.narration)
    assert isinstance(semantics.content, ft.Container) and semantics.content.min_height == 48
    assert all(item.height >= 48 for item in menu.items)
    assert menu_label(menu.items[1]) == host.tr("crud_edit")
    assert menu_label(menu.items[2]) == host.tr("crud_delete")


def test_menu_dispatch_awaits_handlers_and_reports_failures(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, page = view
        received: list[str] = []

        async def fail(event: ft.Event[ft.TextButton]) -> None:
            await asyncio.sleep(0)
            received.append(event.control.data)
            raise LedgerError("crud_stale")

        menu = host._item_menu(
            ft.Text("Item"), "Item", [ft.TextButton("Edit", data="id", on_click=fail)]
        )
        await choose(menu, "Edit")
        assert received == ["id"]
        assert page.show_dialog.call_args.args[0].title == host.tr("action_failed")

    asyncio.run(scenario())


@pytest.mark.parametrize("position", [(28, 180), (185, 420), (358, 720)])
def test_menu_opens_at_completed_tap_position(
    view: tuple[FinanceView, MagicMock], position: tuple[int, int]
) -> None:
    async def scenario() -> None:
        host, _ = view
        menu = host._item_menu(
            ft.Text("Item"), "Item", [ft.TextButton("Edit", on_click=lambda _: None)]
        )
        assert isinstance(menu, ft.ContextMenu)
        target = next(child for child in walk(menu.content) if isinstance(child, ft.Container))
        menu.open = AsyncMock()
        assert target.on_tap_down is not None
        event = ft.TapEvent(
            control=target,
            name="tap_down",
            local_position=ft.Offset(12, 16),
            global_position=ft.Offset(*position),
        )
        cast(Callable[..., None], target.on_tap_down)(event)
        menu.open.assert_not_awaited()
        await fire(target, "click")
        menu.open.assert_awaited_once_with(
            local_position=event.local_position, global_position=event.global_position
        )
        assert menu.primary_trigger is None
        assert menu.secondary_trigger is None

    asyncio.run(scenario())


def test_accessible_activation_does_not_reuse_cancelled_tap_coordinates(
    view: tuple[FinanceView, MagicMock],
) -> None:
    async def scenario() -> None:
        host, _ = view
        menu = host._item_menu(
            ft.Text("Item"), "Item", [ft.TextButton("Edit", on_click=lambda _: None)]
        )
        target = next(child for child in walk(menu.content) if isinstance(child, ft.Container))
        menu.open = AsyncMock()
        cast(Callable[..., None], target.on_tap_down)(
            ft.TapEvent(control=target, name="tap_down", global_position=ft.Offset(20, 200))
        )
        assert isinstance(menu.content, ft.MergeSemantics)
        assert isinstance(menu.content.content, ft.Semantics)
        await fire(menu.content.content, "tap")
        menu.open.assert_awaited_once_with(local_position=None, global_position=None)

    asyncio.run(scenario())


def test_menu_reuses_desktop_action_icons(view: tuple[FinanceView, MagicMock]) -> None:
    host, _ = view
    actions = [
        ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Edit", on_click=lambda _: None),
        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Delete", on_click=lambda _: None),
        ft.TextButton("Report", icon=ft.Icons.BAR_CHART_OUTLINED, on_click=lambda _: None),
    ]
    menu = host._item_menu(ft.Text("Item"), "Item", actions)
    assert [
        child.icon
        for item in menu.items[1:]
        for child in walk(cast(ft.Control, item.content))
        if isinstance(child, ft.Icon)
    ] == [action.icon for action in actions]


@pytest.mark.parametrize("icon_kind", ["native", "custom", "none"])
def test_long_menu_action_wraps_within_available_width(
    view: tuple[FinanceView, MagicMock], icon_kind: str
) -> None:
    async def scenario() -> None:
        host, _ = view
        host.language = "uk"
        name = "A long category name with several words and " + "x" * 100
        label = f"{host.tr('show_operations')}: {name}"
        icon = (
            ft.Icon(ft.Icons.RECEIPT_LONG)
            if icon_kind == "custom"
            else ft.Icons.RECEIPT_LONG
            if icon_kind == "native"
            else None
        )
        handler = MagicMock()
        action = ft.TextButton(label, icon=icon, data="Expenses:Food", on_click=handler)
        menu = host._item_menu(ft.Text(name), name, [action])
        entry = menu.items[1]
        # A separate native item icon creates an unconstrained Flutter Row.
        assert entry.icon is None
        assert isinstance(entry.content, ft.Row)
        text = entry.content.controls[-1]
        assert isinstance(text, ft.Text) and text.expand
        assert text.value == label and not text.no_wrap and text.max_lines is None
        assert entry.height >= 48 and entry.content.height is None
        if icon_kind == "custom":
            assert entry.content.controls[0] is icon
        await choose(menu, label)
        handler.assert_called_once()
        assert handler.call_args.args[0].control is action
        assert handler.call_args.args[0].control.data == "Expenses:Food"

    asyncio.run(scenario())


def test_item_menu_uses_native_bounded_feedback_on_original_item(
    view: tuple[FinanceView, MagicMock],
) -> None:
    host, _ = view
    content = ft.Text("Tapped item")
    menu = host._item_menu(content, "Item", [ft.TextButton("Edit", on_click=lambda _: None)])
    target = next(child for child in walk(menu.content) if isinstance(child, ft.Container))
    assert target.ink
    assert target.clip_behavior == ft.ClipBehavior.HARD_EDGE
    assert target.content is content
    assert target.on_click is not None
    assert target.on_tap_down is not None


@pytest.mark.parametrize("accessible", [False, True])
def test_menu_open_failures_are_reported_and_cannot_dispatch(
    view: tuple[FinanceView, MagicMock], accessible: bool
) -> None:
    async def scenario() -> None:
        host, page = view
        action = MagicMock()
        menu = host._item_menu(ft.Text("Item"), "Item", [ft.TextButton("Edit", on_click=action)])
        menu.open = AsyncMock(side_effect=RuntimeError("Menu unavailable"))
        if accessible:
            semantics = next(
                child for child in walk(menu.content) if isinstance(child, ft.Semantics)
            )
            await fire(semantics, "tap")
        else:
            await fire(menu, "open")
        menu.open.assert_awaited_once()
        assert page.show_dialog.call_args.args[0].title == host.tr("action_failed")
        await fire(menu.items[1], "click")
        action.assert_not_called()

    asyncio.run(scenario())
