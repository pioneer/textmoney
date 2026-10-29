import asyncio
import errno
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

from app.domain.backups import BackupEntry, backup_name, backup_time
from app.domain.storage import ConflictError, digest
from app.storage import StorageError, WriteError

TARGET = "storage-test.beancount"
logger = logging.getLogger(__name__)


def _sync_directory(directory: Path) -> None:
    if sys.platform == "win32":
        logger.warning("Directory synchronization is unavailable on Windows; durability is limited")
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.ENOSYS}:
            raise
        logger.warning(
            "Filesystem does not support directory synchronization; durability is limited"
        )


def _preserve_permissions(source: Path, temporary: Path, metadata: os.stat_result) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        security = ctypes.WinDLL("advapi32", use_last_error=True)
        get_security = security.GetFileSecurityW
        get_security.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        get_security.restype = wintypes.BOOL
        set_security = security.SetFileSecurityW
        set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        set_security.restype = wintypes.BOOL
        required = wintypes.DWORD()
        # Copy the existing DACL rather than inheriting a potentially broader directory ACL.
        get_security(str(source), 4, None, 0, ctypes.byref(required))
        if not required.value:
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = ctypes.create_string_buffer(required.value)
        if not get_security(str(source), 4, descriptor, required.value, ctypes.byref(required)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not set_security(str(temporary), 4 | 0x80000000, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        current = temporary.stat()
        if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        if sys.platform == "linux":
            access_acl = "system.posix_acl_access"
            if access_acl in os.listxattr(source):
                os.setxattr(temporary, access_acl, os.getxattr(source, access_acl))
            elif access_acl in os.listxattr(temporary):
                os.removexattr(temporary, access_acl)


class DesktopFolder:
    def __init__(self, path: str, filename: str = TARGET) -> None:
        if path.startswith("content:"):
            raise StorageError("A content URI requires Android SAF, not filesystem access.")
        self.root = Path(path)
        if filename not in {TARGET, "textmoney.beancount"}:
            raise StorageError("Unsupported journal filename.")
        self.filename = filename
        self.backup_prefix = "storage-test" if filename == TARGET else "textmoney"

    def _target(self) -> Path:
        if not self.root.is_dir():
            raise StorageError("Selected folder is unavailable. Select Folder again.")
        if (
            self.filename == "textmoney.beancount"
            and (self.root / ".textmoney-migration-incomplete").exists()
        ):
            raise StorageError("Incomplete migration. Review the migration output before opening.")
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

    def _list_siblings(self) -> list[str]:
        if not self.root.is_dir():
            raise StorageError("Selected folder is unavailable. Select Folder again.")
        try:
            return sorted(
                path.name
                for path in self.root.iterdir()
                if path.name != self.filename
                and path.name.lower().endswith(".beancount")
                and path.is_file()
            )
        except OSError as exc:
            raise StorageError("Could not list sibling journals. Check folder access.") from exc

    async def list_siblings(self) -> list[str]:
        return await asyncio.to_thread(self._list_siblings)

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

    def _write(self, data: bytes, expected_hash: str | None) -> None:
        temporary: Path | None = None
        phase = "staging"
        try:
            path = self._target()
            metadata = path.stat() if path.exists() else None
            if metadata is not None:
                if not stat.S_ISREG(metadata.st_mode):
                    raise StorageError("Journal must be a regular file.")
                if not os.access(path, os.W_OK):
                    raise StorageError("Journal is read-only. Check file permissions.")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.filename}.",
                suffix=".tmp",
                dir=self.root,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                if metadata is not None:
                    _preserve_permissions(path, temporary, metadata)
                if stream.write(data) != len(data):
                    raise OSError(errno.EIO, "Incomplete temporary journal write")
                stream.flush()
                if metadata is not None:
                    os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
                os.fsync(stream.fileno())
            if digest(self._read()) != expected_hash:
                raise ConflictError(
                    "Conflict: file changed externally. Draft retained. Read to reload."
                )
            path = self._target()
            phase = "replacing"
            os.replace(temporary, path)
            phase = "replaced"
            _sync_directory(self.root)
        except (OSError, StorageError) as exc:
            committed: bool | None = None
            if phase == "staging":
                outcome = "Write failed before replacement; original not modified."
                committed = False
            elif phase == "replacing" and temporary is not None:
                outcome = (
                    "Replacement failed or uncertain; inspect the journal and staged file "
                    f"{temporary.name}."
                )
            else:
                outcome = (
                    "Journal replaced, but directory synchronization failed; "
                    "save may already be committed."
                )
                committed = True
            raise WriteError(
                f"{outcome} {exc}"
                if isinstance(exc, StorageError)
                else f"{outcome} ({type(exc).__name__})",
                committed=committed,
            ) from exc
        finally:
            if temporary is not None and phase == "staging":
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    logger.exception("Could not remove an uncommitted temporary journal")
                    raise WriteError(
                        "Temporary journal cleanup failed; original not modified. "
                        f"Inspect {temporary.name}.",
                        committed=False,
                    ) from exc

    async def write(self, data: bytes, *, expected_hash: str | None) -> None:
        await asyncio.to_thread(self._write, data, expected_hash)

    async def write_atomic(self, data: bytes, *, expected_hash: str | None) -> None:
        await self.write(data, expected_hash=expected_hash)
