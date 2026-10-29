import asyncio
from datetime import date, datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from beancount import loader
from form_controls import change, click, click_async, transaction_form

from app.domain.ledger import ADJUSTMENTS, BANK_FEES, EPOCH
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import translate
from app.ui.finance import FinanceView


@pytest.fixture(autouse=True)
def fixed_form_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "FixedDatetime":
            value = cls(2026, 10, 20, 12).astimezone()
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr("app.ui.datetime_fields.datetime", FixedDatetime)


async def seeded(path: Path) -> Journal:
    journal = Journal(DesktopFolder(str(path), FILENAME), today=lambda: date(2026, 10, 20))
    await journal.load()
    await journal.create_account("Bank", "Bank", "UAH", "10000", opened=EPOCH, opening_date=EPOCH)
    await journal.create_account("Cash", "Cash", "UAH", opened=EPOCH)
    await journal.create_account(
        "Dollar", "Dollar", "USD", "1000", opened=EPOCH, opening_date=EPOCH
    )
    await journal.create_category("Food", "Food", opened=EPOCH)
    await journal.create_category("Salary", "Salary", root="Income", opened=EPOCH)
    return journal


def ui(journal: Journal, monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.language, view.timezone_name, view.journal = "uk", "UTC", journal
    return view, page


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("editing", [False, True])
def test_advanced_fields_reserve_space_for_floating_time_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, editing: bool
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        if editing:
            await journal.add_expense(
                "Assets:Bank",
                "Expenses:Food",
                "10",
                "Synthetic shop",
                "2026-10-18",
                "Synthetic description",
                "2026-10-18T12:34:56",
            )
        view, page = ui(journal, monkeypatch)
        view.language = language
        transaction = journal.ledger.transactions[-1] if editing else None
        view._transaction_dialog(journal, transaction)
        form = transaction_form(page.show_dialog.call_args.args[0])
        assert form.advanced.controls is not None and len(form.advanced.controls) == 1
        fields = form.advanced.controls[0]
        assert isinstance(fields, ft.Column)
        assert fields.spacing == 16 and fields.tight
        assert fields.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        assert form.advanced.controls_padding == ft.Padding.only(top=8)
        index = fields.controls.index(form.payee)
        assert fields.controls[index + 1] is form.when.time_controls
        assert form.when.clock.label == translate(language, "time")
        occurred = form.when.occurred.value
        form.payee.value = "Unfinished draft"
        form.when.clock.value = "17:"
        for sign in ("+", "transfer", "-"):
            form.sign.selected = [sign]
            change(form.sign)
            assert form.advanced.controls == [fields]
            assert fields.controls[index + 1] is form.when.time_controls
            assert form.payee.value == "Unfinished draft"
            assert form.when.clock.value == "17:"
            assert form.when.occurred.value == occurred

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("editing", [False, True])
def test_category_and_date_inputs_use_matching_row_spacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, editing: bool
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        if editing:
            await journal.add_expense("Assets:Bank", "Expenses:Food", "10", "", "2026-10-18")
        view, page = ui(journal, monkeypatch)
        view.language = language
        view._transaction_dialog(journal, journal.ledger.transactions[-1] if editing else None)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        category_row = next(
            control
            for control in dialog.content.controls
            if isinstance(control, ft.Row)
            and control.controls == [form.category, form.category_create]
        )
        date_row = form.when.controls[0]
        assert isinstance(date_row, ft.Row)
        assert category_row.spacing == date_row.spacing == 0
        assert form.category.expand is True and form.when.occurred.expand is True
        date_button = date_row.controls[1]
        assert isinstance(date_button, ft.IconButton)
        assert form.category_create.width == date_button.width
        assert form.category_create.size_constraints == date_button.size_constraints

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "language,label",
    [("en", "Transaction code"), ("uk", "Код операції"), ("ru", "Код операции")],
)
@pytest.mark.parametrize("deleting", [False, True])
def test_transaction_popups_label_the_code_in_small_italic_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, label: str, deleting: bool
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.add_expense("Assets:Bank", "Expenses:Food", "10", "", "2026-10-18")
        transaction = journal.ledger.transactions[-1]
        view, page = ui(journal, monkeypatch)
        view.language = language
        before = journal.source
        backups = list((tmp_path / "backups").iterdir())
        event = MagicMock(control=ft.IconButton(data=transaction.id))
        if deleting:
            view._delete_transaction(event)
            dialog = page.show_dialog.call_args.args[0]
            assert isinstance(dialog.content, ft.Column)
            fields = dialog.content
        else:
            view._edit_transaction(event)
            dialog = page.show_dialog.call_args.args[0]
            advanced = transaction_form(dialog).advanced
            assert advanced.controls is not None
            fields = advanced.controls[0]
            assert isinstance(fields, ft.Column)
        code = next(
            control
            for control in fields.controls
            if isinstance(control, ft.Text) and transaction.id in (control.value or "")
        )
        assert code.value == f"{label}: {transaction.id}"
        assert code.selectable and code.italic and code.size == 12
        click(dialog.actions[0])
        assert journal.source == before
        assert list((tmp_path / "backups").iterdir()) == backups

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_description_first_aligned_fields_and_sign_explanation_follow_account_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view.language = language
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert isinstance(dialog.content, ft.Column)
        assert dialog.content.controls[0] is form.description
        assert form.sign.width == 132
        assert dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        assert form.advanced.expanded_cross_axis_alignment == ft.CrossAxisAlignment.STRETCH
        assert form.advanced.controls is not None
        advanced_fields = form.advanced.controls[0]
        assert isinstance(advanced_fields, ft.Column)
        fees = advanced_fields.controls[2]
        assert isinstance(fees, ft.Column)
        assert fees.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
        for dropdown in (
            form.account,
            form.category,
            form.second_account,
            form.special,
            form.reference,
            form.fee_mode,
            form.fee_currency,
            form.fee_category,
        ):
            assert dropdown.expand is True
        form.account.value = "Assets:Bank"
        form.amount.value, form.second_amount.value = "105", "4200"
        form.description.value = "Description stays primary"
        for second in ("", "Assets:Cash", "Assets:Dollar"):
            form.second_account.value = second
            for sign in ("-", "+", "transfer"):
                form.sign.selected = [sign]
                change(form.sign)
                negative = sign == "-"
                transferring = sign == "transfer"
                assert form.sign_hint.value == translate(
                    language,
                    "amount_transfer_hint"
                    if transferring
                    else "amount_debit_hint"
                    if negative
                    else "amount_credit_hint",
                )
                assert form.account.label == translate(
                    language,
                    "source_account" if negative or transferring else "destination_account",
                )
                assert form.second_account.label == translate(language, "destination_account")
                assert form.second_account.visible == transferring
                category_row = dialog.content.controls[4]
                assert isinstance(category_row, ft.Row)
                assert category_row.visible == (not transferring)
                special_row = advanced_fields.controls[0]
                assert isinstance(special_row, ft.Row)
                assert special_row.visible == (sign == "+")
                assert (form.amount.value, form.second_amount.value) == ("105", "4200")
                assert form.description.value == "Description stays primary"

    asyncio.run(scenario())


@pytest.mark.parametrize(("mode", "root"), [("-", "Expenses"), ("+", "Income")])
def test_create_category_from_transaction_and_select_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, root: str
) -> None:
    async def scenario() -> None:
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2026, 10, 20))
        await journal.load()
        await journal.create_account("Bank", "Bank", "UAH", "10000")
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        transaction_dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(transaction_dialog)
        assert form.category.hint_text == translate("uk", "category_none_available")
        if mode == "+":
            form.sign.selected = ["+"]
            change(form.sign)
        click(form.category_create)
        category_dialog = page.show_dialog.call_args.args[0]
        assert isinstance(category_dialog, ft.AlertDialog)
        assert category_dialog.title == "Додати категорію"
        assert isinstance(category_dialog.content, ft.Column)
        category_type = category_dialog.content.controls[0]
        assert isinstance(category_type, ft.Text)
        assert category_type.value == ("Дохід" if mode == "+" else "Витрата")
        path = next(
            control
            for control in category_dialog.content.controls
            if isinstance(control, ft.TextField) and control.data == "quick_category_path"
        )
        name = next(
            control
            for control in category_dialog.content.controls
            if isinstance(control, ft.TextField) and control.data == "quick_category_name"
        )
        path.value, name.value = "Daily", "Щоденні покупки"
        create_action = category_dialog.actions[-1]
        assert isinstance(create_action, ft.TextButton)
        await click_async(create_action)
        assert journal.source != ""
        assert form.category.value == f"{root}:Daily"
        assert form.category.hint_text is None
        assert form.category.value in {option.key for option in form.category.options}
        created = next(item for item in journal.ledger.categories if item.id == f"{root}:Daily")
        assert created.name == "Щоденні покупки"
        form.account.value, form.amount.value = "Assets:Bank", "25"
        await transaction_dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        transaction = journal.ledger.transactions[-1]
        assert transaction.kind == ("income" if mode == "+" else "expense")
        assert any(posting.account == f"{root}:Daily" for posting in transaction.postings)
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


def test_inline_category_suggestion_uses_parent_and_preserves_transaction_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        transaction_dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(transaction_dialog)
        form.description.value = "Keep this draft"
        initial_transactions = journal.ledger.transactions
        click(form.category_create)
        category_dialog = page.show_dialog.call_args.args[0]
        assert isinstance(category_dialog, ft.AlertDialog)
        assert isinstance(category_dialog.content, ft.Column)
        controls = category_dialog.content.controls or []
        parent_row = next(
            control
            for control in controls
            if isinstance(control, ft.Row)
            and any(
                isinstance(child, ft.Dropdown) and child.data == "quick_category_parent"
                for child in control.controls
            )
        )
        parent = parent_row.controls[0]
        assert isinstance(parent, ft.Dropdown)
        assert parent.expand is True and parent.width is None
        assert parent_row.width is None and not parent_row.wrap
        name = next(
            control
            for control in controls
            if isinstance(control, ft.TextField) and control.data == "quick_category_name"
        )
        path = next(
            control
            for control in controls
            if isinstance(control, ft.TextField) and control.data == "quick_category_path"
        )
        parent.value = "Expenses:Food"
        assert parent.on_select is not None
        MagicMock(wraps=parent.on_select)(MagicMock(control=parent))
        name.value = "Кафе"
        assert name.on_change is not None
        MagicMock(wraps=name.on_change)(MagicMock(control=name))
        assert path.value == "Expenses:Food:Cafe"

        actions = category_dialog.actions or []
        assert actions
        create = actions[-1]
        assert isinstance(create, ft.TextButton)
        await click_async(create)
        assert form.category.value == "Expenses:Food:Cafe"
        assert form.description.value == "Keep this draft"
        assert journal.ledger.transactions == initial_transactions

    asyncio.run(scenario())


def test_failed_quick_category_creation_keeps_transaction_and_category_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        transaction_dialog = page.show_dialog.call_args.args[0]
        original_source = journal.source
        form = transaction_form(transaction_dialog)
        form.description.value = "Keep transaction draft"
        click(form.category_create)
        category_dialog = page.show_dialog.call_args.args[0]
        path = next(
            control
            for control in category_dialog.content.controls
            if isinstance(control, ft.TextField) and control.data == "quick_category_path"
        )
        name = next(
            control
            for control in category_dialog.content.controls
            if isinstance(control, ft.TextField) and control.data == "quick_category_name"
        )
        path.value, name.value = "Food", "Food"
        monkeypatch.setattr(
            journal,
            "create_category",
            AsyncMock(side_effect=OSError("synthetic storage failure")),
        )
        create_action = category_dialog.actions[-1]
        assert isinstance(create_action, ft.TextButton)
        await click_async(create_action)
        assert journal.source == original_source
        assert form.description.value == "Keep transaction draft"
        assert not create_action.disabled
        assert page.show_dialog.call_count == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("reverse_direction", [False, True])
@pytest.mark.parametrize("exchange", [False, True])
@pytest.mark.parametrize("fee_case", ["none", "source-added", "destination-deducted"])
def test_explicit_transfer_creates_edits_and_reverses_with_source_and_destination_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse_direction: bool,
    exchange: bool,
    fee_case: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        before = journal.ledger.balances()
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        primary = "Assets:Dollar" if exchange else "Assets:Bank"
        second = "Assets:Bank" if exchange else "Assets:Cash"
        form.account.value, form.second_account.value = primary, second
        form.amount.value, form.second_amount.value = (
            ("105.00", "4200.00") if exchange else ("1000", "unused")
        )
        if reverse_direction:
            form.account.value, form.second_account.value = second, primary
            if exchange:
                form.amount.value, form.second_amount.value = (
                    form.second_amount.value,
                    form.amount.value,
                )
        form.sign.selected = ["transfer"]
        form.fee_enabled.value = fee_case != "none"
        form.fee.value = "5"
        form.fee_mode.value = "deducted" if fee_case.endswith("deducted") else "added"
        form.when.occurred.value, form.when.clock.value = "20.10.2026", "12:00"
        change(form.second_account)
        if exchange:
            currencies = {a.id: a.currency for a in journal.ledger.accounts}
            sender, target = form.account.value, form.second_account.value
            form.fee_currency.value = currencies[
                target if fee_case.startswith("destination") else sender
            ]
            change(form.fee_currency)
        assert not form.category.visible
        assert form.second_amount.visible == exchange
        assert not form.advanced.expanded
        assert form.fee_mode.visible == (not exchange and fee_case != "none")
        amount, received, currency = (
            form.amount.value,
            form.second_amount.value,
            form.fee_currency.value,
        )
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        original = journal.ledger.transactions[-1]
        details = journal.transfer_details(original)
        assert details.source.account == (second if reverse_direction else primary)
        assert details.target.account == (primary if reverse_direction else second)
        if exchange:
            assert details.debited == Decimal(amount)
            assert details.target.amount == Decimal(received)
            assert details.fee_currency == (
                currency if fee_case != "none" else details.source.currency
            )
        else:
            assert details.debited == (1005 if fee_case == "source-added" else 1000)
            assert details.target.amount == (995 if fee_case == "destination-deducted" else 1000)
        assert not loader.load_string(journal.source)[1]
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.sign.selected == ["transfer"] and form.sign.disabled
        assert not form.advanced.expanded
        original_primary, original_second = form.account.value, form.second_account.value
        amount, received, currency = (
            form.amount.value,
            form.second_amount.value,
            form.fee_currency.value,
        )
        form.account.value, form.second_account.value = original_second, original_primary
        if exchange:
            form.amount.value, form.second_amount.value = received, amount
        change(form.account)
        assert form.fee_currency.value == currency
        if exchange:
            assert form.amount.label == f"Списано ({details.target.currency})"
            assert form.second_amount.label == f"Зараховано ({details.source.currency})"
        form.description.value = "Reversed without a migration"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        updated = journal.transaction_for_edit(original.id)
        reverse = journal.transfer_details(updated)
        assert reverse.source.account == details.target.account
        assert reverse.target.account == details.source.account
        assert updated.timestamp == original.timestamp
        assert reverse.fee == details.fee and reverse.fee_currency == (
            details.fee_currency if details.fee else reverse.source.currency
        )
        if exchange:
            assert reverse.debited == details.target.amount
            assert reverse.target.amount == details.debited
        assert not loader.load_string(journal.source)[1]
        await journal.load()
        assert journal.transaction_for_edit(original.id) == updated
        # The replacement, not both transfer directions, contributes to balances.
        for account in journal.ledger.accounts:
            delta = sum(p.amount for p in updated.postings if p.account == account.id)
            assert journal.ledger.balances()[account.id] == before[account.id] + delta
        totals = journal.ledger.period_totals(date(2026, 1, 1), date(2026, 10, 20))
        assert totals == {
            "income": {},
            "expenses": {reverse.fee_currency: reverse.fee} if reverse.fee else {},
        }
        await journal.delete_transaction(original.id, journal.source)
        assert journal.ledger.balances() == before

    asyncio.run(scenario())


def test_sign_second_account_and_hidden_controls_preserve_draft_and_infer_only_visible_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.sign.selected == ["-"]
        assert isinstance(dialog.content, ft.Column)
        assert dialog.content.controls[0] is form.description
        row = dialog.content.controls[1]
        assert isinstance(row, ft.Row) and row.controls == [form.sign, form.amount]
        assert dialog.content.controls[2] is form.sign_hint
        for row, dropdown in zip(
            dialog.content.controls[3:6],
            (form.account, form.category, form.second_account),
            strict=True,
        ):
            assert isinstance(row, ft.Row)
            if dropdown is form.category:
                assert row.controls == [dropdown, form.category_create]
            else:
                assert row.controls == [dropdown]
        assert len(form.when.controls) == 1
        assert form.advanced.controls is not None
        advanced_fields = form.advanced.controls[0]
        assert isinstance(advanced_fields, ft.Column)
        assert form.when.time_controls in advanced_fields.controls
        assert not form.advanced.expanded and not form.special.visible
        assert form.second_account.value is None
        form.account.value, form.category.value = "Assets:Bank", "Expenses:Food"
        form.amount.value, form.second_amount.value = "12.34", "400.00"
        form.description.value, form.payee.value = "Draft\ntext", "Person"
        original_time = form.when.values()
        form.sign.selected = ["+"]
        change(form.sign)
        assert form.amount.value == "12.34"
        assert form.category.value is None
        form.category.value = "Income:Salary"
        form.second_account.value = "Assets:Dollar"
        form.sign.selected = ["transfer"]
        change(form.second_account)
        assert not form.category.visible and not form.special.visible
        assert form.second_amount.value == "400.00"
        form.second_account.value = "Assets:Cash"
        change(form.second_account)
        assert not form.second_amount.visible and form.second_amount.value == "400.00"
        form.sign.selected = ["+"]
        change(form.sign)
        assert form.category.visible and form.category.value == "Income:Salary"
        assert not form.second_account.visible
        assert form.second_account.value == "Assets:Cash"
        form.special.value = "adjustment"
        change(form.special)
        assert not form.category.visible
        form.sign.selected = ["-"]
        change(form.sign)
        assert form.category.value == "Expenses:Food"
        assert form.amount.value == "12.34" and form.second_amount.value == "400.00"
        assert form.description.value == "Draft\ntext" and form.payee.value == "Person"
        assert form.when.values() == original_time
        assert not form.special.visible
        # Stale hidden transfer and special values must not leak into an expense.
        form.fee_enabled.value, form.fee.value = True, "not a fee"
        form.fee_currency.value = "XXX"
        form.reference.value = "not a linked expense"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        transaction = journal.ledger.transactions[-1]
        assert transaction.kind == "expense" and transaction.related_expense is None
        assert len(transaction.postings) == 2 and transaction.postings[0].currency == "UAH"
        assert BANK_FEES not in {c.id for c in journal.ledger.categories}
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income", "reimbursement", "adjustment"])
def test_saved_kind_protection_is_visible_and_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.choose(kind)
        form.account.value, form.amount.value = "Assets:Bank", "5"
        form.category.value = (
            ADJUSTMENTS
            if kind == "adjustment"
            else "Income:Salary"
            if kind == "income"
            else "Expenses:Food"
        )
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        original = journal.ledger.transactions[-1]
        source = journal.source
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.sign.disabled and form.second_account.disabled and form.special.disabled
        assert form.special.value == (kind if kind != "expense" else "income")
        assert not form.advanced.expanded
        assert form.advanced.subtitle
        form.second_account.value = "Assets:Cash"
        form.sign.selected = ["transfer"]
        change(form.sign)
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "crud_transaction_readonly"
        assert journal.source == source
        assert journal.transaction_for_edit(original.id) == original
        form.second_account.value = ""
        form.sign.selected = ["+" if kind == "expense" else "-"]
        change(form.sign)
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "crud_transaction_readonly"
        assert journal.source == source

    asyncio.run(scenario())


def test_reversing_exchange_with_impossible_fee_refuses_save_without_changing_amounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        await journal.save_transfer(
            "Assets:Dollar",
            "Assets:Bank",
            "100",
            "5",
            BANK_FEES,
            "added",
            "2026-10-20",
            "2026-10-20T12:00:00+00:00",
            "Synthetic",
            "",
            journal.source,
            destination_amount="1",
            fee_currency="UAH",
        )
        original = journal.ledger.transactions[-1]
        source = journal.source
        view, page = ui(journal, monkeypatch)
        event = MagicMock()
        event.control.data = original.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.account.value, form.second_account.value = "Assets:Bank", "Assets:Dollar"
        form.amount.value, form.second_amount.value = "1.00", "100.00"
        change(form.account)
        assert form.amount.value == "1.00" and form.second_amount.value == "100.00"
        assert form.fee_currency.value == "UAH"
        assert form.advanced.title == "Додатково"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "transfer_amount"
        assert form.amount.value == "1.00" and form.second_amount.value == "100.00"
        assert journal.source == source == (tmp_path / FILENAME).read_text()

    asyncio.run(scenario())


@pytest.mark.parametrize("amount", ["-1", "+1", "0"])
def test_signed_or_nonpositive_input_is_rejected_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amount: str,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.account.value, form.category.value, form.amount.value = (
            "Assets:Bank",
            "Expenses:Food",
            amount,
        )
        original = journal.source
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is not None
        assert form.amount.value == amount
        assert journal.source == original

    asyncio.run(scenario())


def test_primary_account_cannot_be_selected_as_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.sign.selected = ["transfer"]
        form.account.value = "Assets:Bank"
        change(form.account)
        assert "Assets:Bank" not in {o.key for o in form.second_account.options}
        form.second_account.value, form.amount.value = "Assets:Bank", "10"
        change(form.second_account)
        assert form.second_account.error_text
        original = journal.source
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "transfer_accounts"
        assert journal.source == original

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["-", "+", "transfer"])
def test_mode_controls_save_semantics_and_retains_hidden_transfer_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert [segment.value for segment in form.sign.segments] == ["-", "+", "transfer"]
        form.account.value, form.second_account.value = "Assets:Dollar", "Assets:Bank"
        form.amount.value, form.second_amount.value = "105", "4200"
        form.sign.selected = ["transfer"]
        form.fee_enabled.value, form.fee.value, form.fee_currency.value = True, "5", "USD"
        change(form.sign)
        assert form.second_account.visible and form.second_amount.visible
        assert form.fee.visible and form.fee_currency.visible
        for ordinary in ("-", "+"):
            form.sign.selected = [ordinary]
            change(form.sign)
            assert not form.second_account.visible and not form.second_amount.visible
            assert not form.fee.visible and not form.fee_currency.visible
            assert not form.preview.visible
            assert form.special.visible == (ordinary == "+")
            assert form.fee_currency.value == "USD" and form.fee.value == "5"
            assert form.second_account.value == "Assets:Bank"
            assert (form.amount.value, form.second_amount.value) == ("105", "4200")
        form.sign.selected = [mode]
        change(form.sign)
        form.category.value = "Income:Salary" if mode == "+" else "Expenses:Food"
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        assert saved.kind == {"-": "expense", "+": "income", "transfer": "transfer"}[mode]
        if mode == "transfer":
            details = journal.transfer_details(saved)
            assert details.debited == 105 and details.target.amount == 4200
            assert details.fee == 5 and details.fee_currency == "USD"
        else:
            assert len(saved.postings) == 2
            assert not any(p.account == "Assets:Bank" for p in saved.postings)
            assert not any(p.account == BANK_FEES for p in saved.postings)
        assert not loader.load_string(journal.source)[1]

    asyncio.run(scenario())


def test_transfer_requires_destination_and_retains_saved_kind_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        journal = await seeded(tmp_path)
        view, page = ui(journal, monkeypatch)
        view._new_transaction(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        form.sign.selected = ["transfer"]
        form.account.value, form.amount.value = "Assets:Bank", "10"
        change(form.sign)
        assert form.second_account.visible and not form.category.visible
        assert "" not in {option.key for option in form.second_account.options}
        original = journal.source
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "transfer_accounts"
        assert journal.source == original and form.amount.value == "10"
        form.second_account.value = "Assets:Cash"
        change(form.second_account)
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message is None
        saved = journal.ledger.transactions[-1]
        event = MagicMock()
        event.control.data = saved.id
        view._edit_transaction(event)
        dialog = page.show_dialog.call_args.args[0]
        form = transaction_form(dialog)
        assert form.sign.disabled and form.sign.selected == ["transfer"]
        assert form.second_account.visible and not form.second_account.disabled
        original = journal.source
        form.sign.selected = ["+"]
        change(form.sign)
        await dialog.actions[-1].on_click(MagicMock())
        assert view.error_message == "crud_transaction_readonly"
        assert journal.source == original

    asyncio.run(scenario())
