import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.device_info import DeviceInfo
from form_controls import change, click_async, transaction_form

from app.domain.ledger import BANK_FEES
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import (
    DesktopPreferences,
    FeePreferences,
    PreferencesError,
    fee_category_in_branches,
)
from app.journal import FILENAME, Journal
from app.ui.finance import DEFAULT_FEE_KEY, FEE_CATEGORIES_KEY, FinanceView

BRANCH = "Expenses:Fees"
CHILD = "Expenses:Fees:Bank"
LEAF = "Expenses:Fees:Bank:Monthly"
SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Assets:Other USD
2000-01-01 open Expenses:Fees:Bank:Monthly
    name: "Monthly"
2000-01-01 open Expenses:Food
2000-01-01 open Expenses:Fees-Other
2000-01-01 open Expenses:Fees:Bank
    name: "Bank"
2000-01-01 open Expenses:Fees:Card
    name: "Card"
2000-01-01 open Expenses:Fees
    name: "Fees"
2000-01-01 open Income:Salary
"""


async def setup(
    path: Path, monkeypatch: pytest.MonkeyPatch, *, restricted: bool = True
) -> tuple[FinanceView, MagicMock, Journal]:
    (path / FILENAME).write_text(SOURCE)
    journal = Journal(DesktopFolder(str(path), FILENAME))
    await journal.load()
    page = MagicMock(spec=ft.Page)
    page.width = 1366
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.journal = journal
    view.preferences = DesktopPreferences(path / "preferences.json")
    if restricted:
        view.fee_preferences = FeePreferences((BRANCH,), CHILD)
        await view._apply_default_fee()
    view._refresh()
    return view, page, journal


def checkbox(view: FinanceView, identifier: str) -> ft.Checkbox:
    for container in view.fee_category_choices.controls:
        assert isinstance(container, ft.Container)
        control = container.content
        assert isinstance(control, ft.Checkbox)
        if control.data == identifier:
            return control
    raise AssertionError(identifier)


def select(view: FinanceView, identifier: str, enabled: bool) -> None:
    control = checkbox(view, identifier)
    assert not control.disabled
    control.value = enabled
    view._fee_branch_changed(MagicMock(control=control))


def test_fee_preferences_roundtrip_and_component_aware_descendants() -> None:
    setting = FeePreferences((BRANCH, BANK_FEES), CHILD)
    assert FeePreferences.parse(setting.serialize()) == setting
    assert fee_category_in_branches(BRANCH, setting.branches)
    assert fee_category_in_branches(LEAF, setting.branches)
    assert not fee_category_in_branches("Expenses:Fees-Other", setting.branches)
    assert not fee_category_in_branches("Income:Fees", setting.branches)


@pytest.mark.parametrize(
    "value",
    [
        "broken",
        "null",
        "[]",
        "{}",
        '{"branches": [], "default": "Expenses:Bank-Fees"}',
        '{"branches": ["Expenses:Fees"], "default": "Expenses:Food"}',
        '{"branches": ["Expenses"], "default": "Income:Salary"}',
        '{"branches": ["Expenses"], "default": "Expenses"}',
        '{"branches": [3], "default": "Expenses:Fees"}',
        '{"branches": ["Expenses:bad"], "default": "Expenses:bad"}',
        '{"branches": "Expenses", "default": "Expenses:Fees"}',
        '{"branches": ["Expenses"], "default": false}',
        '{"branches": ["Expenses"], "default": "Expenses:Fees", "extra": 1}',
    ],
)
def test_invalid_fee_preferences_are_explicit_errors(value: str) -> None:
    with pytest.raises(PreferencesError, match="fee_categories_invalid"):
        FeePreferences.parse(value)


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("width", [390, 1366])
@pytest.mark.parametrize("kind", ["expense", "income", "transfer"])
def test_every_fee_selector_shows_hierarchy_and_only_configured_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, width: int, kind: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.language, page.width = language, width
        view._refresh()
        expected = [BRANCH, CHILD, LEAF, BRANCH + ":Card"]
        assert [o.key for o in view.default_fee.options] == expected
        choices, options = view.fee_category_choices.controls, view.default_fee.options
        view._refresh()
        assert view.fee_category_choices.controls is choices
        assert view.default_fee.options is options
        assert [o.text for o in view.default_fee.options] == [
            "Fees",
            "  Bank",
            "    Monthly",
            "  Card",
        ]
        assert checkbox(view, BRANCH).value
        assert checkbox(view, LEAF).value and checkbox(view, LEAF).disabled
        assert not checkbox(view, "Expenses:Fees-Other").value
        view._new_transaction(MagicMock())
        form = transaction_form(page.show_dialog.call_args.args[0])
        form.sign.selected = [{"expense": "-", "income": "+", "transfer": "transfer"}[kind]]
        change(form.sign)
        form.fee_enabled.value = True
        change(form.fee_enabled)
        assert [(o.key, o.text) for o in form.fee_category.options] == [
            (o.key, o.text) for o in view.default_fee.options
        ]
        assert form.fee_category.value == CHILD and not form.fee_category.disabled
        assert journal.source == SOURCE

    asyncio.run(scenario())


def test_branch_settings_save_atomically_and_future_descendants_are_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, _, journal = await setup(tmp_path, monkeypatch, restricted=False)
        assert checkbox(view, "Expenses").value
        assert checkbox(view, BRANCH).disabled
        assert [o.key for o in view.default_fee.options] == [
            BANK_FEES,
            BRANCH,
            CHILD,
            LEAF,
            BRANCH + ":Card",
            "Expenses:Fees-Other",
            "Expenses:Food",
        ]
        assert [
            container.content.data
            for container in view.fee_category_choices.controls
            if isinstance(container, ft.Container) and isinstance(container.content, ft.Checkbox)
        ] == ["Expenses", *(o.key for o in view.default_fee.options)]
        select(view, "Expenses", False)
        select(view, BRANCH, True)
        select(view, BANK_FEES, True)
        assert checkbox(view, CHILD).value and checkbox(view, CHILD).disabled
        view.default_fee.value = CHILD
        await view._save_default_fee(MagicMock())
        assert view.error_message is None
        assert journal.source == SOURCE and not (tmp_path / "backups").exists()
        settings = FeePreferences((BANK_FEES, BRANCH), CHILD)
        assert view.fee_preferences == settings
        assert view.preferences is not None
        assert await view.preferences.get(FEE_CATEGORIES_KEY) == settings.serialize()
        assert await view.preferences.get(DEFAULT_FEE_KEY) is None
        await journal.create_category("Fees:New", "New")
        view._refresh()
        assert BRANCH + ":New" in {o.key for o in view.default_fee.options}
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}
        assert "textmoney-" not in journal.source

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["false", "exception", "missing", "stale", "invalid"])
def test_failed_settings_keep_saved_policy_and_branch_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        view, _, journal = await setup(tmp_path, monkeypatch, restricted=False)
        before = view.fee_preferences
        select(view, "Expenses", False)
        select(view, BRANCH, True)
        view.default_fee.value = "Expenses:Food" if failure == "invalid" else CHILD
        preferences = MagicMock()
        preferences.set = AsyncMock(
            return_value=failure != "false",
            side_effect=OSError("Synthetic failure") if failure == "exception" else None,
        )
        view.preferences = None if failure == "missing" else preferences
        if failure == "stale":
            journal.source += "\n; external\n"
        source = journal.source
        await view._save_default_fee(MagicMock())
        assert view.error_message and journal.ready
        assert view.fee_preferences == before
        assert view.fee_branches_draft == (BRANCH,)
        assert journal.ledger.default_fee_category == before.default
        assert journal.source == source
        if failure in {"stale", "invalid"}:
            preferences.set.assert_not_awaited()

    asyncio.run(scenario())


def test_unsaved_branches_survive_poll_resize_and_language_changes_without_rebasing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch, restricted=False)
        select(view, "Expenses", False)
        select(view, BRANCH, True)
        view.default_fee.value = CHILD
        original_source = view.default_fee_source
        for width, language in [(390, "uk"), (1366, "ru"), (800, "en")]:
            page.width, view.language = width, language
            view._refresh()
            assert view.fee_branches_draft == (BRANCH,)
            assert view.default_fee.value == CHILD
            assert view.default_fee_source == original_source
        (tmp_path / FILENAME).write_text(SOURCE + "\n; external\n")
        await view._poll_journal()
        assert journal.source != original_source
        assert view.fee_branches_draft == (BRANCH,) and view.default_fee.value == CHILD
        assert view.default_fee_source == original_source
        await view._save_default_fee(MagicMock())
        assert view.error_message == "crud_stale"
        assert view.fee_preferences == FeePreferences()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income", "transfer"])
@pytest.mark.parametrize("inactive", [False, True])
def test_saved_excluded_fee_is_still_editable_and_retains_hierarchy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, inactive: bool
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch, restricted=False)
        values = ("2026-01-10", "2026-01-10T12:00:00")
        if kind == "transfer":
            await journal.save_transfer(
                "Assets:Cash",
                "Assets:Other",
                "100",
                "5",
                LEAF,
                "added",
                *values,
                "Synthetic",
                "",
                journal.source,
            )
        elif kind == "expense":
            await journal.add_expense(
                "Assets:Cash",
                "Expenses:Food",
                "100",
                "",
                values[0],
                "Synthetic",
                values[1],
                fee="5",
                fee_category=LEAF,
            )
        else:
            await journal.add_incoming(
                "Assets:Cash",
                "Income:Salary",
                "100",
                "",
                values[0],
                "income",
                "Synthetic",
                values[1],
                fee="5",
                fee_category=LEAF,
            )
        if inactive:
            await journal.edit_category(LEAF, "Monthly", False, journal.source)
        view.fee_preferences = FeePreferences((BANK_FEES,), BANK_FEES)
        await view._apply_default_fee()
        view._refresh()
        saved = journal.ledger.transactions[-1]
        view._edit_transaction(MagicMock(control=ft.IconButton(data=saved.id)))
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.fee_category.value == LEAF
        assert (LEAF, "    Monthly") in [(o.key, o.text) for o in form.fee_category.options]
        form.description.value = "Edited synthetic"
        if inactive:
            form.fee_enabled.value = False
            change(form.fee_enabled)
        await click_async(dialog.actions[-1])
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        assert (
            (len(saved.postings) == 2)
            if inactive
            else (any(p.account == LEAF and p.amount == 5 for p in saved.postings))
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["legacy", "combined", "invalid"])
def test_startup_migrates_or_restores_fee_policy_without_journal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
        journal_path = tmp_path / "journal"
        journal_path.mkdir()
        (journal_path / FILENAME).write_text(SOURCE)
        preferences = DesktopPreferences()
        await preferences.set("textmoney.folder.v1", str(journal_path))
        await preferences.set(DEFAULT_FEE_KEY, "Expenses:Food")
        configured = FeePreferences((BRANCH,), CHILD)
        if mode != "legacy":
            await preferences.set(
                FEE_CATEGORIES_KEY, configured.serialize() if mode == "combined" else "{}"
            )
        page = MagicMock(spec=ft.Page)
        page.platform, page.width = ft.PagePlatform.LINUX, 390
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(locales=[ft.Locale(language_code="en")])
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        if mode == "invalid":
            assert view.error_message == "fee_categories_invalid" and view.journal is None
        else:
            assert view.journal and view.journal.ready
            assert view.fee_preferences == (
                configured if mode == "combined" else FeePreferences(default="Expenses:Food")
            )
            assert view.journal.source == SOURCE
            if mode == "combined":
                assert "Expenses:Food" not in {o.key for o in view.default_fee.options}
            persisted = await preferences.get(FEE_CATEGORIES_KEY)
            assert (
                (json.loads(persisted or "{}") == json.loads(configured.serialize()))
                if (mode == "combined")
                else persisted is None
            )
        assert (journal_path / FILENAME).read_text() == SOURCE

    asyncio.run(scenario())
