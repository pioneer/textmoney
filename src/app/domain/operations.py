"""Bounded keyset selection over the existing, fully loaded journal."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from heapq import nlargest, nsmallest
from typing import Literal

from app.domain.ledger import Ledger, LedgerError, Transaction
from app.domain.reporting import CurrencyReport, category_posting_matches, currency_totals

PAGE_SIZE = 50
TransactionKey = tuple[date, datetime, str]
PageDirection = Literal["older", "newer", "latest"]


@dataclass(frozen=True)
class OperationScope:
    start: date | None = None
    end: date | None = None
    currency: str = ""
    query: str = ""
    report_root: str = ""
    unassigned: bool = False

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise LedgerError("report_date_range")


def transaction_key(transaction: Transaction) -> TransactionKey:
    return transaction.date, datetime.fromisoformat(transaction.timestamp), transaction.id


def matching_transactions(
    transactions: Iterable[Transaction],
    account: str = "",
    category: str = "",
    *,
    scope: OperationScope | None = None,
    categories: frozenset[str] = frozenset(),
) -> Iterator[Transaction]:
    scope = scope or OperationScope()
    query = scope.query.strip().casefold()
    for transaction in transactions:
        if scope.start is not None and transaction.date < scope.start:
            continue
        if scope.end is not None and transaction.date > scope.end:
            continue
        if query and query not in f"{transaction.payee} {transaction.narration}".casefold():
            continue
        if account and not any(post.account == account for post in transaction.postings):
            continue
        if (category or scope.currency or scope.report_root) and not any(
            (
                category_posting_matches(
                    post,
                    categories,
                    scope.currency,
                    scope.report_root,
                    None if scope.unassigned else category or scope.report_root,
                )
                if scope.report_root
                else (not scope.currency or post.currency == scope.currency)
                and (
                    not category
                    or post.account == category
                    or post.account.startswith(category + ":")
                )
            )
            for post in transaction.postings
        ):
            continue
        yield transaction


@dataclass(frozen=True)
class OperationPage:
    transactions: tuple[Transaction, ...] = ()
    newer: bool = False
    older: bool = False
    totals: tuple[tuple[date, tuple[CurrencyReport, ...]], ...] = ()


def operation_page(
    ledger: Ledger,
    account: str = "",
    category: str = "",
    *,
    scope: OperationScope | None = None,
    cursor: TransactionKey | None = None,
    direction: PageDirection = "latest",
    inclusive: bool = False,
) -> OperationPage:
    categories = frozenset(item.id for item in ledger.categories)

    def matching() -> Iterator[Transaction]:
        return matching_transactions(
            ledger.transactions, account, category, scope=scope, categories=categories
        )

    def candidates() -> Iterator[Transaction]:
        for transaction in matching():
            key = transaction_key(transaction)
            if cursor is None or (
                (key >= cursor if inclusive else key > cursor)
                if direction == "newer"
                else (key <= cursor if inclusive else key < cursor)
            ):
                yield transaction

    select = nsmallest if direction == "newer" else nlargest
    entries = select(PAGE_SIZE, candidates(), key=transaction_key)
    if direction == "newer":
        entries.reverse()
    if not entries:
        return OperationPage()
    first, last = transaction_key(entries[0]), transaction_key(entries[-1])
    newer = older = False
    for transaction in matching():
        key = transaction_key(transaction)
        newer |= key > first
        older |= key < last
        if newer and older:
            break
    days = tuple(dict.fromkeys(transaction.date for transaction in entries))
    totals = tuple(
        (
            day,
            currency_totals(
                ledger,
                day,
                day,
                transactions=matching(),
            ),
        )
        for day in days
    )
    return OperationPage(tuple(entries), newer, older, totals)
