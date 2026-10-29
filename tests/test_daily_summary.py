import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest
from form_controls import click

from app.domain.ledger import EPOCH, Account, Category, Ledger, Posting, Transaction
from app.domain.reporting import currency_totals, period_report
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import format_amount, format_date
from app.ui.finance import FinanceView

DAY = date(2026, 1, 31)


def walk(control: ft.Control) -> Iterator[ft.Control]:
    yield control
    for name in ("content", "title"):
        child = getattr(control, name, None)
        if isinstance(child, ft.Control):
            yield from walk(child)
    for name in ("controls", "actions"):
        for child in getattr(control, name, None) or []:
            yield from walk(child)


def texts(control: ft.Control | str) -> list[str]:
    if isinstance(control, str):
        return [control]
    return [item.value or "" for item in walk(control) if isinstance(item, ft.Text)]


def shown_dialog(view: FinanceView) -> ft.AlertDialog:
    page = view.page
    assert isinstance(page, MagicMock)
    dialog = page.show_dialog.call_args.args[0]
    assert isinstance(dialog, ft.AlertDialog)
    return dialog


def transaction(
    identifier: str,
    postings: tuple[tuple[str, str, str], ...],
    *,
    day: date = DAY,
    kind: str = "expense",
) -> Transaction:
    return Transaction(
        identifier,
        day,
        f"{day}T23:30:00-10:00",
        "",
        identifier,
        tuple(
            Posting(account, Decimal(amount), currency) for account, amount, currency in postings
        ),
        kind,
    )


def daily_ledger() -> Ledger:
    return Ledger(
        accounts=tuple(
            Account(f"Assets:{code}", code, currency, EPOCH)
            for code, currency in (
                ("Bank", "UAH"),
                ("Cash", "UAH"),
                ("Euro", "EUR"),
                ("USD", "USD"),
            )
        ),
        equity_opened=EPOCH,
        adjustments_opened=EPOCH,
        categories=(
            Category("Expenses:Food", "Food", EPOCH),
            Category("Expenses:Food:Groceries", "Groceries", EPOCH, active=False),
            Category("Expenses:Bank-Fees", "Fees", EPOCH),
            Category("Income:Work", "Work", EPOCH),
            Category("Income:Work:Salary", "Salary", EPOCH),
            Category("Expenses:Legacy", "Legacy expense", EPOCH),
            Category("Income:Legacy", "Legacy income", EPOCH),
        ),
        transactions=(
            transaction(
                "purchase",
                (
                    ("Expenses:Food:Groceries", "40", "UAH"),
                    ("Expenses:Bank-Fees", "2", "UAH"),
                    ("Assets:Bank", "-42", "UAH"),
                ),
            ),
            transaction(
                "refund",
                (("Expenses:Food:Groceries", "-45", "UAH"), ("Assets:Bank", "45", "UAH")),
                kind="reimbursement",
            ),
            transaction(
                "parent-purchase",
                (("Expenses:Food", "6", "UAH"), ("Assets:Bank", "-6", "UAH")),
            ),
            transaction(
                "income",
                (
                    ("Income:Work:Salary", "-100", "UAH"),
                    ("Expenses:Bank-Fees", "3", "UAH"),
                    ("Assets:Bank", "97", "UAH"),
                ),
                kind="income",
            ),
            transaction(
                "transfer",
                (
                    ("Assets:Bank", "-11", "UAH"),
                    ("Assets:Cash", "10", "UAH"),
                    ("Expenses:Bank-Fees", "1", "UAH"),
                ),
                kind="transfer",
            ),
            *(
                transaction(
                    f"euro-income-{amount}",
                    (("Income:Work:Salary", f"-{amount}", "EUR"), ("Assets:Euro", amount, "EUR")),
                    kind="income",
                )
                for amount in ("0.10", "0.20")
            ),
            transaction(
                "euro-fee",
                (("Expenses:Bank-Fees", "0.10", "EUR"), ("Assets:Euro", "-0.10", "EUR")),
            ),
            transaction(
                "legacy",
                (
                    ("Income:Legacy", "-8", "USD"),
                    ("Expenses:Legacy", "2", "USD"),
                    ("Assets:USD", "6", "USD"),
                ),
                kind="income",
            ),
            transaction(
                "other-day",
                (("Expenses:Food", "999", "UAH"), ("Assets:Bank", "-999", "UAH")),
                day=date(2026, 1, 30),
            ),
            transaction(
                "opening",
                (("Assets:Cash", "1000", "UAH"), ("Equity:Opening-Balances", "-1000", "UAH")),
                day=EPOCH,
                kind="opening",
            ),
        ),
    )


@pytest.fixture
def view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FinanceView:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: DAY)
    view.journal.ledger = daily_ledger()
    view.journal.ready = True
    return view


def summaries(view: FinanceView) -> list[ft.TextButton]:
    return [
        item
        for item in walk(view.history)
        if isinstance(item, ft.TextButton) and isinstance(item.data, date)
    ]


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("width", [390, 1366])
def test_operations_show_separate_net_expense_and_income_for_each_day_only(
    view: FinanceView, language: str, width: int
) -> None:
    view.language = language
    view._apply_layout(width)
    view._refresh()
    buttons = summaries(view)
    assert [button.data for button in buttons] == [DAY, date(2026, 1, 30), EPOCH]
    assert buttons[0].expand
    assert view.tr("daily_summary") in texts(buttons[0])
    for currency, expenses, income in (
        ("EUR", "0.10", "0.30"),
        ("UAH", "7", "100"),
        ("USD", "2", "8"),
    ):
        assert (
            f"{view.tr('report_expenses')}: {format_amount(Decimal(expenses), language, currency)}"
            in texts(buttons[0])
        )
        assert (
            f"{view.tr('report_income')}: {format_amount(Decimal(income), language, currency)}"
            in texts(buttons[0])
        )
    assert view.tr("daily_no_cashflow") in texts(buttons[-1])
    for listing in (view.home_activity, ft.Column(view._transaction_groups([], {}))):
        assert not any(isinstance(item.data, date) for item in walk(listing))
    assert view.journal is not None
    read_only = ft.Column(
        view._transaction_groups(list(view.journal.ledger.transactions), {}, read_only=True)
    )
    assert view.tr("daily_summary") not in texts(read_only)
    for index, control in enumerate(view.history.controls):
        if isinstance(control, ft.Container) and isinstance(control.content, ft.Text):
            summary = view.history.controls[index + 1]
            assert isinstance(summary, ft.Container) and summary.border is None
            assert view.tr("daily_summary") in texts(summary)


@pytest.mark.parametrize("width", [390, 1366])
def test_day_popup_reuses_report_hierarchy_and_preserves_journal(
    view: FinanceView, tmp_path: Path, width: int
) -> None:
    assert view.journal is not None
    before = view.journal.ledger, view.journal.source
    view._apply_layout(width)
    view._refresh()
    for button in summaries(view):
        click(button)
        dialog = shown_dialog(view)
        assert isinstance(dialog, ft.AlertDialog) and dialog.scrollable
        label = (
            view.tr("initial_date")
            if button.data == EPOCH
            else format_date(button.data, view.language)
        )
        assert dialog.title == view.tr("daily_report", day=label)
        assert len(dialog.actions) == 1
        close = dialog.actions[0]
        assert isinstance(close, ft.TextButton)
        assert close.content == view.tr("daily_report_close")
        if button.data != DAY:
            assert "Groceries" not in texts(dialog)
            continue
        assert isinstance(dialog.content, ft.Container) and dialog.content.theme is not None
        assert view.tr("daily_report_help") in texts(dialog)
        assert view.tr("crud_inactive_label") in texts(dialog)
        assert view.tr("report_expense_categories") in texts(dialog)
        assert view.tr("report_income_categories") in texts(dialog)
        food = next(
            item
            for item in walk(dialog)
            if isinstance(item, ft.ExpansionTile) and "Food" in texts(item.title)
        )
        assert isinstance(food.title, ft.Column if width == 390 else ft.Row)
        if isinstance(food.title, ft.Column):
            name, values = food.title.controls
            assert isinstance(name, ft.Text) and not name.expand
            assert isinstance(values, ft.Row) and values.wrap
        assert format_amount(Decimal("1"), view.language, "UAH") in texts(food.title)
        assert food.controls
        assert format_amount(Decimal("-5"), view.language, "UAH") in texts(food.controls[0])
        assert food.controls_padding == ft.Padding.only(left=16)
        work = next(
            item
            for item in walk(dialog)
            if isinstance(item, ft.ExpansionTile)
            and "Work" in texts(item.title)
            and format_amount(Decimal("100"), view.language, "UAH") in texts(item.title)
        )
        assert "Salary" in texts(work)
        click(close)
    assert isinstance(view.page, MagicMock)
    assert view.page.pop_dialog.call_count == 1
    assert (view.journal.ledger, view.journal.source) == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("width", [390, 1366])
def test_day_popup_retains_unassigned_expenses_and_income(view: FinanceView, width: int) -> None:
    assert view.journal is not None
    view.journal.ledger = replace(view.journal.ledger, categories=())
    view._apply_layout(width)
    view._daily_report(view.journal, DAY)
    dialog = shown_dialog(view)
    assert texts(dialog).count(view.tr("report_uncategorized")) == 6
    for currency, expenses, income in (
        ("EUR", "0.10", "0.30"),
        ("UAH", "7", "100"),
        ("USD", "2", "8"),
    ):
        assert format_amount(Decimal(expenses), view.language, currency) in texts(dialog)
        assert format_amount(Decimal(income), view.language, currency) in texts(dialog)


@pytest.mark.parametrize("state", ["switched", "removed", "unready", "unexpected"])
def test_day_popup_reports_stale_unavailable_and_calculation_errors(
    view: FinanceView, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    view._refresh()
    button = summaries(view)[0]
    assert view.journal is not None
    if state == "switched":
        view.journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
    elif state == "removed":
        view.journal = None
    elif state == "unready":
        view.journal.ready = False
    else:

        def fail(*args: object) -> None:
            raise RuntimeError("Synthetic report failure")

        monkeypatch.setattr("app.ui.finance.period_report", fail)
    click(button)
    dialog = shown_dialog(view)
    assert view.tr("daily_summary") not in texts(dialog)
    assert view.tr("daily_report_help") not in texts(dialog)
    assert view.message.value
    if state == "unready":
        assert view.tr("report_unavailable") == view.message.value
    elif state == "unexpected":
        assert view.tr("error_unexpected") == view.message.value
    else:
        assert view.tr("crud_stale") == view.message.value
    assert list(tmp_path.iterdir()) == []


def test_summaries_only_aggregate_each_days_entries_and_build_detail_lazily(
    view: FinanceView, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert view.journal is not None
    ledger = view.journal.ledger
    counts: list[int] = []
    original = Ledger.period_totals

    def totals(self: Ledger, start: date, end: date) -> dict[str, dict[str, Decimal]]:
        assert start == end
        assert all(item.date == start for item in self.transactions)
        counts.append(len(self.transactions))
        return original(self, start, end)

    monkeypatch.setattr(Ledger, "period_totals", totals)
    details = MagicMock()
    monkeypatch.setattr("app.ui.finance.period_report", details)
    view._transaction_groups(
        sorted(ledger.transactions, key=view._transaction_sort_key, reverse=True),
        {},
        daily_summary=True,
    )
    assert sum(counts) == len(ledger.transactions)
    assert len(counts) == 3
    details.assert_not_called()


@pytest.mark.parametrize("kind", ["transfer", "opening", "adjustment"])
def test_principal_only_day_has_no_expenses_or_income(view: FinanceView, kind: str) -> None:
    assert view.journal is not None
    counterpart = {
        "transfer": "Assets:Bank",
        "opening": "Equity:Opening-Balances",
        "adjustment": "Equity:Balance-Adjustments",
    }[kind]
    ledger = replace(
        view.journal.ledger,
        transactions=(
            transaction(
                "principal",
                (("Assets:Cash", "10", "UAH"), (counterpart, "-10", "UAH")),
                kind=kind,
            ),
        ),
    )
    assert currency_totals(ledger, DAY, DAY) == ()
    view.journal.ledger = ledger
    view._refresh()
    assert view.tr("daily_no_cashflow") in texts(summaries(view)[0])
    click(summaries(view)[0])
    assert view.tr("daily_no_cashflow") in texts(shown_dialog(view))


@pytest.mark.parametrize(("income", "expense"), [("0.30", "0"), ("0", "-5"), ("0", "0")])
def test_zero_negative_and_income_only_day_matches_full_report(
    view: FinanceView, income: str, expense: str
) -> None:
    assert view.journal is not None
    transactions = (
        (
            transaction(
                "receipt",
                (("Income:Work", f"-{income}", "EUR"), ("Assets:Euro", income, "EUR")),
                kind="income",
            ),
        )
        if Decimal(income)
        else (
            transaction(
                "refund",
                (("Expenses:Food", expense, "EUR"), ("Assets:Euro", str(-Decimal(expense)), "EUR")),
                kind="reimbursement",
            ),
        )
        if Decimal(expense)
        else (
            transaction("purchase", (("Expenses:Food", "5", "EUR"), ("Assets:Euro", "-5", "EUR"))),
            transaction(
                "refund",
                (("Expenses:Food", "-5", "EUR"), ("Assets:Euro", "5", "EUR")),
                kind="reimbursement",
            ),
        )
    )
    ledger = replace(
        view.journal.ledger,
        transactions=transactions,
    )
    assert currency_totals(ledger, DAY, DAY) == period_report(ledger, DAY, DAY).currencies
    view.journal.ledger = ledger
    view._refresh()
    assert f"Income: {format_amount(Decimal(income), 'en', 'EUR')}" in texts(summaries(view)[0])
    assert f"Expenses: {format_amount(Decimal(expense), 'en', 'EUR')}" in texts(summaries(view)[0])


def test_click_uses_latest_ledger_and_refresh_updates_edits_deletes_and_empty_state(
    view: FinanceView,
) -> None:
    assert view.journal is not None
    view._refresh()
    button = summaries(view)[0]
    ledger = view.journal.ledger
    view.journal.ledger = replace(
        ledger, transactions=tuple(item for item in ledger.transactions if item.id != "income")
    )
    click(button)
    assert f"Income: {format_amount(Decimal('0'), 'en', 'UAH')}" in texts(shown_dialog(view))
    view._refresh()
    assert f"Income: {format_amount(Decimal('0'), 'en', 'UAH')}" in texts(summaries(view)[0])
    view.journal.ledger = replace(ledger, transactions=())
    view._refresh()
    assert not summaries(view)
    assert texts(view.history) == [view.tr("transactions_empty")]


def test_external_reload_refreshes_summary_and_does_not_create_backup(
    view: FinanceView, tmp_path: Path
) -> None:
    async def scenario() -> None:
        assert view.journal is not None
        source = (
            "2000-01-01 open Assets:Cash UAH\n"
            "2000-01-01 open Expenses:Food\n"
            '  name: "Food"\n'
            '2026-01-31 * "Synthetic"\n'
            "  Assets:Cash -2 UAH\n"
            "  Expenses:Food 2 UAH\n"
        )
        path = tmp_path / FILENAME
        path.write_text(source)
        await view.journal.load()
        view._refresh()
        assert f"Expenses: {format_amount(Decimal('2'), 'en', 'UAH')}" in texts(summaries(view)[0])
        updated = source.replace("2 UAH", "3 UAH")
        path.write_text(updated)
        await view._poll_journal()
        assert f"Expenses: {format_amount(Decimal('3'), 'en', 'UAH')}" in texts(summaries(view)[0])
        assert path.read_text() == updated
        assert sorted(item.name for item in tmp_path.iterdir()) == [FILENAME]

    asyncio.run(scenario())
