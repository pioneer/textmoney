"""Explicit review of an interrupted notification transaction save."""

import hashlib
import logging
import sqlite3
from collections.abc import Callable

import flet as ft

from app.domain.ledger import LedgerError
from app.infrastructure.notification_import import journal_identity, resolve_reviewed_import
from app.infrastructure.notification_store import (
    NotificationStore,
    NotificationStoreError,
    QueueItem,
)
from app.journal import Journal
from app.storage import StorageError
from app.ui.dialogs import EditorDialog, show_dialog

logger = logging.getLogger(__name__)


class NotificationRecovery:
    def __init__(
        self,
        page: ft.Page,
        translate: Callable[..., str],
        store: NotificationStore,
        item: QueueItem,
        journal: Journal,
        *,
        on_resolved: Callable[[], None],
    ) -> None:
        if (
            item.status != "saving"
            or not journal.ready
            or not journal.storage.loaded
            or journal.storage.draft != journal.source
            or item.journal_id != journal_identity(journal)
        ):
            raise NotificationStoreError("Review the original loaded journal first.")
        self.page = page
        self.tr = translate
        self.store = store
        self.item = item
        self.journal = journal
        self.on_resolved = on_resolved
        self.source_hash = hashlib.sha256(journal.source.encode("utf-8")).hexdigest()
        self.busy = False
        self.outcome = ft.Dropdown(
            label=self.tr("notification_recovery_outcome"),
            options=[
                ft.DropdownOption(key="saved", text=self.tr("notification_recovery_saved")),
                ft.DropdownOption(key="not_saved", text=self.tr("notification_recovery_not_saved")),
            ],
            on_select=self._changed,
        )
        self.acknowledge = ft.Checkbox(
            label=self.tr("notification_recovery_acknowledge"), on_change=self._changed
        )
        self.status = ft.Text(color=ft.Colors.ERROR, selectable=True)
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.confirm = ft.TextButton(
            self.tr("notification_recovery_confirm"), disabled=True, on_click=self._confirm
        )
        details: list[ft.Control] = [ft.Text(self.tr("notification_recovery_help"))]
        if item.draft:
            for key, label in (
                ("amount", "notification_rule_amount"),
                ("currency", "notification_rule_currency"),
                ("account", "notification_rule_account"),
                ("date", "notification_rule_date"),
                ("payee", "notification_rule_payee"),
                ("description", "notification_rule_description"),
            ):
                value = item.draft.get(key)
                if isinstance(value, str) and value:
                    details.append(ft.Text(f"{self.tr(label)}: {value}", selectable=True))
        details.extend([self.outcome, self.acknowledge, self.status])
        self.form = ft.Column(details, spacing=10)
        self.dialog: EditorDialog | None = None

    def open(self) -> EditorDialog:
        self.dialog = EditorDialog(
            self.tr("notification_recovery_title"),
            self.form,
            [self.cancel, self.confirm],
            compact=lambda: (self.page.width or 600) <= 599,
            cancel_on_back=self._close,
        )
        show_dialog(self.page, self.dialog)
        return self.dialog

    def _changed(self, event: ft.Event) -> None:
        self.confirm.disabled = not (
            self.outcome.value in ("saved", "not_saved") and self.acknowledge.value
        )
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()

    def _close(self) -> None:
        if not self.busy:
            self.page.pop_dialog()

    async def _cancel(self, event: ft.Event) -> None:
        self._close()

    async def _confirm(self, event: ft.Event) -> None:
        if (
            self.busy
            or self.outcome.value not in ("saved", "not_saved")
            or not self.acknowledge.value
        ):
            return
        self.busy = True
        self.cancel.disabled = True
        self.confirm.disabled = True
        self.outcome.disabled = True
        self.acknowledge.disabled = True
        self.status.value = ""
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()
        try:
            await resolve_reviewed_import(
                self.store,
                self.item,
                self.journal,
                source_hash=self.source_hash,
                saved=self.outcome.value == "saved",
            )
        except (LedgerError, NotificationStoreError, StorageError, TimeoutError):
            logger.warning("Notification save recovery requires another review")
            self.status.value = self.tr("notification_recovery_failed")
        except sqlite3.Error:
            logger.exception("Notification save recovery storage failed")
            self.status.value = self.tr("notification_recovery_failed")
        else:
            self.page.pop_dialog()
            self.on_resolved()
        finally:
            self.busy = False
            self.cancel.disabled = False
            self.outcome.disabled = False
            self.acknowledge.disabled = False
            self._changed(event)
