import base64
from dataclasses import replace

from finance_saf import FinanceSaf

from app.domain.backups import BackupEntry, backup_name, backup_time
from app.domain.recovery import RecoveryState, SaveRecord
from app.domain.storage import ConflictError, digest
from app.storage import RecoveryRequired, StorageError, WriteError


class AndroidFolder:
    def __init__(
        self, bridge: FinanceSaf, uri: str, filename: str = "storage-test.beancount"
    ) -> None:
        if not uri.startswith("content://"):
            raise StorageError("Android requires a content URI returned by the SAF picker.")
        self.bridge = bridge
        self.uri = uri
        if filename not in {"storage-test.beancount", "textmoney.beancount"}:
            raise StorageError("Unsupported journal filename.")
        self.filename = filename
        self.backup_prefix = "storage-test" if filename == "storage-test.beancount" else "textmoney"
        self._record: bytes | None = None
        self._candidate: bytes | None = None

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

    async def list_siblings(self) -> list[str]:
        result = await self._call("list_siblings")
        if not isinstance(result, list) or not all(isinstance(name, str) for name in result):
            raise StorageError("Invalid SAF sibling listing.")
        return sorted(
            name for name in result if name != self.filename and name.lower().endswith(".beancount")
        )

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
        previous = await self.read()
        if digest(previous) != expected_hash:
            raise ConflictError("Conflict: file changed externally. Read to reload.")
        backup = None if previous is None else await self.backup(previous)
        await self.write_guarded(data, expected_hash=expected_hash, backup=backup)
        await self.finish_write()

    async def inspect_recovery(self) -> RecoveryState:
        result = await self._call("recovery")
        if (
            not isinstance(result, dict)
            or not {"current", "marker", "pending", "backup"} <= result.keys()
            or not isinstance(result.get("pending"), list)
        ):
            raise StorageError("Invalid SAF recovery snapshot. Saving is blocked.")
        pending = result["pending"]
        if not all(
            isinstance(name, str)
            and backup_time(name, self.backup_prefix, pending=True) is not None
            for name in pending
        ):
            raise StorageError("Invalid SAF recovery backup list. Saving is blocked.")
        current = _decode(result.get("current"))
        marker = _decode(result.get("marker"))
        backup = _decode(result.get("backup"))
        record = None
        damaged = False
        if marker is not None:
            try:
                record = SaveRecord.decode(marker, self.backup_prefix)
            except (ValueError, UnicodeDecodeError):
                damaged = True
        return RecoveryState(current, marker, tuple(sorted(pending)), record, backup, damaged)

    async def write_guarded(
        self, data: bytes, *, expected_hash: str | None, backup: str | None
    ) -> None:
        state = await self.inspect_recovery()
        if state.required:
            # The just-created backup is not evidence of an earlier interrupted save.
            own_pending = set() if backup is None else {backup.removeprefix("backups/")}
            acknowledged = set() if state.record is None else set(state.record.pending)
            if (
                state.damaged
                or (state.record is not None and state.record.phase == "prepared")
                or set(state.pending) - acknowledged != own_pending
            ):
                raise RecoveryRequired("recovery_required")
        if digest(state.current) != expected_hash:
            raise ConflictError(
                "Conflict: file changed externally. Draft retained. Read to reload."
            )
        candidate = digest(data)
        assert candidate is not None
        record = SaveRecord(expected_hash, candidate, backup)
        SaveRecord.decode(record.encode(), self.backup_prefix)
        try:
            await self._call(
                "write",
                data=_encode(data),
                previous=_encode(state.current),
                marker=_encode(state.marker),
                record=_encode(record.encode()),
            )
        except (StorageError, TimeoutError) as exc:
            raise WriteError(
                "SAF save outcome is uncertain. Do not resubmit. Reload and review recovery.",
                committed=None,
            ) from exc
        self._record = record.encode()
        self._candidate = data

    async def finish_write(self) -> None:
        if self._record is None or self._candidate is None:
            raise StorageError("No verified SAF save is available to finalize.")
        try:
            state = await self.inspect_recovery()
            completed = replace(
                SaveRecord.decode(self._record, self.backup_prefix),
                phase="complete",
                pending=state.pending,
            ).encode()
            await self._call(
                "finish_write",
                record=_encode(self._record),
                data=_encode(self._candidate),
                completed=_encode(completed),
            )
        except (StorageError, TimeoutError) as exc:
            raise WriteError(
                "Journal may already be saved; recovery confirmation failed. "
                "Reload before retrying.",
                committed=None,
            ) from exc
        self._record = self._candidate = None

    async def accept_recovery(self, state: RecoveryState) -> None:
        if state.current is None:
            raise RecoveryRequired("recovery_restore_external")
        backup = await self.backup(state.current)
        current_hash = digest(state.current)
        assert current_hash is not None
        record = SaveRecord(
            current_hash,
            current_hash,
            backup,
            "resolved",
            tuple(sorted((*state.pending, backup.removeprefix("backups/")))),
        )
        await self._call(
            "accept_recovery",
            current=_encode(state.current),
            marker=_encode(state.marker),
            pending=list(state.pending),
            record=_encode(record.encode()),
            archive=backup_name(self.backup_prefix).replace(".beancount", "-recovery.json"),
        )


def _encode(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode("ascii")


def _decode(value: object) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageError("Invalid SAF recovery data. Saving is blocked.")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise StorageError("Invalid SAF recovery encoding. Saving is blocked.") from exc


def unwrap(result: object) -> object:
    if not isinstance(result, dict) or result.get("ok") is not True:
        message = result.get("error") if isinstance(result, dict) else None
        raise StorageError(message if isinstance(message, str) else "Invalid SAF bridge response.")
    return result.get("value")
