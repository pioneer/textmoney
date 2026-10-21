"""Fault-injected SAF model tests, not proof of Android/provider durability."""

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click_async

from app.domain.recovery import SaveRecord
from app.domain.storage import ConflictError, digest
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import RecoveryRequired, StorageError, StorageSession, WriteError
from app.ui.finance import FinanceView


def encoded(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode("ascii")


def decoded(value: object) -> bytes | None:
    assert value is None or isinstance(value, str)
    return None if value is None else base64.b64decode(value)


class SafProvider:
    """Model persistent documents across fresh adapter/journal instances."""

    def __init__(self, data: bytes | None) -> None:
        self.data = data
        self.marker = (
            None
            if data is None
            else SaveRecord(None, digest(data) or "", None, "complete").encode()
        )
        self.backups: dict[str, bytes] = {}
        self.archives: list[bytes] = []
        self.fault = ""
        self.writes = 0
        self.calls: list[str] = []
        self.race = False
        self.bridge = MagicMock()
        self.bridge.call = AsyncMock(side_effect=self.call)

    def folder(self) -> AndroidFolder:
        return AndroidFolder(self.bridge, "content://synthetic/tree/test", FILENAME)

    def snapshot(self) -> dict[str, object]:
        backup = None
        if self.marker is not None:
            try:
                record = SaveRecord.decode(self.marker, "txncount")
            except ValueError:
                record = None
            if record and record.backup:
                name = record.backup.removeprefix("backups/")
                backup = self.backups.get(
                    name, self.backups.get(name.replace("-pending.", "-committed."))
                )
        return {
            "current": encoded(self.data),
            "marker": encoded(self.marker),
            "pending": sorted(name for name in self.backups if "-pending." in name),
            "backup": encoded(backup),
        }

    async def call(self, method: str, **args: object) -> dict[str, object]:
        self.calls.append(method)
        return {"ok": True, "value": await self.execute(method, args)}

    async def execute(self, method: str, args: dict[str, object]) -> object:
        if method == "status":
            return True
        if method == "read":
            return encoded(self.data)
        if method == "recovery":
            return self.snapshot()
        if method == "backup":
            name, data = args["name"], decoded(args["data"])
            assert isinstance(name, str) and data is not None
            self.backups[name] = data
            return f"backups/{name}"
        if method == "write":
            if self.fault == "before":
                raise StorageError("Synthetic permission failure")
            record = decoded(args["record"])
            assert record is not None
            if self.fault == "metadata":
                self.marker = record[:10]
                raise StorageError("Synthetic marker persistence failure")
            assert encoded(self.marker) == args["marker"]
            assert encoded(self.data) == args["previous"]
            intent = SaveRecord.decode(record, "txncount")
            if self.data is not None:
                name = (intent.backup or "").removeprefix("backups/")
                if self.backups.get(name) != self.data:
                    raise StorageError("Pre-write recovery backup is missing or corrupt")
            self.marker = record
            if self.fault == "prepared":
                raise StorageError("Synthetic interruption before journal modification")
            self.writes += 1
            if self.fault == "partial":
                self.data = b""
                raise StorageError("Synthetic disk full")
            self.data = decoded(args["data"])
            if self.fault == "timeout":
                raise TimeoutError("Native operation may already have completed")
            if self.fault == "cancel":
                raise asyncio.CancelledError()
            return None
        if method == "finish_write":
            if self.fault == "finish":
                raise StorageError("Synthetic completion record failure")
            assert encoded(self.marker) == args["record"]
            assert encoded(self.data) == args["data"]
            self.marker = decoded(args["completed"])
            return None
        if method == "commit_backup":
            if self.fault == "finalize":
                raise StorageError("Synthetic rename failure")
            name = args["name"]
            assert isinstance(name, str)
            committed = name.replace("-pending.", "-committed.")
            self.backups[committed] = self.backups.pop(name)
            return f"backups/{committed}"
        if method == "list_backups":
            return []
        if method == "accept_recovery":
            if self.race:
                self.data = b"; external edit during recovery\n"
            record = SaveRecord.decode(decoded(args["record"]) or b"", "txncount")
            pending = sorted(
                name
                for name in self.backups
                if "-pending." in name and f"backups/{name}" != record.backup
            )
            if (
                encoded(self.marker) != args["marker"]
                or encoded(self.data) != args["current"]
                or pending != args["pending"]
            ):
                raise StorageError("Journal changed during recovery. Reload and review again.")
            if self.marker is not None:
                self.archives.append(self.marker)
            self.marker = record.encode()
            return None
        raise AssertionError(f"Unexpected bridge operation: {method}")


@pytest.fixture
def books(tmp_path: Path) -> tuple[bytes, bytes, bytes]:
    async def seed() -> tuple[bytes, bytes, bytes]:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_account("Cash", "Cash", "USD", "100")
        await journal.create_category("Food", "Food")
        truncated = (tmp_path / FILENAME).read_bytes()
        await journal.add_expense("Assets:Cash", "Expenses:Food", "1", "First", "2026-01-01")
        previous = (tmp_path / FILENAME).read_bytes()
        await journal.add_expense("Assets:Cash", "Expenses:Food", "2", "Second", "2026-01-02")
        return previous, (tmp_path / FILENAME).read_bytes(), truncated

    return asyncio.run(seed())


async def prepare(provider: SafProvider, previous: bytes, candidate: bytes) -> StorageSession:
    session = StorageSession(provider.folder())
    await session.read()
    session.draft = candidate.decode()
    assert provider.data == previous
    return session


def test_successful_android_save_and_fresh_instance_load(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        session = await prepare(provider, previous, candidate)
        backup = await session.write()
        assert backup and "-committed." in backup
        assert provider.backups[backup.removeprefix("backups/")] == previous
        assert provider.data == candidate and provider.writes == 1
        assert provider.marker is not None
        assert SaveRecord.decode(provider.marker, "txncount").phase == "complete"
        assert provider.calls.index("backup") < provider.calls.index("write")
        assert provider.calls.index("write") < provider.calls.index("finish_write")
        journal = Journal(provider.folder())
        await journal.load()
        assert journal.ready and len(journal.ledger.transactions) == 3

    asyncio.run(scenario())


def test_first_journal_creation_has_recovery_record_without_previous_backup(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        _, candidate, _ = books
        provider = SafProvider(None)
        session = StorageSession(provider.folder())
        assert not await session.read()
        session.draft = candidate.decode()
        assert await session.write() is None
        assert provider.marker is not None
        record = SaveRecord.decode(provider.marker, "txncount")
        assert record.previous is record.backup is None and record.phase == "complete"
        assert not provider.backups and provider.data == candidate
        await Journal(provider.folder()).load()

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [True, False])
def test_native_backup_recheck_prevents_write_if_recovery_copy_changed(
    books: tuple[bytes, bytes, bytes], missing: bool
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        folder = provider.folder()
        backup = await folder.backup(previous)
        name = backup.removeprefix("backups/")
        if missing:
            del provider.backups[name]
        else:
            provider.backups[name] = b"corrupt"
        marker = provider.marker
        with pytest.raises(WriteError):
            await folder.write_guarded(candidate, expected_hash=digest(previous), backup=backup)
        assert provider.writes == 0 and provider.data == previous
        assert provider.marker == marker

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault,modified",
    [
        ("before", False),
        ("metadata", False),
        ("prepared", False),
        ("partial", True),
        ("timeout", True),
        ("cancel", True),
        ("finish", True),
    ],
)
def test_failures_are_visible_and_block_a_fresh_journal(
    books: tuple[bytes, bytes, bytes], fault: str, modified: bool
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        session = await prepare(provider, previous, candidate)
        provider.fault = fault
        with pytest.raises((WriteError, asyncio.CancelledError)):
            await session.write()
        assert (provider.data != previous) is modified
        assert previous in provider.backups.values()
        assert not session.loaded and session.draft == candidate.decode()
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        assert not journal.ready
        with pytest.raises(StorageError, match="Read first"):
            await journal.storage.write()
        assert provider.writes <= 1
        if fault == "metadata":
            assert provider.writes == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["unchanged", "candidate", "unexpected"])
def test_crash_before_finalization_classifies_and_verifies_backup(
    books: tuple[bytes, bytes, bytes], state: str
) -> None:
    async def scenario() -> None:
        previous, candidate, truncated = books
        provider = SafProvider(previous)
        folder = provider.folder()
        backup = await folder.backup(previous)
        record = SaveRecord(digest(previous), digest(candidate) or "", backup)
        provider.marker = record.encode()
        provider.data = {"unchanged": previous, "candidate": candidate, "unexpected": truncated}[
            state
        ]
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        recovery = journal.storage.recovery
        assert recovery and recovery.classification == state and recovery.backup_verified
        assert journal.recovery_valid and journal.recovery_backup_valid
        assert not journal.ready
        if state == "unexpected":
            assert journal.recovery_transactions == 1  # Syntactically valid loss of transactions.
        before = provider.data
        marker = provider.marker
        await journal.accept_recovery()
        assert provider.data == before and provider.writes == 0
        assert marker in provider.archives and previous in provider.backups.values()
        assert before in provider.backups.values()
        assert journal.ready
        await Journal(provider.folder()).load()

    asyncio.run(scenario())


@pytest.mark.parametrize("marker", [None, b"", b"{", b"{}", b'{"phase":"complete"}'])
def test_missing_or_corrupted_metadata_with_pending_backup_blocks(
    books: tuple[bytes, bytes, bytes], marker: bytes | None
) -> None:
    async def scenario() -> None:
        previous, _, truncated = books
        provider = SafProvider(truncated)
        await provider.folder().backup(previous)
        provider.marker = marker
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        assert journal.storage.recovery and journal.storage.recovery.classification == "metadata"
        assert journal.recovery_valid and not journal.recovery_backup_valid

    asyncio.run(scenario())


def test_existing_journal_without_metadata_requires_one_time_review(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        previous, _, _ = books
        provider = SafProvider(previous)
        provider.marker = None
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        assert journal.recovery_valid and not journal.recovery_backup_valid
        await journal.accept_recovery()
        assert journal.ready and provider.writes == 0
        assert previous in provider.backups.values()

    asyncio.run(scenario())


@pytest.mark.parametrize("backup_state", ["missing", "corrupt", "invalid-journal", "renamed"])
def test_recovery_verifies_backup_hash_and_syntax_and_finds_finalized_copy(
    books: tuple[bytes, bytes, bytes], backup_state: str
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        backup = await provider.folder().backup(previous)
        name = backup.removeprefix("backups/")
        if backup_state == "invalid-journal":
            previous = b"invalid journal syntax\n"
        provider.marker = SaveRecord(digest(previous), digest(candidate) or "", backup).encode()
        if backup_state == "missing":
            del provider.backups[name]
        elif backup_state == "corrupt":
            provider.backups[name] = b"corrupt"
        elif backup_state == "invalid-journal":
            provider.backups[name] = previous
        else:
            provider.backups[name.replace("-pending.", "-committed.")] = provider.backups.pop(name)
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        assert journal.recovery_backup_valid is (backup_state == "renamed")
        assert provider.writes == 0

    asyncio.run(scenario())


def test_external_change_during_recovery_is_not_overwritten(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        backup = await provider.folder().backup(previous)
        marker = SaveRecord(digest(previous), digest(candidate) or "", backup).encode()
        provider.marker = marker
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        provider.race = True
        with pytest.raises(StorageError, match="changed during recovery"):
            await journal.accept_recovery()
        assert provider.data == b"; external edit during recovery\n"
        assert provider.marker == marker and provider.writes == 0
        assert not journal.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("current", [None, b"not a journal\n", b"\xff"])
def test_invalid_or_missing_journal_requires_external_restore(
    books: tuple[bytes, bytes, bytes], current: bytes | None
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        backup = await provider.folder().backup(previous)
        provider.marker = SaveRecord(digest(previous), digest(candidate) or "", backup).encode()
        provider.data = current
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        assert not journal.recovery_valid
        with pytest.raises(RecoveryRequired, match="recovery_restore_external"):
            await journal.accept_recovery()
        assert provider.data == current and provider.writes == 0
        # Explicit external restoration still requires a review, never automatic acceptance.
        provider.data = previous
        with pytest.raises(RecoveryRequired):
            await journal.load()
        await journal.accept_recovery()
        assert journal.ready and provider.data == previous

    asyncio.run(scenario())


def test_save_record_checksum_detects_valid_json_corruption() -> None:
    record = SaveRecord(None, digest(b"candidate") or "", None)
    value = json.loads(record.encode())
    value["phase"] = "complete"
    with pytest.raises(ValueError, match="Corrupted"):
        SaveRecord.decode(json.dumps(value).encode(), "txncount")
    assert (
        SaveRecord.decode(replace(record, phase="complete").encode(), "txncount").phase
        == "complete"
    )


def test_missing_or_malformed_snapshot_is_not_a_clean_journal() -> None:
    async def scenario() -> None:
        for value in (None, {}, {"pending": []}, {"current": None, "pending": "invalid"}):
            bridge = MagicMock()
            bridge.call = AsyncMock(return_value={"ok": True, "value": value})
            with pytest.raises(StorageError, match="Invalid SAF recovery"):
                await AndroidFolder(bridge, "content://synthetic/tree/test").inspect_recovery()

    asyncio.run(scenario())


def test_android_external_conflict_is_refused_without_native_write(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        session = await prepare(provider, previous, candidate)
        provider.data = b"external"
        with pytest.raises(ConflictError):
            await session.write()
        assert provider.writes == 0 and session.draft == candidate.decode()

    asyncio.run(scenario())


def test_finalization_failure_is_a_saved_warning_not_a_retry(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        session = await prepare(provider, previous, candidate)
        provider.fault = "finalize"
        backup = await session.write()
        assert backup and "-pending." in backup
        assert session.loaded and session.cleanup_error
        assert provider.data == candidate and provider.writes == 1
        journal = Journal(provider.folder())
        await journal.load()
        assert journal.ready

    asyncio.run(scenario())


def test_unaccounted_pending_backup_overrides_old_completed_marker(
    books: tuple[bytes, bytes, bytes],
) -> None:
    async def scenario() -> None:
        previous, candidate, truncated = books
        provider = SafProvider(previous)
        session = await prepare(provider, previous, candidate)
        await session.write()
        completed = provider.marker
        # Model a lost/missing newer intent but a surviving pre-write backup.
        await provider.folder().backup(candidate)
        provider.data = truncated
        assert provider.marker == completed
        journal = Journal(provider.folder())
        with pytest.raises(RecoveryRequired):
            await journal.load()
        assert journal.recovery_valid and not journal.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["timeout", "cancellation"])
def test_recovery_waits_for_still_running_native_io(
    books: tuple[bytes, bytes, bytes], interruption: str
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        started, release = asyncio.Event(), asyncio.Event()
        queue = asyncio.Lock()
        native_tasks: list[asyncio.Task[dict[str, object]]] = []

        class QueuedProvider(SafProvider):
            async def call(self, method: str, **args: object) -> dict[str, object]:
                async def native() -> dict[str, object]:
                    async with queue:
                        return await super(QueuedProvider, self).call(method, **args)

                task = asyncio.create_task(native())
                native_tasks.append(task)
                return await asyncio.shield(task)

            async def execute(self, method: str, args: dict[str, object]) -> object:
                if method == "write":
                    assert encoded(self.marker) == args["marker"]
                    assert encoded(self.data) == args["previous"]
                    self.marker = decoded(args["record"])
                    started.set()
                    await release.wait()
                    self.writes += 1
                    self.data = decoded(args["data"])
                    return None
                return await super().execute(method, args)

        provider = QueuedProvider(previous)
        session = await prepare(provider, previous, candidate)
        operation = asyncio.create_task(session.write())
        await asyncio.wait_for(started.wait(), 2)
        if interruption == "timeout":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(operation, 0.01)
        else:
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        assert not session.loaded and provider.data == previous
        journal = Journal(provider.folder())
        reload = asyncio.create_task(journal.load())
        await asyncio.sleep(0)
        assert not reload.done()
        release.set()
        with pytest.raises(RecoveryRequired):
            await asyncio.wait_for(reload, 2)
        assert provider.data == candidate
        assert journal.storage.recovery and journal.storage.recovery.classification == "candidate"
        assert provider.writes == 1 and not journal.ready
        await asyncio.gather(*native_tasks)

    asyncio.run(scenario())


def test_recovery_dialog_preserves_data_until_explicit_confirmation(
    books: tuple[bytes, bytes, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        previous, candidate, _ = books
        provider = SafProvider(previous)
        backup = await provider.folder().backup(previous)
        marker = SaveRecord(digest(previous), digest(candidate) or "", backup).encode()
        provider.marker = marker
        journal = Journal(provider.folder())
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.journal = journal
        await view._review_recovery(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog) and dialog.modal
        assert provider.marker == marker and provider.data == previous
        confirm = dialog.actions[-1]
        assert isinstance(confirm, ft.TextButton) and not confirm.disabled
        await click_async(confirm)
        assert journal.ready and provider.data == previous and provider.writes == 0
        assert marker in provider.archives

    asyncio.run(scenario())
