"""Synthetic local forks and fault injection, never synchronized user data."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import flet as ft
import pytest
from beancount import loader
from form_controls import click, click_async
from test_android_recovery import SafProvider

from app.domain.ledger import LedgerError
from app.domain.storage import ConflictError
from app.infrastructure.beancount_journal import JournalCodec
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import RecoveryRequired, StorageError, WriteError
from app.synchronization import (
    DIRECTORY,
    Book,
    SyncConflict,
    SyncState,
    file_hash,
    json_bytes,
    record_head,
    record_revision,
    review_versions,
    unchanged_extension,
)
from app.ui.finance import FinanceView

DECLARATIONS = (
    'option "operating_currency" "USD"\n'
    "; common comment must survive\n"
    "2000-01-01 open Assets:Cash USD\n"
    "2000-01-01 open Assets:Euro EUR\n"
    "2000-01-01 open Expenses:Food\n"
    "2000-01-01 open Expenses:Fees\n"
    '2000-01-01 custom "opaque" "preserve exactly"\n\n'
)


def transaction(number: int, narration: str = "Original") -> str:
    return (
        f'2026-01-01 * "{narration}"\n'
        f'  id: "{UUID(int=number)}"\n'
        '  opaque: "retain metadata"\n'
        "  Assets:Cash -1 USD\n"
        "  Expenses:Food 1 USD\n"
    )


BASE = DECLARATIONS + transaction(1) + "\n" + transaction(2)
LOCAL = BASE + "\n" + transaction(3, "Local addition")
REMOTE = BASE + "\n" + transaction(4, "Remote addition")
COPY = "txncount (conflicted copy from another device).beancount"


async def fork(tmp_path: Path) -> tuple[Journal, DesktopFolder]:
    folder = DesktopFolder(str(tmp_path), FILENAME)
    (tmp_path / FILENAME).write_bytes(LOCAL.encode())
    await record_revision(folder, BASE.encode(), LOCAL.encode())
    await record_revision(folder, BASE.encode(), REMOTE.encode())
    (tmp_path / COPY).write_bytes(REMOTE.encode())
    journal = Journal(folder)
    with pytest.raises(SyncConflict):
        await journal.load()
    return journal, folder


def test_independent_additions_use_verified_base_and_official_engine(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal, _ = await fork(tmp_path)
        state = journal.sync_state
        assert state is not None
        assert state.common_base(LOCAL.encode(), REMOTE.encode()) == BASE
        review = await journal.sync_review(COPY, local=LOCAL.encode())
        assert review.automatic and len(review.differences) == 2
        candidate = review.candidate({})
        assert candidate.startswith(LOCAL)
        assert transaction(4, "Remote addition") in candidate
        assert len(Book.read(candidate).transactions) == 4
        await journal.resolve_sync(COPY, state, candidate, local_hash=file_hash(LOCAL.encode()))
        assert journal.ready and len(journal.ledger.transactions) == 4
        assert (tmp_path / COPY).read_bytes() == REMOTE.encode()
        assert any(path.read_bytes() == LOCAL.encode() for path in (tmp_path / "backups").iterdir())
        for value in (LOCAL, REMOTE, candidate):
            assert (
                tmp_path / DIRECTORY / f"{file_hash(value.encode())}.beancount"
            ).read_bytes() == (value.encode())
        _, errors, _ = loader.load_file(str(tmp_path / FILENAME))
        assert not errors
        await journal.load()
        assert journal.ready and journal.sync_state and not journal.sync_state.versions

    asyncio.run(scenario())


def test_identical_transactions_are_not_duplicated() -> None:
    review = review_versions(LOCAL, LOCAL, BASE)
    assert review.automatic and review.candidate({}) == LOCAL
    assert len(Book.read(review.candidate({})).transactions) == 3


def test_same_uuid_divergent_edits_require_explicit_choice() -> None:
    local = BASE.replace('"Original"', '"Local edit"', 1)
    remote = BASE.replace('"Original"', '"Remote edit"', 1)
    review = review_versions(local, remote, BASE)
    assert not review.automatic
    assert review.differences[0].kind == "modified"
    with pytest.raises(LedgerError, match="sync_choose"):
        review.candidate({})
    identifier = str(UUID(int=1))
    assert review.candidate({identifier: "local"}) == local
    assert review.candidate({identifier: "remote"}) == remote
    assert "Assets:Cash" in review.local.descriptions[identifier]


def test_deletion_versus_edit_and_independent_edits() -> None:
    local = BASE.replace(transaction(1), "")
    remote = BASE.replace('"Original"', '"Remote edit"', 1)
    review = review_versions(local, remote, BASE)
    assert not review.automatic and review.differences[0].kind == "deleted"
    assert transaction(1, "Remote edit") in review.candidate({str(UUID(int=1)): "remote"})
    different = BASE.replace(transaction(2), transaction(2, "Second edit"))
    independent = review_versions(remote, different, BASE)
    assert independent.automatic
    merged = independent.candidate({})
    assert "Remote edit" in merged and "Second edit" in merged


def test_without_common_ancestor_choices_are_required_even_for_additions() -> None:
    review = review_versions(LOCAL, REMOTE, None)
    assert not review.automatic and review.base is None
    with pytest.raises(LedgerError, match="sync_choose"):
        review.candidate({})
    candidate = review.candidate({str(UUID(int=3)): "local", str(UUID(int=4)): "remote"})
    assert len(Book.read(candidate).transactions) == 4
    files = {
        f"{DIRECTORY}/{file_hash(BASE.encode())}.beancount": BASE.encode(),
        COPY: REMOTE.encode(),
    }
    assert (
        SyncState.read(files, LOCAL.encode()).common_base(LOCAL.encode(), REMOTE.encode()) is None
    )


@pytest.mark.parametrize(
    "remote",
    [
        REMOTE.replace("Assets:Cash USD", "Assets:Cash EUR").replace("Cash -1 USD", "Euro -1 USD"),
        REMOTE.replace("open Expenses:Food", 'open Expenses:Food\n  display-name: "Changed"'),
        REMOTE.replace('"USD"\n', '"EUR"\n', 1),
        REMOTE + "\n2000-01-01 open Expenses:Travel\n",
        REMOTE + "\n; new standalone comment\n",
    ],
)
def test_nontransaction_changes_never_get_transaction_only_merge(remote: str) -> None:
    if "Cash EUR" in remote:
        with pytest.raises(LedgerError):
            review_versions(LOCAL, remote, BASE)
    else:
        review = review_versions(LOCAL, remote, BASE)
        assert not review.compatible and not review.automatic
        with pytest.raises(LedgerError, match="sync_manual"):
            review.candidate({})


@pytest.mark.parametrize(
    "source",
    [
        BASE.replace(f'  id: "{UUID(int=1)}"\n', "", 1),
        BASE.replace(str(UUID(int=1)), "not-a-uuid", 1),
        BASE.replace('"Original"', '"Original" ; inline comment', 1),
        BASE.replace('  opaque: "retain metadata"', "  ; attached comment", 1),
        BASE + '\ninclude "elsewhere.beancount"\n',
        "broken journal",
    ],
)
def test_unsupported_sources_fail_closed(source: str) -> None:
    with pytest.raises(LedgerError):
        review_versions(LOCAL, source, BASE)


def test_transfer_fee_exact_prices_metadata_and_crlf_survive() -> None:
    transfer = (
        '2026-01-02 * "Exchange with fee"\n'
        f'  id: "{UUID(int=5)}"\n'
        '  opaque: "untouched"\n'
        '  txncount-kind: "transfer"\n'
        "  Assets:Cash -100 USD @@ 90 EUR\n"
        "  Assets:Euro 90 EUR\n"
        "  Assets:Cash -1 USD\n"
        "  Expenses:Fees 1 USD\n"
    )
    local = (BASE + "\n" + transfer).replace("\n", "\r\n")
    remote = REMOTE.replace("\n", "\r\n")
    result = review_versions(local, remote, BASE.replace("\n", "\r\n")).candidate({})
    assert transfer.replace("\n", "\r\n") in result and result.startswith(local)
    assert len(Book.read(result).transactions) == 4
    _, errors, _ = loader.load_string(result)
    assert not errors


@pytest.mark.parametrize("name", [COPY, "totally-renamed.BEANCOUNT"])
def test_conflict_detection_preserves_content_not_timestamps(tmp_path: Path, name: str) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path), FILENAME)
        (tmp_path / FILENAME).write_bytes(LOCAL.encode())
        (tmp_path / name).write_bytes(REMOTE.encode())
        (tmp_path / "unrelated.beancount").write_bytes(b"private unrelated file")
        os.utime(tmp_path / name, (1, 1))
        journal = Journal(folder)
        with pytest.raises(SyncConflict):
            await journal.load()
        assert not journal.ready and journal.sync_state and name in journal.sync_state.versions
        before = await folder.sync_scan()
        with pytest.raises(SyncConflict):
            await journal.load()
        assert await folder.sync_scan() == before
        for source in (LOCAL, REMOTE):
            snapshot = tmp_path / DIRECTORY / f"{file_hash(source.encode())}.beancount"
            assert snapshot.read_bytes() == source.encode()
        assert (tmp_path / name).read_bytes() == REMOTE.encode()

    asyncio.run(scenario())


def test_offline_main_replacement_exposes_surviving_authored_branch(tmp_path: Path) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path), FILENAME)
        await record_revision(folder, BASE.encode(), LOCAL.encode())
        await record_head(folder, LOCAL.encode())
        (tmp_path / FILENAME).write_bytes(REMOTE.encode())
        journal = Journal(folder)
        with pytest.raises(SyncConflict):
            await journal.load()
        assert journal.sync_state and LOCAL.encode() in journal.sync_state.versions.values()
        assert not journal.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("which", ["journal", "copy", "metadata", "new_copy"])
def test_review_sources_are_rechecked_before_commit(tmp_path: Path, which: str) -> None:
    async def scenario() -> None:
        journal, _ = await fork(tmp_path)
        state = journal.sync_state
        assert state is not None
        candidate = review_versions(LOCAL, REMOTE, BASE).candidate({})
        if which == "metadata":
            (tmp_path / DIRECTORY / f"{file_hash(BASE.encode())}.beancount").write_bytes(b"damaged")
        else:
            name = (
                FILENAME
                if which == "journal"
                else COPY
                if which == "copy"
                else "conflict.beancount"
            )
            (tmp_path / name).write_bytes((REMOTE + "\n; changed externally\n").encode())
        before = (tmp_path / FILENAME).read_bytes()
        with pytest.raises((StorageError, ConflictError)):
            await journal.resolve_sync(COPY, state, candidate, local_hash=file_hash(LOCAL.encode()))
        assert (tmp_path / FILENAME).read_bytes() == before and not journal.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("which", ["copy", "metadata"])
def test_final_guard_rechecks_sources_after_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    async def scenario() -> None:
        journal, folder = await fork(tmp_path)
        state = journal.sync_state
        assert state is not None
        backup = folder.backup

        async def race(data: bytes) -> str:
            result = await backup(data)
            path = (
                tmp_path / COPY
                if which == "copy"
                else tmp_path / DIRECTORY / f"{file_hash(BASE.encode())}.beancount"
            )
            path.write_bytes(b"; race\n")
            return result

        monkeypatch.setattr(folder, "backup", race)
        with pytest.raises(ConflictError):
            await journal.resolve_sync(
                COPY,
                state,
                review_versions(LOCAL, REMOTE, BASE).candidate({}),
                local_hash=file_hash(LOCAL.encode()),
            )
        assert (tmp_path / FILENAME).read_bytes() == LOCAL.encode()
        assert not journal.ready and list((tmp_path / "backups").iterdir())

    asyncio.run(scenario())


def test_acknowledgment_failure_never_replays_committed_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal, folder = await fork(tmp_path)
        state = journal.sync_state
        assert state is not None
        put = folder.sync_put

        async def fail(name: str, data: bytes) -> None:
            if name.startswith("ack-"):
                raise StorageError("synthetic acknowledgement failure")
            await put(name, data)

        monkeypatch.setattr(folder, "sync_put", fail)
        candidate = review_versions(LOCAL, REMOTE, BASE).candidate({})
        with pytest.raises(WriteError) as error:
            await journal.resolve_sync(COPY, state, candidate, local_hash=file_hash(LOCAL.encode()))
        assert error.value.committed is True and not journal.ready
        assert (tmp_path / FILENAME).read_bytes() == candidate.encode()
        assert len(JournalCodec().parse(candidate).transactions) == 4
        with pytest.raises(SyncConflict):
            await journal.load()
        assert (tmp_path / COPY).read_bytes() == REMOTE.encode()

    asyncio.run(scenario())


def test_old_acknowledgment_does_not_hide_conflict_after_unrelated_rollback() -> None:
    acknowledgment = json_bytes(
        {"name": COPY, "hash": file_hash(REMOTE.encode()), "result": file_hash(LOCAL.encode())}
    )
    files = {
        COPY: REMOTE.encode(),
        f"{DIRECTORY}/{file_hash(LOCAL.encode())}.beancount": LOCAL.encode(),
        f"{DIRECTORY}/ack-{file_hash(acknowledgment)}.json": acknowledgment,
    }
    assert COPY not in SyncState.read(files, LOCAL.encode()).versions
    assert COPY in SyncState.read(files, BASE.encode()).versions


def test_append_must_not_hide_changed_metadata_on_the_last_directive() -> None:
    previous = BASE.rstrip() + "\n"
    assert unchanged_extension(previous.encode(), (previous + "\n" + transaction(3)).encode())
    assert not unchanged_extension(
        previous.encode(), (previous + '    extra: "attached to previous posting"\n').encode()
    )


def test_unchanged_poll_does_not_invalidate_an_open_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal, _ = await fork(tmp_path)
        state = journal.sync_state
        with pytest.raises(SyncConflict):
            await journal.poll()
        assert journal.sync_state is state

    asyncio.run(scenario())


def test_changed_acknowledged_copy_requires_another_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal, _ = await fork(tmp_path)
        state = journal.sync_state
        assert state is not None
        candidate = review_versions(LOCAL, REMOTE, BASE).candidate({})
        await journal.resolve_sync(COPY, state, candidate, local_hash=file_hash(LOCAL.encode()))
        (tmp_path / COPY).write_bytes((REMOTE + "\n" + transaction(5)).encode())
        with pytest.raises(SyncConflict):
            await journal.poll()
        assert journal.sync_state and COPY in journal.sync_state.versions
        assert not journal.ready

    asyncio.run(scenario())


def test_multiple_copies_resolve_separately_without_losing_additions(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal, folder = await fork(tmp_path)
        third = BASE + "\n" + transaction(5)
        await record_revision(folder, BASE.encode(), third.encode())
        other = "txncount another conflict.beancount"
        (tmp_path / other).write_bytes(third.encode())
        with pytest.raises(SyncConflict):
            await journal.load()
        state = journal.sync_state
        assert state is not None
        candidate = review_versions(LOCAL, REMOTE, BASE).candidate({})
        await journal.resolve_sync(COPY, state, candidate, local_hash=file_hash(LOCAL.encode()))
        assert not journal.ready and journal.sync_state and other in journal.sync_state.versions
        state = journal.sync_state
        review = await journal.sync_review(other, local=candidate.encode())
        assert review.automatic
        await journal.resolve_sync(
            other, state, review.candidate({}), local_hash=file_hash(candidate.encode())
        )
        assert journal.ready and len(journal.ledger.transactions) == 5
        assert (tmp_path / COPY).read_bytes() == REMOTE.encode()
        assert (tmp_path / other).read_bytes() == third.encode()

    asyncio.run(scenario())


def test_failed_evidence_fsync_never_changes_journal_or_leaves_partial_named_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / FILENAME).write_bytes(LOCAL.encode())
    folder = DesktopFolder(str(tmp_path), FILENAME)
    monkeypatch.setattr(
        "app.infrastructure.desktop_storage.os.fsync", MagicMock(side_effect=OSError("disk full"))
    )
    with pytest.raises(StorageError):
        asyncio.run(folder.sync_put(f"{file_hash(REMOTE.encode())}.beancount", REMOTE.encode()))
    assert (tmp_path / FILENAME).read_bytes() == LOCAL.encode()
    assert not list((tmp_path / DIRECTORY).iterdir())
    assert not list(tmp_path.glob(".txncount-evidence-*.tmp"))


def test_cycles_and_multiple_incomparable_ancestors_do_not_select_a_base() -> None:
    a, b = file_hash(LOCAL.encode()), file_hash(REMOTE.encode())
    files = {
        f"{DIRECTORY}/{a}.beancount": LOCAL.encode(),
        f"{DIRECTORY}/{b}.beancount": REMOTE.encode(),
    }
    for parent, child in ((a, b), (b, a)):
        files[f"{DIRECTORY}/edge-{parent}-{child}.json"] = json_bytes(
            {"parent": parent, "child": child}
        )
    assert (
        SyncState.read(files, LOCAL.encode()).common_base(LOCAL.encode(), REMOTE.encode()) is None
    )


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_same_transaction_review_has_no_default_choice_without_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        local = BASE.replace('"Original"', '"Local edit"', 1)
        remote = BASE.replace('"Original"', '"Remote edit"', 1)
        (tmp_path / FILENAME).write_bytes(local.encode())
        (tmp_path / COPY).write_bytes(remote.encode())
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.language = language
        view.journal = journal
        await view._review_sync(MagicMock())
        await click_async(page.show_dialog.call_args.args[0].actions[-1])
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog) and isinstance(dialog.content, ft.Column)
        choices = [c for c in dialog.content.controls if isinstance(c, ft.Dropdown)]
        assert len(choices) == 1 and choices[0].value is None
        assert any(
            isinstance(c, ft.Text) and c.value == view.tr("sync_no_base")
            for c in dialog.content.controls
        )
        confirm = dialog.actions[-1]
        assert isinstance(confirm, ft.TextButton)
        await click_async(confirm)
        assert view.error_message == "sync_choose"
        assert (tmp_path / FILENAME).read_bytes() == local.encode()
        choices[0].value = "remote"
        await click_async(confirm)
        assert journal.ready and journal.source == remote

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["snapshot", "edge", "missing", "ack"])
def test_damaged_sync_metadata_blocks_saving(damage: str) -> None:
    base, local = file_hash(BASE.encode()), file_hash(LOCAL.encode())
    files = {
        f"{DIRECTORY}/{base}.beancount": BASE.encode(),
        f"{DIRECTORY}/{local}.beancount": LOCAL.encode(),
        f"{DIRECTORY}/edge-{base}-{local}.json": json_bytes({"parent": base, "child": local}),
    }
    if damage == "snapshot":
        files[f"{DIRECTORY}/{base}.beancount"] = b""
    elif damage == "missing":
        files.pop(f"{DIRECTORY}/{base}.beancount")
    elif damage == "edge":
        files[f"{DIRECTORY}/edge-{base}-{local}.json"] = b"{}"
    else:
        files[f"{DIRECTORY}/ack-{'0' * 64}.json"] = json.dumps({}).encode()
    with pytest.raises(StorageError, match="sync_metadata"):
        SyncState.read(files, LOCAL.encode())


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks requires Windows privilege")
def test_symlink_conflict_is_not_followed(tmp_path: Path) -> None:
    (tmp_path / COPY).symlink_to(tmp_path / "outside")
    with pytest.raises(StorageError):
        asyncio.run(DesktopFolder(str(tmp_path), FILENAME).sync_scan())


@pytest.mark.parametrize(
    "fault", ["", "before", "partial", "prepared", "timeout", "cancel", "finish"]
)
def test_android_merge_reuses_recovery_and_never_retries(fault: str) -> None:
    async def scenario() -> None:
        provider = SafProvider(LOCAL.encode())
        provider.sync_files[COPY] = REMOTE.encode()
        folder = provider.folder()
        await record_revision(folder, BASE.encode(), LOCAL.encode())
        await record_revision(folder, BASE.encode(), REMOTE.encode())
        journal = Journal(folder)
        with pytest.raises(SyncConflict):
            await journal.load()
        state = journal.sync_state
        assert state is not None
        provider.fault = fault
        candidate = review_versions(LOCAL, REMOTE, BASE).candidate({})
        if not fault:
            await journal.resolve_sync(COPY, state, candidate, local_hash=file_hash(LOCAL.encode()))
            assert journal.ready and provider.data == candidate.encode()
        else:
            with pytest.raises((StorageError, TimeoutError, asyncio.CancelledError)):
                await journal.resolve_sync(
                    COPY, state, candidate, local_hash=file_hash(LOCAL.encode())
                )
            assert not journal.ready
            assert provider.data == (
                b""
                if fault == "partial"
                else candidate.encode()
                if fault in {"timeout", "cancel", "finish"}
                else LOCAL.encode()
            )
            provider.fault = ""
            reopened = Journal(provider.folder())
            with pytest.raises(RecoveryRequired):
                await reopened.load()
            if fault in {"timeout", "cancel", "finish"}:
                with pytest.raises(SyncConflict):
                    await reopened.accept_recovery()
        assert provider.sync_files[COPY] == REMOTE.encode()
        assert provider.writes <= 1
        for source in (LOCAL, REMOTE):
            assert (
                provider.sync_files[f"{DIRECTORY}/{file_hash(source.encode())}.beancount"]
                == source.encode()
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_mobile_review_requires_confirmation_and_keeps_postponed_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        journal, folder = await fork(tmp_path)
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.language = language
        view.journal = journal
        before = await folder.sync_scan()
        await view._review_sync(MagicMock())
        picker = page.show_dialog.call_args.args[0]
        assert isinstance(picker, ft.AlertDialog) and picker.modal
        open_button = picker.actions[-1]
        assert isinstance(open_button, ft.TextButton)
        await click_async(open_button)
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog) and dialog.scrollable
        assert isinstance(dialog.content, ft.Column)
        assert any(
            isinstance(control, ft.Text) and control.value == view.tr("sync_auto")
            for control in dialog.content.controls
        )
        cancel = dialog.actions[0]
        assert isinstance(cancel, ft.TextButton)
        click(cancel)
        assert (tmp_path / FILENAME).read_bytes() == LOCAL.encode()
        assert await folder.sync_scan() == before
        await view._review_sync(MagicMock())
        await click_async(page.show_dialog.call_args.args[0].actions[-1])
        await click_async(page.show_dialog.call_args.args[0].actions[-1])
        assert journal.ready and len(journal.ledger.transactions) == 4

    asyncio.run(scenario())
