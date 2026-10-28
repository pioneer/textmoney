"""Explicit, copy-only transfer from an archived TxnCount development journal."""

import argparse
import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

from autobean_refactor import models
from autobean_refactor.parser import Parser
from lark.exceptions import UnexpectedInput

from app.domain.ledger import LedgerError
from app.domain.recovery import SaveRecord
from app.domain.storage import digest
from app.infrastructure.beancount_journal import JournalCodec, render

INCOMPLETE = ".textmoney-migration-incomplete"
OLD_FILE = "txncount.beancount"
NEW_FILE = "textmoney.beancount"
PENDING = re.compile(r"txncount-\d{8}T\d{12}Z-[a-f0-9]{32}-pending\.beancount")
KEYS = (
    "language.v1",
    "theme.v1",
    "timezone.v1",
    "backup-cleanup.v1",
    "default-fee-category.v1",
)


class MigrationError(RuntimeError):
    pass


def migrate_source(source: str) -> str:
    try:
        document = Parser().parse(source, models.File)
    except (UnexpectedInput, ValueError):
        raise MigrationError(
            "Invalid legacy syntax. Fix the archived copy before migration."
        ) from None
    if render(document) != source:
        raise MigrationError("Legacy source did not round-trip exactly; no output written.")
    active: dict[str, bool] = {}
    for entry in document.raw_directives:
        if not isinstance(entry, models.Open) or "txncount-active" not in entry.meta:
            continue
        if "textmoney-active" in entry.meta:
            raise MigrationError("Both visibility keys exist. Resolve them before migration.")
        value = entry.meta["txncount-active"]
        if not isinstance(value, bool):
            raise MigrationError("Legacy visibility metadata must be boolean.")
        active[entry.account] = value
        for item in entry.raw_meta:
            if item.key == "txncount-active":
                item.key = "textmoney-active"

    candidate = render(document)
    codec = JournalCodec()
    before = codec.parse(source)
    expected = replace(
        before,
        accounts=tuple(replace(a, active=active.get(a.id, a.active)) for a in before.accounts),
        categories=tuple(replace(c, active=active.get(c.id, c.active)) for c in before.categories),
    )
    if codec.parse(candidate) != expected:
        raise MigrationError("Migration changed accounting data; no output written.")
    return candidate


def _read(path: Path) -> bytes:
    _reject_symlinks(path)
    if not path.is_file():
        raise MigrationError("Migration input must be an existing regular file, not a symlink.")
    return path.read_bytes()


def _reject_symlinks(path: Path) -> None:
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise MigrationError("Use direct paths without symlinks or directory junctions.")


def _recovery_snapshot(root: Path) -> tuple[bytes | None, tuple[str, ...]]:
    marker = root / f".{OLD_FILE}.recovery.json"
    if marker.is_symlink():
        raise MigrationError("Recovery metadata cannot be a symlink.")
    data = _read(marker) if marker.exists() else None
    backups = root / "backups"
    _reject_symlinks(backups)
    if backups.is_symlink() or (backups.exists() and not backups.is_dir()):
        raise MigrationError("Backups must be a regular directory.")
    pending = (
        tuple(sorted(p.name for p in backups.iterdir() if PENDING.fullmatch(p.name)))
        if backups.exists()
        else ()
    )
    if data is None:
        if pending:
            raise MigrationError("Pending backups need recovery review in TxnCount first.")
        return data, pending
    try:
        record = json.loads(data)
        if not isinstance(record, dict):
            raise ValueError("Invalid record")
        checksum = record.pop("checksum", None)
        if digest(json.dumps(record, sort_keys=True).encode()) != checksum:
            raise ValueError("Invalid checksum")
        backup = record.get("backup")
        if isinstance(backup, str):
            if not backup.startswith("backups/txncount-"):
                raise ValueError("Invalid legacy backup")
            record["backup"] = backup.replace("backups/txncount-", "backups/textmoney-", 1)
        acknowledged = record.get("pending")
        if not isinstance(acknowledged, list) or not all(
            isinstance(name, str) and PENDING.fullmatch(name) for name in acknowledged
        ):
            raise ValueError("Invalid pending inventory")
        record["pending"] = [name.replace("txncount-", "textmoney-", 1) for name in acknowledged]
        record["checksum"] = digest(json.dumps(record, sort_keys=True).encode())
        parsed = SaveRecord.decode(json.dumps(record).encode(), "textmoney")
    except (ValueError, UnicodeDecodeError) as exc:
        raise MigrationError("Damaged recovery metadata. Review it in TxnCount first.") from exc
    if parsed.phase == "prepared" or set(pending) - set(acknowledged):
        raise MigrationError("Unresolved save. Complete recovery review in TxnCount first.")
    return data, pending


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if _read(path) != data:
        raise MigrationError("Migration read-back failed. Output is incomplete; source unchanged.")


def migrate_journal(source: Path, destination: Path) -> None:
    _reject_symlinks(source)
    _reject_symlinks(destination)
    if source.is_symlink() or not source.is_dir():
        raise MigrationError("Source must be a regular journal directory.")
    source = source.resolve()
    if destination.is_symlink() or destination.exists():
        raise MigrationError("Destination must not exist; nothing will be overwritten.")
    destination = destination.resolve()
    if source == destination or source in destination.parents:
        raise MigrationError("Destination must be outside the original journal folder.")
    if (source / NEW_FILE).exists() or (source / INCOMPLETE).exists():
        raise MigrationError("Ambiguous or incomplete source folder. Review it before migration.")
    original = _read(source / OLD_FILE)
    recovery = _recovery_snapshot(source)
    candidate = migrate_source(original.decode("utf-8")).encode("utf-8")
    # The marker prevents a partial copy from being opened as an active journal.
    destination.mkdir(mode=0o700)
    _write_new(
        destination / INCOMPLETE, b"Migration incomplete. Original folder was not modified.\n"
    )
    _write_new(destination / NEW_FILE, candidate)
    if _read(source / OLD_FILE) != original or _recovery_snapshot(source) != recovery:
        raise MigrationError(
            "Source changed during migration. Output blocked; retry in a new folder."
        )
    copied = _read(destination / NEW_FILE)
    if copied != candidate:
        raise MigrationError("Destination changed during migration. Output blocked; review it.")
    JournalCodec().parse(copied.decode("utf-8"))
    (destination / INCOMPLETE).unlink()


def migrate_preferences(source: Path, destination: Path, journal_folder: Path) -> None:
    _reject_symlinks(destination)
    _reject_symlinks(journal_folder)
    if destination.is_symlink() or destination.exists():
        raise MigrationError("Settings destination must not exist; nothing will be overwritten.")
    if (journal_folder / INCOMPLETE).exists():
        raise MigrationError("Journal migration is incomplete.")
    JournalCodec().parse(_read(journal_folder / NEW_FILE).decode("utf-8"))
    original = _read(source)
    values = json.loads(original)
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
    ):
        raise MigrationError("Legacy settings must be a string-to-string object.")
    renamed = {
        f"textmoney.{key}": values[f"txncount.{key}"] for key in KEYS if f"txncount.{key}" in values
    }
    if any(key.startswith("textmoney.") for key in values):
        raise MigrationError("Settings contain both application namespaces; review them first.")
    renamed["textmoney.folder.v1"] = str(journal_folder.resolve())
    if _read(source) != original:
        raise MigrationError("Settings changed during migration; retry after closing the old app.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_new(destination, (json.dumps(renamed, ensure_ascii=False, indent=2) + "\n").encode())
    if _read(source) != original:
        raise MigrationError("Settings changed during copying. Review the output before using it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("journal", "preferences"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--journal-folder", type=Path)
    args = parser.parse_args()
    if args.kind == "preferences" and args.journal_folder is None:
        parser.error("--journal-folder is required for preferences")
    try:
        if args.kind == "journal":
            migrate_journal(args.source, args.destination)
        else:
            migrate_preferences(args.source, args.destination, args.journal_folder)
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except LedgerError as exc:
        print(f"Journal validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError) as exc:
        print(
            f"Migration failed ({type(exc).__name__}). No source was modified. "
            "Do not use partial output; resolve data, access or destination problems, "
            "then retry in a new destination. Private input and paths are withheld.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print("Migration verified. Source and old recovery history are unchanged.")


if __name__ == "__main__":
    main()
