"""Currency-safe summaries derived from the validated ledger."""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext

from app.domain.ledger import ZERO, Ledger, LedgerError


@dataclass(frozen=True)
class CategoryReport:
    identifier: str
    name: str
    amounts: tuple[tuple[str, Decimal], ...]
    children: tuple["CategoryReport", ...] = ()
    active: bool = True

    def amount(self, currency: str) -> Decimal:
        return dict(self.amounts).get(currency, ZERO)


@dataclass(frozen=True)
class CurrencyReport:
    currency: str
    income: Decimal
    expenses: Decimal

    @property
    def net(self) -> Decimal:
        with localcontext() as context:
            context.prec = 50
            return self.income - self.expenses


@dataclass(frozen=True)
class PeriodReport:
    currencies: tuple[CurrencyReport, ...]
    income_categories: tuple[CategoryReport, ...]
    expense_categories: tuple[CategoryReport, ...]
    unassigned_income: tuple[tuple[str, Decimal], ...]
    unassigned_expenses: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class AccountBalance:
    identifier: str
    name: str
    currency: str
    opening: Decimal
    change: Decimal

    @property
    def closing(self) -> Decimal:
        with localcontext() as context:
            context.prec = 50
            return self.opening + self.change


def account_balances(ledger: Ledger, start: date, end: date) -> tuple[AccountBalance, ...]:
    """Per-account balance before ``start``, inclusive change, and balance after ``end``."""
    if end < start:
        raise LedgerError("report_date_range")
    accounts = {account.id: account for account in ledger.accounts if account.opened <= end}
    opening = dict.fromkeys(accounts, ZERO)
    change = dict.fromkeys(accounts, ZERO)
    with localcontext() as context:
        context.prec = 50
        for transaction in ledger.transactions:
            if transaction.date > end:
                continue
            target = opening if transaction.date < start else change
            for posting in transaction.postings:
                if posting.account in accounts:
                    target[posting.account] += posting.amount
    return tuple(
        AccountBalance(
            account.id, account.name, account.currency, opening[account.id], change[account.id]
        )
        for account in sorted(accounts.values(), key=lambda item: (item.name, item.id))
        if account.active or opening[account.id] or change[account.id]
    )


def shift_month(day: date, offset: int) -> date:
    index = day.year * 12 + day.month - 1 + offset
    try:
        return date(index // 12, index % 12 + 1, 1)
    except ValueError as exc:
        raise LedgerError("report_date_range") from exc


def period_bounds(preset: str, today: date) -> tuple[date, date]:
    if preset == "month":
        return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
    if preset == "current_month":
        return today.replace(day=1), today
    if preset == "previous_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if preset == "current_year":
        return date(today.year, 1, 1), today
    raise LedgerError("report_date_range")


def currency_totals(ledger: Ledger, start: date, end: date) -> tuple[CurrencyReport, ...]:
    totals = ledger.period_totals(start, end)
    return tuple(
        CurrencyReport(
            currency,
            totals["income"].get(currency, ZERO),
            totals["expenses"].get(currency, ZERO),
        )
        for currency in sorted(set(totals["income"]) | set(totals["expenses"]))
    )


def period_report(ledger: Ledger, start: date, end: date) -> PeriodReport:
    """Aggregate category postings once; never combine different currencies."""
    if end < start:
        raise LedgerError("report_date_range")

    categories = {category.id: category for category in ledger.categories}
    amounts: dict[str, dict[str, dict[str, Decimal]]] = {
        "Income": defaultdict(dict),
        "Expenses": defaultdict(dict),
    }
    unassigned: dict[str, dict[str, Decimal]] = {
        "Income": {},
        "Expenses": {},
    }
    with localcontext() as context:
        context.prec = 50
        for transaction in ledger.transactions:
            if not start <= transaction.date <= end:
                continue
            for posting in transaction.postings:
                root = (
                    "Income"
                    if posting.account.startswith("Income:")
                    else "Expenses"
                    if posting.account.startswith("Expenses:")
                    else None
                )
                if root is None:
                    continue
                value = -posting.amount if root == "Income" else posting.amount
                if posting.account in categories:
                    current = amounts[root][posting.account]
                    current[posting.currency] = current.get(posting.currency, ZERO) + value
                else:
                    unassigned[root][posting.currency] = (
                        unassigned[root].get(posting.currency, ZERO) + value
                    )

    def category_roots(root: str) -> tuple[CategoryReport, ...]:
        children: dict[str, set[str]] = defaultdict(set)
        direct: dict[str, dict[str, Decimal]] = {
            identifier: dict(values) for identifier, values in amounts[root].items()
        }
        identifiers = set(categories)
        identifiers.update(direct)
        for identifier in identifiers:
            if not identifier.startswith(root + ":"):
                continue
            parts = identifier.split(":")
            for length in range(2, len(parts) + 1):
                children[":".join(parts[: length - 1])].add(":".join(parts[:length]))

        def build(identifier: str) -> CategoryReport | None:
            descendants = tuple(
                item
                for child in sorted(children.get(identifier, ()))
                if (item := build(child)) is not None
            )
            own = direct.get(identifier, {})
            combined = dict(own)
            with localcontext() as context:
                context.prec = 50
                for child in descendants:
                    for currency, value in child.amounts:
                        combined[currency] = combined.get(currency, ZERO) + value
            if not combined and not descendants:
                return None
            category = categories.get(identifier)
            return CategoryReport(
                identifier,
                category.name if category else identifier.rsplit(":", 1)[-1],
                tuple(sorted(combined.items())),
                descendants,
                category.active if category else True,
            )

        top_level = sorted(
            identifier
            for identifier in identifiers
            if identifier.startswith(root + ":") and ":" not in identifier[len(root) + 1 :]
        )
        return tuple(item for identifier in top_level if (item := build(identifier)) is not None)

    return PeriodReport(
        currency_totals(ledger, start, end),
        category_roots("Income"),
        category_roots("Expenses"),
        tuple(sorted(unassigned["Income"].items())),
        tuple(sorted(unassigned["Expenses"].items())),
    )
