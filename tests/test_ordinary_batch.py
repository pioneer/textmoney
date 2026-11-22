import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.ledger import (
    EPOCH,
    LedgerError,
    OrdinaryBatchValidationError,
    OrdinaryTransactionDraft,
)
from app.domain.storage import ConflictError
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import WriteError

TEST_TIMESTAMP = "1970-01-01T12:34:56"


async def seeded(tmp_path: Path) -> Journal:
    journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: EPOCH)
    await journal.load()
    await journal.create_account("Cash", "Cash", "USD", "100.00", opened=EPOCH, opening_date=EPOCH)
    await journal.create_category("Food", "Food", opened=EPOCH)
    await journal.create_category("Salary", "Salary", root="Income", opened=EPOCH)
    return journal


def test_ordinary_batch_validates_all_rows_before_writing(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original = (tmp_path / FILENAME).read_bytes()
        backups = set((tmp_path / "backups").iterdir())
        rows = (
            OrdinaryTransactionDraft(
                "expense", "Assets:Cash", "Expenses:Food", "2", "bad-date", TEST_TIMESTAMP
            ),
            OrdinaryTransactionDraft(
                "expense", "Assets:Cash", "Expenses:Food", "1.001", "1970-01-01", TEST_TIMESTAMP
            ),
            OrdinaryTransactionDraft(
                "income",
                "Assets:Cash",
                "Expenses:Food",
                "3",
                "1970-01-01",
                TEST_TIMESTAMP,
            ),
            OrdinaryTransactionDraft(
                "expense",
                "Assets:Cash",
                "Expenses:Food",
                "4",
                "1970-01-01",
                TEST_TIMESTAMP,
                description="x" * 501,
            ),
            OrdinaryTransactionDraft(
                "expense", "Assets:Cash", "Expenses:Food", "5", "1970-01-01", "not-a-time"
            ),
        )

        with pytest.raises(OrdinaryBatchValidationError) as error:
            await journal.save_ordinary_batch(rows, journal.source)

        assert [(item.row, item.message) for item in error.value.errors] == [
            (1, "Date must be YYYY-MM-DD."),
            (2, "Amount must be a nonnegative decimal with at most 2 fractional digits."),
            (3, "incoming_category"),
            (4, "crud_description"),
            (5, "crud_datetime"),
        ]
        assert original == (tmp_path / FILENAME).read_bytes()
        assert backups == set((tmp_path / "backups").iterdir())
        assert [transaction.kind for transaction in journal.ledger.transactions] == ["opening"]

    asyncio.run(scenario())


def test_validate_ordinary_batch_reports_subset_indexes_without_writes(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        source, ledger, draft = journal.source, journal.ledger, journal.storage.draft
        original = (tmp_path / FILENAME).read_bytes()
        backups = set((tmp_path / "backups").iterdir())
        valid = OrdinaryTransactionDraft(
            "expense", "Assets:Cash", "Expenses:Food", "2", "1970-01-01", TEST_TIMESTAMP
        )
        rows = (
            valid,
            OrdinaryTransactionDraft(
                "expense", "Assets:Cash", "Expenses:Food", "0", "1970-01-01", TEST_TIMESTAMP
            ),
            OrdinaryTransactionDraft(
                "income", "Assets:Cash", "Expenses:Food", "1", "1970-01-01", TEST_TIMESTAMP
            ),
        )

        assert journal.validate_ordinary_batch((valid,)) is None
        with pytest.raises(OrdinaryBatchValidationError) as validation:
            journal.validate_ordinary_batch(rows)
        with pytest.raises(OrdinaryBatchValidationError) as saving:
            await journal.save_ordinary_batch(rows, source)
        assert validation.value.errors == saving.value.errors
        assert [error.row for error in validation.value.errors] == [2, 3]
        assert (journal.source, journal.ledger, journal.storage.draft) == (source, ledger, draft)
        assert journal.ready and journal.storage.loaded
        assert (tmp_path / FILENAME).read_bytes() == original
        assert set((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


def test_ordinary_batch_writes_separate_standard_transactions_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        folder = journal.storage.folder
        original_write = folder.write
        writes = 0

        async def counted_write(data: bytes, *, expected_hash: str | None) -> None:
            nonlocal writes
            writes += 1
            await original_write(data, expected_hash=expected_hash)

        folder.write = counted_write
        backup = await journal.save_ordinary_batch(
            (
                OrdinaryTransactionDraft(
                    "expense",
                    "Assets:Cash",
                    "Expenses:Food",
                    "12.40",
                    "1970-01-01",
                    TEST_TIMESTAMP,
                    "Shop",
                ),
                OrdinaryTransactionDraft(
                    "expense",
                    "Assets:Cash",
                    "Expenses:Food",
                    "8.75",
                    "1970-01-01",
                    TEST_TIMESTAMP,
                    "Shop",
                    "Second receipt",
                ),
                OrdinaryTransactionDraft(
                    "income",
                    "Assets:Cash",
                    "Income:Salary",
                    "21.15",
                    "1970-01-01",
                    TEST_TIMESTAMP,
                    "Employer",
                ),
            ),
            journal.source,
        )

        assert backup is not None
        assert writes == 1
        ordinary = [
            transaction
            for transaction in journal.ledger.transactions
            if transaction.kind in {"expense", "income"}
        ]
        assert len(ordinary) == 3
        assert [transaction.kind for transaction in ordinary] == [
            "expense",
            "expense",
            "income",
        ]
        assert journal.ledger.balances(today=EPOCH)["Assets:Cash"] == 100
        assert journal.ledger.period_totals(EPOCH, EPOCH) == {
            "income": {"USD": Decimal("21.15")},
            "expenses": {"USD": Decimal("21.15")},
        }
        assert [transaction.narration for transaction in ordinary] == [
            "",
            "Second receipt",
            "",
        ]
        assert [transaction.timestamp for transaction in ordinary] == [
            "1970-01-01T12:34:56",
            "1970-01-01T12:34:56",
            "1970-01-01T12:34:56",
        ]
        assert journal.source.count('time: "12:34:56"') == 3
        assert "textmoney-kind:" not in journal.source
        assert "textmoney-expense-id:" not in journal.source
        assert "textmoney-fee-mode:" not in journal.source

    asyncio.run(scenario())


def test_ordinary_batch_stale_baseline_and_external_conflict_do_not_write(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        row = OrdinaryTransactionDraft(
            "expense", "Assets:Cash", "Expenses:Food", "2", "1970-01-01", TEST_TIMESTAMP
        )
        original = (tmp_path / FILENAME).read_bytes()

        with pytest.raises(ConflictError):
            await journal.save_ordinary_batch((row,), journal.source + "\n")
        assert (tmp_path / FILENAME).read_bytes() == original

        external = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: EPOCH)
        await external.load()
        await external.add_expense("Assets:Cash", "Expenses:Food", "1", "", "1970-01-01")
        external_source = (tmp_path / FILENAME).read_bytes()
        with pytest.raises(ConflictError):
            await journal.save_ordinary_batch((row,), journal.source)
        assert (tmp_path / FILENAME).read_bytes() == external_source

    asyncio.run(scenario())


def test_ordinary_batch_uncertain_write_requires_reload_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        original_write = journal.storage.folder.write
        rows = (
            OrdinaryTransactionDraft(
                "expense", "Assets:Cash", "Expenses:Food", "2", "1970-01-01", TEST_TIMESTAMP
            ),
            OrdinaryTransactionDraft(
                "expense", "Assets:Cash", "Expenses:Food", "3", "1970-01-01", TEST_TIMESTAMP
            ),
        )
        source = journal.source

        async def commit_then_fail(data: bytes, *, expected_hash: str | None) -> None:
            await original_write(data, expected_hash=expected_hash)
            raise WriteError("Synthetic uncertain outcome", committed=True)

        monkeypatch.setattr(journal.storage.folder, "write", commit_then_fail)
        with pytest.raises(WriteError) as error:
            await journal.save_ordinary_batch(rows, source)

        assert error.value.committed is True
        assert not journal.ready
        assert journal.source == source
        with pytest.raises(LedgerError, match="Reload"):
            await journal.save_ordinary_batch(rows, source)
        await journal.load()
        assert [transaction.kind for transaction in journal.ledger.transactions] == [
            "opening",
            "expense",
            "expense",
        ]
        with pytest.raises(ConflictError):
            await journal.save_ordinary_batch(rows, source)
        assert len(journal.ledger.transactions) == 3

    asyncio.run(scenario())
