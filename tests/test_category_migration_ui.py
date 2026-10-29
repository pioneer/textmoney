import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import change, click, click_async

from app.domain.category_migration import CategoryMigration
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import DesktopPreferences, FeePreferences
from app.journal import FILENAME, Journal
from app.storage import StorageError
from app.ui.finance import FEE_CATEGORIES_KEY, FinanceView

SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Expenses:Old
    name: "Original"
2000-01-01 open Expenses:Old:Child
    name: "Child"
2000-01-01 open Expenses:Other
    name: "Other"
2000-01-02 * "Synthetic"
    Expenses:Old:Child 5 USD
    Assets:Cash -5 USD
2000-01-02 custom "budget" Expenses:Old "monthly" 10 USD
"""


async def setup(
    path: Path, monkeypatch: pytest.MonkeyPatch, language: str = "en"
) -> tuple[FinanceView, MagicMock, Journal]:
    (path / FILENAME).write_text(SOURCE, newline="")
    journal = Journal(DesktopFolder(str(path), FILENAME))
    await journal.load()
    page = MagicMock(spec=ft.Page)
    page.width = 1366
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.journal, view.language = journal, language
    view.preferences = DesktopPreferences(path / "preferences.json")
    view._refresh()
    view._edit_entity(MagicMock(control=ft.IconButton(data=("category", "Expenses:Old"))))
    return view, page, journal


def controls(dialog: ft.AlertDialog) -> list[ft.Control]:
    assert isinstance(dialog.content, ft.Column)
    children = dialog.content.controls
    return children + [
        child for row in children if isinstance(row, ft.Row) for child in row.controls
    ]


def submit(dialog: ft.AlertDialog) -> ft.TextButton:
    button = dialog.actions[-1]
    assert isinstance(button, ft.TextButton)
    return button


def open_migration(page: MagicMock) -> ft.AlertDialog:
    edit = page.show_dialog.call_args.args[0]
    advanced = next(c for c in controls(edit) if isinstance(c, ft.ExpansionTile))
    assert not advanced.expanded
    button = next(c for c in advanced.controls or [] if isinstance(c, ft.TextButton))
    click(button)
    return page.show_dialog.call_args.args[0]


async def review(page: MagicMock) -> ft.AlertDialog:
    form = open_migration(page)
    code = next(c for c in controls(form) if isinstance(c, ft.TextField))
    code.value = "New"
    await click_async(submit(form))
    return page.show_dialog.call_args.args[0]


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_guarded_migration_needs_explicit_review_and_acknowledgement_and_updates_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch, language)
        view.fee_preferences = FeePreferences(("Expenses:Old",), "Expenses:Old:Child")
        await view._apply_default_fee()
        view._refresh()
        view.budgets.category_id = "Expenses:Old"
        dialog = await review(page)
        texts = [c.value for c in controls(dialog) if isinstance(c, ft.Text)]
        assert view.tr("category_migration_warning") in texts
        assert any("Expenses:Old → Expenses:New" in text for text in texts)
        assert journal.source == SOURCE
        assert not (tmp_path / "backups").exists()
        assert dialog.actions[-1].disabled
        acknowledged = next(c for c in controls(dialog) if isinstance(c, ft.Checkbox))
        assert acknowledged.semantics_label == view.tr("category_migration_ack")
        assert view.tr("category_migration_ack") in texts
        acknowledged.value = True
        change(acknowledged)
        assert not dialog.actions[-1].disabled
        await click_async(submit(dialog))
        assert view.error_message is None
        assert {c.id for c in journal.ledger.categories} == {
            "Expenses:New",
            "Expenses:New:Child",
            "Expenses:Other",
        }
        assert journal.ledger.categories[0].name == "Original"
        assert page.pop_dialog.call_count == 3
        assert view.budgets.category_id == "Expenses:New"
        assert view.fee_preferences == FeePreferences(("Expenses:New",), "Expenses:New:Child")
        assert journal.ledger.default_fee_category == "Expenses:New:Child"
        assert view.preferences
        assert await view.preferences.get(FEE_CATEGORIES_KEY) == view.fee_preferences.serialize()
        backups = list((tmp_path / "backups").glob("*-committed.beancount"))
        assert len(backups) == 1 and backups[0].read_bytes() == SOURCE.encode()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unacknowledged", "source", "folder", "backup"])
def test_migration_errors_keep_dialog_and_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        edit = page.show_dialog.call_args.args[0]
        name = next(c for c in controls(edit) if isinstance(c, ft.TextField))
        name.value = "Unsaved display name"
        dialog = await review(page)
        target_form = page.show_dialog.call_args_list[-2].args[0]
        code = next(c for c in controls(target_form) if isinstance(c, ft.TextField))
        acknowledged = next(c for c in controls(dialog) if isinstance(c, ft.Checkbox))
        acknowledged.value = failure != "unacknowledged"
        change(acknowledged)
        if failure == "source":
            journal.source += "\n; new source\n"
        elif failure == "folder":
            view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
            await view.journal.load()
        elif failure == "backup":
            monkeypatch.setattr(
                journal.storage.folder, "backup", AsyncMock(side_effect=StorageError("Synthetic"))
            )
        await click_async(submit(dialog))
        assert view.error_message
        assert name.value == "Unsaved display name"
        assert code.value == "New"
        assert (tmp_path / FILENAME).read_bytes() == SOURCE.encode()
        page.pop_dialog.assert_not_called()
        assert not acknowledged.disabled and not dialog.actions[0].disabled

    asyncio.run(scenario())


def test_post_commit_preference_error_does_not_offer_to_repeat_completed_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.fee_preferences = FeePreferences(("Expenses:Old",), "Expenses:Old:Child")
        view.preferences = MagicMock(set=AsyncMock(return_value=False))
        dialog = await review(page)
        acknowledged = next(c for c in controls(dialog) if isinstance(c, ft.Checkbox))
        acknowledged.value = True
        change(acknowledged)
        await click_async(submit(dialog))
        assert view.error_message == "category_migration_preferences_failed"
        assert page.pop_dialog.call_count == 3
        assert journal.ready and "open Expenses:New" in journal.source
        assert view.fee_preferences.default == "Expenses:New:Child"
        assert "Do not retry" in view.tr("category_migration_preferences_failed")

    asyncio.run(scenario())


def test_android_migration_entry_and_direct_handler_are_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        bridge = MagicMock()
        journal.storage.folder = AndroidFolder(bridge, "content://synthetic", FILENAME)
        view._edit_entity(MagicMock(control=ft.IconButton(data=("category", "Expenses:Old"))))
        edit = page.show_dialog.call_args.args[0]
        advanced = next(c for c in controls(edit) if isinstance(c, ft.ExpansionTile))
        button = next(c for c in advanced.controls or [] if isinstance(c, ft.TextButton))
        assert button.disabled
        notice = next(c for c in advanced.controls or [] if isinstance(c, ft.Text) and c.visible)
        assert notice.value == view.tr("category_migration_intro")
        click(button)
        assert view.error_message == "category_migration_atomic_required"
        bridge.call.assert_not_called()

    asyncio.run(scenario())


def test_simple_display_name_edit_does_not_migrate_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        edit = page.show_dialog.call_args.args[0]
        name = next(c for c in controls(edit) if isinstance(c, ft.TextField))
        name.value = "New display name"
        await click_async(edit.actions[-1])
        assert view.error_message is None
        assert "open Expenses:Old" in journal.source and "Expenses:New" not in journal.source
        assert (
            next(c for c in journal.ledger.categories if c.id == "Expenses:Old").name
            == "New display name"
        )
        assert journal.ledger.transactions[0].postings[0].account == "Expenses:Old:Child"

    asyncio.run(scenario())


def test_moving_a_fee_subbranch_retains_eligibility_outside_old_ancestor() -> None:
    previous = FeePreferences(("Expenses:Old",), "Expenses:Old:Child:Fees")
    updated = previous.migrate("Expenses:Old:Child", "Expenses:Other:Child")
    assert updated == FeePreferences(
        ("Expenses:Old", "Expenses:Other:Child"), "Expenses:Other:Child:Fees"
    )


def test_migration_cannot_be_submitted_twice_while_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        dialog = await review(page)
        acknowledged = next(c for c in controls(dialog) if isinstance(c, ft.Checkbox))
        acknowledged.value = True
        change(acknowledged)
        started, release = asyncio.Event(), asyncio.Event()
        migrate = journal.migrate_category

        async def pending(plan: CategoryMigration) -> str | None:
            started.set()
            await release.wait()
            return await migrate(plan)

        monkeypatch.setattr(journal, "migrate_category", pending)
        saving = asyncio.create_task(click_async(submit(dialog)))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert acknowledged.disabled and dialog.actions[0].disabled
            await click_async(submit(dialog))
            assert journal.source == SOURCE and not saving.done()
        finally:
            release.set()
            await saving
        assert view.error_message is None and page.pop_dialog.call_count == 3
        assert len(list((tmp_path / "backups").glob("*-committed.beancount"))) == 1

    asyncio.run(scenario())
