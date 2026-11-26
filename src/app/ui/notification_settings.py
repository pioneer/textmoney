"""Android notification source and rule configuration."""

import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import date

import flet as ft

from app.domain.ledger import Ledger
from app.infrastructure.notification_import import (
    open_notification_access_settings,
    set_manual_monitoring,
)
from app.infrastructure.notification_store import NotificationStore, NotificationStoreError
from app.infrastructure.notifications_android import (
    AndroidNotifications,
    NotificationAccess,
    NotificationApp,
    NotificationBridgeError,
)
from app.localization import format_epoch
from app.ui.dialogs import show_dialog
from app.ui.notification_rules import AIStarter, NotificationRuleEditor

logger = logging.getLogger(__name__)


class NotificationSettingsPanel:
    def __init__(
        self,
        page: ft.Page,
        translate: Callable[..., str],
        bridge: AndroidNotifications,
        store: NotificationStore,
        *,
        ledger: Callable[[], Ledger | None],
        today: Callable[[], date],
        on_changed: Callable[[], None],
        on_ai: Callable[[str], AIStarter] | None = None,
        now: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.page = page
        self.tr = translate
        self.bridge = bridge
        self.store = store
        self.ledger = ledger
        self.today = today
        self.on_changed = on_changed
        self.on_ai = on_ai
        self.now = now
        self.busy = False
        self.apps: tuple[NotificationApp, ...] = ()
        self.access: NotificationAccess | None = None
        self.notice: str | None = None
        self.heading = ft.Text(size=18)
        self.help = ft.Text()
        self.mode = ft.Text()
        self.permission = ft.Text()
        self.last_checked = ft.Text()
        self.status = ft.Text(color=ft.Colors.ERROR, selectable=True)
        self.enable = ft.TextButton(on_click=self._enable)
        self.disable = ft.TextButton(on_click=self._disable)
        self.access_button = ft.TextButton(on_click=self._open_access)
        self.refresh = ft.TextButton(on_click=self._refresh)
        self.search = ft.TextField(on_change=self._search_changed)
        self.apps_heading = ft.Text(size=18)
        self.app_list = ft.Column(spacing=0)
        self.app_checkboxes: dict[str, ft.Checkbox] = {}
        self.rules_heading = ft.Text(size=18)
        self.rules = ft.Column(spacing=8)
        self.clear = ft.TextButton(on_click=self._confirm_clear)
        self.reset = ft.TextButton(on_click=self._confirm_reset)
        self.control = ft.Column(
            [
                self.heading,
                self.help,
                self.mode,
                self.permission,
                self.last_checked,
                ft.Row([self.enable, self.disable, self.access_button, self.refresh], wrap=True),
                self.status,
                self.apps_heading,
                self.search,
                self.app_list,
                self.rules_heading,
                self.rules,
                ft.Row([self.clear, self.reset], wrap=True),
            ],
            spacing=8,
        )
        self.render()

    def render(self) -> None:
        self.heading.value = self.tr("notification_settings_title")
        self.help.value = self.tr("notification_settings_help")
        self.mode.value = self.tr(f"notification_settings_mode_{self.store.mode()}")
        if self.access is None:
            access_key = "notification_settings_access_unknown"
        elif not self.access.granted:
            access_key = "notification_settings_access_required"
        elif not self.access.connected:
            access_key = "notification_settings_disconnected"
        else:
            access_key = "notification_settings_connected"
        self.permission.value = self.tr(access_key)
        last_checked = self.store.last_checked()
        self.last_checked.value = (
            self.tr(
                "notification_settings_last_check",
                time=format_epoch(last_checked),
            )
            if last_checked is not None
            else self.tr("notification_settings_never_checked")
        )
        self.status.value = self.tr(self.notice) if self.notice else ""
        self.status.visible = bool(self.notice)
        self.enable.content = self.tr("notification_settings_enable")
        self.enable.visible = self.store.mode() == "off"
        self.disable.content = self.tr("notification_settings_disable")
        self.disable.visible = self.store.mode() != "off"
        self.access_button.content = self.tr("notification_settings_access_button")
        self.refresh.content = self.tr("notification_settings_refresh")
        self.apps_heading.value = self.tr("notification_settings_apps")
        self.search.label = self.tr("notification_settings_search")
        self.rules_heading.value = self.tr("notification_settings_rules")
        self.clear.content = self.tr("notification_settings_clear")
        self.reset.content = self.tr("notification_settings_reset")
        for control in (
            self.enable,
            self.disable,
            self.access_button,
            self.refresh,
            self.search,
            self.clear,
            self.reset,
        ):
            control.disabled = self.busy

        selected = set(self.store.selected_apps())
        known = {app.id for app in self.apps}
        sources = list(self.apps) + [
            NotificationApp(app_id, app_id) for app_id in sorted(selected - known)
        ]
        query = (self.search.value or "").strip().casefold()
        matching = [app for app in sources if query in f"{app.name} {app.id}".casefold()]
        checkboxes: list[ft.Control] = []
        for app in matching[:100]:
            if app.id not in self.app_checkboxes:
                self.app_checkboxes[app.id] = ft.Checkbox(data=app.id, on_change=self._app_changed)
            checkbox = self.app_checkboxes[app.id]
            checkbox.label = f"{app.name} ({app.id})"
            checkbox.value = app.id in selected
            checkbox.disabled = self.busy
            checkboxes.append(checkbox)
        self.app_list.controls = checkboxes
        if len(matching) > 100:
            self.app_list.controls.append(ft.Text(self.tr("notification_settings_search_more")))
        elif not matching:
            self.app_list.controls.append(ft.Text(self.tr("notification_settings_apps_empty")))

        names = {app.id: app.name for app in sources}
        controls: list[ft.Control] = []
        for app_id in sorted(selected, key=lambda value: (names[value].casefold(), value)):
            controls.append(ft.Text(names[app_id], weight=ft.FontWeight.W_600))
            for position, entry in enumerate(self.store.rules(app_id), start=1):
                controls.append(
                    ft.Row(
                        [
                            ft.Text(f"{position}. {entry.rule.name}", expand=True),
                            ft.Text(self.tr(f"notification_rule_{entry.rule.action}")),
                            ft.Text(
                                self.tr(
                                    "notification_settings_rule_enabled"
                                    if entry.rule.enabled
                                    else "notification_settings_rule_disabled"
                                )
                            ),
                            ft.TextButton(
                                self.tr("notification_settings_edit"),
                                data=(app_id, entry.id),
                                on_click=self._edit_rule,
                                disabled=self.busy,
                            ),
                            ft.TextButton(
                                self.tr("notification_settings_duplicate"),
                                data=(app_id, entry.id),
                                on_click=self._duplicate_rule,
                                disabled=self.busy,
                            ),
                            ft.TextButton(
                                self.tr("notification_settings_delete"),
                                data=(app_id, entry.id),
                                on_click=self._confirm_delete,
                                disabled=self.busy,
                            ),
                        ],
                        wrap=True,
                    )
                )
            controls.append(
                ft.TextButton(
                    self.tr("notification_settings_add_rule"),
                    data=app_id,
                    on_click=self._add_rule,
                    disabled=self.busy,
                )
            )
        if not controls:
            controls.append(ft.Text(self.tr("notification_settings_choose_apps")))
        self.rules.controls = controls
        if self.control.parent is not None:
            self.control.update()

    async def _run(self, action: Callable[[], Awaitable[None]]) -> None:
        if self.busy:
            return
        self.busy = True
        self.notice = None
        self.render()
        try:
            await action()
        except NotificationBridgeError as exc:
            logger.warning("Notification bridge operation failed (code=%s)", exc.code)
            self.access = None
            self.notice = "notification_settings_bridge_error"
        except NotificationStoreError:
            logger.warning("Notification settings operation was refused")
            self.notice = "notification_settings_store_error"
        except sqlite3.Error:
            logger.exception("Notification settings storage failed")
            self.notice = "notification_settings_store_error"
        finally:
            self.busy = False
            self.render()
            self.on_changed()

    async def load(self) -> None:
        async def action() -> None:
            self.access = await self.bridge.status()
            self.apps = await self.bridge.apps()

        await self._run(action)

    async def _refresh(self, event: ft.Event) -> None:
        await self.load()

    async def _enable(self, event: ft.Event) -> None:
        async def action() -> None:
            await set_manual_monitoring(self.bridge, self.store, enabled=True)
            self.access = await self.bridge.status()

        await self._run(action)

    async def _disable(self, event: ft.Event) -> None:
        async def action() -> None:
            await set_manual_monitoring(self.bridge, self.store, enabled=False)
            self.access = await self.bridge.status()

        await self._run(action)

    async def _open_access(self, event: ft.Event) -> None:
        await self._run(lambda: open_notification_access_settings(self.bridge))

    def _search_changed(self, event: ft.Event) -> None:
        self.render()

    def _app_changed(self, event: ft.Event[ft.Checkbox]) -> None:
        if self.busy:
            return
        self.notice = None
        try:
            self.store.set_app_selected(event.control.data, bool(event.control.value))
        except NotificationStoreError:
            logger.warning("Notification source selection was refused")
            self.notice = "notification_settings_store_error"
        except sqlite3.Error:
            logger.exception("Notification source selection storage failed")
            self.notice = "notification_settings_store_error"
        self.render()
        self.on_changed()

    def _saved(self, rule: object) -> None:
        self.render()
        self.on_changed()

    def _editor(self, app_id: str, *, rule_id: str | None = None, duplicate: bool = False) -> None:
        try:
            draft = None
            if duplicate:
                draft = next(
                    (entry.rule for entry in self.store.rules(app_id) if entry.id == rule_id), None
                )
                if draft is None:
                    raise NotificationStoreError("Rule is unavailable.")
                rule_id = None
            NotificationRuleEditor(
                self.page,
                self.tr,
                self.store,
                app_id,
                rule_id=rule_id,
                draft=draft,
                ledger=self.ledger(),
                today=self.today,
                on_saved=self._saved,
                on_ai=self.on_ai(app_id) if self.on_ai is not None else None,
            ).open()
        except NotificationStoreError:
            logger.warning("Notification rule is unavailable")
            self.notice = "notification_settings_store_error"
            self.render()
        except sqlite3.Error:
            logger.exception("Notification rule editor storage failed")
            self.notice = "notification_settings_store_error"
            self.status.value = self.tr(self.notice)
            self.status.visible = True
            if self.status.parent is not None:
                self.status.update()

    def _add_rule(self, event: ft.Event[ft.TextButton]) -> None:
        self._editor(event.control.data)

    def _edit_rule(self, event: ft.Event[ft.TextButton]) -> None:
        app_id, rule_id = event.control.data
        self._editor(app_id, rule_id=rule_id)

    def _duplicate_rule(self, event: ft.Event[ft.TextButton]) -> None:
        app_id, rule_id = event.control.data
        self._editor(app_id, rule_id=rule_id, duplicate=True)

    def _confirm_delete(self, event: ft.Event[ft.TextButton]) -> None:
        app_id, rule_id = event.control.data
        try:
            revision = self.store.rule_revision(app_id)
        except sqlite3.Error:
            logger.exception("Notification rule deletion lookup failed")
            self.notice = "notification_settings_store_error"
            self.status.value = self.tr(self.notice)
            self.status.visible = True
            if self.status.parent is not None:
                self.status.update()
            return
        error = ft.Text(color=ft.Colors.ERROR, selectable=True)

        async def cancel(_: ft.Event) -> None:
            self.page.pop_dialog()

        async def delete(_: ft.Event) -> None:
            try:
                self.store.delete_rule(rule_id, expected_revision=revision)
            except NotificationStoreError:
                logger.warning("Notification rule deletion was refused")
                error.value = self.tr("notification_settings_store_error")
                if error.parent is not None:
                    error.update()
            except sqlite3.Error:
                logger.exception("Notification rule deletion storage failed")
                error.value = self.tr("notification_settings_store_error")
                if error.parent is not None:
                    error.update()
            else:
                self.page.pop_dialog()
                self.render()
                self.on_changed()

        show_dialog(
            self.page,
            ft.AlertDialog(
                modal=True,
                title=self.tr("notification_settings_delete_title"),
                content=ft.Column(
                    [ft.Text(self.tr("notification_settings_delete_help")), error], tight=True
                ),
                actions=[
                    ft.TextButton(self.tr("crud_cancel"), on_click=cancel),
                    ft.TextButton(self.tr("notification_settings_delete"), on_click=delete),
                ],
            ),
        )

    def _confirm_clear(self, event: ft.Event) -> None:
        self._confirm_history(reset=False)

    def _confirm_reset(self, event: ft.Event) -> None:
        self._confirm_history(reset=True)

    def _confirm_history(self, *, reset: bool) -> None:
        error = ft.Text(color=ft.Colors.ERROR, selectable=True)

        async def cancel(_: ft.Event) -> None:
            self.page.pop_dialog()

        async def confirm(_: ft.Event) -> None:
            try:
                if reset:
                    self.store.reset_history(self.now())
                else:
                    self.store.clear_captured()
            except NotificationStoreError:
                logger.warning("Notification history operation was refused")
                error.value = self.tr("notification_settings_history_failed")
                error.update()
            except sqlite3.Error:
                logger.exception("Notification history storage failed")
                error.value = self.tr("notification_settings_history_failed")
                error.update()
            else:
                self.page.pop_dialog()
                self.render()
                self.on_changed()

        show_dialog(
            self.page,
            ft.AlertDialog(
                modal=True,
                title=self.tr(
                    "notification_settings_reset_title"
                    if reset
                    else "notification_settings_clear_title"
                ),
                content=ft.Column(
                    [
                        ft.Text(
                            self.tr(
                                "notification_settings_reset_help"
                                if reset
                                else "notification_settings_clear_help"
                            )
                        ),
                        error,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton(self.tr("crud_cancel"), on_click=cancel),
                    ft.TextButton(
                        self.tr(
                            "notification_settings_reset"
                            if reset
                            else "notification_settings_clear"
                        ),
                        on_click=confirm,
                    ),
                ],
            ),
        )
