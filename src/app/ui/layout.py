"""Shared action and period layouts for primary screens."""

import flet as ft


def action_toolbar(*controls: ft.Control) -> ft.Container:
    return ft.Container(
        ft.Row(
            list(controls),
            alignment=ft.MainAxisAlignment.END,
            wrap=True,
            spacing=8,
            run_spacing=8,
        ),
        alignment=ft.Alignment.CENTER_RIGHT,
    )


def month_navigation(
    previous: ft.IconButton, heading: ft.Control, following: ft.IconButton
) -> ft.Row:
    for button in (previous, following):
        button.icon_size = 20
        button.padding = 14
        button.visual_density = ft.VisualDensity.STANDARD
        button.size_constraints = ft.BoxConstraints(min_width=48, min_height=48)
    return ft.Row([previous, heading, following], spacing=8)
