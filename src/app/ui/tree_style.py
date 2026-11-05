"""Shared layout for category and account hierarchy rows."""

import flet as ft
import flet.canvas as fc

from app.icons import DISPLAY_SIZE

TREE_ROW_HEIGHT = 40
TREE_MENU_ROW_HEIGHT = 48
TREE_INDENT = 16


class CategoryConnectorRow(fc.Canvas):
    def __init__(
        self,
        content: ft.Container,
        continued: tuple[bool, ...],
        *,
        header_height: float | None = None,
        branch_length: float = TREE_INDENT / 2,
    ) -> None:
        super().__init__(content=content, height=content.height, on_resize=self._resized)
        self.continued = continued
        self._header_height = header_height
        self._branch_length = branch_length
        self._draw(float(content.height or header_height or TREE_MENU_ROW_HEIGHT))

    def _draw(self, height: float) -> None:
        self._row_height = height
        center = min(self._header_height or height, height) / 2
        shapes: list[fc.Shape] = []
        paint = ft.Paint(
            color=ft.Colors.OUTLINE_VARIANT,
            stroke_width=1.25,
            style=ft.PaintingStyle.STROKE,
        )
        for level, continued in enumerate(self.continued):
            position = TREE_INDENT * level + TREE_INDENT / 4
            if level < len(self.continued) - 1:
                if continued:
                    shapes.append(fc.Line(position, 0, position, height, paint))
                continue
            end = position + self._branch_length
            if continued:
                shapes.extend(
                    [
                        fc.Line(position, 0, position, height, paint),
                        fc.Line(position, center, end, center, paint),
                    ]
                )
            else:
                radius = min(TREE_INDENT * 3 / 16, center)
                shapes.append(
                    fc.Path(
                        elements=[
                            fc.Path.MoveTo(position, 0),
                            fc.Path.LineTo(position, center - radius),
                            fc.Path.QuadraticTo(position, center, position + radius, center),
                            fc.Path.LineTo(end, center),
                        ],
                        paint=paint,
                    )
                )
        self.shapes = shapes

    def _resized(self, event: fc.CanvasResizeEvent) -> None:
        if event.height > 0 and event.height != self._row_height:
            self._draw(event.height)
            self.update()


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
