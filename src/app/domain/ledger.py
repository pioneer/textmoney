"""Explicit monetary movements and bounded total-price exchanges, not a booking engine."""

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

EPOCH = date(1970, 1, 1)
EQUITY = "Equity:Opening-Balances"
ADJUSTMENTS = "Equity:Balance-Adjustments"
BANK_FEES = "Expenses:Bank-Fees"
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


def description_text(value: str) -> str:
    value = value.replace("\r\n", "\n")
    if len(value) > 500 or any(ord(char) < 32 and char != "\n" for char in value):
        raise LedgerError("crud_description")
    return value


def currency_code(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch("[A-Z]{3}", value):
        raise LedgerError("Currency must be a three-letter uppercase code, e.g. UAH, USD or EUR.")
    return value


def exchange_values(
    debited: Decimal,
    credited: Decimal,
    fee: Decimal,
    source_currency: str,
    target_currency: str,
    fee_currency: str,
) -> tuple[Decimal, Decimal, Decimal]:
    for value in (debited, credited, fee):
        checked_amount(value)
    if source_currency == target_currency or fee_currency not in {source_currency, target_currency}:
        raise LedgerError("exchange_fee_invalid")
    with localcontext() as context:
        context.prec = 50
        principal = debited - (fee if fee_currency == source_currency else ZERO)
        gross = credited + (fee if fee_currency == target_currency else ZERO)
        if debited <= ZERO or credited <= ZERO or fee < ZERO or principal <= ZERO:
            raise LedgerError("transfer_amount")
        checked_amount(principal)
        checked_amount(gross)
        return principal, gross, gross / principal


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    currency: str
    opened: date
    active: bool = True

    @property
    def liability(self) -> bool:
        return self.id.startswith("Liabilities:")


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    opened: date
    active: bool = True


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Decimal
    currency: str
    total_price: Decimal | None = None
    price_currency: str | None = None


@dataclass(frozen=True)
class TransferDetails:
    source: Posting
    target: Posting
    fee: Decimal
    fee_category: str
    fee_currency: str
    mode: str
    debited: Decimal

    @property
    def exchange(self) -> bool:
        return self.source.currency != self.target.currency

    @property
    def converted(self) -> Decimal:
        return -self.source.amount

    @property
    def received_principal(self) -> Decimal:
        return (
            self.source.total_price if self.source.total_price is not None else self.target.amount
        )

    @property
    def rate(self) -> Decimal:
        with localcontext() as context:
            context.prec = 50
            return self.received_principal / self.converted


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
    related_expense: str | None = None
    fee_mode: str = "added"


@dataclass(frozen=True)
class Ledger:
    accounts: tuple[Account, ...] = ()
    categories: tuple[Category, ...] = ()
    transactions: tuple[Transaction, ...] = ()
    equity_opened: date | None = None
    operating_currencies: tuple[str, ...] = ()
    adjustments_opened: date | None = None
    default_fee_category: str = BANK_FEES

    def period_totals(self, start: date, end: date) -> dict[str, dict[str, Decimal]]:
        """Inclusive accounting-date totals; income positive, reimbursements net expenses."""
        if end < start:
            raise LedgerError("incoming_period")
        result: dict[str, dict[str, Decimal]] = {"income": {}, "expenses": {}}
        with localcontext() as context:
            context.prec = 50
            for transaction in self.transactions:
                if start <= transaction.date <= end:
                    for posting in transaction.postings:
                        group = (
                            "income"
                            if posting.account.startswith("Income:")
                            else "expenses"
                            if posting.account.startswith("Expenses:")
                            else None
                        )
                        if group:
                            values = result[group]
                            value = -posting.amount if group == "income" else posting.amount
                            values[posting.currency] = values.get(posting.currency, ZERO) + value
        return result

    def category_active(self, identifier: str) -> bool:
        return any(c.id == identifier for c in self.categories) and all(
            c.active
            for c in self.categories
            if identifier == c.id or identifier.startswith(c.id + ":")
        )

    def balances(
        self, *, today: date | None = None, negative_assets: set[str] | None = None
    ) -> dict[str, Decimal]:
        balances = {account.id: ZERO for account in self.accounts}
        accounts = {account.id: account for account in self.accounts}
        categories = {category.id: category for category in self.categories}
        declared = set(accounts) | set(categories)
        if self.equity_opened is not None:
            declared.add(EQUITY)
        if self.adjustments_opened is not None:
            declared.add(ADJUSTMENTS)
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
                        else self.adjustments_opened
                        if posting.account == ADJUSTMENTS
                        else self.equity_opened
                    )
                    if opened is None or transaction.date < opened:
                        raise LedgerError("Transaction predates its account/category declaration.")
                    weight, currency = posting.amount, posting.currency
                    if posting.total_price is not None:
                        checked_amount(posting.total_price)
                        if (
                            posting.total_price <= ZERO
                            or posting.amount == ZERO
                            or posting.price_currency is None
                            or posting.price_currency == posting.currency
                        ):
                            raise LedgerError("exchange_invalid")
                        currency = currency_code(posting.price_currency)
                        weight = (
                            posting.total_price if posting.amount > ZERO else -posting.total_price
                        )
                    elif posting.price_currency is not None:
                        raise LedgerError("exchange_invalid")
                    totals[currency] = totals.get(currency, ZERO) + weight
                    if posting.account in accounts:
                        if posting.currency != accounts[posting.account].currency:
                            raise LedgerError("Posting currency differs from the account currency.")
                        balances[posting.account] += posting.amount
                if any(p.total_price is not None for p in transaction.postings):
                    self.transfer_details(transaction)
                if any(total != ZERO for total in totals.values()) or (
                    len(totals) != 1
                    and not any(p.total_price is not None for p in transaction.postings)
                ):
                    raise LedgerError(
                        "Unbalanced or multi-currency transaction. No journal changes allowed."
                    )
                if negative_assets is not None:
                    negative_assets.update(
                        identifier
                        for identifier, balance in balances.items()
                        if balance < ZERO and not accounts[identifier].liability
                    )
        return balances

    def balance_warnings(self, *, today: date | None = None) -> set[str]:
        negative: set[str] = set()
        self.balances(today=today, negative_assets=negative)
        return negative

    def transfer_details(self, transaction: Transaction) -> TransferDetails:
        accounts = {a.id for a in self.accounts}
        prices = [p for p in transaction.postings if p.total_price is not None]
        sources = [
            p
            for p in transaction.postings
            if p.account in accounts and p.amount < ZERO and (not prices or p in prices)
        ]
        targets = [p for p in transaction.postings if p.account in accounts and p.amount > ZERO]
        fees = [
            p
            for p in transaction.postings
            if p.account.startswith("Expenses:") and p.amount >= ZERO
        ]
        if len(sources) != 1 or len(targets) != 1 or len(fees) > 1:
            raise LedgerError("crud_transaction_readonly")
        source, target = sources[0], targets[0]
        fee = fees[0].amount if fees else ZERO
        fee_currency = fees[0].currency if fees else source.currency
        extra = [p for p in transaction.postings if p not in (source, target, *fees)]
        if source.account == target.account or target.total_price is not None:
            raise LedgerError("crud_transaction_readonly")
        if prices:
            if (
                len(prices) != 1
                or prices[0] != source
                or source.currency == target.currency
                or source.price_currency != target.currency
                or source.total_price is None
                or source.total_price <= ZERO
                or fee_currency not in {source.currency, target.currency}
                or (fees and fees[0].total_price is not None)
                or len(extra) > 1
            ):
                raise LedgerError("exchange_invalid")
            source_fee = fee if fee_currency == source.currency else ZERO
            if extra:
                debit = extra[0]
                if (
                    debit.account != source.account
                    or debit.currency != source.currency
                    or debit.amount != -source_fee
                    or debit.total_price is not None
                ):
                    raise LedgerError("exchange_invalid")
            elif source_fee:
                raise LedgerError("exchange_invalid")
            if source.total_price != target.amount + (
                fee if fee_currency == target.currency else ZERO
            ):
                raise LedgerError("exchange_invalid")
        elif (
            len(extra) > 1
            or any(
                p.account != source.account or p.amount != ZERO or p.currency != source.currency
                for p in extra
            )
            or source.currency != target.currency
            or fee_currency != source.currency
            or transaction.fee_mode not in {"added", "deducted"}
        ):
            raise LedgerError("crud_transaction_readonly")
        return TransferDetails(
            source,
            target,
            fee,
            fees[0].account if fees else BANK_FEES,
            fee_currency,
            transaction.fee_mode,
            -source.amount + (fee if prices and fee_currency == source.currency else ZERO),
        )

    def transfer(
        self,
        source: str,
        destination: str,
        amount: Decimal,
        fee: Decimal,
        fee_category: str,
        fee_mode: str,
        occurred: date,
        timestamp: datetime,
        transaction_id: str,
        *,
        today: date | None = None,
        destination_amount: Decimal | None = None,
        fee_currency: str | None = None,
    ) -> Transaction:
        accounts = {a.id: a for a in self.accounts}
        if source == destination or source not in accounts or destination not in accounts:
            raise LedgerError("transfer_accounts")
        if not accounts[source].active or not accounts[destination].active:
            raise LedgerError("crud_inactive")
        currency = accounts[source].currency
        target_currency = accounts[destination].currency
        exchange = currency != target_currency
        if exchange != (destination_amount is not None):
            raise LedgerError("transfer_currency")
        checked_amount(amount)
        checked_amount(fee)
        if amount <= ZERO or fee < ZERO or (not exchange and fee_mode not in {"added", "deducted"}):
            raise LedgerError("transfer_amount")
        if not exchange and fee_mode == "deducted" and fee >= amount:
            raise LedgerError("transfer_amount")
        if fee and (
            not fee_category.startswith("Expenses:") or not self.category_active(fee_category)
        ):
            raise LedgerError("incoming_category")
        if not EPOCH <= occurred <= (today or date.today()):
            raise LedgerError("incoming_date")
        if timestamp.utcoffset() is None:
            raise LedgerError("crud_datetime")
        if exchange:
            assert destination_amount is not None
            if fee_currency is None and not fee:
                fee_currency = currency
            if fee_currency is None:
                raise LedgerError("exchange_fee_invalid")
            principal, gross, _ = exchange_values(
                amount,
                destination_amount,
                fee,
                currency,
                target_currency,
                fee_currency,
            )
            postings = (
                Posting(
                    source,
                    -checked_amount(principal),
                    currency,
                    checked_amount(gross),
                    target_currency,
                ),
                Posting(destination, destination_amount, target_currency),
                *((Posting(fee_category, fee, fee_currency),) if fee else ()),
                *((Posting(source, -fee, currency),) if fee and fee_currency == currency else ()),
            )
        else:
            if fee_currency is not None and fee_currency != currency:
                raise LedgerError("exchange_fee_invalid")
            debit = amount + fee if fee_mode == "added" else amount
            credit = amount - fee if fee_mode == "deducted" else amount
            postings = (
                Posting(source, -checked_amount(debit), currency),
                Posting(destination, checked_amount(credit), currency),
                *((Posting(fee_category, fee, currency),) if fee else ()),
            )
        transaction = Transaction(
            transaction_id,
            occurred,
            timestamp.isoformat(),
            "",
            "",
            postings,
            "transfer",
            fee_mode=fee_mode,
        )
        replace(self, transactions=(*self.transactions, transaction)).balances(today=today)
        return transaction

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
        if account_id not in accounts or category_id not in {
            c.id for c in self.categories if c.id.startswith("Expenses:")
        }:
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
            display_name(payee) if payee.strip() else "",
            "Expense",
            (
                Posting(category_id, amount, accounts[account_id].currency),
                Posting(account_id, -amount, accounts[account_id].currency),
            ),
            "expense",
        )
        replace(self, transactions=(*self.transactions, transaction)).balances(today=today)
        return transaction

    def incoming(
        self,
        account_id: str,
        category_id: str,
        amount: Decimal,
        payee: str,
        occurred: date,
        timestamp: datetime,
        transaction_id: str,
        kind: str,
        *,
        today: date | None = None,
    ) -> Transaction:
        account = next((a for a in self.accounts if a.id == account_id), None)
        if account is None:
            raise LedgerError("incoming_choose_account")
        if not account.active:
            raise LedgerError("crud_inactive")
        root = {"income": "Income:", "reimbursement": "Expenses:", "adjustment": "Equity:"}.get(
            kind
        )
        if root is None or not category_id.startswith(root):
            raise LedgerError("incoming_category")
        if kind == "adjustment":
            if category_id != ADJUSTMENTS or self.adjustments_opened is None:
                raise LedgerError("incoming_category")
        elif not self.category_active(category_id):
            raise LedgerError("incoming_category")
        checked_amount(amount)
        if amount <= ZERO:
            raise LedgerError("incoming_amount")
        if not EPOCH <= occurred <= (today or date.today()):
            raise LedgerError("incoming_date")
        if timestamp.utcoffset() is None:
            raise LedgerError("crud_datetime")
        transaction = Transaction(
            transaction_id,
            occurred,
            timestamp.isoformat(),
            display_name(payee) if payee.strip() else "",
            "",
            (
                Posting(account_id, amount, account.currency),
                Posting(category_id, -amount, account.currency),
            ),
            kind,
        )
        replace(self, transactions=(*self.transactions, transaction)).balances(today=today)
        return transaction
