"""Currency-safe summaries derived from the validated ledger."""

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


def period_bounds(preset: str, today: date) -> tuple[date, date]:
    if preset == "current_month":
        return today.replace(day=1), today
    if preset == "previous_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if preset == "current_year":
        return date(today.year, 1, 1), today
    raise LedgerError("report_date_range")


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

    totals = ledger.period_totals(start, end)
    currencies = sorted(set(totals["income"]) | set(totals["expenses"]))
    return PeriodReport(
        tuple(
            CurrencyReport(
                currency,
                totals["income"].get(currency, ZERO),
                totals["expenses"].get(currency, ZERO),
            )
            for currency in currencies
        ),
        category_roots("Income"),
        category_roots("Expenses"),
        tuple(sorted(unassigned["Income"].items())),
        tuple(sorted(unassigned["Expenses"].items())),
    )
