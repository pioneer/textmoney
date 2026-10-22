"""Local conflict evidence and conservative, lossless transaction reconciliation."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from autobean_refactor import models

from app.domain.ledger import LedgerError
from app.domain.storage import ConflictError, digest
from app.infrastructure.beancount_journal import JournalCodec, append, render
from app.storage import StorageError

DIRECTORY = ".txncount-sync"
SHA = r"[a-f0-9]{64}"


class SyncConflict(ConflictError):
    pass


@runtime_checkable
class SyncFolder(Protocol):
    async def sync_scan(self) -> dict[str, bytes]: ...
    async def sync_put(self, name: str, data: bytes) -> None: ...
    async def sync_expect(self, versions: dict[str, str]) -> None: ...


def auxiliary_name(name: str) -> bool:
    return (
        re.fullmatch(
            rf"(?:{SHA}\.beancount|(?:edge|conflict)-{SHA}-{SHA}\.json|(?:ack|head)-{SHA}\.json)",
            name,
        )
        is not None
    )


def file_hash(data: bytes) -> str:
    value = digest(data)
    assert value is not None
    return value


def json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def unchanged_extension(previous: bytes, current: bytes) -> bool:
    if not current.startswith(previous):
        return False
    try:
        codec = JournalCodec()
        old = codec.document(previous.decode("utf-8"))
        new = codec.document(current.decode("utf-8"))
    except (LedgerError, UnicodeDecodeError):
        return False  # Unprovable extensions require review, not inferred ancestry.
    before = [render(entry) for entry in old.raw_directives]
    after = [render(entry) for entry in new.raw_directives]
    return before == after[: len(before)]


@dataclass(frozen=True)
class TransactionChunk:
    identifier: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Book:
    source: str
    transactions: dict[str, TransactionChunk]
    skeleton: str
    descriptions: dict[str, str]

    @classmethod
    def read(cls, source: str) -> "Book":
        document = JournalCodec().document(source)
        chunks = {}
        skeleton = source
        for entry in document.raw_directives:
            if not isinstance(entry, models.Transaction):
                continue
            identifier = entry.meta.get("id")
            if not isinstance(identifier, str):
                raise LedgerError("sync_manual")
            try:
                UUID(identifier)
            except ValueError as exc:
                raise LedgerError("sync_manual") from exc
            if any(
                isinstance(token, (models.BlockComment, models.InlineComment))
                for token in entry.tokens
            ):
                raise LedgerError("sync_manual")
            text = render(entry)
            position = entry.token_store.get_position(entry.first_token)
            start = sum(len(line) for line in source.splitlines(keepends=True)[: position.line])
            start += position.column
            if source[start : start + len(text)] != text or identifier in chunks:
                raise LedgerError("sync_manual")
            chunks[identifier] = TransactionChunk(identifier, text, start, start + len(text))
        for chunk in sorted(chunks.values(), key=lambda item: item.start, reverse=True):
            skeleton = skeleton[: chunk.start] + skeleton[chunk.end :]
        # Only blank separator lines may differ. Other comments/directives must match exactly.
        skeleton = "".join(line for line in skeleton.splitlines(keepends=True) if line.strip())
        ledger = JournalCodec().parse(source)
        descriptions = {
            item.id: (
                f"{item.date.isoformat()} · {item.timestamp}\n"
                f"{item.payee} — {item.narration}\n"
                + "\n".join(
                    f"{p.account}: {p.amount} {p.currency}"
                    + (
                        (
                            f" @ {p.unit_price}"
                            if p.unit_price is not None
                            else f" @@ {p.total_price}"
                        )
                        + f" {p.price_currency}"
                        if p.has_price
                        else ""
                    )
                    for p in item.postings
                )
            )
            for item in ledger.transactions
        }
        return cls(source, chunks, skeleton, descriptions)


@dataclass(frozen=True)
class Difference:
    identifier: str
    local: str | None
    remote: str | None
    base: str | None
    automatic: str | None
    kind: str


@dataclass(frozen=True)
class MergeReview:
    local: Book
    remote: Book
    base: Book | None
    differences: tuple[Difference, ...]
    compatible: bool

    @property
    def automatic(self) -> bool:
        return (
            self.base is not None
            and self.compatible
            and all(item.automatic is not None for item in self.differences)
        )

    def candidate(self, choices: Mapping[str, str]) -> str:
        if not self.compatible:
            raise LedgerError("sync_manual")
        desired = {identifier: chunk.text for identifier, chunk in self.local.transactions.items()}
        for item in self.differences:
            choice = choices.get(item.identifier, item.automatic)
            if choice not in {"local", "remote"}:
                raise LedgerError("sync_choose")
            value = item.local if choice == "local" else item.remote
            if value is None:
                desired.pop(item.identifier, None)
            else:
                desired[item.identifier] = value
        result = self.local.source
        for chunk in sorted(
            self.local.transactions.values(), key=lambda item: item.start, reverse=True
        ):
            result = result[: chunk.start] + desired.get(chunk.identifier, "") + result[chunk.end :]
        for identifier, chunk in self.remote.transactions.items():
            if identifier not in self.local.transactions and identifier in desired:
                result = append(result, chunk.text)
        JournalCodec().document(result)
        return result


def review_versions(local: str, remote: str, base: str | None) -> MergeReview:
    left, right = Book.read(local), Book.read(remote)
    ancestor = None if base is None else Book.read(base)
    compatible = left.skeleton == right.skeleton and (
        ancestor is None or left.skeleton == ancestor.skeleton
    )
    differences = []
    identifiers = sorted(set(left.transactions) | set(right.transactions))
    if ancestor is not None:
        identifiers = sorted(set(identifiers) | set(ancestor.transactions))
    for identifier in identifiers:
        a = left.transactions.get(identifier)
        b = right.transactions.get(identifier)
        old = None if ancestor is None else ancestor.transactions.get(identifier)
        a_text, b_text, old_text = (None if item is None else item.text for item in (a, b, old))
        if a_text == b_text:
            continue
        automatic = None
        if ancestor is not None:
            if a_text == old_text:
                automatic = "remote"
            elif b_text == old_text:
                automatic = "local"
        kind = (
            "added"
            if ancestor is not None and old is None
            else "deleted"
            if a is None or b is None
            else "modified"
        )
        differences.append(Difference(identifier, a_text, b_text, old_text, automatic, kind))
    return MergeReview(left, right, ancestor, tuple(differences), compatible)


@dataclass(frozen=True)
class SyncState:
    files: dict[str, bytes]
    versions: dict[str, bytes]
    edges: tuple[tuple[str, str], ...]
    acknowledged: frozenset[tuple[str, str]]

    @classmethod
    def read(cls, files: dict[str, bytes], current: bytes) -> "SyncState":
        edges = []
        conflicts = []
        heads = set()
        acknowledged = set()
        acknowledgments = []
        for name, data in files.items():
            if not name.startswith(f"{DIRECTORY}/"):
                continue
            leaf = name.removeprefix(f"{DIRECTORY}/")
            if not auxiliary_name(leaf):
                raise StorageError("sync_metadata")
            if leaf.endswith(".beancount"):
                if file_hash(data) != leaf.removesuffix(".beancount"):
                    raise StorageError("sync_metadata")
                continue
            try:
                value = json.loads(data)
            except (ValueError, UnicodeDecodeError) as exc:
                raise StorageError("sync_metadata") from exc
            if leaf.startswith("head-"):
                head = leaf.removeprefix("head-").removesuffix(".json")
                if value != {"head": head} or f"{DIRECTORY}/{head}.beancount" not in files:
                    raise StorageError("sync_metadata")
                heads.add(head)
            elif leaf.startswith(("edge-", "conflict-")):
                parts = leaf.removesuffix(".json").split("-")
                if value != {"parent": parts[1], "child": parts[2]}:
                    raise StorageError("sync_metadata")
                if any(f"{DIRECTORY}/{part}.beancount" not in files for part in parts[1:]):
                    raise StorageError("sync_metadata")
                if leaf.startswith("edge-"):
                    edges.append((parts[1], parts[2]))
                else:
                    conflicts.append(parts[1])
            else:
                if not isinstance(value, dict) or set(value) != {"name", "hash", "result"}:
                    raise StorageError("sync_metadata")
                if file_hash(data) != leaf.removeprefix("ack-").removesuffix(".json"):
                    raise StorageError("sync_metadata")
                name_value, hash_value, result = value["name"], value["hash"], value["result"]
                if not all(isinstance(item, str) for item in (name_value, hash_value, result)):
                    raise StorageError("sync_metadata")
                if not re.fullmatch(SHA, hash_value) or not re.fullmatch(SHA, result):
                    raise StorageError("sync_metadata")
                if f"{DIRECTORY}/{result}.beancount" not in files:
                    raise StorageError("sync_metadata")
                acknowledgments.append((name_value, hash_value, result))
        ancestry = {file_hash(current)}
        while True:
            expanded = ancestry | {parent for parent, child in edges if child in ancestry}
            if expanded == ancestry:
                break
            ancestry = expanded
        acknowledged.update(
            (name, version) for name, version, result in acknowledgments if result in ancestry
        )
        # Shared UUIDs identify renamed copies independently of vendor-specific naming.
        ids = set(re.findall(rb'\bid:\s*"([a-fA-F0-9-]{36})"', current))
        known_hashes = {part for edge in edges for part in edge}
        versions = {}
        # Authored branch tips survive a later synchronization replacement, even
        # when the replacement happened while this device was closed.
        tips = {
            head
            for head in heads
            if not any(head != other and head in ancestors(edges, other) for other in heads)
        }
        for old in set(conflicts) | (tips - ancestry):
            previous = files[f"{DIRECTORY}/{old}.beancount"]
            if old not in conflicts:
                observed = [current] + [
                    files[f"{DIRECTORY}/{version}.beancount"]
                    for version in ancestry
                    if f"{DIRECTORY}/{version}.beancount" in files
                ]
                if any(unchanged_extension(previous, value) for value in observed):
                    continue
            name = f"previous-{old}"
            if (name, old) not in acknowledged and old != file_hash(current):
                versions[name] = previous
        for name, data in files.items():
            if "/" in name or not name.lower().endswith(".beancount"):
                continue
            same_ids = ids.intersection(re.findall(rb'\bid:\s*"([a-fA-F0-9-]{36})"', data))
            recognizable = any(
                word in name.casefold()
                for word in ("txncount", "conflict", "conflicted", "конфлікт", "конфликт")
            )
            if not recognizable and not same_ids and file_hash(data) not in known_hashes:
                continue
            if (name, file_hash(data)) not in acknowledged:
                versions[name] = data
        return cls(files, versions, tuple(edges), frozenset(acknowledged))

    def common_base(self, local: bytes, remote: bytes) -> str | None:
        left = ancestors(self.edges, file_hash(local))
        right = ancestors(self.edges, file_hash(remote))
        common = left & right
        # Multiple incomparable bases require manual reconciliation, never a timestamp guess.
        latest = [
            item
            for item in common
            if not any(item != other and item in ancestors(self.edges, other) for other in common)
        ]
        if len(latest) != 1:
            return None
        data = self.files.get(f"{DIRECTORY}/{latest[0]}.beancount")
        return None if data is None else data.decode("utf-8")


async def preserve(folder: SyncFolder, data: bytes) -> None:
    await folder.sync_put(f"{file_hash(data)}.beancount", data)


def ancestors(edges: list[tuple[str, str]] | tuple[tuple[str, str], ...], start: str) -> set[str]:
    found = {start}
    while True:
        expanded = found | {parent for parent, child in edges if child in found}
        if expanded == found:
            return found
        found = expanded


async def record_head(folder: SyncFolder, candidate: bytes) -> None:
    value = file_hash(candidate)
    await folder.sync_put(f"head-{value}.json", json_bytes({"head": value}))


async def record_revision(
    folder: SyncFolder, previous: bytes, candidate: bytes
) -> dict[str, bytes]:
    await preserve(folder, previous)
    await preserve(folder, candidate)
    parent, child = file_hash(previous), file_hash(candidate)
    files = {
        f"{DIRECTORY}/{parent}.beancount": previous,
        f"{DIRECTORY}/{child}.beancount": candidate,
    }
    if parent != child:
        name = f"edge-{parent}-{child}.json"
        data = json_bytes({"parent": parent, "child": child})
        await folder.sync_put(name, data)
        files[f"{DIRECTORY}/{name}"] = data
    return files
