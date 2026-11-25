import asyncio
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click_async

import app.ui.notification_settings as notification_settings
from app.domain.notification_rules import Pattern, Rule
from app.infrastructure.notification_store import (
    Evaluation,
    IncomingNotification,
    NotificationStore,
)
from app.infrastructure.notifications_android import (
    AndroidNotifications,
    NotificationAccess,
    NotificationApp,
    NotificationBridgeError,
)
from app.ui.notification_rules import NotificationRuleEditor
from app.ui.notification_settings import NotificationSettingsPanel


def _panel(store: NotificationStore) -> tuple[NotificationSettingsPanel, MagicMock]:
    bridge = MagicMock(spec=AndroidNotifications)
    bridge.status = AsyncMock(return_value=NotificationAccess(True, True))
    bridge.apps = AsyncMock(
        return_value=(NotificationApp("bank.app", "Bank"), NotificationApp("other.app", "Other"))
    )
    page = MagicMock(spec=ft.Page)
    return (
        NotificationSettingsPanel(
            page,
            lambda key, **values: key,
            bridge,
            store,
            ledger=lambda: None,
            today=lambda: date(2026, 11, 23),
            on_changed=lambda: None,
        ),
        bridge,
    )


def test_settings_manual_setup_never_reads_notifications(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        panel, bridge = _panel(store)
        asyncio.run(panel.load())
        assert panel.permission.value == "notification_settings_connected"
        assert len(panel.app_list.controls) == 2
        checkbox = panel.app_checkboxes["bank.app"]
        event = MagicMock()
        event.control.data = "bank.app"
        event.control.value = True
        panel._app_changed(event)
        assert store.selected_apps() == ("bank.app",)
        assert panel.app_checkboxes["bank.app"] is checkbox
        asyncio.run(panel._enable(MagicMock()))
        assert store.mode() == "manual"
        asyncio.run(panel._disable(MagicMock()))
        assert store.mode() == "off"
        bridge.active.assert_not_awaited()
    finally:
        store.close()


def test_settings_reports_listener_failure_without_false_connected_state(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        panel, bridge = _panel(store)
        asyncio.run(panel.load())
        bridge.disable = AsyncMock(side_effect=NotificationBridgeError("listener_unavailable"))
        asyncio.run(panel._disable(MagicMock()))
        assert store.mode() == "off"
        assert panel.permission.value == "notification_settings_access_unknown"
        assert panel.status.value == "notification_settings_bridge_error"
    finally:
        store.close()


def test_settings_can_remove_uninstalled_sources_and_duplicate_disabled_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_app_selected("missing.app", True)
        store.save_rule(
            "rule",
            Rule("Declined", "missing.app", "ignore", body=Pattern("Declined"), enabled=True),
        )
        panel, _bridge = _panel(store)
        asyncio.run(panel.load())
        checkboxes = [item for item in panel.app_list.controls if isinstance(item, ft.Checkbox)]
        assert any(item.data == "missing.app" and item.value for item in checkboxes)
        opened: list[NotificationRuleEditor] = []
        monkeypatch.setattr(NotificationRuleEditor, "open", lambda editor: opened.append(editor))
        event = MagicMock()
        event.control.data = ("missing.app", "rule")
        panel._duplicate_rule(event)
        assert len(opened) == 1
        assert opened[0].name.value == "Declined"
        assert opened[0].enabled.value is False
        assert len(store.rules("missing.app")) == 1
        event.control.data = "missing.app"
        event.control.value = False
        panel._app_changed(event)
        assert store.selected_apps() == ()
    finally:
        store.close()


def test_rule_deletion_reports_storage_failure_without_removing_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_app_selected("bank.app", True)
        store.save_rule("rule", Rule("Declined", "bank.app", "ignore", body=Pattern("Declined")))
        panel, _bridge = _panel(store)
        shown = MagicMock()
        monkeypatch.setattr(notification_settings, "show_dialog", shown)
        event = MagicMock()
        event.control.data = ("bank.app", "rule")
        with monkeypatch.context() as patch:
            patch.setattr(
                store,
                "rule_revision",
                MagicMock(side_effect=sqlite3.OperationalError("storage unavailable")),
            )
            panel._confirm_delete(event)
            assert panel.status.value == "notification_settings_store_error"
            shown.assert_not_called()
        panel._confirm_delete(event)
        dialog = shown.call_args.args[1]
        with monkeypatch.context() as patch:
            patch.setattr(
                store,
                "delete_rule",
                MagicMock(side_effect=sqlite3.OperationalError("storage unavailable")),
            )
            asyncio.run(click_async(dialog.actions[-1]))
            assert dialog.content.controls[-1].value == "notification_settings_store_error"
        assert [entry.id for entry in store.rules("bank.app")] == ["rule"]
    finally:
        store.close()


def test_rule_editor_reports_storage_failure_without_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_app_selected("bank.app", True)
        panel, _bridge = _panel(store)
        opened = MagicMock()
        monkeypatch.setattr(NotificationRuleEditor, "open", opened)
        monkeypatch.setattr(
            store, "rules", MagicMock(side_effect=sqlite3.OperationalError("storage unavailable"))
        )
        panel._editor("bank.app")
        assert panel.status.value == "notification_settings_store_error"
        assert panel.status.visible
        opened.assert_not_called()
    finally:
        store.close()


def test_history_clearing_and_reset_require_explicit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        item = IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid")

        def evaluate(_: IncomingNotification) -> Evaluation:
            return Evaluation("pending", draft={"amount": "5"})

        store.intake(item, now=101, source="manual", evaluate=evaluate)
        panel, _bridge = _panel(store)
        panel.now = lambda: 200
        shown = MagicMock()
        monkeypatch.setattr(notification_settings, "show_dialog", shown)
        panel._confirm_clear(MagicMock())
        assert store.count(("pending",)) == 1
        dialog = shown.call_args.args[1]
        assert isinstance(dialog.actions[-1], ft.TextButton)
        asyncio.run(click_async(dialog.actions[-1]))
        assert store.count(("pending",)) == 0
        assert store.intake(item, now=102, source="manual", evaluate=evaluate) == "duplicate"
        panel._confirm_reset(MagicMock())
        dialog = shown.call_args.args[1]
        assert isinstance(dialog.actions[-1], ft.TextButton)
        asyncio.run(click_async(dialog.actions[-1]))
        assert store.intake(item, now=201, source="manual", evaluate=evaluate) == "too_old"
    finally:
        store.close()
