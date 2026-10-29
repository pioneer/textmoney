"""Searchable single-selection account tree used by budget filters."""

from collections.abc import Awaitable, Callable

import flet as ft

from app.localization import translate
from app.ui.tree_style import compact_tree_theme, compact_tree_tile


class TreePicker(ft.AlertDialog):
    def __init__(
        self,
        page: ft.Page,
        language: str,
        title: str,
        items: dict[str, str],
        selected: str,
        all_label: str,
        on_select: Callable[[str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title, scrollable=True)
        self.owner = page
        self.language = language
        self.items = dict(items)
        self.selected = selected
        self.select = on_select
        self.expanded: dict[str, bool] = {}
        self.search = ft.TextField(
            label=translate(language, "budget_search_tree"), on_change=self._search
        )
        self.listing = ft.Column(spacing=0)
        self.content = ft.Column(
            [
                self.search,
                ft.TextButton(all_label, data="", on_click=self._choose),
                ft.Container(self.listing, theme=compact_tree_theme()),
            ],
            width=420,
            tight=True,
        )
        self.actions = [ft.TextButton(translate(language, "crud_cancel"), on_click=self._cancel)]
        self._render()

    def _render(self) -> None:
        query = (self.search.value or "").strip().casefold()
        matches = {
            key
            for key, label in self.items.items()
            if not query or query in f"{label} {key}".casefold()
        }
        visible = {
            key
            for key in self.items
            if any(
                key == match or key.startswith(match + ":") or match.startswith(key + ":")
                for match in matches
            )
        }

        def node(key: str) -> ft.Control:
            button = ft.TextButton(
                self.items[key],
                data=key,
                tooltip=key,
                on_click=self._choose,
                style=ft.ButtonStyle(
                    padding=0,
                    alignment=ft.Alignment.CENTER_LEFT,
                    visual_density=ft.VisualDensity.COMPACT,
                ),
            )
            children = sorted(
                (item for item in visible if item.rpartition(":")[0] == key),
                key=lambda item: (self.items[item].casefold(), item),
            )
            if not children:
                return ft.Container(content=button, padding=0)

            def changed(event: ft.Event[ft.ExpansionTile]) -> None:
                self.expanded[key] = event.data is True

            return compact_tree_tile(
                ft.ExpansionTile(
                    title=button,
                    data=key,
                    expanded=bool(query)
                    or self.expanded.get(
                        key, self.selected.startswith(key + ":") or ":" not in key
                    ),
                    on_change=changed,
                    controls=[node(child) for child in children],
                    maintain_state=True,
                )
            )

        roots = sorted(
            (key for key in visible if key.rpartition(":")[0] not in visible),
            key=lambda key: (self.items[key].casefold(), key),
        )
        self.listing.controls = [node(key) for key in roots] or [
            ft.Text(translate(self.language, "budget_no_matches"))
        ]

    def _search(self, event: ft.Event[ft.TextField]) -> None:
        self._render()
        self.owner.update()

    async def _choose(self, event: ft.Event[ft.TextButton]) -> None:
        self.owner.pop_dialog()
        await self.select(str(event.control.data))

    def _cancel(self, event: ft.Event[ft.TextButton]) -> None:
        self.owner.pop_dialog()
