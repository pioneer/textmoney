import asyncio
import errno
import hashlib
import os
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from app.domain.storage import ConflictError, digest
from app.infrastructure.android_storage import AndroidFolder, unwrap
from app.infrastructure.desktop_storage import TARGET, DesktopFolder
from app.storage import SAMPLE, StorageError, StorageSession, WriteError
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


def test_atomic_replacement_orders_sync_check_replace_and_directory_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        target = tmp_path / TARGET
        target.write_bytes(b"original")
        folder = DesktopFolder(str(tmp_path))
        session = StorageSession(folder)
        await session.read()
        session.draft = "complete replacement"
        events: list[str] = []
        original_replace, original_fsync, original_read = os.replace, os.fsync, folder._read

        def sync(descriptor: int) -> None:
            events.append(
                "directory-sync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file-sync"
            )
            original_fsync(descriptor)

        def read() -> bytes | None:
            events.append("read")
            return original_read()

        def replace(source: Path, destination: Path) -> None:
            assert source.parent == destination.parent == tmp_path
            assert source.name.startswith(f".{TARGET}.") and source.suffix == ".tmp"
            assert source.read_bytes() == b"complete replacement"
            assert destination.read_bytes() == b"original"
            assert events[-2:] == ["file-sync", "read"]
            events.append("replace")
            original_replace(source, destination)

        monkeypatch.setattr(os, "replace", replace)
        monkeypatch.setattr(os, "fsync", sync)
        monkeypatch.setattr(folder, "_read", read)
        backup = await session.write()
        assert backup and "-committed." in backup
        assert (tmp_path / backup).read_bytes() == b"original"
        assert target.read_bytes() == b"complete replacement"
        assert session.last_hash == digest(target.read_bytes())
        assert not list(tmp_path.glob(f".{TARGET}.*.tmp"))
        if sys.platform != "win32":
            assert events[events.index("replace") + 1] == "directory-sync"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["create", "permissions", "partial", "short", "flush", "fsync", "replace"]
)
def test_atomic_write_failures_preserve_original_and_pending_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        target = tmp_path / TARGET
        target.write_bytes(b"original")
        folder = DesktopFolder(str(tmp_path))
        session = StorageSession(folder)
        await session.read()
        session.draft = "complete replacement"
        original_backup = folder.backup
        original_temporary = tempfile.NamedTemporaryFile

        def temporary(*, mode: Literal["wb"], prefix: str, suffix: str, dir: Path, delete: bool):
            stream = original_temporary(
                mode=mode, prefix=prefix, suffix=suffix, dir=dir, delete=delete
            )
            original_write = stream.write

            def write(data: bytes) -> int:
                original_write(data[:2])
                if failure == "short":
                    return 2
                raise OSError(errno.ENOSPC, "synthetic partial write")

            if failure in {"partial", "short"}:
                monkeypatch.setattr(stream, "write", write)
            elif failure == "flush":
                monkeypatch.setattr(
                    stream, "flush", MagicMock(side_effect=OSError(errno.ENOSPC, "synthetic flush"))
                )
            return stream

        async def backup(data: bytes) -> str:
            result = await original_backup(data)
            if failure == "create":
                monkeypatch.setattr(
                    tempfile, "NamedTemporaryFile", MagicMock(side_effect=PermissionError())
                )
            elif failure == "fsync":
                monkeypatch.setattr(
                    os, "fsync", MagicMock(side_effect=OSError(errno.EIO, "synthetic fsync"))
                )
            elif failure == "replace":
                monkeypatch.setattr(os, "replace", MagicMock(side_effect=PermissionError()))
            elif failure == "permissions":
                monkeypatch.setattr(
                    "app.infrastructure.desktop_storage._preserve_permissions",
                    MagicMock(side_effect=PermissionError()),
                )
            else:
                monkeypatch.setattr(tempfile, "NamedTemporaryFile", temporary)
            return result

        monkeypatch.setattr(folder, "backup", backup)
        with pytest.raises(WriteError) as error:
            await session.write()
        assert error.value.committed is (None if failure == "replace" else False)
        assert target.read_bytes() == b"original"
        pending = list((tmp_path / "backups").glob("*-pending.beancount"))
        assert len(pending) == 1 and pending[0].read_bytes() == b"original"
        assert not list((tmp_path / "backups").glob("*-committed.beancount"))
        assert session.draft == "complete replacement" and not session.loaded
        staged = list(tmp_path.glob(f".{TARGET}.*.tmp"))
        if failure == "replace":
            assert len(staged) == 1 and staged[0].read_bytes() == b"complete replacement"
            assert staged[0].name in str(error.value)
        else:
            assert not staged

    asyncio.run(scenario())


@pytest.mark.parametrize("initial", [b"original", None])
def test_atomic_write_rechecks_baseline_after_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: bytes | None
) -> None:
    async def scenario() -> None:
        target = tmp_path / TARGET
        if initial is not None:
            target.write_bytes(initial)
        folder = DesktopFolder(str(tmp_path))
        session = StorageSession(folder)
        await session.read()
        session.draft = "candidate"
        original_fsync = os.fsync

        def sync(descriptor: int) -> None:
            original_fsync(descriptor)
            target.write_bytes(b"external edit during staging")

        original_backup = folder.backup

        async def backup(data: bytes) -> str:
            result = await original_backup(data)
            monkeypatch.setattr(os, "fsync", sync)
            return result

        monkeypatch.setattr(folder, "backup", backup)
        if initial is None:
            monkeypatch.setattr(os, "fsync", sync)
        with pytest.raises(ConflictError):
            await session.write()
        assert target.read_bytes() == b"external edit during staging"
        assert session.external_changes and not session.loaded
        assert not list(tmp_path.glob(f".{TARGET}.*.tmp"))
        if initial is not None:
            assert next((tmp_path / "backups").iterdir()).read_bytes() == initial

    asyncio.run(scenario())


def test_atomic_replacement_preserves_existing_permissions(tmp_path: Path) -> None:
    async def scenario() -> None:
        target = tmp_path / TARGET
        target.write_bytes(b"original")
        target.chmod(0o640 if sys.platform != "win32" else 0o600)
        previous = target.stat()
        session = StorageSession(DesktopFolder(str(tmp_path)))
        await session.read()
        session.draft = "replacement"
        await session.write()
        current = target.stat()
        assert stat.S_IMODE(current.st_mode) == stat.S_IMODE(previous.st_mode)
        if sys.platform != "win32":
            assert (current.st_uid, current.st_gid) == (previous.st_uid, previous.st_gid)

    asyncio.run(scenario())


def test_final_baseline_read_failure_does_not_replace_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / TARGET
    target.write_bytes(b"original")
    folder = DesktopFolder(str(tmp_path))
    session = StorageSession(folder)
    asyncio.run(session.read())
    session.draft = "replacement"
    backup, fsync = folder.backup, os.fsync

    def sync(descriptor: int) -> None:
        fsync(descriptor)
        monkeypatch.setattr(
            folder, "_read", MagicMock(side_effect=StorageError("synthetic read failure"))
        )

    async def fail(data: bytes) -> str:
        result = await backup(data)
        monkeypatch.setattr(os, "fsync", sync)
        return result

    monkeypatch.setattr(folder, "backup", fail)
    with pytest.raises(WriteError, match="original not modified") as error:
        asyncio.run(session.write())
    assert error.value.committed is False
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(f".{TARGET}.*.tmp"))
    assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"
    assert not session.loaded


def test_new_atomic_journal_is_private_on_posix(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")
    session = StorageSession(DesktopFolder(str(tmp_path)))
    asyncio.run(session.read())
    asyncio.run(session.write())
    assert stat.S_IMODE((tmp_path / TARGET).stat().st_mode) == 0o600


@pytest.mark.parametrize("existing_acl", [True, False])
def test_atomic_replacement_preserves_linux_access_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_acl: bool
) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux POSIX access ACL")
    from app.infrastructure import desktop_storage

    target = tmp_path / TARGET
    target.write_bytes(b"original")
    target.chmod(0o640)
    # Linux ACL xattr: owner, named user, group, mask and other.
    entries = [(1, 6, 0xFFFFFFFF), (2, 4, os.getuid() + 1), (4, 0, 0xFFFFFFFF)]
    entries += [(16, 4, 0xFFFFFFFF), (32, 0, 0xFFFFFFFF)]
    acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)
    directory_entries = [(1, 7, 0xFFFFFFFF), *entries[1:]]
    directory_acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", *entry) for entry in directory_entries
    )
    try:
        if existing_acl:
            os.setxattr(target, "system.posix_acl_access", acl)
        os.setxattr(tmp_path, "system.posix_acl_default", directory_acl)
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            pytest.skip("Filesystem does not support access ACLs")
        raise
    original_permissions = desktop_storage._preserve_permissions

    def permissions(source: Path, temporary: Path, metadata: os.stat_result) -> None:
        assert temporary.stat().st_size == 0
        if sys.platform == "linux":
            assert "system.posix_acl_access" in os.listxattr(temporary)
        original_permissions(source, temporary, metadata)

    monkeypatch.setattr(desktop_storage, "_preserve_permissions", permissions)
    session = StorageSession(DesktopFolder(str(tmp_path)))
    asyncio.run(session.read())
    session.draft = "replacement"
    asyncio.run(session.write())
    assert target.read_bytes() == b"replacement"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    if existing_acl:
        assert os.getxattr(target, "system.posix_acl_access") == acl
    else:
        assert "system.posix_acl_access" not in os.listxattr(target)


def test_temporary_cleanup_failure_is_explicit_and_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / TARGET
    target.write_bytes(b"original")
    folder = DesktopFolder(str(tmp_path))
    session = StorageSession(folder)
    asyncio.run(session.read())
    session.draft = "replacement"
    backup = folder.backup

    async def fail(data: bytes) -> str:
        result = await backup(data)
        monkeypatch.setattr(os, "fsync", MagicMock(side_effect=OSError(errno.EIO, "fsync")))
        monkeypatch.setattr(Path, "unlink", MagicMock(side_effect=PermissionError()))
        return result

    monkeypatch.setattr(folder, "backup", fail)
    with pytest.raises(StorageError, match="cleanup failed"):
        asyncio.run(session.write())
    assert target.read_bytes() == b"original"
    assert len(list(tmp_path.glob(f".{TARGET}.*.tmp"))) == 1
    assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"
    assert not session.loaded
    assert "Could not remove an uncommitted temporary journal" in caplog.text


def test_replace_exception_can_have_an_uncertain_commit_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / TARGET
    target.write_bytes(b"original")
    session = StorageSession(DesktopFolder(str(tmp_path)))
    asyncio.run(session.read())
    session.draft = "replacement"
    replace = os.replace

    def uncertain(source: Path, destination: Path) -> None:
        replace(source, destination)
        raise OSError(errno.EIO, "synthetic uncertain replacement response")

    replacement = MagicMock(side_effect=uncertain)
    monkeypatch.setattr(os, "replace", replacement)
    with pytest.raises(WriteError) as error:
        asyncio.run(session.write())
    assert error.value.committed is None
    assert target.read_bytes() == b"replacement"
    assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"
    assert not session.loaded
    with pytest.raises(StorageError, match="Read first"):
        asyncio.run(session.write())
    replacement.assert_called_once()


@pytest.mark.parametrize("failure", ["directory", "readback"])
def test_post_replacement_failure_is_uncertain_and_keeps_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        target = tmp_path / TARGET
        target.write_bytes(b"original")
        folder = DesktopFolder(str(tmp_path))
        session = StorageSession(folder)
        await session.read()
        session.draft = "replacement"
        replace = os.replace

        def commit(source: Path, destination: Path) -> None:
            replace(source, destination)
            if failure == "readback":
                monkeypatch.setattr(folder, "read", AsyncMock(return_value=b"unexpected"))

        monkeypatch.setattr(os, "replace", commit)
        if failure == "directory":
            monkeypatch.setattr(
                "app.infrastructure.desktop_storage._sync_directory",
                MagicMock(side_effect=OSError(errno.EIO, "synthetic directory failure")),
            )
        with pytest.raises(StorageError) as error:
            await session.write()
        if failure == "directory":
            assert isinstance(error.value, WriteError) and error.value.committed is True
            assert "already be committed" in str(error.value)
        assert not session.loaded
        assert target.read_bytes() == b"replacement"
        assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"
        assert not list(tmp_path.glob(f".{TARGET}.*.tmp"))
        assert not list((tmp_path / "backups").glob("*-committed.beancount"))

    asyncio.run(scenario())


def test_unsupported_directory_sync_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.infrastructure.desktop_storage import _sync_directory

    if sys.platform != "win32":
        monkeypatch.setattr(
            os, "fsync", MagicMock(side_effect=OSError(errno.EINVAL, "unsupported"))
        )
    _sync_directory(tmp_path)
    assert "durability is limited" in caplog.text


def test_process_interruption_before_replace_leaves_main_and_backup_intact(tmp_path: Path) -> None:
    target = tmp_path / TARGET
    target.write_bytes(b"original")
    code = """
import asyncio, os, time
from app.infrastructure.desktop_storage import DesktopFolder
from app.storage import StorageSession
def interrupted(source, destination):
    print('STAGED', flush=True)
    time.sleep(60)
os.replace = interrupted
async def main():
    session = StorageSession(DesktopFolder(os.environ['SYNTHETIC_FOLDER']))
    await session.read()
    session.draft = 'complete replacement'
    await session.write()
asyncio.run(main())
"""
    with pytest.raises(subprocess.TimeoutExpired) as error:
        subprocess.run(
            [sys.executable, "-c", code],
            env=os.environ
            | {
                "SYNTHETIC_FOLDER": str(tmp_path),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            },
            capture_output=True,
            timeout=10,
            check=True,
        )
    assert b"STAGED" in (error.value.stdout or b"")
    assert target.read_bytes() == b"original"
    assert next((tmp_path / "backups").iterdir()).read_bytes() == b"original"
    staged = list(tmp_path.glob(f".{TARGET}.*.tmp"))
    assert len(staged) == 1 and staged[0].read_bytes() == b"complete replacement"


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
            monkeypatch.setattr(
                "app.infrastructure.desktop_storage.tempfile.NamedTemporaryFile",
                MagicMock(side_effect=PermissionError()),
            )
        with pytest.raises(StorageError, match="failed"):
            await (session.read() if operation == "read" else session.write())
        assert session.draft == "unsaved"
        if operation == "write":
            assert not session.loaded

    asyncio.run(scenario())


def test_write_failure_keeps_previous_verified_backup(tmp_path: Path) -> None:
    class FailingFolder(DesktopFolder):
        async def write(self, data: bytes, *, expected_hash: str | None) -> None:
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
                {"ok": True, "value": False},
            ]
        )
        folder = AndroidFolder(bridge, "content://synthetic/tree/test")
        assert "persisted" in await folder.status()
        assert await folder.read() == b"synthetic"
        assert await folder.backup(b"synthetic") == "backups/synthetic.beancount"
        with pytest.raises(StorageError, match="grant missing"):
            await folder.status()

    asyncio.run(scenario())


def test_readback_mismatch_requires_new_read_and_keeps_backup(tmp_path: Path) -> None:
    class TruncatingFolder(DesktopFolder):
        async def write(self, data: bytes, *, expected_hash: str | None) -> None:
            await super().write(data[:2], expected_hash=expected_hash)

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
