"""Journal-scoped defaults with immutable, explicitly reviewed revisions."""

import asyncio
import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Protocol

from app.domain.storage import ConflictError
from app.infrastructure.preferences import FeePreferences, PreferencesError, parse_default_account
from app.infrastructure.record_files import RECORD_NAME, RecordPolicy
from app.storage import StorageError

MAX_RECORD = 64 * 1024
MAX_RECORDS = 4096
MAX_TOTAL = 4 * 1024 * 1024
DefaultValue = str | None | FeePreferences | tuple[str, ...]


class SharedDefaultsError(PreferencesError):
    pass


POLICY = RecordPolicy(
    "textmoney-settings",
    "settings",
    MAX_RECORD,
    MAX_RECORDS,
    MAX_TOTAL,
    SharedDefaultsError,
    "shared_settings_invalid",
    "shared_settings_limit",
    "shared_settings_storage",
)


class DefaultsFiles(Protocol):
    async def read_records(self) -> dict[str, bytes]: ...
    async def create_record(self, name: str, data: bytes) -> None: ...


def _value(group: str, value: object) -> DefaultValue:
    if group == "account":
        if value == "":
            raise SharedDefaultsError("shared_settings_invalid")
        return parse_default_account(value)
    if group == "commission":
        return FeePreferences.parse(json.dumps(value))
    if group == "budget_order":
        if (
            not isinstance(value, (list, tuple))
            or len(value) > MAX_RECORDS
            or any(
                not isinstance(item, str)
                or not re.fullmatch(
                    r"(?:Assets|Liabilities|Equity|Income|Expenses)(?::[A-Z][A-Za-z0-9-]*)*",
                    item,
                )
                for item in value
            )
            or len(set(value)) != len(value)
        ):
            raise SharedDefaultsError("shared_settings_invalid")
        return tuple(value)
    raise SharedDefaultsError("shared_settings_invalid")


def _heads(versions: Mapping[str, tuple[tuple[str, ...], DefaultValue]]) -> tuple[str, ...]:
    remaining = {name: set(parents) for name, (parents, _) in versions.items()}
    ancestors = set().union(*remaining.values())
    if ancestors - versions.keys():
        raise SharedDefaultsError("shared_settings_incomplete")
    while remaining:
        roots = {name for name, parents in remaining.items() if not parents}
        if not roots:
            raise SharedDefaultsError("shared_settings_invalid")
        remaining = {
            name: parents - roots for name, parents in remaining.items() if name not in roots
        }
    return tuple(sorted(versions.keys() - ancestors))


class SharedDefaults:
    groups = frozenset({"account", "commission"})

    def __init__(self, files: DefaultsFiles) -> None:
        self.files = files
        self.records: dict[str, bytes] = {}
        self.heads: dict[str, tuple[str, ...]] = {}
        self.values: dict[str, DefaultValue] = {}
        self.conflicts: dict[str, tuple[DefaultValue, ...]] = {}
        self.loaded = False
        self.lock = asyncio.Lock()

    async def load(self) -> bool:
        async with self.lock:
            return await self._load()

    async def _load(self) -> bool:
        records = await self.files.read_records()
        if self.loaded and records == self.records:
            return False
        if len(records) > MAX_RECORDS or sum(map(len, records.values())) > MAX_TOTAL:
            raise SharedDefaultsError("shared_settings_limit")
        revisions: dict[str, dict[str, tuple[tuple[str, ...], DefaultValue]]] = {}
        try:
            for name, data in records.items():
                if (
                    not RECORD_NAME.fullmatch(name)
                    or len(data) > MAX_RECORD
                    or sha256(data).hexdigest() + ".json" != name
                ):
                    raise SharedDefaultsError("shared_settings_invalid")
                record = json.loads(data)
                if (
                    not isinstance(record, dict)
                    or set(record) != {"version", "changes"}
                    or type(record["version"]) is not int
                    or record["version"] != 1
                    or not isinstance(record["changes"], dict)
                    or not record["changes"]
                    or not set(record["changes"]) <= self.groups
                ):
                    raise SharedDefaultsError("shared_settings_invalid")
                for group, change in record["changes"].items():
                    if (
                        not isinstance(change, dict)
                        or set(change) != {"parents", "value"}
                        or not isinstance(change["parents"], list)
                        or len(change["parents"]) > MAX_RECORDS
                        or any(
                            not isinstance(p, str) or not RECORD_NAME.fullmatch(p)
                            for p in change["parents"]
                        )
                        or len(set(change["parents"])) != len(change["parents"])
                    ):
                        raise SharedDefaultsError("shared_settings_invalid")
                    revisions.setdefault(group, {})[name] = (
                        tuple(change["parents"]),
                        _value(group, change["value"]),
                    )
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError, PreferencesError) as exc:
            if isinstance(exc, SharedDefaultsError):
                raise
            raise SharedDefaultsError("shared_settings_invalid") from exc
        heads = {group: _heads(versions) for group, versions in revisions.items()}
        values: dict[str, DefaultValue] = {}
        conflicts: dict[str, tuple[DefaultValue, ...]] = {}
        for group, names in heads.items():
            choices = tuple(revisions[group][name][1] for name in names)
            if any(choice != choices[0] for choice in choices):
                conflicts[group] = tuple(dict.fromkeys(choices))
            else:
                values[group] = choices[0]
        self.records, self.heads, self.values, self.conflicts = records, heads, values, conflicts
        self.loaded = True
        return True

    async def save(self, group: str, value: DefaultValue, expected: tuple[str, ...]) -> None:
        await self.save_changes({group: value}, {group: expected})

    async def save_changes(
        self, changes: dict[str, DefaultValue], expected: dict[str, tuple[str, ...]]
    ) -> None:
        async with self.lock:
            if not changes or not changes.keys() <= self.groups:
                raise SharedDefaultsError("shared_settings_invalid")
            await self._load()
            if any(self.heads.get(group, ()) != expected.get(group) for group in changes):
                raise ConflictError("shared_settings_stale")
            if all(
                group in self.values and self.values[group] == value
                for group, value in changes.items()
            ):
                return
            serialized = {}
            for group, value in changes.items():
                encoded = (
                    json.loads(value.serialize()) if isinstance(value, FeePreferences) else value
                )
                _value(group, encoded)
                serialized[group] = {"parents": self.heads.get(group, ()), "value": encoded}
            data = json.dumps(
                {"version": 1, "changes": serialized}, sort_keys=True, separators=(",", ":")
            ).encode()
            name = sha256(data).hexdigest() + ".json"
            if len(data) > MAX_RECORD or (
                name not in self.records
                and (
                    len(self.records) >= MAX_RECORDS
                    or sum(map(len, self.records.values())) + len(data) > MAX_TOTAL
                )
            ):
                raise SharedDefaultsError("shared_settings_limit")
            try:
                await self.files.create_record(name, data)
                await self._load()
                if any(
                    group not in self.values
                    or self.values[group] != value
                    or group in self.conflicts
                    for group, value in changes.items()
                ):
                    raise SharedDefaultsError("shared_settings_conflicts")
            except (PreferencesError, StorageError, OSError, TimeoutError) as exc:
                raise SharedDefaultsError("shared_settings_write_uncertain") from exc
