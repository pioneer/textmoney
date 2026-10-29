import asyncio
import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from beancount import loader

from app.domain.ledger import LedgerError
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import StorageError, StorageSession, WriteError


def source(root: str) -> str:
    amount, movement = ("10", "-10") if root == "Expenses" else ("-10", "10")
    return f'''; {root}:Food stays in comments
option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open {root}:Food
    name: "Original display name"
    other: "{root}:Food"
2000-01-01 open {root}:Food:Shops
    name: "Shops"
2000-01-01 open {root}:Food:Hidden
    name: "Hidden"
    textmoney-active: FALSE
2000-01-01 open {root}:Food-Other
2000-01-01 open {root}:Home
2000-01-02 * "Synthetic" "{root}:Food" #synthetic ^shared
    time: "12:01"
    category-ref: {root}:Food:Shops
    keep: "{root}:Food"
    {root}:Food:Shops  {amount} USD ; keep posting
    Assets:Cash  {movement} USD
2000-01-03 * "Synthetic"
    {root}:Food  {amount} USD
    Assets:Cash  {movement} USD
2000-01-04 custom "budget" {root}:Food "monthly" 50 USD ; budget note
2000-01-04 custom "budget" "{root}:Food:Shops" "monthly" 20 USD
2000-01-04 note {root}:Food "{root}:Food"
2000-01-04 document {root}:Food:Hidden "synthetic.pdf"
2000-01-04 custom "synthetic" {root}:Food "{root}:Food"
'''


async def setup(path: Path, text: str) -> Journal:
    (path / FILENAME).write_text(text, encoding="utf-8", newline="")
    journal = Journal(DesktopFolder(str(path), FILENAME))
    await journal.load()
    return journal


@pytest.mark.parametrize("root", ["Expenses", "Income"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_subtree_migration_is_lossless_and_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: str, newline: str
) -> None:
    async def scenario() -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "synthetic.pdf").write_bytes(b"synthetic")
        original = source(root).replace("\n", newline)
        journal = await setup(tmp_path, original)
        before = journal.ledger.period_totals(date(2000, 1, 1), date(2000, 1, 31))
        old, new = f"{root}:Food", f"{root}:Home:Meals"
        plan = await journal.plan_category_migration(old, new, original)
        assert (plan.categories, plan.transactions, plan.budgets) == (3, 2, 2)
        assert plan.references == 11
        assert journal.source == original
        assert not (tmp_path / "backups").exists()
        assert not loader.load_string(plan.candidate)[1]
        replace_file = os.replace
        replacements = []

        def verify_replace(staged: Path, target: Path) -> None:
            assert target.read_bytes() == original.encode()
            assert staged.read_bytes() == plan.candidate.encode()
            assert staged.parent == target.parent == tmp_path
            replacements.append(target)
            replace_file(staged, target)

        monkeypatch.setattr("app.infrastructure.desktop_storage.os.replace", verify_replace)
        journal.default_fee_category = old + ":Shops"
        backup = await journal.migrate_category(plan)
        assert backup and (tmp_path / backup).read_bytes() == original.encode()
        assert replacements == [tmp_path / FILENAME]
        assert (tmp_path / FILENAME).read_bytes() == plan.candidate.encode()
        assert journal.ledger.period_totals(date(2000, 1, 1), date(2000, 1, 31)) == before
        assert journal.default_fee_category == new + ":Shops"
        assert {b.account for b in journal.ledger.budgets} == {new, new + ":Shops"}
        expected = original
        for token_before, token_after in [
            (f"open {old}", f"open {new}"),
            (f"    {old} ", f"    {new} "),
            (f"    {old}:", f"    {new}:"),
            (f"category-ref: {old}:", f"category-ref: {new}:"),
            (f'custom "budget" {old}', f'custom "budget" {new}'),
            (f'custom "budget" "{old}', f'custom "budget" "{new}'),
            (f"note {old} ", f"note {new} "),
            (f"document {old}:", f"document {new}:"),
            (f'custom "synthetic" {old} ', f'custom "synthetic" {new} '),
        ]:
            expected = expected.replace(token_before, token_after)
        # A similarly named sibling is never part of the migrated subtree.
        expected = expected.replace(f"open {new}-Other", f"open {old}-Other")
        assert journal.source == expected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "target",
    [
        "Expenses:Food",
        "Expenses:Food:Nested",
        "Income:Meals",
        "Expenses:Home",
        "Expenses:Food-Other",
        "Expenses:Missing:Meals",
        "Expenses:bad",
    ],
)
def test_invalid_or_colliding_targets_do_not_write(tmp_path: Path, target: str) -> None:
    async def scenario() -> None:
        original = source("Expenses")
        journal = await setup(tmp_path, original)
        with pytest.raises(LedgerError):
            await journal.plan_category_migration("Expenses:Food", target, original)
        assert (tmp_path / FILENAME).read_bytes() == original.encode()
        assert not (tmp_path / "backups").exists()

    asyncio.run(scenario())


def test_undeclared_destination_descendants_and_quoted_budget_collisions_are_rejected(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        original = source("Expenses") + (
            '2000-01-05 custom "budget" "Expenses:New:Other" "monthly" 10 USD\n'
        )
        journal = await setup(tmp_path, original)
        with pytest.raises(LedgerError, match="category_migration_collision"):
            await journal.plan_category_migration("Expenses:Food", "Expenses:New", original)

    asyncio.run(scenario())


def test_review_counts_transactions_with_only_typed_metadata_references(tmp_path: Path) -> None:
    async def scenario() -> None:
        original = (
            source("Expenses")
            + """2000-01-05 * "Synthetic"
    reference: Expenses:Food
    Expenses:Food-Other 1 USD
    Assets:Cash -1 USD
"""
        )
        journal = await setup(tmp_path, original)
        plan = await journal.plan_category_migration("Expenses:Food", "Expenses:New", original)
        assert plan.transactions == 3
        assert "reference: Expenses:New" in plan.candidate
        assert "Expenses:Food-Other 1 USD" in plan.candidate

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["stale", "tampered", "conflict", "backup", "staging"])
def test_migration_failures_preserve_original_and_do_not_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        original = source("Expenses")
        journal = await setup(tmp_path, original)
        plan = await journal.plan_category_migration("Expenses:Food", "Expenses:New", original)
        disk = original
        error = ConflictError
        if failure == "stale":
            journal.source += "\n; new baseline\n"
        elif failure == "tampered":
            plan = replace(plan, candidate=plan.candidate + "\n")
        elif failure == "conflict":
            disk += "\n; external\n"
            (tmp_path / FILENAME).write_text(disk, newline="")
        elif failure == "backup":
            error = StorageError
            monkeypatch.setattr(
                journal.storage.folder, "backup", AsyncMock(side_effect=StorageError("Synthetic"))
            )
        else:
            error = WriteError
            monkeypatch.setattr(
                "app.infrastructure.desktop_storage.os.fsync",
                MagicMock(side_effect=[None, OSError("Synthetic")]),
            )
        with pytest.raises(error):
            await journal.migrate_category(plan)
        assert (tmp_path / FILENAME).read_bytes() == disk.encode()
        assert not list(tmp_path.glob(".*.tmp"))

    asyncio.run(scenario())


def test_android_and_unknown_adapters_cannot_migrate_even_when_called_directly(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await setup(tmp_path, source("Expenses"))
        plan = await journal.plan_category_migration(
            "Expenses:Food", "Expenses:New", journal.source
        )
        bridge = MagicMock()
        android = AndroidFolder(bridge, "content://synthetic", FILENAME)
        journal.storage.folder = android
        assert not journal.category_migration_available
        with pytest.raises(StorageError, match="category_migration_atomic_required"):
            await journal.migrate_category(plan)
        session = StorageSession(android)
        with pytest.raises(StorageError, match="category_migration_atomic_required"):
            await session.write(require_atomic=True)
        bridge.call.assert_not_called()
        assert not (tmp_path / "backups").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["backup", "staging"])
def test_external_writer_during_migration_prevents_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async def scenario() -> None:
        original = source("Expenses")
        journal = await setup(tmp_path, original)
        plan = await journal.plan_category_migration("Expenses:Food", "Expenses:New", original)
        external = original + "; external synthetic edit\n"
        if phase == "backup":
            backup = journal.storage.folder.backup

            async def modified_backup(data: bytes) -> str:
                result = await backup(data)
                (tmp_path / FILENAME).write_text(external, newline="")
                return result

            monkeypatch.setattr(journal.storage.folder, "backup", modified_backup)
        else:
            fsync = os.fsync
            calls = 0

            def modified_fsync(fd: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    (tmp_path / FILENAME).write_text(external, newline="")
                fsync(fd)

            monkeypatch.setattr("app.infrastructure.desktop_storage.os.fsync", modified_fsync)
        with pytest.raises(ConflictError):
            await journal.migrate_category(plan)
        assert (tmp_path / FILENAME).read_bytes() == external.encode()
        backups = list((tmp_path / "backups").glob("*-pending.beancount"))
        assert len(backups) == 1 and backups[0].read_bytes() == original.encode()
        assert not list(tmp_path.glob(".*.tmp"))
        assert not journal.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["replace", "directory-sync"])
def test_atomic_migration_explicitly_reports_uncertain_and_committed_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async def scenario() -> None:
        original = source("Expenses")
        journal = await setup(tmp_path, original)
        plan = await journal.plan_category_migration("Expenses:Food", "Expenses:New", original)
        operation = "os.replace" if phase == "replace" else "_sync_directory"
        monkeypatch.setattr(
            f"app.infrastructure.desktop_storage.{operation}",
            MagicMock(side_effect=OSError("Synthetic")),
        )
        with pytest.raises(WriteError) as failure:
            await journal.migrate_category(plan)
        assert failure.value.committed is (None if phase == "replace" else True)
        assert (tmp_path / FILENAME).read_bytes() == (
            original if phase == "replace" else plan.candidate
        ).encode()
        backups = list((tmp_path / "backups").glob("*-pending.beancount"))
        assert len(backups) == 1 and backups[0].read_bytes() == original.encode()
        assert not journal.ready and journal.source == original

    asyncio.run(scenario())
