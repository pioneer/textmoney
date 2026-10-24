import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from fava.core import FavaLedger

from app.domain.ledger import LedgerError
from app.infrastructure.beancount_journal import JournalCodec
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal

TODAY = date(2026, 3, 1)
FIXTURE = Path(__file__).parent / "fixtures" / "compatibility.beancount"


def unit_price_journal() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_unit_price_exchanges_preserve_units_and_balance_with_official_engines(
    tmp_path: Path,
) -> None:
    source = unit_price_journal()
    ledger = JournalCodec(today=lambda: TODAY).parse(source)

    assert ledger.balances(today=TODAY) == {
        "Assets:Bank:USD": Decimal("219.50"),
        "Assets:Bank:UAH": Decimal("1382.50"),
        "Assets:Cash": Decimal("55"),
        "Liabilities:Loan": Decimal("-90"),
    }
    assert ledger.period_totals(date(2026, 1, 1), date(2026, 1, 31)) == {
        "income": {"UAH": Decimal("500")},
        "expenses": {"USD": Decimal("1"), "UAH": Decimal("84.25")},
    }
    transactions = {transaction.narration: transaction for transaction in ledger.transactions}
    forward = ledger.transfer_details(transactions["USD to UAH using unit price"])
    reverse = ledger.transfer_details(transactions["UAH to USD using unit price"])
    assert forward.rate == Decimal("42.125")
    assert reverse.rate == Decimal("0.0425")
    positive = ledger.transfer_details(transactions["UAH to USD with positive unit price"])
    assert positive.received_principal == Decimal("100")
    assert positive.converted == Decimal("4212.50")
    assert (
        ledger.transfer_details(transactions["Source-currency transfer fee"]).fee_currency == "USD"
    )
    assert (
        ledger.transfer_details(transactions["Destination-currency transfer fee"]).fee_currency
        == "UAH"
    )

    entries, errors, _ = loader.load_string(source)
    assert not errors, [error.message for error in errors]
    path = tmp_path / FILENAME
    path.write_text(source, encoding="utf-8", newline="")
    fava = FavaLedger(str(path), poll_watcher=False)
    assert not fava.errors
    assert JournalCodec(today=lambda: TODAY).document(source).raw_directives


def test_unit_price_source_round_trips_exactly_and_is_read_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = unit_price_journal()
        path = tmp_path / FILENAME
        path.write_text(source, encoding="utf-8", newline="")
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        assert journal.source == source
        transaction = next(
            t for t in journal.ledger.transactions if t.narration == "USD to UAH using unit price"
        )
        with pytest.raises(LedgerError, match="crud_transaction_readonly"):
            journal.transaction_for_edit(transaction.id)
        with pytest.raises(LedgerError, match="crud_transaction_readonly"):
            await journal.delete_transaction(transaction.id, source)
        await journal.create_category("Unrelated", "Unrelated", opened=date(2026, 1, 1))
        saved = path.read_text(encoding="utf-8")
        assert saved.startswith(source)
        assert "Assets:Bank:USD -100 USD @ 42.125 UAH" in saved
        assert "Assets:Bank:USD -2 USD @@ 84.25 UAH" in saved

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "annotation",
    ["@ 0 UAH", "@ -42.125 UAH", "@ 42.125 USD"],
)
def test_unsupported_or_invalid_unit_price_exchanges_fail_closed(annotation: str) -> None:
    source = unit_price_journal().replace("-100 USD @ 42.125 UAH", f"-100 USD {annotation}", 1)
    with pytest.raises(LedgerError):
        JournalCodec(today=lambda: TODAY).parse(source)
