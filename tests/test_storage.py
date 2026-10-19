import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from app.domain.storage import ConflictError, digest
from app.infrastructure.android_storage import AndroidFolder, unwrap
from app.infrastructure.desktop_storage import TARGET, DesktopFolder
from app.storage import SAMPLE, StorageError, StorageSession
from app.ui.home import StorageView


def test_sha256_and_missing_baseline() -> None:
    assert digest(b"synthetic") == hashlib.sha256(b"synthetic").hexdigest()
    assert digest(None) is None
    assert digest(b"") != digest(None)


def test_storage_diagnostics_never_touch_legacy_test_or_journal(tmp_path: Path) -> None:
    async def scenario() -> None:
        legacy = tmp_path / "poc-test.beancount"
        journal = tmp_path / "txncount.beancount"
        legacy.write_bytes(b"legacy synthetic file")
        journal.write_bytes(b"synthetic journal sentinel")
        session = StorageSession(DesktopFolder(str(tmp_path)))
        await session.read()
        await session.write()
        assert (tmp_path / "storage-test.beancount").read_text() == SAMPLE
        assert legacy.read_bytes() == b"legacy synthetic file"
        assert journal.read_bytes() == b"synthetic journal sentinel"

    asyncio.run(scenario())


def test_create_modify_and_verified_unique_backups(tmp_path: Path) -> None:
    async def scenario() -> None:
        session = StorageSession(DesktopFolder(str(tmp_path)))
        assert not await session.read()
        assert session.draft == SAMPLE
        assert await session.write() is None
        assert (tmp_path / TARGET).read_text() == SAMPLE
        session.draft = "synthetic replacement\n"
        backup = await session.write()
        assert backup is not None
        assert (tmp_path / backup).read_bytes() == SAMPLE.encode()
        another = await session.backup()
        assert another != backup
        assert (tmp_path / another).read_bytes() == b"synthetic replacement\n"
        assert (tmp_path / TARGET).read_bytes() == b"synthetic replacement\n"
        assert session.last_hash == digest(b"synthetic replacement\n")

    asyncio.run(scenario())


@pytest.mark.parametrize("external", [b"external replacement", b"", None])
def test_external_change_conflict_preserves_draft(tmp_path: Path, external: bytes | None) -> None:
    async def scenario() -> None:
        path = tmp_path / TARGET
        path.write_bytes(b"original")
        session = StorageSession(DesktopFolder(str(tmp_path)))
        await session.read()
        session.draft = "unsaved synthetic input"
        if external is None:
            path.unlink()
        else:
            path.write_bytes(external)
        assert await session.poll()
        with pytest.raises(ConflictError, match="Draft retained"):
            await session.write()
        assert session.draft == "unsaved synthetic input"
        assert not (tmp_path / "backups").exists()
        assert (path.read_bytes() if path.exists() else None) == external
        await session.read()
        assert not session.external_changes

    asyncio.run(scenario())


def test_created_externally_after_missing_read_is_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        session = StorageSession(DesktopFolder(str(tmp_path)))
        await session.read()
        (tmp_path / TARGET).write_bytes(b"external")
        with pytest.raises(ConflictError):
            await session.write()
        assert (tmp_path / TARGET).read_bytes() == b"external"

    asyncio.run(scenario())


def test_write_requires_read_and_missing_backup_is_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        session = StorageSession(DesktopFolder(str(tmp_path)))
        with pytest.raises(StorageError, match="Read first"):
            await session.write()
        await session.read()
        with pytest.raises(StorageError, match="No file"):
            await session.backup()

    asyncio.run(scenario())


def test_backup_failure_leaves_original_and_draft(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / TARGET
        path.write_bytes(b"original")
        (tmp_path / "backups").write_bytes(b"not a directory")
        session = StorageSession(DesktopFolder(str(tmp_path)))
        await session.read()
        session.draft = "unsaved"
        with pytest.raises(StorageError, match="Backup failed"):
            await session.write()
        assert path.read_bytes() == b"original"
        assert session.draft == "unsaved"

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["read", "write"])
def test_io_failures_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path))
        session = StorageSession(folder)
        await session.read()
        session.draft = "unsaved"
        if operation == "read":
            monkeypatch.setattr(Path, "read_bytes", MagicMock(side_effect=PermissionError()))
        else:
            original_open = Path.open

            def open_unless_writing(path: Path, mode: str = "r", *args, **kwargs):
                if mode == "wb":
                    raise PermissionError()
                return original_open(path, mode, *args, **kwargs)

            monkeypatch.setattr(Path, "open", open_unless_writing)
        with pytest.raises(StorageError, match="failed"):
            await (session.read() if operation == "read" else session.write())
        assert session.draft == "unsaved"
        if operation == "write":
            assert not session.loaded

    asyncio.run(scenario())


def test_write_failure_keeps_previous_verified_backup(tmp_path: Path) -> None:
    class FailingFolder(DesktopFolder):
        async def write(self, data: bytes) -> None:
            raise StorageError("Synthetic output failure.")

    async def scenario() -> None:
        (tmp_path / TARGET).write_bytes(b"original")
        session = StorageSession(FailingFolder(str(tmp_path)))
        await session.read()
        session.draft = "unsaved"
        with pytest.raises(StorageError, match="Recovery: backups/"):
            await session.write()
        assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"
        assert session.draft == "unsaved"
        assert not session.loaded

    asyncio.run(scenario())


def test_external_change_during_backup_refuses_overwrite(tmp_path: Path) -> None:
    class RacingFolder(DesktopFolder):
        async def backup(self, data: bytes) -> str:
            backup = await super().backup(data)
            (tmp_path / TARGET).write_bytes(b"external")
            return backup

    async def scenario() -> None:
        (tmp_path / TARGET).write_bytes(b"original")
        session = StorageSession(RacingFolder(str(tmp_path)))
        await session.read()
        with pytest.raises(ConflictError):
            await session.write()
        assert (tmp_path / TARGET).read_bytes() == b"external"
        assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"

    asyncio.run(scenario())


def test_non_utf8_read_keeps_draft(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / TARGET).write_bytes(b"\xff")
        session = StorageSession(DesktopFolder(str(tmp_path)))
        session.draft = "unsaved"
        with pytest.raises(StorageError, match="not UTF-8"):
            await session.read()
        assert session.draft == "unsaved"
        assert not session.loaded

    asyncio.run(scenario())


def test_symlink_and_uri_not_treated_as_filesystem_path(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="content URI"):
        DesktopFolder("content://synthetic/tree/test")
    target = tmp_path / TARGET
    try:
        target.symlink_to(tmp_path / "unrelated")
    except OSError:
        pytest.skip("Host does not allow symlink creation")
    with pytest.raises(StorageError, match="symlink"):
        asyncio.run(DesktopFolder(str(tmp_path)).read())


def test_android_bridge_rejects_missing_grant_and_invalid_response() -> None:
    with pytest.raises(StorageError):
        unwrap({"ok": False, "error": "Grant revoked. Select Folder again."})
    with pytest.raises(StorageError):
        unwrap(None)
    with pytest.raises(StorageError, match="content URI"):
        AndroidFolder(MagicMock(), "/not/a/saf/uri")


def test_android_transport_roundtrip_and_permission_status() -> None:
    async def scenario() -> None:
        bridge = MagicMock()
        bridge.call = AsyncMock(
            side_effect=[
                {"ok": True, "value": True},
                {"ok": True, "value": "c3ludGhldGlj"},
                {"ok": True, "value": "backups/synthetic.beancount"},
                {"ok": True, "value": None},
                {"ok": True, "value": False},
            ]
        )
        folder = AndroidFolder(bridge, "content://synthetic/tree/test")
        assert "persisted" in await folder.status()
        assert await folder.read() == b"synthetic"
        assert await folder.backup(b"synthetic") == "backups/synthetic.beancount"
        await folder.write(b"synthetic")
        assert bridge.call.call_args.kwargs["data"] == "c3ludGhldGlj"
        with pytest.raises(StorageError, match="grant missing"):
            await folder.status()

    asyncio.run(scenario())


def test_readback_mismatch_requires_new_read_and_keeps_backup(tmp_path: Path) -> None:
    class TruncatingFolder(DesktopFolder):
        async def write(self, data: bytes) -> None:
            await super().write(data[:2])

    async def scenario() -> None:
        (tmp_path / TARGET).write_bytes(b"original")
        session = StorageSession(TruncatingFolder(str(tmp_path)))
        await session.read()
        session.draft = "replacement"
        with pytest.raises(StorageError, match="read-back differs"):
            await session.write()
        assert session.draft == "replacement"
        assert not session.loaded
        assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"

    asyncio.run(scenario())


def test_unchanged_poll_and_restart_require_explicit_baseline(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / TARGET).write_bytes(b"original")
        session = StorageSession(DesktopFolder(str(tmp_path)))
        await session.read()
        assert not await session.poll()
        restarted = StorageSession(DesktopFolder(str(tmp_path)))
        with pytest.raises(StorageError, match="Read first"):
            await restarted.write()
        assert await restarted.read()
        assert restarted.last_hash == session.last_hash

    asyncio.run(scenario())


@pytest.mark.parametrize("poll_in_progress", [False, True])
def test_ui_conflict_retains_visible_unsaved_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, poll_in_progress: bool
) -> None:
    async def scenario() -> None:
        path = tmp_path / TARGET
        path.write_bytes(b"original")
        view = StorageView()
        view.update = MagicMock()
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(StorageView, "page", property(lambda self: page))
        view.session = StorageSession(DesktopFolder(str(tmp_path)))
        await view.session.read()
        view.editor.value = "unsaved editor text"
        path.write_bytes(b"external edit")
        if poll_in_progress:
            await view.lock.acquire()
            action = asyncio.create_task(view._operate("write"))
            await asyncio.sleep(0)
            view.lock.release()
            await action
        else:
            await view._operate("write")
        assert view.editor.value == "unsaved editor text"
        assert view.session.draft == "unsaved editor text"
        assert "Conflict" in (view.result.value or "")
        page.show_dialog.assert_called_once()
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog)
        assert dialog.modal
        assert dialog.title == "Write blocked: external change"
        assert isinstance(dialog.content, ft.Text)
        assert "not overwritten" in (dialog.content.value or "")
        assert "unsaved text" in (dialog.content.value or "")
        assert view.controls.index(view.result) < view.controls.index(view.editor)
        assert view.access.value == await view.session.folder.status()
        assert path.read_bytes() == b"external edit"
        assert not view.editor.disabled
        view._close_conflict(MagicMock())
        page.pop_dialog.assert_called_once()
        assert view.editor.value == "unsaved editor text"

    asyncio.run(scenario())
