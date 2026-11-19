import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click

from app.domain.ledger import Account, Ledger
from app.domain.operations import OperationScope
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.ui import finance
from app.ui.finance import FinanceView
from app.ui.operations import OperationsLocation
from app.ui.theme import DARK_NAVIGATION_SURFACE


@pytest.fixture
def navigation(monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    page.pop_dialog.return_value = None
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.startup_complete = True
    view._apply_layout(page.width)
    return view, page


def test_compact_navigation_leaves_room_for_system_insets(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    assert page.bottom_appbar is view.navigation_bar
    assert view.navigation_bar.height is None
    assert view.navigation_bar.content is view.navigation_row
    assert all(button.height == 80 for button in view.bottom_buttons)
    assert isinstance(view.overflow_menu.content, ft.Column)
    assert view.overflow_menu.content.height == 80


def test_mobile_screen_history_back_forward_and_branching(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    assert not view.navigation_back.visible
    assert not view.navigation_forward.visible
    view.budgets.month = date(2026, 2, 1)
    click(view.bottom_buttons[2])
    view._overflow_selected(MagicMock(control=MagicMock(data="accounts")))
    view._navigate("settings")
    assert view.screen_history == ["home", "budgets", "accounts", "settings"]
    assert not view.navigation_back.visible
    assert not view.navigation_forward.visible

    for screen in ("accounts", "budgets", "home"):
        assert view._back()
        assert view.current_screen == screen
        assert not view.navigation_back.visible
    assert view._compact_selected_index() == 0
    assert view._forward()
    assert view.current_screen == "budgets"
    assert view.budgets.month == date(2026, 2, 1)
    assert view._compact_selected_index() == 2
    assert page.bottom_appbar is view.navigation_bar
    assert view._forward() and view.current_screen == "accounts"
    assert view._forward() and view.current_screen == "settings"
    assert not view._forward()
    assert view._back() and view.current_screen == "accounts"
    assert view._back() and view.current_screen == "budgets"
    view._navigate("budgets")
    assert view.screen_history == ["home", "budgets", "accounts", "settings"]
    view._navigate("reports")
    assert view.screen_history == ["home", "budgets", "reports"]
    assert not view._forward()


@pytest.mark.parametrize(
    "platform,web,desktop",
    [
        (ft.PagePlatform.LINUX, False, True),
        (ft.PagePlatform.WINDOWS, False, True),
        (ft.PagePlatform.MACOS, False, True),
        (ft.PagePlatform.ANDROID, False, False),
        (ft.PagePlatform.IOS, False, False),
        (ft.PagePlatform.LINUX, True, False),
    ],
)
@pytest.mark.parametrize("width", [390, 800, 1366])
def test_desktop_history_buttons_follow_platform_not_width(
    navigation: tuple[FinanceView, MagicMock],
    platform: ft.PagePlatform,
    web: bool,
    desktop: bool,
    width: int,
) -> None:
    view, page = navigation
    page.platform, page.web, page.width = platform, web, width
    view.language = "uk"
    if not web:
        page.views = [ft.View()]
        view.did_mount()
        assert (page.on_keyboard_event == view._navigation_key) == desktop
    view._apply_layout(width)
    assert view.navigation_back.visible == desktop
    assert view.navigation_forward.visible == desktop
    assert view.navigation_back.disabled and view.navigation_forward.disabled
    assert view.navigation_back.tooltip == view.tr("nav_back")
    assert view.navigation_forward.tooltip == view.tr("nav_forward")
    view._navigate("budgets")
    assert view.navigation_back.visible == desktop
    assert view.navigation_forward.visible == desktop
    assert not view.navigation_back.disabled and view.navigation_forward.disabled
    if desktop:
        click(view.navigation_back)
    else:
        assert view._back()
    assert view.current_screen == "home"
    assert view.navigation_back.visible == desktop
    assert view.navigation_back.disabled and not view.navigation_forward.disabled
    if desktop:
        click(view.navigation_forward)
        assert view.current_screen == "budgets"
        assert not view.navigation_back.disabled and view.navigation_forward.disabled


def test_desktop_keyboard_handler_is_installed_and_removed(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.platform, page.web = ft.PagePlatform.LINUX, False
    view.did_mount()
    assert page.on_keyboard_event == view._navigation_key
    view.will_unmount()
    assert page.on_keyboard_event is None


def test_browser_history_restores_distinct_operations_scopes(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.route = "/"
    view._setup_browser_navigation()
    first = OperationsLocation(scope=OperationScope(query="Food"))
    second = OperationsLocation(scope=OperationScope(query="Housing"))
    view._navigate("transactions", operations=first)
    first_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=first_route))
    view._navigate("transactions", operations=second)
    second_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=second_route))
    assert view.screen_history == ["home", "transactions", "transactions"]
    view._browser_route_changed(MagicMock(route=first_route))
    assert view.operations.scope == first.scope
    view._browser_route_changed(MagicMock(route=second_route))
    assert view.operations.scope == second.scope
    view._browser_route_changed(MagicMock(route=first_route))
    view._navigate("budgets")
    assert view.screen_history == ["home", "transactions", "budgets"]
    assert second_route not in view.browser_routes


def test_system_back_uses_history_and_allows_exit_at_start(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, _ = navigation
    root = MagicMock(spec=ft.View)
    view._navigate("budgets")
    assert not view.navigation_back.visible
    assert not view.navigation_forward.visible
    asyncio.run(view._system_back(MagicMock(control=root)))
    assert view.current_screen == "home"
    root.confirm_pop.assert_awaited_once_with(False)
    root.confirm_pop.reset_mock()
    asyncio.run(view._system_back(MagicMock(control=root)))
    root.confirm_pop.assert_awaited_once_with(True)


@pytest.mark.parametrize(
    "popup_type",
    [ft.AlertDialog, ft.DatePicker, ft.TimePicker],
    ids=["alert-dialog", "date-picker", "time-picker"],
)
def test_system_back_dismisses_dialog_before_screen(
    navigation: tuple[FinanceView, MagicMock],
    popup_type: type[ft.DialogControl],
) -> None:
    view, page = navigation
    root = MagicMock(spec=ft.View)
    view._navigate("accounts")
    page.pop_dialog.side_effect = [popup_type(), None]
    asyncio.run(view._system_back(MagicMock(control=root)))
    assert view.current_screen == "accounts"
    assert view.screen_history == ["home", "accounts"]
    root.confirm_pop.assert_awaited_once_with(False)
    root.confirm_pop.reset_mock()
    asyncio.run(view._system_back(MagicMock(control=root)))
    assert view.current_screen == "home"
    root.confirm_pop.assert_awaited_once_with(False)


def test_shortcuts_filters_and_resizing_preserve_screen_history(
    navigation: tuple[FinanceView, MagicMock],
    tmp_path: Path,
) -> None:
    view, page = navigation
    view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
    view.journal.ready = True
    view.journal.ledger = Ledger(
        accounts=(Account("Assets:Cash", "Cash", "USD", date(2000, 1, 1)),)
    )
    view._refresh()
    view._go_accounts(MagicMock())
    view._navigate("transactions")
    view.operations_filters.visible = True
    view.operations_account.value = "Assets:Cash"
    view._apply_layout(800)
    view._apply_layout(390)
    assert view.screen_history == ["home", "accounts", "transactions"]
    view._navigate("budgets")
    view._back()
    assert view.current_screen == "transactions"
    assert view.operations_filters.visible
    assert view.operations_account.value == "Assets:Cash"
    assert view._compact_selected_index() == 1
    assert page.bottom_appbar is view.navigation_bar


def test_android_back_handler_is_installed_and_removed(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.platform = ft.PagePlatform.ANDROID
    page.web = False
    root = ft.View()
    page.views = [root]
    view.did_mount()
    assert not root.can_pop
    assert root.on_confirm_pop == view._system_back
    view.will_unmount()
    assert root.can_pop
    assert root.on_confirm_pop is None


def test_browser_back_and_forward_retrace_visits(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.web = True
    page.route = "/"
    view._setup_browser_navigation()
    view._navigate("budgets")
    budget_route = view.browser_routes[-1]
    view._navigate("accounts")
    account_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=budget_route))
    view._browser_route_changed(MagicMock(route=account_route))
    assert view.current_screen == "accounts"
    view._browser_route_changed(MagicMock(route=budget_route))
    assert view.current_screen == "budgets"
    assert view._compact_selected_index() == 2
    view._browser_route_changed(MagicMock(route="/"))
    assert view.current_screen == "home"
    view._browser_route_changed(MagicMock(route=budget_route))
    assert view.current_screen == "budgets"
    view._browser_route_changed(MagicMock(route=account_route))
    assert view.current_screen == "accounts"
    view._browser_route_changed(MagicMock(route=budget_route))
    view._navigate("budgets")
    assert view.browser_routes == ["/", budget_route, account_route]
    view._navigate("reports")
    assert account_route not in view.browser_routes
    assert view.screen_history == ["home", "budgets", "reports"]


def test_browser_route_pushes_wait_for_each_route_acknowledgement(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.route = "/"
    view._setup_browser_navigation()
    view._navigate("budgets")
    first = view.browser_routes[-1]
    view._navigate("accounts")
    second = view.browser_routes[-1]

    async def scenario() -> None:
        first_task = asyncio.create_task(view._push_browser_route(first))
        second_task = asyncio.create_task(view._push_browser_route(second))
        await asyncio.sleep(0)
        page.push_route.assert_awaited_once_with(first)
        view._browser_route_changed(MagicMock(route=first))
        await first_task
        await asyncio.sleep(0)
        assert page.push_route.await_count == 2
        view._browser_route_changed(MagicMock(route=second))
        await second_task

    asyncio.run(scenario())


def test_unknown_browser_route_does_not_change_screen_or_log_url(
    navigation: tuple[FinanceView, MagicMock], caplog: pytest.LogCaptureFixture
) -> None:
    view, page = navigation
    page.route = "/"
    view._setup_browser_navigation()
    view._browser_route_changed(MagicMock(route="/private-data"))
    assert view.current_screen == "home"
    assert view.screen_history == ["home"]
    assert "unrecognized browser navigation route" in caplog.text
    assert "/private-data" not in caplog.text


def test_failed_browser_route_push_is_reported_and_clears_pending(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.route = "/"
    view._setup_browser_navigation()
    view._navigate("budgets")
    route = view.browser_routes[-1]
    page.push_route.side_effect = RuntimeError("navigation failed")
    with pytest.raises(RuntimeError, match="navigation failed"):
        asyncio.run(view._push_browser_route(route))
    assert route not in view._browser_pending_routes


def test_mobile_web_back_uses_browser_history_before_forward(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.route = "/"
    view._setup_browser_navigation()
    view._navigate("budgets")
    budget_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=budget_route))
    view._navigate("accounts")
    account_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=account_route))
    assert view._browser_history is not None
    view._browser_history.back = AsyncMock()

    async def scenario() -> None:
        back = asyncio.create_task(view._browser_back())
        await asyncio.sleep(0)
        assert view.current_screen == "accounts"
        view._browser_route_changed(MagicMock(route=budget_route))
        await back
        assert view.current_screen == "budgets"
        view._browser_route_changed(MagicMock(route=account_route))
        assert view.current_screen == "accounts"

    asyncio.run(scenario())
    view._browser_history.back.assert_awaited_once()


def test_destination_opened_during_browser_back_branches_after_back(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.route = "/"
    view._setup_browser_navigation()
    view._navigate("budgets")
    budget_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=budget_route))
    view._navigate("accounts")
    account_route = view.browser_routes[-1]
    view._browser_route_changed(MagicMock(route=account_route))
    assert view._browser_history is not None
    view._browser_history.back = AsyncMock()

    async def scenario() -> None:
        back = asyncio.create_task(view._browser_back())
        await asyncio.sleep(0)
        view._navigate("reports")
        page.run_task.assert_called_with(view._navigate_after_browser_back, "reports", None)
        deferred = asyncio.create_task(view._navigate_after_browser_back("reports"))
        await asyncio.sleep(0)
        assert view.current_screen == "accounts"
        view._browser_route_changed(MagicMock(route=budget_route))
        await back
        await deferred
        assert view.current_screen == "reports"
        assert view.screen_history == ["home", "budgets", "reports"]
        assert account_route not in view.browser_routes

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "order",
    [
        ("home", "transactions", "budgets", "reports", "settings"),
        ("home", "transactions", "budgets", "reports", "settings", "accounts"),
        ("home", "transactions", "budgets", "reports", "accounts", "categories", "settings"),
        ("settings", "categories", "accounts", "home", "reports", "transactions", "budgets"),
    ],
    ids=["settings-only", "added-destination", "management-and-settings", "reordered"],
)
def test_mobile_groups_follow_the_shared_order_and_navigate_directly(
    navigation: tuple[FinanceView, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    order: tuple[str, ...],
) -> None:
    view, page = navigation
    monkeypatch.setattr(finance, "RAIL_DESTINATIONS", order)
    screens = tuple(screen for screen in order if screen != "settings")
    bottom, overflow = screens[:4], (*screens[4:], "settings")
    view._apply_layout(page.width)
    assert finance.mobile_destination_groups() == (bottom, overflow)
    for button, screen in zip(view.bottom_buttons, bottom, strict=True):
        label = view.nav_rail.destinations[order.index(screen)].label
        assert isinstance(label, str)
        assert button.tooltip == ft.Tooltip(label, exclude_from_semantics=True)
    assert [item.data for item in view.overflow_menu.items if item.data] == list(overflow)
    assert [item.content for item in view.overflow_menu.items if item.data] == [
        view.nav_rail.destinations[order.index(screen)].label for screen in overflow
    ]
    items = tuple(view.overflow_menu.items)
    view._refresh_labels()
    view._apply_layout(page.width)
    assert all(
        before is after for before, after in zip(items, view.overflow_menu.items, strict=True)
    )
    assert view.overflow_menu.visible == bool(overflow)
    header = view.body.controls[0]
    assert isinstance(header, ft.Container) and isinstance(header.content, ft.Row)
    assert header.content.controls == [
        view.navigation_back,
        view.navigation_forward,
        view.header_titles,
    ]
    for item, semantics in zip(
        view.navigation_row.controls, (*view.bottom_semantics, view.more_semantics), strict=True
    ):
        assert isinstance(item, ft.Container)
        assert isinstance(item.content, ft.MergeSemantics)
        assert item.content.content is semantics
    for index, screen in enumerate(bottom):
        click(view.bottom_buttons[index])
        assert view.current_screen == screen
        assert view.screen_host.content is getattr(view, f"{screen}_screen")
        assert view._selected_index() == index
        assert page.bottom_appbar is view.navigation_bar
        assert not view.navigation_back.visible
        assert not view.navigation_forward.visible
    for screen in overflow:
        view._navigate(bottom[0])
        item = next(item for item in view.overflow_menu.items if item.data == screen)
        assert item.on_click is not None
        cast(Callable[[object], object], item.on_click)(MagicMock(control=item))
        assert view.current_screen == screen
        assert view.screen_host.content is getattr(view, f"{screen}_screen")
        assert view._selected_index() == len(bottom)
        assert page.bottom_appbar is view.navigation_bar
        assert not view.navigation_back.visible
        assert not view.navigation_forward.visible
        assert view.overflow_menu.visible
        for width in (800, 390):
            page.width = width
            view._apply_layout(width)
            assert view.current_screen == screen
            assert view.nav_rail.selected_index == order.index(screen)
            assert view.overflow_menu.visible == (width <= 599)
            assert page.bottom_appbar is (view.navigation_bar if width <= 599 else None)
        if screen != "settings":
            view._navigate("settings")
            assert view.overflow_menu.visible
            assert view._back()
            assert view.current_screen == screen
            assert view._selected_index() == len(bottom)
            assert page.bottom_appbar is view.navigation_bar
        assert view._back()
        assert view.current_screen == bottom[0]
        assert view._compact_selected_index() == 0
        assert page.bottom_appbar is view.navigation_bar


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_management_actions_fill_narrow_content_and_align_right_on_wider_screens(
    navigation: tuple[FinanceView, MagicMock], language: str
) -> None:
    view, page = navigation
    view.language = language
    for width, action_width in (
        (320, 280),
        (390, 350),
        (599, 559),
        (600, 470),
        (689, 559),
        (690, 240),
        (1366, 240),
        (390, 350),
    ):
        page.width = width
        view._resized(MagicMock(spec=ft.PageResizeEvent, width=width))
        for screen, panel, controls in (
            ("accounts", view.accounts_panel, [view.account_save]),
            ("categories", view.categories_panel, [view.category_save]),
            ("budgets", None, [view.budgets.add, view.budgets.history]),
        ):
            view._navigate(screen)
            assert view.body.width is not None
            if panel is not None:
                assert panel.border is None and panel.padding is None
                assert panel.width == view.body.width - 40
                assert isinstance(panel.content, ft.Column)
                toolbar = panel.content.controls[0]
            else:
                toolbar = view.budgets.controls[0]
            assert isinstance(toolbar, ft.Container)
            assert toolbar.alignment == ft.Alignment.CENTER_RIGHT
            row = toolbar.content
            assert isinstance(row, ft.Row) and row.wrap
            assert row.alignment == ft.MainAxisAlignment.END
            assert row.controls == controls
            assert row.spacing == row.run_spacing == 8
            assert isinstance(controls[0], ft.Button)
            if screen == "budgets":
                assert isinstance(controls[1], ft.OutlinedButton)
            for button in controls:
                assert button.width == action_width and button.height == 48
                assert button.disabled
        view._navigate("transactions")
        toggle = view.transactions_screen.controls[0]
        assert isinstance(toggle, ft.Container) and isinstance(toggle.content, ft.Row)
        assert toggle.alignment == ft.Alignment.CENTER_RIGHT
        assert toggle.content.controls == [view.operations.reset, view.operations_filter_toggle]
        assert toggle.content.alignment == ft.MainAxisAlignment.END


@pytest.mark.parametrize("width", [320, 800, 1366])
def test_settings_keep_group_frames_and_right_aligned_local_actions(
    navigation: tuple[FinanceView, MagicMock], width: int
) -> None:
    view, page = navigation
    page.width = width
    view._navigate("settings")
    storage, preferences, about = view.settings_screen.controls[:3]
    for section in (storage, preferences, about):
        assert isinstance(section, ft.Container)
        assert section.padding == 16 and section.border_radius == 12
        assert section.border == ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
    assert isinstance(storage, ft.Container) and isinstance(storage.content, ft.Column)
    assert isinstance(preferences, ft.Container) and isinstance(preferences.content, ft.Column)
    for root, groups in (
        (
            storage.content.controls,
            [[view.select_button, view.reload_button], [view.backup_clean_button]],
        ),
        (
            preferences.content.controls,
            [[view.timezone_save], [view.default_account_save], [view.default_fee_save]],
        ),
        (view.advanced_settings.controls or [], [[view.operating_cancel, view.operating_save]]),
    ):
        for buttons in groups:
            row = next(
                item.content
                for item in root
                if isinstance(item, ft.Container)
                and isinstance(item.content, ft.Row)
                and item.content.controls == buttons
            )
            assert row.alignment == ft.MainAxisAlignment.END and row.wrap
    assert view.operating_cancel.on_click == view._reset_operating_currencies
    assert view.operating_save.on_click == view._save_operating_currencies


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize(
    "screen", ["home", "transactions", "accounts", "categories", "reports", "budgets"]
)
def test_mobile_more_settings_opens_separate_view_and_back_restores_screen(
    navigation: tuple[FinanceView, MagicMock], language: str, screen: str
) -> None:
    view, page = navigation
    view.language = language
    view._navigate(screen)
    item = view.overflow_menu.items[-1]
    assert item.content == view.tr("nav_settings")
    assert item.icon == ft.Icons.SETTINGS_OUTLINED
    assert [item.tooltip for item in view.bottom_buttons] == [
        ft.Tooltip(view.tr(key), exclude_from_semantics=True)
        for key in ("nav_home", "nav_transactions", "budget_title", "nav_reports")
    ]
    assert item.on_click is not None
    cast(Callable[[object], object], item.on_click)(MagicMock(control=item))
    assert view.current_screen == "settings"
    assert view.screen_host.content is view.settings_screen
    assert view.screen_history[view.history_index - 1] == screen
    assert page.bottom_appbar is view.navigation_bar
    assert page.floating_action_button is None
    assert view.overflow_menu.visible
    assert not view.navigation_back.visible
    assert not view.navigation_forward.visible
    assert view._compact_selected_index() == 4
    assert view.more_semantics.selected
    assert all(not semantics.selected for semantics in view.bottom_semantics)

    assert view._back()
    assert view.current_screen == screen
    assert page.bottom_appbar is view.navigation_bar
    assert page.floating_action_button is (
        None if screen in {"accounts", "categories"} else view.fab
    )
    assert view.overflow_menu.visible
    if screen in {"accounts", "categories"}:
        assert view._selected_index() == 4
    else:
        assert view._compact_selected_index() == view._selected_index()


@pytest.mark.parametrize("width", [320, 599, 600, 800, 1023, 1024, 1366])
def test_settings_destination_follows_layout_width(
    navigation: tuple[FinanceView, MagicMock], width: int
) -> None:
    view, page = navigation
    page.width = width
    view._apply_layout(width)
    compact = width <= 599
    assert view.overflow_menu.visible == compact
    assert not view.navigation_back.visible
    assert len(view.bottom_buttons) == 4
    assert len(view.nav_rail.destinations) == 7
    assert view.nav_rail.destinations[-1].label == view.tr("nav_settings")
    if compact:
        assert page.bottom_appbar is view.navigation_bar
    else:
        assert page.bottom_appbar is None
        assert isinstance(view.controls[0], ft.Container)
        surface = view.controls[0].content
        assert isinstance(surface, ft.Container)
        assert surface.content is view.nav_rail
        assert surface.bgcolor == ft.Colors.SURFACE
        assert view.controls[0].dark_theme == ft.Theme(
            color_scheme=ft.ColorScheme(surface=DARK_NAVIGATION_SURFACE)
        )
        view.nav_rail.selected_index = 6
        view._rail_changed(MagicMock(control=view.nav_rail))
        assert view.current_screen == "settings"
        assert view.nav_rail.selected_index == 6
        assert not view.overflow_menu.visible and not view.navigation_back.visible


def test_settings_resize_preserves_return_screen_and_unsaved_preferences(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    view._navigate("reports")
    view.report_period.value = "custom"
    view.report_start.occurred.value = "10/"
    view._navigate("settings")
    view.operating_currencies.value = "EUR USD draft"
    view.timezone_choice.value = "Europe/Kyiv"
    for width in (800, 390, 1366, 599, 600, 390):
        page.width = width
        view._apply_layout(width)
        view._refresh_labels()
        assert view.current_screen == "settings"
        assert view.screen_host.content is view.settings_screen
        assert view.nav_rail.selected_index == 6
        assert view._compact_selected_index() == 4
        assert page.bottom_appbar is (view.navigation_bar if width <= 599 else None)
        assert not view.navigation_back.visible
        assert not view.navigation_forward.visible
        assert view.overflow_menu.visible == (width <= 599)
        assert view.operating_currencies.value == "EUR USD draft"
        assert view.timezone_choice.value == "Europe/Kyiv"
        assert view.screen_history[view.history_index - 1] == "reports"
    view._navigate("settings")
    assert view.screen_history[view.history_index - 1] == "reports"
    assert view._back()
    assert view.current_screen == "reports"
    assert view.report_period.value == "custom"
    assert view.report_start.occurred.value == "10/"
    assert page.bottom_appbar is view.navigation_bar


@pytest.mark.parametrize(
    "index,screen", list(enumerate(("home", "transactions", "budgets", "reports")))
)
def test_bottom_bar_still_navigates_to_each_primary_screen(
    navigation: tuple[FinanceView, MagicMock], index: int, screen: str
) -> None:
    view, _ = navigation
    click(view.bottom_buttons[index])
    assert view.current_screen == screen
    assert view._compact_selected_index() == index
    assert view.overflow_menu.visible
    assert view.bottom_semantics[index].selected
    assert not view.more_semantics.selected


def test_bottom_selection_updates_existing_indicators(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, _ = navigation
    contents = tuple(button.content for button in view.bottom_buttons)

    for index in (1, 3, 1, 3):
        click(view.bottom_buttons[index])
        selected = [
            item_index
            for item_index, button in enumerate(view.bottom_buttons)
            if isinstance(button.content, ft.Column)
            and isinstance(button.content.controls[0], ft.Container)
            and button.content.controls[0].gradient is not None
        ]
        assert selected == [index]
        assert all(
            button.content is content
            for button, content in zip(view.bottom_buttons, contents, strict=True)
        )


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("primary", ["home", "transactions", "budgets", "reports"])
@pytest.mark.parametrize("tab", [0, 1])
def test_compact_management_menu_returns_to_previous_primary_screen(
    navigation: tuple[FinanceView, MagicMock], language: str, primary: str, tab: int
) -> None:
    view, page = navigation
    view.language = language
    view._navigate(primary)
    view.budgets.month = date(2026, 2, 1)
    view.budgets.category_id = "Expenses:Food"
    assert view.overflow_menu.visible
    assert not view.more_semantics.selected
    assert isinstance(view.overflow_menu.tooltip, ft.Tooltip)
    assert view.overflow_menu.tooltip.message == view.tr("nav_more")
    assert [item.data for item in view.overflow_menu.items] == [
        "accounts",
        "categories",
        None,
        "settings",
    ]
    assert [item.content for item in view.overflow_menu.items] == [
        view.tr("accounts_title"),
        view.tr("categories_title"),
        None,
        view.tr("nav_settings"),
    ]
    item = view.overflow_menu.items[tab]
    assert item.on_click is not None
    cast(Callable[[object], object], item.on_click)(MagicMock(control=item))
    screen = "categories" if tab else "accounts"
    assert view.current_screen == screen
    assert view.screen_host.content is getattr(view, f"{screen}_screen")
    assert view.screen_history[view.history_index - 1] == primary
    assert view.overflow_menu.visible
    assert view._compact_selected_index() == 4
    assert view.more_semantics.selected
    assert not view.navigation_back.visible
    assert not view.navigation_forward.visible
    assert page.bottom_appbar is view.navigation_bar
    assert page.floating_action_button is None
    view._navigate("settings")
    assert view._back()
    assert view.current_screen == screen
    assert view.screen_history[view.history_index - 1] == primary
    for width in (800, 390, 1366, 599, 600, 390):
        page.width = width
        view._apply_layout(width)
        view._refresh_labels()
        assert view.current_screen == screen
        assert not view.navigation_back.visible
        assert not view.navigation_forward.visible
        assert view.nav_rail.selected_index == 4 + tab
        assert page.bottom_appbar is (view.navigation_bar if width <= 599 else None)
        assert view.overflow_menu.visible == (width <= 599)
        assert view._compact_selected_index() == 4
    assert view._back()
    assert view.current_screen == primary
    assert page.bottom_appbar is view.navigation_bar
    index = view._compact_selected_index()
    assert index is not None
    assert view.bottom_buttons[index].tooltip == ft.Tooltip(
        view.tr("budget_title" if primary == "budgets" else f"nav_{primary}"),
        exclude_from_semantics=True,
    )
    assert view.budgets.month == date(2026, 2, 1)
    assert view.budgets.category_id == "Expenses:Food"


def test_rail_settings_resizes_into_mobile_settings_with_back_navigation(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.width = 1366
    view._apply_layout(page.width)
    view._navigate("categories")
    view.nav_rail.selected_index = 6
    view._rail_changed(MagicMock(control=view.nav_rail))
    page.width = 390
    view._apply_layout(page.width)
    assert view.current_screen == "settings"
    assert not view.navigation_back.visible
    assert not view.navigation_forward.visible
    assert page.bottom_appbar is view.navigation_bar
    assert view._back()
    assert view.current_screen == "categories"
    assert view._selected_index() == 4
    assert not view.navigation_back.visible and page.bottom_appbar is view.navigation_bar


@pytest.mark.parametrize("first,second", [("accounts", "categories"), ("categories", "accounts")])
def test_management_back_restores_previous_secondary_screen_after_resizing(
    navigation: tuple[FinanceView, MagicMock], first: str, second: str
) -> None:
    view, page = navigation
    view._navigate("budgets")
    page.width = 1366
    view._navigate(first)
    first_panel = getattr(view, f"{first}_panel")
    view._navigate(second)
    assert view.screen_history[: view.history_index] == ["home", "budgets", first]
    for width in (390, 800, 1366, 390):
        page.width = width
        view._apply_layout(width)
        assert view.current_screen == second
        assert view.screen_host.content is getattr(view, f"{second}_screen")
        assert view.nav_rail.selected_index == (4 if second == "accounts" else 5)
        assert view._compact_selected_index() == 4
    view._navigate("settings")
    assert view._back()
    assert view.screen_history[: view.history_index] == ["home", "budgets", first]
    assert view._back()
    assert view.current_screen == first
    restored = view.screen_host.content
    assert isinstance(restored, ft.Column) and restored.controls == [first_panel]
    assert not view.navigation_back.visible
    assert view._back()
    assert view.current_screen == "budgets"
    assert page.bottom_appbar is view.navigation_bar
    assert view.screen_history[view.history_index - 1] == "home"


@pytest.mark.parametrize("width", [390, 1366])
def test_budget_report_opens_from_navigation_and_returns_without_losing_state(
    navigation: tuple[FinanceView, MagicMock], width: int
) -> None:
    view, page = navigation
    page.width = width
    budgets = view.budgets
    budgets.month = date(2026, 2, 1)
    budgets.category_id = "Expenses:Food"
    budgets.currency.value = "USD"
    budgets.overspent.value = True
    view._navigate("reports")
    overview = view.report_overview
    view.report_period.value = "custom"
    view.report_start.occurred.value = "10/"
    page.run_task.reset_mock()
    assert view.reports_screen.controls == [view.report_overview]
    assert not hasattr(view, "budgets_button")
    if width <= 599:
        click(view.bottom_buttons[2])
    else:
        view.nav_rail.selected_index = 2
        view._rail_changed(MagicMock(control=view.nav_rail))
    page.run_task.assert_called_once_with(budgets.refresh)
    for resized_width in (width, 800, 390, 1366, 599, 600):
        page.width = resized_width
        view._apply_layout(resized_width)
        view._refresh_labels()
        assert view.current_screen == "budgets"
        assert view.screen_host.content is view.budgets_screen
        assert view.budgets_screen.controls == [budgets]
        assert view.budgets is budgets
        assert budgets.month == date(2026, 2, 1)
        assert budgets.category_id == "Expenses:Food"
        assert budgets.currency.value == "USD"
        assert budgets.overspent.value
        assert view.nav_rail.selected_index == 2
        assert view._compact_selected_index() == 2
        assert view._selected_index() == 2
        assert view.budgets_screen.controls == [budgets]
        assert len(view.nav_rail.destinations) == 7
        assert len(view.bottom_buttons) == 4
        assert view.screen_host.padding == ft.Padding.only(bottom=96)
        assert page.bottom_appbar is (view.navigation_bar if resized_width <= 599 else None)
        view._navigate("home")
        view._navigate("budgets")
        view._navigate("settings")
        assert view.screen_history[view.history_index - 1] == "budgets"
        assert view._compact_selected_index() == 4
        assert view.nav_rail.selected_index == 6
        view._back()
        assert view.current_screen == "budgets"
    page.width = 390
    view._apply_layout(page.width)
    click(view.bottom_buttons[3])
    assert view.current_screen == "reports"
    assert view.screen_host.content is view.reports_screen
    assert view.report_overview is overview
    assert view.report_period.value == "custom"
    assert view.report_start.occurred.value == "10/"
    click(view.bottom_buttons[2])
    assert view.current_screen == "budgets"
    assert budgets.month == date(2026, 2, 1)
    assert budgets.category_id == "Expenses:Food"
    view._navigate("categories")
    assert view.screen_host.content is view.categories_screen
    assert not hasattr(view, "accounting_tabs")
    assert view.nav_rail.selected_index == 5
    assert view._compact_selected_index() == 4


@pytest.mark.parametrize("width", [600, 800, 1366])
@pytest.mark.parametrize(
    "index,screen",
    list(
        enumerate(
            ("home", "transactions", "budgets", "reports", "accounts", "categories", "settings")
        )
    ),
)
def test_sidebar_order_and_screen_selection(
    navigation: tuple[FinanceView, MagicMock], width: int, index: int, screen: str
) -> None:
    view, page = navigation
    page.width = width
    view._apply_layout(width)
    assert [item.label for item in view.nav_rail.destinations] == [
        view.tr(key)
        for key in (
            "nav_home",
            "nav_transactions",
            "budget_title",
            "nav_reports",
            "accounts_title",
            "categories_title",
            "nav_settings",
        )
    ]
    view.nav_rail.selected_index = index
    view._rail_changed(MagicMock(control=view.nav_rail))
    assert view.current_screen == screen
    assert view.nav_rail.selected_index == view._selected_index() == index
    assert not view.overflow_menu.visible
    assert view.budgets_screen.controls == [view.budgets]


@pytest.mark.parametrize("width", [600, 1366])
def test_sidebar_budget_entry_preserves_state(
    navigation: tuple[FinanceView, MagicMock], width: int
) -> None:
    view, page = navigation
    page.width = width
    view._navigate("budgets")
    budgets = view.budgets
    budgets.month = date(2026, 2, 1)
    budgets.category_id = "Expenses:Food"
    budgets.currency.value = "USD"
    budgets.overspent.value = True
    view._navigate("home")
    view.nav_rail.selected_index = 2
    view._rail_changed(MagicMock(control=view.nav_rail))
    assert view.current_screen == "budgets"
    assert view.screen_host.content is view.budgets_screen
    assert view.nav_rail.selected_index == 2
    assert view.budgets is budgets
    assert budgets.month == date(2026, 2, 1)
    assert budgets.category_id == "Expenses:Food"
    assert budgets.currency.value == "USD"
    assert budgets.overspent.value


@pytest.mark.parametrize("width", [390, 599, 600, 800, 1366])
def test_home_keeps_scroll_clearance_for_floating_action(
    navigation: tuple[FinanceView, MagicMock], width: int
) -> None:
    view, page = navigation
    page.width = width
    activity = ft.Text("Final synthetic transaction")
    view.home_activity.controls = [activity]
    for screen in ("home", "transactions", "settings", "home"):
        view._navigate(screen)
    assert view.screen_host.content is view.home_screen
    assert view.screen_host.padding == ft.Padding.only(bottom=96)
    assert view.body.scroll == ft.ScrollMode.AUTO
    assert page.floating_action_button is view.fab
    assert view.home_activity.controls == [activity]


@pytest.mark.parametrize("width", [390, 599, 600, 800, 1366])
@pytest.mark.parametrize("tab", [0, 1])
def test_accounting_keeps_scroll_clearance_for_both_tabs(
    navigation: tuple[FinanceView, MagicMock], width: int, tab: int
) -> None:
    view, page = navigation
    page.width = width
    screen = "categories" if tab else "accounts"
    view._navigate(screen)
    assert view.screen_host.content is getattr(view, f"{screen}_screen")
    expected_padding = ft.Padding.only(bottom=96) if width >= 600 else None
    assert view.screen_host.padding == expected_padding
    assert view.body.scroll == ft.ScrollMode.AUTO
    assert page.floating_action_button is (view.fab if width >= 600 else None)
    view._navigate("settings")
    view._back()
    assert view.current_screen == screen
    assert view.screen_host.padding == expected_padding


@pytest.mark.parametrize("width", [390, 599, 600, 800, 1366])
@pytest.mark.parametrize("diagnostics", [False, True])
def test_settings_clearance_follows_floating_action_visibility(
    navigation: tuple[FinanceView, MagicMock], width: int, diagnostics: bool
) -> None:
    view, page = navigation
    page.width = width
    view.diagnostics.controls = [ft.Text("Synthetic diagnostics")]
    view._navigate("settings")
    if diagnostics:
        view._diagnostics(MagicMock())
    assert view.screen_host.content is view.settings_screen
    assert view.diagnostics.visible == diagnostics
    assert view.screen_host.padding == (ft.Padding.only(bottom=96) if width >= 600 else None)
    assert view.body.scroll == ft.ScrollMode.AUTO
    assert page.floating_action_button is (view.fab if width >= 600 else None)


def test_screen_clearance_updates_on_resize_and_does_not_accumulate(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    for width in (1366, 390, 599, 600, 800, 390):
        page.width = width
        for screen in ("accounts", "settings", "reports", "budgets", "transactions", "home"):
            view._navigate(screen)
            view._apply_layout(width)
            view._refresh_labels()
            expected = (
                ft.Padding.only(bottom=96)
                if screen in {"home", "budgets"}
                or (screen in {"accounts", "settings"} and width >= 600)
                else None
            )
            assert view.screen_host.padding == expected
            view._refresh()
            assert view.screen_host.padding == expected
