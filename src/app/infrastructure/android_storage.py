import base64

from finance_saf import FinanceSaf

from app.domain.backups import BackupEntry, backup_name, backup_time
from app.storage import StorageError


class AndroidFolder:
    def __init__(
        self, bridge: FinanceSaf, uri: str, filename: str = "storage-test.beancount"
    ) -> None:
        if not uri.startswith("content://"):
            raise StorageError("Android requires a content URI returned by the SAF picker.")
        self.bridge = bridge
        self.uri = uri
        if filename not in {"storage-test.beancount", "txncount.beancount"}:
            raise StorageError("Unsupported journal filename.")
        self.filename = filename
        self.backup_prefix = "storage-test" if filename == "storage-test.beancount" else "txncount"

    async def _call(self, method: str, **arguments: object) -> object:
        result = await self.bridge.call(method, uri=self.uri, filename=self.filename, **arguments)
        return unwrap(result)

    async def status(self) -> str:
        if await self._call("status") is not True:
            raise StorageError("Persistent read/write grant missing. Select Folder again.")
        return "Android SAF: persisted read + write grant present (operations checked separately)."

    async def read(self) -> bytes | None:
        result = await self._call("read")
        if result is None:
            return None
        if not isinstance(result, str):
            raise StorageError("Invalid SAF read response.")
        try:
            return base64.b64decode(result, validate=True)
        except ValueError as exc:
            raise StorageError("Invalid SAF file data.") from exc

    async def backup(self, data: bytes) -> str:
        result = await self._call(
            "backup",
            name=backup_name(self.backup_prefix),
            data=base64.b64encode(data).decode("ascii"),
        )
        if not isinstance(result, str):
            raise StorageError("Invalid SAF backup response. Original not modified.")
        return result

    async def commit_backup(self, path: str) -> str:
        name = path.removeprefix("backups/")
        if path != f"backups/{name}" or backup_time(name, self.backup_prefix, pending=True) is None:
            raise StorageError("Invalid pending backup name.")
        result = await self._call("commit_backup", name=name)
        expected = path.replace("-pending.", "-committed.")
        if result != expected:
            raise StorageError("Invalid SAF backup finalization response. Inspect recovery copies.")
        return expected

    async def list_backups(self) -> list[BackupEntry]:
        result = await self._call("list_backups")
        if not isinstance(result, list):
            raise StorageError("Invalid SAF backup listing.")
        entries = []
        for item in result:
            if not isinstance(item, dict):
                raise StorageError("Invalid SAF backup entry.")
            name, size = item.get("name"), item.get("size")
            if not isinstance(name, str) or type(size) is not int or size < 0:
                raise StorageError("Invalid SAF backup name or size.")
            created = backup_time(name, self.backup_prefix)
            if created is not None:
                entries.append(BackupEntry(name, size, created))
        return entries

    async def delete_backup(self, entry: BackupEntry) -> None:
        if backup_time(entry.name, self.backup_prefix) != entry.created:
            raise StorageError("Refusing to delete an unrecognized backup.")
        if await self._call("delete_backup", name=entry.name, size=entry.size) is not True:
            raise StorageError("Invalid SAF backup deletion response.")

    async def write(self, data: bytes, *, expected_hash: str | None) -> None:
        await self._call("write", data=base64.b64encode(data).decode("ascii"))


def unwrap(result: object) -> object:
    if not isinstance(result, dict) or result.get("ok") is not True:
        message = result.get("error") if isinstance(result, dict) else None
        raise StorageError(message if isinstance(message, str) else "Invalid SAF bridge response.")
    return result.get("value")
