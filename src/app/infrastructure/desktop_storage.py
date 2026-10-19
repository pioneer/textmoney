import asyncio
import os
from pathlib import Path
from uuid import uuid4

from app.storage import StorageError

TARGET = "storage-test.beancount"


class DesktopFolder:
    def __init__(self, path: str, filename: str = TARGET) -> None:
        if path.startswith("content:"):
            raise StorageError("A content URI requires Android SAF, not filesystem access.")
        self.root = Path(path)
        if filename not in {TARGET, "txncount.beancount"}:
            raise StorageError("Unsupported journal filename.")
        self.filename = filename
        self.backup_prefix = "storage-test" if filename == TARGET else "txncount"

    def _target(self) -> Path:
        if not self.root.is_dir():
            raise StorageError("Selected folder is unavailable. Select Folder again.")
        target = self.root / self.filename
        if target.is_symlink():
            raise StorageError("Refusing a symlink test file; use a regular synthetic file.")
        return target

    async def status(self) -> str:
        self._target()
        return "Desktop folder available; access is checked on each operation."

    def _read(self) -> bytes | None:
        try:
            return self._target().read_bytes()
        except FileNotFoundError:
            if not self.root.is_dir():
                raise StorageError("Selected folder disappeared. Select Folder again.") from None
            return None
        except OSError as exc:
            raise StorageError(
                f"Read failed ({type(exc).__name__}). Check folder permissions."
            ) from exc

    async def read(self) -> bytes | None:
        return await asyncio.to_thread(self._read)

    def _backup(self, data: bytes) -> str:
        try:
            self._target()
            directory = self.root / "backups"
            if directory.is_symlink():
                raise StorageError("Refusing a symlink backups folder.")
            directory.mkdir(exist_ok=True)
            name = f"{self.backup_prefix}-{uuid4().hex}.beancount"
            path = directory / name
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != data:
                raise StorageError("Backup verification failed. Original not modified.")
            return f"backups/{name}"
        except OSError as exc:
            raise StorageError(
                f"Backup failed ({type(exc).__name__}). Original not modified; "
                "check free space/access."
            ) from exc

    async def backup(self, data: bytes) -> str:
        return await asyncio.to_thread(self._backup, data)

    def _write(self, data: bytes) -> None:
        try:
            path = self._target()
            with path.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise StorageError(
                f"Write failed ({type(exc).__name__}). Inspect test file and latest backup."
            ) from exc

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self._write, data)
