import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import msgpack
import pytest
from flet.messaging.protocol import configure_encode_object_for_msgpack
from flet.messaging.session import Session
from form_controls import transaction_form

from app.domain.ledger import EPOCH
from app.domain.storage import ConflictError
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.ui.dialogs import show_dialog
from app.ui.finance import FinanceView


def keyboard_session(platform: ft.PagePlatform = ft.PagePlatform.LINUX) -> Session:
    connection = MagicMock()
    encode = configure_encode_object_for_msgpack(ft.BaseControl)
    connection.send_message.side_effect = lambda message: msgpack.packb(message, default=encode)
    session = Session(connection)
    session.page.platform = platform
    msgpack.packb(session.get_page_patch(), default=encode)
    return session


async def press(session: Session, key: str = "Escape") -> None:
    dialog = session.page._dialogs.controls[-1]
    assert isinstance(dialog, ft.AlertDialog)
    assert isinstance(dialog.content, ft.KeyboardListener)
    await session.dispatch_event(dialog.content._i, "key_down", {"key": key})


@pytest.mark.parametrize(
    "platform", [ft.PagePlatform.LINUX, ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS]
)
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("focus", ["content", "cancel", "save"])
def test_escape_dispatches_the_cancel_button_event(
    platform: ft.PagePlatform, asynchronous: bool, focus: str
) -> None:
    async def scenario() -> None:
        session = keyboard_session(platform)
        callback = AsyncMock() if asynchronous else MagicMock()
        cancel = ft.TextButton("Скасувати", on_click=callback)
        save_callback = MagicMock()
        save = ft.TextButton("Save", on_click=save_callback)
        dialog = ft.AlertDialog(modal=True, actions=[cancel, save])
        show_dialog(session.page, dialog)

        listener = (
            dialog.content if focus == "content" else dialog.actions[0 if focus == "cancel" else 1]
        )
        assert isinstance(listener, ft.KeyboardListener)
        await session.dispatch_event(listener._i, "key_down", {"key": "ESCAPE"})

        callback.assert_called_once()
        if asynchronous:
            callback.assert_awaited_once()
        event = callback.call_args.args[0]
        assert event.control is cancel
        assert event.name == "click"
        assert event.page is session.page
        save_callback.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["Enter", "Tab", "a"])
def test_other_keys_do_not_cancel(key: str) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        callback = MagicMock()
        cancel = ft.TextButton("Cancel", on_click=callback)
        show_dialog(session.page, ft.AlertDialog(modal=True, actions=[cancel]))
        await press(session, key)
        callback.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", [ft.PagePlatform.ANDROID, ft.PagePlatform.IOS])
def test_mobile_keyboard_behavior_is_unchanged(platform: ft.PagePlatform) -> None:
    async def scenario() -> None:
        session = keyboard_session(platform)
        callback = MagicMock()
        cancel = ft.TextButton("Cancel", on_click=callback)
        content = ft.Text("Synthetic")
        dialog = ft.AlertDialog(modal=True, content=content, actions=[cancel])
        show_dialog(session.page, dialog)
        assert dialog.content is content
        assert dialog.actions == [cancel]
        assert session.page.on_keyboard_event is None
        callback.assert_not_called()

    asyncio.run(scenario())


def test_escape_closes_only_the_top_dialog_and_waits_for_dismissal() -> None:
    async def scenario() -> None:
        session = keyboard_session()
        page = session.page
        draft = ft.TextField(value="Unfinished parent draft")
        parent_callback = MagicMock()
        parent_cancel = ft.TextButton("Cancel", on_click=parent_callback)
        parent = ft.AlertDialog(modal=True, content=draft, actions=[parent_cancel])
        show_dialog(page, parent)
        dismissed = MagicMock()

        def cancel(event: ft.Event[ft.TextButton]) -> None:
            assert event.control is child_cancel
            page.pop_dialog()

        child_cancel = ft.TextButton("Keep editing", on_click=cancel)
        child = ft.AlertDialog(
            modal=True,
            actions=[child_cancel],
            on_dismiss=dismissed,
        )
        show_dialog(page, child)
        await press(session)
        assert not child.open and parent.open
        assert draft.value == "Unfinished parent draft"
        parent_callback.assert_not_called()
        dismissed.assert_not_called()

        await press(session)
        parent_callback.assert_not_called()
        await session.dispatch_event(child._i, "dismiss", None)
        dismissed.assert_called_once()
        await press(session)
        parent_callback.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("disabled_target", ["button", "dialog"])
def test_disabled_cancel_does_not_close_or_fall_through(disabled_target: str) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        parent_callback = MagicMock()
        parent_cancel = ft.TextButton("Parent cancel", on_click=parent_callback)
        show_dialog(session.page, ft.AlertDialog(modal=True, actions=[parent_cancel]))
        callback = MagicMock()
        cancel = ft.TextButton("Cancel", disabled=disabled_target == "button", on_click=callback)
        dialog = ft.AlertDialog(modal=True, disabled=disabled_target == "dialog", actions=[cancel])
        show_dialog(session.page, dialog)
        await press(session)
        callback.assert_not_called()
        parent_callback.assert_not_called()
        assert dialog.open

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["nonmodal", "date", "time"])
def test_native_dismissal_does_not_also_cancel_the_parent(kind: str) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        callback = MagicMock()
        cancel = ft.TextButton("Parent cancel", on_click=callback)
        parent = ft.AlertDialog(modal=True, actions=[cancel])
        show_dialog(session.page, parent)
        native_cancel = MagicMock()
        child = (
            ft.DatePicker()
            if kind == "date"
            else ft.TimePicker()
            if kind == "time"
            else ft.AlertDialog(actions=[ft.TextButton("Cancel", on_click=native_cancel)])
        )
        if isinstance(child, ft.AlertDialog):
            show_dialog(session.page, child)
            assert not isinstance(child.content, ft.KeyboardListener)
        else:
            session.page.show_dialog(child)
        assert session.page.on_keyboard_event is None
        callback.assert_not_called()
        native_cancel.assert_not_called()
        assert child.open and parent.open

    asyncio.run(scenario())


def test_cancel_callback_failures_reach_the_flet_error_boundary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        session = keyboard_session()

        async def fail(event: ft.Event[ft.TextButton]) -> None:
            raise RuntimeError("intentional cancellation failure")

        show_dialog(
            session.page,
            ft.AlertDialog(modal=True, actions=[ft.TextButton("Cancel", on_click=fail)]),
        )
        await press(session)

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "intentional cancellation failure" in caplog.text
    assert "Traceback" in caplog.text


async def finance_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Session, FinanceView, Journal]:
    session = keyboard_session()
    monkeypatch.setattr(FinanceView, "page", property(lambda _: session.page))
    view = FinanceView()
    journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
    await journal.load()
    await journal.create_account("Bank", "Bank", "UAH", opened=EPOCH)
    await journal.create_category("Food", "Food", opened=EPOCH)
    view.journal = journal
    view.timezone_name = "UTC"
    return session, view, journal


@pytest.mark.parametrize("category", [False, True])
@pytest.mark.parametrize("edit", [False, True])
def test_blocked_delete_escape_cancels_without_opening_or_saving_an_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: bool, edit: bool
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        original = (tmp_path / FILENAME).read_bytes()
        backups = set((tmp_path / "backups").iterdir())
        view._delete_entity(
            MagicMock(
                control=ft.IconButton(
                    data=("category", "Expenses:Food") if category else ("account", "Assets:Bank")
                )
            )
        )
        blocked = session.page._dialogs.controls[-1]
        assert isinstance(blocked, ft.AlertDialog)
        assert blocked.title == view.tr("crud_delete_blocked")
        if edit:
            action = blocked.actions[-1]
            assert isinstance(action, ft.KeyboardListener)
            await session.dispatch_event(action.content._i, "click", None)
            editor = session.page._dialogs.controls[-1]
            assert isinstance(editor, ft.AlertDialog) and editor is not blocked
            assert not blocked.open and editor.open and editor.title == view.tr("crud_edit")
            await press(session)
            assert not editor.open
        else:
            await press(session)
            assert not blocked.open
            assert session.page._dialogs.controls[-1] is blocked
        assert (tmp_path / FILENAME).read_bytes() == original
        assert set((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("nested", ["category", "conflict"])
def test_real_transaction_escape_preserves_parent_draft_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editing: bool, nested: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        if editing:
            await journal.add_expense(
                "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
            )
        original = (tmp_path / FILENAME).read_bytes()
        view._transaction_dialog(journal, journal.ledger.transactions[-1] if editing else None)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.description.value = "Unfinished draft"
        form.amount.value = "17."
        form.when.clock.value = "18:"
        if nested == "category":
            await session.dispatch_event(form.category_create._i, "click", None)
        else:
            view._error(ConflictError("crud_stale"), notify=True)
        child = session.page._dialogs.controls[-1]
        assert child is not parent and child.open
        await press(session)
        assert not child.open and parent.open
        assert form.description.value == "Unfinished draft"
        assert form.amount.value == "17."
        assert form.when.clock.value == "18:"
        await session.dispatch_event(child._i, "dismiss", None)
        await press(session)
        assert not parent.open
        assert (tmp_path / FILENAME).read_bytes() == original

    asyncio.run(scenario())


def test_real_edit_dialog_cannot_be_cancelled_during_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()

        async def save() -> None:
            started.set()
            await release.wait()

        view._edit_dialog("Synthetic form", [ft.TextField()], journal, save)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        cancel_listener, submit_listener = dialog.actions
        assert isinstance(cancel_listener, ft.KeyboardListener)
        assert isinstance(submit_listener, ft.KeyboardListener)
        cancel, submit = cancel_listener.content, submit_listener.content
        saving = asyncio.create_task(session.dispatch_event(submit._i, "click", None))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert cancel.disabled
            await press(session)
            assert dialog.open
            assert not saving.done()
        finally:
            release.set()
            await saving
        assert not dialog.open
        assert not cancel.disabled

    asyncio.run(scenario())
