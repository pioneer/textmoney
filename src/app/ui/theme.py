"""Light-theme surfaces; dark mode keeps the inherited Material palette."""

import flet as ft

MINT_SURFACE = "#e0f1ea"
NAVIGATION_SURFACE = "#f5faf8"


def light_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme_seed="#287a6a",
        scaffold_bgcolor="#ffffff",
        color_scheme=ft.ColorScheme(
            primary="#287a6a",
            on_primary="#ffffff",
            surface="#ffffff",
            surface_container_low="#ffffff",
            surface_container_highest="#ffffff",
            on_surface="#202725",
            on_surface_variant="#59645f",
            outline_variant="#dce5e0",
            secondary_container=MINT_SURFACE,
            on_secondary_container="#205f50",
        ),
        navigation_rail_theme=ft.NavigationRailTheme(
            bgcolor=NAVIGATION_SURFACE,
            indicator_color=MINT_SURFACE,
        ),
        navigation_bar_theme=ft.NavigationBarTheme(
            bgcolor=NAVIGATION_SURFACE,
            indicator_color=MINT_SURFACE,
        ),
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
        ),
    )


def summary_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme=ft.ColorScheme(surface=MINT_SURFACE, primary="#205f50"),
        text_button_theme=ft.TextButtonTheme(style=ft.ButtonStyle(bgcolor=MINT_SURFACE)),
    )
