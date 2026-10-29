import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.device_info import DeviceInfo
from form_controls import change, click_async, transaction_form

from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import (
    DesktopPreferences,
    PreferencesError,
    parse_default_account,
)
from app.journal import FILENAME, Journal
from app.localization import date_input
from app.ui.finance import DEFAULT_ACCOUNT_KEY, FOLDER_KEY, FinanceView

SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
    name: "Cash"
2000-01-01 open Assets:Other EUR
    name: "Other"
2000-01-01 open Liabilities:Card USD
    name: "Credit card"
2000-01-01 open Assets:Hidden USD
    textmoney-active: FALSE
2000-01-01 open Expenses:Food
2000-01-01 open Income:Salary
2000-01-02 * "Synthetic"
    Expenses:Food 5 USD
    Assets:Cash -5 USD
"""


async def setup(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FinanceView, MagicMock, Journal]:
    (path / FILENAME).write_text(SOURCE, newline="")
    journal = Journal(DesktopFolder(str(path), FILENAME))
    await journal.load()
    page = MagicMock(spec=ft.Page)
    page.width = 1366
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.journal = journal
    view.preferences = DesktopPreferences(path / "preferences.json")
    view._refresh()
    return view, page, journal


@pytest.mark.parametrize("value", [None, "", "Assets:Cash", "Liabilities:Card", "Assets:Bank:USD"])
def test_default_account_preference_validation(value: str | None) -> None:
    assert parse_default_account(value) == (value or None)


@pytest.mark.parametrize(
    "value",
    [
        False,
        1,
        [],
        {},
        "Assets",
        "Cash",
        "Expenses:Food",
        "Income:Salary",
        "Assets:bad",
        "Assets:Cash ",
    ],
)
def test_invalid_preferences_are_not_silently_ignored(value: object) -> None:
    with pytest.raises(PreferencesError, match="default_account_invalid"):
        parse_default_account(value)


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("width", [390, 1366])
def test_setting_saves_and_clears_without_writing_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, width: int
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.language, page.width = language, width
        view._refresh()
        assert view.default_account.value == ""
        assert [o.key for o in view.default_account.options] == [
            "",
            "Assets:Cash",
            "Assets:Other",
            "Liabilities:Card",
        ]
        assert view.default_account.options[0].text == view.tr("default_account_none")
        assert view.default_account.expand is True
        view._new_transaction(MagicMock())
        assert transaction_form(page.show_dialog.call_args.args[0]).account.value is None
        view.default_account.value = "Liabilities:Card"
        await view._save_default_account(MagicMock())
        assert view.error_message is None and view.default_account_id == "Liabilities:Card"
        assert view.preferences is not None
        assert await view.preferences.get(DEFAULT_ACCOUNT_KEY) == "Liabilities:Card"
        view._new_transaction(MagicMock())
        assert (
            transaction_form(page.show_dialog.call_args.args[0]).account.value == "Liabilities:Card"
        )
        view.default_account.value = ""
        await view._save_default_account(MagicMock())
        assert view.default_account_id is None and not view.error_message
        assert await view.preferences.get(DEFAULT_ACCOUNT_KEY) == ""
        view._new_transaction(MagicMock())
        assert transaction_form(page.show_dialog.call_args.args[0]).account.value is None
        assert journal.source == SOURCE and (tmp_path / FILENAME).read_bytes() == SOURCE.encode()
        assert not (tmp_path / "backups").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["Assets:Cash", "Liabilities:Card"])
@pytest.mark.parametrize("kind", ["expense", "income", "transfer", "reimbursement", "adjustment"])
def test_new_forms_preselect_only_primary_account_and_keep_manual_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identifier: str, kind: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.default_account.value = identifier
        await view._save_default_account(MagicMock())
        view._new_transaction(MagicMock())
        form = transaction_form(page.show_dialog.call_args.args[0])
        if kind == "transfer":
            form.sign.selected = ["transfer"]
            change(form.sign)
        else:
            form.choose(kind)
        assert form.account.value == identifier
        assert form.second_account.value is None
        assert identifier not in {o.key for o in form.second_account.options}
        form.account.value = "Assets:Other"
        change(form.account)
        form.amount.value = "17."
        for operation in ("expense", "income"):
            form.choose(operation)
            assert form.account.value == "Assets:Other" and form.amount.value == "17."
        view._refresh()
        assert form.account.value == "Assets:Other"
        assert journal.source == SOURCE

    asyncio.run(scenario())


@pytest.mark.parametrize("inactive", [False, True])
def test_default_does_not_replace_account_in_existing_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inactive: bool
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.default_account.value = "Liabilities:Card"
        await view._save_default_account(MagicMock())
        if inactive:
            await journal.edit_account("Assets:Cash", "Cash", None, False, journal.source)
        view._transaction_dialog(journal, journal.ledger.transactions[0])
        form = transaction_form(page.show_dialog.call_args.args[0])
        assert form.account.value == "Assets:Cash"
        assert "Assets:Cash" in {o.key for o in form.account.options}
        assert form.amount.value == "5"

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["Assets:Missing", "Assets:Hidden"])
def test_unavailable_default_has_notice_and_no_fallback_to_another_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identifier: str
) -> None:
    async def scenario() -> None:
        view, page, _ = await setup(tmp_path, monkeypatch)
        view.default_account_id = identifier
        view.default_account_journal = None
        view._refresh()
        assert view.default_account_notice.visible
        assert view.default_account_notice.value == view.tr("default_account_unavailable")
        assert view.default_account.value == identifier
        assert view.default_account.options[-1].key == identifier
        view._new_transaction(MagicMock())
        assert transaction_form(page.show_dialog.call_args.args[0]).account.value is None
        view.default_account.value = ""
        await view._save_default_account(MagicMock())
        assert not view.default_account_notice.visible and view.default_account_id is None

    asyncio.run(scenario())


def test_deactivation_reactivation_and_folder_change_reconcile_saved_setting_without_rewriting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.default_account.value = "Assets:Cash"
        await view._save_default_account(MagicMock())
        for active in (False, True):
            await journal.edit_account("Assets:Cash", "Renamed cash", None, active, journal.source)
            view._refresh()
            assert view.default_account_notice.visible is not active
            view._new_transaction(MagicMock())
            form = transaction_form(page.show_dialog.call_args.args[0])
            assert form.account.value == ("Assets:Cash" if active else None)
            if active:
                assert view.default_account.options[1].text == "Renamed cash (USD)"
        view.default_account.value = "Assets:Other"
        other = tmp_path / "other"
        other.mkdir()
        view.journal = Journal(DesktopFolder(str(other), FILENAME))
        await view.journal.load()
        view._refresh()
        assert view.default_account.value == "Assets:Cash"
        assert view.default_account_notice.visible
        view._new_transaction(MagicMock())
        assert transaction_form(page.show_dialog.call_args.args[0]).account.value is None
        assert view.preferences is not None
        assert await view.preferences.get(DEFAULT_ACCOUNT_KEY) == "Assets:Cash"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["false", "exception", "missing", "invalid", "inactive", "folder"]
)
def test_failed_saves_keep_previous_default_and_unsaved_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.default_account.value = "Assets:Cash"
        await view._save_default_account(MagicMock())
        draft = (
            "Expenses:Food"
            if failure == "invalid"
            else ("Assets:Hidden" if failure == "inactive" else "Liabilities:Card")
        )
        view.default_account.value = draft
        preferences = MagicMock(
            set=AsyncMock(
                return_value=failure != "false",
                side_effect=OSError("Synthetic failure") if failure == "exception" else None,
            )
        )
        view.preferences = None if failure == "missing" else preferences
        if failure == "folder":
            view.default_account_journal = None
        await view._save_default_account(MagicMock())
        assert view.error_message
        assert view.default_account_id == "Assets:Cash"
        assert view.default_account.value == ("Assets:Cash" if failure == "folder" else draft)
        view._new_transaction(MagicMock())
        assert transaction_form(page.show_dialog.call_args.args[0]).account.value == "Assets:Cash"
        if failure in {"folder", "invalid", "inactive"}:
            preferences.set.assert_not_awaited()
        assert journal.source == SOURCE and not (tmp_path / "backups").exists()

    asyncio.run(scenario())


def test_unsaved_default_survives_refresh_poll_resize_and_language_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        view, _, journal = await setup(tmp_path, monkeypatch)
        view.default_account.value = "Assets:Other"
        for width, language in [(390, "uk"), (1366, "ru"), (800, "en")]:
            view.language = language
            view._apply_layout(width)
            await view._poll_journal()
            assert view.default_account.value == "Assets:Other"
            assert view.default_account_id is None
        assert journal.source == SOURCE

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", [ft.PagePlatform.LINUX, ft.PagePlatform.ANDROID])
@pytest.mark.parametrize(
    "saved", [None, "", "Assets:Cash", "Liabilities:Card", "Assets:Missing", "Assets:bad"]
)
def test_startup_restores_optional_setting_on_desktop_and_shared_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: ft.PagePlatform, saved: str | None
) -> None:
    async def scenario() -> None:
        _, page, journal = await setup(tmp_path, monkeypatch)
        store = DesktopPreferences(tmp_path / "restart.json")
        if saved is not None:
            await store.set(DEFAULT_ACCOUNT_KEY, saved)
        await store.set(FOLDER_KEY, "synthetic")
        monkeypatch.setattr("app.ui.finance.DesktopPreferences", MagicMock(return_value=store))
        monkeypatch.setattr(ft, "SharedPreferences", MagicMock(return_value=store))
        page.platform = platform
        page.get_device_info = AsyncMock(return_value=DeviceInfo(locales=[ft.Locale("en")]))
        view = FinanceView()
        view._sync_system_zone = AsyncMock()

        async def attach(location: str) -> None:
            assert location == "synthetic"
            view.journal = journal

        view._attach = attach
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        if saved == "Assets:bad":
            assert view.startup_complete and view.error_message == "default_account_invalid"
            assert view.default_account_id is None and view.journal is None
            return
        assert view.startup_complete and view.error_message is None
        assert view.default_account_id == (saved or None)
        view.system_zone = "UTC"
        view._new_transaction(MagicMock())
        expected = saved if saved in {"Assets:Cash", "Liabilities:Card"} else None
        assert transaction_form(page.show_dialog.call_args.args[0]).account.value == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income", "transfer"])
def test_saving_new_transaction_uses_default_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        view, page, journal = await setup(tmp_path, monkeypatch)
        view.default_account.value = "Liabilities:Card"
        await view._save_default_account(MagicMock())
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        if kind == "transfer":
            form.sign.selected = ["transfer"]
            change(form.sign)
            form.second_account.value = "Assets:Cash"
            change(form.second_account)
        else:
            form.choose(kind)
            form.category.value = "Expenses:Food" if kind == "expense" else "Income:Salary"
        form.amount.value = "7"
        form.description.value = "Synthetic default account"
        form.when.occurred.value = date_input(journal.ledger.transactions[0].date, view.language)
        form.when.clock.value = "12:00"
        action = dialog.actions[-1]
        assert isinstance(action, ft.TextButton)
        await click_async(action)
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        assert saved.kind == kind
        assert any(p.account == "Liabilities:Card" for p in saved.postings)
        assert view.default_account_id == "Liabilities:Card"
        assert len(list((tmp_path / "backups").glob("*-committed.beancount"))) == 1

    asyncio.run(scenario())


def test_settings_are_disabled_without_a_ready_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    assert view.default_account.disabled and view.default_account_save.disabled
