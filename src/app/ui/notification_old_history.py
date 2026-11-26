"""Explicit recovery of an old notification still visible to Android."""

import logging
import sqlite3
from collections.abc import Callable

import flet as ft

from app.infrastructure.notification_store import (
    IncomingNotification,
    NotificationStore,
    NotificationStoreError,
)
from app.localization import format_epoch
from app.ui.dialogs import EditorDialog, show_dialog

logger = logging.getLogger(__name__)
PAGE_SIZE = 20


class NotificationOldHistory:
    def __init__(
        self,
        page: ft.Page,
        translate: Callable[..., str],
        store: NotificationStore,
        candidates: tuple[IncomingNotification, ...],
        *,
        now: Callable[[], int],
        on_recovered: Callable[[], None],
    ) -> None:
        self.page = page
        self.tr = translate
        self.store = store
        self.candidates = candidates
        self.now = now
        self.on_recovered = on_recovered
        self.page_index = 0
        self.selection = ft.Dropdown(
            label=self.tr("notification_old_select"), on_select=self._selected
        )
        self.sample = ft.Text(selectable=True)
        self.acknowledge = ft.Checkbox(
            label=self.tr("notification_old_ack"), on_change=self._changed
        )
        self.status = ft.Text(color=ft.Colors.ERROR, selectable=True)
        self.previous = ft.TextButton(
            self.tr("notification_inbox_previous"), on_click=self._previous
        )
        self.next = ft.TextButton(self.tr("notification_inbox_next"), on_click=self._next)
        self.page_label = ft.Text()
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.confirm = ft.TextButton(
            self.tr("notification_old_confirm"), disabled=True, on_click=self._confirm
        )
        self.form = ft.Column(
            [
                ft.Text(self.tr("notification_old_help")),
                ft.Row([self.previous, self.page_label, self.next], wrap=True),
                self.selection,
                self.sample,
                self.acknowledge,
                self.status,
            ],
            spacing=10,
        )
        self.dialog: EditorDialog | None = None
        self._render_page()

    def _render_page(self) -> None:
        pages = max(1, (len(self.candidates) + PAGE_SIZE - 1) // PAGE_SIZE)
        start = self.page_index * PAGE_SIZE
        self.selection.options = [
            ft.DropdownOption(
                key=str(index),
                text=f"{item.app_id} · " + format_epoch(item.posted),
            )
            for index, item in enumerate(self.candidates[start : start + PAGE_SIZE], start=start)
        ]
        self.selection.value = None
        self.sample.value = ""
        self.acknowledge.value = False
        self.confirm.disabled = True
        self.previous.disabled = self.page_index == 0
        self.next.disabled = self.page_index >= pages - 1
        self.page_label.value = self.tr(
            "notification_inbox_page", page=self.page_index + 1, pages=pages
        )
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()

    def open(self) -> EditorDialog:
        self.dialog = EditorDialog(
            self.tr("notification_old_title"),
            self.form,
            [self.cancel, self.confirm],
            compact=lambda: (self.page.width or 600) <= 599,
            cancel_on_back=self._close,
        )
        show_dialog(self.page, self.dialog)
        return self.dialog

    def _selected(self, event: ft.Event) -> None:
        index = self.selection.value
        if index is None or not index.isdecimal() or int(index) >= len(self.candidates):
            self.sample.value = ""
            self.selection.value = None
        else:
            item = self.candidates[int(index)]
            self.sample.value = "\n".join(part for part in (item.title, item.body) if part)
        self.acknowledge.value = False
        self.status.value = ""
        self._changed(event)

    def _changed(self, event: ft.Event) -> None:
        self.confirm.disabled = not (self.selection.value is not None and self.acknowledge.value)
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()

    def _previous(self, event: ft.Event) -> None:
        if self.page_index > 0:
            self.page_index -= 1
            self._render_page()

    def _next(self, event: ft.Event) -> None:
        if not self.next.disabled:
            self.page_index += 1
            self._render_page()

    def _close(self) -> None:
        self.page.pop_dialog()

    async def _cancel(self, event: ft.Event) -> None:
        self._close()

    async def _confirm(self, event: ft.Event) -> None:
        index = self.selection.value
        if (
            index is None
            or not index.isdecimal()
            or int(index) >= len(self.candidates)
            or not self.acknowledge.value
        ):
            return
        try:
            self.store.recover_old(
                self.candidates[int(index)], now=self.now(), confirm_possible_duplicate=True
            )
        except NotificationStoreError:
            logger.warning("Old notification recovery was refused")
            self.status.value = self.tr("notification_old_failed")
        except sqlite3.Error:
            logger.exception("Old notification recovery storage failed")
            self.status.value = self.tr("notification_old_failed")
        else:
            self._close()
            self.on_recovered()
            return
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()
