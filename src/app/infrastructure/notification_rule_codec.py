"""Strict local serialization for notification rules."""

import json
from typing import Literal

from app.domain.notification_rules import Pattern, Rule, TransactionMapping, Value

_FIELDS = (
    "kind",
    "amount",
    "currency",
    "account",
    "destination",
    "destination_amount",
    "category",
    "payee",
    "description",
    "date",
    "time",
)


class RuleCodecError(ValueError):
    """Stored or proposed rule data is invalid."""


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuleCodecError("Duplicate rule field.")
        result[key] = value
    return result


def parse_unique_json(text: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_unique_keys)
    except json.JSONDecodeError as exc:
        raise RuleCodecError("Rule is not valid JSON.") from exc


def encode_rule(rule: Rule) -> str:
    mapping = rule.mapping
    fields = (
        {
            field: (
                None
                if (source := getattr(mapping, field)) is None
                else {"text": source.text, "captured": source.captured}
            )
            for field in _FIELDS
        }
        if mapping is not None
        else None
    )
    return json.dumps(
        {
            "version": 1,
            "name": rule.name,
            "app_id": rule.app_id,
            "action": rule.action,
            "enabled": rule.enabled,
            "title": rule.title.source if rule.title else None,
            "body": rule.body.source if rule.body else None,
            "mapping": (
                {
                    "fields": fields,
                    "decimal_separator": mapping.decimal_separator,
                    "grouping_separator": mapping.grouping_separator,
                    "date_format": mapping.date_format,
                    "currency_symbols": list(mapping.currency_symbols),
                }
                if mapping is not None
                else None
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _value(value: object) -> Value | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"text", "captured"}
        or not isinstance(value["text"], str)
        or not value["text"]
        or len(value["text"]) > 512
        or type(value["captured"]) is not bool
    ):
        raise RuleCodecError("Invalid rule field mapping.")
    return Value(value["text"], value["captured"])


def _date_format(value: object) -> Literal["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"]:
    if value == "%Y-%m-%d":
        return "%Y-%m-%d"
    if value == "%d.%m.%Y":
        return "%d.%m.%Y"
    if value == "%d/%m/%Y":
        return "%d/%m/%Y"
    if value == "%m/%d/%Y":
        return "%m/%d/%Y"
    raise RuleCodecError("Unsupported rule date format.")


def _mapping(value: object) -> TransactionMapping | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "fields",
        "decimal_separator",
        "grouping_separator",
        "date_format",
        "currency_symbols",
    }:
        raise RuleCodecError("Invalid rule mapping.")
    fields = value["fields"]
    symbols = value["currency_symbols"]
    if not isinstance(fields, dict) or set(fields) != set(_FIELDS):
        raise RuleCodecError("Invalid rule fields.")
    if not isinstance(value["decimal_separator"], str) or (
        value["grouping_separator"] is not None and not isinstance(value["grouping_separator"], str)
    ):
        raise RuleCodecError("Invalid number format.")
    if (
        not isinstance(symbols, list)
        or len(symbols) > 50
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(item, str) or len(item) > 32 for item in pair)
            for pair in symbols
        )
    ):
        raise RuleCodecError("Invalid currency symbol mappings.")
    return TransactionMapping(
        kind=_value(fields["kind"]),
        amount=_value(fields["amount"]),
        currency=_value(fields["currency"]),
        account=_value(fields["account"]),
        destination=_value(fields["destination"]),
        destination_amount=_value(fields["destination_amount"]),
        category=_value(fields["category"]),
        payee=_value(fields["payee"]),
        description=_value(fields["description"]),
        date=_value(fields["date"]),
        time=_value(fields["time"]),
        decimal_separator=value["decimal_separator"],
        grouping_separator=value["grouping_separator"],
        date_format=_date_format(value["date_format"]),
        currency_symbols=tuple((pair[0], pair[1]) for pair in symbols),
    )


def decode_rule(text: str) -> Rule:
    if len(text.encode("utf-8")) > 64 * 1024:
        raise RuleCodecError("Rule exceeds the supported size.")
    value = parse_unique_json(text)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"version", "name", "app_id", "action", "enabled", "title", "body", "mapping"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["name"], str)
        or len(value["name"]) > 120
        or not isinstance(value["app_id"], str)
        or len(value["app_id"]) > 1024
        or type(value["enabled"]) is not bool
        or value["action"] not in ("suggest", "ignore")
        or any(
            value[field] is not None and not isinstance(value[field], str)
            for field in ("title", "body")
        )
    ):
        raise RuleCodecError("Invalid rule.")
    action: Literal["suggest", "ignore"] = "suggest" if value["action"] == "suggest" else "ignore"
    return Rule(
        value["name"],
        value["app_id"],
        action,
        title=Pattern.parse(value["title"]) if value["title"] is not None else None,
        body=Pattern.parse(value["body"]) if value["body"] is not None else None,
        enabled=value["enabled"],
        mapping=_mapping(value["mapping"]),
    )
