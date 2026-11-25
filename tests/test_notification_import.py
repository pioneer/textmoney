import asyncio
import hashlib
import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from finance_saf import FinanceSaf
from form_controls import change, click, click_async, transaction_form
from popup_keyboard import PopupMenuBackScope

import app.infrastructure.notification_import as notification_import
import app.ui.finance as finance_ui
from app.domain.ledger import EPOCH, Account, Category, Ledger, LedgerError
from app.domain.notification_rules import Pattern, Rule, TransactionMapping, Value
from app.infrastructure.ai import AIError, AISettings, Capability, Model, Provider
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.notification_import import (
    capture_background_notification,
    check_notifications,
    journal_identity,
    matching_rule_id,
    open_notification_access_settings,
    preview_re_evaluation,
    preview_recent_re_evaluation,
    read_review_draft,
    resolve_reviewed_import,
    save_reviewed_notification,
    set_manual_monitoring,
)
from app.infrastructure.notification_import import (
    test_rule as try_rule,
)
from app.infrastructure.notification_store import (
    Evaluation,
    IncomingNotification,
    NotificationStore,
    NotificationStoreError,
)
from app.infrastructure.notifications_android import AndroidNotifications, NotificationBridgeError
from app.journal import FILENAME, Journal
from app.localization import date_input
from app.storage import WriteError
from app.ui.finance import FinanceView, NotificationReview


def test_android_notification_composition_navigation_and_badge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = MagicMock(spec=ft.Page)
    page.platform = ft.PagePlatform.ANDROID
    page.web = False
    page.width = 390
    page._dialogs = MagicMock(controls=[])
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    native = MagicMock()
    native.call = AsyncMock(return_value={"ok": True, "value": str(tmp_path)})
    monkeypatch.setattr(finance_ui, "NotificationBridge", lambda: native)
    monkeypatch.setattr(finance_ui.time, "time", lambda: 100)
    view = FinanceView()
    view.notification_supported = True
    view.startup_complete = True
    asyncio.run(view._initialize_notifications())
    store = view.notification_store
    assert store is not None
    native.call.assert_awaited_once_with("storage_dir")
    assert view.notification_inbox is not None and view.notification_settings is not None
    view._navigate("transactions")
    view._open_notification_inbox(MagicMock())
    assert view.current_screen == "settings"
    assert not view.notification_inbox_open
    view._navigate("transactions")
    with monkeypatch.context() as patch:
        failure = MagicMock()
        patch.setattr(view, "_error", failure)
        patch.setattr(store, "count", MagicMock(side_effect=sqlite3.OperationalError("failed")))
        view._open_notification_inbox(MagicMock())
        assert isinstance(failure.call_args.args[0], NotificationStoreError)
        assert view.current_screen == "transactions"
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    store.intake(
        IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid"),
        now=101,
        source="manual",
        evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
    )
    view._notification_changed()
    transactions = view.bottom_buttons[1]
    assert view.bottom_items[1][1].badge == "1"
    assert view.notification_import_button.badge == "1"
    assert "1" in (view.bottom_semantics[1].label or "")
    rail_label = view.nav_rail.destinations[1].label
    assert isinstance(rail_label, ft.Semantics)
    assert "1" in (rail_label.label or "")
    inbox = view.notification_inbox
    with monkeypatch.context() as patch:
        patch.setattr(inbox, "pending_count", lambda: 101)
        view._build_navigation()
        rail_label = view.nav_rail.destinations[1].label
        assert isinstance(rail_label, ft.Semantics)
        assert "101" in (rail_label.label or "")
        assert view.bottom_items[1][1].badge == "99+"
    view._build_navigation()
    store.set_app_selected("bank.app", False)
    store.set_mode("off")
    view._open_notification_inbox(MagicMock())
    assert view.notification_inbox_open
    store.set_app_selected("bank.app", True)
    store.set_mode("manual")
    assert view._back()
    view._navigate("transactions")
    original = view.operations.location()
    view._open_notification_inbox(MagicMock())
    assert view.notification_inbox_open
    assert view.current_screen == "transactions"
    assert view.transactions_screen.controls == [view.notification_inbox.control]
    assert transactions is view.bottom_buttons[1]
    assert view._back()
    assert not view.notification_inbox_open
    assert view.operations.location() == original
    assert view._forward() and view.notification_inbox_open
    view._notification_open_settings()
    assert view.current_screen == "settings"
    assert view.notification_section in view.settings_screen.controls
    native.call.assert_awaited_once_with("storage_dir")
    store.close()


def test_inbox_refreshes_access_and_app_names_without_checking_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = MagicMock(spec=ft.Page)
    page.platform, page.web, page.width = ft.PagePlatform.ANDROID, False, 390
    page._dialogs = MagicMock(controls=[])
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    monkeypatch.setattr(finance_ui.time, "time", lambda: 101)
    status: list[object] = [{"ok": True, "value": {"access": False, "connected": False}}]
    apps: list[object] = [[{"id": "bank.app", "name": "Example Bank"}]]
    native = MagicMock()

    async def call(method: str, **_arguments: object) -> object:
        if method == "storage_dir":
            return {"ok": True, "value": str(tmp_path)}
        if method == "status":
            return status[0]
        if method == "apps":
            return {"ok": True, "value": apps[0]}
        raise AssertionError(f"Unexpected bridge call: {method}")

    native.call = AsyncMock(side_effect=call)
    monkeypatch.setattr(finance_ui, "NotificationBridge", lambda: native)
    view = FinanceView()
    view.notification_supported = True
    view.startup_complete = True
    asyncio.run(view._initialize_notifications())
    store = view.notification_store
    inbox = view.notification_inbox
    panel = view.notification_settings
    assert store is not None and inbox is not None and panel is not None
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
        )
        view._navigate("transactions")
        page.run_task.reset_mock()
        view._open_notification_inbox(MagicMock())
        page.run_task.assert_called_once_with(panel.load)
        inbox.render()
        assert inbox.access_status.value == view.tr("notification_settings_access_unknown")
        asyncio.run(panel.load())
        assert inbox.access_status.value == view.tr("notification_settings_access_required")
        card = inbox.content.controls[0]
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Column)
        heading = card.content.controls[0]
        assert isinstance(heading, ft.Text) and heading.value == "Example Bank (bank.app)"
        store.set_mode("off")
        inbox.render()
        assert inbox.access_status.value == ""
        store.set_mode("manual")

        status[0] = {"ok": True, "value": {"access": True, "connected": False}}
        page.run_task.reset_mock()
        view._notification_lifecycle_changed(MagicMock(state=ft.AppLifecycleState.RESUME))
        page.run_task.assert_called_once_with(panel.load)
        asyncio.run(panel.load())
        assert inbox.access_status.value == view.tr("notification_settings_disconnected")

        status[0] = {"ok": False, "error": "listener_unavailable"}
        asyncio.run(panel.load())
        assert inbox.access_status.value == view.tr("notification_settings_bridge_error")
        status[0] = {"ok": True, "value": {"access": True, "connected": True}}
        apps[0] = []
        asyncio.run(panel.load())
        assert inbox.access_status.value == ""
        card = inbox.content.controls[0]
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Column)
        heading = card.content.controls[0]
        assert isinstance(heading, ft.Text) and heading.value == "bank.app"
        assert view._back()
        page.run_task.reset_mock()
        assert view._forward()
        page.run_task.assert_called_once_with(panel.load)
        assert all(call.args[0] != "active" for call in native.call.await_args_list)
    finally:
        store.close()


def test_notification_retention_pruned_on_startup_and_resume_without_reading_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = MagicMock(spec=ft.Page)
    page.platform, page.web, page.width = ft.PagePlatform.ANDROID, False, 390
    page._dialogs = MagicMock(controls=[])
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    clock = [100]
    monkeypatch.setattr(finance_ui.time, "time", lambda: clock[0])
    native = MagicMock()
    native.call = AsyncMock(return_value={"ok": True, "value": str(tmp_path)})
    monkeypatch.setattr(finance_ui, "NotificationBridge", lambda: native)
    store = NotificationStore(tmp_path / "notification-import.sqlite")
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    store.intake(
        IncomingNotification("bank.app", "old", 100, "v1", "Bank", "Paid"),
        now=100,
        source="manual",
        evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
    )
    store.close()

    clock[0] += 31 * 86400
    view = FinanceView()
    view.notification_supported = True
    view.startup_complete = True
    asyncio.run(view._initialize_notifications())
    store = view.notification_store
    assert store is not None
    assert store.count(("pending",)) == 0
    assert store.expired_count() == 1
    store.intake(
        IncomingNotification("bank.app", "new", clock[0], "v1", "Bank", "Paid"),
        now=clock[0],
        source="manual",
        evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
    )
    view._notification_changed()
    assert view.notification_import_button.badge == "1"
    clock[0] += 31 * 86400
    view.current_screen = "settings"
    view._notification_lifecycle_changed(MagicMock(state=ft.AppLifecycleState.RESUME))
    assert store.count(("pending",)) == 0
    assert store.expired_count() == 2
    assert view.notification_import_button.badge is None
    assert view.notification_settings is not None
    page.run_task.assert_called_once_with(view.notification_settings.load)
    clock[0] -= 1
    error = MagicMock()
    monkeypatch.setattr(view, "_error", error)
    view._notification_lifecycle_changed(MagicMock(state=ft.AppLifecycleState.RESUME))
    assert isinstance(error.call_args.args[0], NotificationStoreError)
    assert error.call_args.kwargs == {"notify": True}
    error.reset_mock()
    monkeypatch.setattr(store, "prune", MagicMock(side_effect=sqlite3.OperationalError("failed")))
    view._notification_lifecycle_changed(MagicMock(state=ft.AppLifecycleState.RESUME))
    assert isinstance(error.call_args.args[0], NotificationStoreError)
    assert error.call_args.kwargs == {"notify": True}
    page.run_task.assert_called_once_with(view.notification_settings.load)
    native.call.assert_awaited_once_with("storage_dir")
    store.close()


def test_notification_ai_checks_model_and_opens_consent_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock(spec=ft.Page)
    page.platform, page.web, page.width = ft.PagePlatform.ANDROID, False, 390
    page._dialogs = MagicMock(controls=[])
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    settings = MagicMock(spec=AISettings)
    model = Model(Provider.OPENAI, "gpt-5.4-mini")
    settings.resolve = AsyncMock(return_value=(model, "test-key"))
    view._ai_settings = settings
    view.preferences = MagicMock()
    consent = MagicMock()
    monkeypatch.setattr(finance_ui, "NotificationAIConsent", consent)
    callback = MagicMock()

    async def start() -> None:
        await view._notification_ai_starter("bank.app")("Bank", "Paid", callback)

    asyncio.run(start())
    settings.resolve.assert_awaited_once_with(Capability.NOTIFICATION_RULE)
    consent.assert_called_once()
    assert consent.call_args.kwargs["body"] == "Paid"
    assert consent.call_args.kwargs["on_draft"] is callback
    consent.return_value.open.assert_called_once()
    callback.assert_not_called()

    settings.resolve.side_effect = AIError("ai_unconfigured")
    missing = MagicMock()
    monkeypatch.setattr(view, "_notification_ai_setup_dialog", missing)
    asyncio.run(start())
    missing.assert_called_once_with("ai_unconfigured")
    assert consent.call_count == 1


@pytest.mark.parametrize(
    ("platform", "web"),
    [
        (ft.PagePlatform.LINUX, False),
        (ft.PagePlatform.WINDOWS, False),
        (ft.PagePlatform.MACOS, False),
        (ft.PagePlatform.IOS, False),
        (ft.PagePlatform.ANDROID, True),
    ],
)
def test_notification_composition_never_initializes_on_unsupported_platform(
    platform: ft.PagePlatform, web: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = MagicMock(spec=ft.Page)
    page.platform, page.web = platform, web
    page.width = 390
    page._dialogs = MagicMock(controls=[])
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    bridge = MagicMock()
    monkeypatch.setattr(finance_ui, "NotificationBridge", bridge)
    view = FinanceView()
    asyncio.run(view._initialize_notifications())
    bridge.assert_not_called()
    assert view.notification_inbox is None
    assert view.notification_import_button not in view.transactions_screen.controls


class FakeBridge:
    def __init__(self, response: object) -> None:
        self.response = response

    async def call(self, method: str, **arguments: object) -> object:
        assert method == "active"
        assert arguments == {"apps": ["bank.app"]}
        return self.response


def test_monitoring_mode_changes_take_effect_before_native_calls(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    bridge = MagicMock(spec=AndroidNotifications)
    observed: list[str] = []

    async def enable() -> None:
        observed.append(store.mode())

    async def disable() -> None:
        observed.append(store.mode())
        raise NotificationBridgeError("listener_unavailable")

    bridge.enable = AsyncMock(side_effect=enable)
    bridge.disable = AsyncMock(side_effect=disable)
    try:
        asyncio.run(set_manual_monitoring(bridge, store, enabled=True))
        with pytest.raises(NotificationBridgeError, match="listener_unavailable"):
            asyncio.run(set_manual_monitoring(bridge, store, enabled=False))
        assert observed == ["manual", "off"]
        assert store.mode() == "off"
    finally:
        store.close()


def test_requesting_access_does_not_change_capture_mode(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    bridge = MagicMock(spec=AndroidNotifications)
    try:
        asyncio.run(open_notification_access_settings(bridge))
        bridge.enable.assert_awaited_once()
        bridge.open_settings.assert_awaited_once()
        assert store.mode() == "off"
    finally:
        store.close()


def test_off_mode_never_accesses_native_notifications(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_app_selected("bank.app", True)
        with pytest.raises(ValueError, match="Enable"):
            asyncio.run(
                check_notifications(
                    AndroidNotifications(FakeBridge(None)),
                    store,
                    Ledger(),
                    now=1_800_000_010,
                    today=date(2026, 11, 23),
                )
            )
    finally:
        store.close()


def test_manual_check_creates_review_draft_and_rechecks_do_not_duplicate(tmp_path) -> None:
    opened = date(2020, 1, 1)
    ledger = Ledger(
        accounts=(Account("Assets:Bank", "Bank", "EUR", opened),),
        categories=(Category("Expenses:Food", "Food", opened),),
    )
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
                body=Pattern.parse("Paid {amount} {currency}"),
                mapping=TransactionMapping(
                    kind=Value("expense"),
                    amount=Value("amount", captured=True),
                    currency=Value("currency", captured=True),
                    account=Value("Assets:Bank"),
                    category=Value("Expenses:Food"),
                    date=Value("2026-11-20"),
                ),
            ),
        )
        bridge = AndroidNotifications(
            FakeBridge(
                {
                    "ok": True,
                    "value": [
                        {
                            "app_id": "bank.app",
                            "key": "key",
                            "posted": 1_800_000_000,
                            "posted_millis": 1_800_000_000_000,
                            "version": "a" * 64,
                            "title": "Bank",
                            "body": "Paid 5 EUR",
                        }
                    ],
                }
            )
        )
        result = asyncio.run(
            check_notifications(bridge, store, ledger, now=1_800_000_010, today=date(2026, 11, 23))
        )
        assert result.recorded == 1
        item = store.items(("pending",))[0]
        assert item.draft is not None
        assert item.draft["amount"] == "5" and item.draft["errors"] == []
        decoded = read_review_draft(item)
        assert decoded.amount == Decimal("5") and decoded.date == date(2026, 11, 20)
        with pytest.raises(NotificationStoreError, match="invalid"):
            read_review_draft(replace(item, draft={**item.draft, "amount": "NaN"}))
        assert not ledger.transactions
        repeated = asyncio.run(
            check_notifications(bridge, store, ledger, now=1_800_000_011, today=date(2026, 11, 23))
        )
        assert repeated.duplicates == 1
        assert len(store.items(("pending",))) == 1
    finally:
        store.close()


def test_matching_rule_lookup_uses_priority_and_refuses_changed_sources(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        first = Rule("Purchase", "bank.app", "suggest", body=Pattern("Paid {amount}"))
        store.save_rule("first", first)
        store.save_rule("second", first)
        source = IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid 5")
        store.intake(
            source,
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", "Purchase", {"amount": "5"}),
        )
        item = store.items(("pending",))[0]
        assert matching_rule_id(store, item) == "first"
        store.reorder_rules("bank.app", ("second", "first"))
        assert matching_rule_id(store, item) is None
        store.reorder_rules("bank.app", ("first", "second"))
        assert matching_rule_id(store, item) == "first"
        store.intake(
            replace(source, version="v2", body="Paid 7"),
            now=102,
            source="manual",
            evaluate=lambda _: Evaluation("pending", "Purchase", {"amount": "7"}),
        )
        assert matching_rule_id(store, item) is None
    finally:
        store.close()


def test_background_event_is_only_evaluated_when_enabled_and_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        item = IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid 5 EUR")
        store.set_app_selected("bank.app", True)
        store.save_rule(
            "purchase",
            Rule(
                "Purchase",
                "bank.app",
                "suggest",
                body=Pattern("Paid {amount} {currency}"),
                mapping=TransactionMapping(
                    kind=Value("expense"),
                    amount=Value("amount", captured=True),
                    currency=Value("currency", captured=True),
                ),
            ),
        )
        rules = MagicMock(wraps=store.rules)
        monkeypatch.setattr(store, "rules", rules)
        kwargs = {"now": 101, "today": date(2026, 11, 23)}
        assert capture_background_notification(store, item, **kwargs) == "off"
        store.set_mode("manual")
        assert capture_background_notification(store, item, **kwargs) == "off"
        store.set_mode("background")
        unselected = replace(item, app_id="other.app")
        assert capture_background_notification(store, unselected, **kwargs) == "unselected"
        assert store.items(("pending", "unmatched", "ignored")) == ()
        rules.assert_not_called()
        assert capture_background_notification(store, item, **kwargs) == "recorded"
        pending = store.items(("pending",))
        assert len(pending) == 1
        assert pending[0].journal_id is None
        assert pending[0].draft is not None and pending[0].draft["amount"] == "5"
        assert capture_background_notification(store, item, **kwargs) == "duplicate"
        rules.assert_called_once_with("bank.app")
    finally:
        store.close()


def test_android_journal_identity_is_stable_and_bound_to_folder_and_filename() -> None:
    bridge = MagicMock(spec=FinanceSaf)
    one = Journal(AndroidFolder(bridge, "content://provider/tree/one", FILENAME))
    same = Journal(AndroidFolder(bridge, "content://provider/tree/one", FILENAME))
    other = Journal(AndroidFolder(bridge, "content://provider/tree/two", FILENAME))
    assert journal_identity(one) == journal_identity(same)
    assert journal_identity(one) != journal_identity(other)
    assert len(journal_identity(one)) == 64


@pytest.mark.parametrize("saved", [False, True])
def test_uncertain_save_requires_same_unchanged_reviewed_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved: bool
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    journal = Journal(AndroidFolder(MagicMock(spec=FinanceSaf), "content://tree/one", FILENAME))
    other = Journal(AndroidFolder(MagicMock(spec=FinanceSaf), "content://tree/two", FILENAME))
    for candidate in (journal, other):
        candidate.ready = True
        candidate.storage.loaded = True
        candidate.source = "reviewed journal"
        candidate.storage.draft = candidate.source
    poll = AsyncMock(return_value=True)
    monkeypatch.setattr(journal, "poll", poll)
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
        )
        item = store.items(("pending",))[0]
        item = store.begin_review(
            item.id, item.review_snapshot, journal_id=journal_identity(journal)
        )
        store.begin_import(item.id, journal_id=item.journal_id or "", snapshot=item.review_snapshot)
        item = store.items(("saving",))[0]
        source_hash = hashlib.sha256(journal.source.encode()).hexdigest()
        with pytest.raises(NotificationStoreError, match="another journal"):
            asyncio.run(
                resolve_reviewed_import(store, item, other, source_hash=source_hash, saved=True)
            )
        with pytest.raises(NotificationStoreError, match="Journal changed"):
            asyncio.run(
                resolve_reviewed_import(store, item, journal, source_hash=source_hash, saved=True)
            )
        assert len(store.items(("saving",))) == 1
        poll.return_value = False
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v2", "Bank", "Updated"),
            now=102,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        with pytest.raises(NotificationStoreError, match="Notification changed"):
            asyncio.run(
                resolve_reviewed_import(store, item, journal, source_hash=source_hash, saved=saved)
            )
        item = store.items(("saving",))[0]
        asyncio.run(
            resolve_reviewed_import(store, item, journal, source_hash=source_hash, saved=saved)
        )
        assert len(store.items(("imported" if saved else "pending",))) == 1
        assert store.items(("pending" if saved else "imported",)) == ()
    finally:
        store.close()


def test_rule_preview_reports_winner_and_requires_fresh_explicit_application(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid 5 EUR"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        store.save_rule(
            "first", Rule("First", "bank.app", "ignore", body=Pattern.parse("Paid {amount} EUR"))
        )
        store.save_rule(
            "second", Rule("Second", "bank.app", "ignore", body=Pattern.parse("{message}"))
        )
        item = store.items(("unmatched",))[0]
        preview = preview_re_evaluation(store, item, None, today=date(2026, 11, 23))
        assert preview.evaluation == Evaluation("ignored", "First")
        assert preview.captures == {"amount": "5"}
        assert store.items(("unmatched",))[0].id == item.id
        store.reorder_rules("bank.app", ("second", "first"))
        with pytest.raises(NotificationStoreError, match="changed"):
            store.apply_evaluation(item.id, preview.snapshot, preview.evaluation)
        refreshed = store.items(("unmatched",))[0]
        preview = preview_re_evaluation(store, refreshed, None, today=date(2026, 11, 23))
        assert preview.evaluation.rule_name == "Second"
        store.apply_evaluation(item.id, preview.snapshot, preview.evaluation)
        assert store.items(("ignored",))[0].rule_name == "Second"
    finally:
        store.close()


def test_recent_rule_preview_and_batch_are_explicit_and_atomic(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        for key in ("first", "second"):
            store.intake(
                IncomingNotification("bank.app", key, 100, "v1", "Bank", "Paid 5 EUR"),
                now=101,
                source="manual",
                evaluate=lambda _: Evaluation("unmatched"),
            )
        store.save_rule(
            "ignore", Rule("Ignore", "bank.app", "ignore", body=Pattern("Paid {amount} EUR"))
        )
        previews = preview_recent_re_evaluation(store, None, now=101, today=date(2026, 11, 23))
        assert len(previews) == 2
        assert store.count(("unmatched",)) == 2
        batch = tuple(
            (entry.item.id, entry.preview.snapshot, entry.preview.evaluation) for entry in previews
        )
        store.set_app_selected("bank.app", False)
        store.set_app_selected("bank.app", True)
        with pytest.raises(NotificationStoreError):
            store.apply_recent_evaluations(batch, now=101)
        assert store.count(("unmatched",)) == 2
    finally:
        store.close()


def test_recent_batch_rolls_back_if_a_later_preview_is_stale(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        for app_id in ("a.app", "b.app"):
            store.set_app_selected(app_id, True)
            store.intake(
                IncomingNotification(app_id, "key", 100, "v1", "Bank", "Paid"),
                now=101,
                source="manual",
                evaluate=lambda _: Evaluation("unmatched"),
            )
            store.save_rule(app_id, Rule("Ignore", app_id, "ignore", body=Pattern("Paid")))
        previews = preview_recent_re_evaluation(store, None, now=101, today=date(2026, 11, 23))
        batch = tuple(
            (entry.item.id, entry.preview.snapshot, entry.preview.evaluation) for entry in previews
        )
        stale = previews[-1].item.app_id
        store.save_rule("new", Rule("New", stale, "ignore", body=Pattern("Paid")))
        with pytest.raises(NotificationStoreError, match="changed"):
            store.apply_recent_evaluations(batch, now=101)
        assert store.count(("unmatched",)) == 2
        assert store.count(("ignored",)) == 0
        previews = preview_recent_re_evaluation(store, None, now=101, today=date(2026, 11, 23))
        batch = tuple(
            (entry.item.id, entry.preview.snapshot, entry.preview.evaluation) for entry in previews
        )
        store.apply_recent_evaluations(batch, now=101)
        assert store.count(("ignored",)) == 2
    finally:
        store.close()


def test_recent_preview_excludes_old_and_possible_duplicate_samples(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        store.intake(
            IncomingNotification("bank.app", "old", 100, "v1", "Bank", "Paid"),
            now=101,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        store.reset_history(101)
        store.recover_old(
            IncomingNotification("bank.app", "uncertain", 100, "v1", "Bank", "Paid"),
            now=102,
            confirm_possible_duplicate=True,
        )
        store.intake(
            IncomingNotification("bank.app", "recent", 103, "v1", "Bank", "Paid"),
            now=103,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
        store.save_rule("ignore", Rule("Ignore", "bank.app", "ignore", body=Pattern("Paid")))
        previews = preview_recent_re_evaluation(store, None, now=103, today=date(2026, 11, 23))
        assert [entry.item.key for entry in previews] == ["recent"]
        with pytest.raises(NotificationStoreError, match="clock moved backwards"):
            preview_recent_re_evaluation(store, None, now=102, today=date(2026, 11, 23))
        assert (
            preview_recent_re_evaluation(store, None, now=103 + 7 * 86400, today=date(2026, 11, 23))
            == ()
        )
        assert store.count(("uncertain",)) == 1
    finally:
        store.close()


def test_rule_draft_preview_reports_higher_priority_and_does_not_save(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_app_selected("bank.app", True)
        store.save_rule(
            "declined",
            Rule("Declined", "bank.app", "ignore", body=Pattern.parse("Paid {amount} EUR")),
        )
        proposed = Rule(
            "Purchase",
            "bank.app",
            "suggest",
            body=Pattern.parse("Paid {amount} EUR"),
            enabled=False,
            mapping=TransactionMapping(amount=Value("amount", captured=True)),
        )
        preview = try_rule(
            store,
            "purchase",
            proposed,
            1,
            title=None,
            body="Paid 5 EUR",
            ledger=None,
            today=date(2026, 11, 23),
        )
        assert preview.proposed_matched
        assert preview.winning_rule_id == "declined"
        assert preview.evaluation.outcome == "ignored"
        first = try_rule(
            store,
            "purchase",
            proposed,
            0,
            title=None,
            body="Paid 5 EUR",
            ledger=None,
            today=date(2026, 11, 23),
        )
        assert first.winning_rule_id == "purchase"
        assert first.evaluation.outcome == "pending"
        assert first.captures == {"amount": "5"}
        assert first.evaluation.draft is not None
        assert first.evaluation.draft["amount"] == "5"
        assert [entry.id for entry in store.rules("bank.app")] == ["declined"]
        assert store.items(("pending", "ignored", "unmatched")) == ()
        with pytest.raises(NotificationStoreError, match="position"):
            try_rule(
                store, "purchase", proposed, 2, title=None, body=None, ledger=None, today=EPOCH
            )
        incomplete = replace(
            proposed, mapping=TransactionMapping(amount=Value("missing", captured=True))
        )
        result = try_rule(
            store,
            "purchase",
            incomplete,
            0,
            title=None,
            body="Paid 5 EUR",
            ledger=None,
            today=EPOCH,
        )
        assert result.evaluation.draft is not None
        errors = result.evaluation.draft["errors"]
        assert isinstance(errors, (list, tuple))
        assert ("amount", "Mapped value is missing.") in errors
    finally:
        store.close()


def test_notification_editor_preserves_edits_and_saves_only_after_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2026, 11, 23))
        await journal.load()
        await journal.create_account("Cash", "Cash", "EUR", "100", opened=EPOCH)
        await journal.create_category("Food", "Food", opened=EPOCH)
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
                    body=Pattern.parse("Paid {amount} {currency}"),
                    mapping=TransactionMapping(
                        kind=Value("expense"),
                        amount=Value("amount", captured=True),
                        currency=Value("currency", captured=True),
                        account=Value("Assets:Cash"),
                        category=Value("Expenses:Food"),
                        date=Value("2026-11-20"),
                        time=Value("12:30"),
                    ),
                ),
            )
            bridge = AndroidNotifications(
                FakeBridge(
                    {
                        "ok": True,
                        "value": [
                            {
                                "app_id": "bank.app",
                                "key": "key",
                                "posted": 1_800_000_000,
                                "posted_millis": 1_800_000_000_000,
                                "version": "a" * 64,
                                "title": "Bank",
                                "body": "Paid 5 EUR",
                            }
                        ],
                    }
                )
            )
            await check_notifications(
                bridge, store, journal.ledger, now=1_800_000_010, today=date(2026, 11, 23)
            )
            item = store.items(("pending",))[0]
            monkeypatch.setattr(notification_import, "journal_identity", lambda _: "a" * 64)
            monkeypatch.setattr(finance_ui, "journal_identity", lambda _: "a" * 64)
            store.bind_journal(item.id, "a" * 64)
            review = NotificationReview(store, store.items(("pending",))[0])
            page = MagicMock(spec=ft.Page)
            page.platform = ft.PagePlatform.ANDROID
            page.web = False
            page.width = 390
            monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
            view = FinanceView()
            view.journal = journal
            view._refresh()
            view._transaction_dialog(journal, review=review)
            dialog = page.show_dialog.call_args.args[0]
            form = transaction_form(dialog)
            assert form.sign.selected == ["-"]
            assert form.account.value == "Assets:Cash"
            assert form.category.value == "Expenses:Food"
            assert form.amount.value == "5"
            assert form.when.occurred.value == date_input(date(2026, 11, 20), "en")
            assert form.when.clock.value == "12:30"
            assert len(journal.ledger.transactions) == 1
            form.description.value = "Edited while reviewing"
            form.amount.value = "6"
            dialog.open = True
            page._dialogs = MagicMock(controls=[dialog])
            event = MagicMock()
            event.control.confirm_pop = AsyncMock()
            await view._system_back(event)
            event.control.confirm_pop.assert_awaited_once_with(False)
            view._transaction_dialog(journal, review=review)
            dialog = page.show_dialog.call_args.args[0]
            form = transaction_form(dialog)
            assert form.description.value == "Edited while reviewing"
            assert form.amount.value == "6"
            form.fee_enabled.value = True
            form.fee.value = "2"
            editors: list[finance_ui.NotificationRuleEditor] = []
            previews: list[finance_ui.NotificationRuleApplication] = []
            monkeypatch.setattr(
                finance_ui.NotificationRuleEditor, "open", lambda editor: editors.append(editor)
            )
            monkeypatch.setattr(
                finance_ui.NotificationRuleApplication,
                "open",
                lambda preview: previews.append(preview),
            )
            assert isinstance(dialog.content, PopupMenuBackScope)
            assert isinstance(dialog.content.content, ft.Column)
            source_tile = next(
                control
                for control in dialog.content.content.controls
                if isinstance(control, ft.ExpansionTile)
                and control.title == view.tr("notification_source")
            )
            assert source_tile.controls is not None
            source_actions = source_tile.controls[-1]
            assert isinstance(source_actions, ft.Row)
            edit_button = next(
                control
                for control in source_actions.controls
                if isinstance(control, ft.TextButton)
                and control.content == view.tr("notification_edit_matching_rule")
            )
            click(edit_button)
            assert editors[-1].rule_id == "purchase"
            assert editors[-1].name.value == "Purchase"
            editors.clear()
            original_rule = store.rules("bank.app")[0].rule
            store.save_rule("purchase", replace(original_rule, name="Changed elsewhere"))
            with monkeypatch.context() as patch:
                report_error = MagicMock()
                patch.setattr(view, "_error", report_error)
                click(edit_button)
                assert not editors
                assert isinstance(report_error.call_args.args[0], NotificationStoreError)
            store.save_rule("purchase", original_rule)
            rule_button = source_actions.controls[0]
            assert isinstance(rule_button, ft.TextButton)
            click(rule_button)
            editor = editors[0]
            editor.name.value = "Revised purchase"
            editor.body_pattern.value = "Paid {amount} {currency}"
            editor.priority.value = "1"
            editor.enabled.value = True
            for field, mode, value in (
                ("kind", "fixed", "expense"),
                ("amount", "fixed", "9"),
                ("currency", "fixed", "EUR"),
                ("account", "fixed", "Assets:Cash"),
                ("category", "fixed", "Expenses:Food"),
                ("description", "fixed", "From revised rule"),
                ("date", "fixed", "2026-11-20"),
                ("time", "fixed", "12:30"),
            ):
                editor.mappings[field][0].value = mode
                editor.mappings[field][1].value = value
            saved_rule = editor.save_rule()
            assert editor.on_saved is not None
            editor.on_saved(saved_rule)
            assert len(previews) == 1
            assert form.amount.value == "6"
            assert form.description.value == "Edited while reviewing"
            await previews[0]._apply(MagicMock())
            assert form.amount.value == "9"
            assert form.description.value == "From revised rule"
            assert form.fee_enabled.value and form.fee.value == "2"
            assert len(journal.ledger.transactions) == 1
            form.fee_enabled.value = False
            form.fee.value = "0"
            form.amount.value = "6"
            form.description.value = "Edited while reviewing"
            currency = next(
                control
                for control in dialog.content.content.controls
                if control.data == "notification_currency"
            )
            assert isinstance(currency, ft.TextField)
            currency.value = "USD"
            await click_async(dialog.actions[-1])
            assert len(store.items(("pending",))) == 1
            assert len(journal.ledger.transactions) == 1
            currency.value = "EUR"
            form.sign.selected = []
            await click_async(dialog.actions[-1])
            assert len(journal.ledger.transactions) == 1
            form.sign.selected = ["-"]
            change(form.sign)
            form.amount.value = "invalid"
            await click_async(dialog.actions[-1])
            assert len(store.items(("pending",))) == 1
            assert len(journal.ledger.transactions) == 1
            form.amount.value = "6"
            await click_async(dialog.actions[-1])
            assert len(store.items(("imported",))) == 1
            assert len(journal.ledger.transactions) == 2
            assert journal.ledger.transactions[-1].narration == "Edited while reviewing"

            source = IncomingNotification(
                "bank.app", "second", 1_800_000_000, "v1", "Bank", "Paid 5 EUR"
            )
            assert item.draft is not None
            store.intake(
                source,
                now=1_800_000_011,
                source="manual",
                evaluate=lambda _: Evaluation("pending", "Purchase", item.draft),
            )
            second = store.items(("pending",))[0]
            store.bind_journal(second.id, "a" * 64)
            second_review = NotificationReview(store, store.items(("pending",))[0])
            view._transaction_dialog(journal, review=second_review)
            dialog = page.show_dialog.call_args.args[0]
            changed = replace(source, version="v2", body="Paid 7 EUR")
            assert (
                store.intake(
                    changed,
                    now=1_800_000_012,
                    source="manual",
                    evaluate=lambda _: Evaluation("pending", "Purchase", item.draft),
                )
                == "changed"
            )
            await click_async(dialog.actions[-1])
            assert journal.ready
            assert len(journal.ledger.transactions) == 2
            assert store.items(("pending",))[0].changed
            assert view.message_key == "notification_state_error"
        finally:
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "validation", "uncertain"])
def test_reviewed_save_reserves_and_resolves_only_verified_journal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path), FILENAME)
        journal = Journal(folder, today=lambda: date(2026, 11, 23))
        await journal.load()
        await journal.create_account("Cash", "Cash", "EUR", "100", opened=EPOCH)
        await journal.create_category("Food", "Food", opened=EPOCH)
        store = NotificationStore(tmp_path / "notifications.sqlite")
        try:
            store.set_mode("manual")
            store.set_app_selected("bank.app", True)
            store.intake(
                IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid 5 EUR"),
                now=101,
                source="manual",
                evaluate=lambda _: Evaluation("pending", "Purchase", {"amount": "5"}),
            )
            item_id = store.items(("pending",))[0].id
            monkeypatch.setattr(notification_import, "journal_identity", lambda _: "a" * 64)
            store.bind_journal(item_id, "a" * 64)
            snapshot = store.items(("pending",))[0].review_snapshot
            original_write = folder.write

            async def fail_after_write(data: bytes, *, expected_hash: str | None) -> None:
                await original_write(data, expected_hash=expected_hash)
                raise WriteError("uncertain", committed=True)

            if outcome == "uncertain":
                monkeypatch.setattr(folder, "write", fail_after_write)

            async def save() -> str | None:
                return await journal.add_expense(
                    "Assets:Cash",
                    "Expenses:Food",
                    "invalid" if outcome == "validation" else "5",
                    "Shop",
                    "2026-11-20",
                )

            if outcome == "success":
                await save_reviewed_notification(store, item_id, journal, save, snapshot=snapshot)
                assert len(store.items(("imported",))) == 1
                assert len(journal.ledger.transactions) == 2
            else:
                error = LedgerError if outcome == "validation" else WriteError
                with pytest.raises(error):
                    await save_reviewed_notification(
                        store, item_id, journal, save, snapshot=snapshot
                    )
                if outcome == "validation":
                    assert len(store.items(("pending",))) == 1
                else:
                    assert len(store.items(("saving",))) == 1
                    reloaded = Journal(DesktopFolder(str(tmp_path), FILENAME))
                    await reloaded.load()
                    assert len(reloaded.ledger.transactions) == 2
                    with pytest.raises(NotificationStoreError):
                        await save_reviewed_notification(
                            store, item_id, reloaded, save, snapshot=snapshot
                        )
        finally:
            store.close()

    asyncio.run(scenario())
