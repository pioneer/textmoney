"""Lossless reading, directive splicing and generation with the phone-tested parser."""

import io
from collections.abc import Callable
from datetime import date, datetime

from autobean_refactor import models, printer
from autobean_refactor.parser import Parser
from lark.exceptions import UnexpectedInput

from app.domain.ledger import (
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
        equity_opened = None
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
                if entry.account.startswith("Assets:"):
                    account_path(entry.account, "Assets")
                    if len(currencies) != 1:
                        raise LedgerError(
                            "Each ordinary account needs exactly one declared currency."
                        )
                    accounts.append(
                        Account(
                            entry.account, name, currency_code(currencies[0]), entry.date, active
                        )
                    )
                elif entry.account.startswith("Expenses:"):
                    account_path(entry.account, "Expenses")
                    if currencies:
                        raise LedgerError(
                            "MVP expense category declarations must not restrict currencies."
                        )
                    categories.append(Category(entry.account, name, entry.date, active))
                elif entry.account == EQUITY:
                    if currencies:
                        raise LedgerError(
                            "Opening-balance equity must not restrict currencies in MVP 0.1."
                        )
                    equity_opened = entry.date
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
                        or posting.price is not None
                    ):
                        raise LedgerError(
                            "Inference, costs or FX need a full engine. "
                            "Saving blocked; journal preserved."
                        )
                    postings.append(
                        Posting(posting.account, posting.number, currency_code(posting.currency))
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
                    )
                )
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
        )
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
    return render(
        models.Transaction.from_value(
            transaction.date,
            transaction.payee or None,
            transaction.narration,
            [
                models.Posting.from_value(p.account, p.amount, p.currency)
                for p in transaction.postings
            ],
            meta={
                "id": transaction.id,
                "timestamp": transaction.timestamp,
                "txncount-kind": transaction.kind,
            },
        )
    )


def equity_declaration(opened: date) -> str:
    return render(models.Open.from_value(opened, EQUITY, []))
