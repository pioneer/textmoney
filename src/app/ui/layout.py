"""Shared layouts and row feedback for primary screens."""

from collections.abc import Callable

import flet as ft


class HoverRow(ft.Container):
    def __init__(
        self,
        content: ft.Control,
        *,
        enabled: Callable[[], bool],
        padding: ft.PaddingValue | None = None,
    ) -> None:
        super().__init__(content=content, padding=padding, on_hover=self._hovered)
        self._hover_enabled = enabled

    def before_update(self) -> None:
        super().before_update()
        if not self._hover_enabled():
            self.bgcolor = None

    def _hovered(self, event: ft.Event[ft.Container]) -> None:
        color = (
            ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE)
            if event.data is True and self._hover_enabled()
            else None
        )
        if self.bgcolor != color:
            self.bgcolor = color
            self.update()


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


def settings_field(row: ft.Row) -> ft.Container:
    return ft.Container(content=row, width=480)


def settings_section(content: ft.Control, *, expandable: bool = False) -> ft.Container:
    return ft.Container(
        content=content,
        padding=0 if expandable else 16,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=12,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS if expandable else None,
    )


def settings_expander(tile: ft.ExpansionTile) -> ft.Container:
    tile.tile_padding = 16
    tile.shape = ft.RoundedRectangleBorder()
    tile.collapsed_shape = ft.RoundedRectangleBorder()
    return settings_section(tile, expandable=True)


def month_navigation(
    previous: ft.IconButton, heading: ft.Control, following: ft.IconButton
) -> ft.Row:
    for button in (previous, following):
        button.icon_size = 20
        button.padding = 14
        button.visual_density = ft.VisualDensity.STANDARD
        button.size_constraints = ft.BoxConstraints(min_width=48, min_height=48)
    return ft.Row([previous, heading, following], spacing=8)
