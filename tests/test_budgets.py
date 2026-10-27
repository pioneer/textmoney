import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from beancount import loader
from fava.beans.abc import Custom as FavaCustom
from fava.core import FavaLedger
from fava.core.budgets import calculate_budget, calculate_budget_children, parse_budgets
from fava.core.conversion import conversion_from_str
from fava.util.date import INTERVALS as FAVA_INTERVALS
from fava.util.date import parse_date, parse_fye_string

from app.domain.budgets import BudgetFilter, calculate, report_range, rows
from app.domain.ledger import EPOCH, LedgerError
from app.domain.storage import ConflictError
from app.infrastructure.beancount_journal import JournalCodec
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import format_budget_number
from app.storage import StorageError

TODAY = date(2026, 12, 31)
HEADER = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Assets:Euro EUR
2000-01-01 open Expenses:Food
2000-01-01 open Expenses:Food:Meals
2000-01-01 open Expenses:FoodOther
2000-01-01 open Expenses:Transport
2000-01-01 open Income:Salary
"""
MOVEMENTS = """
2026-01-02 * "Market" "Split" #shared
    Expenses:Food 10.00 USD
    Expenses:Food:Meals 5.00 USD
    Expenses:Transport 7.00 USD
    Assets:Cash -22.00 USD
2026-01-03 * "Refund" ""
    Expenses:Food -3.00 USD
    Assets:Cash 3.00 USD
2026-01-04 * "Work" ""
    Income:Salary -500.00 USD
    Assets:Cash 500.00 USD
2026-01-16 * "Cafe" ""
    Expenses:Food:Meals 12.00 EUR
    Assets:Euro -12.00 EUR
2026-02-02 * "Market" ""
    Expenses:Food 11.00 USD
    Assets:Cash -11.00 USD
2026-03-01 * "Shop" ""
    Expenses:FoodOther 4.00 USD
    Assets:Cash -4.00 USD
"""


@pytest.mark.parametrize(
    ("definitions", "begin", "end"),
    [
        ('2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD', "2026-01-01", "2026-02-01"),
        (
            '2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD\n'
            '2026-01-01 custom "budget" Expenses:Transport "monthly" 20 USD',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2026-01-01 custom "budget" Expenses:Food:Meals "monthly" 30 USD',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD\n'
            '2026-01-01 custom "budget" Expenses:Food:Meals "monthly" 30 USD\n'
            '2026-01-01 custom "budget" Expenses:FoodOther "monthly" 5 USD',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2026-01-16 custom "budget" Expenses:Food "monthly" 10000 UAH',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2026-01-01 custom "budget" Expenses:Food "monthly" 310 USD\n'
            '2026-01-16 custom "budget" Expenses:Food "monthly" 620 USD',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2025-01-01 custom "budget" Expenses:Food "monthly" 100 USD\n'
            '2026-01-01 custom "budget" Expenses:Food "monthly" 200 USD',
            "2025-03-15",
            "2025-04-04",
        ),
        (
            '2026-01-01 custom "budget" Expenses:Food "daily" 10 USD\n'
            '2026-01-01 custom "budget" Expenses:Transport "weekly" 30 USD',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2026-01-01 custom "budget" Expenses:Food "quarterly" 100 USD\n'
            '2026-01-01 custom "budget" Expenses:Transport "yearly" 500 USD',
            "2026-01-01",
            "2026-04-02",
        ),
        *[
            (
                f'{year}-01-01 custom "budget" Expenses:Food "monthly" 100 USD',
                f"{year}-02-01",
                f"{year}-03-01",
            )
            for year in (2024, 2025)
        ],
        *[
            (
                '2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD',
                f"2026-{month:02}-01",
                f"2026-{month + 1:02}-01",
            )
            for month in (1, 2, 4)
        ],
        (
            '2026-01-01 custom "budget" Expenses:Food "monthly" 310 USD\n'
            '2026-01-16 custom "budget" Expenses:Food "monthly" 620 EUR',
            "2026-01-01",
            "2026-02-01",
        ),
        (
            '2026-01-01 custom "budget" Income:Salary "monthly" -2000 USD',
            "2026-01-01",
            "2026-03-01",
        ),
        ('2026-01-01 custom "budget" Expenses:Food "monthly" 0 USD', "2026-01-01", "2026-02-01"),
        ("", "2026-01-01", "2026-02-01"),
        (
            '2026-01-16 custom "budget" Expenses:Food "monthly" 0 USD\n'
            '2026-01-01 custom "budget" Expenses:Food "monthly" 310 USD\n'
            '2026-01-16 custom "budget" Expenses:Food "daily" 2 USD',
            "2026-01-01",
            "2026-03-01",
        ),
        (
            '2026-01-01 custom "budget" Expenses:Food "MONTH" (10 / 3) GOLD\n'
            '    description: "Preserved externally authored definition"',
            "2026-01-01",
            "2026-02-01",
        ),
        *[
            (
                f'2026-01-01 custom "budget" Expenses:Food "{interval}" 100 USD',
                "2026-01-28",
                "2026-04-03",
            )
            for interval in FAVA_INTERVALS
        ],
    ],
)
def test_exact_allocation_parity(definitions: str, begin: str, end: str) -> None:
    source = HEADER + definitions + "\n"
    entries, errors, _ = loader.load_string(source)
    assert not errors
    reference, errors = parse_budgets([e for e in entries if isinstance(e, FavaCustom)])
    assert not errors
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    start, stop = date.fromisoformat(begin), date.fromisoformat(end)
    for account in ("Expenses", "Expenses:Food", "Expenses:Food:Meals", "Income:Salary"):
        expected = dict(calculate_budget(reference, account, start, stop))
        children = dict(calculate_budget_children(reference, account, start, stop))
        # Application operations must not inherit precision 50 from money reports.
        with localcontext() as context:
            context.prec = 50
            assert calculate(ledger.budgets, account, start, stop) == expected
            assert calculate(ledger.budgets, account, start, stop, children=True) == children


@pytest.fixture
def reports(tmp_path: Path) -> tuple[FavaLedger, str]:
    source = (
        HEADER
        + MOVEMENTS
        + """
2026-01-01 custom "budget" Expenses:Food "monthly" 100.00 USD
2026-01-01 custom "budget" Expenses:Food:Meals "weekly" 21.00 EUR
2026-01-16 custom "budget" Expenses:Food "daily" 4.00 USD
2026-01-01 custom "budget" Income:Salary "monthly" -2000.00 USD
2026-01-01 price USD 2.00 EUR
2026-02-10 price USD 3.00 EUR
"""
    )
    path = tmp_path / FILENAME
    path.write_text(source)
    reference = FavaLedger(str(path), poll_watcher=False)
    assert not reference.errors
    return reference, source


@pytest.mark.parametrize("conversion", ["units", "at_cost", "at_value", "EUR", "USD", "GBP"])
@pytest.mark.parametrize("interval", ["daily", "weekly", "monthly", "quarterly", "yearly"])
def test_period_actual_budget_difference_and_conversion_match_fava(
    reports: tuple[FavaLedger, str], conversion: str, interval: str
) -> None:
    reference, source = reports
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    # Full quarter deliberately includes zero-activity and zero-budget periods.
    actual_rows = rows(ledger, date(2026, 1, 1), date(2026, 4, 1), interval, conversion=conversion)
    filtered = reference.get_filtered(time="2026-Q1")
    for account in ("Expenses", "Expenses:Food", "Expenses:Food:Meals", "Income"):
        expected = reference.charts.interval_totals(
            filtered, FAVA_INTERVALS[interval], account, conversion
        )
        actual = [row for row in actual_rows if row.account == account]
        assert len(actual) == len(expected)
        for result, target in zip(actual, expected, strict=True):
            assert result.budget == target.budgets
            assert result.actual == dict(target.balance)
            for currency in target.budgets:
                assert result.remaining(currency) == (
                    target.budgets[currency] - target.balance.get(currency, Decimal(0))
                )
            for currency in set(result.actual) - result.budget.keys():
                assert result.remaining(currency) is None


@pytest.mark.parametrize("cumulative", [False, True])
def test_account_tree_direct_children_and_cumulative_parity(
    reports: tuple[FavaLedger, str], cumulative: bool
) -> None:
    reference, source = reports
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    results = rows(
        ledger,
        date(2026, 1, 1),
        date(2026, 4, 1),
        cumulative=cumulative,
        conversion="EUR",
        tree=True,
    )
    filtered = reference.get_filtered(time="2026-Q1")
    trees, periods = reference.interval_balances(
        filtered, FAVA_INTERVALS["monthly"], "Expenses", accumulate=cumulative
    )
    for tree, period in zip(trees, periods, strict=True):
        for account in ("Expenses", "Expenses:Food", "Expenses:Food:Meals", "Income"):
            result = next(
                row for row in results if row.account == account and row.begin == period.begin
            )
            begin = date(2026, 1, 1) if cumulative else period.begin
            assert result.budget == reference.budgets.calculate_children(account, begin, period.end)
            assert result.direct_budget == reference.budgets.calculate(account, begin, period.end)
            node = tree.get(account).serialise(
                conversion_from_str("EUR"),
                reference.prices,
                period.end_inclusive,
                with_cost=False,
            )
            assert result.direct_actual == dict(node.balance)
            assert result.actual == dict(node.balance_children)


def test_filtered_transactions_do_not_filter_budget_definitions(
    reports: tuple[FavaLedger, str],
) -> None:
    reference, source = reports
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    result = rows(
        ledger,
        date(2026, 1, 1),
        date(2026, 2, 1),
        filters=BudgetFilter("Expenses:Food", "Market", "shared"),
    )
    filtered = reference.get_filtered(
        time="2026-01", account="Expenses:Food", filter="#shared payee:Market"
    )
    expected = reference.charts.interval_totals(
        filtered, FAVA_INTERVALS["month"], "Expenses:Food", "units"
    )
    food = next(row for row in result if row.account == "Expenses:Food")
    assert food.actual == dict(expected[0].balance)
    assert food.budget == expected[0].budgets
    assert [transaction.payee for transaction in food.transactions] == ["Market"]


def test_zero_missing_and_consumption_are_distinct() -> None:
    ledger = JournalCodec(today=lambda: TODAY).parse(
        HEADER + MOVEMENTS + '\n2026-01-01 custom "budget" Expenses:Food "monthly" 0 USD\n'
    )
    food = next(
        row
        for row in rows(ledger, date(2026, 1, 1), date(2026, 2, 1))
        if row.account == "Expenses:Food"
    )
    assert food.budget == {"USD": Decimal(0)}
    assert food.remaining("USD") == Decimal("-12.00")
    assert food.remaining("EUR") is None
    assert food.consumed("USD") is None


def test_safe_budget_crud_history_backup_and_external_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        await journal.create_category("Food", "Food", opened=EPOCH)
        initial = journal.source
        backup = await journal.save_budget(
            "Expenses:Food", date(2026, 1, 1), "monthly", "100.1234", "USD", initial
        )
        assert backup and (tmp_path / backup).read_text() == initial
        await journal.save_budget(
            "Expenses:Food", date(2026, 2, 1), "monthly", "200", "USD", journal.source
        )
        assert len(journal.ledger.budgets) == 2
        assert calculate(
            journal.ledger.budgets, "Expenses:Food", date(2026, 1, 1), date(2026, 2, 1)
        )
        assert len(list((tmp_path / "backups").iterdir())) >= 2
        entries, errors, _ = loader.load_string(journal.source)
        assert not errors
        expected, errors = parse_budgets([e for e in entries if isinstance(e, FavaCustom)])
        assert not errors
        assert calculate(
            journal.ledger.budgets, "Expenses:Food", date(2026, 1, 1), date(2026, 3, 1)
        ) == calculate_budget(expected, "Expenses:Food", date(2026, 1, 1), date(2026, 3, 1))
        old_source = journal.source
        identifier = journal.ledger.budgets[0].id
        await journal.save_budget(
            "Expenses:Food",
            date(2026, 1, 1),
            "daily",
            "3",
            "USD",
            old_source,
            identifier=identifier,
        )
        with pytest.raises(ConflictError):
            await journal.delete_budget(identifier, old_source)
        source = journal.source
        (tmp_path / FILENAME).write_text(source + "\n; External change\n")
        with pytest.raises(ConflictError):
            await journal.save_budget(
                "Expenses:Food", date(2026, 1, 1), "daily", "1", "USD", source
            )
        assert (tmp_path / FILENAME).read_text().endswith("; External change\n")
        await journal.load()
        await journal.delete_budget(journal.ledger.budgets[0].id, journal.source)
        assert len(journal.ledger.budgets) == 1

    asyncio.run(scenario())


def test_budget_edit_preserves_unrelated_bytes_comments_metadata_and_crlf(tmp_path: Path) -> None:
    source = (
        HEADER
        + """
; Keep this comment
2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD ; note
    description: "Keep private metadata"

2026-01-01 custom "unrelated" "preserve me"
"""
    )

    async def scenario() -> None:
        path = tmp_path / FILENAME
        path.write_bytes(source.replace("\n", "\r\n").encode())
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        await journal.save_budget(
            "Expenses:Food",
            date(2026, 1, 1),
            "monthly",
            "200",
            "USD",
            journal.source,
            identifier=journal.ledger.budgets[0].id,
        )
        assert (
            path.read_bytes() == source.replace("100 USD", "200 USD").replace("\n", "\r\n").encode()
        )
        await journal.delete_budget(journal.ledger.budgets[0].id, journal.source)
        assert "; Keep this comment" in journal.source and "; note" in journal.source
        assert 'custom "unrelated" "preserve me"' in journal.source

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "directive",
    [
        '2026-01-01 custom "budget" Expenses:Food "nonsense" 100 USD',
        '2026-01-01 custom "budget" Expenses:Food "monthly"',
    ],
)
def test_invalid_budget_is_explicit_not_silently_discarded(directive: str) -> None:
    with pytest.raises(LedgerError, match="Line .*Custom directive"):
        JournalCodec().parse(HEADER + directive + "\n")


@pytest.mark.parametrize("fye", ["12-31", "06-30", "02-28", "18-30", "01-15"])
@pytest.mark.parametrize(
    "period",
    [
        "2026",
        "2026-02",
        "2024-02-29",
        "2026-Q2",
        "2026-W01",
        "FY2026",
        "2026-01-15 to 2026-02-10",
        "FY2026-Q1",
        "FY2026-Q3",
    ],
)
def test_fiscal_and_explicit_ranges_match_fava(period: str, fye: str) -> None:
    expected = parse_date(period, parse_fye_string(fye))
    if expected == (None, None):
        with pytest.raises(LedgerError):
            report_range(period, fye)
    else:
        assert report_range(period, fye) == expected


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("conversion", ["units", "EUR", "USD", "GBP"])
def test_income_statement_series_and_net_profit_match_reference(
    reports: tuple[FavaLedger, str], conversion: str, invert: bool
) -> None:
    reference, source = reports
    ledger = replace(JournalCodec(today=lambda: TODAY).parse(source), invert_income=invert)
    actual = rows(ledger, date(2026, 1, 1), date(2026, 4, 1), summary=True, conversion=conversion)
    filtered = reference.get_filtered(time="2026-Q1")
    for label, accounts, inverted in [
        ("Expenses", "Expenses", False),
        ("Income", "Income", invert),
        ("Net Profit", ("Income", "Expenses"), invert),
    ]:
        expected = reference.charts.interval_totals(
            filtered, FAVA_INTERVALS["month"], accounts, conversion, invert=inverted
        )
        found = [row for row in actual if row.account == label]
        for result, target in zip(found, expected, strict=True):
            assert result.actual == dict(target.balance)
            assert result.budget == target.budgets
            if label == "Net Profit":
                assert result.budget == result.direct_budget == {}


@pytest.mark.parametrize("locale", [None, "en", "uk", "ru", "de", "fr", "hi"])
def test_display_context_precision_and_formatted_values_match_fava(
    tmp_path: Path, locale: str | None
) -> None:
    source = (
        HEADER
        + MOVEMENTS
        + """
2000-01-01 commodity EUR
    precision: 3
2000-01-01 note Assets:Cash "Precision metadata"
    reference-amount: 1.2345 USD
2026-01-01 custom "budget" Expenses:Food "monthly" 100.00 USD
2026-01-01 custom "budget" Expenses:Food "monthly" 310.000 EUR
"""
    )
    if locale:
        source += f'2000-01-01 custom "fava-option" "locale" "{locale}"\n'
    path = tmp_path / FILENAME
    path.write_text(source)
    reference = FavaLedger(str(path), poll_watcher=False)
    assert not reference.errors
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    assert dict(ledger.currency_precisions) == reference.format_decimal.precisions
    assert ledger.budget_locale == locale
    assert ledger.invert_income == reference.fava_options.invert_income_liabilities_equity
    for row in rows(ledger, date(2026, 1, 1), date(2026, 2, 1)):
        for values in (row.budget, row.actual, row.direct_budget, row.direct_actual):
            for currency, amount in values.items():
                result = format_budget_number(
                    amount, dict(ledger.currency_precisions)[currency], locale
                )
                expected = reference.format_decimal(amount, currency)
                if locale is None:
                    expected = expected.replace("-", "−")
                assert result == expected


def test_browser_fixed_and_intl_formatting_matches_fava_frontend() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional JavaScript runtime unavailable; backend formatter parity still runs.")
    # Fava frontend/src/format.ts uses d3 .pf (Number.toFixed) or Intl.NumberFormat.
    cases = [
        {"value": value, "precision": precision, "locale": locale}
        for value in (
            "0",
            "-0.001",
            "1.005",
            "1.125",
            "-2.55",
            "99999.995",
            "99.99999999999999999999999992",
            "123456789012.34",
        )
        for precision in (0, 2, 4, 14)
        for locale in (None, "en", "uk", "ru", "de", "fr", "hi")
    ]
    javascript = r"""
const cases = JSON.parse(process.argv[1]);
console.log(JSON.stringify(cases.map(({value, precision, locale}) => {
    const n = Number(value);
    if (locale !== null) {
        return new Intl.NumberFormat(locale, {
            minimumFractionDigits: precision,
            maximumFractionDigits: precision,
        }).format(n);
    }
    const fixed = Math.abs(n).toFixed(precision);
    return (n < 0 && Number(fixed) !== 0 ? "\u2212" : "") + fixed;
})));
"""
    expected = json.loads(
        subprocess.run(
            [node, "-e", javascript, json.dumps(cases)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for case, target in zip(cases, expected, strict=True):
        assert (
            format_budget_number(Decimal(case["value"]), case["precision"], case["locale"])
            == target
        )


def test_last_100_chart_periods_and_partial_periods_match_fava(
    reports: tuple[FavaLedger, str],
) -> None:
    reference, source = reports
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    for time in ("2026", "2026-01-15 to 2026-02-10"):
        begin, end = report_range(time)
        result = rows(ledger, begin, end, "daily")
        actual = [row for row in result if row.account == "Expenses"]
        expected = reference.charts.interval_totals(
            reference.get_filtered(time=time), FAVA_INTERVALS["day"], "Expenses", "units"
        )
        assert len(actual) == len(expected)
        for row, target in zip(actual, expected, strict=True):
            assert row.budget == target.budgets
            assert row.actual == dict(target.balance)


def test_budget_save_failure_preserves_original_and_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        await journal.create_category("Food", "Food", opened=EPOCH)
        original = (tmp_path / FILENAME).read_bytes()

        async def fail() -> str | None:
            raise StorageError("Backup failed (synthetic). Original not modified.")

        monkeypatch.setattr(journal.storage, "write", fail)
        with pytest.raises(StorageError):
            await journal.save_budget(
                "Expenses:Food", date(2026, 1, 1), "monthly", "100", "USD", journal.source
            )
        assert (tmp_path / FILENAME).read_bytes() == original
        assert 'custom "budget"' in journal.storage.draft
        assert not journal.ready

    asyncio.run(scenario())


def test_external_same_date_priority_changes_are_detected(tmp_path: Path) -> None:
    first = '2026-01-01 custom "budget" Expenses:Food "daily" 1 USD\n'
    second = '2026-01-01 custom "budget" Expenses:Food "daily" 2 USD\n'

    async def scenario() -> None:
        path = tmp_path / FILENAME
        path.write_text(HEADER + first + second)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        assert calculate(
            journal.ledger.budgets, "Expenses:Food", date(2026, 1, 1), date(2026, 1, 2)
        ) == {"USD": Decimal(2)}
        path.write_text(HEADER + second + first)
        await journal.poll()
        assert calculate(
            journal.ledger.budgets, "Expenses:Food", date(2026, 1, 1), date(2026, 1, 2)
        ) == {"USD": Decimal(1)}
        assert (
            sum(
                value
                for key, value in journal.external_summary.items()
                if key.startswith("budgets_")
            )
            > 0
        )

    asyncio.run(scenario())


def test_retrospective_posting_edits_and_deletions_recalculate_like_fava(
    tmp_path: Path,
) -> None:
    source = HEADER + MOVEMENTS + '\n2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD\n'
    changed = source.replace("Expenses:Food 10.00 USD", "Expenses:Food 20.00 USD").replace(
        "Assets:Cash -22.00 USD", "Assets:Cash -32.00 USD"
    )
    removed = changed[: changed.index("2026-01-02 *")] + changed[changed.index("2026-01-03 *") :]

    async def scenario() -> None:
        path = tmp_path / FILENAME
        path.write_text(source)
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        for candidate in (changed, removed):
            path.write_text(candidate)
            await journal.poll()
            reference = FavaLedger(str(path), poll_watcher=False)
            assert not reference.errors
            actual = next(
                row
                for row in rows(journal.ledger, date(2026, 1, 1), date(2026, 2, 1))
                if row.account == "Expenses"
            )
            expected = reference.charts.interval_totals(
                reference.get_filtered(time="2026-01"),
                FAVA_INTERVALS["month"],
                "Expenses",
                "units",
            )[0]
            assert actual.actual == dict(expected.balance)
            assert actual.budget == expected.budgets

    asyncio.run(scenario())


def test_priced_transfers_fees_and_adjustments_keep_posting_classifications(
    tmp_path: Path,
) -> None:
    source = (
        HEADER
        + """
2000-01-01 open Equity:Balance-Adjustments
2026-01-01 custom "budget" Expenses:Transport "monthly" 10 USD
2026-01-05 * "Exchange" ""
    Assets:Cash -10 USD @ 2 EUR
    Assets:Euro 18 EUR
    Expenses:Transport 2 EUR
2026-01-06 * "Adjustment" ""
    Assets:Cash 5 USD
    Equity:Balance-Adjustments -5 USD
"""
    )
    path = tmp_path / FILENAME
    path.write_text(source)
    reference = FavaLedger(str(path), poll_watcher=False)
    assert not reference.errors
    ledger = JournalCodec(today=lambda: TODAY).parse(source)
    for conversion in ("units", "USD", "EUR", "at_cost", "at_value"):
        expected = reference.charts.interval_totals(
            reference.get_filtered(time="2026-01"),
            FAVA_INTERVALS["month"],
            "Expenses",
            conversion,
        )[0]
        actual = next(
            row
            for row in rows(ledger, date(2026, 1, 1), date(2026, 2, 1), conversion=conversion)
            if row.account == "Expenses"
        )
        assert actual.actual == dict(expected.balance) == {"EUR": Decimal(2)}
        assert actual.budget == expected.budgets


def test_budgets_remain_engine_free_and_notice_is_shipped() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    configuration = tomllib.loads((root / "pyproject.toml").read_text())
    production = configuration["project"]["dependencies"]
    assert not any(
        item.lower().startswith(("fava", "beancount==", "beanquery", "rustledger"))
        for item in production
    )
    assert "fava==1.30.16" in configuration["dependency-groups"]["dev"]
    assert "babel" in configuration["tool"]["flet"]["android"]["extract_packages"]
    notice = (root / "src/app/notices/fava.txt").read_text()
    assert "The MIT License (MIT)" in notice and "Copyright (c) 2015-2016 Dominik Aumayr" in notice
