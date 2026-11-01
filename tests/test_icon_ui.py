import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click_async, transaction_form
from test_daily_summary import walk
from test_dialog_keyboard import finance_session, press
from test_icons import asset, raster, store_at

from app.domain.budgets import Budget
from app.domain.reporting import period_report
from app.icons import IconAsset, IconError, normalize_image
from app.infrastructure.icon_sources import IconifySearch, bundled_icons
from app.journal import FILENAME
from app.ui.dialogs import show_dialog
from app.ui.icons import IconPicker, icon_image
from app.ui.tree_picker import TreePicker


def images(control: ft.Control) -> list[ft.Image]:
    return [child for child in walk(control) if isinstance(child, ft.Image)]


@pytest.mark.parametrize("key", ["Escape", "Enter"])
def test_picker_keys_cancel_or_save_and_leave_parent_draft_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        view._transaction_dialog(journal)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.payee.value = "Synthetic unsaved draft"
        original = (tmp_path / FILENAME).read_bytes()
        view._open_icon_picker(journal, "Assets:Bank")
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._choose(MagicMock(control=ft.TextButton(data=bundled_icons("bank")[0])))
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, key)
        assert not picker.open and parent.open
        assert form.payee.value == "Synthetic unsaved draft"
        assert (tmp_path / FILENAME).read_bytes() == original
        assert bool(view.icons.assets) == (key == "Enter")
        if key == "Enter":
            assert images(view.home_balances)

    asyncio.run(scenario())


def test_failed_save_retains_selection_and_does_not_close_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock(side_effect=IconError("icon_storage"))
        picker = IconPicker(session.page, "en", asset(), IconifySearch(), persist)
        show_dialog(session.page, picker)
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert picker.open and picker.selected == asset()
        assert "permissions" in (picker.status.value or "")
        assert not picker.submit.disabled and not picker.cancel.disabled

    asyncio.run(scenario())


def test_import_resizes_previews_and_requires_explicit_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock()
        data = raster(512, 512)
        monkeypatch.setattr(
            ft.FilePicker,
            "pick_files",
            AsyncMock(
                return_value=[
                    ft.FilePickerFile(id=1, name="synthetic.png", size=len(data), bytes=data)
                ]
            ),
        )
        picker = IconPicker(session.page, "en", None, IconifySearch(), persist)
        show_dialog(session.page, picker)
        await click_async(picker.import_button)
        assert picker.selected is not None
        assert not picker.selected.monochrome
        assert "permission" in (picker.status.value or "")
        assert len(images(picker.results)) == 1
        image = images(picker.results)[0]
        assert image.width == image.height == 16
        assert all(image.width == image.height == 16 for image in images(picker.preview))
        persist.assert_not_awaited()
        await press(session)
        persist.assert_not_awaited()

    asyncio.run(scenario())


def test_invalid_import_never_appears_as_candidate_or_replaces_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        data = raster(16, 16)
        monkeypatch.setattr(
            ft.FilePicker,
            "pick_files",
            AsyncMock(
                return_value=[ft.FilePickerFile(id=1, name="small.png", size=len(data), bytes=data)]
            ),
        )
        picker = IconPicker(session.page, "en", asset(), IconifySearch(), AsyncMock())
        show_dialog(session.page, picker)
        original = list(picker.results.controls)
        await click_async(picker.import_button)
        assert picker.selected == asset()
        assert picker.results.controls == original
        assert "48" in (picker.status.value or "")

    asyncio.run(scenario())


def test_stale_assignment_is_not_overwritten_and_selection_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        view._open_icon_picker(journal, "Assets:Bank")
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker.selected = asset("Draft")
        external = store_at(tmp_path)
        await external.assign("Assets:Bank", asset("External"), ())
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert picker.open and picker.selected.label == "Draft"
        assert "reopen" in (picker.status.value or "")
        assert view.icons.assets["Assets:Bank"].label == "External"

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_entity_icons_reach_lists_settings_transactions_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.language = language
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset("Bank"), ())
        await view.icons.assign("Expenses:Food", asset("Food"), ())
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        view._refresh()
        assert images(view.home_balances)
        assert images(view.account_list)
        assert images(view.category_list)
        assert len(images(view.history)) >= 2
        for context in (view.home_balances, view.account_list, view.category_list, view.history):
            assert all(image.width == image.height == 16 for image in images(context))
        view.default_account.value = "Assets:Bank"
        view._render_default_account()
        assert isinstance(view.default_account.leading_icon, ft.Image)
        assert any(
            isinstance(option.leading_icon, ft.Image) for option in view.default_account.options
        )
        report = period_report(
            journal.ledger,
            journal.ledger.transactions[-1].date,
            journal.ledger.transactions[-1].date,
        )
        section = view._report_category_section(
            "report_expense_categories",
            report.expense_categories,
            (),
            "UAH",
            report.currencies[0].expenses,
        )
        assert images(section)
        view._transaction_dialog(journal)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        form = transaction_form(dialog)
        assert any(isinstance(option.leading_icon, ft.Image) for option in form.account.options)
        assert any(isinstance(option.leading_icon, ft.Image) for option in form.category.options)
        form.account.value = "Assets:Bank"
        await form.account._trigger_event("select", None)
        assert isinstance(form.account.leading_icon, ft.Image)

    asyncio.run(scenario())


def test_budget_definition_tree_and_filters_use_entity_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Expenses:Food", asset(), ())
        budget = Budget(
            "synthetic",
            "Expenses:Food",
            journal.today().replace(day=1),
            "monthly",
            Decimal(100),
            "UAH",
        )
        assert images(view.budgets._definition(budget))
        view.budgets.category_id = "Expenses:Food"
        view.budgets.category_choices = {"Expenses:Food": "Food"}
        view.budgets._sync_filters()
        assert isinstance(view.budgets.scope.icon, ft.Image)
        assert isinstance(view.budgets.active_filters.controls[0], ft.Chip)
        assert isinstance(view.budgets.active_filters.controls[0].leading, ft.Image)
        picker = TreePicker(
            session.page,
            "en",
            "Synthetic",
            {"Expenses:Food": "Food"},
            "",
            "All",
            AsyncMock(),
            icon=view._entity_icon,
        )
        assert isinstance(picker.listing.controls[0], ft.Container)
        button = picker.listing.controls[0].content
        assert isinstance(button, ft.TextButton) and isinstance(button.icon, ft.Image)

    asyncio.run(scenario())


@pytest.mark.parametrize("vector", [True, False])
def test_icons_render_at_16_pixels_without_changing_stored_artwork(vector: bool) -> None:
    original = (
        asset()
        if vector
        else IconAsset(
            "Imported",
            normalize_image(raster())[0],
            False,
            "User-provided",
            "Unverified",
            "Synthetic",
        )
    )
    serialized = original.serialize()
    image = icon_image(original)
    assert image.width == image.height == 16
    assert image.src == original.data
    assert image.fit == ft.BoxFit.CONTAIN
    assert image.color == (ft.Colors.ON_SURFACE if vector else None)
    assert original.serialize() == serialized


def test_icon_read_failure_does_not_disable_financial_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset(), ())
        monkeypatch.setattr(
            view.icons.files, "read_records", AsyncMock(side_effect=IconError("icon_storage"))
        )
        await view._load_icons()
        assert journal.ready
        assert view.icon_notice.visible
        assert view.icons.assets["Assets:Bank"] == asset()

    asyncio.run(scenario())


def test_busy_save_disables_escape_and_duplicate_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        async def persist(_asset: object) -> None:
            started.set()
            await release.wait()

        save = AsyncMock(side_effect=persist)
        picker = IconPicker(session.page, "en", asset(), IconifySearch(), save)
        show_dialog(session.page, picker)
        task = asyncio.create_task(click_async(picker.submit))
        await started.wait()
        assert picker.cancel.disabled and picker.submit.disabled
        await press(session)
        assert picker.open
        await click_async(picker.submit)
        save.assert_awaited_once()
        release.set()
        await task
        assert not picker.open

    asyncio.run(scenario())
