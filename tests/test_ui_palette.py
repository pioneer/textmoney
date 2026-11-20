from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import flet as ft
import pytest

from app.domain.ledger import LedgerError
from app.ui.budgets import budget_progress_bar
from app.ui.finance import FinanceView
from app.ui.theme import (
    ACCENT_SURFACE,
    DARK_ACCENT_SURFACE,
    DARK_NAVIGATION_SURFACE,
    NAVIGATION_SURFACE,
    ON_ACCENT_SURFACE,
    PRIMARY_COLOR,
    dark_theme,
    light_theme,
)
from app.ui.tree_style import CategoryMenuAnchor


def test_light_palette_uses_white_content_and_approved_mint() -> None:
    theme = light_theme()
    scheme = theme.color_scheme
    assert scheme is not None
    assert theme.color_scheme_seed == PRIMARY_COLOR == "#016d59"
    assert scheme.surface == scheme.surface_container_low == "#ffffff"
    assert scheme.surface_container_highest == "#ffffff"
    assert scheme.primary == PRIMARY_COLOR and scheme.on_primary == "#ffffff"
    assert scheme.secondary_container == ACCENT_SURFACE == "#d5f3e5"
    assert scheme.on_secondary_container == ON_ACCENT_SURFACE
    assert scheme.on_surface == "#202725"
    assert scheme.on_surface_variant == "#59645f"
    assert scheme.outline_variant == "#d7e7df"
    assert theme.navigation_rail_theme is not None
    assert theme.navigation_rail_theme.bgcolor == NAVIGATION_SURFACE == "#effaf5"
    assert theme.navigation_rail_theme.indicator_color == ft.Colors.TRANSPARENT
    assert theme.bottom_appbar_theme is not None
    assert theme.bottom_appbar_theme.bgcolor == NAVIGATION_SURFACE


def test_summary_surfaces_have_separate_light_and_dark_mint() -> None:
    view = FinanceView()
    summary = view._daily_summary(MagicMock(), date(2026, 11, 5), ())
    for surface in (view.operations_totals, summary):
        assert surface.theme is not None
        assert surface.theme.color_scheme is not None
        assert surface.theme.color_scheme.surface == ACCENT_SURFACE
        assert surface.theme.color_scheme.primary == ON_ACCENT_SURFACE
        assert surface.dark_theme is not None
        assert surface.dark_theme.color_scheme is not None
        assert surface.dark_theme.color_scheme.surface == DARK_ACCENT_SURFACE
        assert surface.theme_mode is None
    assert summary.theme is not None
    assert summary.theme.text_button_theme is not None
    assert summary.theme.text_button_theme.style is not None
    assert summary.theme.text_button_theme.style.bgcolor == ACCENT_SURFACE
    assert isinstance(view.operations_totals.content, ft.Container)
    assert view.operations_totals.content.bgcolor == ft.Colors.SURFACE


def test_dark_palette_keeps_navigation_and_popups_dark() -> None:
    theme = dark_theme()
    assert theme.color_scheme is not None
    assert theme.color_scheme.surface == theme.scaffold_bgcolor == "#101c18"
    assert theme.color_scheme.secondary_container == DARK_ACCENT_SURFACE
    assert theme.navigation_rail_theme is not None
    assert theme.navigation_rail_theme.bgcolor == DARK_NAVIGATION_SURFACE
    assert theme.bottom_appbar_theme is not None
    assert theme.bottom_appbar_theme.bgcolor == DARK_NAVIGATION_SURFACE
    assert theme.dialog_theme is not None and theme.dialog_theme.bgcolor == "#172a21"
    assert theme.dropdown_theme is not None and theme.dropdown_theme.menu_style is not None
    assert theme.dropdown_theme.menu_style.bgcolor == "#172a21"


def test_gradient_decorations_retain_native_actions_and_theme_roles() -> None:
    view = FinanceView()
    assert view.fab.content is view.fab_surface
    assert view.fab.on_click == view._new_transaction
    assert view.fab.disabled
    assert view.fab_surface.opacity == 0.38
    assert isinstance(view.fab_surface.gradient, ft.LinearGradient)
    assert view.fab_surface.gradient.colors == [ft.Colors.PRIMARY, ft.Colors.SECONDARY]
    view._build_navigation()
    selected_icon = view.nav_rail.destinations[0].selected_icon
    assert isinstance(selected_icon, ft.Container)
    assert isinstance(selected_icon.gradient, ft.LinearGradient)
    bottom = view._bottom_content("Home", ft.Icons.HOME, True)
    pill = bottom.controls[0]
    assert isinstance(pill, ft.Container) and isinstance(pill.gradient, ft.LinearGradient)
    assert pill.gradient == selected_icon.gradient
    assert pill.gradient.colors == [
        ft.Colors.PRIMARY_CONTAINER,
        ft.Colors.SECONDARY_CONTAINER,
    ]
    unselected = view._bottom_content("Other", ft.Icons.HOME, False).controls[0]
    assert isinstance(unselected, ft.Container) and unselected.gradient is None


@pytest.mark.parametrize("desktop", [True, False])
def test_fab_cursor_tracks_readiness_without_changing_mobile(desktop: bool) -> None:
    view = FinanceView()
    view._desktop_pointer = desktop
    view._set_fab_ready(True)
    assert not view.fab.disabled
    assert view.fab.mouse_cursor == (ft.MouseCursor.CLICK if desktop else None)
    assert view.fab_surface.opacity == 0.9
    view._set_fab_ready(False)
    assert view.fab.disabled
    assert view.fab.mouse_cursor is None
    assert view.fab_surface.opacity == 0.38


@pytest.mark.parametrize("consumed,expected", [("-5", 0), ("50", 0.5), ("125", 1)])
def test_budget_gradient_preserves_native_value_and_red_overspending(
    consumed: str, expected: float
) -> None:
    progress = budget_progress_bar(Decimal(consumed), False, compact=True)
    assert isinstance(progress, ft.Container) and isinstance(progress.content, ft.ShaderMask)
    native = progress.content.content
    assert isinstance(native, ft.ProgressBar)
    assert native.value == expected
    assert native.height == native.bar_height == 3
    assert progress.content.blend_mode == ft.BlendMode.SRC_IN
    assert native.bgcolor == ft.Colors.TRANSPARENT
    overspent = budget_progress_bar(Decimal(consumed), True)
    assert isinstance(overspent, ft.ProgressBar)
    assert overspent.value == expected and overspent.color == ft.Colors.ERROR


def test_light_popup_surfaces_are_white() -> None:
    theme = light_theme()
    assert theme.dialog_theme is not None and theme.dialog_theme.bgcolor == "#ffffff"
    assert theme.popup_menu_theme is not None and theme.popup_menu_theme.color == "#ffffff"
    assert theme.dropdown_theme is not None and theme.dropdown_theme.menu_style is not None
    assert theme.dropdown_theme.menu_style.bgcolor == "#ffffff"
    assert theme.date_picker_theme is not None
    assert theme.date_picker_theme.bgcolor == "#ffffff"
    assert theme.date_picker_theme.header_bgcolor == "#ffffff"
    assert theme.date_picker_theme.range_picker_bgcolor == "#ffffff"
    assert theme.time_picker_theme is not None and theme.time_picker_theme.bgcolor == "#ffffff"


@pytest.mark.parametrize("background", ["#ffffff", "#223344", None])
def test_category_menu_positioning_preserves_visual_style(background: str | None) -> None:
    style = ft.MenuStyle(bgcolor=background, elevation=5)
    dropdown = ft.Dropdown(menu_height=480, menu_style=style)
    anchor = CategoryMenuAnchor(dropdown)
    assert dropdown.menu_style is not None
    assert dropdown.menu_style.bgcolor == style.bgcolor
    assert dropdown.menu_style.elevation == style.elevation
    assert dropdown.menu_style.alignment is not None
    assert style.alignment is None
    anchor.before_update()
    assert dropdown.menu_style.bgcolor == style.bgcolor
    assert dropdown.menu_style.elevation == style.elevation


@pytest.mark.parametrize(
    "mode,brightness,dark",
    [
        (ft.ThemeMode.LIGHT, ft.Brightness.DARK, False),
        (ft.ThemeMode.DARK, ft.Brightness.LIGHT, True),
        (ft.ThemeMode.SYSTEM, ft.Brightness.LIGHT, False),
        (ft.ThemeMode.SYSTEM, ft.Brightness.DARK, True),
        (ft.ThemeMode.SYSTEM, None, False),
    ],
)
@pytest.mark.parametrize("dark_background", [None, "#223344"])
def test_category_menu_uses_active_account_dropdown_theme(
    monkeypatch: pytest.MonkeyPatch,
    mode: ft.ThemeMode,
    brightness: ft.Brightness | None,
    dark: bool,
    dark_background: str | None,
) -> None:
    page = MagicMock(spec=ft.Page)
    page.theme = light_theme()
    page.dark_theme = ft.Theme(
        dropdown_theme=ft.DropdownTheme(menu_style=ft.MenuStyle(bgcolor=dark_background))
    )
    page.theme_mode = mode
    page.platform_brightness = brightness
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    dropdown = ft.Dropdown(menu_height=480)
    anchor = view._category_menu_anchor(dropdown)
    assert isinstance(anchor, CategoryMenuAnchor)
    assert dropdown.menu_style is not None
    assert dropdown.menu_style.bgcolor == (dark_background if dark else "#ffffff")
    page.theme_mode = ft.ThemeMode.DARK if not dark else ft.ThemeMode.LIGHT
    anchor.before_update()
    assert dropdown.menu_style.bgcolor == (dark_background if not dark else "#ffffff")


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize(
    ("key", "icon", "color"),
    [
        ("no_folder", ft.Icons.INFO_OUTLINE, ft.Colors.ON_SURFACE_VARIANT),
        ("cancelled", ft.Icons.INFO_OUTLINE, ft.Colors.ON_SURFACE_VARIANT),
        ("journal_loaded", ft.Icons.CHECK_CIRCLE_OUTLINE, ft.Colors.PRIMARY),
        ("reloaded", ft.Icons.CHECK_CIRCLE_OUTLINE, ft.Colors.PRIMARY),
        ("saved_backup", ft.Icons.CHECK_CIRCLE_OUTLINE, ft.Colors.PRIMARY),
        ("backups_saved_warning", ft.Icons.ERROR_OUTLINE, ft.Colors.ERROR),
        ("error_timeout", ft.Icons.ERROR_OUTLINE, ft.Colors.ERROR),
        ("error_theme_save", ft.Icons.ERROR_OUTLINE, ft.Colors.ERROR),
    ],
)
def test_status_icons_follow_message_and_language(
    language: str, key: str, icon: ft.IconData, color: ft.Colors
) -> None:
    view = FinanceView()
    view.language = language
    view._set_message(key)
    assert view.message_status.controls == [view.message_icon, view.message]
    assert view.message.value == view.tr(key)
    assert view.message_icon.icon == icon and view.message_icon.color == color
    assert view.message_icon.size == 18 and view.message_status.visible
    view._refresh_labels()
    assert view.message_icon.icon == icon


def test_status_error_icon_resets_after_success() -> None:
    view = FinanceView()
    view._set_message("journal_loaded")
    view._set_error(LedgerError("crud_stale"))
    assert view.message_icon.icon == ft.Icons.ERROR_OUTLINE
    assert view.message_icon.color == ft.Colors.ERROR
    view._refresh_labels()
    assert view.message_icon.icon == ft.Icons.ERROR_OUTLINE
    view._set_message("reloaded")
    assert view.error_message is None
    assert view.message_icon.icon == ft.Icons.CHECK_CIRCLE_OUTLINE


@pytest.mark.parametrize("theme", [light_theme(), dark_theme()], ids=["light", "dark"])
def test_palette_text_contrast(theme: ft.Theme) -> None:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(
            value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
        )

    scheme = theme.color_scheme
    assert scheme is not None
    for foreground, background in (
        (scheme.on_surface, scheme.surface),
        (scheme.on_surface_variant, scheme.surface),
        (scheme.on_surface, scheme.secondary_container),
        (scheme.on_surface_variant, scheme.secondary_container),
        (scheme.primary, scheme.surface),
        (scheme.on_secondary_container, scheme.secondary_container),
        (scheme.on_secondary_container, scheme.primary_container),
        (scheme.on_primary, scheme.primary),
        (scheme.on_primary, scheme.secondary),
        (
            scheme.on_surface,
            NAVIGATION_SURFACE if theme == light_theme() else DARK_NAVIGATION_SURFACE,
        ),
    ):
        assert isinstance(foreground, str) and isinstance(background, str)
        lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
        assert (lighter + 0.05) / (darker + 0.05) >= 4.5, (foreground, background)
