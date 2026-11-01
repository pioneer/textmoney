"""Validated, offline-capable entity icons and append-only assignment records."""

import asyncio
import base64
import io
import json
import math
import re
import warnings
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from xml.etree import ElementTree as ET

from PIL import Image, ImageOps, UnidentifiedImageError

from app.domain.category_migration import migrated_category
from app.domain.storage import ConflictError

DISPLAY_SIZE = 16
RASTER_SIZE = 96
MAX_INPUT = 2 * 1024 * 1024
MAX_RECORD = 384 * 1024
MAX_RECORDS = 4096
RECORD_NAME = re.compile(r"[a-f0-9]{64}\.json")


class IconError(Exception):
    """A safe, localizable icon failure without user payloads or remote URLs."""


def _shape(width: float, height: float, *, raster: bool) -> None:
    if (
        not all(math.isfinite(value) and value > 0 for value in (width, height))
        or max(width, height) / min(width, height) > 2
        or (raster and (min(width, height) < 48 or max(width, height) > 4096))
    ):
        raise IconError("icon_dimensions")


def normalize_image(data: bytes) -> tuple[bytes, bool]:
    """Allow a small inert SVG subset, or decode and re-encode a bounded raster."""
    if not data or len(data) > MAX_INPUT:
        raise IconError("icon_size")
    if data.lstrip().startswith((b"<svg", b"<?xml")):
        if b"<!" in data or b"<?" in data.replace(b'<?xml version="1.0"?>', b""):
            raise IconError("icon_svg")
        try:
            root = ET.fromstring(data)
            if root.tag != "{http://www.w3.org/2000/svg}svg":
                raise IconError("icon_svg")
            bounds = [float(value) for value in root.attrib["viewBox"].replace(",", " ").split()]
            if len(bounds) != 4:
                raise IconError("icon_dimensions")
            _shape(bounds[2], bounds[3], raster=False)
            tags = {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
            attributes = {
                "d",
                "fill",
                "fill-rule",
                "clip-rule",
                "stroke",
                "stroke-width",
                "stroke-linecap",
                "stroke-linejoin",
                "stroke-miterlimit",
                "opacity",
                "fill-opacity",
                "stroke-opacity",
                "transform",
                "viewBox",
                "width",
                "height",
                "x",
                "y",
                "x1",
                "x2",
                "y1",
                "y2",
                "cx",
                "cy",
                "r",
                "rx",
                "ry",
                "points",
            }
            elements = list(root.iter())
            if len(elements) > 512:
                raise IconError("icon_svg")
            for element in elements:
                if element.tag.removeprefix("{http://www.w3.org/2000/svg}") not in tags:
                    raise IconError("icon_svg")
                for key, value in element.attrib.items():
                    if key not in attributes or re.search(r"url|[<>]|javascript", value, re.I):
                        raise IconError("icon_svg")
                element.text = None
                element.tail = None
            root.set("width", "24")
            root.set("height", "24")
            # ElementTree's ns0 prefix prevents Flutter's SVG sniffing.
            result = (
                ET.tostring(root, encoding="unicode")
                .replace("ns0:", "")
                .replace(":ns0", "")
                .encode()
            )
            return result, True
        except (ET.ParseError, KeyError, ValueError, RecursionError) as exc:
            raise IconError("icon_svg") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                _shape(*original.size, raster=True)
                if (
                    original.format not in {"PNG", "JPEG", "WEBP"}
                    or getattr(original, "n_frames", 1) != 1
                ):
                    raise IconError("icon_format")
                image = ImageOps.exif_transpose(original).convert("RGBA")
                side = min(RASTER_SIZE, max(image.size))
                image.thumbnail((side, side), Image.Resampling.LANCZOS)
                square = Image.new("RGBA", (side, side))
                square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
                output = io.BytesIO()
                square.save(output, format="PNG")
                return output.getvalue(), False
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise IconError("icon_format") from exc


@dataclass(frozen=True)
class IconAsset:
    label: str
    data: bytes
    monochrome: bool
    source: str
    license: str
    attribution: str
    license_url: str = ""
    license_text: str = ""

    def serialize(self) -> dict[str, object]:
        return {
            "label": self.label,
            "data": base64.b64encode(self.data).decode("ascii"),
            "monochrome": self.monochrome,
            "source": self.source,
            "license": self.license,
            "attribution": self.attribution,
            "license_url": self.license_url,
            "license_text": self.license_text,
        }

    @classmethod
    def parse(cls, value: object) -> "IconAsset":
        if not isinstance(value, dict) or set(value) != {
            "label",
            "data",
            "monochrome",
            "source",
            "license",
            "attribution",
            "license_url",
            "license_text",
        }:
            raise IconError("icon_records_invalid")
        if (
            type(value["monochrome"]) is not bool
            or any(
                not isinstance(value[key], str) or len(value[key]) > 32768
                for key in (
                    "label",
                    "source",
                    "license",
                    "attribution",
                    "license_url",
                    "license_text",
                )
            )
            or not isinstance(value["data"], str)
        ):
            raise IconError("icon_records_invalid")
        try:
            data = base64.b64decode(value["data"], validate=True)
        except ValueError as exc:
            raise IconError("icon_records_invalid") from exc
        normalized, vector = normalize_image(data)
        return cls(
            value["label"],
            normalized,
            value["monochrome"] and vector,
            value["source"],
            value["license"],
            value["attribution"],
            value["license_url"],
            value["license_text"],
        )


class IconFiles(Protocol):
    async def read_records(self) -> dict[str, bytes]: ...
    async def create_record(self, name: str, data: bytes) -> None: ...


def _identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 512
        and bool(re.fullmatch(r"(Assets|Liabilities|Expenses|Income)(:[A-Z][A-Za-z0-9-]*)+", value))
    )


class IconStore:
    """Immutable records avoid replacing a manifest through non-atomic SAF writes."""

    def __init__(self, files: IconFiles) -> None:
        self.files = files
        self.assets: dict[str, IconAsset] = {}
        self.heads: dict[str, tuple[str, ...]] = {}
        self.conflicts: set[str] = set()
        self.records: dict[str, bytes] = {}
        self.lock = asyncio.Lock()
        self.loaded = False

    async def load(self) -> bool:
        async with self.lock:
            return await self._load()

    async def _load(self) -> bool:
        records = await self.files.read_records()
        if self.loaded and records == self.records:
            return False
        revisions: dict[str, dict[str, tuple[tuple[str, ...], IconAsset | None]]] = {}
        if len(records) > MAX_RECORDS:
            raise IconError("icon_records_invalid")
        try:
            for name, data in records.items():
                if (
                    not RECORD_NAME.fullmatch(name)
                    or len(data) > MAX_RECORD
                    or sha256(data).hexdigest() + ".json" != name
                ):
                    raise IconError("icon_records_invalid")
                record = json.loads(data)
                if (
                    not isinstance(record, dict)
                    or set(record) != {"version", "changes"}
                    or record["version"] != 1
                ):
                    raise IconError("icon_records_invalid")
                changes = record["changes"]
                if not isinstance(changes, dict) or not changes or len(changes) > 1024:
                    raise IconError("icon_records_invalid")
                for identifier, change in changes.items():
                    if (
                        not _identifier(identifier)
                        or not isinstance(change, dict)
                        or set(change) != {"parents", "asset"}
                    ):
                        raise IconError("icon_records_invalid")
                    parents = change["parents"]
                    if (
                        not isinstance(parents, list)
                        or len(parents) > MAX_RECORDS
                        or any(
                            not isinstance(parent, str) or not RECORD_NAME.fullmatch(parent)
                            for parent in parents
                        )
                        or len(set(parents)) != len(parents)
                    ):
                        raise IconError("icon_records_invalid")
                    asset = None if change["asset"] is None else IconAsset.parse(change["asset"])
                    revisions.setdefault(identifier, {})[name] = (tuple(parents), asset)
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as exc:
            raise IconError("icon_records_invalid") from exc
        heads: dict[str, tuple[str, ...]] = {}
        assets: dict[str, IconAsset] = {}
        conflicts: set[str] = set()
        for identifier, versions in revisions.items():
            ancestors = {parent for parents, _ in versions.values() for parent in parents}
            if ancestors - versions.keys():
                raise IconError("icon_sync_incomplete")
            heads[identifier] = tuple(sorted(versions.keys() - ancestors))
            if not heads[identifier]:
                raise IconError("icon_records_invalid")
            values = [versions[name][1] for name in heads[identifier]]
            if any(value != values[0] for value in values):
                conflicts.add(identifier)
            elif values[0] is not None:
                assets[identifier] = values[0]
        self.records, self.heads, self.assets, self.conflicts = records, heads, assets, conflicts
        self.loaded = True
        return True

    async def assign(
        self, identifier: str, asset: IconAsset | None, expected: tuple[str, ...]
    ) -> None:
        async with self.lock:
            await self._load()
            if self.heads.get(identifier, ()) != expected:
                raise ConflictError("icon_stale")
            await self._write({identifier: asset})

    async def _write(self, changes: dict[str, IconAsset | None]) -> None:
        if any(not _identifier(identifier) for identifier in changes):
            raise IconError("icon_records_invalid")
        changes = {
            key: IconAsset.parse(value.serialize()) if value is not None else None
            for key, value in changes.items()
        }
        data = json.dumps(
            {
                "version": 1,
                "changes": {
                    key: {
                        "parents": self.heads.get(key, ()),
                        "asset": value.serialize() if value else None,
                    }
                    for key, value in changes.items()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(data) > MAX_RECORD:
            if len(changes) <= 2:
                raise IconError("icon_size")
            items = list(changes.items())
            midpoint = max(2, len(items) // 4 * 2)
            await self._write(dict(items[:midpoint]))
            await self._write(dict(items[midpoint:]))
            return
        name = sha256(data).hexdigest() + ".json"
        if name not in self.records and (
            len(self.records) >= MAX_RECORDS
            or sum(map(len, self.records.values())) + len(data) > 16 * 1024 * 1024
        ):
            raise IconError("icon_records_invalid")
        await self.files.create_record(name, data)
        await self._load()

    async def migrate(self, original: str, target: str) -> None:
        async with self.lock:
            await self._load()
            if self.conflicts:
                raise IconError("icon_conflicts")
            changes: dict[str, IconAsset | None] = {}
            for identifier, asset in self.assets.items():
                moved = migrated_category(identifier, original, target)
                if moved != identifier:
                    changes[identifier] = None
                    changes[moved] = asset
            if changes:
                await self._write(changes)
