"""Shared layout for category and account hierarchy rows."""

import flet as ft

from app.icons import DISPLAY_SIZE

TREE_ROW_HEIGHT = 40


def compact_tree_theme() -> ft.Theme:
    return ft.Theme(
        icon_theme=ft.IconTheme(size=DISPLAY_SIZE),
        list_tile_theme=ft.ListTileTheme(
            # COMPACT density subtracts 4, leaving the entity label's 8-pixel gap.
            horizontal_spacing=12,
            min_leading_width=DISPLAY_SIZE,
            min_vertical_padding=0,
        ),
    )


def compact_tree_leaf(title: ft.Control, *, data: str | None = None) -> ft.ListTile:
    return ft.ListTile(
        title=title,
        data=data,
        dense=True,
        min_height=TREE_ROW_HEIGHT,
        min_vertical_padding=0,
        visual_density=ft.VisualDensity.COMPACT,
        content_padding=0,
    )


def compact_tree_tile(tile: ft.ExpansionTile) -> ft.ExpansionTile:
    tile.affinity = ft.TileAffinity.LEADING
    tile.dense = True
    tile.min_tile_height = TREE_ROW_HEIGHT
    tile.visual_density = ft.VisualDensity.COMPACT
    tile.tile_padding = 0
    tile.controls_padding = ft.Padding.only(left=16)
    tile.expanded_cross_axis_alignment = ft.CrossAxisAlignment.STRETCH
    tile.shape = ft.RoundedRectangleBorder()
    tile.collapsed_shape = ft.RoundedRectangleBorder()
    return tile
