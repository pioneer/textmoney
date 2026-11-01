"""Bounded sidecar access; journals and arbitrary filesystem paths are not exposed."""

import asyncio
import base64
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder

RECORD_NAME = re.compile(r"^[a-f0-9]{64}\.json$")


@dataclass(frozen=True)
class RecordPolicy:
    directory: str
    method: str
    max_record: int
    max_records: int
    max_total: int
    error: type[Exception]
    invalid: str
    size: str
    storage: str


class DesktopRecordFiles:
    def __init__(self, folder: DesktopFolder, policy: RecordPolicy) -> None:
        self.folder = folder
        self.policy = policy

    def _directory(self, *, create: bool = False) -> Path:
        root = self.folder.root
        directory = root / self.policy.directory
        if not root.is_dir() or directory.is_symlink():
            raise self.policy.error(self.policy.storage)
        if create:
            directory.mkdir(exist_ok=True)
        if directory.exists() and not directory.is_dir():
            raise self.policy.error(self.policy.storage)
        return directory

    async def read_records(self) -> dict[str, bytes]:
        return await asyncio.to_thread(self._read)

    def _read(self) -> dict[str, bytes]:
        try:
            directory = self._directory()
            if not directory.exists():
                return {}
            paths = [path for path in directory.iterdir() if not path.name.endswith(".tmp")]
            if len(paths) > self.policy.max_records:
                raise self.policy.error(self.policy.invalid)
            records = {}
            total = 0
            for path in paths:
                if not RECORD_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                    raise self.policy.error(self.policy.invalid)
                with path.open("rb") as stream:
                    data = stream.read(self.policy.max_record + 1)
                if len(data) > self.policy.max_record:
                    raise self.policy.error(self.policy.size)
                total += len(data)
                if total > self.policy.max_total:
                    raise self.policy.error(self.policy.size)
                records[path.name] = data
            return records
        except OSError as exc:
            raise self.policy.error(self.policy.storage) from exc

    async def create_record(self, name: str, data: bytes) -> None:
        await asyncio.to_thread(self._create, name, data)

    def _create(self, name: str, data: bytes) -> None:
        if not RECORD_NAME.fullmatch(name) or len(data) > self.policy.max_record:
            raise self.policy.error(self.policy.invalid)
        temporary: Path | None = None
        try:
            directory = self._directory(create=True)
            target = directory / name
            if target.is_symlink():
                raise self.policy.error(self.policy.storage)
            if target.exists():
                if target.read_bytes() != data:
                    raise self.policy.error(self.policy.invalid)
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
                    raise self.policy.error(self.policy.invalid) from None
        except OSError as exc:
            raise self.policy.error(self.policy.storage) from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class AndroidRecordFiles:
    def __init__(self, folder: AndroidFolder, policy: RecordPolicy) -> None:
        self.folder = folder
        self.policy = policy

    async def read_records(self) -> dict[str, bytes]:
        result = await self.folder._call(f"{self.policy.method}_read")
        if not isinstance(result, dict) or len(result) > self.policy.max_records:
            raise self.policy.error(self.policy.invalid)
        records = {}
        total = 0
        for name, encoded in result.items():
            if (
                not isinstance(name, str)
                or not RECORD_NAME.fullmatch(name)
                or not isinstance(encoded, str)
                or len(encoded) > self.policy.max_record * 2
            ):
                raise self.policy.error(self.policy.invalid)
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise self.policy.error(self.policy.invalid) from exc
            total += len(data)
            if len(data) > self.policy.max_record or total > self.policy.max_total:
                raise self.policy.error(self.policy.size)
            records[name] = data
        return records

    async def create_record(self, name: str, data: bytes) -> None:
        if not RECORD_NAME.fullmatch(name) or len(data) > self.policy.max_record:
            raise self.policy.error(self.policy.invalid)
        if (
            await self.folder._call(
                f"{self.policy.method}_create",
                name=name,
                data=base64.b64encode(data).decode("ascii"),
            )
            is not True
        ):
            raise self.policy.error(self.policy.storage)
