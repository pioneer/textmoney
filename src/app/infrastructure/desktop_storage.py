import asyncio
import os
from pathlib import Path

from app.domain.backups import BackupEntry, backup_name, backup_time
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
            name = backup_name(self.backup_prefix)
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

    def _backup_directory(self) -> Path:
        self._target()
        directory = self.root / "backups"
        if directory.is_symlink():
            raise StorageError("Refusing a symlink backups folder.")
        return directory

    def _commit_backup(self, path: str) -> str:
        name = path.removeprefix("backups/")
        if path != f"backups/{name}" or backup_time(name, self.backup_prefix, pending=True) is None:
            raise StorageError("Invalid pending backup name.")
        try:
            directory = self._backup_directory()
            original = directory / name
            destination = directory / name.replace("-pending.", "-committed.")
            if original.is_symlink() or not original.is_file() or destination.exists():
                raise StorageError("Backup finalization refused; recovery copy retained.")
            original.rename(destination)
            return f"backups/{destination.name}"
        except OSError as exc:
            raise StorageError("Backup finalization failed; inspect recovery copies.") from exc

    async def commit_backup(self, path: str) -> str:
        return await asyncio.to_thread(self._commit_backup, path)

    def _list_backups(self) -> list[BackupEntry]:
        try:
            directory = self._backup_directory()
            if not directory.exists():
                return []
            entries = []
            for path in directory.iterdir():
                created = backup_time(path.name, self.backup_prefix)
                if created is not None and not path.is_symlink() and path.is_file():
                    entries.append(BackupEntry(path.name, path.stat().st_size, created))
            return entries
        except OSError as exc:
            raise StorageError("Could not list backups. Check folder access.") from exc

    async def list_backups(self) -> list[BackupEntry]:
        return await asyncio.to_thread(self._list_backups)

    def _delete_backup(self, entry: BackupEntry) -> None:
        if backup_time(entry.name, self.backup_prefix) != entry.created:
            raise StorageError("Refusing to delete an unrecognized backup.")
        try:
            path = self._backup_directory() / entry.name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != entry.size:
                raise StorageError("Backup changed during cleanup. Review backups and retry.")
            path.unlink()
        except OSError as exc:
            raise StorageError("Could not delete old backup. Check folder access.") from exc

    async def delete_backup(self, entry: BackupEntry) -> None:
        await asyncio.to_thread(self._delete_backup, entry)

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
