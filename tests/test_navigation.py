from unittest.mock import MagicMock

import flet as ft
import pytest

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


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("screen", ["home", "transactions", "accounts", "reports"])
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
        view.tr(f"nav_{name}") for name in ("home", "transactions", "accounts", "reports")
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
    assert page.navigation_bar is view.navigation_bar
    assert page.floating_action_button is view.fab
    assert view.settings_button.visible and not view.settings_back.visible
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
    assert len(view.nav_rail.destinations) == 5
    assert view.nav_rail.destinations[-1].label == view.tr("nav_settings")
    if compact:
        assert page.navigation_bar is view.navigation_bar
    else:
        assert page.navigation_bar is None
        assert isinstance(view.controls[0], ft.Container)
        assert view.controls[0].content is view.nav_rail
        view.nav_rail.selected_index = 4
        view._rail_changed(MagicMock(control=view.nav_rail))
        assert view.current_screen == "settings"
        assert view.nav_rail.selected_index == 4
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
        assert view.nav_rail.selected_index == 4
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
    "index,screen", list(enumerate(("home", "transactions", "accounts", "reports")))
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


def test_rail_settings_resizes_into_mobile_settings_with_back_navigation(
    navigation: tuple[FinanceView, MagicMock],
) -> None:
    view, page = navigation
    page.width = 1366
    view._apply_layout(page.width)
    view._navigate("accounts")
    view.accounting_tabs.selected_index = 1
    view.nav_rail.selected_index = 4
    view._rail_changed(MagicMock(control=view.nav_rail))
    page.width = 390
    view._apply_layout(page.width)
    assert view.current_screen == "settings"
    assert view.settings_back.visible
    assert page.navigation_bar is None
    view._back_from_settings(MagicMock())
    assert view.current_screen == "accounts"
    assert view.accounting_tabs.selected_index == 1
    assert view.navigation_bar.selected_index == 2


@pytest.mark.parametrize("width", [390, 599, 600, 800, 1366])
@pytest.mark.parametrize("tab", [0, 1])
def test_accounting_keeps_scroll_clearance_for_both_tabs(
    navigation: tuple[FinanceView, MagicMock], width: int, tab: int
) -> None:
    view, page = navigation
    page.width = width
    view._navigate("accounts")
    view.accounting_tabs.selected_index = tab
    view._accounting_tab_changed(MagicMock(control=view.accounting_tabs))
    assert view.screen_host.content is view.accounts_screen
    assert view.screen_host.padding == ft.Padding.only(bottom=96)
    assert view.body.scroll == ft.ScrollMode.AUTO
    assert page.floating_action_button is view.fab
    assert view.accounts_panel.visible == (tab == 0)
    assert view.categories_panel.visible == (tab == 1)
    view._open_settings(MagicMock())
    view._back_from_settings(MagicMock())
    assert view.accounting_tabs.selected_index == tab
    assert view.screen_host.padding == ft.Padding.only(bottom=96)


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
        for screen in ("accounts", "settings", "reports", "transactions", "home"):
            view._navigate(screen)
            view._apply_layout(width)
            view._refresh_labels()
            expected = (
                ft.Padding.only(bottom=96)
                if screen == "accounts" or (screen == "settings" and width >= 600)
                else None
            )
            assert view.screen_host.padding == expected
            view._refresh()
            assert view.screen_host.padding == expected
