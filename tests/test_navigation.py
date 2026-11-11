from collections.abc import Callable
from datetime import date
from typing import cast
from unittest.mock import MagicMock

import flet as ft
import pytest
from form_controls import click

from app.ui import finance
from app.ui.finance import FinanceView


@pytest.fixture
def navigation(monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.startup_complete = True
    view._apply_layout(page.width)
    return view, page


@pytest.mark.parametrize(
    "order",
    [
        ("home", "transactions", "budgets", "reports", "settings"),
        ("home", "transactions", "budgets", "reports", "settings", "accounts"),
        ("home", "transactions", "budgets", "reports", "accounts", "categories", "settings"),
        ("settings", "categories", "accounts", "home", "reports", "transactions", "budgets"),
    ],
    ids=["no-overflow", "added-destination", "two-overflow", "reordered"],
)
def test_mobile_groups_follow_the_shared_order_and_navigate_directly(
    navigation: tuple[FinanceView, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    order: tuple[str, ...],
) -> None:
    view, page = navigation
    monkeypatch.setattr(finance, "RAIL_DESTINATIONS", order)
    screens = tuple(screen for screen in order if screen != "settings")
    bottom, overflow = screens[:4], screens[4:]
    view._apply_layout(page.width)
    assert finance.mobile_destination_groups() == (bottom, overflow)
    assert [item.label for item in view.navigation_bar.destinations] == [
        view.nav_rail.destinations[order.index(screen)].label for screen in bottom
    ]
    assert [item.data for item in view.overflow_menu.items] == list(overflow)
    assert [item.content for item in view.overflow_menu.items] == [
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
    assert header.content.controls[-2:] == [view.overflow_menu, view.settings_button]
    assert view.overflow_menu.content is None
    assert isinstance(view.overflow_menu.icon, ft.Icon)
    assert view.overflow_menu.icon.icon == ft.Icons.MORE_VERT
    assert view.overflow_menu.icon.semantics_label == view.tr("nav_more")
    for index, screen in enumerate(bottom):
        view.navigation_bar.selected_index = index
        view._bar_changed(MagicMock(control=view.navigation_bar))
        assert view.current_screen == screen
        assert view.screen_host.content is getattr(view, f"{screen}_screen")
        assert view._selected_index() == index
        assert page.navigation_bar is view.navigation_bar
        assert not view.management_back.visible
    for screen in overflow:
        view._navigate(bottom[0])
        item = next(item for item in view.overflow_menu.items if item.data == screen)
        assert item.on_click is not None
        cast(Callable[[object], object], item.on_click)(MagicMock(control=item))
        assert view.current_screen == screen
        assert view.screen_host.content is getattr(view, f"{screen}_screen")
        assert view._selected_index() is None
        assert page.navigation_bar is None
        assert view.management_back.visible and view.overflow_menu.visible
        for width in (800, 390):
            page.width = width
            view._apply_layout(width)
            assert view.current_screen == screen
            assert view.nav_rail.selected_index == order.index(screen)
            assert view.overflow_menu.visible == (width <= 599)
            assert page.navigation_bar is None
        view._open_settings(MagicMock())
        assert not view.overflow_menu.visible
        view._back_from_settings(MagicMock())
        assert view.current_screen == screen
        assert view._selected_index() is None and page.navigation_bar is None
        click(view.management_back)
        assert view.current_screen == bottom[0]
        assert view.navigation_bar.selected_index == 0
        assert page.navigation_bar is view.navigation_bar


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_management_toolbars_share_unframed_gutters_and_wrapping_actions(
    navigation: tuple[FinanceView, MagicMock], language: str
) -> None:
    view, page = navigation
    view.language = language
    for width in (320, 390, 800, 1366):
        page.width = width
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
            for button in controls:
                assert isinstance(button, ft.Button)
                assert button.width == 240 and button.height == 48
                assert button.width <= view.body.width - 40
        view._navigate("transactions")
        toggle = view.transactions_screen.controls[0]
        assert isinstance(toggle, ft.Container) and isinstance(toggle.content, ft.Row)
        assert toggle.alignment == ft.Alignment.CENTER_RIGHT
        assert toggle.content.controls == [view.operations_filter_toggle]
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
def test_mobile_settings_gear_opens_separate_view_and_back_restores_screen(
    navigation: tuple[FinanceView, MagicMock], language: str, screen: str
) -> None:
    view, page = navigation
    view.language = language
    view._navigate(screen)
    assert view.settings_button.visible and not view.settings_back.visible
    assert view.settings_button.icon == ft.Icons.SETTINGS_OUTLINED
    assert view.settings_button.icon_size == 20
    assert view.settings_button.size_constraints is None
    assert view.settings_button.tooltip == view.tr("nav_settings")
    assert view.settings_button.on_click == view._open_settings
    assert [item.label for item in view.navigation_bar.destinations] == [
        view.tr(key) for key in ("nav_home", "nav_transactions", "budget_title", "nav_reports")
    ]
    view._open_settings(MagicMock())
    assert view.current_screen == "settings"
    assert view.screen_host.content is view.settings_screen
    assert view.settings_return_screen == screen
    assert page.navigation_bar is None
    assert page.floating_action_button is None
    assert not view.settings_button.visible and view.settings_back.visible
    assert view.settings_back.tooltip == view.tr("nav_back")
    assert view.settings_back.icon == ft.Icons.ARROW_BACK
    assert view.settings_back.on_click == view._back_from_settings
    assert 0 <= view.navigation_bar.selected_index < 4

    view._back_from_settings(MagicMock())
    assert view.current_screen == screen
    assert page.navigation_bar is (
        None if screen in {"accounts", "categories"} else view.navigation_bar
    )
    assert page.floating_action_button is (
        None if screen in {"accounts", "categories"} else view.fab
    )
    assert view.settings_button.visible and not view.settings_back.visible
    if screen in {"accounts", "categories"}:
        assert view._selected_index() is None
    else:
        assert view.navigation_bar.selected_index == view._selected_index()


@pytest.mark.parametrize("width", [320, 599, 600, 800, 1023, 1024, 1366])
def test_settings_destination_follows_layout_width(
    navigation: tuple[FinanceView, MagicMock], width: int
) -> None:
    view, page = navigation
    page.width = width
    view._apply_layout(width)
    compact = width <= 599
    assert view.settings_button.visible == compact
    assert not view.settings_back.visible
    assert len(view.navigation_bar.destinations) == 4
    assert len(view.nav_rail.destinations) == 7
    assert view.nav_rail.destinations[-1].label == view.tr("nav_settings")
    if compact:
        assert page.navigation_bar is view.navigation_bar
    else:
        assert page.navigation_bar is None
        assert isinstance(view.controls[0], ft.Container)
        surface = view.controls[0].content
        assert isinstance(surface, ft.Container)
        assert surface.content is view.nav_rail
        assert surface.bgcolor == ft.Colors.SURFACE
        assert view.controls[0].dark_theme == ft.Theme()
        view.nav_rail.selected_index = 6
        view._rail_changed(MagicMock(control=view.nav_rail))
        assert view.current_screen == "settings"
        assert view.nav_rail.selected_index == 6
        assert not view.settings_button.visible and not view.settings_back.visible


def test_settings_resize_preserves_return_screen_and_unsaved_preferences(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    view._navigate("reports")
    view.report_period.value = "custom"
    view.report_start.occurred.value = "10/"
    view._open_settings(MagicMock())
    view.operating_currencies.value = "EUR USD draft"
    view.timezone_choice.value = "Europe/Kyiv"
    for width in (800, 390, 1366, 599, 600, 390):
        page.width = width
        view._apply_layout(width)
        view._refresh_labels()
        assert view.current_screen == "settings"
        assert view.screen_host.content is view.settings_screen
        assert view.nav_rail.selected_index == 6
        assert view.navigation_bar.selected_index == 3
        assert page.navigation_bar is None
        assert view.settings_back.visible == (width <= 599)
        assert not view.settings_button.visible
        assert view.operating_currencies.value == "EUR USD draft"
        assert view.timezone_choice.value == "Europe/Kyiv"
        assert view.settings_return_screen == "reports"
    view._open_settings(MagicMock())
    assert view.settings_return_screen == "reports"
    view._back_from_settings(MagicMock())
    assert view.current_screen == "reports"
    assert view.report_period.value == "custom"
    assert view.report_start.occurred.value == "10/"
    assert page.navigation_bar is view.navigation_bar


@pytest.mark.parametrize(
    "index,screen", list(enumerate(("home", "transactions", "budgets", "reports")))
)
def test_bottom_bar_still_navigates_to_each_primary_screen(
    navigation: tuple[FinanceView, MagicMock], index: int, screen: str
) -> None:
    view, _ = navigation
    view.navigation_bar.selected_index = index
    view._bar_changed(MagicMock(control=view.navigation_bar))
    assert view.current_screen == screen
    assert view.navigation_bar.selected_index == index
    assert view.settings_button.visible


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
    assert view.overflow_menu.content is None
    assert isinstance(view.overflow_menu.icon, ft.Icon)
    assert view.overflow_menu.icon.icon == ft.Icons.MORE_VERT
    assert view.overflow_menu.icon.semantics_label == view.tr("nav_more")
    assert view.overflow_menu.tooltip == view.tr("nav_more")
    assert [item.data for item in view.overflow_menu.items] == ["accounts", "categories"]
    assert [item.content for item in view.overflow_menu.items] == [
        view.tr("accounts_title"),
        view.tr("categories_title"),
    ]
    assert view.settings_button.visible
    item = view.overflow_menu.items[tab]
    assert item.on_click is not None
    cast(Callable[[object], object], item.on_click)(MagicMock(control=item))
    screen = "categories" if tab else "accounts"
    assert view.current_screen == screen
    assert view.screen_host.content is getattr(view, f"{screen}_screen")
    assert view.management_history == [primary]
    assert view.overflow_menu.visible
    assert view._compact_selected_index() is None
    assert view.management_back.visible and view.settings_button.visible
    assert page.navigation_bar is None and page.floating_action_button is None
    view._open_settings(MagicMock())
    view._back_from_settings(MagicMock())
    assert view.current_screen == screen
    assert view.management_history == [primary]
    for width in (800, 390, 1366, 599, 600, 390):
        page.width = width
        view._apply_layout(width)
        view._refresh_labels()
        assert view.current_screen == screen
        assert view.management_back.visible == (width <= 599)
        assert view.nav_rail.selected_index == 4 + tab
        assert page.navigation_bar is None
        assert view.overflow_menu.visible == (width <= 599)
        assert view._compact_selected_index() is None
    click(view.management_back)
    assert view.current_screen == primary
    assert page.navigation_bar is view.navigation_bar
    assert view.navigation_bar.destinations[view.navigation_bar.selected_index].label == view.tr(
        "budget_title" if primary == "budgets" else f"nav_{primary}"
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
    assert view.settings_back.visible
    assert page.navigation_bar is None
    view._back_from_settings(MagicMock())
    assert view.current_screen == "categories"
    assert view._selected_index() is None
    assert view.management_back.visible and page.navigation_bar is None


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
    assert view.management_history == ["budgets", first]
    for width in (390, 800, 1366, 390):
        page.width = width
        view._apply_layout(width)
        assert view.current_screen == second
        assert view.screen_host.content is getattr(view, f"{second}_screen")
        assert view.nav_rail.selected_index == (4 if second == "accounts" else 5)
        assert view._compact_selected_index() is None
    view._open_settings(MagicMock())
    view._back_from_settings(MagicMock())
    assert view.management_history == ["budgets", first]
    click(view.management_back)
    assert view.current_screen == first
    restored = view.screen_host.content
    assert isinstance(restored, ft.Column) and restored.controls == [first_panel]
    assert view.management_back.visible
    click(view.management_back)
    assert view.current_screen == "budgets"
    assert page.navigation_bar is view.navigation_bar
    assert not view.management_history


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
        view.navigation_bar.selected_index = 2
        view._bar_changed(MagicMock(control=view.navigation_bar))
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
        assert view.navigation_bar.selected_index == 2
        assert view._selected_index() == 2
        assert view.budgets_screen.controls == [budgets]
        assert len(view.nav_rail.destinations) == 7
        assert len(view.navigation_bar.destinations) == 4
        assert view.screen_host.padding == ft.Padding.only(bottom=96)
        assert page.navigation_bar is (view.navigation_bar if resized_width <= 599 else None)
        view._navigate("home")
        view._navigate("budgets")
        view._open_settings(MagicMock())
        assert view.settings_return_screen == "budgets"
        assert view.navigation_bar.selected_index == 2
        assert view.nav_rail.selected_index == 6
        view._back_from_settings(MagicMock())
        assert view.current_screen == "budgets"
    page.width = 390
    view._apply_layout(page.width)
    view.navigation_bar.selected_index = 3
    view._bar_changed(MagicMock(control=view.navigation_bar))
    assert view.current_screen == "reports"
    assert view.screen_host.content is view.reports_screen
    assert view.report_overview is overview
    assert view.report_period.value == "custom"
    assert view.report_start.occurred.value == "10/"
    view.navigation_bar.selected_index = 2
    view._bar_changed(MagicMock(control=view.navigation_bar))
    assert view.current_screen == "budgets"
    assert budgets.month == date(2026, 2, 1)
    assert budgets.category_id == "Expenses:Food"
    view._navigate("categories")
    assert view.screen_host.content is view.categories_screen
    assert not hasattr(view, "accounting_tabs")
    assert view.nav_rail.selected_index == 5
    assert view.navigation_bar.selected_index == 2


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
    view._open_settings(MagicMock())
    view._back_from_settings(MagicMock())
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
    view._open_settings(MagicMock())
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
