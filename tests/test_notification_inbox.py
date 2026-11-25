import asyncio
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

import app.infrastructure.notification_store as notification_store
import app.ui.notification_inbox as notification_inbox
from app.domain.notification_rules import Pattern, Rule, TransactionMapping, Value
from app.infrastructure.notification_store import (
    Evaluation,
    IncomingNotification,
    NotificationStore,
)
from app.infrastructure.notifications_android import AndroidNotifications, NotificationBridgeError
from app.ui.notification_inbox import NotificationInbox, NotificationRecentPreview
from app.ui.notification_old_history import NotificationOldHistory


def _inbox(store: NotificationStore) -> tuple[NotificationInbox, MagicMock, MagicMock]:
    bridge = MagicMock(spec=AndroidNotifications)
    bridge.active = AsyncMock(
        return_value=(IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid 5 EUR"),)
    )
    review = MagicMock()
    inbox = NotificationInbox(
        lambda key, **values: key,
        bridge,
        store,
        page=MagicMock(),
        journal=lambda: None,
        on_review=review,
        on_rules=lambda item: None,
        on_recovery=lambda item: None,
        on_settings=lambda: None,
        on_changed=lambda: None,
        now=lambda: 101,
        today=lambda: date(2026, 11, 23),
    )
    return inbox, bridge, review


def test_inbox_only_checks_on_command_and_counts_pending(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.save_rule(
            "purchase",
            Rule(
                "Purchase",
                "bank.app",
                "suggest",
                body=Pattern("Paid {amount} EUR"),
                mapping=TransactionMapping(amount=Value("amount", captured=True)),
            ),
        )
        inbox, bridge, review = _inbox(store)
        bridge.active.assert_not_awaited()
        assert inbox.pending_count() == 0
        asyncio.run(inbox._check(MagicMock()))
        assert inbox.pending_count() == 1
        assert store.last_checked() == 101
        event = MagicMock()
        event.control.data = store.items(("pending",))[0]
        inbox._review(event)
        review.assert_called_once_with(event.control.data)
        assert inbox.pending_count() == 1
        asyncio.run(inbox._check(MagicMock()))
        assert inbox.pending_count() == 1
        inbox._skip(event)
        assert inbox.pending_count() == 0
        assert len(store.items(("skipped",))) == 1
    finally:
        store.close()


def test_inbox_reports_access_failure_and_stale_skip(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.save_rule(
            "purchase",
            Rule("Purchase", "bank.app", "suggest", body=Pattern("Paid {amount} EUR")),
        )
        inbox, bridge, _review = _inbox(store)
        asyncio.run(inbox._check(MagicMock()))
        event = MagicMock()
        event.control.data = store.items(("pending",))[0]
        bridge.active.return_value = (
            IncomingNotification("bank.app", "key", 100, "v2", "Bank", "Paid 6 EUR"),
        )
        asyncio.run(inbox._check(MagicMock()))
        inbox._skip(event)
        assert inbox.pending_count() == 1
        assert inbox.status.value == "notification_inbox_changed"
        bridge.active.side_effect = NotificationBridgeError("access_required")
        asyncio.run(inbox._check(MagicMock()))
        assert inbox.status.value == "notification_inbox_access_error"
        assert store.last_checked() == 101
    finally:
        store.close()


def test_skip_reports_storage_failure_without_removing_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
        )
        inbox, _bridge, _review = _inbox(store)
        event = MagicMock()
        event.control.data = store.items(("pending",))[0]
        monkeypatch.setattr(
            store, "skip", MagicMock(side_effect=sqlite3.OperationalError("storage unavailable"))
        )
        inbox._skip(event)
        assert inbox.status.value == "notification_inbox_storage_error"
        assert store.count(("pending",)) == 1
    finally:
        store.close()


def test_expiry_notice_can_be_acknowledged_without_clearing_new_expiries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "first", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
        )
        store.prune(101 + 30 * notification_store.DAY)
        inbox, _bridge, _review = _inbox(store)
        row = inbox.content.controls[0]
        assert isinstance(row, ft.Row)
        button = row.controls[1]
        assert isinstance(button, ft.TextButton) and button.data == 1
        event = MagicMock()
        event.control = button
        store.intake(
            IncomingNotification(
                "bank.app", "second", 101 + 30 * notification_store.DAY, "v1", "Bank", "Paid"
            ),
            now=101 + 30 * notification_store.DAY,
            source="manual",
            evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
        )
        store.prune(101 + 60 * notification_store.DAY)
        inbox._acknowledge_expired(event)
        assert store.expired_count() == 2
        assert inbox.status.value == "notification_inbox_expired_changed"
        row = inbox.content.controls[0]
        assert isinstance(row, ft.Row)
        button = row.controls[1]
        assert isinstance(button, ft.TextButton) and button.data == 2
        event.control = button
        monkeypatch.setattr(
            store,
            "acknowledge_expired",
            MagicMock(side_effect=sqlite3.OperationalError("storage unavailable")),
        )
        inbox._acknowledge_expired(event)
        assert store.expired_count() == 2
        assert inbox.status.value == "notification_inbox_storage_error"
        monkeypatch.undo()
        inbox._acknowledge_expired(event)
        assert store.expired_count() == 0
        assert not any(isinstance(control, ft.Row) for control in inbox.content.controls)
    finally:
        store.close()


def test_inbox_shows_notification_time_mapped_fields_and_validation_errors(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v1", None, "Paid 5 EUR"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation(
                "pending",
                "Purchase",
                {
                    "amount": "5",
                    "currency": "EUR",
                    "errors": [("account", "Required for import.")],
                },
            ),
        )
        inbox, _bridge, _review = _inbox(store)
        inbox.tr = lambda key, **values: f"{key} {values}" if values else key
        inbox.render()
        card = inbox.content.controls[0]
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Column)
        text = [control.value for control in card.content.controls if isinstance(control, ft.Text)]
        assert "Purchase" in text
        assert any(value.startswith("notification_inbox_posted ") for value in text if value)
        assert "notification_inbox_title_unavailable" in text
        assert "Paid 5 EUR" in text
        assert "notification_rule_amount: 5" in text
        assert "notification_rule_currency: EUR" in text
        assert any(
            "notification_rule_error_required" in value for value in text if isinstance(value, str)
        )
        store.save_rule("new", Rule("New", "bank.app", "ignore", body=Pattern("Paid")))
        inbox.render()
        card = inbox.content.controls[0]
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Column)
        assert any(
            isinstance(control, ft.Text) and control.value == "notification_inbox_stale_rule"
            for control in card.content.controls
        )
    finally:
        store.close()


def test_recent_re_evaluation_requires_preview_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "recent", 100, "v1", "Bank", "Paid 5 EUR"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        store.intake(
            IncomingNotification("bank.app", "pending", 100, "v1", "Bank", "Paid 6 EUR"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", "Old", {"amount": "6"}),
        )
        store.save_rule(
            "purchase",
            Rule("Purchase", "bank.app", "suggest", body=Pattern("Paid {amount} EUR")),
        )
        inbox, bridge, _review = _inbox(store)
        opened: list[NotificationRecentPreview] = []
        monkeypatch.setattr(NotificationRecentPreview, "open", lambda view: opened.append(view))
        inbox._re_evaluate(MagicMock())
        assert len(opened) == 1
        assert len(opened[0].previews) == 1
        assert any(
            isinstance(control, ft.Text) and control.value == "notification_recent_previous_rule"
            for control in opened[0].form.controls
        )
        assert store.count(("unmatched",)) == 1
        assert store.count(("pending",)) == 1
        bridge.active.assert_not_awaited()
        asyncio.run(opened[0]._confirm(MagicMock()))
        assert store.count(("unmatched",)) == 0
        assert store.count(("pending",)) == 2
        assert next(item for item in store.items(("pending",)) if item.key == "pending").draft == {
            "amount": "6"
        }
        assert inbox.notice == "notification_recent_applied"
    finally:
        store.close()


def test_recent_re_evaluation_refuses_stale_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        store.save_rule("ignore", Rule("Ignore", "bank.app", "ignore", body=Pattern("Paid")))
        inbox, bridge, _review = _inbox(store)
        opened: list[NotificationRecentPreview] = []
        monkeypatch.setattr(NotificationRecentPreview, "open", lambda view: opened.append(view))
        inbox._re_evaluate(MagicMock())
        store.save_rule("other", Rule("Other", "bank.app", "ignore", body=Pattern("Paid")))
        asyncio.run(opened[0]._confirm(MagicMock()))
        assert opened[0].status.value == "notification_recent_failed"
        assert store.count(("unmatched",)) == 1
        bridge.active.assert_not_awaited()
    finally:
        store.close()


def test_recent_re_evaluation_reports_capacity_and_journal_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "recent", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        store.intake(
            IncomingNotification("bank.app", "pending", 100, "v1", "Bank", "Existing"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", "Old", {"amount": "6"}),
        )
        store.save_rule("new", Rule("New", "bank.app", "suggest", body=Pattern("Paid")))
        inbox, _bridge, _review = _inbox(store)
        opened: list[NotificationRecentPreview] = []
        monkeypatch.setattr(NotificationRecentPreview, "open", lambda view: opened.append(view))
        inbox._re_evaluate(MagicMock())
        monkeypatch.setattr(notification_store, "MAX_PENDING", 1)
        asyncio.run(opened[0]._confirm(MagicMock()))
        assert opened[0].status.value == "notification_inbox_capacity"
        assert store.count(("unmatched",)) == 1
        current_journal = MagicMock(ready=True, source="changed")
        opened[0].journal = lambda: current_journal
        monkeypatch.setattr(notification_inbox, "journal_identity", lambda _: "a" * 64)
        asyncio.run(opened[0]._confirm(MagicMock()))
        assert opened[0].status.value == "notification_recent_failed"
        assert store.count(("unmatched",)) == 1
    finally:
        store.close()


def test_inbox_pages_large_queues_without_losing_pending_count(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        for index in range(21):
            store.intake(
                IncomingNotification("bank.app", str(index), 100, "v1", "Bank", "Paid"),
                now=101,
                source="manual",
                evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
            )
        inbox, _bridge, _review = _inbox(store)
        assert inbox.pending_count() == 21
        assert len(inbox.content.controls) == 20
        assert inbox.previous.disabled and not inbox.next.disabled
        inbox._next(MagicMock())
        assert len(inbox.content.controls) == 1
        assert not inbox.previous.disabled and inbox.next.disabled
        inbox._previous(MagicMock())
        assert len(inbox.content.controls) == 20
    finally:
        store.close()


def test_old_history_checks_only_on_request_and_recovers_one_selected_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.reset_history(100)
        inbox, bridge, _review = _inbox(store)
        opened: list[NotificationOldHistory] = []
        monkeypatch.setattr(NotificationOldHistory, "open", lambda view: opened.append(view))
        bridge.active.assert_not_awaited()
        asyncio.run(inbox._old_history(MagicMock()))
        assert bridge.active.await_count == 1
        assert len(opened) == 1
        assert store.count(("uncertain",)) == 0
        view = opened[0]
        asyncio.run(view._confirm(MagicMock()))
        assert store.count(("uncertain",)) == 0
        view.selection.value = "0"
        view._selected(MagicMock())
        assert view.sample.value == "Bank\nPaid 5 EUR"
        view.acknowledge.value = True
        view._changed(MagicMock())
        assert not view.confirm.disabled
        asyncio.run(view._confirm(MagicMock()))
        assert store.count(("uncertain",)) == 1
        assert inbox.tab == "recent"
        assert inbox.notice == "notification_old_recovered"
        asyncio.run(inbox._old_history(MagicMock()))
        assert inbox.notice == "notification_old_empty"
    finally:
        store.close()


def test_old_history_requires_fresh_source_selection_and_pages_results(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.reset_history(100)
        candidates = tuple(
            IncomingNotification("bank.app", str(i), 100, "v1", "Bank", "Paid") for i in range(21)
        )
        view = NotificationOldHistory(
            MagicMock(),
            lambda key, **values: key,
            store,
            candidates,
            now=lambda: 101,
            on_recovered=MagicMock(),
        )
        assert len(view.selection.options) == 20
        view.selection.value = "0"
        view._selected(MagicMock())
        view.acknowledge.value = True
        view._next(MagicMock())
        assert len(view.selection.options) == 1
        assert view.selection.value is None and not view.acknowledge.value
        view.selection.value = "20"
        view._selected(MagicMock())
        view.acknowledge.value = True
        store.set_app_selected("bank.app", False)
        asyncio.run(view._confirm(MagicMock()))
        assert view.status.value == "notification_old_failed"
        assert store.count(("uncertain",)) == 0
    finally:
        store.close()
