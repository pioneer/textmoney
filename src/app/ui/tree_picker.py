"""Searchable single-selection account tree used by budget filters."""

from collections.abc import Awaitable, Callable

import flet as ft

from app.localization import translate


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
                self.listing,
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
            )
            children = sorted(
                (item for item in visible if item.rpartition(":")[0] == key),
                key=lambda item: (self.items[item].casefold(), item),
            )
            if not children:
                return ft.Container(content=button, padding=ft.Padding.only(left=24))

            def changed(event: ft.Event[ft.ExpansionTile]) -> None:
                self.expanded[key] = event.data is True

            return ft.ExpansionTile(
                title=button,
                data=key,
                expanded=bool(query)
                or self.expanded.get(key, self.selected.startswith(key + ":") or ":" not in key),
                on_change=changed,
                controls=[node(child) for child in children],
                controls_padding=ft.Padding.only(left=16),
                expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
                maintain_state=True,
                dense=True,
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
