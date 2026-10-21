import asyncio
import time as system_time
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from beancount import loader
from flet.controls.device_info import DeviceInfo
from form_controls import transaction_form

from app.domain.ledger import Account, LedgerError, Posting, Transaction
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import DesktopPreferences
from app.journal import FILENAME, Journal
from app.localization import date_input, format_timestamp, parse_date_input, translate
from app.storage import StorageError
from app.timekeeping import local_datetime, named_zone, wall_timestamp
from app.ui.datetime_fields import DateTimeFields
from app.ui.finance import TIMEZONE_KEY, FinanceView


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


@pytest.mark.parametrize(
    "clock",
    ["", "24:00", "24:00:00", "24:00:00.000000", "12:60", "12:00:60", "1200", "12", "12:00+02:00"],
)
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
    assert editor.occurred.value == "10/25/2026"
    assert editor.values()[1] == stamp
    editor.clock.value = "03:31"
    with pytest.raises(LedgerError, match="crud_time_ambiguous"):
        editor.values()
    editor.occurred.value = "10/23/2026"
    assert editor.values() == ("2026-10-23", "2026-10-23T03:31:00+03:00")


def test_explicit_zone_change_preserves_draft_instant_and_accounting_date() -> None:
    editor = DateTimeFields("en", "UTC", date(2026, 1, 1), "2026-01-02T23:30:00+00:00")
    editor.set_zone("Europe/Kyiv")
    assert editor.occurred.value == "01/03/2026"
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
    assert texts(row) == ["Wallet → Food", "10.00 UAH", "Private long description", "Oct 18, 2026"]
    assert isinstance(row, ft.Container)
    assert isinstance(row.content, ft.Row)
    actions = row.content.controls[1:]
    assert all(isinstance(action, ft.IconButton) for action in actions)
    assert [action.tooltip for action in actions] == ["Edit", "Delete"]
    assert all(action.data == "stable-id" for action in actions)


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("kind", ["expense", "income", "reimbursement", "adjustment", "external"])
def test_transaction_description_preview_is_bounded_without_changing_saved_text(
    compact: bool, kind: str
) -> None:
    narration = "Synthetic purchase\nwith details " + "and more details " * 40
    transaction = replace(expense(), kind=kind, narration=narration)
    row = FinanceView()._transaction_card(transaction, {}, compact)
    assert isinstance(row, ft.Container) and isinstance(row.content, ft.Row)
    column = row.content.controls[0]
    assert isinstance(column, ft.Column)
    preview = column.controls[1]
    assert isinstance(preview, ft.Text)
    assert preview.value == " ".join(narration.split())
    assert preview.max_lines == 1 and preview.overflow == ft.TextOverflow.ELLIPSIS
    assert transaction.narration == narration


@pytest.mark.parametrize("narration", ["", " \n "])
def test_blank_transaction_description_does_not_add_an_empty_preview(narration: str) -> None:
    row = FinanceView()._transaction_card(replace(expense(), narration=narration), {})
    assert isinstance(row, ft.Container) and isinstance(row.content, ft.Row)
    column = row.content.controls[0]
    assert isinstance(column, ft.Column)
    assert len(column.controls) == 2


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
    assert content.count(transaction.narration) == 1
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
        view._transaction_dialog(view.journal)
        dialog = page.show_dialog.call_args.args[0]
        amount = transaction_form(dialog).amount
        amount.value = "12.34"
        view.timezone_choice.text = "Europe/Kyiv"
        await view._save_timezone(MagicMock())
        assert view.timezone_name == "Europe/Kyiv"
        view.preferences.set.assert_awaited_once_with(TIMEZONE_KEY, "Europe/Kyiv")
        assert view.journal.source == original
        assert amount.value == "12.34"
        view.preferences.set.reset_mock()
        view.timezone_choice.text = "invalid-zone"
        await view._save_timezone(MagicMock())
        assert view.timezone_name == "Europe/Kyiv"
        view.preferences.set.assert_not_called()
        assert amount.value == "12.34"
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
    tzset = getattr(system_time, "tzset", None)
    if tzset is None:
        pytest.skip("POSIX timezone test; Windows requires platform testing.")
    try:
        with monkeypatch.context() as context:
            context.setenv("TZ", "Europe/Kyiv")
            tzset()
            assert wall_timestamp(date(2026, 1, 15), "12:00").endswith("+02:00")
            assert wall_timestamp(date(2026, 7, 15), "12:00").endswith("+03:00")
            with pytest.raises(LedgerError, match="crud_time_missing"):
                wall_timestamp(date(2026, 3, 29), "03:30")
            with pytest.raises(LedgerError, match="crud_time_ambiguous"):
                wall_timestamp(date(2026, 10, 25), "03:30")
    finally:
        tzset()


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
        editor.clock.value = "14:15"
        await dialog.actions[-1].on_click(MagicMock())
        edited = view.journal.ledger.transactions[-1]
        assert edited.id == transaction.id
        assert edited.date == date(2026, 10, 17)
        assert edited.timestamp == "2026-10-17T14:15:00+03:00"
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
        (tmp_path / FILENAME).write_text(
            view.journal.source.replace('txncount-kind: "opening"', 'txncount-kind: "external"'),
            newline="",
        )
        await view.journal.load()
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


def test_optional_payee_is_blank_and_collapsed_after_success_not_after_validation_failure(
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
        await view.journal.create_category("Food", "Food")
        view._transaction_dialog(view.journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, tile, payee = (
            form.account,
            form.category,
            form.amount,
            form.advanced,
            form.payee,
        )
        assert not payee.value and not tile.expanded
        account.value = "Assets:Cash"
        category.value = "Expenses:Food"
        amount.value = "10"
        payee.value = "One-off shop"
        tile.expanded = True
        await dialog.actions[-1].on_click(MagicMock())
        assert view.journal.ledger.transactions[-1].payee == "One-off shop"
        page.pop_dialog.assert_called_once()
        view._transaction_dialog(view.journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, tile, payee = (
            form.account,
            form.category,
            form.amount,
            form.advanced,
            form.payee,
        )
        assert not payee.value and not tile.expanded
        account.value = "Assets:Cash"
        category.value = "Expenses:Food"
        amount.value = "invalid"
        payee.value = "Unsaved shop"
        tile.expanded = True
        await dialog.actions[-1].on_click(MagicMock())
        assert payee.value == "Unsaved shop"
        assert amount.value == "invalid"
        assert tile.expanded
        assert page.pop_dialog.call_count == 1

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


def test_new_datetime_group_has_one_date_and_both_stored_dates_follow_manual_changes() -> None:
    editor = DateTimeFields("uk", "Europe/Kyiv", timestamp="2026-10-18T12:34:56+03:00")
    assert len(editor.controls) == 3
    assert editor.values() == ("2026-10-18", "2026-10-18T12:34:56+03:00")
    editor.occurred.value = "17.10.2026"
    editor.clock.value = "14:15"
    assert editor.values() == ("2026-10-17", "2026-10-17T14:15:00+03:00")


def test_new_expense_starts_empty_even_with_a_legacy_default_payee_preference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("APPDATA", str(tmp_path))
        await DesktopPreferences().set("txncount.default-payee.v1", "Legacy shop")
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.width = 390
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(locales=[ft.Locale(language_code="en")])
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view._transaction_dialog(journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        tile, payee = form.advanced, form.payee
        assert isinstance(payee, ft.TextField)
        assert not payee.value
        assert not tile.expanded
        assert tile.maintain_state
        assert not hasattr(view, "default_payee_field")

    asyncio.run(scenario())


def test_description_is_prominent_and_currency_settings_are_advanced_in_all_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
    view = FinanceView()
    journal = MagicMock()
    journal.source = ""
    for language in ("en", "uk", "ru"):
        view.language = language
        view._refresh_labels()
        view._transaction_dialog(journal)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        description, tile = form.description, form.advanced
        assert description.multiline and description.min_lines == 1
        assert description.text_size == 17
        assert form.payee not in dialog.content.controls
        assert not tile.expanded
        assert view.advanced_settings in view.settings_screen.controls
        assert not view.advanced_settings.expanded
        assert view.operating_currencies in (view.advanced_settings.controls or [])
        assert view.operating_currencies.label == translate(language, "crud_operating")
        assert isinstance(view.operating_currencies.label, str)
        assert "Fava" not in view.operating_currencies.label
        assert translate(language, "crud_operating_help") in texts(
            ft.Column(view.advanced_settings.controls or [])
        )
    view.startup_complete = True
    view._apply_layout(390)
    assert page.floating_action_button is view.fab
    view._apply_layout(1366)
    view._apply_layout(2000)


def test_hidden_saved_payee_and_original_dates_survive_edit_and_conflicting_draft_is_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.timezone_name = "Europe/Kyiv"
        journal = view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", "100")
        await journal.create_category("Food", "Food")
        await journal.add_expense("Assets:Cash", "Expenses:Food", "10", "Saved shop", "2026-01-01")
        original = journal.ledger.transactions[-1]
        stamp = "2026-01-02T00:30:00.123456Z"
        source = journal.source.replace(original.timestamp, stamp)
        (tmp_path / FILENAME).write_text(source, newline="")
        await journal.load()
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        tile = next(c for c in dialog.content.controls if isinstance(c, ft.ExpansionTile))
        assert not tile.expanded
        assert tile.controls
        payee = next(c for c in tile.controls if isinstance(c, ft.TextField))
        assert payee.value == "Saved shop"
        when = next(c for c in dialog.content.controls if isinstance(c, DateTimeFields))
        assert when.occurred.value == "01/02/2026"
        transaction_form(dialog).amount.value = "12"
        await dialog.actions[-1].on_click(MagicMock())
        saved = next(t for t in journal.ledger.transactions if t.id == original.id)
        assert saved.payee == "Saved shop"
        assert saved.date == date(2026, 1, 1)
        assert saved.timestamp == stamp

        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        tile = next(c for c in dialog.content.controls if isinstance(c, ft.ExpansionTile))
        assert tile.controls
        payee = next(c for c in tile.controls if isinstance(c, ft.TextField))
        payee.value = "Draft shop"
        tile.expanded = True
        description = next(
            c for c in dialog.content.controls if isinstance(c, ft.TextField) and c.multiline
        )
        description.value = "Unsaved\nsecond line"
        external = journal.source + "\n; external change\n"
        (tmp_path / FILENAME).write_text(external, newline="")
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message
        assert payee.value == "Draft shop"
        assert description.value == "Unsaved\nsecond line"
        assert tile.expanded
        assert (tmp_path / FILENAME).read_text(newline="") == external

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_opening_rows_hide_dates_and_page_headings_are_not_repeated(language: str) -> None:
    view = FinanceView()
    view.language = language
    view._refresh_labels()
    opening = replace(
        expense(),
        date=date(1970, 1, 1),
        kind="opening",
        postings=(
            Posting("Assets:Cash", Decimal("100"), "UAH"),
            Posting("Equity:Opening-Balances", Decimal("-100"), "UAH"),
        ),
    )
    for compact in (False, True):
        row = view._transaction_card(opening, {"Assets:Cash": "Wallet"}, compact)
        assert texts(row) == [
            f"{view.tr('kind_opening')} → Wallet",
            "100.00 UAH" if language == "en" else "100,00 UAH",
        ]
    header = view.body.controls[0]
    assert isinstance(header, ft.Container)
    assert header.content is view.page_title
    for screen in ("home", "transactions", "accounts", "settings"):
        view.current_screen = screen
        view._refresh_labels()
        assert texts(header) == [str(view.page_title.value)]
        assert "TxnCount" not in texts(header)
    assert view.tr("settings_title") not in texts(view.settings_screen)


@pytest.mark.parametrize(
    "mode", ["save", "zero", "negative", "conflict", "stale", "backup", "cancel"]
)
def test_opening_transaction_editor_uses_safe_account_balance_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        journal = view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", "100")
        await journal.create_category("Food", "Food")
        if mode != "zero":
            await journal.add_expense("Assets:Cash", "Expenses:Food", "10", "", "2026-01-01")
        else:
            await journal.edit_account("Assets:Cash", "Wallet", "0", True, journal.source)
        source = journal.source.replace(
            'txncount-kind: "opening"', 'txncount-kind: "opening"\n    receipt: "keep" ; comment'
        )
        (tmp_path / FILENAME).write_text(source, newline="")
        await journal.load()
        original = next(t for t in journal.ledger.transactions if t.kind == "opening")
        before = journal.source
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == view.tr("kind_opening")
        assert not any(isinstance(c, DateTimeFields) for c in dialog.content.controls)
        details = next(c for c in dialog.content.controls if isinstance(c, ft.ExpansionTile))
        assert not details.expanded
        assert details.controls
        assert any("1970" in str(c.value) for c in details.controls if isinstance(c, ft.Text))
        amount = next(c for c in dialog.content.controls if isinstance(c, ft.TextField))
        assert amount.value == ("0" if mode == "zero" else "100")
        amount.value = "5" if mode == "negative" else "120"
        expected = before
        if mode in {"conflict", "stale"}:
            expected += "\n; external\n"
            (tmp_path / FILENAME).write_text(expected, newline="")
            if mode == "stale":
                await journal.poll()
        elif mode == "backup":
            journal.storage.folder.backup = AsyncMock(side_effect=StorageError("Synthetic"))
        if mode == "cancel":
            dialog.actions[0].on_click(MagicMock())
        else:
            await dialog.actions[-1].on_click(MagicMock())
        if mode in {"save", "zero", "negative"}:
            saved = next(t for t in journal.ledger.transactions if t.id == original.id)
            assert saved.date == original.date and saved.timestamp == original.timestamp
            assert journal.opening_balance("Assets:Cash") == Decimal(
                "5" if mode == "negative" else "120"
            )
            assert journal.ledger.balances()["Assets:Cash"] == Decimal(
                "120" if mode == "zero" else "-5" if mode == "negative" else "110"
            )
            assert view.balance_warning.visible == (mode == "negative")
            assert 'receipt: "keep" ; comment' in journal.source
            assert any(p.read_text(newline="") == before for p in (tmp_path / "backups").iterdir())
            assert not loader.load_string(journal.source)[1]
        else:
            assert (tmp_path / FILENAME).read_text(newline="") == expected
            assert amount.value == ("5" if mode == "negative" else "120")
            if mode != "cancel":
                assert view.error_message

    asyncio.run(scenario())


def test_multiple_openings_remain_readonly_with_dates_in_collapsed_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        journal = view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Cash", "Wallet", "UAH", "100")
        original = journal.ledger.transactions[0]
        source = journal.source + (
            '\n1970-01-01 * "Additional opening"\n'
            '  id: "second-opening"\n'
            '  txncount-kind: "opening"\n'
            "  Assets:Cash 10 UAH\n"
            "  Equity:Opening-Balances -10 UAH\n"
        )
        (tmp_path / FILENAME).write_text(source, newline="")
        await journal.load()
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        assert dialog.title == view.tr("details")
        assert view.tr("crud_opening_complex") in texts(dialog.content)
        assert not any(isinstance(c, ft.TextField) for c in dialog.content.controls)
        details = next(c for c in dialog.content.controls if isinstance(c, ft.ExpansionTile))
        assert not details.expanded
        assert len(dialog.actions) == 1
        assert (tmp_path / FILENAME).read_text(newline="") == source

    asyncio.run(scenario())
