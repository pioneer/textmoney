"""Bounded single-currency expenses, not a general Beancount booking engine."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

EPOCH = date(1970, 1, 1)
EQUITY = "Equity:Opening-Balances"
ZERO = Decimal("0")
MAX_AMOUNT = Decimal("999999999999.99")


class LedgerError(Exception):
    """Safe, actionable validation message without private journal excerpts."""


def money(text: str) -> Decimal:
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", text.strip()):
        raise LedgerError("Amount must be a nonnegative decimal with at most 2 fractional digits.")
    try:
        return checked_amount(Decimal(text.strip()))
    except InvalidOperation as exc:
        raise LedgerError("Invalid monetary amount.") from exc


def checked_amount(value: Decimal) -> Decimal:
    if not value.is_finite() or abs(value) > MAX_AMOUNT or value != value.quantize(Decimal(".01")):
        raise LedgerError(
            "Amounts must be finite, within 12 integer digits, and have at most 2 decimals."
        )
    return value


def account_path(identifier: str, root: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith(f"{root}:"):
        identifier = identifier[len(root) + 1 :]
    if not re.fullmatch(r"[A-Z][A-Za-z0-9-]*(?::[A-Z][A-Za-z0-9-]*)*", identifier):
        raise LedgerError(
            "Technical ID needs colon-separated segments starting with A-Z, "
            "e.g. Cash or Food:Groceries."
        )
    return f"{root}:{identifier}"


def display_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise LedgerError("Display name/payee must be 1-120 characters without control characters.")
    return value


def currency_code(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch("[A-Z]{3}", value):
        raise LedgerError("Currency must be a three-letter uppercase code, e.g. UAH, USD or EUR.")
    return value


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    currency: str
    opened: date = EPOCH
    active: bool = True


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    opened: date = EPOCH
    active: bool = True


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Transaction:
    id: str
    date: date
    timestamp: str
    payee: str
    narration: str
    postings: tuple[Posting, ...]
    kind: str
    persistent_id: bool = True


@dataclass(frozen=True)
class Ledger:
    accounts: tuple[Account, ...] = ()
    categories: tuple[Category, ...] = ()
    transactions: tuple[Transaction, ...] = ()
    equity_opened: date | None = None
    operating_currencies: tuple[str, ...] = ()

    def category_active(self, identifier: str) -> bool:
        return any(c.id == identifier for c in self.categories) and all(
            c.active
            for c in self.categories
            if identifier == c.id or identifier.startswith(c.id + ":")
        )

    def balances(self, *, today: date | None = None) -> dict[str, Decimal]:
        balances = {account.id: ZERO for account in self.accounts}
        accounts = {account.id: account for account in self.accounts}
        categories = {category.id: category for category in self.categories}
        declared = set(accounts) | set(categories)
        if self.equity_opened is not None:
            declared.add(EQUITY)
        ids: set[str] = set()
        with localcontext() as context:
            context.prec = 50
            for transaction in sorted(
                self.transactions,
                key=lambda t: (t.date, datetime.fromisoformat(t.timestamp), t.id),
            ):
                if transaction.date > (today or date.today()):
                    raise LedgerError("Future-dated transactions are not supported in MVP 0.1.")
                if transaction.id in ids:
                    raise LedgerError(
                        "Duplicate transaction ID. Journal unchanged; "
                        "resolve duplicates externally."
                    )
                ids.add(transaction.id)
                if len(transaction.postings) < 2:
                    raise LedgerError("Transaction needs at least two explicit postings.")
                totals: dict[str, Decimal] = {}
                for posting in transaction.postings:
                    checked_amount(posting.amount)
                    if posting.account not in declared:
                        raise LedgerError(
                            "Posting references an undeclared account. Create accounts explicitly."
                        )
                    opened = (
                        accounts[posting.account].opened
                        if posting.account in accounts
                        else categories[posting.account].opened
                        if posting.account in categories
                        else self.equity_opened
                    )
                    if opened is None or transaction.date < opened:
                        raise LedgerError("Transaction predates its account/category declaration.")
                    totals[posting.currency] = totals.get(posting.currency, ZERO) + posting.amount
                    if posting.account in accounts:
                        if posting.currency != accounts[posting.account].currency:
                            raise LedgerError("Posting currency differs from the account currency.")
                        balances[posting.account] += posting.amount
                if len(totals) != 1 or any(total != ZERO for total in totals.values()):
                    raise LedgerError(
                        "Unbalanced or multi-currency transaction. No journal changes allowed."
                    )
                if any(balance < ZERO for balance in balances.values()):
                    raise LedgerError(
                        "Insufficient funds: an ordinary account would become negative."
                    )
        return balances

    def expense(
        self,
        account_id: str,
        category_id: str,
        amount: Decimal,
        payee: str,
        occurred: date,
        timestamp: datetime,
        transaction_id: str,
        *,
        today: date | None = None,
    ) -> Transaction:
        accounts = {account.id: account for account in self.accounts}
        if account_id not in accounts or category_id not in {c.id for c in self.categories}:
            raise LedgerError("Choose an explicitly created account and category.")
        if not accounts[account_id].active or not self.category_active(category_id):
            raise LedgerError("crud_inactive")
        checked_amount(amount)
        if amount <= ZERO:
            raise LedgerError("Expense amount must be greater than zero.")
        if not EPOCH <= occurred <= (today or date.today()):
            raise LedgerError("Expense date must be between 1970-01-01 and today.")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise LedgerError("Timestamp must include a timezone.")
        transaction = Transaction(
            transaction_id,
            occurred,
            timestamp.isoformat(),
            display_name(payee),
            "Expense",
            (
                Posting(category_id, amount, accounts[account_id].currency),
                Posting(account_id, -amount, accounts[account_id].currency),
            ),
            "expense",
        )
        Ledger(
            self.accounts, self.categories, (*self.transactions, transaction), self.equity_opened
        ).balances(today=today)
        return transaction
