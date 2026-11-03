import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest
from beancount import loader
from beancount.ops import summarize
from fava.core import FavaLedger
from fava.util.date import INTERVALS as FAVA_INTERVALS
from form_controls import change, transaction_form
from test_account_balances import TODAY, open_form, seeded
from test_daily_summary import walk
from test_transfers import transfer

from app.domain.budgets import rows
from app.domain.ledger import ADJUSTMENTS, EPOCH, EQUITY, LedgerError
from app.domain.reporting import account_balances
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import format_amount
from app.ui.finance import FinanceView

MARCH = date(2026, 3, 1), date(2026, 4, 1)


@pytest.mark.parametrize("path", ["CreditCards:Bank", "Loans:Bank", "People:Alex"])
def test_native_liability_acceptance_scenario_matches_beancount_and_fava(
    tmp_path: Path, path: str
) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        await journal.create_account(
            "Bank:Debit",
            "Debit",
            "UAH",
            "10000",
            opened=EPOCH,
            opening_date=date(2026, 3, 3),
        )
        await journal.create_account(
            path,
            "Credit account",
            "UAH",
            "78317.92",
            root="Liabilities",
            opened=date(2026, 3, 3),
            opening_date=date(2026, 3, 3),
        )
        identifier = f"Liabilities:{path}"
        await journal.create_category("Food:Groceries", "Groceries", opened=EPOCH)
        await journal.create_category("Financial:Interest", "Interest", opened=EPOCH)
        await journal.save_budget(
            "Expenses:Food:Groceries", MARCH[0], "monthly", "1000", "UAH", journal.source
        )
        opening = next(
            transaction
            for transaction in journal.ledger.transactions
            if transaction.kind == "opening"
            and any(posting.account == identifier for posting in transaction.postings)
        )
        assert [(p.account, p.amount) for p in opening.postings] == [
            (identifier, Decimal("-78317.92")),
            (EQUITY, Decimal("78317.92")),
        ]
        assert journal.opening_balance(identifier) == Decimal("78317.92")
        assert journal.ledger.period_totals(*MARCH) == {"income": {}, "expenses": {}}

        await journal.add_expense(
            identifier, "Expenses:Food:Groceries", "500", "", "2026-03-04", "Groceries"
        )
        assert journal.ledger.balances()[identifier] == Decimal("-78817.92")
        before_repayment = rows(journal.ledger, *MARCH)
        await transfer(
            journal,
            source_account="Assets:Bank:Debit",
            destination=identifier,
            amount="2000",
            occurred="2026-03-05",
            timestamp="2026-03-05T12:00",
            description="Credit account repayment",
        )
        assert journal.ledger.transactions[-1].kind == "transfer"
        assert rows(journal.ledger, *MARCH) == before_repayment
        assert journal.ledger.period_totals(*MARCH)["expenses"] == {"UAH": Decimal("500")}
        await journal.add_expense(
            identifier, "Expenses:Financial:Interest", "300", "", "2026-03-06", "Interest"
        )
        balances = journal.ledger.balances()
        assert balances == {"Assets:Bank:Debit": Decimal("8000"), identifier: Decimal("-77117.92")}
        assert sum(balances.values()) == Decimal("-69117.92")
        assert not journal.ledger.balance_warnings()
        assert journal.ledger.period_totals(*MARCH) == {
            "income": {},
            "expenses": {"UAH": Decimal("800")},
        }
        budgets = {row.account: row for row in rows(journal.ledger, *MARCH)}
        assert budgets["Expenses:Food:Groceries"].actual == {"UAH": Decimal("500")}
        assert budgets["Expenses:Food:Groceries"].budget["UAH"].quantize(Decimal("0.01")) == 1000
        assert budgets["Expenses:Financial:Interest"].actual == {"UAH": Decimal("300")}
        view = FinanceView()
        view.language = "en"
        assert view._account_balance_text(identifier, balances[identifier], "UAH") == (
            "Outstanding debt: 77,117.92 UAH"
        )

        saved = journal.source
        assert (tmp_path / FILENAME).read_text() == saved
        entries, errors, _ = loader.load_string(saved)
        assert not errors
        official, _ = summarize.balance_by_account(entries)
        assert official[identifier].get_currency_units("UAH").number == Decimal("-77117.92")
        fava = FavaLedger(str(tmp_path / FILENAME), poll_watcher=False)
        assert not fava.errors
        expenses = fava.charts.interval_totals(
            fava.get_filtered(time="2026-03"), FAVA_INTERVALS["month"], "Expenses", "units"
        )
        assert [dict(item.balance) for item in expenses] == [{"UAH": Decimal("800")}]
        assert 'custom "budget"' in saved
        assert "textmoney-" not in saved and "timestamp:" not in saved and "id:" not in saved
        await journal.load()
        assert journal.source == saved and journal.ledger.balances() == balances
        await journal.edit_account(
            identifier,
            "Renamed credit account",
            "78317.92",
            True,
            journal.source,
            opening_date=date(2026, 3, 3),
        )
        assert journal.ledger.balances() == balances

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("Liabilities:Card", "Assets:Euro"),
        ("Assets:Bank", "Liabilities:Foreign"),
        ("Liabilities:Card", "Liabilities:Foreign"),
    ],
)
@pytest.mark.parametrize("fee_currency", ["UAH", "EUR"])
def test_cross_currency_liability_transfers_preserve_explicit_amounts_and_fees(
    tmp_path: Path, source: str, target: str, fee_currency: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.create_account(
            "Foreign",
            "Foreign debt",
            "EUR",
            "10",
            root="Liabilities",
            opened=EPOCH,
            opening_date=EPOCH,
        )
        before = journal.ledger.balances()
        await transfer(
            journal,
            source_account=source,
            destination=target,
            amount="100",
            destination_amount="2",
            fee="1",
            fee_currency=fee_currency,
            occurred="2026-03-10",
            timestamp="2026-03-10T12:00",
        )
        after = journal.ledger.balances()
        assert after[source] == before[source] - 100
        assert after[target] == before[target] + 2
        assert journal.ledger.period_totals(*MARCH) == {
            "income": {},
            "expenses": {fee_currency: Decimal("1")},
        }
        assert not journal.ledger.balance_warnings()
        assert " @@" in journal.source
        assert not loader.load_string(journal.source)[1]
        assert not FavaLedger(str(tmp_path / FILENAME), poll_watcher=False).errors
        saved = journal.source
        await journal.load()
        assert journal.source == saved and journal.ledger.balances() == after
        view = FinanceView()
        view.journal = journal
        for identifier in (source, target):
            account = next(
                account for account in journal.ledger.accounts if account.id == identifier
            )
            text = view._account_balance_text(identifier, after[identifier], account.currency)
            if account.liability:
                assert text == view.tr(
                    "account_debt_balance",
                    amount=format_amount(-after[identifier], view.language, account.currency),
                )
            else:
                assert text == format_amount(after[identifier], view.language, account.currency)

    asyncio.run(scenario())


def test_liability_fees_refunds_adjustments_and_asset_warnings(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.add_expense(
            "Liabilities:Card",
            "Expenses:Food",
            "100",
            "",
            "2026-03-10",
            fee="5",
            fee_category="Expenses:Bank-Fees",
        )
        expense = journal.ledger.transactions[-1]
        assert journal.ledger.balances()["Liabilities:Card"] == Decimal("-405")
        assert not journal.ledger.balance_warnings()
        await journal.add_incoming(
            "Liabilities:Card",
            "Expenses:Food",
            "100",
            "",
            "2026-03-11",
            "reimbursement",
            related_expense=expense.id,
        )
        assert journal.ledger.balances()["Liabilities:Card"] == Decimal("-305")
        assert set(journal.ledger.transactions[-1].links) == set(
            next(t for t in journal.ledger.transactions if t.id == expense.id).links
        )
        assert journal.ledger.transactions[-1].links
        await journal.add_incoming(
            "Liabilities:Card", ADJUSTMENTS, "10", "", "2026-03-12", "adjustment"
        )
        assert journal.ledger.balances()["Liabilities:Card"] == Decimal("-295")
        await transfer(
            journal,
            destination="Liabilities:Card",
            amount="2000",
            occurred="2026-03-13",
            timestamp="2026-03-13T12:00",
            fee="5",
            fee_category="Expenses:Bank-Fees",
        )
        assert journal.ledger.balances()["Liabilities:Card"] == Decimal("1705")
        assert journal.ledger.balances()["Assets:Bank"] == Decimal("-1005")
        assert journal.ledger.balance_warnings() == {"Assets:Bank"}
        assert journal.ledger.period_totals(date(2026, 3, 1), date(2026, 4, 1)) == {
            "income": {},
            "expenses": {"UAH": Decimal("10")},
        }
        original = journal.source
        with pytest.raises(LedgerError, match="commission_amount"):
            await journal.add_incoming(
                "Liabilities:Card", ADJUSTMENTS, "1", "", "2026-03-14", "adjustment", fee="1"
            )
        assert journal.source == original
        balances = journal.ledger.balances()
        await journal.load()
        assert journal.ledger.balances() == balances
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize(
    ("repayment", "balance", "key", "display"),
    [
        ("0", "-300", "account_debt_balance", "300"),
        ("300", "0", "account_debt_balance", "0"),
        ("800", "500", "account_credit_balance", "500"),
    ],
)
def test_liability_balance_labels_on_home_accounts_details_reports_and_forms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    repayment: str,
    balance: str,
    key: str,
    display: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        if repayment != "0":
            await transfer(
                journal,
                destination="Liabilities:Card",
                amount=repayment,
                occurred="2026-03-10",
                timestamp="2026-03-10T12:00",
            )
        page = MagicMock(spec=ft.Page)
        page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        monkeypatch.setattr(FinanceView, "_today", lambda _: TODAY)
        view = FinanceView()
        view.language, view.timezone_name, view.journal = language, "UTC", journal
        view._refresh()
        view._render_reports()
        expected = view.tr(key, amount=format_amount(Decimal(display), language, "UAH"))
        for control in (view.home_balances, view.account_list, view.report_balances):
            assert expected in [item.value for item in walk(control) if isinstance(item, ft.Text)]
        cards = view._report_balance_cards(
            account_balances(journal.ledger, date(2026, 3, 1), date(2026, 3, 31)), ("UAH",)
        )
        assert expected in [
            item.value for card in cards for item in walk(card) if isinstance(item, ft.Text)
        ]
        event = MagicMock()
        event.control.data = ("account", "Liabilities:Card")
        view._edit_entity(event)
        dialog = page.show_dialog.call_args.args[0]
        assert expected in [
            item.value for item in walk(dialog.content) if isinstance(item, ft.Text)
        ]
        view._new_transaction(MagicMock())
        form = transaction_form(page.show_dialog.call_args.args[0])
        form.account.value = "Liabilities:Card"
        change(form.account)
        assert form.account.helper_text == expected
        form.amount.value = "100"
        change(form.amount)
        assert form.account.helper_text == view.tr(
            "form_liability_balance",
            now=expected,
            after=view._account_balance_text("Liabilities:Card", Decimal(balance) - 100, "UAH"),
        )
        assert form.account.helper_style is None
        assert journal.ledger.balances()["Liabilities:Card"] == Decimal(balance)
        assert view._account_balance_text("Assets:Loan", Decimal("-100"), "UAH") == (
            format_amount(Decimal("-100"), language, "UAH")
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("amount", ["200", "300", "800", "2000"])
def test_repayment_form_crosses_zero_without_liability_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amount: str
) -> None:
    journal = asyncio.run(seeded(tmp_path))
    view, page = open_form(journal, monkeypatch)
    form = transaction_form(page.show_dialog.call_args.args[0])
    form.sign.selected = ["transfer"]
    form.account.value, form.second_account.value = "Assets:Bank", "Liabilities:Card"
    change(form.sign)
    form.amount.value = amount
    change(form.amount)
    assert form.second_account.helper_text == view.tr(
        "form_liability_balance",
        now=view.tr("account_debt_balance", amount=format_amount(Decimal("300"), "en", "UAH")),
        after=view._account_balance_text("Liabilities:Card", Decimal(amount) - 300, "UAH"),
    )
    assert form.second_account.helper_style is None
    assert form.account.helper_style == (
        ft.TextStyle(color=ft.Colors.ERROR) if Decimal(amount) > 1000 else None
    )


@pytest.mark.parametrize("balance", ["-78317.92", "0.00", "500.00"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_existing_native_liability_journal_loads_losslessly(
    tmp_path: Path, balance: str, newline: str
) -> None:
    source = f"""1970-01-01 open Liabilities:People:Alex UAH
    note: "Keep my account annotation"
1970-01-01 open Equity:Opening-Balances
2026-03-01 * "Existing opening" #original ^personal
    note: "Keep my transaction annotation"
    Liabilities:People:Alex {balance} UAH
    Equity:Opening-Balances {-Decimal(balance)} UAH
""".replace("\n", newline)
    (tmp_path / FILENAME).write_text(source, encoding="utf-8", newline="")

    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: TODAY)
        await journal.load()
        assert journal.ready and journal.source == source
        assert journal.ledger.accounts[0].liability
        assert journal.ledger.balances() == {"Liabilities:People:Alex": Decimal(balance)}
        assert journal.opening_balance("Liabilities:People:Alex") == -Decimal(balance)
        await journal.edit_account(
            "Liabilities:People:Alex", "Personal debt", None, True, journal.source
        )
        assert 'note: "Keep my account annotation"' in journal.source
        assert 'note: "Keep my transaction annotation"' in journal.source
        assert "#original ^personal" in journal.source
        assert journal.ledger.balances() == {"Liabilities:People:Alex": Decimal(balance)}
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())
