import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest
from form_controls import change, transaction_form

from app.domain.ledger import (
    BANK_FEES,
    EPOCH,
    Ledger,
    LedgerError,
    Transaction,
    draft_movements,
)
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import format_amount
from app.ui.finance import FinanceView

TODAY = date(2026, 3, 15)
STAMP = datetime(2026, 3, 10, 12)


async def seeded(path: Path) -> Journal:
    journal = Journal(DesktopFolder(str(path), FILENAME), today=lambda: TODAY)
    await journal.load()
    await journal.create_account("Bank", "Bank", "UAH", "1000", opened=EPOCH, opening_date=EPOCH)
    await journal.create_account("Cash", "Cash", "UAH", "50", opened=EPOCH, opening_date=EPOCH)
    await journal.create_account("Euro", "Euro", "EUR", "20", opened=EPOCH, opening_date=EPOCH)
    await journal.create_account(
        "Card", "Card", "UAH", "300", root="Liabilities", opened=EPOCH, opening_date=EPOCH
    )
    await journal.create_category("Food", "Food", opened=EPOCH)
    await journal.create_category("Bank-Fees", "Bank fees", opened=EPOCH)
    await journal.create_category("Salary", "Salary", root="Income", opened=EPOCH)
    return journal


def account_changes(ledger: Ledger, transaction: Transaction) -> dict[str, Decimal]:
    accounts = {account.id for account in ledger.accounts}
    result: dict[str, Decimal] = {}
    for posting in transaction.postings:
        if posting.account in accounts:
            result[posting.account] = result.get(posting.account, Decimal("0")) + posting.amount
    return result


@pytest.mark.parametrize(
    ("kind", "fee", "mode"),
    [
        ("expense", "0", "added"),
        ("expense", "3", "added"),
        ("expense", "3", "deducted"),
        ("income", "2", "added"),
        ("income", "2", "deducted"),
        ("reimbursement", "0", "added"),
        ("transfer", "4", "added"),
        ("transfer", "4", "deducted"),
        ("exchange", "1", "source"),
        ("exchange", "1", "target"),
    ],
)
def test_draft_movements_match_ledger_builders(
    tmp_path: Path, kind: str, fee: str, mode: str
) -> None:
    ledger = asyncio.run(seeded(tmp_path)).ledger
    amount, commission = Decimal("100"), Decimal(fee)
    args = (TODAY, STAMP, "draft")
    if kind == "expense":
        built = ledger.expense(
            "Assets:Bank",
            "Expenses:Food",
            amount,
            "",
            *args,
            today=TODAY,
            fee=commission,
            fee_mode=mode,
        )
        draft = draft_movements(kind, "Assets:Bank", amount, fee=commission, fee_mode=mode)
    elif kind in {"income", "reimbursement"}:
        category = "Income:Salary" if kind == "income" else "Expenses:Food"
        built = ledger.incoming(
            "Assets:Bank",
            category,
            amount,
            "",
            *args,
            kind,
            today=TODAY,
            fee=commission,
            fee_mode=mode,
        )
        draft = draft_movements(kind, "Assets:Bank", amount, fee=commission, fee_mode=mode)
    elif kind == "transfer":
        built = ledger.transfer(
            "Assets:Bank",
            "Liabilities:Card",
            amount,
            commission,
            BANK_FEES,
            mode,
            *args,
            today=TODAY,
        )
        draft = draft_movements(
            kind,
            "Assets:Bank",
            amount,
            fee=commission,
            fee_mode=mode,
            destination="Liabilities:Card",
        )
    else:
        fee_currency = "UAH" if mode == "source" else "EUR"
        built = ledger.transfer(
            "Assets:Bank",
            "Assets:Euro",
            amount,
            commission,
            BANK_FEES,
            "added",
            *args,
            today=TODAY,
            destination_amount=Decimal("2"),
            fee_currency=fee_currency,
        )
        draft = draft_movements(
            "transfer",
            "Assets:Bank",
            amount,
            fee=commission,
            destination="Assets:Euro",
            destination_amount=Decimal("2"),
            currencies=("UAH", "EUR", fee_currency),
        )
    assert draft == account_changes(ledger, built)


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        ("expense", {"fee": Decimal("5"), "fee_mode": "deducted", "amount": Decimal("5")}),
        ("reimbursement", {"fee": Decimal("1")}),
        ("transfer", {}),
        ("transfer", {"destination": "Assets:Bank"}),
        ("transfer", {"destination": "Assets:Euro", "currencies": ("UAH", "EUR", "UAH")}),
        ("unknown", {"destination": "Assets:Cash"}),
    ],
)
def test_draft_movements_reject_drafts_the_builders_reject(
    kind: str, changes: dict[str, object]
) -> None:
    values: dict[str, object] = {"amount": Decimal("10")} | changes
    amount = values.pop("amount")
    assert isinstance(amount, Decimal)
    with pytest.raises(LedgerError):
        draft_movements(kind, "Assets:Bank", amount, **values)  # type: ignore[arg-type]


def open_form(
    journal: Journal, monkeypatch: pytest.MonkeyPatch, transaction_id: str | None = None
) -> tuple[FinanceView, MagicMock]:
    view = FinanceView()
    view.journal, view.language, view.timezone_name = journal, "en", "UTC"
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    if transaction_id is None:
        view._new_transaction(MagicMock())
    else:
        event = MagicMock()
        event.control.data = transaction_id
        view._edit_transaction(event)
    return view, page


def money(value: str, currency: str = "UAH") -> str:
    return format_amount(Decimal(value), "en", currency)


def test_form_shows_balance_now_and_after_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = asyncio.run(seeded(tmp_path))
    view, page = open_form(journal, monkeypatch)
    form = transaction_form(page.show_dialog.call_args.args[0])
    form.account.value = "Assets:Cash"
    change(form.account)
    assert form.account.helper_text == view.tr("form_balance_now", now=money("50"))

    form.amount.value = "20"
    change(form.amount)
    assert form.account.helper_text == view.tr("form_balance", now=money("50"), after=money("30"))
    assert form.account.helper_style is None

    form.fee_enabled.value, form.fee.value = True, "40"
    change(form.fee_enabled)
    assert form.account.helper_text == view.tr("form_balance", now=money("50"), after=money("-10"))
    assert form.account.helper_style == ft.TextStyle(color=ft.Colors.ERROR)

    form.amount.value = "1.234"
    change(form.amount)
    assert form.account.helper_text == view.tr("form_balance_now", now=money("50"))
    assert form.account.helper_style is None
    assert form.second_account.helper_text is None


def test_form_transfer_shows_both_balances_and_liability_is_not_an_overdraft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = asyncio.run(seeded(tmp_path))
    view, page = open_form(journal, monkeypatch)
    form = transaction_form(page.show_dialog.call_args.args[0])
    form.sign.selected = ["transfer"]
    form.account.value, form.second_account.value = "Liabilities:Card", "Assets:Euro"
    change(form.sign)
    form.amount.value, form.second_amount.value = "200", "4"
    change(form.second_amount)
    assert form.account.helper_text == view.tr(
        "form_balance", now=money("-300"), after=money("-500")
    )
    assert form.account.helper_style is None
    assert form.second_account.helper_text == view.tr(
        "form_balance", now=money("20", "EUR"), after=money("24", "EUR")
    )


def test_form_edit_excludes_the_original_transaction_from_after_balance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def prepare() -> tuple[Journal, str]:
        journal = await seeded(tmp_path)
        await journal.add_expense("Assets:Bank", "Expenses:Food", "100", "", "2026-03-10")
        return journal, journal.ledger.transactions[-1].id

    journal, identifier = asyncio.run(prepare())
    view, page = open_form(journal, monkeypatch, identifier)
    form = transaction_form(page.show_dialog.call_args.args[0])
    assert form.account.helper_text == view.tr("form_balance", now=money("900"), after=money("900"))
    form.amount.value = "150"
    change(form.amount)
    assert form.account.helper_text == view.tr("form_balance", now=money("900"), after=money("850"))
    form.account.value = "Assets:Cash"
    change(form.account)
    assert form.account.helper_text == view.tr("form_balance", now=money("50"), after=money("-100"))


def test_operations_totals_sum_accounts_per_currency_including_liabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(FinanceView, "_today", lambda _: TODAY)
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.language = "en"
        (tmp_path / "empty").mkdir()
        empty = Journal(DesktopFolder(str(tmp_path / "empty"), FILENAME), today=lambda: TODAY)
        await empty.load()
        view.journal = empty
        view._refresh()
        assert not view.operations_totals.visible
        view.journal = await seeded(tmp_path)
        view._build_screens()
        view._refresh()
        assert view.transactions_screen.controls[0] is view.operations_totals
        assert not any(
            isinstance(control, ft.Text) for control in view.transactions_screen.controls
        )
        assert view.operations_totals.visible
        strip = view.operations_totals.content
        assert isinstance(strip, ft.Row) and not strip.wrap and strip.scroll
        assert not view.operations_totals_values.wrap
        assert view.operations_totals_label.value == view.tr("operations_totals")
        values = [
            control.value
            for control in view.operations_totals_values.controls
            if isinstance(control, ft.Text)
        ]
        assert values == [money("750"), money("20", "EUR")]
        view.journal = None
        view._refresh()
        assert not view.operations_totals.visible
        assert not view.operations_totals_values.controls

    asyncio.run(scenario())


def test_overview_balances_follow_period_and_survive_empty_periods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(FinanceView, "_today", lambda _: TODAY)
        page = MagicMock(spec=ft.Page)
        page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        journal = await seeded(tmp_path)
        await journal.add_expense("Assets:Bank", "Expenses:Food", "100", "", "2026-03-10")
        view = FinanceView()
        view.language = "en"
        view.journal = journal
        view._render_reports()
        overview = view.report_overview.controls
        assert overview.index(view.report_summary) < overview.index(view.report_categories)
        last = overview[-1]
        assert isinstance(last, ft.Container) and last.content is view.report_balances
        title, uah, eur = view.report_balances.controls
        assert isinstance(title, ft.Text) and title.value == view.tr("report_balances")

        def rows(card: ft.Control) -> list[tuple[str | None, str | None, str | None]]:
            assert isinstance(card, ft.Container) and isinstance(card.content, ft.Column)
            result = []
            for item in card.content.controls[1:]:
                assert isinstance(item, ft.Column)
                row, detail = item.controls
                assert isinstance(row, ft.Row) and isinstance(detail, ft.Text)
                name, closing = row.controls
                assert isinstance(name, ft.Text) and isinstance(closing, ft.Text)
                result.append((name.value, detail.value, closing.value))
            return result

        march = rows(uah)
        assert march[0] == (
            "Bank",
            view.tr("report_balance_detail", start=money("1000"), change=money("-100")),
            money("900"),
        )
        assert [item[0] for item in march] == ["Bank", "Card", "Cash"]
        assert rows(eur)[0][1] == view.tr(
            "report_balance_detail", start=money("20", "EUR"), change=money("0", "EUR")
        )

        view._report_previous_month(MagicMock())
        empty_state = view.report_summary.controls[0]
        assert isinstance(empty_state, ft.Text)
        assert empty_state.value == view.tr("report_empty_period")
        _, february, _ = view.report_balances.controls
        assert rows(february)[0] == (
            "Bank",
            view.tr("report_balance_detail", start=money("1000"), change=money("0")),
            money("1000"),
        )

        await journal.add_incoming("Assets:Cash", "Income:Salary", "5", "", "2026-03-11", "income")
        view._report_next_month(MagicMock())
        _, march_uah, _ = view.report_balances.controls
        assert rows(march_uah)[2][1] == view.tr(
            "report_balance_detail", start=money("50"), change="+" + money("5")
        )

        view.report_period.value = "custom"
        view.report_start.occurred.value = "03/20/2026"
        view.report_end.occurred.value = "03/01/2026"
        view._render_reports()
        assert view.report_error.visible and not view.report_balances.controls

    asyncio.run(scenario())
