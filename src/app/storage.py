"""Shared storage policy. No Flet or Android dependencies."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from app.domain.backups import BackupEntry, BackupPlan, retention_plan
from app.domain.recovery import RecoveryState
from app.domain.storage import ConflictError, digest

logger = logging.getLogger(__name__)
SAMPLE = (
    '; Synthetic storage diagnostics only\noption "title" "textmoney"\n'
    "2026-01-01 open Assets:Test USD\n"
    "2026-01-01 open Equity:Test USD\n"
    '2026-01-02 * "Synthetic test"\n'
    "  Assets:Test  1.00 USD\n  Equity:Test\n"
)


class StorageError(Exception):
    """An actionable storage operation failure, safe to display and log."""


class WriteError(StorageError):
    """A write failure with an explicit commit outcome."""

    def __init__(self, message: str, *, committed: bool | None) -> None:
        super().__init__(message)
        self.committed = committed


class RecoveryRequired(StorageError):
    """A persistent save record needs explicit review before further writes."""


@runtime_checkable
class RecoveryFolder(Protocol):
    async def inspect_recovery(self) -> RecoveryState: ...
    async def write_guarded(
        self, data: bytes, *, expected_hash: str | None, backup: str | None
    ) -> None: ...
    async def finish_write(self) -> None: ...
    async def accept_recovery(self, state: RecoveryState) -> None: ...


class Folder(Protocol):
    async def status(self) -> str: ...
    async def read(self) -> bytes | None: ...
    async def backup(self, data: bytes) -> str: ...
    async def write(self, data: bytes, *, expected_hash: str | None) -> None: ...
    async def commit_backup(self, path: str) -> str: ...
    async def list_backups(self) -> list[BackupEntry]: ...
    async def delete_backup(self, entry: BackupEntry) -> None: ...


class StorageSession:
    def __init__(self, folder: Folder, missing_text: str = SAMPLE) -> None:
        self.folder = folder
        self.missing_text = missing_text
        self.draft = missing_text
        self.loaded = False
        self.last_hash: str | None = None
        self.external_changes = False
        self.auto_cleanup = True
        self.cleanup_error = False
        self.recovery_backup: str | None = None
        self.recovery: RecoveryState | None = None

    async def read(self) -> bool:
        if isinstance(self.folder, RecoveryFolder):
            self.loaded = False
            self.recovery = await self.folder.inspect_recovery()
            if self.recovery.required:
                raise RecoveryRequired("recovery_required")
        data = await self.folder.read()
        try:
            text = self.missing_text if data is None else data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StorageError("File is not UTF-8. Draft retained; file not modified.") from exc
        self.draft = text
        self.last_hash = digest(data)
        self.loaded = True
        self.external_changes = False
        logger.info("File read (exists=%s)", data is not None)
        return data is not None

    async def poll(self) -> bool:
        if self.loaded and digest(await self.folder.read()) != self.last_hash:
            if not self.external_changes:
                logger.info("External file change detected")
            self.external_changes = True
        return self.external_changes

    async def _checked_read(self) -> bytes | None:
        if not self.loaded:
            raise StorageError("Read first to establish a baseline before writing or backing up.")
        data = await self.folder.read()
        if digest(data) != self.last_hash:
            self.external_changes = True
            raise ConflictError(
                "Conflict: file changed externally. Draft retained. Read to reload."
            )
        return data

    async def backup(self) -> str:
        previous = await self._checked_read()
        if previous is None:
            raise StorageError("No file exists to back up. Original is untouched.")
        result = await self.folder.backup(previous)
        logger.info("Verified file backup created")
        return result

    async def write(self) -> str | None:
        self.cleanup_error = False
        data = self.draft.encode("utf-8")
        previous = await self._checked_read()
        backup = None if previous is None else await self.folder.backup(previous)
        self.recovery_backup = backup
        # Detect modifications during backup too; providers offer no compare-and-swap.
        await self._checked_read()
        try:
            if isinstance(self.folder, RecoveryFolder):
                await self.folder.write_guarded(data, expected_hash=self.last_hash, backup=backup)
            else:
                await self.folder.write(data, expected_hash=self.last_hash)
            if await self.folder.read() != data:
                raise StorageError("Write read-back differs. Inspect file and backup.")
        except asyncio.CancelledError:
            self.loaded = False
            raise
        except ConflictError:
            self.external_changes = True
            self.loaded = False
            raise
        except WriteError as exc:
            self.loaded = False
            raise WriteError(
                f"{exc} Read before retrying. Recovery: {backup or 'no previous file'}.",
                committed=exc.committed,
            ) from exc
        except (StorageError, TimeoutError) as exc:
            self.loaded = False
            raise StorageError(
                f"Write failed or uncertain. Read before retrying. "
                f"Recovery: {backup or 'no previous file'}. {exc}"
            ) from exc
        self.last_hash = digest(data)
        self.external_changes = False
        logger.info("File write verified (previous backup=%s)", backup is not None)
        try:
            if backup is not None:
                backup = await self.folder.commit_backup(backup)
                self.recovery_backup = backup
            if self.auto_cleanup:
                await self.cleanup_backups()
        except (StorageError, ConflictError, TimeoutError):
            self.cleanup_error = True
            logger.exception("Journal saved, but backup maintenance failed")
        if isinstance(self.folder, RecoveryFolder):
            try:
                await self.folder.finish_write()
            except (StorageError, TimeoutError, asyncio.CancelledError):
                self.loaded = False
                raise
        return backup

    async def preview_cleanup(self) -> BackupPlan:
        await self._checked_read()
        plan = retention_plan(await self.folder.list_backups(), datetime.now(UTC))
        return BackupPlan(
            tuple(item for item in plan.remove if f"backups/{item.name}" != self.recovery_backup)
        )

    async def cleanup_backups(self, approved: BackupPlan | None = None) -> int:
        plan = await self.preview_cleanup()
        allowed = None if approved is None else set(approved.remove)
        removed = 0
        for entry in plan.remove:
            if f"backups/{entry.name}" == self.recovery_backup:
                continue
            if allowed is not None and entry not in allowed:
                continue
            await self._checked_read()
            await self.folder.delete_backup(entry)
            removed += 1
        if removed:
            logger.info("Old backup cleanup completed (count=%d)", removed)
        return removed
