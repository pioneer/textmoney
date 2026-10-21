import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.device_info import DeviceInfo
from form_controls import click_async

from app.domain.backups import BackupEntry, backup_name, backup_time, retention_plan
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import DesktopPreferences
from app.journal import FILENAME, Journal
from app.storage import StorageError, StorageSession
from app.ui.finance import BACKUP_CLEANUP_KEY, FinanceView

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def entry(created: datetime, index: int = 0, *, state: str = "committed") -> BackupEntry:
    name = f"txncount-{created:%Y%m%dT%H%M%S%fZ}-{index:032x}-{state}.beancount"
    return BackupEntry(name, 8, created)


def snapshots(path: Path, count: int = 40) -> list[BackupEntry]:
    directory = path / "backups"
    directory.mkdir(exist_ok=True)
    now = datetime.now(UTC)
    entries = [entry(now - timedelta(minutes=i), i) for i in range(count)]
    for item in entries:
        (directory / item.name).write_bytes(b"original")
    return entries


def test_retention_keeps_exact_latest_twenty_when_tiers_overlap() -> None:
    entries = [entry(NOW - timedelta(seconds=i), i) for i in range(100)]
    plan = retention_plan(entries, NOW)
    assert plan.remove == tuple(entries[20:])
    assert plan.size == 80 * 8
    assert not retention_plan(entries[:20], NOW).remove


def test_daily_monthly_tiers_overlap_and_keep_latest_in_each_bucket() -> None:
    entries = [
        entry(NOW - timedelta(days=day, hours=hour), day * 10 + hour)
        for day in range(800)
        for hour in (0, 1)
    ]
    removed = {item.name for item in retention_plan(entries, NOW).remove}
    kept = [item for item in entries if item.name not in removed]
    assert len(kept) == 50  # 20 latest + 20 older daily + 10 older monthly.
    for day in range(30):
        latest = entries[day * 2]
        assert latest.name not in removed
    for month in range(12):
        year, number = divmod(NOW.year * 12 + NOW.month - 1 - month, 12)
        in_month = [
            item
            for item in entries
            if (item.created.year, item.created.month) == (year, number + 1)
        ]
        assert max(in_month, key=lambda item: item.created).name not in removed
    assert entries[60].name in removed  # The 31st calendar day is not a daily snapshot.
    assert all(item.created >= datetime(2025, 11, 1, tzinfo=UTC) for item in kept)


def test_future_snapshots_are_preserved_and_sparse_old_history_keeps_twenty() -> None:
    future = [entry(NOW + timedelta(days=i + 1), i) for i in range(30)]
    assert not retention_plan(future, NOW).remove
    old = [entry(NOW - timedelta(days=1000 + i), i) for i in range(21)]
    assert retention_plan(old, NOW).remove == (old[-1],)


@pytest.mark.parametrize(
    "name",
    [
        "txncount-" + "a" * 32 + ".beancount",
        "../txncount.beancount",
        "txncount-20269999T120000000000Z-" + "a" * 32 + "-committed.beancount",
        "other-20261021T120000000000Z-" + "a" * 32 + "-committed.beancount",
    ],
)
def test_unrecognized_and_legacy_names_are_never_eligible(name: str) -> None:
    assert backup_time(name, "txncount") is None


def test_names_embed_utc_time_and_unique_id_and_start_protected() -> None:
    name = backup_name("txncount")
    assert backup_time(name, "txncount") is None
    created = backup_time(name, "txncount", pending=True)
    assert created is not None
    assert abs((datetime.now(UTC) - created).total_seconds()) < 5
    assert backup_name("txncount") != name
    assert backup_time(name, "storage-test", pending=True) is None


def test_verified_save_prunes_but_keeps_last_recovery_and_unrecognized_files(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (tmp_path / FILENAME).write_bytes(b"original")
        snapshots(tmp_path)
        protected = entry(NOW - timedelta(days=800), state="pending")
        directory = tmp_path / "backups"
        (directory / protected.name).write_bytes(b"recovery")
        legacy = "txncount-" + "a" * 32 + ".beancount"
        for name in [legacy, "notes.txt", "storage-test-" + "b" * 32 + ".beancount"]:
            (directory / name).write_bytes(b"unrelated")
        session = StorageSession(DesktopFolder(str(tmp_path), FILENAME))
        await session.read()
        session.draft = "updated"
        backup = await session.write()
        assert backup is not None
        assert (tmp_path / backup).read_bytes() == b"original"
        assert "-committed." in backup
        assert len(await session.folder.list_backups()) == 20
        assert (directory / protected.name).read_bytes() == b"recovery"
        assert (directory / legacy).read_bytes() == b"unrelated"
        assert (directory / "notes.txt").exists()
        assert not session.cleanup_error

    asyncio.run(scenario())


def test_disable_automatic_cleanup_still_finalizes_and_manual_cleanup_has_preview(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path), FILENAME)
        snapshots(tmp_path)
        (tmp_path / FILENAME).write_bytes(b"original")
        session = StorageSession(folder)
        session.auto_cleanup = False
        await session.read()
        session.draft = "updated"
        await session.write()
        assert len(await folder.list_backups()) == 41
        preview = await session.preview_cleanup()
        assert len(preview.remove) == 21
        assert preview.size == 21 * 8
        assert len(await folder.list_backups()) == 41  # Preview does not delete.
        assert await session.cleanup_backups(preview) == 21
        assert len(await folder.list_backups()) == 20

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["write", "readback", "conflict", "timeout", "cancelled"])
def test_failed_write_preserves_pending_copy_across_restart_and_cleanup(
    tmp_path: Path, failure: str
) -> None:
    class FailingFolder(DesktopFolder):
        async def write(self, data: bytes, *, expected_hash: str | None) -> None:
            if failure == "write":
                raise StorageError("Synthetic write failure.")
            if failure == "timeout":
                raise TimeoutError("Synthetic timeout.")
            if failure == "cancelled":
                raise asyncio.CancelledError()
            await super().write(b"truncated", expected_hash=expected_hash)

        async def backup(self, data: bytes) -> str:
            result = await super().backup(data)
            if failure == "conflict":
                (tmp_path / FILENAME).write_bytes(b"external")
            return result

    async def scenario() -> None:
        snapshots(tmp_path)
        (tmp_path / FILENAME).write_bytes(b"original")
        folder = FailingFolder(str(tmp_path), FILENAME)
        folder.list_backups = AsyncMock()
        session = StorageSession(folder)
        await session.read()
        session.draft = "updated"
        with pytest.raises((StorageError, ConflictError, TimeoutError, asyncio.CancelledError)):
            await session.write()
        folder.list_backups.assert_not_awaited()
        pending = list((tmp_path / "backups").glob("*-pending.beancount"))
        assert len(pending) == 1 and pending[0].read_bytes() == b"original"
        restarted = StorageSession(DesktopFolder(str(tmp_path), FILENAME))
        await restarted.read()
        await restarted.cleanup_backups()
        assert pending[0].read_bytes() == b"original"

    asyncio.run(scenario())


def test_cancelled_maintenance_requires_journal_reload_before_another_save(tmp_path: Path) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path), FILENAME)
        journal = Journal(folder)
        await journal.load()
        await journal.create_account("Bank", "Bank", "UAH")
        folder.commit_backup = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await journal.create_category("Food", "Food")
        assert not journal.ready and not journal.storage.loaded
        await journal.load()
        assert any(category.id == "Expenses:Food" for category in journal.ledger.categories)
        assert list((tmp_path / "backups").glob("*-pending.beancount"))

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["commit_backup", "list_backups", "delete_backup"])
def test_cleanup_failure_does_not_turn_verified_save_into_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    async def scenario() -> None:
        snapshots(tmp_path)
        (tmp_path / FILENAME).write_bytes(b"original")
        folder = DesktopFolder(str(tmp_path), FILENAME)
        monkeypatch.setattr(
            folder, operation, AsyncMock(side_effect=StorageError("Synthetic failure."))
        )
        session = StorageSession(folder)
        await session.read()
        session.draft = "updated"
        backup = await session.write()
        assert backup is not None
        assert (tmp_path / backup).read_bytes() == b"original"
        assert (tmp_path / FILENAME).read_bytes() == b"updated"
        assert session.loaded and session.cleanup_error
        assert "Journal saved, but backup maintenance failed" in caplog.text

    asyncio.run(scenario())


def test_manual_preview_rechecks_external_changes_and_never_expands_approval(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        snapshots(tmp_path)
        (tmp_path / FILENAME).write_bytes(b"original")
        session = StorageSession(DesktopFolder(str(tmp_path), FILENAME))
        await session.read()
        preview = await session.preview_cleanup()
        late = entry(datetime.now(UTC) - timedelta(days=1000), 900)
        late_path = tmp_path / "backups" / late.name
        late_path.write_bytes(b"original")
        await session.cleanup_backups(preview)
        assert late_path.exists()  # It was not in the approved preview.
        (tmp_path / FILENAME).write_bytes(b"external")
        with pytest.raises(ConflictError):
            await session.cleanup_backups(preview)
        assert late_path.exists()

    asyncio.run(scenario())


def test_desktop_rejects_symlinks_and_changed_size_and_uses_filename_not_mtime(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        entries = snapshots(tmp_path)
        folder = DesktopFolder(str(tmp_path), FILENAME)
        path = tmp_path / "backups" / entries[-1].name
        path.write_bytes(b"changed size")
        with pytest.raises(StorageError, match="changed"):
            await folder.delete_backup(entries[-1])
        with pytest.raises(StorageError, match="unrecognized"):
            await folder.delete_backup(entry(NOW, state="pending"))
        assert (await folder.list_backups())[0].created == backup_time(
            (await folder.list_backups())[0].name, "txncount"
        )
        try:
            (tmp_path / "linked").symlink_to(tmp_path / "backups", target_is_directory=True)
            path.unlink()
            path.symlink_to(tmp_path / FILENAME)
        except OSError:
            pytest.skip("Host does not allow symlinks")
        assert entries[-1].name not in {item.name for item in await folder.list_backups()}
        with pytest.raises(StorageError):
            await folder.delete_backup(entries[-1])

    asyncio.run(scenario())


def test_android_backup_retention_transport_is_scoped_and_validated() -> None:
    async def scenario() -> None:
        bridge = MagicMock()
        pending = entry(NOW, state="pending").name
        committed = entry(NOW)
        bridge.call = AsyncMock(
            side_effect=[
                {"ok": True, "value": f"backups/{committed.name}"},
                {
                    "ok": True,
                    "value": [{"name": committed.name, "size": 8}, {"name": pending, "size": 8}],
                },
                {"ok": True, "value": True},
            ]
        )
        folder = AndroidFolder(bridge, "content://synthetic/tree/test", FILENAME)
        assert await folder.commit_backup(f"backups/{pending}") == f"backups/{committed.name}"
        assert await folder.list_backups() == [committed]
        await folder.delete_backup(committed)
        assert bridge.call.call_args.args == ("delete_backup",)
        assert bridge.call.call_args.kwargs["filename"] == FILENAME
        assert bridge.call.call_args.kwargs["name"] == committed.name
        with pytest.raises(StorageError):
            await folder.delete_backup(entry(NOW, state="pending"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value", [None, {}, [{"name": "x", "size": -1}], [{"name": "x", "size": True}]]
)
def test_android_rejects_malformed_listing(value: object) -> None:
    bridge = MagicMock()
    bridge.call = AsyncMock(return_value={"ok": True, "value": value})
    with pytest.raises(StorageError):
        asyncio.run(AndroidFolder(bridge, "content://synthetic/tree/test", FILENAME).list_backups())


def test_settings_toggle_persists_and_preview_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.language = "uk"
        view.preferences = DesktopPreferences(tmp_path / "settings.json")
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Bank", "Bank", "UAH")
        view.journal = journal
        view.backup_cleanup.value = False
        await view._backup_cleanup_changed(MagicMock(control=view.backup_cleanup))
        assert await view.preferences.get(BACKUP_CLEANUP_KEY) == "off"
        assert not journal.storage.auto_cleanup
        snapshots(tmp_path)
        await view._preview_backup_cleanup(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog)
        assert isinstance(dialog.content, ft.Text)
        assert "20" in dialog.content.value
        assert len(await journal.storage.folder.list_backups()) == 40
        confirm = dialog.actions[-1]
        assert isinstance(confirm, ft.TextButton)
        await click_async(confirm)
        assert len(await journal.storage.folder.list_backups()) == 20
        assert view.error_message is None
        journal.storage.cleanup_error = True
        view._saved("backups/test")
        assert view.message_key == "backups_saved_warning"

    asyncio.run(scenario())


@pytest.mark.parametrize("saved", [None, "on", "off", "invalid"])
def test_retention_preference_restores_before_attaching_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved: str | None
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        preferences = DesktopPreferences()
        await preferences.set("txncount.folder.v1", str(tmp_path))
        if saved is not None:
            await preferences.set(BACKUP_CLEANUP_KEY, saved)
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.width = 390
        page.locale_configuration = ft.LocaleConfiguration()
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(locales=[ft.Locale(language_code="uk")])
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))
        view = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        if saved == "invalid":
            assert view.error_message == view.tr("backups_invalid_setting")
            assert view.journal is None
        else:
            assert view.journal is not None
            assert view.journal.storage.auto_cleanup == (saved != "off")
            assert view.backup_cleanup.value == (saved != "off")

    asyncio.run(scenario())


def test_failed_preference_save_does_not_change_effective_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        view.journal = journal
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=False)
        view.backup_cleanup.value = False
        await view._backup_cleanup_changed(MagicMock(control=view.backup_cleanup))
        assert view.auto_backup_cleanup and journal.storage.auto_cleanup
        assert view.backup_cleanup.value
        assert view.error_message == view.tr("backups_setting_failed")

    asyncio.run(scenario())


def test_cleanup_confirmation_refuses_switched_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Bank", "Bank", "UAH")
        view.journal = journal
        snapshots(tmp_path)
        await view._preview_backup_cleanup(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        confirm = dialog.actions[-1]
        assert isinstance(confirm, ft.TextButton)
        view.journal = None
        await click_async(confirm)
        assert view.error_message == "crud_stale"
        assert len(await journal.storage.folder.list_backups()) == 40

    asyncio.run(scenario())
