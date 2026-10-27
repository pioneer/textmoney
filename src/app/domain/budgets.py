"""Fava 1.30.16 budget allocation and reports for explicit, cost-free postings.

Allocation/interval/price-map logic adapted from Fava (MIT).
Copyright (c) 2015-2016 Dominik Aumayr <dominik@aumayr.name>
The complete license is shipped in app/notices/fava.txt.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING

from app.domain.ledger import LedgerError

if TYPE_CHECKING:
    from app.domain.ledger import Ledger, Transaction

INTERVALS = ("daily", "weekly", "monthly", "quarterly", "yearly")
ALIASES: dict[str, str] = dict(
    zip(("day", "week", "month", "quarter", "year"), INTERVALS, strict=True)
)
ZERO = Decimal(0)
REFERENCE_CONTEXT = Context(prec=28)


@dataclass(frozen=True)
class Budget:
    id: str
    account: str
    date: date
    interval: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Price:
    date: date
    currency: str
    amount: Decimal
    quote: str


def interval_name(value: str) -> str:
    name = ALIASES.get(value.lower(), value.lower())
    if name not in INTERVALS:
        raise LedgerError("budget_interval_error")
    return name


def budget_amount(value: str) -> Decimal:
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
        raise LedgerError("budget_amount_error")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise LedgerError("budget_amount_error") from exc
    if not result.is_finite():
        raise LedgerError("budget_amount_error")
    return result


def budget_currency(value: str) -> str:
    if not re.fullmatch(r"[A-Z](?:[A-Z0-9'._-]*[A-Z0-9])?", value):
        raise LedgerError("budget_currency_error")
    return value


def interval_start(day: date, interval: str) -> date:
    interval = interval_name(interval)
    if interval == "yearly":
        return date(day.year, 1, 1)
    if interval == "quarterly":
        return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
    if interval == "monthly":
        return day.replace(day=1)
    if interval == "weekly":
        return day - timedelta(days=day.weekday())
    return day


def interval_next(day: date, interval: str) -> date:
    interval = interval_name(interval)
    try:
        if interval == "yearly":
            return date(day.year + 1, 1, 1)
        if interval == "quarterly":
            month = (day.month - 1) // 3 * 3 + 4
            return date(day.year + (month > 12), month % 12, 1)
        if interval == "monthly":
            return date(day.year + (day.month == 12), day.month % 12 + 1, 1)
        return day + timedelta(days=7 - day.weekday() if interval == "weekly" else 1)
    except (ValueError, OverflowError):
        return date.max


def report_range(value: str, fiscal_year_end: str = "12-31") -> tuple[date, date]:
    """Fava's explicit date/interval/range notation, including fiscal years."""
    value = value.strip().lower()
    match = re.fullmatch(r"(.*?)(?:-|to)(?=\s*(?:fy)*\d{4})(.*)", value)
    if match:
        return (
            report_range(match[1], fiscal_year_end)[0],
            report_range(match[2], fiscal_year_end)[1],
        )
    try:
        fiscal = re.fullmatch(r"fy(\d{4})(?:-q([1-4]))?", value)
        if fiscal:
            month, day = map(int, fiscal_year_end.split("-"))
            start = date(
                int(fiscal[1]) - 1 + (month - 1) // 12, (month - 1) % 12 + 1, day
            ) + timedelta(days=1)
            if (month - 1) % 12 + 1 == 2 and day == 28:
                start = start.replace(month=3, day=1)
            if fiscal[2]:
                if (date(2001, (month - 1) % 12 + 1, day) + timedelta(days=1)).day != 1:
                    raise LedgerError("budget_fiscal_quarter_error")
                offset = (int(fiscal[2]) - 1) * 3
                index = start.year * 12 + start.month - 1 + offset
                start = date(index // 12, index % 12 + 1, 1)
                return start, date((index + 3) // 12, (index + 3) % 12 + 1, 1)
            return start, start.replace(year=start.year + 1)
        match = re.fullmatch(r"(\d{4})(?:-q([1-4])|-w(\d{2})|-(\d{2})(?:-(\d{2}))?)?", value)
        if not match:
            raise LedgerError("budget_range_error")
        year, quarter, week, month, day = match.groups()
        if week:
            start = datetime.strptime(f"{year}-W{week}-1", "%G-W%V-%w").date()
            interval = "weekly"
        elif quarter:
            start, interval = date(int(year), (int(quarter) - 1) * 3 + 1, 1), "quarterly"
        else:
            start = date(int(year), int(month or 1), int(day or 1))
            interval = "daily" if day else "monthly" if month else "yearly"
        return start, interval_next(start, interval)
    except (ValueError, OverflowError) as exc:
        raise LedgerError("budget_range_error") from exc


def _days(day: date, interval: str) -> int:
    if interval == "daily":
        return 1
    if interval == "weekly":
        return 7
    start = interval_start(day, interval)
    return (interval_next(start, interval) - start).days


def calculate(
    budgets: Iterable[Budget], account: str, begin: date, end: date, *, children: bool = False
) -> dict[str, Decimal]:
    """Half-open range; preserve Fava's daily summation and Counter update order."""
    if end < begin:
        raise LedgerError("report_date_range")
    grouped: dict[str, list[Budget]] = defaultdict(list)
    for budget in sorted(budgets, key=lambda item: item.date):
        grouped[budget.account].append(budget)
    combined: dict[str, Decimal] = {}
    with localcontext(REFERENCE_CONTEXT):
        for identifier, definitions in grouped.items():
            if not (identifier.startswith(account) if children else identifier == account):
                continue
            values: dict[str, Decimal] = defaultdict(Decimal)
            day = begin
            while day < end:
                active: dict[str, Budget] = {}
                for budget in definitions:
                    if budget.date > day:
                        break
                    active[budget.currency] = budget
                for budget in active.values():
                    values[budget.currency] += budget.amount / _days(day, budget.interval)
                day += timedelta(days=1)
            if not children:
                return dict(values)
            for currency, amount in values.items():
                combined[currency] = amount + combined[currency] if currency in combined else amount
    return combined


def _add(values: dict[str, Decimal], currency: str, amount: Decimal) -> None:
    value = amount + values.get(currency, ZERO)
    if value == ZERO:
        values.pop(currency, None)
    else:
        values[currency] = value


class PriceMap:
    def __init__(self, prices: Iterable[Price]) -> None:
        self.rates: dict[tuple[str, str], list[tuple[date, Decimal]]] = defaultdict(list)
        with localcontext(REFERENCE_CONTEXT):
            for price in sorted(prices, key=lambda item: item.date):
                self.rates[price.currency, price.quote].append((price.date, price.amount))
                if price.amount != ZERO:
                    self.rates[price.quote, price.currency].append(
                        (price.date, Decimal(1) / price.amount)
                    )

    def get(self, currency: str, quote: str, day: date) -> Decimal | None:
        if currency == quote:
            return Decimal(1)
        return next(
            (number for when, number in reversed(self.rates[currency, quote]) if when <= day),
            None,
        )

    def convert(self, values: dict[str, Decimal], conversion: str, day: date) -> dict[str, Decimal]:
        if conversion in {"units", "at_cost", "at_value"}:
            return dict(values)
        result: dict[str, Decimal] = {}
        with localcontext(REFERENCE_CONTEXT):
            for currency, amount in values.items():
                rate = self.get(currency, conversion, day)
                _add(
                    result,
                    conversion if rate is not None else currency,
                    amount * rate if rate is not None else amount,
                )
        return result


@dataclass(frozen=True)
class BudgetFilter:
    account: str = ""
    text: str = ""
    tag: str = ""
    account_tree: bool = False

    def matches(self, transaction: "Transaction") -> bool:
        return (
            (
                not self.account
                or any(
                    (
                        posting.account == self.account
                        or posting.account.startswith(self.account + ":")
                    )
                    if self.account_tree
                    else posting.account.startswith(self.account)
                    for posting in transaction.postings
                )
            )
            and (not self.tag or self.tag in transaction.tags)
            and (
                not self.text
                or self.text.casefold() in f"{transaction.payee} {transaction.narration}".casefold()
            )
        )


@dataclass(frozen=True)
class BudgetRow:
    account: str
    begin: date
    end: date
    budget: dict[str, Decimal]
    actual: dict[str, Decimal]
    direct_budget: dict[str, Decimal]
    direct_actual: dict[str, Decimal]
    transactions: tuple["Transaction", ...]

    def remaining(self, currency: str, *, direct: bool = False) -> Decimal | None:
        budget = self.direct_budget if direct else self.budget
        actual = self.direct_actual if direct else self.actual
        if currency not in budget:
            return None
        with localcontext(REFERENCE_CONTEXT):
            return budget[currency] - actual.get(currency, ZERO)

    def consumed(self, currency: str, *, direct: bool = False) -> Decimal | None:
        """App presentation ratio, not a financial field supplied by Fava."""
        budget = self.direct_budget if direct else self.budget
        actual = self.direct_actual if direct else self.actual
        if not budget.get(currency):
            return None
        with localcontext(REFERENCE_CONTEXT):
            return actual.get(currency, ZERO) / budget[currency] * 100

    def inverted(self) -> "BudgetRow":
        with localcontext(REFERENCE_CONTEXT):
            return replace(
                self,
                budget={k: -v for k, v in self.budget.items()},
                actual={k: -v for k, v in self.actual.items()},
                direct_budget={k: -v for k, v in self.direct_budget.items()},
                direct_actual={k: -v for k, v in self.direct_actual.items()},
            )


def rows(
    ledger: "Ledger",
    begin: date,
    end: date,
    interval: str = "monthly",
    *,
    conversion: str = "units",
    cumulative: bool = False,
    tree: bool = False,
    summary: bool = False,
    filters: BudgetFilter | None = None,
) -> tuple[BudgetRow, ...]:
    """Report clipped calendar intervals over a half-open transaction filter.

    Match Fava's account report for cumulative values and interval chart for changes.
    No prices on postings are mistaken for market-price directives.
    """
    if begin >= end:
        raise LedgerError("report_date_range")
    filters = filters or BudgetFilter()
    interval = interval_name(interval)
    accounts = {"Expenses", "Income"}
    for identifier in (
        *(category.id for category in ledger.categories),
        *(budget.account for budget in ledger.budgets),
    ):
        parts = identifier.split(":")
        accounts.update(":".join(parts[:length]) for length in range(1, len(parts) + 1))
    if summary:
        accounts = {"Income", "Expenses", "Net Profit"}
    transactions = sorted(
        (
            transaction
            for transaction in ledger.transactions
            if begin <= transaction.date < end and filters.matches(transaction)
        ),
        key=lambda item: item.date,
    )
    prices = PriceMap(ledger.prices)
    periods = []
    start = begin
    while start < end:
        stop = min(end, interval_next(start, interval))
        periods.append((start, stop))
        start = stop
    if not tree and not cumulative:
        periods = periods[-100:]
    result: list[BudgetRow] = []
    with localcontext(REFERENCE_CONTEXT):
        for start, stop in periods:
            for account in sorted(accounts):
                net_profit = summary and account == "Net Profit"
                actual: dict[str, Decimal] = {}
                direct: dict[str, Decimal] = {}
                contributing = []
                for transaction in transactions:
                    if not (begin if cumulative else start) <= transaction.date < stop:
                        continue
                    matched = False
                    for posting in transaction.postings:
                        # Account trees are component-aware; chart prefixes are literal.
                        included = (
                            posting.account.startswith(("Income", "Expenses"))
                            if net_profit
                            else (
                                posting.account == account
                                or posting.account.startswith(account + ":")
                                if cumulative or tree
                                else posting.account.startswith(account)
                            )
                        )
                        if included:
                            _add(actual, posting.currency, posting.amount)
                            matched = True
                        if posting.account == account:
                            _add(direct, posting.currency, posting.amount)
                    if matched:
                        contributing.append(transaction)
                budget_begin = begin if cumulative else start
                row = BudgetRow(
                    account,
                    start,
                    stop,
                    {}
                    if net_profit
                    else calculate(ledger.budgets, account, budget_begin, stop, children=True),
                    prices.convert(actual, conversion, stop - timedelta(days=1)),
                    {} if net_profit else calculate(ledger.budgets, account, budget_begin, stop),
                    prices.convert(
                        actual if net_profit else direct, conversion, stop - timedelta(days=1)
                    ),
                    tuple(contributing),
                )
                if summary and ledger.invert_income and account in {"Income", "Net Profit"}:
                    row = row.inverted()
                result.append(row)
    return tuple(result)
