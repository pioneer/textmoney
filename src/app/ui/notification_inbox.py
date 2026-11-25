"""Explicit notification checking and review queue presentation."""

import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Literal

import flet as ft

from app.infrastructure.notification_import import (
    RecentRulePreview,
    check_notifications,
    journal_identity,
    preview_recent_re_evaluation,
)
from app.infrastructure.notification_store import (
    DAY,
    PENDING_AGE,
    NotificationCapacityError,
    NotificationStore,
    NotificationStoreError,
    QueueItem,
)
from app.infrastructure.notifications_android import (
    AndroidNotifications,
    NotificationAccess,
    NotificationApp,
    NotificationBridgeError,
)
from app.journal import Journal
from app.ui.dialogs import EditorDialog, show_dialog
from app.ui.notification_old_history import NotificationOldHistory
from app.ui.notification_rules import describe_draft_errors, describe_draft_fields

logger = logging.getLogger(__name__)
InboxTab = Literal["pending", "recent", "processed"]
PAGE_SIZE = 20


class NotificationInbox:
    def __init__(
        self,
        translate: Callable[..., str],
        bridge: AndroidNotifications,
        store: NotificationStore,
        *,
        page: ft.Page,
        journal: Callable[[], Journal | None],
        on_review: Callable[[QueueItem], None],
        on_rules: Callable[[QueueItem], None],
        on_recovery: Callable[[QueueItem], None],
        on_settings: Callable[[], None],
        on_changed: Callable[[], None],
        on_ai: Callable[[QueueItem], Awaitable[None]] | None = None,
        access: Callable[[], NotificationAccess | None] = lambda: None,
        access_failed: Callable[[], bool] = lambda: False,
        apps: Callable[[], tuple[NotificationApp, ...]] = lambda: (),
        now: Callable[[], int] = lambda: int(time.time()),
        today: Callable[[], date] = date.today,
    ) -> None:
        self.tr = translate
        self.bridge = bridge
        self.store = store
        self.page = page
        self.journal = journal
        self.on_review = on_review
        self.on_rules = on_rules
        self.on_recovery = on_recovery
        self.on_settings = on_settings
        self.on_changed = on_changed
        self.on_ai = on_ai
        self.access = access
        self.access_failed = access_failed
        self.apps = apps
        self.now = now
        self.today = today
        self.tab: InboxTab = "pending"
        self.pages: dict[InboxTab, int] = {"pending": 0, "recent": 0, "processed": 0}
        self.busy = False
        self.notice: str | None = None
        self.title = ft.Text(size=20, weight=ft.FontWeight.W_600)
        self.status = ft.Text(selectable=True)
        self.access_status = ft.Text(selectable=True)
        self.check = ft.Button(on_click=self._check)
        self.settings = ft.TextButton(on_click=self._settings)
        self.old_history = ft.TextButton(on_click=self._old_history)
        self.re_evaluate = ft.TextButton(on_click=self._re_evaluate)
        self.tabs = {
            tab: ft.TextButton(data=tab, on_click=self._select_tab)
            for tab in ("pending", "recent", "processed")
        }
        self.content = ft.Column(spacing=8)
        self.previous = ft.TextButton(on_click=self._previous)
        self.next = ft.TextButton(on_click=self._next)
        self.page_label = ft.Text()
        self.control = ft.Column(
            [
                self.title,
                ft.Row([self.check, self.settings, self.old_history, self.re_evaluate], wrap=True),
                self.status,
                self.access_status,
                ft.Row(list(self.tabs.values()), wrap=True),
                self.content,
                ft.Row([self.previous, self.page_label, self.next], wrap=True),
            ],
            spacing=8,
        )
        self.render()

    def pending_count(self) -> int:
        return self.store.count(("pending", "saving"))

    def render(self) -> None:
        self.title.value = self.tr("notification_inbox_title")
        self.check.content = self.tr("notification_inbox_check")
        self.check.disabled = (
            self.busy or self.store.mode() == "off" or not self.store.selected_apps()
        )
        self.settings.content = self.tr("notification_inbox_settings")
        self.settings.disabled = self.busy
        self.old_history.content = self.tr("notification_old_title")
        self.old_history.disabled = self.check.disabled
        self.re_evaluate.content = self.tr("notification_recent_title")
        self.re_evaluate.disabled = self.busy
        for tab, button in self.tabs.items():
            button.content = self.tr(f"notification_inbox_tab_{tab}")
            button.disabled = self.busy or self.tab == tab
        if self.notice:
            self.status.value = self.tr(self.notice)
        elif self.store.mode() == "off" or not self.store.selected_apps():
            self.status.value = self.tr("notification_inbox_setup")
        else:
            self.status.value = ""
        access = self.access()
        access_key = None
        if self.store.mode() != "off" and self.store.selected_apps():
            if self.access_failed():
                access_key = "notification_settings_bridge_error"
            elif access is None:
                access_key = "notification_settings_access_unknown"
            elif not access.granted:
                access_key = "notification_settings_access_required"
            elif not access.connected:
                access_key = "notification_settings_disconnected"
        self.access_status.value = self.tr(access_key) if access_key else ""
        statuses = {
            "pending": ("pending", "saving"),
            "recent": ("unmatched", "ignored", "uncertain"),
            "processed": ("imported", "skipped", "expired", "cleared"),
        }[self.tab]
        total = self.store.count(statuses)
        last_page = max(0, (total - 1) // PAGE_SIZE)
        self.pages[self.tab] = min(self.pages[self.tab], last_page)
        page = self.pages[self.tab]
        items = self.store.items(
            statuses, limit=PAGE_SIZE, offset=page * PAGE_SIZE, newest_first=True
        )
        self.previous.content = self.tr("notification_inbox_previous")
        self.previous.disabled = self.busy or page == 0
        self.next.content = self.tr("notification_inbox_next")
        self.next.disabled = self.busy or page >= last_page
        self.page_label.value = self.tr(
            "notification_inbox_page", page=page + 1, pages=last_page + 1
        )
        controls: list[ft.Control] = []
        expired = self.store.expired_count()
        if self.tab == "pending" and expired:
            controls.append(
                ft.Row(
                    [
                        ft.Text(self.tr("notification_inbox_expired", count=expired)),
                        ft.TextButton(
                            self.tr("notification_inbox_acknowledge_expired"),
                            data=expired,
                            disabled=self.busy,
                            on_click=self._acknowledge_expired,
                        ),
                    ],
                    wrap=True,
                )
            )
        selected = set(self.store.selected_apps())
        app_names = {app.id: app.name for app in self.apps()}
        for item in items:
            name = app_names.get(item.app_id)
            source = f"{name} ({item.app_id})" if name and name != item.app_id else item.app_id
            details: list[ft.Control] = [
                ft.Text(source, weight=ft.FontWeight.W_600),
                ft.Text(self.tr(f"notification_inbox_status_{item.status}")),
                ft.Text(
                    self.tr(
                        "notification_inbox_posted",
                        time=datetime.fromtimestamp(item.posted)
                        .astimezone()
                        .strftime("%Y-%m-%d %H:%M"),
                    )
                ),
            ]
            details.append(ft.Text(item.rule_name or self.tr("notification_no_rule")))
            if item.stale_rule:
                details.append(ft.Text(self.tr("notification_inbox_stale_rule")))
            if item.changed:
                details.append(ft.Text(self.tr("notification_inbox_changed_source")))
            if item.status == "pending" and item.first_seen + PENDING_AGE - self.now() <= 7 * DAY:
                details.append(ft.Text(self.tr("notification_inbox_expiring")))
            if item.title is not None:
                details.append(ft.Text(item.title, selectable=True))
            elif item.status in ("pending", "unmatched", "ignored", "uncertain"):
                details.append(ft.Text(self.tr("notification_inbox_title_unavailable")))
            if item.body is not None:
                details.append(ft.Text(item.body, selectable=True))
            elif item.status in ("pending", "unmatched", "ignored", "uncertain"):
                details.append(ft.Text(self.tr("notification_inbox_body_unavailable")))
            if item.status == "pending":
                details.extend(
                    ft.Text(value, selectable=True)
                    for value in describe_draft_fields(self.tr, item.draft)
                )
                errors = describe_draft_errors(self.tr, item.draft)
                if errors:
                    details.append(
                        ft.Text(self.tr("notification_rule_test_errors", errors="; ".join(errors)))
                    )
            actions: list[ft.Control] = []
            if item.status == "pending":
                actions = [
                    ft.TextButton(
                        self.tr("notification_inbox_review"), data=item, on_click=self._review
                    ),
                    ft.TextButton(
                        self.tr("notification_inbox_skip"), data=item, on_click=self._skip
                    ),
                ]
            elif item.status == "saving":
                actions = [
                    ft.TextButton(
                        self.tr("notification_inbox_recover"), data=item, on_click=self._recover
                    )
                ]
            elif item.status in ("unmatched", "ignored", "uncertain") and (
                item.title is not None or item.body is not None
            ):
                actions = [
                    ft.TextButton(
                        self.tr("notification_inbox_rule"),
                        data=item,
                        on_click=self._rules,
                        disabled=item.app_id not in selected,
                    )
                ]
            if actions:
                if (
                    self.on_ai is not None
                    and item.status in ("pending", "unmatched", "ignored", "uncertain")
                    and item.app_id in selected
                    and (item.title is not None or item.body is not None)
                ):
                    actions.append(
                        ft.TextButton(
                            self.tr("notification_ai_title"), data=item, on_click=self._ai
                        )
                    )
                details.append(ft.Row(actions, wrap=True))
            controls.append(ft.Container(content=ft.Column(details, spacing=4), padding=12))
        if not items:
            key = (
                "notification_inbox_waiting"
                if self.tab == "pending" and self.store.mode() == "background"
                else f"notification_inbox_empty_{self.tab}"
            )
            controls.append(ft.Text(self.tr(key)))
        self.content.controls = controls
        if self.control.parent is not None:
            self.control.update()

    async def _check(self, event: ft.Event) -> None:
        if self.busy:
            return
        self.busy = True
        self.notice = None
        self.render()
        try:
            journal = self.journal()
            ready = journal is not None and journal.ready
            result = await check_notifications(
                self.bridge,
                self.store,
                journal.ledger if ready and journal else None,
                now=self.now(),
                today=journal.today() if ready and journal else self.today(),
                journal_id=journal_identity(journal) if ready and journal else None,
            )
            self.notice = (
                "notification_inbox_capacity" if result.capacity else "notification_inbox_checked"
            )
        except NotificationBridgeError as exc:
            logger.warning("Notification check failed (code=%s)", exc.code)
            self.notice = "notification_inbox_access_error"
        except (NotificationStoreError, ValueError):
            logger.warning("Notification check was refused")
            self.notice = "notification_inbox_check_error"
        except sqlite3.Error:
            logger.exception("Notification check storage failed")
            self.notice = "notification_inbox_check_error"
        finally:
            self.busy = False
            self.render()
            self.on_changed()

    def _select_tab(self, event: ft.Event[ft.TextButton]) -> None:
        self.tab = event.control.data
        self.render()

    async def _old_history(self, event: ft.Event) -> None:
        if self.busy:
            return
        self.busy = True
        self.notice = None
        self.render()
        try:
            selected = self.store.selected_apps()
            if self.store.mode() == "off" or not selected:
                raise NotificationStoreError("Enable checking and select source apps.")
            visible = await self.bridge.active(selected)
            candidates = self.store.old_recovery_candidates(visible, now=self.now())
            if not candidates:
                self.notice = "notification_old_empty"
            else:
                NotificationOldHistory(
                    self.page,
                    self.tr,
                    self.store,
                    candidates,
                    now=self.now,
                    on_recovered=self._old_recovered,
                ).open()
        except NotificationBridgeError as exc:
            logger.warning("Old notification lookup failed (code=%s)", exc.code)
            self.notice = "notification_inbox_access_error"
        except NotificationStoreError:
            logger.warning("Old notification lookup was refused")
            self.notice = "notification_inbox_check_error"
        except sqlite3.Error:
            logger.exception("Old notification lookup storage failed")
            self.notice = "notification_inbox_check_error"
        finally:
            self.busy = False
            self.render()

    def _old_recovered(self) -> None:
        self.tab = "recent"
        self.pages["recent"] = 0
        self.notice = "notification_old_recovered"
        self.render()
        self.on_changed()

    def _re_evaluate(self, event: ft.Event) -> None:
        if self.busy:
            return
        try:
            journal = self.journal()
            ready = journal is not None and journal.ready
            journal_id = journal_identity(journal) if ready and journal else None
            source = journal.source if ready and journal else None
            previews = preview_recent_re_evaluation(
                self.store,
                journal.ledger if ready and journal else None,
                now=self.now(),
                today=journal.today() if ready and journal else self.today(),
                journal_id=journal_id,
            )
            if not previews:
                self.notice = "notification_recent_empty"
                self.render()
                return
            NotificationRecentPreview(
                self.page,
                self.tr,
                self.store,
                previews,
                now=self.now,
                journal=self.journal,
                journal_snapshot=(journal_id, source)
                if journal_id and source is not None
                else None,
                on_applied=self._recent_applied,
            ).open()
        except (NotificationStoreError, ValueError):
            logger.warning("Recent notification preview was refused")
            self.notice = "notification_recent_failed"
            self.render()
        except sqlite3.Error:
            logger.exception("Recent notification preview storage failed")
            self.notice = "notification_recent_failed"
            self.render()

    def _recent_applied(self) -> None:
        self.notice = "notification_recent_applied"
        self.render()
        self.on_changed()

    def _previous(self, event: ft.Event) -> None:
        if self.pages[self.tab] > 0:
            self.pages[self.tab] -= 1
            self.render()

    def _next(self, event: ft.Event) -> None:
        if not self.next.disabled:
            self.pages[self.tab] += 1
            self.render()

    def _settings(self, event: ft.Event) -> None:
        self.on_settings()

    def _review(self, event: ft.Event[ft.TextButton]) -> None:
        self.on_review(event.control.data)

    def _rules(self, event: ft.Event[ft.TextButton]) -> None:
        self.on_rules(event.control.data)

    async def _ai(self, event: ft.Event[ft.TextButton]) -> None:
        if self.on_ai is not None:
            await self.on_ai(event.control.data)

    def _recover(self, event: ft.Event[ft.TextButton]) -> None:
        self.on_recovery(event.control.data)

    def _acknowledge_expired(self, event: ft.Event[ft.TextButton]) -> None:
        try:
            self.store.acknowledge_expired(event.control.data)
        except NotificationStoreError:
            logger.warning("Expired notification acknowledgement was refused")
            self.notice = "notification_inbox_expired_changed"
        except sqlite3.Error:
            logger.exception("Expired notification acknowledgement storage failed")
            self.notice = "notification_inbox_storage_error"
            self.status.value = self.tr(self.notice)
            if self.status.parent is not None:
                self.status.update()
            return
        else:
            self.notice = None
        self.render()
        self.on_changed()

    def _skip(self, event: ft.Event[ft.TextButton]) -> None:
        item: QueueItem = event.control.data
        try:
            self.store.skip(item.id, snapshot=item.review_snapshot)
        except NotificationStoreError:
            logger.warning("Skipping a notification was refused")
            self.notice = "notification_inbox_changed"
        except sqlite3.Error:
            logger.exception("Skipping a notification failed in storage")
            self.notice = "notification_inbox_storage_error"
            self.status.value = self.tr(self.notice)
            if self.status.parent is not None:
                self.status.update()
            return
        else:
            self.notice = None
        self.render()
        self.on_changed()


class NotificationRecentPreview:
    """Review changed recent outcomes before applying them atomically."""

    def __init__(
        self,
        page: ft.Page,
        translate: Callable[..., str],
        store: NotificationStore,
        previews: tuple[RecentRulePreview, ...],
        *,
        now: Callable[[], int],
        journal: Callable[[], Journal | None],
        journal_snapshot: tuple[str, str] | None,
        on_applied: Callable[[], None],
    ) -> None:
        self.page = page
        self.tr = translate
        self.store = store
        self.previews = previews
        self.now = now
        self.journal = journal
        self.journal_snapshot = journal_snapshot
        self.on_applied = on_applied
        self.status = ft.Text(color=ft.Colors.ERROR, selectable=True)
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.confirm = ft.TextButton(self.tr("notification_recent_confirm"), on_click=self._confirm)
        details: list[ft.Control] = [
            ft.Text(self.tr("notification_recent_help")),
            ft.Text(self.tr("notification_recent_count", count=len(previews))),
        ]
        for entry in previews:
            item, evaluation = entry.item, entry.preview.evaluation
            timestamp = datetime.fromtimestamp(item.posted).astimezone().strftime("%Y-%m-%d %H:%M")
            details.extend(
                [
                    ft.Text(f"{item.app_id} · {timestamp}", weight=ft.FontWeight.W_600),
                    ft.Text(
                        self.tr(
                            "notification_recent_transition",
                            before=self.tr(f"notification_inbox_status_{item.status}"),
                            after=self.tr(f"notification_inbox_status_{evaluation.outcome}"),
                        )
                    ),
                    ft.Text(
                        self.tr(
                            "notification_recent_previous_rule",
                            name=item.rule_name or self.tr("notification_no_rule"),
                        )
                    ),
                    ft.Text(
                        self.tr(
                            "notification_apply_winner",
                            name=evaluation.rule_name or self.tr("notification_no_rule"),
                        )
                    ),
                ]
            )
            details.extend(
                ft.Text(part, selectable=True)
                for part in (item.title, item.body)
                if part is not None
            )
            details.extend(
                ft.Text(value, selectable=True)
                for value in describe_draft_fields(self.tr, evaluation.draft)
            )
            errors = describe_draft_errors(self.tr, evaluation.draft)
            if errors:
                details.append(
                    ft.Text(self.tr("notification_rule_test_errors", errors="; ".join(errors)))
                )
        details.append(self.status)
        self.form = ft.Column(details, spacing=10)
        self.dialog: EditorDialog | None = None

    def open(self) -> EditorDialog:
        self.dialog = EditorDialog(
            self.tr("notification_recent_title"),
            self.form,
            [self.cancel, self.confirm],
            compact=lambda: (self.page.width or 600) <= 599,
            cancel_on_back=self._close,
        )
        show_dialog(self.page, self.dialog)
        return self.dialog

    def _close(self) -> None:
        self.page.pop_dialog()

    async def _cancel(self, event: ft.Event) -> None:
        self._close()

    async def _confirm(self, event: ft.Event) -> None:
        try:
            current = self.journal()
            snapshot = (
                (journal_identity(current), current.source)
                if current is not None and current.ready
                else None
            )
            if snapshot != self.journal_snapshot:
                raise NotificationStoreError("Selected journal changed; preview again.")
            self.store.apply_recent_evaluations(
                tuple(
                    (entry.item.id, entry.preview.snapshot, entry.preview.evaluation)
                    for entry in self.previews
                ),
                now=self.now(),
            )
        except NotificationCapacityError:
            logger.warning("Recent notification application exceeded pending capacity")
            self.status.value = self.tr("notification_inbox_capacity")
        except (NotificationStoreError, ValueError):
            logger.warning("Recent notification application was refused")
            self.status.value = self.tr("notification_recent_failed")
        except sqlite3.Error:
            logger.exception("Recent notification application storage failed")
            self.status.value = self.tr("notification_recent_failed")
        else:
            self._close()
            self.on_applied()
            return
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()
