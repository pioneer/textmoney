"""Bounded, deterministic notification-rule matching without platform dependencies."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from app.domain.ledger import (
    Ledger,
    LedgerError,
    currency_code,
    description_text,
    display_name,
    money,
)

MAX_PATTERN_LENGTH = 512
MAX_NOTIFICATION_LENGTH = 4096
MAX_CAPTURES = 8
_CAPTURE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")


class NotificationRuleError(ValueError):
    """A rule or extracted value needs user correction."""


@dataclass(frozen=True)
class Value:
    """A fixed value or the name of a captured field."""

    text: str
    captured: bool = False

    def resolve(self, captures: dict[str, str]) -> str | None:
        return captures.get(self.text) if self.captured else self.text


@dataclass(frozen=True)
class TransactionMapping:
    kind: Value | None = None
    amount: Value | None = None
    currency: Value | None = None
    account: Value | None = None
    destination: Value | None = None
    destination_amount: Value | None = None
    category: Value | None = None
    payee: Value | None = None
    description: Value | None = None
    date: Value | None = None
    time: Value | None = None
    decimal_separator: str = "."
    grouping_separator: str | None = None
    date_format: Literal["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"] = "%Y-%m-%d"
    currency_symbols: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.date_format not in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
            raise NotificationRuleError("Unsupported date format.")
        if len({symbol for symbol, _ in self.currency_symbols}) != len(self.currency_symbols):
            raise NotificationRuleError("Currency symbols must be unique.")
        if any(
            not symbol or not re.fullmatch(r"[A-Z]{3}", code)
            for symbol, code in self.currency_symbols
        ):
            raise NotificationRuleError("Currency symbol mappings need explicit currency codes.")
        # Validate the configured separators even when the amount is not yet available.
        parse_amount(
            "1",
            decimal_separator=self.decimal_separator,
            grouping_separator=self.grouping_separator,
        )


@dataclass(frozen=True)
class _Capture:
    name: str


@dataclass(frozen=True, init=False)
class Pattern:
    """Whole-field literal pattern with named ``{capture}`` placeholders.

    Captures end at the first following literal, or at the end of the field.
    Double braces represent literal braces. Adjacent captures are not allowed.
    """

    source: str
    tokens: tuple[str | _Capture, ...]

    def __init__(self, source: str) -> None:
        if not source or len(source) > MAX_PATTERN_LENGTH:
            raise NotificationRuleError("Pattern must contain 1-512 characters.")
        tokens: list[str | _Capture] = []
        literal: list[str] = []
        names: set[str] = set()
        pos = 0
        while pos < len(source):
            char = source[pos]
            if char in "{}" and source[pos : pos + 2] == char * 2:
                literal.append(char)
                pos += 2
            elif char == "{":
                end = source.find("}", pos + 1)
                name = source[pos + 1 : end] if end != -1 else ""
                if not _CAPTURE_NAME.fullmatch(name):
                    raise NotificationRuleError("Invalid capture name or unclosed brace.")
                if name in names:
                    raise NotificationRuleError("Capture names must be unique.")
                if len(names) >= MAX_CAPTURES:
                    raise NotificationRuleError("A pattern may contain at most 8 captures.")
                if literal:
                    tokens.append("".join(literal))
                    literal.clear()
                elif tokens and isinstance(tokens[-1], _Capture):
                    raise NotificationRuleError("Adjacent captures need a literal separator.")
                tokens.append(_Capture(name))
                names.add(name)
                pos = end + 1
            elif char == "}":
                raise NotificationRuleError("Unescaped closing brace.")
            else:
                literal.append(char)
                pos += 1
        if literal:
            tokens.append("".join(literal))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "tokens", tuple(tokens))

    @classmethod
    def parse(cls, source: str) -> "Pattern":
        return cls(source)

    def match(self, text: str) -> dict[str, str] | None:
        if len(text) > MAX_NOTIFICATION_LENGTH:
            raise NotificationRuleError("Notification field exceeds the supported size.")
        captures: dict[str, str] = {}
        position = 0
        for index, token in enumerate(self.tokens):
            if isinstance(token, str):
                if not text.startswith(token, position):
                    return None
                position += len(token)
                continue
            if index + 1 == len(self.tokens):
                end = len(text)
            else:
                delimiter = self.tokens[index + 1]
                if not isinstance(delimiter, str):
                    raise NotificationRuleError("Adjacent captures need a literal separator.")
                end = text.find(delimiter, position)
                if end == -1:
                    return None
            if end == position:
                return None
            captures[token.name] = text[position:end]
            position = end
        return captures if position == len(text) else None


@dataclass(frozen=True)
class Rule:
    name: str
    app_id: str
    action: Literal["suggest", "ignore"]
    title: Pattern | None = None
    body: Pattern | None = None
    enabled: bool = True
    mapping: TransactionMapping | None = None

    def __post_init__(self) -> None:
        if (
            not self.name.strip()
            or len(self.name) > 120
            or any(ord(char) < 32 for char in self.name)
            or not self.app_id.strip()
            or len(self.app_id) > 1024
            or any(ord(char) < 32 for char in self.app_id)
        ):
            raise NotificationRuleError("Rule name and source app are required.")
        if self.action not in ("suggest", "ignore"):
            raise NotificationRuleError("Unknown rule action.")
        if self.title is None and self.body is None:
            raise NotificationRuleError("A title or body pattern is required.")
        title_names = (
            {token.name for token in self.title.tokens if isinstance(token, _Capture)}
            if (self.title is not None)
            else set()
        )
        body_names = (
            {token.name for token in self.body.tokens if isinstance(token, _Capture)}
            if (self.body is not None)
            else set()
        )
        if title_names & body_names:
            raise NotificationRuleError("Title and body captures must have distinct names.")
        if self.action == "ignore" and self.mapping is not None:
            raise NotificationRuleError("Ignore rules do not map transaction fields.")

    def missing_captures(self) -> tuple[str, ...]:
        if self.mapping is None:
            return ()
        names = {
            token.name
            for pattern in (self.title, self.body)
            if pattern is not None
            for token in pattern.tokens
            if isinstance(token, _Capture)
        }
        sources = (
            self.mapping.kind,
            self.mapping.amount,
            self.mapping.currency,
            self.mapping.account,
            self.mapping.destination,
            self.mapping.destination_amount,
            self.mapping.category,
            self.mapping.payee,
            self.mapping.description,
            self.mapping.date,
            self.mapping.time,
        )
        return tuple(
            sorted({source.text for source in sources if source and source.captured} - names)
        )


@dataclass(frozen=True)
class Match:
    rule: Rule
    captures: dict[str, str]


@dataclass(frozen=True)
class Draft:
    kind: Literal["expense", "income", "transfer"] | None
    amount: Decimal | None
    currency: str | None
    account: str | None
    destination: str | None
    destination_amount: Decimal | None
    category: str | None
    payee: str | None
    description: str | None
    date: date | None
    time: str | None
    errors: tuple[tuple[str, str], ...]


def first_match(
    rules: tuple[Rule, ...], app_id: str, title: str | None, body: str | None
) -> Match | None:
    """Return the first enabled matching rule for this app, including ignore rules."""
    for rule in rules:
        if not rule.enabled or rule.app_id != app_id:
            continue
        captures: dict[str, str] = {}
        for pattern, text in ((rule.title, title), (rule.body, body)):
            if pattern is None:
                continue
            if text is None:
                break
            matched = pattern.match(text)
            if matched is None:
                break
            captures.update(matched)
        else:
            return Match(rule, captures)
    return None


def parse_amount(text: str, *, decimal_separator: str, grouping_separator: str | None) -> Decimal:
    """Parse an explicitly configured number format without guessing currency or direction."""
    if decimal_separator not in (".", ","):
        raise NotificationRuleError("Decimal separator must be a period or comma.")
    if grouping_separator not in (None, ".", ",", " ", "\u00a0", "\u202f"):
        raise NotificationRuleError("Unsupported grouping separator.")
    if grouping_separator == decimal_separator:
        raise NotificationRuleError("Decimal and grouping separators must differ.")
    value = text.strip()
    if not value or len(value) > 32:
        raise NotificationRuleError("Invalid amount.")
    sign = ""
    if value[0] in "+-":
        sign, value = value[0], value[1:]
    parts = value.split(decimal_separator)
    if len(parts) > 2 or not parts[0] or (len(parts) == 2 and not parts[1]):
        raise NotificationRuleError("Invalid decimal amount.")
    integer = parts[0]
    fraction = parts[1] if len(parts) == 2 else ""
    if grouping_separator and grouping_separator in integer:
        groups = integer.split(grouping_separator)
        if not (1 <= len(groups[0]) <= 3) or any(len(group) != 3 for group in groups[1:]):
            raise NotificationRuleError("Invalid digit grouping.")
        integer = "".join(groups)
    if not re.fullmatch(r"[0-9]+", integer) or (
        fraction and not re.fullmatch(r"[0-9]{1,2}", fraction)
    ):
        raise NotificationRuleError("Invalid amount digits or precision.")
    try:
        result = money(integer + ("." + fraction if fraction else ""))
    except LedgerError as exc:
        raise NotificationRuleError("Amount exceeds the supported range or precision.") from exc
    return -result if sign == "-" else result


def preview_transaction(match: Match, ledger: Ledger, *, today: date) -> Draft:
    """Extract a review draft without guessing missing fields or writing a journal."""
    if match.rule.action != "suggest":
        raise NotificationRuleError("An ignored notification has no transaction draft.")
    mapping = match.rule.mapping or TransactionMapping()
    errors: list[tuple[str, str]] = []

    def value(field: str, source: Value | None, *, required: bool = False) -> str | None:
        if source is None:
            if required:
                errors.append((field, "Required for import."))
            return None
        result = source.resolve(match.captures)
        if result is None or not result.strip():
            errors.append((field, "Mapped value is missing."))
            return None
        return result.strip()

    raw_kind = value("kind", mapping.kind, required=True)
    kind: Literal["expense", "income", "transfer"] | None = None
    if raw_kind in ("expense", "income", "transfer"):
        kind = raw_kind
    elif raw_kind is not None:
        errors.append(("kind", "Choose expense, income, or transfer."))

    raw_amount = value("amount", mapping.amount, required=True)
    amount: Decimal | None = None
    if raw_amount is not None:
        try:
            amount = parse_amount(
                raw_amount,
                decimal_separator=mapping.decimal_separator,
                grouping_separator=mapping.grouping_separator,
            )
            if amount <= 0:
                raise NotificationRuleError("Amount must be positive.")
        except NotificationRuleError as exc:
            errors.append(("amount", str(exc)))
            amount = None

    raw_currency = value("currency", mapping.currency, required=True)
    currency: str | None = None
    if raw_currency is not None:
        try:
            currency = currency_code(dict(mapping.currency_symbols).get(raw_currency, raw_currency))
        except LedgerError:
            errors.append(("currency", "Map the currency to a three-letter code."))

    raw_date = value("date", mapping.date, required=True)
    transaction_date: date | None = None
    if raw_date is not None:
        try:
            transaction_date = datetime.strptime(raw_date, mapping.date_format).date()
            if transaction_date < date(1970, 1, 1) or transaction_date > today:
                errors.append(("date", "Date must be between 1970-01-01 and today."))
                transaction_date = None
        except ValueError:
            errors.append(("date", "Date does not match the configured format."))

    raw_time = value("time", mapping.time)
    transaction_time: str | None = None
    if raw_time is not None:
        try:
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", raw_time):
                raise ValueError
            time.fromisoformat(raw_time)
            transaction_time = raw_time
        except ValueError:
            errors.append(("time", "Time must be a local HH:MM or HH:MM:SS value."))

    def account_value(field: str, source: Value | None, *, required: bool) -> str | None:
        identifier = value(field, source, required=required)
        if identifier is None:
            return None
        account = next((item for item in ledger.accounts if item.id == identifier), None)
        if account is None or not account.active:
            errors.append((field, "Account is missing or inactive in the selected journal."))
        elif transaction_date is not None and transaction_date < account.opened:
            errors.append((field, "Account is not open on this date."))
        elif field == "account" and currency is not None and account.currency != currency:
            errors.append((field, "Account currency does not match the extracted currency."))
        return identifier

    account = account_value("account", mapping.account, required=True)
    destination = account_value("destination", mapping.destination, required=kind == "transfer")
    if kind == "transfer" and account and destination and account == destination:
        errors.append(("destination", "Transfer accounts must be different."))

    target_account = next((item for item in ledger.accounts if item.id == destination), None)
    source_account = next((item for item in ledger.accounts if item.id == account), None)
    is_exchange = (
        kind == "transfer"
        and source_account is not None
        and target_account is not None
        and source_account.id != target_account.id
        and currency == source_account.currency
        and target_account.currency != currency
    )
    raw_destination_amount = value(
        "destination_amount", mapping.destination_amount, required=is_exchange
    )
    destination_amount: Decimal | None = None
    if raw_destination_amount is not None:
        try:
            destination_amount = parse_amount(
                raw_destination_amount,
                decimal_separator=mapping.decimal_separator,
                grouping_separator=mapping.grouping_separator,
            )
            if destination_amount <= 0:
                raise NotificationRuleError("Amount must be positive.")
            if (
                kind == "transfer"
                and not is_exchange
                and amount is not None
                and amount != destination_amount
            ):
                raise NotificationRuleError("Same-currency transfers must have matching amounts.")
        except NotificationRuleError as exc:
            errors.append(("destination_amount", str(exc)))
            destination_amount = None

    category = value("category", mapping.category, required=kind in ("expense", "income"))
    if category is not None:
        category_entry = next((item for item in ledger.categories if item.id == category), None)
        root = "Expenses:" if kind == "expense" else "Income:" if kind == "income" else None
        if (
            category_entry is None
            or not ledger.category_active(category)
            or (root is not None and not category.startswith(root))
        ):
            errors.append(("category", "Category is unavailable for this transaction type."))
        elif transaction_date is not None and transaction_date < category_entry.opened:
            errors.append(("category", "Category is not open on this date."))

    payee = value("payee", mapping.payee)
    if payee is not None:
        try:
            payee = display_name(payee)
        except LedgerError:
            errors.append(("payee", "Payee is invalid."))
    description = value("description", mapping.description)
    if description is not None:
        try:
            description = description_text(description)
        except LedgerError:
            errors.append(("description", "Description is invalid."))
    return Draft(
        kind,
        amount,
        currency,
        account,
        destination,
        destination_amount,
        category,
        payee,
        description,
        transaction_date,
        transaction_time,
        tuple(errors),
    )
