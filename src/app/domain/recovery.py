"""Hash-only SAF save records; never an alternative financial ledger."""

import json
import re
from dataclasses import dataclass

from app.domain.backups import backup_time
from app.domain.storage import digest


@dataclass(frozen=True)
class SaveRecord:
    previous: str | None
    candidate: str
    backup: str | None
    phase: str = "prepared"
    pending: tuple[str, ...] = ()

    def encode(self) -> bytes:
        value = {
            "version": 1,
            "previous": self.previous,
            "candidate": self.candidate,
            "backup": self.backup,
            "phase": self.phase,
            "pending": list(self.pending),
        }
        checksum = digest(json.dumps(value, sort_keys=True).encode("utf-8"))
        return json.dumps(value | {"checksum": checksum}, sort_keys=True).encode("utf-8")

    @classmethod
    def decode(cls, data: bytes, prefix: str) -> "SaveRecord":
        value = json.loads(data)
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value.get("version") != 1
            or set(value)
            != {"version", "previous", "candidate", "backup", "phase", "pending", "checksum"}
        ):
            raise ValueError("Invalid save record")
        checksum = value.pop("checksum", None)
        if checksum != digest(json.dumps(value, sort_keys=True).encode("utf-8")):
            raise ValueError("Corrupted save record")
        previous, candidate, backup, phase = (
            value.get(key) for key in ("previous", "candidate", "backup", "phase")
        )
        sha256 = re.compile(r"[a-f0-9]{64}")
        if previous is not None and (
            not isinstance(previous, str) or not sha256.fullmatch(previous)
        ):
            raise ValueError("Invalid previous hash")
        if not isinstance(candidate, str) or not sha256.fullmatch(candidate):
            raise ValueError("Invalid candidate hash")
        if not isinstance(phase, str) or phase not in {"prepared", "complete", "resolved"}:
            raise ValueError("Invalid save phase")
        if backup is not None and (
            not isinstance(backup, str)
            or not backup.startswith("backups/")
            or all(
                backup_time(backup.removeprefix("backups/"), prefix, pending=pending) is None
                for pending in (False, True)
            )
        ):
            raise ValueError("Invalid backup identifier")
        if previous is not None and backup is None:
            raise ValueError("Missing previous-version backup")
        pending = value.get("pending")
        if not isinstance(pending, list) or not all(
            isinstance(name, str) and backup_time(name, prefix, pending=True) is not None
            for name in pending
        ):
            raise ValueError("Invalid protected pending backups")
        return cls(previous, candidate, backup, phase, tuple(pending))


@dataclass(frozen=True)
class RecoveryState:
    current: bytes | None
    marker: bytes | None
    pending: tuple[str, ...]
    record: SaveRecord | None
    backup: bytes | None
    damaged: bool = False

    @property
    def required(self) -> bool:
        if self.damaged:
            return True
        if self.record is None:
            return self.current is not None or bool(self.pending)
        return self.record.phase == "prepared" or bool(set(self.pending) - set(self.record.pending))

    @property
    def classification(self) -> str:
        if self.record is None:
            return "metadata"
        current = digest(self.current)
        if current == self.record.candidate:
            return "candidate"
        if current == self.record.previous:
            return "unchanged"
        return "unexpected"

    @property
    def backup_verified(self) -> bool:
        return (
            self.record is not None
            and self.record.previous is not None
            and self.backup is not None
            and digest(self.backup) == self.record.previous
        )
