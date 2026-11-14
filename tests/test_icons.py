import asyncio
import gzip
import io
import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from app.domain.storage import ConflictError
from app.icons import MAX_INPUT, IconAsset, IconError, IconStore, normalize_image
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.icon_files import AndroidIconFiles, DesktopIconFiles
from app.infrastructure.icon_sources import DATA, MAX_RESULTS, bundled_icons

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path fill="currentColor" d="M2 2h20v20H2z"/></svg>'
)


def raster(width: int = 256, height: int = 256, *, format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(output, format=format)
    return output.getvalue()


def asset(label: str = "Synthetic") -> IconAsset:
    return IconAsset(label, normalize_image(SVG)[0], True, "Synthetic", "CC0-1.0", "Test")


def store_at(path: Path) -> IconStore:
    return IconStore(DesktopIconFiles(DesktopFolder(str(path), "textmoney.beancount")))


@pytest.mark.parametrize("dimensions", [(48, 48), (48, 96), (256, 256), (4096, 4096)])
def test_rasters_are_resized_with_padding_and_no_stretch(dimensions: tuple[int, int]) -> None:
    data, vector = normalize_image(raster(*dimensions))
    assert not vector
    with Image.open(io.BytesIO(data)) as image:
        side = min(96, max(dimensions))
        assert image.size == (side, side)
        assert image.mode == "RGBA"
        if dimensions == (48, 96):
            assert image.getpixel((0, 0)) == (0, 0, 0, 0)
            assert image.getpixel((48, 48)) == (255, 0, 0, 255)
        assert not image.info
    assert normalize_image(data)[0] == data


@pytest.mark.parametrize("dimensions", [(47, 48), (16, 16), (96, 47), (48, 97), (4097, 4097)])
def test_unsuitable_raster_dimensions_are_hidden(dimensions: tuple[int, int]) -> None:
    with pytest.raises(IconError, match="icon_dimensions"):
        normalize_image(raster(*dimensions))


@pytest.mark.parametrize(
    "data",
    [
        b"not an image",
        b"x" * (MAX_INPUT + 1),
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 10"/>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>',
        SVG.replace(b"<path", b"<script"),
        SVG.replace(b"<path", b'<image href="https://example.test/secret"'),
        SVG.replace(b'fill="currentColor"', b'fill="url(https://example.test/secret)"'),
        SVG.replace(b"<path", b'<path onload="alert(1)"'),
        SVG.replace(b"<path", b"<foreignObject"),
        b'<!DOCTYPE svg [<!ENTITY x "x">]>' + SVG,
        SVG.replace(b"0 0 24 24", b"0 0 NaN 24"),
    ],
    ids=[
        "not-an-image",
        "oversized-input",
        "wide-svg",
        "missing-viewbox",
        "script",
        "external-image",
        "external-fill",
        "event-handler",
        "foreign-object",
        "doctype-entity",
        "non-finite-viewbox",
    ],
)
def test_invalid_or_active_images_are_rejected(data: bytes) -> None:
    with pytest.raises(IconError):
        normalize_image(data)


def test_animated_and_unsupported_images_are_rejected() -> None:
    output = io.BytesIO()
    Image.new("RGB", (96, 96), "red").save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (96, 96), "blue")],
    )
    with pytest.raises(IconError, match="icon_format"):
        normalize_image(output.getvalue())
    with pytest.raises(IconError, match="icon_format"):
        normalize_image(raster(format="BMP"))


def test_svg_scalability_and_asset_license_roundtrip() -> None:
    data, vector = normalize_image(SVG.replace(b"24 24", b"48 48"))
    assert vector and b'width="24"' in data and b'height="24"' in data
    original = asset()
    assert IconAsset.parse(original.serialize()) == original
    with pytest.raises(IconError):
        IconAsset.parse({"data": "invalid"})


def test_bundled_catalogue_is_licensed_offline_searchable_and_valid() -> None:
    catalogue_counts = {}
    for prefix in ("ic", "material-symbols", "simple-icons", "logos"):
        with gzip.open(DATA / f"{prefix}.json.gz", "rt", encoding="utf-8") as source:
            catalogue_counts[prefix] = len(json.load(source)["icons"])
    icons = bundled_icons()
    assert catalogue_counts["ic"] >= 10_000
    assert catalogue_counts["material-symbols"] >= 15_000
    assert catalogue_counts["simple-icons"] >= 3_000
    assert catalogue_counts["logos"] >= 1_000
    assert sum(catalogue_counts.values()) >= 31_000
    assert len(icons) == MAX_RESULTS
    assert all(icon.monochrome and icon.license == "Apache-2.0" for icon in icons)
    assert all(icon.label.startswith("outline-") for icon in icons)
    assert all("TERMS AND CONDITIONS" in icon.license_text for icon in icons)
    assert all(normalize_image(icon.data)[1] for icon in icons)
    assert bundled_icons("bank")[0].label == "outline-account-balance"
    assert bundled_icons("банк")[0].label == "outline-account-balance"
    assert bundled_icons("продукти")[0].label == "outline-shopping-cart"
    assert any(" · material-symbols:" in icon.source for icon in bundled_icons("home"))
    assert any(" · material-symbols:home" in icon.source for icon in bundled_icons("home-sharp"))
    github = bundled_icons("github")
    assert any(" · simple-icons:" in icon.source for icon in github)
    assert any(" · logos:" in icon.source for icon in github)
    assert all(icon.license == "CC0-1.0" for icon in github)
    assert bundled_icons("unfindable") == []


def test_assign_remove_restart_and_external_sync_do_not_touch_journal(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = tmp_path / "textmoney.beancount"
        journal.write_text("; synthetic untouched\n")
        store = store_at(tmp_path)
        assert await store.load()
        assert not await store.load()
        await store.assign("Assets:Bank", asset(), ())
        fresh = store_at(tmp_path)
        await fresh.load()
        assert fresh.assets == store.assets == {"Assets:Bank": asset()}
        assert await fresh.load() is False
        await fresh.assign("Assets:Bank", None, fresh.heads["Assets:Bank"])
        assert await store.load()
        assert store.assets == {}
        assert journal.read_text() == "; synthetic untouched\n"
        assert not (tmp_path / "backups").exists()

    asyncio.run(scenario())


def test_previously_assigned_thin_line_icon_and_license_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        original = IconAsset(
            "Previously assigned thin-line icon",
            normalize_image(SVG)[0],
            True,
            "https://github.com/Arcticons-Team/Arcticons · arcticons:monobank",
            "CC-BY-SA-4.0",
            "Arcticons contributors",
            "https://creativecommons.org/licenses/by-sa/4.0/",
            (DATA / "CC-BY-SA-4.0.txt").read_text(encoding="utf-8"),
        )
        await store_at(tmp_path).assign("Assets:Bank", original, ())
        fresh = store_at(tmp_path)
        await fresh.load()
        assert fresh.assets == {"Assets:Bank": original}
        assert IconAsset.parse(original.serialize()) == original

    asyncio.run(scenario())


def test_stale_picker_preserves_newer_assignment(tmp_path: Path) -> None:
    async def scenario() -> None:
        first, second = store_at(tmp_path), store_at(tmp_path)
        await first.load()
        await second.assign("Assets:Bank", asset("Newer"), ())
        with pytest.raises(ConflictError, match="icon_stale"):
            await first.assign("Assets:Bank", asset("Stale"), ())
        assert first.assets["Assets:Bank"].label == "Newer"

    asyncio.run(scenario())


def test_concurrent_sync_is_visible_and_resolvable_without_losing_revisions(tmp_path: Path) -> None:
    async def scenario() -> None:
        other = tmp_path / "other"
        other.mkdir()
        first, second = store_at(tmp_path), store_at(other)
        await first.assign("Expenses:Food", asset("First"), ())
        await second.assign("Expenses:Food", asset("Second"), ())
        for path in (other / "textmoney-icons").iterdir():
            (tmp_path / "textmoney-icons" / path.name).write_bytes(path.read_bytes())
        await first.load()
        assert first.conflicts == {"Expenses:Food"}
        assert "Expenses:Food" not in first.assets
        assert len(first.heads["Expenses:Food"]) == 2
        await first.assign("Expenses:Food", asset("Resolved"), first.heads["Expenses:Food"])
        assert not first.conflicts and first.assets["Expenses:Food"].label == "Resolved"
        assert len(list((tmp_path / "textmoney-icons").iterdir())) == 3

    asyncio.run(scenario())


def test_category_subtree_migration_moves_icons_in_one_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = store_at(tmp_path)
        await store.assign("Expenses:Food", asset("Parent"), ())
        await store.assign("Expenses:Food:Shop", asset("Child"), ())
        await store.assign("Assets:Bank", asset("Bank"), ())
        count = len(store.records)
        await store.migrate("Expenses:Food", "Expenses:Living:Food")
        assert len(store.records) == count + 1
        assert set(store.assets) == {
            "Expenses:Living:Food",
            "Expenses:Living:Food:Shop",
            "Assets:Bank",
        }
        assert store.assets["Expenses:Living:Food:Shop"].label == "Child"
        await store.migrate("Expenses:Food", "Expenses:Living:Food")
        assert len(store.records) == count + 1

    asyncio.run(scenario())


def test_sidecar_failure_retains_original_and_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = store_at(tmp_path)
        await store.assign("Assets:Bank", asset(), ())
        original = dict(store.records)
        monkeypatch.setattr(
            "app.infrastructure.record_files.os.link", MagicMock(side_effect=OSError("disk full"))
        )
        with pytest.raises(IconError, match="icon_storage"):
            await store.assign("Assets:Bank", asset("New"), store.heads["Assets:Bank"])
        assert store.records == original and store.assets["Assets:Bank"] == asset()
        assert {
            path.name: path.read_bytes() for path in (tmp_path / "textmoney-icons").iterdir()
        } == original

    asyncio.run(scenario())


def test_large_icon_migration_preserves_every_assignment_with_bounded_records(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = store_at(tmp_path)
        licensed = bundled_icons("bank")[0]
        for index in range(36):
            await store.assign(f"Expenses:Old:Category{index}", licensed, ())
        count = len(store.records)
        await store.migrate("Expenses:Old", "Expenses:New")
        assert len(store.assets) == 36
        assert all(key.startswith("Expenses:New:") for key in store.assets)
        assert len(store.records) > count + 1
        assert all(len(data) <= 384 * 1024 for data in store.records.values())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name,data",
    [
        ("conflicted copy.json", b"{}"),
        ("0" * 64 + ".json", b"{}"),
    ],
)
def test_damaged_and_conflict_copy_records_are_not_silently_ignored(
    tmp_path: Path, name: str, data: bytes
) -> None:
    directory = tmp_path / "textmoney-icons"
    directory.mkdir()
    (directory / name).write_bytes(data)
    with pytest.raises(IconError, match="icon_records_invalid"):
        asyncio.run(store_at(tmp_path).load())


def test_missing_parent_is_reported_as_incomplete_sync(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "changes": {"Assets:Bank": {"parents": ["a" * 64 + ".json"], "asset": None}},
    }
    data = json.dumps(payload).encode()
    directory = tmp_path / "textmoney-icons"
    directory.mkdir()
    (directory / (sha256(data).hexdigest() + ".json")).write_bytes(data)
    with pytest.raises(IconError, match="icon_sync_incomplete"):
        asyncio.run(store_at(tmp_path).load())


def test_sidecar_symlinks_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "textmoney-icons"
    outside = tmp_path / "outside"
    outside.mkdir()
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(IconError, match="icon_storage"):
        asyncio.run(store_at(tmp_path).load())


def test_android_sidecar_transport_and_invalid_responses() -> None:
    async def scenario() -> None:
        bridge = MagicMock()
        bridge.call = AsyncMock(return_value={"ok": True, "value": {}})
        files = AndroidIconFiles(
            AndroidFolder(bridge, "content://synthetic/tree", "textmoney.beancount")
        )
        assert await files.read_records() == {}
        assert bridge.call.call_args.args == ("icons_read",)
        bridge.call.return_value = {"ok": True, "value": True}
        await files.create_record("a" * 64 + ".json", b"synthetic")
        assert bridge.call.call_args.args == ("icons_create",)
        bridge.call.return_value = {"ok": True, "value": {"../private": "bad"}}
        with pytest.raises(IconError):
            await files.read_records()

    asyncio.run(scenario())
