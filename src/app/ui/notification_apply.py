"""Explicit single-notification rule re-evaluation preview."""

import logging
import sqlite3
from collections.abc import Callable, Mapping
from datetime import date

import flet as ft

from app.domain.ledger import Ledger
from app.infrastructure.notification_import import RulePreview, preview_re_evaluation
from app.infrastructure.notification_store import (
    NotificationStore,
    NotificationStoreError,
    QueueItem,
)
from app.ui.dialogs import EditorDialog, show_dialog
from app.ui.notification_rules import describe_draft_errors

logger = logging.getLogger(__name__)

_FIELDS = (
    ("kind", "notification_rule_kind"),
    ("amount", "notification_rule_amount"),
    ("currency", "notification_rule_currency"),
    ("account", "notification_rule_account"),
    ("destination", "notification_rule_destination"),
    ("destination_amount", "notification_rule_destination_amount"),
    ("category", "notification_rule_category"),
    ("payee", "notification_rule_payee"),
    ("description", "notification_rule_description"),
    ("date", "notification_rule_date"),
    ("time", "notification_rule_time"),
    ("fee_enabled", "transfer_fee_enabled"),
    ("fee", "transfer_fee"),
    ("fee_mode", "transfer_mode"),
    ("fee_category", "transfer_fee_category"),
    ("fee_currency", "exchange_fee_currency"),
    ("reference", "incoming_reference_label"),
)
_PRESERVED_FIELDS = frozenset(
    {"fee_enabled", "fee", "fee_mode", "fee_category", "fee_currency", "reference"}
)


class NotificationRuleApplication:
    def __init__(
        self,
        page: ft.Page,
        translate: Callable[..., str],
        store: NotificationStore,
        item: QueueItem,
        *,
        ledger: Ledger | None,
        today: date,
        journal_id: str | None,
        current_fields: Mapping[str, str | None] | None,
        on_applied: Callable[[int, str], None],
    ) -> None:
        self.page = page
        self.tr = translate
        self.store = store
        self.on_applied = on_applied
        self.overwrite_edits = current_fields is not None
        if current_fields is not None and (
            set(current_fields) != {field for field, _ in _FIELDS}
            or any(
                value is not None and not isinstance(value, str)
                for value in current_fields.values()
            )
        ):
            raise NotificationStoreError("Review all transaction draft fields before applying.")
        current = next(
            (
                candidate
                for candidate in store.items(
                    ("pending", "unmatched", "ignored", "uncertain"),
                    app_id=item.app_id,
                    with_content=True,
                )
                if candidate.id == item.id
            ),
            None,
        )
        if current is None or current.review_snapshot != item.review_snapshot:
            raise NotificationStoreError("Notification changed; review it again.")
        self.item = current
        self.preview: RulePreview = preview_re_evaluation(
            store, current, ledger, today=today, journal_id=journal_id
        )
        self.confirm_duplicate = ft.Checkbox(
            label=self.tr("notification_apply_duplicate_ack"),
            visible=current.status == "uncertain",
            on_change=self._changed,
        )
        self.status = ft.Text(color=ft.Colors.ERROR, selectable=True)
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.apply = ft.TextButton(
            self.tr("notification_apply_confirm"),
            disabled=current.status == "uncertain",
            on_click=self._apply,
        )
        before: Mapping[str, object] = (
            current_fields if current_fields is not None else item.draft or {}
        )
        after = self.preview.evaluation.draft or {}
        details: list[ft.Control] = [
            ft.Text(self.tr("notification_apply_help")),
            ft.Text(
                self.tr(
                    "notification_apply_winner",
                    name=self.preview.evaluation.rule_name or self.tr("notification_no_rule"),
                )
            ),
            ft.Text(
                self.tr(
                    "notification_apply_outcome",
                    outcome=self.tr(
                        {
                            "pending": "notification_rule_outcome_suggest",
                            "ignored": "notification_rule_outcome_ignore",
                            "unmatched": "notification_rule_outcome_unmatched",
                        }[self.preview.evaluation.outcome]
                    ),
                )
            ),
        ]
        if self.overwrite_edits:
            details.append(ft.Text(self.tr("notification_apply_overwrite")))
        empty = self.tr("notification_apply_empty")
        for field, label in _FIELDS:
            old_value = before.get(field)
            new_value = old_value if field in _PRESERVED_FIELDS else after.get(field)
            if old_value != new_value:
                details.append(
                    ft.Text(
                        self.tr(
                            "notification_apply_change",
                            field=self.tr(label),
                            before=str(old_value) if old_value is not None else empty,
                            after=str(new_value) if new_value is not None else empty,
                        ),
                        selectable=True,
                    )
                )
        errors = describe_draft_errors(self.tr, self.preview.evaluation.draft)
        if errors:
            details.append(
                ft.Text(self.tr("notification_rule_test_errors", errors="; ".join(errors)))
            )
        details.extend([self.confirm_duplicate, self.status])
        self.form = ft.Column(details, spacing=10)
        self.dialog: EditorDialog | None = None

    def open(self) -> EditorDialog:
        self.dialog = EditorDialog(
            self.tr("notification_apply_title"),
            self.form,
            [self.cancel, self.apply],
            compact=lambda: (self.page.width or 600) <= 599,
            cancel_on_back=self._close,
        )
        show_dialog(self.page, self.dialog)
        return self.dialog

    def _changed(self, event: ft.Event) -> None:
        self.apply.disabled = self.item.status == "uncertain" and not self.confirm_duplicate.value
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()

    def _close(self) -> None:
        self.page.pop_dialog()

    async def _cancel(self, event: ft.Event) -> None:
        self._close()

    async def _apply(self, event: ft.Event) -> None:
        if self.item.status == "uncertain" and not self.confirm_duplicate.value:
            return
        try:
            self.store.apply_evaluation(
                self.item.id,
                self.preview.snapshot,
                self.preview.evaluation,
                overwrite_edits=self.overwrite_edits,
                confirm_possible_duplicate=bool(self.confirm_duplicate.value),
            )
        except NotificationStoreError:
            logger.warning("Notification rule application was refused")
            self.status.value = self.tr("notification_apply_failed")
            if self.status.parent is not None:
                self.status.update()
        except sqlite3.Error:
            logger.exception("Notification rule application storage failed")
            self.status.value = self.tr("notification_apply_failed")
            if self.status.parent is not None:
                self.status.update()
        else:
            self.page.pop_dialog()
            self.on_applied(self.item.id, self.preview.evaluation.outcome)
