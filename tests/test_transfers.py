import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from autobean_refactor import models
from beancount import loader
from beancount.ops import summarize
from fava.core import FavaLedger
from form_controls import change, transaction_form
from journal_fixtures import review_external_fixture

from app.domain.ledger import BANK_FEES, EPOCH, LedgerError, exchange_values
from app.domain.storage import ConflictError
from app.infrastructure.beancount_journal import transaction_time
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import translate
from app.storage import StorageError
from app.ui.finance import FinanceView

DAY = "2026-01-10"
STAMP = "2026-01-10T12:00:00.123456+02:00"


async def seeded(path: Path) -> Journal:
    journal = Journal(DesktopFolder(str(path), FILENAME), today=lambda: date(2026, 3, 1))
    await journal.load()
    await journal.create_account("Bank", "Bank", "UAH", "2000", opened=EPOCH, opening_date=EPOCH)
    await journal.create_account("Other", "Other bank", "UAH", opened=EPOCH)
    await journal.create_account("Cash", "Cash", "UAH", opened=EPOCH)
    await journal.create_account("Euro", "Euro", "EUR", opened=EPOCH)
    await journal.create_account(
        "Loan", "Loan", "UAH", "1500", root="Liabilities", opened=EPOCH, opening_date=EPOCH
    )
    await journal.create_category("Fees:Other", "Other fees", opened=EPOCH)
    return journal


async def transfer(journal: Journal, **changes: str) -> str | None:
    values = dict(
        source_account="Assets:Bank",
        destination="Assets:Other",
        amount="1000",
        fee="0",
        fee_category=BANK_FEES,
        fee_mode="added",
        occurred=DAY,
        timestamp=STAMP,
        description="Synthetic transfer",
        payee="",
        expected_source=journal.source,
    )
    values.update(changes)
    return await journal.save_transfer(**values)


@pytest.mark.parametrize("target", ["Assets:Other", "Assets:Cash", "Liabilities:Loan"])
@pytest.mark.parametrize(
    ("mode", "fee", "source_change", "destination_change"),
    [
        ("added", "0", "-1000", "1000"),
        ("added", "5", "-1005", "1000"),
        ("deducted", "5", "-1000", "995"),
    ],
)
def test_same_currency_transfer_balances_reports_roundtrip(
    tmp_path: Path,
    target: str,
    mode: str,
    fee: str,
    source_change: str,
    destination_change: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        balances = journal.ledger.balances()
        backup = await transfer(journal, destination=target, fee=fee, fee_mode=mode)
        transaction = journal.ledger.transactions[-1]
        assert transaction.kind == "transfer"
        assert len(transaction.postings) == (3 if fee != "0" else 2)
        assert transaction.fee_mode == "added"
        assert journal.transfer_details(transaction).mode == "added"
        assert journal.transfer_details(transaction).target.amount == Decimal(destination_change)
        after = journal.ledger.balances()
        assert after["Assets:Bank"] == balances["Assets:Bank"] + Decimal(source_change)
        assert after[target] == balances[target] + Decimal(destination_change)
        totals = journal.ledger.period_totals(date(2026, 1, 1), date(2026, 2, 1))
        assert totals == {"income": {}, "expenses": {"UAH": Decimal(fee)} if fee != "0" else {}}
        assert (tmp_path / str(backup)).read_text() == before
        assert not loader.load_string(journal.source)[1]
        fava = FavaLedger(str(tmp_path / FILENAME))
        assert not list(fava.errors)
        saved = journal.source
        await journal.load()
        assert journal.source == saved
        assert journal.ledger.transactions[-1] == transaction
        assert journal.ledger.balances() == after
        assert (BANK_FEES in {c.id for c in journal.ledger.categories}) == (fee != "0")

    asyncio.run(scenario())


def test_cash_to_bank_borrowing_and_liability_opening_edits(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        assert journal.opening_balance("Liabilities:Loan") == 1500
        assert journal.ledger.balances()["Liabilities:Loan"] == -1500
        await transfer(
            journal, source_account="Liabilities:Loan", destination="Assets:Cash", amount="50"
        )
        assert journal.ledger.balances()["Liabilities:Loan"] == -1550
        assert not journal.ledger.balance_warnings()
        await transfer(journal, source_account="Assets:Cash", amount="50")
        assert journal.ledger.balances()["Assets:Cash"] == 0
        original = next(
            t
            for t in journal.ledger.transactions
            if t.kind == "opening" and any(p.account == "Liabilities:Loan" for p in t.postings)
        )
        await journal.edit_account("Liabilities:Loan", "Loan", "1600", True, journal.source)
        assert journal.opening_balance("Liabilities:Loan") == 1600
        assert journal.ledger.balances()["Liabilities:Loan"] == -1650
        assert any(t.id == original.id for t in journal.ledger.transactions)
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


def test_fee_defaults_custom_category_and_deactivation(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.set_default_fee_category("Expenses:Fees:Other", journal.source)
        await journal.load()
        assert journal.ledger.default_fee_category == "Expenses:Fees:Other"
        await transfer(journal, fee="2", fee_category=journal.ledger.default_fee_category)
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}
        assert journal.ledger.transactions[-1].postings[-1].account == "Expenses:Fees:Other"
        await journal.edit_category("Expenses:Fees", "Fees", False, journal.source)
        original = journal.source
        with pytest.raises(LedgerError, match="incoming_category"):
            await transfer(journal, fee="2", fee_category="Expenses:Fees:Other")
        assert journal.source == original
        await journal.edit_category("Expenses:Fees", "Fees", True, journal.source)
        await transfer(journal, fee="1")
        assert len([c for c in journal.ledger.categories if c.id == BANK_FEES]) == 1
        await journal.set_default_fee_category(BANK_FEES, journal.source)
        await journal.load()
        assert journal.ledger.default_fee_category == BANK_FEES

    asyncio.run(scenario())


def test_transfer_edit_fee_modes_accounts_and_delete_preserve_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await transfer(journal, fee="5")
        original = journal.ledger.transactions[-1]
        source = journal.source.replace(
            f'  time: "{transaction_time(original.timestamp)}"',
            f'  time: "{transaction_time(original.timestamp)}"\n'
            '  textmoney-kind: "transfer"\n'
            '  receipt: "keep" ; keep comment',
        ).replace(
            "Assets:Bank -1005 UAH",
            'Assets:Bank -1005 UAH ; posting comment\n        reference: "keep posting"',
        )
        (tmp_path / FILENAME).write_text(source)
        await review_external_fixture(journal)
        await transfer(
            journal,
            identifier=original.id,
            destination="Liabilities:Loan",
            amount="100",
            fee="3",
            fee_mode="deducted",
            fee_category="Expenses:Fees:Other",
            occurred="2026-01-11",
        )
        updated = journal.transaction_for_edit(original.id)
        assert updated.id == original.id and updated.timestamp == "2026-01-11T12:00:00"
        assert updated.date == date(2026, 1, 11)
        assert [p.amount for p in updated.postings] == [-100, 97, 3]
        assert "keep comment" in journal.source and "keep posting" in journal.source
        await transfer(journal, identifier=original.id, fee="0", destination="Assets:Cash")
        assert len(journal.transaction_for_edit(original.id).postings) == 2
        await transfer(journal, identifier=original.id, fee="4")
        assert len(journal.transaction_for_edit(original.id).postings) == 3
        assert not loader.load_string(journal.source)[1]
        await journal.delete_transaction(original.id, journal.source)
        assert all(t.id != original.id for t in journal.ledger.transactions)
        assert journal.ledger.balances()["Assets:Bank"] == 2000
        assert journal.ledger.period_totals(date(2026, 1, 1), date(2026, 2, 1)) == {
            "income": {},
            "expenses": {},
        }
        assert "keep comment" in journal.source

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0"},
        {"amount": "-1"},
        {"amount": "NaN"},
        {"fee": "-1"},
        {"fee": "1.001"},
        {"fee": "1000", "fee_mode": "deducted"},
        {"fee": "1001", "fee_mode": "deducted"},
        {"fee_mode": "other"},
        {"destination": "Assets:Bank"},
        {"destination": "Assets:Missing"},
        {"destination": "Assets:Euro"},
        {"fee": "1", "fee_category": "Expenses:Missing"},
        {"amount": "999999999999.99", "fee": "1"},
        {"timestamp": "2026-01-10T25:00:00"},
        {"occurred": "2026-04-01"},
        {"occurred": "bad"},
    ],
)
def test_invalid_transfers_never_write_or_create_fee_category(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = journal.source
        backups = set((tmp_path / "backups").iterdir())
        with pytest.raises(LedgerError):
            await transfer(journal, **changes)
        assert journal.source == original
        assert (tmp_path / FILENAME).read_text() == original
        assert backups == set((tmp_path / "backups").iterdir())

    asyncio.run(scenario())


def test_overdraft_and_backdated_history_warn_but_allow_reload_and_other_operations(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await transfer(journal, amount="2100")
        assert journal.ledger.balances()["Assets:Bank"] == -100
        assert journal.ledger.balance_warnings() == {"Assets:Bank"}
        await journal.load()
        assert journal.ready
        await journal.create_category("Food", "Food", opened=EPOCH)
        await journal.add_expense("Assets:Bank", "Expenses:Food", "2", "", DAY)
        assert journal.ledger.balances()["Assets:Bank"] == -102
        # Later funding removes the current overdraft but not the historical warning.
        await transfer(
            journal,
            source_account="Assets:Other",
            destination="Assets:Bank",
            amount="200",
            occurred="2026-02-01",
        )
        assert journal.ledger.balances()["Assets:Bank"] == 98
        assert journal.ledger.balance_warnings() == {"Assets:Bank"}

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["stale", "conflict", "backup", "write"])
def test_transfer_failure_preserves_original_and_does_not_save_fee_separately(
    tmp_path: Path, failure: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = journal.source
        changes = {}
        expected = original
        error = StorageError
        if failure == "stale":
            changes["expected_source"] = "old"
            error = ConflictError
        elif failure == "conflict":
            expected += "; external\n"
            (tmp_path / FILENAME).write_text(expected)
            error = ConflictError
        elif failure == "backup":
            journal.storage.folder.backup = AsyncMock(
                side_effect=StorageError("Synthetic backup failure")
            )
        else:
            journal.storage.folder.write = AsyncMock(
                side_effect=StorageError("Synthetic write failure")
            )
        with pytest.raises(error):
            await transfer(journal, fee="5", **changes)
        assert journal.source == original
        assert (tmp_path / FILENAME).read_text() == expected
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_unified_transfer_form_fee_edit_and_complete_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.language = language
        view.timezone_name = "UTC"
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, category, amount, description, when = (
            form.account,
            form.category,
            form.amount,
            form.description,
            form.when,
        )
        destination, enabled, fee, deduction, fee_category = (
            form.second_account,
            form.fee_enabled,
            form.fee,
            form.fee_mode,
            form.fee_category,
        )
        assert isinstance(destination, ft.Dropdown)
        assert isinstance(enabled, ft.Checkbox)
        assert isinstance(fee, ft.TextField)
        assert isinstance(deduction, ft.Dropdown)
        assert isinstance(fee_category, ft.Dropdown)
        account.value, destination.value = "Assets:Bank", "Liabilities:Loan"
        form.sign.selected = ["transfer"]
        change(destination)
        assert not category.visible and not fee.visible
        assert destination.label == translate(language, "destination_account")
        amount.value, description.value = "2100", "Synthetic repayment"
        enabled.value = True
        assert enabled.on_change is not None
        cast(Callable[[ft.Event[ft.Checkbox]], None], enabled.on_change)(MagicMock())
        assert fee.visible and fee_category.visible and deduction.visible
        fee.value = "5"
        deduction.value = "deducted"
        when.occurred.value = "01/10/2026" if language == "en" else "10.01.2026"
        when.clock.value = "12:00"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        assert view.balance_warning.visible
        transaction = journal.ledger.transactions[-1]
        assert len(transaction.postings) == 3
        event = MagicMock()
        event.control.data = transaction.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.sign.disabled and form.sign.selected == ["transfer"]
        assert isinstance(form.advanced.subtitle, str)
        assert translate(language, "kind_transfer") in form.advanced.subtitle
        destination, enabled, fee, deduction, fee_category = (
            form.second_account,
            form.fee_enabled,
            form.fee,
            form.fee_mode,
            form.fee_category,
        )
        assert isinstance(destination, ft.Dropdown)
        assert isinstance(fee, ft.TextField)
        assert isinstance(deduction, ft.Dropdown)
        assert isinstance(fee_category, ft.Dropdown)
        assert destination.value == "Liabilities:Loan" and fee.value == "5"
        assert deduction.value == "added" and fee_category.value == BANK_FEES
        assert form.amount.value == "2095"
        assert journal.transfer_details(transaction).source.amount == Decimal("-2100")
        form.amount.value = "1000"
        deduction.value = "added"
        fee.value = "2"
        await dialog.actions[-1].on_click(MagicMock())
        assert not view.balance_warning.visible
        view._delete_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        await dialog.actions[-1].on_click(MagicMock())
        assert all(t.id != transaction.id for t in journal.ledger.transactions)
        assert journal.ledger.balances()["Liabilities:Loan"] == -1500

    asyncio.run(scenario())


def test_fee_annotation_survives_disabling_fee_and_then_reenabling(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await transfer(journal, fee="5")
        transaction = journal.ledger.transactions[-1]
        source = journal.source.replace(
            "Expenses:Bank-Fees 5 UAH",
            'Expenses:Bank-Fees 5 UAH ; fee receipt\n        receipt: "preserve"',
        )
        (tmp_path / FILENAME).write_text(source)
        await review_external_fixture(journal)
        await transfer(journal, identifier=transaction.id, fee="0")
        saved = journal.transaction_for_edit(transaction.id)
        assert len(saved.postings) == 3 and saved.postings[-1].amount == 0
        assert 'receipt: "preserve"' in journal.source and "; fee receipt" in journal.source
        await transfer(journal, identifier=transaction.id, fee="2")
        assert 'receipt: "preserve"' in journal.source
        assert journal.transaction_for_edit(transaction.id).postings[-1].amount == 2
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


def test_transfer_settings_draft_is_not_silently_rebased(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.journal = journal
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=True)
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._refresh()
        assert view.default_fee.value == BANK_FEES
        view.default_fee.value = "Expenses:Fees:Other"
        await view._save_default_fee(MagicMock())
        assert journal.ledger.default_fee_category == "Expenses:Fees:Other"
        assert view.error_message is None
        view.default_fee.value = BANK_FEES
        journal.source += "; external reload\n"
        view._refresh()
        assert view.default_fee.value == BANK_FEES
        await view._save_default_fee(MagicMock())
        assert view.error_message == "crud_stale"
        await journal.load()
        view.default_fee_journal = None
        view._refresh()
        view.default_fee.value = BANK_FEES
        await view._save_default_fee(MagicMock())
        assert journal.ledger.default_fee_category == BANK_FEES
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}

    asyncio.run(scenario())


def test_liability_opening_row_and_editor_use_account_name_and_amount_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view = FinanceView()
        view.journal = journal
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        opening = next(
            t
            for t in journal.ledger.transactions
            if t.kind == "opening" and any(p.account == "Liabilities:Loan" for p in t.postings)
        )
        card = view._transaction_card(opening, {"Liabilities:Loan": "Loan"})
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        column = card.content.controls[0]
        assert isinstance(column, ft.Column)
        heading = column.controls[0]
        assert isinstance(heading, ft.Row) and isinstance(heading.controls[0], ft.Text)
        assert heading.controls[0].value == "Opening balance → Loan"
        event = MagicMock()
        event.control.data = opening.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        amount = next(c for c in dialog.content.controls if isinstance(c, ft.TextField))
        assert amount.label == "Opening amount owed" and amount.value == "1500"
        amount.value = "1600"
        await dialog.actions[-1].on_click(MagicMock())
        assert journal.ledger.balances()["Liabilities:Loan"] == -1600

    asyncio.run(scenario())


async def exchange_seeded(path: Path) -> Journal:
    journal = await seeded(path)
    await journal.create_account(
        "Dollar", "Dollar", "USD", "1000", opened=EPOCH, opening_date=EPOCH
    )
    await journal.create_account("Pound", "Pound", "GBP", "500", opened=EPOCH, opening_date=EPOCH)
    return journal


@pytest.mark.parametrize("exchange", [False, True])
@pytest.mark.parametrize("compact", [False, True])
def test_transfer_summary_is_above_details_and_accounts(
    tmp_path: Path, exchange: bool, compact: bool
) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        options = (
            {
                "source_account": "Assets:Dollar",
                "destination_amount": "4200",
                "fee_currency": "USD",
            }
            if exchange
            else {}
        )
        await transfer(journal, amount="100", fee="5", **options)
        transaction = journal.ledger.transactions[-1]
        view = FinanceView()
        view.journal = journal
        names = {account.id: account.name for account in journal.ledger.accounts}
        row = view._transaction_card(transaction, names, compact)
        assert isinstance(row, ft.Container) and isinstance(row.content, ft.Row)
        column = row.content.controls[0]
        assert isinstance(column, ft.Column)
        heading = column.controls[0]
        assert isinstance(heading, ft.Row)
        summary, amount = heading.controls
        assert isinstance(summary, ft.Text)
        assert summary.value == "Synthetic transfer" and summary.size == 14
        assert isinstance(amount, ft.Text)
        assert amount.value == ("4,200.00 UAH" if exchange else "100.00 UAH")
        movement = column.controls[-1]
        assert isinstance(movement, ft.Text)
        assert movement.value == ("Dollar → Other bank" if exchange else "Bank → Other bank")
        assert movement.size == 10
        details = column.controls[1:-1]
        assert len(details) == (2 if exchange else 1)
        fee = details[-1]
        assert isinstance(fee, ft.Text)
        assert fee.value == ("Fee: 5.00 USD" if exchange else "Fee: 5.00 UAH")
        assert len(row.content.controls[1:]) == (1 if compact else 2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("source", "target", "debit", "credit"),
    [
        ("Assets:Dollar", "Assets:Bank", "100.00", "4200.00"),
        ("Assets:Bank", "Assets:Dollar", "4200", "100"),
        ("Assets:Euro", "Assets:Pound", "3", "1"),
        ("Assets:Pound", "Assets:Euro", "3.01", "7.99"),
        ("Assets:Euro", "Liabilities:Loan", "2.47", "101.13"),
        ("Assets:Dollar", "Assets:Bank", "999999999999.99", "0.01"),
        ("Assets:Dollar", "Assets:Bank", "0.01", "999999999999.99"),
    ],
)
@pytest.mark.parametrize("fee_side", ["none", "source", "destination"])
def test_exchange_exact_balances_real_engine_and_roundtrip(
    tmp_path: Path,
    source: str,
    target: str,
    debit: str,
    credit: str,
    fee_side: str,
) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        currencies = {a.id: a.currency for a in journal.ledger.accounts}
        # Extreme values leave no room for a fee or its total-price component.
        fee = (
            Decimal("0.01")
            if fee_side != "none" and min(Decimal(debit), Decimal(credit)) > Decimal(".01")
            else Decimal("0")
        )
        currency = currencies[target] if fee_side == "destination" else currencies[source]
        before = journal.source
        balances = journal.ledger.balances()
        backup = await transfer(
            journal,
            source_account=source,
            destination=target,
            amount=debit,
            destination_amount=credit,
            fee=str(fee),
            fee_currency=currency,
        )
        transaction = journal.ledger.transactions[-1]
        details = journal.transfer_details(transaction)
        principal = Decimal(debit) - (fee if fee_side == "source" else 0)
        gross = Decimal(credit) + (fee if fee_side == "destination" else 0)
        assert details.debited == Decimal(debit)
        assert details.target.amount == Decimal(credit)
        assert details.converted == principal and details.received_principal == gross
        assert details.rate.is_finite() and details.rate > 0
        assert transaction.postings[0].total_price == gross
        assert transaction.postings[0].price_currency == currencies[target]
        assert journal.ledger.balances()[source] == balances[source] - Decimal(debit)
        assert journal.ledger.balances()[target] == balances[target] + Decimal(credit)
        assert journal.ledger.period_totals(date(2026, 1, 1), date(2026, 2, 1)) == {
            "income": {},
            "expenses": {currency: fee} if fee else {},
        }
        assert (tmp_path / str(backup)).read_text() == before
        entries, errors, _ = loader.load_string(journal.source)
        assert not errors, [e.message for e in errors]
        official, _ = summarize.balance_by_account(entries)
        for account in journal.ledger.accounts:
            units = Decimal("0")
            for position in official.get(account.id, ()):
                if position.units.currency == account.currency:
                    assert isinstance(position.units.number, Decimal)
                    units += position.units.number
            assert units == journal.ledger.balances()[account.id]
        fava = FavaLedger(str(tmp_path / FILENAME))
        assert not list(fava.errors)
        await journal.load()
        assert journal.transaction_for_edit(transaction.id) == transaction
        assert journal.transfer_details(transaction) == details

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0"},
        {"amount": "-1"},
        {"amount": "NaN"},
        {"amount": "1.001"},
        {"destination_amount": "0"},
        {"destination_amount": "-1"},
        {"destination_amount": "Infinity"},
        {"destination_amount": "1.001"},
        {"fee": "-1"},
        {"fee": "100", "fee_currency": "USD"},
        {"fee": "101", "fee_currency": "USD"},
        {"fee": "1", "fee_currency": "EUR"},
        {"fee": "1"},  # Nonzero fees require an explicit currency.
        {"fee": "1", "fee_currency": "UAH", "destination_amount": "999999999999.99"},
        {"destination": "Assets:Missing"},
        {"destination": "Assets:Dollar"},
        {"source_account": "Assets:Missing"},
    ],
)
def test_exchange_rejects_invalid_without_writes(tmp_path: Path, changes: dict[str, str]) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        original = journal.source
        backups = sorted((tmp_path / "backups").iterdir())
        values = dict(
            source_account="Assets:Dollar",
            amount="100",
            destination="Assets:Bank",
            destination_amount="4200",
            fee="0",
        )
        values.update(changes)
        with pytest.raises(LedgerError):
            await transfer(journal, **values)
        assert journal.source == original == (tmp_path / FILENAME).read_text()
        assert sorted((tmp_path / "backups").iterdir()) == backups
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}

    asyncio.run(scenario())


def test_exchange_edit_accounts_fee_sides_same_currency_and_delete(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        await transfer(
            journal,
            source_account="Assets:Dollar",
            amount="105",
            destination="Assets:Bank",
            destination_amount="4200",
            fee="5",
            fee_currency="USD",
        )
        original = journal.ledger.transactions[-1]
        text = journal.source.replace(
            f'  time: "{transaction_time(original.timestamp)}"',
            f'  time: "{transaction_time(original.timestamp)}"\n'
            '  textmoney-kind: "transfer"\n'
            '  receipt: "keep" ; annotation',
        )
        text = text.replace(
            "Assets:Dollar -5.00 USD",
            'Assets:Dollar -5.00 USD ; debit note\n        reference: "fee debit"',
        )
        text = text.replace(
            "Expenses:Bank-Fees 5.00 USD",
            'Expenses:Bank-Fees 5.00 USD ; fee note\n        reference: "expense"',
        )
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        await transfer(
            journal,
            identifier=original.id,
            source_account="Assets:Euro",
            destination="Assets:Pound",
            amount="100",
            destination_amount="80",
            fee="1",
            fee_currency="GBP",
            occurred="2026-01-11",
            fee_category="Expenses:Fees:Other",
        )
        updated = journal.transaction_for_edit(original.id)
        details = journal.transfer_details(updated)
        assert details.debited == 100 and details.received_principal == 81
        assert updated.timestamp == "2026-01-11T12:00:00" and updated.date == date(2026, 1, 11)
        assert all(
            word in journal.source
            for word in ("annotation", "debit note", "fee note", "fee debit", "expense")
        )
        await transfer(
            journal,
            identifier=original.id,
            source_account="Assets:Dollar",
            destination="Assets:Bank",
            amount="105",
            destination_amount="4200",
            fee="5",
            fee_currency="USD",
        )
        details = journal.transfer_details(journal.transaction_for_edit(original.id))
        assert details.converted == 100 and details.rate == 42
        await transfer(journal, identifier=original.id, fee="0")
        assert all(
            p.total_price is None for p in journal.transaction_for_edit(original.id).postings
        )
        assert not loader.load_string(journal.source)[1]
        assert "fee debit" in journal.source
        await transfer(journal, identifier=original.id, fee="2")
        assert journal.transfer_details(journal.transaction_for_edit(original.id)).fee == 2
        await journal.delete_transaction(original.id, journal.source)
        assert journal.ledger.balances()["Assets:Dollar"] == 1000
        assert journal.ledger.balances()["Assets:Bank"] == 2000
        assert journal.ledger.period_totals(date(2026, 1, 1), date(2026, 2, 1)) == {
            "income": {},
            "expenses": {},
        }

    asyncio.run(scenario())


def test_price_validation_does_not_accept_arbitrary_fx_constructs(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        await transfer(
            journal,
            source_account="Assets:Dollar",
            amount="100",
            destination="Assets:Bank",
            destination_amount="4200",
        )
        transaction = journal.ledger.transactions[-1]
        source = transaction.postings[0]
        for invalid in (
            replace(source, total_price=Decimal("4199")),
            replace(source, total_price=Decimal("-4200")),
            replace(source, price_currency="EUR"),
            replace(source, price_currency=None),
        ):
            candidate = replace(transaction, postings=(invalid, *transaction.postings[1:]))
            ledger = replace(journal.ledger, transactions=(candidate,))
            with pytest.raises(LedgerError):
                ledger.balances()
        assert journal.codec is not None
        for text in (
            journal.source.replace("@@ 4200", "@ 41"),
            journal.source.replace("-100.00 USD @@", "-100.00 USD {1 UAH} @@"),
            journal.source.replace("@@ 4200", "@@"),
        ):
            with pytest.raises(LedgerError):
                journal.codec.parse(text)
        text = journal.source.replace(
            f'  time: "{transaction_time(transaction.timestamp)}"',
            f'  time: "{transaction_time(transaction.timestamp)}"\n  textmoney-kind: "expense"',
        )
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        assert journal.transaction_for_edit(transaction.id).kind == "transfer"

    asyncio.run(scenario())


def test_exchange_rate_excludes_fees_and_keeps_repeating_precision() -> None:
    assert exchange_values(Decimal("105"), Decimal("4200"), Decimal("5"), "USD", "UAH", "USD") == (
        100,
        4200,
        42,
    )
    assert exchange_values(Decimal("100"), Decimal("4195"), Decimal("5"), "USD", "UAH", "UAH") == (
        100,
        4200,
        42,
    )
    principal, gross, rate = exchange_values(
        Decimal("3"), Decimal("1"), Decimal("0"), "USD", "UAH", "USD"
    )
    assert principal == 3 and gross == 1
    assert str(rate) == "0." + "3" * 50


@pytest.mark.parametrize("failure", ["stale", "conflict", "backup", "write", "inactive"])
def test_exchange_reuses_failure_guards(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        if failure == "inactive":
            await journal.edit_account("Assets:Dollar", "Dollar", None, False, journal.source)
        original = journal.source
        expected = original
        changes = {}
        error = StorageError
        if failure == "stale":
            changes["expected_source"] = "outdated"
            error = ConflictError
        elif failure == "conflict":
            expected += "; external edit\n"
            (tmp_path / FILENAME).write_text(expected)
            error = ConflictError
        elif failure == "backup":
            journal.storage.folder.backup = AsyncMock(
                side_effect=StorageError("Synthetic backup failure")
            )
        elif failure == "write":
            journal.storage.folder.write = AsyncMock(
                side_effect=StorageError("Synthetic write failure")
            )
        else:
            error = LedgerError
        with pytest.raises(error):
            await transfer(
                journal,
                source_account="Assets:Dollar",
                destination="Assets:Bank",
                amount="105",
                destination_amount="4200",
                fee="5",
                fee_currency="USD",
                **changes,
            )
        assert journal.source == original
        assert (tmp_path / FILENAME).read_text() == expected
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}

    asyncio.run(scenario())


def test_annotated_source_fee_retains_its_role_without_expense_annotation(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        await transfer(
            journal,
            source_account="Assets:Dollar",
            amount="105",
            destination="Assets:Bank",
            destination_amount="4200",
            fee="5",
            fee_currency="USD",
        )
        original = journal.ledger.transactions[-1]
        text = journal.source.replace(
            "Assets:Dollar -5.00 USD", 'Assets:Dollar -5.00 USD\n      role: "source-fee"'
        )
        assert "source-fee" in text
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        for fee in ("0", "5"):
            await transfer(
                journal,
                identifier=original.id,
                source_account="Assets:Dollar",
                amount="105",
                destination="Assets:Bank",
                destination_amount="4200",
                fee=fee,
                fee_currency="USD",
            )
        assert journal.codec is not None
        document = journal.codec.document(journal.source)
        entry = journal._transaction(document, original.id)
        marked = [p for p in entry.postings if isinstance(p, models.Posting) and p.meta.get("role")]
        assert len(marked) == 1
        assert marked[0].account == "Assets:Dollar" and marked[0].number == -5

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_exchange_unified_form_preview_draft_and_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
) -> None:
    async def scenario() -> None:
        journal = await exchange_seeded(tmp_path)
        view = FinanceView()
        view.journal, view.language, view.timezone_name = journal, language, "UTC"
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        account, amount, description, when = form.account, form.amount, form.description, form.when
        destination, credited, enabled, fee, deduction, currency, category, preview = (
            form.second_account,
            form.second_amount,
            form.fee_enabled,
            form.fee,
            form.fee_mode,
            form.fee_currency,
            form.fee_category,
            form.preview,
        )
        assert isinstance(destination, ft.Dropdown)
        assert isinstance(credited, ft.TextField)
        assert isinstance(enabled, ft.Checkbox)
        assert isinstance(fee, ft.TextField)
        assert isinstance(deduction, ft.Dropdown)
        assert isinstance(currency, ft.Dropdown)
        assert isinstance(category, ft.Dropdown)
        assert isinstance(preview, ft.Text)
        account.value, destination.value = "Assets:Dollar", "Assets:Bank"
        form.sign.selected = ["transfer"]
        amount.value, credited.value = "105.00", "4200.00"
        change(destination)
        assert amount.label == f"{translate(language, 'exchange_debited')} (USD)"
        assert credited.label == f"{translate(language, 'exchange_credited')} (UAH)"
        assert credited.visible and not deduction.visible
        enabled.value, fee.value, currency.value = True, "5", "USD"
        assert enabled.on_change is not None
        cast(Callable[[ft.Event[ft.Checkbox]], None], enabled.on_change)(MagicMock())
        assert "100" in (preview.value or "") and "42 UAH" in (preview.value or "")
        assert not deduction.visible and currency.visible
        currency.value = "UAH"
        assert currency.on_select is not None
        cast(Callable[[ft.Event[ft.Dropdown]], None], currency.on_select)(MagicMock())
        assert amount.value == "105.00" and credited.value == "4200.00"
        currency.value, amount.value, credited.value = "USD", "3.00", "1.00"
        description.value = "Unsaved exchange draft"
        when.occurred.value = "01/10/2026" if language == "en" else "10.01.2026"
        when.clock.value = "12:00"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is not None
        assert description.value == "Unsaved exchange draft"
        assert amount.value == "3.00" and credited.value == "1.00"
        amount.value, credited.value = "105.00", "4200.00"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        original = journal.ledger.transactions[-1]
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        destination, credited, enabled, fee, deduction, currency, category, preview = (
            form.second_account,
            form.second_amount,
            form.fee_enabled,
            form.fee,
            form.fee_mode,
            form.fee_currency,
            form.fee_category,
            form.preview,
        )
        assert isinstance(credited, ft.TextField) and isinstance(currency, ft.Dropdown)
        assert isinstance(fee, ft.TextField) and isinstance(deduction, ft.Dropdown)
        assert credited.value == "4200.00" and form.amount.value == "105.00"
        assert fee.value == "5.00" and currency.value == "USD"
        assert not deduction.visible
        form.amount.value, credited.value, currency.value = "100", "4195", "UAH"
        await dialog.actions[-1].on_click(MagicMock())
        details = journal.transfer_details(journal.transaction_for_edit(original.id))
        assert details.debited == 100 and details.target.amount == 4195
        assert details.rate == 42 and details.fee_currency == "UAH"
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())
