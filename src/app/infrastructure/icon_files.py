"""Bounded sidecar access; journals and arbitrary filesystem paths are not exposed."""

import asyncio
import base64
import os
from pathlib import Path
from uuid import uuid4

from app.icons import MAX_RECORD, MAX_RECORDS, RECORD_NAME, IconError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder

DIRECTORY = "textmoney-icons"


class DesktopIconFiles:
    def __init__(self, folder: DesktopFolder) -> None:
        self.folder = folder

    def _directory(self, *, create: bool = False) -> Path:
        root = self.folder.root
        directory = root / DIRECTORY
        if not root.is_dir() or directory.is_symlink():
            raise IconError("icon_storage")
        if create:
            directory.mkdir(exist_ok=True)
        if directory.exists() and not directory.is_dir():
            raise IconError("icon_storage")
        return directory

    async def read_records(self) -> dict[str, bytes]:
        return await asyncio.to_thread(self._read)

    def _read(self) -> dict[str, bytes]:
        try:
            directory = self._directory()
            if not directory.exists():
                return {}
            paths = [path for path in directory.iterdir() if not path.name.endswith(".tmp")]
            if len(paths) > MAX_RECORDS:
                raise IconError("icon_records_invalid")
            records = {}
            total = 0
            for path in paths:
                if not RECORD_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                    raise IconError("icon_records_invalid")
                with path.open("rb") as stream:
                    data = stream.read(MAX_RECORD + 1)
                if len(data) > MAX_RECORD:
                    raise IconError("icon_size")
                total += len(data)
                if total > 16 * 1024 * 1024:
                    raise IconError("icon_size")
                records[path.name] = data
            return records
        except OSError as exc:
            raise IconError("icon_storage") from exc

    async def create_record(self, name: str, data: bytes) -> None:
        await asyncio.to_thread(self._create, name, data)

    def _create(self, name: str, data: bytes) -> None:
        if not RECORD_NAME.fullmatch(name) or len(data) > MAX_RECORD:
            raise IconError("icon_records_invalid")
        temporary: Path | None = None
        try:
            directory = self._directory(create=True)
            target = directory / name
            if target.is_symlink():
                raise IconError("icon_storage")
            if target.exists():
                if target.read_bytes() != data:
                    raise IconError("icon_records_invalid")
                return
            temporary = directory / f"{uuid4().hex}.tmp"
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != data:
                    raise IconError("icon_records_invalid") from None
        except OSError as exc:
            raise IconError("icon_storage") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class AndroidIconFiles:
    def __init__(self, folder: AndroidFolder) -> None:
        self.folder = folder

    async def read_records(self) -> dict[str, bytes]:
        result = await self.folder._call("icons_read")
        if not isinstance(result, dict) or len(result) > MAX_RECORDS:
            raise IconError("icon_records_invalid")
        records = {}
        for name, encoded in result.items():
            if (
                not isinstance(name, str)
                or not RECORD_NAME.fullmatch(name)
                or not isinstance(encoded, str)
                or len(encoded) > MAX_RECORD * 2
            ):
                raise IconError("icon_records_invalid")
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise IconError("icon_records_invalid") from exc
            if len(data) > MAX_RECORD:
                raise IconError("icon_size")
            records[name] = data
        return records

    async def create_record(self, name: str, data: bytes) -> None:
        if not RECORD_NAME.fullmatch(name) or len(data) > MAX_RECORD:
            raise IconError("icon_records_invalid")
        if (
            await self.folder._call(
                "icons_create", name=name, data=base64.b64encode(data).decode("ascii")
            )
            is not True
        ):
            raise IconError("icon_storage")
