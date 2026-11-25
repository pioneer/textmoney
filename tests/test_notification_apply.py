import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from app.domain.notification_rules import Pattern, Rule
from app.infrastructure.notification_store import (
    Evaluation,
    IncomingNotification,
    NotificationStore,
    NotificationStoreError,
    QueueItem,
)
from app.ui.notification_apply import _FIELDS, NotificationRuleApplication


def _pending(store: NotificationStore, key: str) -> None:
    store.intake(
        IncomingNotification("bank.app", key, 100, "v1", "Bank", "Paid 5"),
        now=101,
        source="manual",
        evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
    )


def _view(
    store: NotificationStore,
    item: QueueItem,
    *,
    current_fields: dict[str, str | None] | None = None,
) -> tuple[NotificationRuleApplication, MagicMock]:
    callback = MagicMock()
    view = NotificationRuleApplication(
        MagicMock(spec=ft.Page),
        lambda key, **values: f"{key} {values}",
        store,
        item,
        ledger=None,
        today=date(2026, 11, 23),
        journal_id=None,
        current_fields=current_fields,
        on_applied=callback,
    )
    return view, callback


def test_rule_application_previews_edits_and_changes_only_confirmed_item(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        _pending(store, "first")
        _pending(store, "second")
        first = store.items(("pending",))[0]
        store.set_editing(first.id, True)
        first = store.items(("pending",))[0]
        store.save_rule("ignore", Rule("Ignore", "bank.app", "ignore", body=Pattern("Paid {x}")))
        with pytest.raises(NotificationStoreError, match="all transaction draft fields"):
            _view(store, first, current_fields={"amount": "incomplete"})
        fields: dict[str, str | None] = {name: None for name, _ in _FIELDS}
        fields["amount"] = "manually edited"
        view, callback = _view(store, first, current_fields=fields)
        texts = [control.value for control in view.form.controls if isinstance(control, ft.Text)]
        assert any("manually edited" in (text or "") for text in texts)
        assert len(store.items(("pending",))) == 2
        asyncio.run(view._apply(MagicMock()))
        assert len(store.items(("pending",))) == 1
        assert store.items(("ignored",))[0].id == first.id
        callback.assert_called_once_with(first.id, "ignored")
    finally:
        store.close()


def test_rule_application_rejects_stale_preview_and_unconfirmed_duplicate(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        store.set_app_selected("bank.app", True)
        _pending(store, "first")
        item = store.items(("pending",))[0]
        store.save_rule("ignore", Rule("Ignore", "bank.app", "ignore", body=Pattern("Paid {x}")))
        view, callback = _view(store, item)
        store.save_rule("other", Rule("Other", "bank.app", "ignore", body=Pattern("Other")))
        asyncio.run(view._apply(MagicMock()))
        assert view.status.value.startswith("notification_apply_failed")
        assert store.count(("pending",)) == 1
        callback.assert_not_called()

        store.reset_history(200)
        old = IncomingNotification("bank.app", "old", 100, "v1", "Bank", "Paid 5")
        item_id = store.recover_old(old, now=201, confirm_possible_duplicate=True)
        uncertain = store.items(("uncertain",))[0]
        assert uncertain.id == item_id
        view, callback = _view(store, uncertain)
        asyncio.run(view._apply(MagicMock()))
        assert store.count(("uncertain",)) == 1
        view.confirm_duplicate.value = True
        asyncio.run(view._apply(MagicMock()))
        assert store.count(("uncertain",)) == 0
        callback.assert_called_once_with(item_id, "ignored")
    finally:
        store.close()
