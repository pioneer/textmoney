"""Bounded keyset selection over the existing, fully loaded journal."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from heapq import nlargest, nsmallest
from typing import Literal

from app.domain.ledger import Ledger, Transaction
from app.domain.reporting import CurrencyReport, currency_totals

PAGE_SIZE = 50
TransactionKey = tuple[date, datetime, str]
PageDirection = Literal["older", "newer", "latest"]


def transaction_key(transaction: Transaction) -> TransactionKey:
    return transaction.date, datetime.fromisoformat(transaction.timestamp), transaction.id


def matching_transactions(
    transactions: Iterable[Transaction], account: str = "", category: str = ""
) -> Iterator[Transaction]:
    for transaction in transactions:
        if account and not any(post.account == account for post in transaction.postings):
            continue
        if category and not any(
            post.account == category or post.account.startswith(category + ":")
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
    cursor: TransactionKey | None = None,
    direction: PageDirection = "latest",
    inclusive: bool = False,
) -> OperationPage:
    def candidates() -> Iterator[Transaction]:
        for transaction in matching_transactions(ledger.transactions, account, category):
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
    for transaction in matching_transactions(ledger.transactions, account, category):
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
                transactions=matching_transactions(ledger.transactions, account, category),
            ),
        )
        for day in days
    )
    return OperationPage(tuple(entries), newer, older, totals)
