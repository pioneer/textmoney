"""Lossless reading, directive splicing and generation with the phone-tested parser."""

import io
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal

from autobean_refactor import models, printer
from autobean_refactor.parser import Parser
from lark.exceptions import UnexpectedInput

from app.domain.ledger import (
    ADJUSTMENTS,
    EQUITY,
    Account,
    Category,
    Ledger,
    LedgerError,
    Posting,
    Transaction,
    account_path,
    currency_code,
    display_name,
)


def render(model: models.RawModel) -> str:
    return printer.print_model(model, io.StringIO()).getvalue()


def append(source: str, addition: str) -> str:
    newline = "\r\n" if "\r\n" in source else "\n"
    return (
        source
        + (newline if source.endswith(("\n", "\r")) else newline * 2)
        + addition.replace("\r\n", "\n").replace("\n", newline).rstrip("\r\n")
        + newline
    )


class JournalCodec:
    def __init__(self, *, today: Callable[[], date] = date.today) -> None:
        self.parser = Parser()
        self.today = today

    def parse(self, text: str) -> Ledger:
        try:
            return self._parse(text)
        except ArithmeticError:
            raise LedgerError(
                "Invalid monetary expression. Saving blocked; journal preserved."
            ) from None

    def _parse(self, text: str) -> Ledger:
        try:
            file = self.parser.parse(text, models.File)
        except (UnexpectedInput, ValueError) as exc:
            line = getattr(exc, "line", 1)
            column = getattr(exc, "column", 1)
            raise LedgerError(
                f"Line {line}, column {column}: unsupported or invalid Beancount syntax. "
                "Check the directive and its indentation. "
                "Original bytes preserved; saving blocked."
            ) from None
        accounts: list[Account] = []
        categories: list[Category] = []
        transactions: list[Transaction] = []
        transaction_entries: list[models.Transaction] = []
        equity_opened = None
        adjustments_opened = None
        operating_currencies: list[str] = []
        names: set[str] = set()
        safe = (
            models.Note,
            models.Event,
            models.Price,
            models.Commodity,
            models.Custom,
            models.Document,
            models.IgnoredLine,
        )
        for entry in file.raw_directives:
            try:
                self._validate_entry(entry)
            except LedgerError as exc:
                raise self._entry_error(entry, str(exc)) from None
            except ArithmeticError:
                raise self._entry_error(entry, "Invalid monetary expression.") from None
            if isinstance(entry, safe):
                continue
            if isinstance(entry, models.Option):
                if entry.key not in {"title", "operating_currency"}:
                    raise self._entry_error(entry, "Unsupported accounting option.")
                if entry.key == "operating_currency":
                    operating_currencies.append(currency_code(entry.value))
            elif isinstance(entry, models.Open):
                if entry.account in names:
                    raise self._entry_error(entry, "Duplicate account declaration.")
                names.add(entry.account)
                if entry.booking is not None:
                    raise self._entry_error(entry, "Lot booking requires the official engine.")
                name = entry.meta.get("name", entry.account)
                if not isinstance(name, str):
                    raise self._entry_error(entry, "Account name metadata must be a string.")
                name = display_name(name)
                active = entry.meta.get("txncount-active", True)
                if not isinstance(active, bool):
                    raise self._entry_error(entry, "txncount-active metadata must be boolean.")
                currencies = list(entry.currencies)
                if entry.account.startswith(("Assets:", "Liabilities:")):
                    account_path(entry.account, entry.account.split(":")[0])
                    if len(currencies) != 1:
                        raise self._entry_error(
                            entry, "Each ordinary account needs exactly one declared currency."
                        )
                    accounts.append(
                        Account(
                            entry.account, name, currency_code(currencies[0]), entry.date, active
                        )
                    )
                elif entry.account.startswith(("Expenses:", "Income:")):
                    account_path(entry.account, entry.account.split(":")[0])
                    if currencies:
                        raise self._entry_error(
                            entry, "Expense category declarations must not restrict currencies."
                        )
                    categories.append(Category(entry.account, name, entry.date, active))
                elif entry.account in {EQUITY, ADJUSTMENTS}:
                    if currencies:
                        raise self._entry_error(
                            entry, "Opening-balance equity must not restrict currencies."
                        )
                    if entry.account == EQUITY:
                        equity_opened = entry.date
                    else:
                        adjustments_opened = entry.date
                else:
                    raise self._entry_error(
                        entry, "This account type requires unsupported accounting semantics."
                    )
            elif isinstance(entry, models.Transaction):
                postings: list[Posting] = []
                for posting in entry.postings:
                    if isinstance(posting, models.BlockComment):
                        continue
                    posting_currency = posting.currency
                    if (
                        posting.number is None
                        or posting_currency is None
                        or posting.cost is not None
                        or (
                            posting.price is not None
                            and not isinstance(posting.price, (models.TotalPrice, models.UnitPrice))
                        )
                    ):
                        raise self._entry_error(
                            entry, "Posting inference and costs require the official engine."
                        )
                    price = posting.price
                    price_currency = price.currency if price else None
                    if price is not None and (price.number is None or price_currency is None):
                        raise self._entry_error(
                            entry, "Exchange prices need explicit amounts and currencies."
                        )
                    total_price = (
                        price.number if isinstance(price, models.TotalPrice) and price else None
                    )
                    unit_price = (
                        price.number if isinstance(price, models.UnitPrice) and price else None
                    )
                    postings.append(
                        Posting(
                            posting.account,
                            posting.number,
                            currency_code(posting_currency),
                            total_price,
                            currency_code(price_currency) if price_currency else None,
                            unit_price,
                        )
                    )
                clock = entry.meta.get("time", "00:00")
                if (
                    not isinstance(clock, str)
                    or len(clock) not in {5, 8}
                    or clock[2] != ":"
                    or (len(clock) == 8 and clock[5] != ":")
                ):
                    raise self._entry_error(
                        entry, 'Transaction time must be a quoted "HH:MM" or "HH:MM:SS".'
                    )
                try:
                    parsed = time.fromisoformat(clock)
                    if parsed.tzinfo is not None:
                        raise ValueError("offset not allowed")
                except ValueError:
                    raise self._entry_error(
                        entry, 'Transaction time must be a valid local "HH:MM" or "HH:MM:SS".'
                    ) from None
                transactions.append(
                    Transaction(
                        transaction_identity(entry, len(transactions)),
                        entry.date,
                        datetime.combine(entry.date, parsed).isoformat(),
                        entry.payee or "",
                        entry.narration or "",
                        tuple(postings),
                        "external",
                        links=tuple(entry.links),
                    )
                )
                transaction_entries.append(entry)
            else:
                raise self._entry_error(
                    entry, "This directive requires unsupported accounting semantics."
                )
        ledger = Ledger(
            tuple(accounts),
            tuple(categories),
            tuple(transactions),
            equity_opened,
            tuple(dict.fromkeys(operating_currencies)),
            adjustments_opened,
        )
        inferred = tuple(
            replace(transaction, kind=_infer_transaction_kind(transaction, ledger))
            for transaction in ledger.transactions
        )
        linked_expenses: dict[str, list[Transaction]] = {}
        for transaction in inferred:
            if transaction.kind == "expense":
                for link in transaction.links:
                    linked_expenses.setdefault(link, []).append(transaction)
        resolved = []
        for transaction in inferred:
            if transaction.kind == "reimbursement":
                candidates = {
                    expense.id
                    for link in transaction.links
                    for expense in linked_expenses.get(link, [])
                    if expense.date <= transaction.date
                    and any(
                        debit.account == credit.account and debit.currency == credit.currency
                        for debit in expense.postings
                        for credit in transaction.postings
                        if debit.account.startswith("Expenses:")
                    )
                }
                if len(candidates) == 1:
                    transaction = replace(transaction, related_expense=candidates.pop())
            resolved.append(transaction)
        ledger = replace(ledger, transactions=tuple(resolved))
        try:
            ledger.balances(today=self.today())
        except LedgerError as exc:
            # Validate prefixes to locate the first unsupported economic movement.
            for index, entry in enumerate(transaction_entries):
                try:
                    replace(ledger, transactions=ledger.transactions[: index + 1]).balances(
                        today=self.today()
                    )
                except LedgerError:
                    raise self._entry_error(entry, str(exc)) from None
            raise
        return ledger

    @staticmethod
    def _entry_error(entry: models.RawTreeModel, reason: str) -> LedgerError:
        reason = {
            "exchange_invalid": (
                "Invalid exchange postings. Use one explicit positive conversion price "
                "and matching source, destination and optional fee amounts."
            ),
            "crud_transaction_readonly": (
                "Unsupported posting structure. Use explicit balanced movements between "
                "declared accounts; complex booking requires the official engine."
            ),
        }.get(reason, reason)
        line = entry.token_store.get_position(entry.first_token).line + 1
        return LedgerError(
            f"Line {line} ({type(entry).__name__} directive): {reason} "
            "Original bytes preserved; saving blocked."
        )

    @staticmethod
    def _validate_entry(entry: models.RawTreeModel) -> None:
        if isinstance(entry, models.Option):
            if entry.key not in {"title", "operating_currency"}:
                raise LedgerError("Unsupported accounting option; use the official engine.")
            if entry.key == "operating_currency":
                currency_code(entry.value)
        if isinstance(entry, models.Open):
            if entry.booking is not None:
                raise LedgerError("Lot booking requires the official engine.")
            name = entry.meta.get("name", entry.account)
            if not isinstance(name, str):
                raise LedgerError("Account name metadata must be a string.")
            display_name(name)
            for currency in entry.currencies:
                currency_code(currency)
            if entry.account.startswith(("Assets:", "Liabilities:", "Expenses:", "Income:")):
                account_path(entry.account, entry.account.split(":")[0])
        if isinstance(entry, models.Transaction):
            for posting in entry.postings:
                if isinstance(posting, models.BlockComment):
                    continue
                if posting.number is None or posting.currency is None or posting.cost is not None:
                    raise LedgerError(
                        "Posting inference and cost lots require the official engine."
                    )
                currency_code(posting.currency)
                if posting.price is not None:
                    if posting.price.number is None or posting.price.currency is None:
                        raise LedgerError("Exchange prices need explicit amounts and currencies.")
                    currency_code(posting.price.currency)
        if isinstance(
            entry, (models.Include, models.Close, models.Balance, models.Pad, models.Plugin)
        ):
            raise LedgerError("Unsupported accounting semantics; use the official engine.")

    def document(self, source: str) -> models.File:
        self.parse(source)
        document = self.parser.parse(source, models.File)
        if render(document) != source:
            raise LedgerError("crud_lossless")
        return document


def replace_directive(
    source: str,
    entry: models.RawTreeModel,
    change: Callable[[], None] | None,
) -> str:
    """Splice only the selected directive; all other bytes remain untouched."""
    before = render(entry)
    position = entry.token_store.get_position(entry.first_token)
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: position.line]) + position.column
    if source[start : start + len(before)] != before:
        raise LedgerError("crud_lossless")
    if change is None:
        newline = "\r\n" if "\r\n" in source else "\n"
        after = newline.join(
            token.raw_text
            for token in entry.tokens
            if isinstance(token, (models.BlockComment, models.InlineComment))
        )
    else:
        change()
        after = render(entry)
    return source[:start] + after + source[start + len(before) :]


def transaction_identity(entry: models.Transaction, index: int) -> str:
    """Source position, meaningful only with the unchanged draft baseline."""
    line = entry.token_store.get_position(entry.first_token).line + 1
    return f"source-{index}-line-{line}"


def transaction_time(timestamp: str) -> str:
    clock = datetime.fromisoformat(timestamp).time()
    return clock.isoformat(timespec="seconds" if clock.second else "minutes")


def open_account(account: Account) -> str:
    return render(
        models.Open.from_value(
            account.opened,
            account.id,
            [account.currency],
            meta={"name": account.name},
        )
    )


def open_category(category: Category) -> str:
    return render(
        models.Open.from_value(
            category.opened,
            category.id,
            [],
            meta={"name": category.name},
        )
    )


def transaction_text(transaction: Transaction) -> str:
    exchange = any(p.total_price is not None for p in transaction.postings)
    return render(
        models.Transaction.from_value(
            transaction.date,
            transaction.payee or None,
            transaction.narration,
            [
                models.Posting.from_value(
                    p.account,
                    p.amount.quantize(Decimal(".01")) if exchange else p.amount,
                    p.currency,
                    price=models.TotalPrice.from_value(
                        p.total_price.quantize(Decimal(".01")), p.price_currency
                    )
                    if p.total_price is not None
                    else None,
                )
                for p in transaction.postings
            ],
            meta={"time": transaction_time(transaction.timestamp)},
            links=transaction.links,
        )
    )


def _infer_transaction_kind(transaction: Transaction, ledger: Ledger) -> str:
    if len(transaction.postings) == 2:
        postings = transaction.postings
        accounts = {account.id for account in ledger.accounts}
        ordinary = [posting for posting in postings if posting.account in accounts]
        for equity, kind in ((EQUITY, "opening"), (ADJUSTMENTS, "adjustment")):
            if (
                len(ordinary) == 1
                and {posting.account for posting in postings} == {ordinary[0].account, equity}
                and sum((posting.amount for posting in postings), Decimal(0)) == 0
            ):
                return kind
        if len(ordinary) == 1:
            account = ordinary[0]
            category = next(posting for posting in postings if posting is not account)
            if category.account.startswith("Expenses:"):
                if account.amount < 0 < category.amount:
                    return "expense"
                if category.amount < 0 < account.amount:
                    return "reimbursement"
            if category.account.startswith("Income:") and category.amount < 0 < account.amount:
                return "income"
    try:
        ledger.transfer_details(transaction)
    except LedgerError:
        return "external"
    return "transfer"


def equity_declaration(opened: date) -> str:
    return render(models.Open.from_value(opened, EQUITY, []))
