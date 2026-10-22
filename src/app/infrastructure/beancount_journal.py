"""Lossless reading, directive splicing and generation with the phone-tested parser."""

import io
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from autobean_refactor import models, printer
from autobean_refactor.parser import Parser
from lark.exceptions import UnexpectedInput

from app.domain.ledger import (
    ADJUSTMENTS,
    BANK_FEES,
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
        except ArithmeticError as exc:
            raise LedgerError(
                "Invalid monetary expression. Saving blocked; journal preserved."
            ) from exc

    def _parse(self, text: str) -> Ledger:
        try:
            file = self.parser.parse(text, models.File)
        except (UnexpectedInput, ValueError) as exc:
            raise LedgerError(
                "Journal has unsupported or invalid syntax. "
                "Original bytes preserved; saving blocked."
            ) from exc
        accounts: list[Account] = []
        categories: list[Category] = []
        transactions: list[Transaction] = []
        infer_kind_indices: list[int] = []
        equity_opened = None
        adjustments_opened = None
        operating_currencies: list[str] = []
        default_fee_category = None
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
            if isinstance(entry, safe):
                continue
            if isinstance(entry, models.Option):
                if entry.key not in {"title", "operating_currency"}:
                    raise LedgerError(
                        "Unsupported accounting option. Saving blocked; journal preserved."
                    )
                if entry.key == "operating_currency":
                    operating_currencies.append(currency_code(entry.value))
            elif isinstance(entry, models.Open):
                if entry.account in names:
                    raise LedgerError("Duplicate account declaration. Journal unchanged.")
                names.add(entry.account)
                if entry.booking is not None:
                    raise LedgerError("Lot booking is not supported in MVP 0.1.")
                name = entry.meta.get("display-name", entry.account)
                if not isinstance(name, str):
                    raise LedgerError("Account display-name metadata must be a string.")
                name = display_name(name)
                active = entry.meta.get("txncount-active", True)
                if not isinstance(active, bool):
                    raise LedgerError("crud_active_invalid")
                currencies = list(entry.currencies)
                if entry.account.startswith(("Assets:", "Liabilities:")):
                    account_path(entry.account, entry.account.split(":")[0])
                    if len(currencies) != 1:
                        raise LedgerError(
                            "Each ordinary account needs exactly one declared currency."
                        )
                    accounts.append(
                        Account(
                            entry.account, name, currency_code(currencies[0]), entry.date, active
                        )
                    )
                elif entry.account.startswith(("Expenses:", "Income:")):
                    account_path(entry.account, entry.account.split(":")[0])
                    if currencies:
                        raise LedgerError(
                            "MVP expense category declarations must not restrict currencies."
                        )
                    categories.append(Category(entry.account, name, entry.date, active))
                    default_fee = entry.meta.get("txncount-default-transfer-fee", False)
                    if not isinstance(default_fee, bool):
                        raise LedgerError("transfer_category")
                    if default_fee:
                        if (
                            not entry.account.startswith("Expenses:")
                            or default_fee_category is not None
                        ):
                            raise LedgerError("transfer_category")
                        default_fee_category = entry.account
                elif entry.account in {EQUITY, ADJUSTMENTS}:
                    if currencies:
                        raise LedgerError(
                            "Opening-balance equity must not restrict currencies in MVP 0.1."
                        )
                    if entry.account == EQUITY:
                        equity_opened = entry.date
                    else:
                        adjustments_opened = entry.date
                else:
                    raise LedgerError(
                        "This journal contains account types outside MVP 0.1. Saving blocked."
                    )
            elif isinstance(entry, models.Transaction):
                postings: list[Posting] = []
                for posting in entry.postings:
                    if isinstance(posting, models.BlockComment):
                        continue
                    if (
                        posting.number is None
                        or posting.currency is None
                        or posting.cost is not None
                        or (
                            posting.price is not None
                            and not isinstance(posting.price, models.TotalPrice)
                        )
                    ):
                        raise LedgerError(
                            "Inference, costs or unit prices need a full engine. "
                            "Saving blocked; journal preserved."
                        )
                    price = posting.price
                    if price is not None and (price.number is None or price.currency is None):
                        raise LedgerError("exchange_invalid")
                    postings.append(
                        Posting(
                            posting.account,
                            posting.number,
                            currency_code(posting.currency),
                            price.number if price else None,
                            currency_code(price.currency) if price and price.currency else None,
                        )
                    )
                identifier = entry.meta.get("id")
                persistent_id = identifier is not None
                if identifier is None:
                    # Stable source identity for external transactions without application metadata.
                    identifier = (
                        f"external-line-{entry.token_store.get_position(entry.first_token).line}"
                    )
                if not isinstance(identifier, str) or not identifier:
                    raise LedgerError("Transaction IDs must be nonempty strings.")
                stamp = entry.meta.get("timestamp", f"{entry.date.isoformat()}T00:00:00+00:00")
                if not isinstance(stamp, str):
                    raise LedgerError("Timestamp metadata must be an ISO string with a timezone.")
                try:
                    parsed = datetime.fromisoformat(stamp)
                    if parsed.utcoffset() is None:
                        raise ValueError("timezone missing")
                except ValueError as exc:
                    raise LedgerError(
                        "Timestamp metadata must be an ISO string with a timezone."
                    ) from exc
                kind = entry.meta.get("txncount-kind", "external")
                if not isinstance(kind, str):
                    raise LedgerError("Transaction txncount-kind metadata must be a string.")
                related = entry.meta.get("txncount-expense-id")
                if related is not None and (not isinstance(related, str) or not related):
                    raise LedgerError("incoming_reference")
                fee_mode = entry.meta.get("txncount-fee-mode", "added")
                if not isinstance(fee_mode, str):
                    raise LedgerError("transfer_mode")
                transactions.append(
                    Transaction(
                        identifier,
                        entry.date,
                        stamp,
                        entry.payee or "",
                        entry.narration or "",
                        tuple(postings),
                        kind,
                        persistent_id,
                        related,
                        fee_mode,
                    )
                )
                if "txncount-kind" not in entry.meta:
                    infer_kind_indices.append(len(transactions) - 1)
            else:
                raise LedgerError(
                    f"{type(entry).__name__} requires unsupported engine semantics. "
                    "Journal preserved; saving blocked."
                )
        ledger = Ledger(
            tuple(accounts),
            tuple(categories),
            tuple(transactions),
            equity_opened,
            tuple(dict.fromkeys(operating_currencies)),
            adjustments_opened,
            default_fee_category or BANK_FEES,
        )
        if infer_kind_indices:
            inferred = list(ledger.transactions)
            for index in infer_kind_indices:
                inferred[index] = replace(
                    inferred[index], kind=_infer_transaction_kind(inferred[index], ledger)
                )
            ledger = replace(ledger, transactions=tuple(inferred))
        ledger.balances(today=self.today())
        return ledger

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


def open_account(account: Account) -> str:
    return render(
        models.Open.from_value(
            account.opened,
            account.id,
            [account.currency],
            meta={"display-name": account.name},
        )
    )


def open_category(category: Category) -> str:
    return render(
        models.Open.from_value(
            category.opened,
            category.id,
            [],
            meta={"display-name": category.name},
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
            meta={
                "id": transaction.id,
                "timestamp": transaction.timestamp,
                **(
                    {"txncount-fee-mode": transaction.fee_mode}
                    if transaction.kind == "transfer"
                    else {}
                ),
                **(
                    {"txncount-expense-id": transaction.related_expense}
                    if transaction.related_expense
                    else {}
                ),
            },
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
