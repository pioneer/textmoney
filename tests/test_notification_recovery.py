import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from finance_saf import FinanceSaf

import app.ui.notification_recovery as recovery_ui
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.notification_import import journal_identity
from app.infrastructure.notification_store import (
    Evaluation,
    IncomingNotification,
    NotificationStore,
    NotificationStoreError,
)
from app.journal import FILENAME, Journal
from app.ui.notification_recovery import NotificationRecovery


def _recovery(path: Path) -> tuple[NotificationStore, NotificationRecovery, MagicMock, MagicMock]:
    store = NotificationStore(path)
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    store.intake(
        IncomingNotification("bank.app", "key", 100, "v1", "Bank", "Paid"),
        now=101,
        source="manual",
        evaluate=lambda _: Evaluation("pending", draft={"amount": "5"}),
    )
    journal = Journal(AndroidFolder(MagicMock(spec=FinanceSaf), "content://tree/one", FILENAME))
    journal.ready = True
    journal.storage.loaded = True
    journal.source = journal.storage.draft = "Reviewed"
    item = store.items(("pending",))[0]
    item = store.begin_review(item.id, item.review_snapshot, journal_id=journal_identity(journal))
    store.begin_import(item.id, journal_id=item.journal_id or "", snapshot=item.review_snapshot)
    item = store.items(("saving",))[0]
    callback = MagicMock()
    page = MagicMock(spec=ft.Page)
    view = NotificationRecovery(
        page,
        lambda key, **values: key,
        store,
        item,
        journal,
        on_resolved=callback,
    )
    return store, view, callback, page


def test_recovery_requires_explicit_review_and_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, view, callback, page = _recovery(tmp_path / "notifications.sqlite")
    resolve = AsyncMock()
    monkeypatch.setattr(recovery_ui, "resolve_reviewed_import", resolve)
    try:
        asyncio.run(view._confirm(MagicMock()))
        resolve.assert_not_awaited()
        view.outcome.value = "not_saved"
        view.acknowledge.value = True
        view._changed(MagicMock())
        assert not view.confirm.disabled
        asyncio.run(view._confirm(MagicMock()))
        resolve.assert_awaited_once_with(
            store,
            view.item,
            view.journal,
            source_hash=hashlib.sha256(b"Reviewed").hexdigest(),
            saved=False,
        )
        callback.assert_called_once()
        page.pop_dialog.assert_called_once()
    finally:
        store.close()


def test_recovery_failure_preserves_review_and_disables_back_while_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, view, callback, page = _recovery(tmp_path / "notifications.sqlite")
    monkeypatch.setattr(
        recovery_ui,
        "resolve_reviewed_import",
        AsyncMock(side_effect=NotificationStoreError("changed")),
    )
    try:
        view.outcome.value = "saved"
        view.acknowledge.value = True
        asyncio.run(view._confirm(MagicMock()))
        assert view.status.value == "notification_recovery_failed"
        assert view.outcome.value == "saved" and view.acknowledge.value
        assert len(store.items(("saving",))) == 1
        callback.assert_not_called()
        view.busy = True
        view._close()
        page.pop_dialog.assert_not_called()
    finally:
        store.close()
