import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from beancount import loader
from fava.core import FavaLedger
from journal_fixtures import review_external_fixture

from app.domain.budgets import rows
from app.domain.ledger import BANK_FEES, EPOCH, LedgerError, cashflow_amounts
from app.domain.storage import ConflictError
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.storage import StorageError

DAY = "2026-01-10"
STAMP = "2026-01-10T12:34:56"


async def seeded(path: Path) -> Journal:
    journal = Journal(DesktopFolder(str(path), FILENAME), today=lambda: date(2026, 3, 1))
    await journal.load()
    await journal.create_account("Cash", "Synthetic cash", "USD", opened=EPOCH)
    await journal.create_account("Euro", "Synthetic euro", "EUR", opened=EPOCH)
    await journal.create_category("Food", "Synthetic food", opened=EPOCH)
    await journal.create_category("Salary", "Synthetic income", root="Income", opened=EPOCH)
    await journal.create_category("Other-Fees", "Synthetic other fees", opened=EPOCH)
    return journal


async def save(
    journal: Journal,
    kind: str,
    *,
    fee: str = "5",
    mode: str = "added",
    category: str = BANK_FEES,
) -> str | None:
    if kind == "expense":
        return await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food",
            "100",
            "",
            DAY,
            "Synthetic purchase",
            STAMP,
            fee=fee,
            fee_mode=mode,
            fee_category=category,
        )
    return await journal.add_incoming(
        "Assets:Cash",
        "Income:Salary",
        "100",
        "",
        DAY,
        "income",
        "Synthetic income",
        STAMP,
        fee=fee,
        fee_mode=mode,
        fee_category=category,
        expected_source=journal.source,
    )


@pytest.mark.parametrize(
    "kind,mode,principal,movement",
    [
        ("expense", "added", "100", "-105"),
        ("expense", "deducted", "95", "-100"),
        ("income", "added", "-105", "100"),
        ("income", "deducted", "-100", "95"),
    ],
)
def test_commission_accounting_roundtrip_beancount_fava_and_reports(
    tmp_path: Path, kind: str, mode: str, principal: str, movement: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        backup = await save(journal, kind, mode=mode)
        transaction = journal.ledger.transactions[-1]
        assert transaction.kind == kind
        details = journal.ledger.cashflow_details(transaction)
        assert details.category.amount == Decimal(principal)
        assert details.account.amount == Decimal(movement)
        assert details.fee == 5 and details.fee_category == BANK_FEES
        assert len(transaction.postings) == 3
        assert journal.transaction_for_edit(transaction.id) == transaction
        assert backup and (tmp_path / backup).read_text() == before
        assert not loader.load_string(journal.source)[1]
        assert not list(FavaLedger(str(tmp_path / FILENAME)).errors)
        totals = journal.ledger.period_totals(date(2026, 1, 1), date(2026, 1, 31))
        assert totals == (
            {"income": {}, "expenses": {"USD": -Decimal(movement)}}
            if kind == "expense"
            else {"income": {"USD": -Decimal(principal)}, "expenses": {"USD": Decimal("5")}}
        )
        assert "textmoney-" not in journal.source
        report = {
            row.account: row.actual
            for row in rows(journal.ledger, date(2026, 1, 1), date(2026, 2, 1), tree=True)
        }
        assert report[BANK_FEES] == {"USD": Decimal("5")}
        assert report["Expenses"] == totals["expenses"]
        assert report["Expenses:Food" if kind == "expense" else "Income:Salary"] == {
            "USD": Decimal(principal)
        }
        await journal.load()
        assert journal.transaction_for_edit(journal.ledger.transactions[-1].id) == transaction
        assert journal.ledger.cashflow_details(transaction) == details

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize("annotated", [False, True])
def test_commission_edit_remove_restore_and_delete_preserve_user_annotations(
    tmp_path: Path, kind: str, annotated: bool
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await save(journal, kind)
        text = journal.source.replace(f"{DAY} *", f"{DAY} !").replace(
            '  time: "12:34:56"', '  time: "12:34:56"\n  receipt: "synthetic" ; keep'
        )
        if annotated:
            text = text.replace(
                f"    {BANK_FEES} 5 USD",
                f'    {BANK_FEES} 5 USD ; fee note\n      receipt: "fee"',
            )
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        original = journal.ledger.transactions[-1]
        category = "Expenses:Food" if kind == "expense" else "Income:Salary"
        for fee, fee_category in [
            ("0", BANK_FEES),
            ("7", "Expenses:Other-Fees"),
        ]:
            before = journal.source
            backup = await journal.edit_transaction(
                original.id,
                "Assets:Euro",
                category,
                "90",
                "",
                DAY,
                STAMP,
                "Edited",
                journal.source,
                fee=fee,
                fee_category=fee_category,
            )
            assert backup and (tmp_path / backup).read_text() == before
            original = journal.ledger.transactions[-1]
            details = journal.ledger.cashflow_details(original)
            assert details.fee == Decimal(fee) and details.fee_currency == "EUR"
            assert original.kind == kind
            assert 'receipt: "synthetic" ; keep' in journal.source
            assert f"{DAY} !" in journal.source
            if annotated:
                assert 'receipt: "fee"' in journal.source and "; fee note" in journal.source
            assert len(original.postings) == (3 if annotated or fee != "0" else 2)
            assert not loader.load_string(journal.source)[1]
        before = journal.source
        backup = await journal.delete_transaction(original.id, before)
        assert backup and (tmp_path / backup).read_text() == before
        assert not journal.ledger.transactions
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize("failure", ["conflict", "backup", "write"])
def test_commission_failure_never_saves_fee_or_declaration_separately(
    tmp_path: Path, kind: str, failure: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        disk = before
        error = StorageError
        if failure == "conflict":
            disk += "\n; external\n"
            (tmp_path / FILENAME).write_text(disk)
            error = ConflictError
        else:
            setattr(
                journal.storage.folder,
                failure,
                AsyncMock(side_effect=StorageError("Synthetic failure")),
            )
        with pytest.raises(error):
            await save(journal, kind)
        assert journal.source == before
        assert (tmp_path / FILENAME).read_text() == disk
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize(
    "fee,mode", [("-1", "added"), ("100", "deducted"), ("101", "deducted"), ("5", "invalid")]
)
def test_invalid_commission_never_changes_journal(
    tmp_path: Path, kind: str, fee: str, mode: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        before = journal.source
        with pytest.raises(LedgerError):
            await save(journal, kind, fee=fee, mode=mode)
        assert journal.source == before and (tmp_path / FILENAME).read_text() == before

    asyncio.run(scenario())


def test_custom_commissions_follow_posting_order_and_larger_splits_stay_readonly(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        for kind in ("expense", "income"):
            await save(journal, kind, category="Expenses:Other-Fees")
            transaction = journal.ledger.transactions[-1]
            assert journal.transaction_for_edit(transaction.id) == transaction
            assert (
                journal.ledger.cashflow_details(transaction).fee_category == "Expenses:Other-Fees"
            )
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}
        text = journal.source + (
            f'\n{DAY} * "Native two-category split"\n'
            "    Expenses:Other-Fees 20 USD\n"
            "    Assets:Cash -25 USD\n"
            "    Expenses:Food 5 USD\n"
        )
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        transaction = journal.ledger.transactions[-1]
        assert transaction.kind == "expense"
        details = journal.ledger.cashflow_details(transaction)
        assert details.category.account == "Expenses:Other-Fees" and details.amount == 20
        assert details.fee_category == "Expenses:Food" and details.fee == 5
        await journal.create_category("Bank-Fees", "Synthetic fees", opened=EPOCH)
        text = journal.source + (
            f'\n{DAY} * "Larger split"\n'
            "    Expenses:Food 90 USD\n"
            "    Assets:Cash -105 USD\n"
            "    Expenses:Other-Fees 10 USD\n"
            f"    {BANK_FEES} 5 USD\n"
        )
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        split = journal.ledger.transactions[-1]
        assert split.kind == "external"
        with pytest.raises(LedgerError, match="crud_transaction_readonly"):
            journal.transaction_for_edit(split.id)
        assert not loader.load_string(text)[1]

    asyncio.run(scenario())


def test_fee_category_inactive_and_unrelated_special_receipts_are_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.create_category("Bank-Fees", "Bank fees", opened=EPOCH)
        await journal.edit_category(BANK_FEES, "Bank fees", False, journal.source)
        for kind in ("expense", "income"):
            with pytest.raises(LedgerError, match="incoming_category"):
                await save(journal, kind)
        with pytest.raises(LedgerError, match="commission_amount"):
            journal.ledger.incoming(
                "Assets:Cash",
                "Expenses:Food",
                Decimal("10"),
                "",
                date.fromisoformat(DAY),
                datetime.fromisoformat(STAMP),
                "synthetic",
                "reimbursement",
                fee=Decimal("1"),
            )
        for kind in ("expense", "income"):
            with pytest.raises(LedgerError, match="incoming_category"):
                await save(journal, kind, category="Income:Salary")

    asyncio.run(scenario())


def test_commission_amount_calculation_has_no_implicit_currency_conversion() -> None:
    assert cashflow_amounts(Decimal("100"), Decimal("5"), "added", "income") == (105, 100)
    with pytest.raises(LedgerError, match="commission_amount"):
        cashflow_amounts(Decimal("100"), Decimal("5"), "added", "adjustment")


@pytest.mark.parametrize("kind", ["expense", "income"])
def test_legacy_edit_call_preserves_existing_commission(tmp_path: Path, kind: str) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await save(journal, kind)
        original = journal.ledger.transactions[-1]
        edit = journal.edit_expense if kind == "expense" else journal.edit_transaction
        await edit(
            original.id,
            "Assets:Cash",
            "Expenses:Food" if kind == "expense" else "Income:Salary",
            "200",
            "",
            DAY,
            STAMP,
            "Legacy caller",
            journal.source,
        )
        details = journal.ledger.cashflow_details(journal.ledger.transactions[-1])
        assert details.fee == 5 and details.amount == 200
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


def test_same_category_and_equal_postings_keep_distinct_roles_and_annotations(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.add_expense(
            "Assets:Cash",
            "Expenses:Food",
            "5",
            "",
            DAY,
            "Same category",
            STAMP,
            fee="5",
            fee_category="Expenses:Food",
        )
        text = journal.source.replace(
            "    Expenses:Food 5 USD",
            '    Expenses:Food 5 USD ; purchase\n      receipt: "main"',
            1,
        )
        offset = text.rfind("    Expenses:Food 5 USD")
        assert offset >= 0
        text = text[:offset] + text[offset:].replace(
            "    Expenses:Food 5 USD",
            '    Expenses:Food 5 USD ; commission\n      receipt: "fee"',
            1,
        )
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        original = journal.ledger.transactions[-1]
        for fee in ("7", "0"):
            await journal.edit_transaction(
                original.id,
                "Assets:Euro",
                "Expenses:Food",
                "6",
                "",
                DAY,
                STAMP,
                "Same roles",
                journal.source,
                fee=fee,
                fee_category="Expenses:Food",
            )
            original = journal.ledger.transactions[-1]
            details = journal.ledger.cashflow_details(original)
            assert details.amount == 6 and details.fee == Decimal(fee)
            assert details.category.account == details.fee_category == "Expenses:Food"
            assert len(original.postings) == 3 and details.fee_currency == "EUR"
            assert '; purchase\n      receipt: "main"' in journal.source
            assert '; commission\n      receipt: "fee"' in journal.source
            assert not loader.load_string(journal.source)[1]
        assert "textmoney-" not in journal.source

    asyncio.run(scenario())


def test_linked_reimbursement_matches_purchase_not_commission(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await save(journal, "expense")
        original = journal.ledger.transactions[-1]
        await journal.add_incoming(
            "Assets:Cash",
            "Expenses:Food",
            "10",
            "",
            DAY,
            "reimbursement",
            "Refund",
            STAMP,
            related_expense=original.id,
            expected_source=journal.source,
        )
        expense, refund = journal.ledger.transactions
        assert refund.related_expense == expense.id and refund.links == expense.links
        await journal.load()
        expense, refund = journal.ledger.transactions
        assert refund.related_expense == expense.id
        text = journal.source.replace("Expenses:Food -10 USD", f"{BANK_FEES} -10 USD")
        assert text != journal.source
        (tmp_path / FILENAME).write_text(text)
        await review_external_fixture(journal)
        assert journal.ledger.transactions[-1].related_expense is None

    asyncio.run(scenario())
