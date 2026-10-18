"""Shared storage policy. No Flet or Android dependencies."""

import logging
from typing import Protocol

from app.domain.storage import ConflictError, digest

logger = logging.getLogger(__name__)
SAMPLE = (
    '; Synthetic storage PoC only\noption "title" "txncount"\n'
    "2026-01-01 open Assets:Test USD\n"
    "2026-01-01 open Equity:Test USD\n"
    '2026-01-02 * "Synthetic test"\n'
    "  Assets:Test  1.00 USD\n  Equity:Test\n"
)


class StorageError(Exception):
    """An actionable storage operation failure, safe to display and log."""


class Folder(Protocol):
    async def status(self) -> str: ...
    async def read(self) -> bytes | None: ...
    async def backup(self, data: bytes) -> str: ...
    async def write(self, data: bytes) -> None: ...


class StorageSession:
    def __init__(self, folder: Folder, missing_text: str = SAMPLE) -> None:
        self.folder = folder
        self.missing_text = missing_text
        self.draft = missing_text
        self.loaded = False
        self.last_hash: str | None = None
        self.external_changes = False

    async def read(self) -> bool:
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
        data = self.draft.encode("utf-8")
        previous = await self._checked_read()
        backup = None if previous is None else await self.folder.backup(previous)
        # Detect modifications during backup too; providers offer no compare-and-swap.
        await self._checked_read()
        try:
            await self.folder.write(data)
            if await self.folder.read() != data:
                raise StorageError("Write read-back differs. Inspect file and backup.")
        except StorageError as exc:
            self.loaded = False
            raise StorageError(
                f"Write failed or uncertain. Read before retrying. "
                f"Recovery: {backup or 'no previous file'}. {exc}"
            ) from exc
        self.last_hash = digest(data)
        self.external_changes = False
        logger.info("File write verified (previous backup=%s)", backup is not None)
        return backup
