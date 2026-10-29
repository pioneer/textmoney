import asyncio
import logging
from datetime import date
from decimal import Decimal
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
from app.localization import date_input
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


@pytest.mark.parametrize("key", ["Tab", "a"])
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


@pytest.mark.parametrize("kind", ["date", "time"])
def test_native_dismissal_does_not_also_cancel_the_parent(kind: str) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        callback = MagicMock()
        cancel = ft.TextButton("Parent cancel", on_click=callback)
        parent = ft.AlertDialog(modal=True, actions=[cancel])
        show_dialog(session.page, parent)
        child = ft.DatePicker() if kind == "date" else ft.TimePicker()
        session.page.show_dialog(child)
        assert session.page.on_keyboard_event is None
        callback.assert_not_called()
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


@pytest.mark.parametrize("confirm", [False, True])
def test_category_migration_keyboard_gates_acknowledgement_and_retains_nested_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirm: bool
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        original = (tmp_path / FILENAME).read_bytes()
        backups = set((tmp_path / "backups").iterdir())
        view._edit_entity(MagicMock(control=ft.IconButton(data=("category", "Expenses:Food"))))
        editor = session.page._dialogs.controls[-1]
        assert isinstance(editor, ft.AlertDialog)
        assert isinstance(editor.content, ft.KeyboardListener)
        assert isinstance(editor.content.content, ft.Column)
        name = next(c for c in editor.content.content.controls if isinstance(c, ft.TextField))
        name.value = "Unsaved name"
        advanced = next(
            c for c in editor.content.content.controls if isinstance(c, ft.ExpansionTile)
        )
        entry = next(c for c in advanced.controls or [] if isinstance(c, ft.TextButton))
        await session.dispatch_event(entry._i, "click", None)
        target = session.page._dialogs.controls[-1]
        assert isinstance(target, ft.AlertDialog)
        assert isinstance(target.content, ft.KeyboardListener)
        assert isinstance(target.content.content, ft.Column)
        code = next(c for c in target.content.content.controls if isinstance(c, ft.TextField))
        code.value = "Meals"
        await session.dispatch_event(code._i, "focus", None)
        await press(session, "Enter")
        review = session.page._dialogs.controls[-1]
        assert isinstance(review, ft.AlertDialog)
        assert isinstance(review.content, ft.KeyboardListener)
        assert isinstance(review.content.content, ft.Column)
        await press(session, "Enter")
        assert review.open and (tmp_path / FILENAME).read_bytes() == original
        if confirm:
            row = next(c for c in review.content.content.controls if isinstance(c, ft.Row))
            ack = next(c for c in row.controls if isinstance(c, ft.Checkbox))
            ack.value = True
            await session.dispatch_event(ack._i, "change", None)
            await press(session, "Enter")
            assert not review.open and not target.open and not editor.open
            assert "Expenses:Meals" in journal.source and "Expenses:Food" not in journal.source
            assert journal.ledger.categories[0].name == "Food"
            assert len(set((tmp_path / "backups").iterdir()) - backups) == 1
        else:
            await press(session)
            assert not review.open and target.open and editor.open
            await session.dispatch_event(review._i, "dismiss", None)
            assert code.value == "Meals"
            await press(session)
            assert not target.open and editor.open
            await session.dispatch_event(target._i, "dismiss", None)
            assert name.value == "Unsaved name"
            await press(session)
            assert not editor.open
            assert (tmp_path / FILENAME).read_bytes() == original
            assert set((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("key", ["Enter", "Numpad Enter"])
@pytest.mark.parametrize("modal", [False, True])
def test_enter_dispatches_the_primary_button_event(
    asynchronous: bool, key: str, modal: bool
) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        cancel_callback = MagicMock()
        callback = AsyncMock() if asynchronous else MagicMock()
        cancel = ft.TextButton("Cancel", on_click=cancel_callback)
        submit = ft.TextButton("Confirm", on_click=callback)
        show_dialog(session.page, ft.AlertDialog(modal=modal, actions=[cancel, submit]))
        await press(session, key)
        callback.assert_called_once()
        if asynchronous:
            callback.assert_awaited_once()
        event = callback.call_args.args[0]
        assert event.control is submit and event.name == "click"
        assert event.page is session.page
        cancel_callback.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("editing", [False, True])
def test_transaction_enter_uses_existing_validation_and_preserves_invalid_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editing: bool
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        if editing:
            await journal.add_expense(
                "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
            )
        original = (tmp_path / FILENAME).read_bytes()
        backups = set((tmp_path / "backups").iterdir())
        view._transaction_dialog(journal, journal.ledger.transactions[-1] if editing else None)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.description.value = "Unfinished draft"
        form.amount.value = "not an amount"
        await session.dispatch_event(form.amount._i, "focus", None)
        await press(session, "Enter")
        error = session.page._dialogs.controls[-1]
        assert error is not parent and error.open and parent.open
        assert form.description.value == "Unfinished draft"
        assert form.amount.value == "not an amount"
        assert (tmp_path / FILENAME).read_bytes() == original
        assert set((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "platform", [ft.PagePlatform.LINUX, ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS]
)
def test_enter_uses_the_only_close_action(platform: ft.PagePlatform) -> None:
    async def scenario() -> None:
        session = keyboard_session(platform)
        callback = MagicMock()
        show_dialog(
            session.page,
            ft.AlertDialog(modal=True, actions=[ft.TextButton("Keep editing", on_click=callback)]),
        )
        await press(session, "Enter")
        callback.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("multiline", [False, True])
@pytest.mark.parametrize("disabled_target", ["button", "dialog", "input", "container", "hidden"])
def test_enter_respects_disabled_and_hidden_actions_or_inputs(
    multiline: bool, disabled_target: str
) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        callback = MagicMock()
        field = ft.TextField(multiline=multiline, disabled=disabled_target == "input")
        container = ft.Column([field], disabled=disabled_target == "container")
        submit = ft.TextButton(
            "Save",
            disabled=disabled_target == "button",
            visible=disabled_target != "hidden",
            on_click=callback,
        )
        dialog = ft.AlertDialog(
            modal=True,
            disabled=disabled_target == "dialog",
            content=container,
            actions=[ft.TextButton("Cancel", on_click=MagicMock()), submit],
        )
        show_dialog(session.page, dialog)
        await session.dispatch_event(field._i, "focus", None)
        if multiline:
            await session.dispatch_event(field._i, "submit", None)
        else:
            await press(session, "Enter")
        callback.assert_not_called()
        assert dialog.open

    asyncio.run(scenario())


@pytest.mark.parametrize("multiline", [False, True])
def test_enter_submits_once_from_nested_inputs_and_keeps_native_controls(
    multiline: bool,
) -> None:
    async def scenario() -> None:
        session = keyboard_session()
        callback = MagicMock()
        original_focus, original_blur = AsyncMock(), MagicMock()
        field = ft.TextField(multiline=multiline, on_focus=original_focus, on_blur=original_blur)
        dialog = ft.AlertDialog(
            modal=True,
            content=ft.Container(ft.Column([ft.Row([field])])),
            actions=[
                ft.TextButton("Cancel", on_click=MagicMock()),
                ft.TextButton("Save", on_click=callback),
            ],
        )
        show_dialog(session.page, dialog)
        assert field.autofocus
        assert field.shift_enter is multiline
        await session.dispatch_event(field._i, "focus", None)
        original_focus.assert_awaited_once()
        await press(session, "Enter")
        if multiline:
            callback.assert_not_called()
            await session.dispatch_event(field._i, "submit", None)
        callback.assert_called_once()
        await session.dispatch_event(field._i, "blur", None)
        original_blur.assert_called_once()
        callback.reset_mock()
        await press(session, "Enter")
        callback.assert_not_called()
        for action in dialog.actions:
            assert isinstance(action, ft.KeyboardListener)
            await session.dispatch_event(action._i, "key_down", {"key": "Enter"})
        callback.assert_not_called()

    asyncio.run(scenario())


def test_enter_does_not_submit_a_parent_or_closed_dialog() -> None:
    async def scenario() -> None:
        session = keyboard_session()
        parent_callback = MagicMock()
        parent = ft.AlertDialog(
            modal=True,
            actions=[
                ft.TextButton("Cancel", on_click=MagicMock()),
                ft.TextButton("Save", on_click=parent_callback),
            ],
        )
        show_dialog(session.page, parent)
        child_callback = MagicMock(side_effect=lambda _: session.page.pop_dialog())
        child = ft.AlertDialog(
            modal=True, actions=[ft.TextButton("Close", on_click=child_callback)]
        )
        show_dialog(session.page, child)
        assert isinstance(parent.content, ft.KeyboardListener)
        await session.dispatch_event(parent.content._i, "key_down", {"key": "Enter"})
        parent_callback.assert_not_called()
        await press(session, "Enter")
        assert not child.open and parent.open
        child_callback.assert_called_once()
        await press(session, "Enter")
        child_callback.assert_called_once()
        parent_callback.assert_not_called()
        await session.dispatch_event(child._i, "dismiss", None)
        await press(session, "Enter")
        parent_callback.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("focus", ["amount", "description"])
def test_transaction_enter_saves_through_the_existing_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editing: bool, focus: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        if editing:
            await journal.add_expense(
                "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
            )
        view._transaction_dialog(journal, journal.ledger.transactions[-1] if editing else None)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.account.value, form.category.value = "Assets:Bank", "Expenses:Food"
        form.description.value = "Submitted description"
        form.amount.value = "17"
        form.when.occurred.value = date_input(date(2026, 10, 18), view.language)
        form.when.clock.value = "12:30"
        field = form.amount if focus == "amount" else form.description
        await session.dispatch_event(field._i, "focus", None)
        if focus == "description":
            await session.dispatch_event(field._i, "submit", None)
        else:
            await press(session, "Enter")
        assert not parent.open
        assert len(journal.ledger.transactions) == 1
        assert journal.ledger.transactions[0].narration == "Submitted description"
        assert journal.ledger.transactions[0].postings[0].amount == Decimal("17")

    asyncio.run(scenario())


def test_enter_and_escape_cannot_repeat_an_inflight_keyboard_submit() -> None:
    async def scenario() -> None:
        session = keyboard_session()
        started, release = asyncio.Event(), asyncio.Event()
        cancel_callback = MagicMock()
        calls = 0

        async def submit(event: ft.Event[ft.TextButton]) -> None:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            session.page.pop_dialog()

        show_dialog(
            session.page,
            ft.AlertDialog(
                modal=True,
                actions=[
                    ft.TextButton("Cancel", on_click=cancel_callback),
                    ft.TextButton("Save", on_click=submit),
                ],
            ),
        )
        saving = asyncio.create_task(press(session, "Enter"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            await press(session, "Enter")
            await press(session, "Escape")
            assert calls == 1
            cancel_callback.assert_not_called()
        finally:
            release.set()
            await saving

    asyncio.run(scenario())


def test_enter_rejects_a_stale_transaction_without_rebasing_the_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view._transaction_dialog(journal)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.account.value, form.category.value = "Assets:Bank", "Expenses:Food"
        form.description.value, form.amount.value = "Retained draft", "17"
        external = journal.source + "; external synthetic edit\n"
        (tmp_path / FILENAME).write_text(external)
        await session.dispatch_event(form.description._i, "submit", None)
        assert parent.open
        assert session.page._dialogs.controls[-1] is not parent
        assert form.description.value == "Retained draft" and form.amount.value == "17"
        assert (tmp_path / FILENAME).read_text() == external

    asyncio.run(scenario())


def test_report_month_enter_keeps_invalid_year_and_selects_valid_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        refresh = AsyncMock()
        monkeypatch.setattr(view.budgets, "_refresh_filters", refresh)
        view.budgets._pick_month(MagicMock())
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        assert isinstance(dialog.content, ft.KeyboardListener)
        assert isinstance(dialog.content.content, ft.Column)
        month, year, error = dialog.content.content.controls
        assert isinstance(month, ft.Dropdown)
        assert isinstance(year, ft.TextField)
        assert isinstance(error, ft.Text)
        original = (tmp_path / FILENAME).read_bytes()
        year.value = "invalid"
        await session.dispatch_event(year._i, "focus", None)
        await press(session, "Enter")
        assert dialog.open and error.visible and year.value == "invalid"
        refresh.assert_not_called()
        year.value, month.value = "2027", "2"
        await press(session, "Enter")
        assert not dialog.open
        assert view.budgets.month == date(2027, 2, 1)
        refresh.assert_awaited_once()
        assert (tmp_path / FILENAME).read_bytes() == original

    asyncio.run(scenario())
