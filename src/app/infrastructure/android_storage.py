import base64
from uuid import uuid4

from finance_saf import FinanceSaf

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
            name=f"{self.backup_prefix}-{uuid4().hex}.beancount",
            data=base64.b64encode(data).decode("ascii"),
        )
        if not isinstance(result, str):
            raise StorageError("Invalid SAF backup response. Original not modified.")
        return result

    async def write(self, data: bytes) -> None:
        await self._call("write", data=base64.b64encode(data).decode("ascii"))


def unwrap(result: object) -> object:
    if not isinstance(result, dict) or result.get("ok") is not True:
        message = result.get("error") if isinstance(result, dict) else None
        raise StorageError(message if isinstance(message, str) else "Invalid SAF bridge response.")
    return result.get("value")
