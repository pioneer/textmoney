"""Shared mint surfaces and semantic gradients for both appearances."""

import flet as ft

ACCENT_SURFACE = "#d5f3e5"
NAVIGATION_SURFACE = "#effaf5"
PRIMARY_COLOR = "#016d59"
ON_ACCENT_SURFACE = "#205f50"
DARK_ACCENT_SURFACE = "#203f32"
DARK_NAVIGATION_SURFACE = "#14241e"


def action_gradient() -> ft.LinearGradient:
    return ft.LinearGradient(colors=[ft.Colors.PRIMARY, ft.Colors.SECONDARY])


def selection_gradient() -> ft.LinearGradient:
    return ft.LinearGradient(colors=[ft.Colors.PRIMARY_CONTAINER, ft.Colors.SECONDARY_CONTAINER])


def desktop_pointer(page: ft.Page, *, mobile_preview: bool = False) -> bool:
    return not mobile_preview and page.platform in {
        ft.PagePlatform.LINUX,
        ft.PagePlatform.WINDOWS,
        ft.PagePlatform.MACOS,
    }


def click_cursor() -> dict[ft.ControlState, ft.MouseCursor]:
    return {
        ft.ControlState.DISABLED: ft.MouseCursor.BASIC,
        ft.ControlState.DEFAULT: ft.MouseCursor.CLICK,
    }


def _desktop_actions(theme: ft.Theme) -> ft.Theme:
    style = ft.ButtonStyle(mouse_cursor=click_cursor())
    theme.button_theme = ft.ButtonTheme(style=style)
    theme.text_button_theme = ft.TextButtonTheme(style=style)
    theme.filled_button_theme = ft.FilledButtonTheme(style=style)
    theme.outlined_button_theme = ft.OutlinedButtonTheme(style=style)
    theme.icon_button_theme = ft.IconButtonTheme(style=style)
    if theme.popup_menu_theme is not None:
        theme.popup_menu_theme.mouse_cursor = click_cursor()
    if theme.floating_action_button_theme is not None:
        theme.floating_action_button_theme.hover_color = ft.Colors.with_opacity(
            0.7, ft.Colors.ON_PRIMARY
        )
        theme.floating_action_button_theme.hover_elevation = 12
    return theme


def light_theme(*, desktop: bool = False) -> ft.Theme:
    theme = ft.Theme(
        color_scheme_seed=PRIMARY_COLOR,
        scaffold_bgcolor="#ffffff",
        color_scheme=ft.ColorScheme(
            primary=PRIMARY_COLOR,
            on_primary="#ffffff",
            secondary="#00836a",
            on_secondary="#ffffff",
            primary_container="#bcebd6",
            on_primary_container=ON_ACCENT_SURFACE,
            surface="#ffffff",
            surface_container_low="#ffffff",
            surface_container_highest="#ffffff",
            on_surface="#202725",
            on_surface_variant="#59645f",
            outline_variant="#d7e7df",
            secondary_container=ACCENT_SURFACE,
            on_secondary_container=ON_ACCENT_SURFACE,
        ),
        navigation_rail_theme=ft.NavigationRailTheme(
            bgcolor=NAVIGATION_SURFACE,
            indicator_color=ft.Colors.TRANSPARENT,
        ),
        bottom_appbar_theme=ft.BottomAppBarTheme(bgcolor=NAVIGATION_SURFACE),
        dialog_theme=ft.DialogTheme(bgcolor="#ffffff"),
        dropdown_theme=ft.DropdownTheme(menu_style=ft.MenuStyle(bgcolor="#ffffff")),
        popup_menu_theme=ft.PopupMenuTheme(color="#ffffff"),
        date_picker_theme=ft.DatePickerTheme(
            bgcolor="#ffffff",
            header_bgcolor="#ffffff",
            range_picker_bgcolor="#ffffff",
            range_picker_header_bgcolor="#ffffff",
        ),
        time_picker_theme=ft.TimePickerTheme(bgcolor="#ffffff"),
        floating_action_button_theme=ft.FloatingActionButtonTheme(
            bgcolor=ft.Colors.PRIMARY,
            foreground_color=ft.Colors.ON_PRIMARY,
            size_constraints=ft.BoxConstraints(min_width=56, min_height=56, max_height=56),
        ),
    )
    return _desktop_actions(theme) if desktop else theme


def dark_theme(*, desktop: bool = False) -> ft.Theme:
    popup_surface = "#172a21"
    theme = ft.Theme(
        color_scheme_seed="#81c6b3",
        surfaces=ft.ThemeSurfaces.TONAL,
        scaffold_bgcolor="#101c18",
        color_scheme=ft.ColorScheme(
            primary="#8ddfc2",
            on_primary="#00382d",
            secondary="#b4eed9",
            on_secondary="#00382d",
            primary_container="#2a5140",
            on_primary_container="#bcebd6",
            secondary_container=DARK_ACCENT_SURFACE,
            on_secondary_container="#bcebd6",
            surface="#101c18",
            surface_container_low=popup_surface,
            surface_container_highest="#294237",
            on_surface="#e3eee8",
            on_surface_variant="#b5c8be",
            outline_variant="#344d41",
        ),
        navigation_rail_theme=ft.NavigationRailTheme(
            bgcolor=DARK_NAVIGATION_SURFACE,
            indicator_color=ft.Colors.TRANSPARENT,
        ),
        bottom_appbar_theme=ft.BottomAppBarTheme(bgcolor=DARK_NAVIGATION_SURFACE),
        dialog_theme=ft.DialogTheme(bgcolor=popup_surface),
        dropdown_theme=ft.DropdownTheme(menu_style=ft.MenuStyle(bgcolor=popup_surface)),
        popup_menu_theme=ft.PopupMenuTheme(color=popup_surface),
        date_picker_theme=ft.DatePickerTheme(
            bgcolor=popup_surface,
            header_bgcolor=popup_surface,
            range_picker_bgcolor=popup_surface,
            range_picker_header_bgcolor=popup_surface,
        ),
        time_picker_theme=ft.TimePickerTheme(bgcolor=popup_surface),
        floating_action_button_theme=ft.FloatingActionButtonTheme(
            bgcolor=ft.Colors.PRIMARY,
            foreground_color=ft.Colors.ON_PRIMARY,
            size_constraints=ft.BoxConstraints(min_width=56, min_height=56, max_height=56),
        ),
    )
    return _desktop_actions(theme) if desktop else theme


def summary_theme(*, dark: bool = False) -> ft.Theme:
    surface = DARK_ACCENT_SURFACE if dark else ACCENT_SURFACE
    foreground = "#bcebd6" if dark else ON_ACCENT_SURFACE
    return ft.Theme(
        color_scheme=ft.ColorScheme(
            surface=surface,
            primary=foreground,
        ),
        text_button_theme=ft.TextButtonTheme(style=ft.ButtonStyle(bgcolor=surface)),
    )
