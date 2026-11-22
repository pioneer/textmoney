"""Validated, offline-capable entity icons and append-only assignment records."""

import asyncio
import base64
import io
import json
import math
import re
import warnings
from dataclasses import dataclass, field
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


def normalize_generated_image(
    data: bytes, *, require_transparency: bool = False
) -> tuple[bytes, bool]:
    """Fit transparent generated artwork and mark single-tone neutral art for theming."""
    original_data = data
    data, vector = normalize_image(data)
    if vector:
        raise IconError("icon_format")
    if require_transparency:
        with Image.open(io.BytesIO(original_data)) as original:
            opacity = original.convert("RGBA").tobytes()[3::4]
        if min(opacity) > 16 or max(opacity) < 64:
            raise IconError("icon_ai_adaptation_error")
    with Image.open(io.BytesIO(data)) as original:
        image = original.convert("RGBA")
    rgba = image.tobytes()
    if min(rgba[3::4]) > 16:
        return data, False
    bounds = image.getchannel("A").point([0] * 64 + [255] * 192).getbbox()
    if bounds is None:
        return data, False

    pixels = [rgba[index : index + 3] for index in range(0, len(rgba), 4) if rgba[index + 3] >= 64]
    neutral = all(max(pixel) - min(pixel) <= 24 for pixel in pixels)
    luminance = [pixel[0] for pixel in pixels]
    monochrome = neutral and max(luminance) - min(luminance) <= 35

    artwork = image.crop(bounds)
    artwork.thumbnail((80, 80), Image.Resampling.LANCZOS)
    if max(artwork.size) < 80:
        scale = 80 / max(artwork.size)
        artwork = artwork.resize(
            (max(1, round(artwork.width * scale)), max(1, round(artwork.height * scale))),
            Image.Resampling.LANCZOS,
        )
    canvas = Image.new("RGBA", (RASTER_SIZE, RASTER_SIZE))
    canvas.paste(artwork, ((RASTER_SIZE - artwork.width) // 2, (RASTER_SIZE - artwork.height) // 2))
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue(), monochrome


def _adaptive_layers(data: bytes) -> tuple[bytes, bytes] | None:
    normalized, vector = normalize_image(data)
    if vector:
        raise IconError("icon_ai_adaptation_error")
    with Image.open(io.BytesIO(normalized)) as original:
        image = original.convert("RGBA")
    alpha = image.getchannel("A")
    if min(image.tobytes()[3::4]) > 16 or alpha.getbbox() is None:
        raise IconError("icon_ai_adaptation_error")
    fixed = bytearray(image.tobytes())
    neutral = bytearray(len(fixed))
    has_neutral = False
    for index in range(0, len(fixed), 4):
        red, green, blue, opacity = fixed[index : index + 4]
        chroma = max(red, green, blue) - min(red, green, blue)
        weight = max(0.0, min(1.0, (48 - chroma) / 24))
        mask_alpha = round(opacity * weight)
        neutral[index + 3] = mask_alpha
        has_neutral |= mask_alpha > 0
        # The two overlaid layers must retain the original combined opacity.
        fixed[index + 3] = (
            round(255 * (opacity - mask_alpha) / (255 - mask_alpha)) if mask_alpha < 255 else 0
        )
    if not has_neutral:
        return None
    layers = []
    for pixels in (fixed, neutral):
        output = io.BytesIO()
        Image.frombytes("RGBA", image.size, bytes(pixels)).save(output, format="PNG")
        layers.append(output.getvalue())
    return layers[0], layers[1]


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
    adaptive_neutrals: bool = False
    adaptive_layers: tuple[bytes, bytes] | None = field(
        init=False, default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.adaptive_neutrals:
            if self.monochrome:
                raise IconError("icon_records_invalid")
            object.__setattr__(self, "adaptive_layers", _adaptive_layers(self.data))

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
            **({"adaptive_neutrals": True} if self.adaptive_neutrals else {}),
        }

    @classmethod
    def parse(cls, value: object) -> "IconAsset":
        if not isinstance(value, dict) or set(value) - {"adaptive_neutrals"} != {
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
            or type(value.get("adaptive_neutrals", False)) is not bool
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
        normalized, _ = normalize_image(data)
        try:
            return cls(
                value["label"],
                normalized,
                value["monochrome"],
                value["source"],
                value["license"],
                value["attribution"],
                value["license_url"],
                value["license_text"],
                value.get("adaptive_neutrals", False),
            )
        except IconError as exc:
            raise IconError("icon_records_invalid") from exc


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
