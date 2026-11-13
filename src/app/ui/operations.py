"""A single bounded Operations page with quiet bidirectional navigation."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

import flet as ft

from app.domain.operations import (
    OperationPage,
    PageDirection,
    matching_transactions,
    operation_page,
    transaction_key,
)

if TYPE_CHECKING:
    from app.ui.finance import FinanceView


class OperationsView:
    def __init__(self, host: "FinanceView") -> None:
        self.host = host
        self.page = OperationPage()
        self.key: tuple[object, ...] | None = None
        self.generation = 0
        self.busy = False
        self.failed_direction: PageDirection | None = None
        self.history = ft.Column(spacing=0, key="operations-history")
        self.error = ft.Text(visible=False, color=ft.Colors.ERROR)
        self.retry = ft.TextButton(on_click=self._retry, visible=False)
        self.newer = [
            ft.TextButton(icon=ft.Icons.CHEVRON_LEFT, on_click=self._newer) for _ in range(2)
        ]
        self.older = [
            ft.TextButton(icon=ft.Icons.CHEVRON_RIGHT, on_click=self._older) for _ in range(2)
        ]
        self.latest = [ft.TextButton(on_click=self._latest) for _ in range(2)]
        self.end = ft.Text(size=12, color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
        self.loading = [
            ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False) for _ in range(2)
        ]
        self.navigation = [
            ft.Row(
                [self.newer[index], self.latest[index], self.older[index], self.loading[index]],
                alignment=ft.MainAxisAlignment.CENTER,
                wrap=True,
                spacing=0,
            )
            for index in range(2)
        ]
        self.status = ft.Row(
            [self.end, self.error, self.retry],
            alignment=ft.MainAxisAlignment.CENTER,
            wrap=True,
        )

    def _key(self) -> tuple[object, ...]:
        journal = self.host.journal
        return (
            id(journal),
            id(journal.ledger) if journal else None,
            bool(journal and journal.ready),
            self.host.language,
            self.host.icon_revision,
            self.host.operations_account.value,
            self.host.operations_category.value,
        )

    def sync(self) -> None:
        key = self._key()
        if key == self.key:
            return
        previous = self.key
        self.key = key
        self.generation += 1
        self.busy = False
        self.failed_direction = None
        self.error.visible = False
        journal = self.host.journal
        if journal is None or not journal.ready:
            self.page = OperationPage()
            self.history.controls = [ft.Text(self.host.tr("transactions_empty"))]
            self._labels()
            return
        account, category = self._filters()
        cursor = None
        if (
            previous is not None
            and previous[0] == key[0]
            and previous[5:] == key[5:]
            and self.page.newer
            and self.page.transactions
        ):
            anchor = self.page.transactions[0]
            # Source IDs can change on reload; never use an old ID for row actions.
            current = next(
                (
                    entry
                    for entry in matching_transactions(
                        journal.ledger.transactions, account, category
                    )
                    if replace(entry, id=anchor.id) == anchor
                ),
                anchor,
            )
            cursor = transaction_key(current)
        page = operation_page(
            journal.ledger, account, category, cursor=cursor, direction="older", inclusive=True
        )
        if cursor is not None and not page.transactions:
            page = operation_page(
                journal.ledger, account, category, cursor=cursor, direction="newer", inclusive=True
            )
        self._render(page)

    def _filters(self) -> tuple[str, str]:
        return (
            str(self.host.operations_account.value or ""),
            str(self.host.operations_category.value or ""),
        )

    def _labels(self) -> None:
        tr = self.host.tr
        for newer, older, latest in zip(self.newer, self.older, self.latest, strict=True):
            newer.content, older.content, latest.content = (
                tr("operations_newer"),
                tr("operations_older"),
                tr("operations_latest"),
            )
            newer.disabled = self.busy or not self.page.newer
            older.disabled = self.busy or not self.page.older
            latest.disabled = self.busy
            latest.visible = self.page.newer
        for navigation in self.navigation:
            navigation.visible = self.page.newer or self.page.older
        for loading in self.loading:
            loading.visible = self.busy
        self.end.value = tr("operations_end")
        self.end.visible = bool(self.page.transactions) and self.page.newer and not self.page.older
        self.retry.content = tr("operations_retry")
        self.retry.visible = self.failed_direction is not None and not self.busy

    def _render(self, page: OperationPage) -> None:
        journal = self.host.journal
        assert journal is not None and journal.ready
        names = {account.id: account.name for account in journal.ledger.accounts}
        names.update({category.id: category.name for category in journal.ledger.categories})
        controls = self.host._transaction_groups(
            list(page.transactions), names, daily_summary=True, daily_totals=dict(page.totals)
        )
        for index, control in enumerate(controls):
            if index >= len(self.history.controls):
                break
            previous = self.history.controls[index]
            if isinstance(control, ft.Container) and type(previous) is type(control):
                assert isinstance(previous, ft.Container)
                previous.content = control.content
                previous.data = control.data
                previous.padding = control.padding
                previous.alignment = control.alignment
                previous.border = control.border
                previous.theme = control.theme
                previous.dark_theme = control.dark_theme
                controls[index] = previous
        self.page = page
        self.history.controls = controls or [
            ft.Text(
                self.host.tr("operations_no_matches")
                if any(self._filters())
                else self.host.tr("transactions_empty")
            )
        ]
        self._labels()
        self.host._update_transaction_separators()

    async def move(self, direction: PageDirection) -> None:
        if self.busy:
            return
        self.sync()
        journal = self.host.journal
        if journal is None or not journal.ready or not self.page.transactions:
            return
        if direction == "older" and not self.page.older:
            return
        if direction == "newer" and not self.page.newer:
            return
        generation = self.generation
        key = self._key()
        screen = self.host.current_screen
        cursor = (
            None
            if direction == "latest"
            else transaction_key(self.page.transactions[-1 if direction == "older" else 0])
        )
        self.busy = True
        self.error.visible = False
        self.failed_direction = None
        self._labels()
        self.host.page.update()
        try:
            page = await asyncio.to_thread(
                operation_page, journal.ledger, *self._filters(), cursor=cursor, direction=direction
            )
            if (
                generation != self.generation
                or key != self._key()
                or screen != self.host.current_screen
            ):
                return
            self._render(page)
            self.host.page.update()
            try:
                await self.host.body.scroll_to(offset=0, duration=0)
            except Exception as exc:
                self.host._error(exc, notify=True)
        except Exception as exc:
            if generation == self.generation:
                self.error.value = self.host.tr("operations_load_failed")
                self.error.visible = True
                self.failed_direction = direction
                self.host._error(exc, notify=True)
        finally:
            if generation == self.generation:
                self.busy = False
                self._labels()
                self.host.page.update()

    async def _older(self, event: ft.Event[ft.TextButton]) -> None:
        await self.move("older")

    async def _newer(self, event: ft.Event[ft.TextButton]) -> None:
        await self.move("newer")

    async def _latest(self, event: ft.Event[ft.TextButton]) -> None:
        await self.move("latest")

    async def _retry(self, event: ft.Event[ft.TextButton]) -> None:
        if self.failed_direction is not None:
            await self.move(self.failed_direction)
