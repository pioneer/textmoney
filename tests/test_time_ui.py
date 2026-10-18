import asyncio
import time as system_time
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from app.domain.ledger import Account, LedgerError, Posting, Transaction
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input, format_timestamp, parse_date_input, translate
from app.timekeeping import local_datetime, named_zone, wall_timestamp
from app.ui.datetime_fields import DateTimeFields
from app.ui.finance import DEFAULT_PAYEE_KEY, TIMEZONE_KEY, FinanceView


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_localized_manual_date_roundtrip(language: str) -> None:
    day = date(2026, 10, 18)
    assert parse_date_input(date_input(day, language), language) == day
    with pytest.raises(LedgerError, match="crud_date_invalid"):
        parse_date_input("2026-10-18", language)
    with pytest.raises(LedgerError, match="crud_date_invalid"):
        parse_date_input("31.02.2026" if language != "en" else "02/31/2026", language)


@pytest.mark.parametrize(
    ("day", "offset"), [(date(2026, 1, 15), "+02:00"), (date(2026, 7, 15), "+03:00")]
)
def test_named_timezone_uses_date_specific_dst_rules(day: date, offset: str) -> None:
    assert wall_timestamp(day, "12:34:56", "Europe/Kyiv") == f"{day}T12:34:56{offset}"


@pytest.mark.parametrize(
    ("day", "error"),
    [(date(2026, 3, 29), "crud_time_missing"), (date(2026, 10, 25), "crud_time_ambiguous")],
)
def test_dst_gaps_and_folds_are_not_silently_guessed(day: date, error: str) -> None:
    with pytest.raises(LedgerError, match=error):
        wall_timestamp(day, "03:30", "Europe/Kyiv")


@pytest.mark.parametrize("clock", ["", "24:00", "12:60", "1200", "12", "12:00+02:00"])
def test_invalid_time_is_actionable(clock: str) -> None:
    with pytest.raises(LedgerError, match="crud_time_invalid"):
        wall_timestamp(date(2026, 1, 1), clock, "UTC")


def test_unknown_zone_is_rejected_and_timestamp_display_converts_without_changing_date() -> None:
    with pytest.raises(LedgerError, match="crud_timezone_invalid"):
        named_zone("not/a-zone")
    original = "2026-10-18T23:30:00+00:00"
    assert format_timestamp(original, "en", "Europe/Kyiv") == "Oct 19, 2026 · 02:30:00 EEST"
    assert local_datetime(datetime.fromisoformat(original), "UTC").day == 18


def test_unchanged_edit_retains_exact_timestamp_offset_precision_and_dst_fold() -> None:
    stamp = "2026-10-25T03:30:00.123456+02:00"
    editor = DateTimeFields("uk", "Europe/Kyiv", date(2026, 10, 24), stamp)
    assert editor.values() == ("2026-10-24", stamp)
    editor.set_language("en")
    assert editor.occurred.value == "10/24/2026"
    assert editor.values()[1] == stamp
    editor.occurred.value = "10/23/2026"
    assert editor.values() == ("2026-10-23", stamp)
    editor.clock.value = "03:31"
    with pytest.raises(LedgerError, match="crud_time_ambiguous"):
        editor.values()


def test_explicit_zone_change_preserves_draft_instant_and_accounting_date() -> None:
    editor = DateTimeFields("en", "UTC", date(2026, 1, 1), "2026-01-02T23:30:00+00:00")
    editor.set_zone("Europe/Kyiv")
    assert editor.occurred.value == "01/01/2026"
    assert editor.stamp_date.value == "01/03/2026"
    assert editor.clock.value == "01:30:00"
    assert editor.values() == ("2026-01-01", "2026-01-02T23:30:00+00:00")


def test_partial_date_is_preserved_when_language_changes() -> None:
    editor = DateTimeFields("en", "UTC")
    editor.occurred.value = "10/"
    editor.set_language("uk")
    assert editor.occurred.value == "10/"
    assert editor.occurred.helper == "MM/DD/YYYY"
    assert not editor.pristine


def test_date_and_time_pickers_update_manual_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(DateTimeFields, "page", property(lambda self: page))
    editor = DateTimeFields("uk", "UTC", device_zone="Europe/Kyiv")
    row = editor.controls[0]
    assert isinstance(row, ft.Row)
    button = row.controls[1]
    assert isinstance(button, ft.IconButton)
    handler = button.on_click
    assert handler is not None
    MagicMock(wraps=handler)()
    picker = page.show_dialog.call_args.args[0]
    # Flet returns the chosen client-local midnight as a UTC datetime.
    picker.value = datetime(2026, 10, 17, 21, tzinfo=UTC)
    picker.on_change(MagicMock())
    assert editor.occurred.value == "18.10.2026"
    editor._pick_time()
    picker = page.show_dialog.call_args.args[0]
    assert picker.hour_format == ft.TimePickerHourFormat.H24
    picker.value = time(23, 18)
    picker.on_change()
    assert editor.clock.value == "23:18:00"
    assert editor.values()[0] == "2026-10-18"


def texts(control: ft.Control) -> list[str]:
    if isinstance(control, ft.Text):
        return [control.value or ""]
    if isinstance(control, ft.Container) and control.content is not None:
        return texts(control.content)
    if isinstance(control, (ft.Row, ft.Column)):
        return [text for child in control.controls for text in texts(child)]
    return []


def expense() -> Transaction:
    return Transaction(
        "stable-id",
        date(2026, 10, 18),
        "2026-10-18T12:34:56+03:00",
        "Synthetic shop",
        "Private long description",
        (
            Posting("Assets:Cash", Decimal("-10"), "UAH"),
            Posting("Expenses:Food", Decimal("10"), "UAH"),
        ),
        "expense",
    )


def test_compact_transaction_has_one_amount_movement_and_icon_actions() -> None:
    view = FinanceView()
    row = view._transaction_card(expense(), {"Assets:Cash": "Wallet", "Expenses:Food": "Food"})
    assert texts(row) == ["Wallet → Food", "10.00 UAH", "Oct 18, 2026 · Synthetic shop"]
    assert isinstance(row, ft.Container)
    assert isinstance(row.content, ft.Row)
    actions = row.content.controls[1:]
    assert all(isinstance(action, ft.IconButton) for action in actions)
    assert [action.tooltip for action in actions] == ["Edit", "Delete"]
    assert all(action.data == "stable-id" for action in actions)


def test_multicurrency_or_split_rows_do_not_invent_a_combined_amount() -> None:
    transaction = replace(
        expense(),
        postings=(
            Posting("Assets:Cash", Decimal("-10"), "USD"),
            Posting("Expenses:Food", Decimal("9"), "EUR"),
            Posting("Expenses:Fees", Decimal("1"), "EUR"),
        ),
    )
    content = texts(FinanceView()._transaction_card(transaction, {}))
    assert "Multiple postings" in content
    assert not any("USD" in text or "EUR" in text for text in content)


def test_compact_account_hides_technical_id_but_retains_name_balance_and_state() -> None:
    view = FinanceView()
    row = view._entity_card(Account("Assets:Cash", "Wallet", "UAH", active=False), Decimal("90"))
    assert texts(row) == ["Wallet", translate("en", "crud_inactive_label"), "90.00 UAH"]


def test_timezone_preference_persists_without_rewriting_journal_and_failure_retains_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=True)
        view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await view.journal.load()
        await view.journal.create_account("Cash", "Wallet", "UAH", "100")
        original = view.journal.source
        view.amount.value = "12.34"
        view.timezone_choice.text = "Europe/Kyiv"
        await view._save_timezone(MagicMock())
        assert view.timezone_name == "Europe/Kyiv"
        view.preferences.set.assert_awaited_once_with(TIMEZONE_KEY, "Europe/Kyiv")
        assert view.journal.source == original
        assert view.amount.value == "12.34"
        view.preferences.set.reset_mock()
        view.timezone_choice.text = "invalid-zone"
        await view._save_timezone(MagicMock())
        assert view.timezone_name == "Europe/Kyiv"
        view.preferences.set.assert_not_called()
        assert view.amount.value == "12.34"
        view.timezone_choice.text = "UTC"
        view.preferences.set.return_value = False
        await view._save_timezone(MagicMock())
        assert view.timezone_name == "Europe/Kyiv"
        assert view.journal.source == original

    asyncio.run(scenario())


def test_android_system_zone_comes_from_client_and_missing_zone_blocks_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.bridge = MagicMock()
        view.bridge.call = AsyncMock(return_value={"ok": True, "value": "Europe/Kyiv"})
        await view._sync_system_zone()
        assert view._effective_zone() == "Europe/Kyiv"
        assert view.expense_when.zone == "Europe/Kyiv"
        view.bridge.call.assert_awaited_once_with("timezone")
        view.bridge.call.return_value = {"ok": True, "value": None}
        with pytest.raises(LedgerError, match="crud_timezone_unavailable"):
            await view._sync_system_zone()
        with pytest.raises(LedgerError, match="crud_timezone_unavailable"):
            view._effective_zone()
        view.timezone_name = "UTC"
        assert view._effective_zone() == "UTC"

    asyncio.run(scenario())


def test_system_timezone_uses_os_rules_for_selected_date(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(system_time, "tzset"):
        pytest.skip("POSIX timezone test; Windows requires platform testing.")
    try:
        with monkeypatch.context() as context:
            context.setenv("TZ", "Europe/Kyiv")
            system_time.tzset()
            assert wall_timestamp(date(2026, 1, 15), "12:00").endswith("+02:00")
            assert wall_timestamp(date(2026, 7, 15), "12:00").endswith("+03:00")
            with pytest.raises(LedgerError, match="crud_time_missing"):
                wall_timestamp(date(2026, 3, 29), "03:30")
            with pytest.raises(LedgerError, match="crud_time_ambiguous"):
                wall_timestamp(date(2026, 10, 25), "03:30")
    finally:
        system_time.tzset()


def test_manual_edit_saves_localized_date_and_named_zone_time_with_stable_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.language = "uk"
        view.timezone_name = "Europe/Kyiv"
        view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await view.journal.load()
        await view.journal.create_account("Cash", "Wallet", "UAH", "100")
        await view.journal.create_category("Food", "Food")
        await view.journal.add_expense(
            "Assets:Cash", "Expenses:Food", "10", "Synthetic shop", "2026-10-18"
        )
        transaction = view.journal.ledger.transactions[-1]
        event = MagicMock()
        event.control.data = transaction.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        editor = next(c for c in dialog.content.controls if isinstance(c, DateTimeFields))
        editor.occurred.value = "17.10.2026"
        editor.stamp_date.value = "16.10.2026"
        editor.clock.value = "14:15"
        await dialog.actions[-1].on_click(MagicMock())
        edited = view.journal.ledger.transactions[-1]
        assert edited.id == transaction.id
        assert edited.date == date(2026, 10, 17)
        assert edited.timestamp == "2026-10-16T14:15:00+03:00"
        assert list((tmp_path / "backups").iterdir())
        assert view.journal.ledger.balances()["Assets:Cash"] == Decimal("90")

    asyncio.run(scenario())


def test_readonly_transaction_opens_details_instead_of_hiding_its_postings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await view.journal.load()
        await view.journal.create_account("Cash", "Wallet", "UAH", "100")
        original = view.journal.source
        event = MagicMock()
        event.control.data = view.journal.ledger.transactions[0].id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == "Details"
        assert "Wallet: 100.00 UAH" in texts(dialog.content)
        assert any("Equity:Opening-Balances" in text for text in texts(dialog.content))
        assert len(dialog.actions) == 1
        assert view.journal.source == original

    asyncio.run(scenario())


def test_default_payee_prefills_new_expenses_but_never_overwrites_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=True)
        view.default_payee_field.value = "  Default shop  "
        await view._save_default_payee(MagicMock())
        assert view.default_payee == view.payee.value == "Default shop"
        view.preferences.set.assert_awaited_once_with(DEFAULT_PAYEE_KEY, "Default shop")
        view.payee.value = "Unsaved custom payee"
        view.amount.value = "12.34"
        view.default_payee_field.value = "Other shop"
        await view._save_default_payee(MagicMock())
        assert view.payee.value == "Unsaved custom payee"
        assert view.amount.value == "12.34"
        view.preferences.set.return_value = False
        view.default_payee_field.value = "Failure"
        await view._save_default_payee(MagicMock())
        assert view.default_payee == "Other shop"
        assert view.payee.value == "Unsaved custom payee"
        view.preferences.set.return_value = True
        view.default_payee_field.value = ""
        await view._save_default_payee(MagicMock())
        assert view.default_payee == ""
        assert view.payee.value == "Unsaved custom payee"

    asyncio.run(scenario())


def test_default_payee_resets_after_success_not_after_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.default_payee = "Default shop"
        view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await view.journal.load()
        await view.journal.create_account("Cash", "Wallet", "UAH", "100")
        await view.journal.create_category("Food", "Food")
        view.account_choice.value = "Assets:Cash"
        view.category_choice.value = "Expenses:Food"
        view.amount.value = "10"
        view.payee.value = "One-off shop"
        await view._add_expense(MagicMock())
        assert view.journal.ledger.transactions[-1].payee == "One-off shop"
        assert view.payee.value == "Default shop"
        view.amount.value = "1000"
        view.payee.value = "Unsaved shop"
        await view._add_expense(MagicMock())
        assert view.payee.value == "Unsaved shop"
        assert view.amount.value == "1000"

    asyncio.run(scenario())


def test_accounting_today_can_come_from_device_zone_instead_of_python_host(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2100, 1, 1))
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", "100")
        await journal.create_category("Food", "Food")
        await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food",
            "10",
            "Synthetic",
            "2100-01-01",
            timestamp="2100-01-01T00:01:00+14:00",
        )
        assert journal.ledger.transactions[-1].date == date(2100, 1, 1)
        with pytest.raises(LedgerError, match="Expense date must"):
            await journal.add_expense(
                "Assets:Cash",
                "Expenses:Food",
                "10",
                "Synthetic",
                "2100-01-02",
            )

    asyncio.run(scenario())
