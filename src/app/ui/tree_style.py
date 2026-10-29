"""Shared layout for category and account hierarchy rows."""

import flet as ft


def compact_tree_theme() -> ft.Theme:
    return ft.Theme(list_tile_theme=ft.ListTileTheme(horizontal_spacing=8, min_leading_width=24))


def compact_tree_tile(tile: ft.ExpansionTile) -> ft.ExpansionTile:
    tile.affinity = ft.TileAffinity.LEADING
    tile.dense = True
    tile.min_tile_height = 40
    tile.visual_density = ft.VisualDensity.COMPACT
    tile.tile_padding = 0
    tile.controls_padding = ft.Padding.only(left=16)
    tile.expanded_cross_axis_alignment = ft.CrossAxisAlignment.STRETCH
    tile.shape = ft.RoundedRectangleBorder()
    tile.collapsed_shape = ft.RoundedRectangleBorder()
    return tile
