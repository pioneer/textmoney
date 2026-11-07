from datetime import date
from unittest.mock import MagicMock

import flet as ft
import pytest

from app.domain.ledger import LedgerError
from app.ui.finance import FinanceView
from app.ui.theme import MINT_SURFACE, NAVIGATION_SURFACE, light_theme
from app.ui.tree_style import CategoryMenuAnchor


def test_light_palette_uses_white_content_and_restrained_mint() -> None:
    theme = light_theme()
    scheme = theme.color_scheme
    assert scheme is not None
    assert scheme.surface == scheme.surface_container_low == "#ffffff"
    assert scheme.surface_container_highest == "#ffffff"
    assert scheme.primary == "#287a6a" and scheme.on_primary == "#ffffff"
    assert scheme.on_surface == "#202725"
    assert scheme.on_surface_variant == "#59645f"
    assert scheme.outline_variant == "#dce5e0"
    assert theme.navigation_rail_theme is not None
    assert theme.navigation_bar_theme is not None
    for navigation in (theme.navigation_rail_theme, theme.navigation_bar_theme):
        assert navigation.bgcolor == NAVIGATION_SURFACE
        assert navigation.indicator_color == MINT_SURFACE


def test_summary_surfaces_are_scoped_without_dark_overrides() -> None:
    view = FinanceView()
    summary = view._daily_summary(MagicMock(), date(2026, 11, 5), ())
    for surface in (view.operations_totals, summary):
        assert surface.theme is not None
        assert surface.theme.color_scheme is not None
        assert surface.theme.color_scheme.surface == MINT_SURFACE
        assert surface.theme.color_scheme.primary == "#205f50"
        assert surface.dark_theme == ft.Theme()
        assert surface.theme_mode is None
    assert summary.theme is not None
    assert summary.theme.text_button_theme is not None
    assert summary.theme.text_button_theme.style is not None
    assert summary.theme.text_button_theme.style.bgcolor == MINT_SURFACE
    assert isinstance(view.operations_totals.content, ft.Container)
    assert view.operations_totals.content.bgcolor == ft.Colors.SURFACE


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


def test_palette_text_contrast() -> None:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(
            value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
        )

    for foreground, background in (
        ("#202725", "#ffffff"),
        ("#59645f", "#ffffff"),
        ("#202725", MINT_SURFACE),
        ("#59645f", MINT_SURFACE),
        ("#287a6a", "#ffffff"),
        ("#205f50", MINT_SURFACE),
        ("#ffffff", "#287a6a"),
    ):
        lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
        assert (lighter + 0.05) / (darker + 0.05) >= 4.5
