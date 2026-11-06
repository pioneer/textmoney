import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from app.domain.ledger import (
    EPOCH,
    ZERO,
    Account,
    Category,
    Ledger,
    LedgerError,
    Posting,
    Transaction,
)
from app.domain.reporting import account_balances, period_bounds, period_report, shift_month
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import date_input
from app.ui.finance import FinanceView


def transaction(
    identifier: str,
    occurred: date,
    *postings: Posting,
    kind: str = "expense",
) -> Transaction:
    return Transaction(
        identifier,
        occurred,
        f"{occurred.isoformat()}T12:00:00+00:00",
        "",
        identifier,
        tuple(postings),
        kind,
    )


def posting(account: str, amount: str, currency: str = "UAH") -> Posting:
    return Posting(account, Decimal(amount), currency)


def report_ledger() -> Ledger:
    categories = (
        Category("Expenses:Food", "Food", EPOCH),
        Category("Expenses:Food:Groceries", "Groceries", EPOCH, active=False),
        Category("Expenses:Bank-Fees", "Bank fees", EPOCH),
        Category("Income:Salary", "Salary", EPOCH),
        Category("Income:Contract", "Contract work", EPOCH),
    )
    transactions = (
        transaction(
            "income-start",
            date(2026, 1, 1),
            posting("Assets:Bank", "100"),
            posting("Income:Salary", "-100"),
            kind="income",
        ),
        transaction(
            "expense-end",
            date(2026, 1, 31),
            posting("Expenses:Food:Groceries", "30"),
            posting("Assets:Bank", "-30"),
        ),
        transaction(
            "partial-reimbursement",
            date(2026, 1, 31),
            posting("Expenses:Food:Groceries", "-10"),
            posting("Assets:Cash", "10"),
            kind="reimbursement",
        ),
        transaction(
            "excess-reimbursement",
            date(2026, 1, 31),
            posting("Expenses:Food:Groceries", "-25"),
            posting("Assets:Cash", "25"),
            kind="reimbursement",
        ),
        transaction(
            "same-currency-transfer-fee",
            date(2026, 1, 15),
            posting("Assets:Bank", "-102"),
            posting("Assets:Cash", "100"),
            posting("Expenses:Bank-Fees", "2"),
            kind="transfer",
        ),
        transaction(
            "cross-currency-transfer-and-fee",
            date(2026, 1, 20),
            posting("Assets:Bank", "-10"),
            posting("Assets:Euro", "5", "EUR"),
            posting("Expenses:Bank-Fees", "0.50", "EUR"),
            kind="transfer",
        ),
        transaction(
            "loan-principal",
            date(2026, 1, 22),
            posting("Assets:Bank", "-20"),
            posting("Liabilities:Loan", "20"),
            kind="transfer",
        ),
        transaction(
            "opening-balance",
            date(2026, 1, 1),
            posting("Assets:Cash", "50"),
            posting("Equity:Opening-Balances", "-50"),
            kind="opening",
        ),
        transaction(
            "balance-adjustment",
            date(2026, 1, 31),
            posting("Assets:Cash", "1"),
            posting("Equity:Balance-Adjustments", "-1"),
            kind="adjustment",
        ),
        transaction(
            "usd-income-one",
            date(2026, 1, 10),
            posting("Assets:USD", "0.10", "USD"),
            posting("Income:Contract", "-0.10", "USD"),
            kind="income",
        ),
        transaction(
            "usd-income-two",
            date(2026, 1, 10),
            posting("Assets:USD", "0.20", "USD"),
            posting("Income:Contract", "-0.20", "USD"),
            kind="income",
        ),
        transaction(
            "uncategorized-legacy-expense",
            date(2026, 1, 12),
            posting("Expenses:Legacy", "1", "EUR"),
            posting("Assets:Euro", "-1", "EUR"),
        ),
        transaction(
            "before-period",
            date(2025, 12, 31),
            posting("Income:Salary", "-500"),
            posting("Assets:Bank", "500"),
            kind="income",
        ),
        transaction(
            "after-period",
            date(2026, 2, 1),
            posting("Expenses:Food:Groceries", "500"),
            posting("Assets:Bank", "-500"),
        ),
    )
    return Ledger(categories=categories, transactions=transactions)


JANUARY = (date(2026, 1, 1), date(2026, 1, 31))


def balance_ledger() -> Ledger:
    accounts = (
        Account("Assets:Bank", "Bank", "UAH", EPOCH),
        Account("Assets:Cash", "Cash", "UAH", EPOCH),
        Account("Assets:Euro", "Euro", "EUR", EPOCH),
        Account("Liabilities:Loan", "Loan", "UAH", EPOCH),
        Account("Assets:Old", "Old", "UAH", EPOCH, active=False),
        Account("Assets:Closed", "Closed", "UAH", EPOCH, active=False),
        Account("Assets:Future", "Future", "UAH", date(2026, 2, 1)),
    )
    return replace(report_ledger(), accounts=accounts)


def test_account_balances_split_opening_inclusive_change_and_closing() -> None:
    result = {item.identifier: item for item in account_balances(balance_ledger(), *JANUARY)}

    assert list(result) == ["Assets:Bank", "Assets:Cash", "Assets:Euro", "Liabilities:Loan"]
    bank = result["Assets:Bank"]
    assert (bank.opening, bank.change, bank.closing) == (
        Decimal("500"),
        Decimal("-62"),
        Decimal("438"),
    )
    cash = result["Assets:Cash"]
    assert (cash.opening, cash.change, cash.closing) == (ZERO, Decimal("186"), Decimal("186"))
    euro = result["Assets:Euro"]
    assert (euro.currency, euro.change) == ("EUR", Decimal("4"))
    assert result["Liabilities:Loan"].closing == Decimal("20")


def test_account_balances_hide_idle_inactive_and_unopened_accounts() -> None:
    ledger = balance_ledger()
    ledger = replace(
        ledger,
        transactions=(
            *ledger.transactions,
            transaction(
                "old-funds",
                date(2025, 6, 1),
                posting("Assets:Old", "7"),
                posting("Equity:Opening-Balances", "-7"),
                kind="opening",
            ),
        ),
    )
    january = {item.identifier for item in account_balances(ledger, *JANUARY)}
    february = {
        item.identifier for item in account_balances(ledger, date(2026, 2, 1), date(2026, 2, 28))
    }

    assert "Assets:Closed" not in january and "Assets:Future" not in january
    assert "Assets:Old" in january
    assert "Assets:Future" in february
    with pytest.raises(LedgerError):
        account_balances(ledger, date(2026, 2, 1), date(2026, 1, 31))


def test_period_report_applies_accounting_dates_reimbursements_and_currency_rules() -> None:
    report = period_report(report_ledger(), date(2026, 1, 1), date(2026, 1, 31))

    assert [
        (item.currency, item.income, item.expenses, item.net) for item in report.currencies
    ] == [
        ("EUR", Decimal("0"), Decimal("1.50"), Decimal("-1.50")),
        ("UAH", Decimal("100"), Decimal("-3"), Decimal("103")),
        ("USD", Decimal("0.30"), Decimal("0"), Decimal("0.30")),
    ]
    assert report.unassigned_expenses == (("EUR", Decimal("1")),)
    assert report.unassigned_income == ()
    assert report.expense_categories[0].name == "Bank fees"
    assert report.expense_categories[0].amount("UAH") == Decimal("2")
    food = next(item for item in report.expense_categories if item.identifier == "Expenses:Food")
    groceries = food.children[0]
    assert food.amount("UAH") == Decimal("-5")
    assert groceries.amount("UAH") == Decimal("-5")
    assert not groceries.active
    salary = next(item for item in report.income_categories if item.identifier == "Income:Salary")
    assert salary.amount("UAH") == Decimal("100")


def test_empty_period_and_report_changes_follow_current_ledger_snapshot() -> None:
    ledger = report_ledger()
    empty = period_report(ledger, date(2030, 1, 1), date(2030, 1, 31))
    assert empty.currencies == ()
    assert empty.expense_categories == ()

    original = period_report(ledger, date(2026, 1, 1), date(2026, 1, 31))
    edited_transactions = tuple(item for item in ledger.transactions if item.id != "expense-end")
    edited = period_report(
        replace(ledger, transactions=edited_transactions),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    assert next(item for item in original.currencies if item.currency == "UAH").expenses == -3
    assert next(item for item in edited.currencies if item.currency == "UAH").expenses == -33


def test_hierarchical_totals_include_parent_postings_without_changing_primary_totals() -> None:
    ledger = Ledger(
        categories=(
            Category("Expenses:Food", "Food", EPOCH),
            Category("Expenses:Food:Groceries", "Groceries", EPOCH),
            Category("Expenses:Food:Restaurants", "Restaurants", EPOCH),
        ),
        transactions=(
            transaction(
                "parent-direct",
                date(2026, 1, 1),
                posting("Expenses:Food", "6"),
                posting("Assets:Cash", "-6"),
            ),
            transaction(
                "child",
                date(2026, 1, 2),
                posting("Expenses:Food:Groceries", "4"),
                posting("Assets:Cash", "-4"),
            ),
        ),
    )

    report = period_report(ledger, date(2026, 1, 1), date(2026, 1, 31))
    food = report.expense_categories[0]
    assert food.amount("UAH") == Decimal("10")
    assert food.children[0].amount("UAH") == Decimal("4")
    assert report.currencies[0].expenses == Decimal("10")


def test_period_presets_cover_expected_calendar_boundaries() -> None:
    assert period_bounds("current_month", date(2026, 3, 31)) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
    )
    assert period_bounds("previous_month", date(2024, 3, 5)) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )
    assert period_bounds("current_year", date(2026, 6, 1)) == (
        date(2026, 1, 1),
        date(2026, 6, 1),
    )
    assert period_bounds("month", date(2024, 2, 1)) == (date(2024, 2, 1), date(2024, 2, 29))
    assert period_bounds("month", date(2025, 2, 1)) == (date(2025, 2, 1), date(2025, 2, 28))
    assert period_bounds("month", date(9999, 12, 1)) == (date(9999, 12, 1), date.max)
    with pytest.raises(LedgerError, match="report_date_range"):
        period_bounds("unknown", date(2026, 1, 1))
    with pytest.raises(LedgerError, match="report_date_range"):
        period_report(Ledger(), date(2026, 2, 1), date(2026, 1, 31))


def test_decimal_totals_remain_exact() -> None:
    ledger = Ledger(
        categories=(Category("Income:Sales", "Sales", EPOCH),),
        transactions=tuple(
            transaction(
                f"income-{index}",
                date(2026, 1, 1),
                posting("Income:Sales", "-0.10"),
                posting("Assets:Cash", "0.10"),
                kind="income",
            )
            for index in range(3)
        ),
    )

    report = period_report(ledger, date(2026, 1, 1), date(2026, 1, 1))
    assert report.currencies[0].income == Decimal("0.30")
    assert report.income_categories[0].amount("UAH") == Decimal("0.30")


def test_reports_navigation_and_custom_range_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        journal = Journal(
            DesktopFolder(str(tmp_path), FILENAME),
            today=lambda: date(2026, 3, 1),
        )
        await journal.load()
        view = FinanceView()
        page = MagicMock(spec=ft.Page)
        page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view.journal = journal
        view.current_screen = "reports"
        view._refresh()

        assert view._selected_index() == 3
        assert len(view.navigation_bar.destinations) == 4
        assert view.report_period.value == "current_month"
        empty_state = view.report_summary.controls[0]
        assert isinstance(empty_state, ft.Text)
        assert empty_state.value == view.tr("report_empty_period")

        view.report_period.value = "custom"
        view.report_start.occurred.value = date_input(date(2026, 2, 2), view.language)
        view.report_end.occurred.value = date_input(date(2026, 2, 1), view.language)
        view._report_date_changed(MagicMock())
        assert view.report_error.visible
        assert view.report_error.value == view.tr("report_date_range")
        assert view.report_summary.controls == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "month,offset,expected",
    [
        (date(2026, 1, 31), -1, date(2025, 12, 1)),
        (date(2025, 12, 31), 1, date(2026, 1, 1)),
        (date(2024, 3, 31), -1, date(2024, 2, 1)),
        (date(2024, 2, 29), 1, date(2024, 3, 1)),
    ],
)
def test_month_navigation_uses_calendar_months(month: date, offset: int, expected: date) -> None:
    assert shift_month(month, offset) == expected


@pytest.mark.parametrize("month,offset", [(date.min, -1), (date.max, 1)])
def test_month_navigation_rejects_out_of_range_dates(month: date, offset: int) -> None:
    with pytest.raises(LedgerError, match="report_date_range"):
        shift_month(month, offset)


@pytest.mark.parametrize("width", [390, 1366])
@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_overview_month_arrows_presets_and_refresh_preserve_selected_month(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, language: str
) -> None:
    async def scenario() -> None:
        today = [date(2024, 3, 5)]
        monkeypatch.setattr(FinanceView, "_today", lambda _: today[0])
        page = MagicMock(spec=ft.Page)
        page.width = width
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.language = language
        view.startup_complete = True
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: today[0])
        await journal.load()
        view.journal = journal
        view._navigate("reports")

        previous, month_heading, following = view.report_month_navigation.controls
        assert previous is view.report_previous and following is view.report_next
        assert isinstance(month_heading, ft.Row)
        calendar, label = month_heading.controls
        assert isinstance(calendar, ft.Icon) and calendar.icon == ft.Icons.CALENDAR_TODAY
        assert calendar.size == 16 and label is view.report_month_label
        assert month_heading.alignment == ft.MainAxisAlignment.CENTER
        assert view.report_month_navigation.visible
        assert view.report_month_label.value == f"{view.tr('date_month_03')} 2024"
        assert view.report_previous.tooltip == view.tr("budget_previous")
        assert view.report_next.tooltip == view.tr("budget_next")
        assert view._report_dates() == (date(2024, 3, 1), today[0])

        view._report_previous_month(MagicMock())
        assert view.report_period.value == "month"
        assert view._report_dates() == (date(2024, 2, 1), date(2024, 2, 29))
        view._report_next_month(MagicMock())
        assert view._report_dates() == (date(2024, 3, 1), today[0])
        view._report_next_month(MagicMock())
        assert view._report_dates() == (date(2024, 4, 1), date(2024, 4, 30))
        assert any(
            option.key == "month" and option.text == f"{view.tr('date_month_04')} 2024"
            for option in view.report_period.options
        )
        cached = view._report_data
        view._refresh()
        assert view._report_data is cached
        view._navigate("home")
        view._navigate("reports")
        assert view.report_month == date(2024, 4, 1)
        view.language = "uk" if language != "uk" else "en"
        view._refresh_labels()
        view._render_reports()
        assert view.report_month_label.value == f"{view.tr('date_month_04')} 2024"
        assert view._report_dates() == (date(2024, 4, 1), date(2024, 4, 30))

        today[0] = date(2024, 5, 1)
        view._refresh()
        assert view._report_dates() == (date(2024, 4, 1), date(2024, 4, 30))
        journal.ledger = replace(journal.ledger, transactions=())
        view._refresh()
        assert view._report_data is not cached
        assert view.report_month == date(2024, 4, 1)

        view.report_period.value = "current_year"
        view._report_period_changed(MagicMock())
        assert not view.report_month_navigation.visible
        assert view._report_dates() == (date(2024, 1, 1), today[0])
        view._report_previous_month(MagicMock())
        assert view.report_period.value == "current_year"

        view.report_period.value = "custom"
        view.report_start.occurred.value = "10/"
        view._report_period_changed(MagicMock())
        assert not view.report_month_navigation.visible
        assert view.report_range.visible and view.report_error.visible
        view.report_period.value = "month"
        view._report_period_changed(MagicMock())
        assert view.report_month_navigation.visible and not view.report_range.visible
        assert not view.report_error.visible
        assert view.report_start.occurred.value == "10/"
        assert view._report_dates() == (date(2024, 4, 1), date(2024, 4, 30))

        view.report_period.value = "previous_month"
        view._report_period_changed(MagicMock())
        assert view._report_dates() == (date(2024, 4, 1), date(2024, 4, 30))
        view._report_previous_month(MagicMock())
        assert view._report_dates() == (date(2024, 3, 1), date(2024, 3, 31))
        view.report_period.value = "current_month"
        view._report_period_changed(MagicMock())
        assert view._report_dates() == (date(2024, 5, 1), today[0])

    asyncio.run(scenario())


@pytest.mark.parametrize("month,offset", [(date.min, -1), (date(9999, 12, 1), 1)])
def test_overview_month_boundary_errors_preserve_period_and_show_warning(
    monkeypatch: pytest.MonkeyPatch, month: date, offset: int
) -> None:
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.report_month = month
    view.report_period.value = "month"
    view._render_reports()
    assert view.report_previous.disabled == (offset == -1)
    assert view.report_next.disabled == (offset == 1)
    assert (
        view.report_month_label.value == f"{view.tr(f'date_month_{month.month:02}')} {month.year}"
    )
    unavailable = view.report_summary.controls[0]
    assert isinstance(unavailable, ft.Text)
    assert unavailable.value == view.tr("report_unavailable")
    view._report_step_month(offset)
    assert view.report_month == month and view.report_period.value == "month"
    assert view.report_error.visible and view.report_error.value == view.tr("report_date_range")
    page.update.assert_called()


def test_overview_month_navigation_updates_inclusive_ledger_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(FinanceView, "_today", lambda _: date(2026, 1, 31))
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2026, 1, 31))
        await journal.load()
        journal.ledger = report_ledger()
        view.journal = journal
        view._render_reports()
        january = view._report_data
        assert january is not None and january.currencies[1].expenses == Decimal("-3")
        view._report_next_month(MagicMock())
        february = view._report_data
        assert february is not None and february.currencies[0].expenses == Decimal("500")
        assert view._report_range == (date(2026, 2, 1), date(2026, 2, 28))
        view._report_previous_month(MagicMock())
        assert view._report_data == january
        view._report_previous_month(MagicMock())
        december = view._report_data
        assert december is not None and december.currencies[0].income == Decimal("500")
        assert view._report_range == (date(2025, 12, 1), date(2025, 12, 31))

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [390, 1366])
def test_report_category_tree_is_compact_borderless_and_has_no_root_indent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        page.width = width
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        monkeypatch.setattr(FinanceView, "_today", lambda _: date(2026, 1, 31))
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        journal.ledger = report_ledger()
        view = FinanceView()
        view.journal = journal
        view._render_reports()
        card = view.report_categories.controls[1]
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Column)
        assert card.theme is not None
        assert card.theme.list_tile_theme == ft.ListTileTheme(
            horizontal_spacing=12, min_leading_width=16, min_vertical_padding=0
        )
        assert card.theme.icon_theme == ft.IconTheme(size=16)
        expenses = card.content.controls[1]
        assert isinstance(expenses, ft.Column) and expenses.spacing == 0
        heading, bank_fees, food = expenses.controls
        assert isinstance(heading, ft.Text)
        assert isinstance(bank_fees, ft.ListTile) and bank_fees.content_padding == 0
        assert isinstance(food, ft.ExpansionTile)
        assert food.affinity == ft.TileAffinity.LEADING
        assert food.dense and food.min_tile_height == 40
        assert food.tile_padding == 0
        assert food.controls_padding == ft.Padding.only(left=16)
        assert food.shape == food.collapsed_shape == ft.RoundedRectangleBorder()
        assert bank_fees.min_height == food.min_tile_height == 40
        assert bank_fees.min_vertical_padding == 0
        assert food.controls and isinstance(food.controls[0], ft.ListTile)
        groceries = food.controls[0]
        assert groceries.content_padding == 0 and groceries.min_height == 40
        assert isinstance(groceries.title, ft.Row)
        inactive = groceries.title.controls[0]
        assert isinstance(inactive, ft.Column)
        assert isinstance(inactive.controls[1], ft.Text)
        assert inactive.controls[1].value == view.tr("crud_inactive_label")

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [390, 800, 1366])
@pytest.mark.parametrize("state", ["unavailable", "empty", "populated"])
def test_reports_keep_scroll_clearance_for_floating_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    state: str,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        page.width = width
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        monkeypatch.setattr(FinanceView, "_today", lambda _: date(2026, 1, 31))
        view = FinanceView()
        view.startup_complete = True
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2026, 1, 31))
        if state != "unavailable":
            await journal.load()
        if state == "populated":
            await journal.create_account("Cash", "Cash", "UAH", "100", opened=EPOCH)
            await journal.create_category("Food", "Food", opened=EPOCH)
            await journal.add_expense("Assets:Cash", "Expenses:Food", "10", "", "2026-01-15")
        view.journal = journal
        view._refresh()
        for screen in ("reports", "home", "reports"):
            view._navigate(screen)
            if screen != "reports":
                continue
            assert view.screen_host.content is view.reports_screen
            assert view.report_overview.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
            period_row = next(
                control
                for control in view.report_overview.controls
                if isinstance(control, ft.Row) and control.controls == [view.report_period]
            )
            assert period_row.width is None and not period_row.wrap
            assert view.report_period.expand is True and view.report_period.width is None
            assert view.report_summary.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
            assert view.report_summary.width is None
            if state == "populated":
                assert all(
                    isinstance(card, ft.Container) and card.width is None
                    for card in view.report_summary.controls
                )
            listing = view.report_overview.controls[-1]
            assert isinstance(listing, ft.Container)
            assert listing.content is view.report_balances
            assert listing.padding == ft.Padding.only(bottom=96)
            assert view.body.scroll == ft.ScrollMode.AUTO
            assert page.floating_action_button is view.fab
            assert view.fab.disabled == (state == "unavailable")
            assert bool(view.report_categories.controls) == (state == "populated")

    asyncio.run(scenario())
