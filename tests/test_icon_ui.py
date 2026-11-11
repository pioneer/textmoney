import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.messaging.session import Session
from form_controls import category_content, click, click_async, transaction_form
from test_daily_summary import walk
from test_dialog_keyboard import finance_session, press
from test_icons import asset, raster, store_at

from app.domain.budgets import Budget
from app.domain.ledger import EQUITY, Posting, Transaction
from app.domain.reporting import period_report
from app.icons import IconAsset, IconError, normalize_image
from app.infrastructure.icon_sources import IconifySearch, bundled_icons
from app.journal import FILENAME, Journal
from app.localization import format_amount
from app.storage import StorageError
from app.ui.dialogs import show_dialog
from app.ui.finance import FinanceView
from app.ui.icons import IconDraft, IconPicker, icon_image
from app.ui.tree_picker import TreePicker


def images(control: ft.Control) -> list[ft.Image]:
    return [child for child in walk(control) if isinstance(child, ft.Image)]


@pytest.mark.parametrize("width", [390, 1366])
def test_operations_filter_icons_hierarchy_and_popup_only_icon_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.create_category("Food:Shops", "Shops")
        view.icons = store_at(tmp_path)
        for identifier in ("Assets:Bank", "Expenses:Food", "Expenses:Food:Shops"):
            await view.icons.assign(identifier, asset(identifier), ())
        view.operations_account.value = "Assets:Bank"
        view.operations_category.value = "Expenses:Food:Shops"
        view._apply_layout(width)
        view._refresh()
        account = next(
            option for option in view.operations_account.options if option.key == "Assets:Bank"
        )
        assert account.text == journal.ledger.accounts[0].name
        assert view.operations_account.text_style == ft.TextStyle(size=16)
        assert view.operations_category.text_style == view.operations_account.text_style
        assert isinstance(account.leading_icon, ft.Container) and images(account.leading_icon)
        assert isinstance(view.operations_account.leading_icon, ft.Container)
        assert isinstance(view.operations_category.leading_icon, ft.Container)
        assert images(view.operations_account.leading_icon)
        assert images(view.operations_category.leading_icon)
        for control in (view.operations_account, view.operations_category):
            assert isinstance(control.leading_icon, ft.Container)
            assert control.leading_icon.width == control.leading_icon.height == 24
            assert images(control.leading_icon)[0].width == 16
            assert images(control.leading_icon)[0].height == 16
        categories = view.operations_category.options
        parent = next(option for option in categories if option.key == "Expenses:Food")
        child = next(option for option in categories if option.key == "Expenses:Food:Shops")
        assert categories.index(parent) < categories.index(child)
        for option, indent in ((parent, 0), (child, 16)):
            assert category_content(option).padding == ft.Padding.only(left=indent)
            assert images(category_content(option))
            label = next(
                control
                for control in walk(category_content(option))
                if isinstance(control, ft.Text)
            )
            assert option.text == label.value == ("Shops" if option is child else "Food")
            assert label.size is None and label.weight is None
            assert not label.spans
            assert images(category_content(option))[0].width == 16
            assert images(category_content(option))[0].height == 16
        for listing in (view.account_list, view.category_list):
            assert not any(
                isinstance(control, ft.IconButton) and control.on_click == view._choose_entity_icon
                for control in walk(listing)
            )
        view.operations_account.value = view.operations_category.value = ""
        view._operations_filter_changed(MagicMock())
        assert view.operations_account.leading_icon is None
        assert view.operations_category.leading_icon is None

    asyncio.run(scenario())


def action(dialog: ft.AlertDialog, index: int) -> ft.TextButton:
    control = dialog.actions[index]
    if isinstance(control, ft.KeyboardListener):
        control = control.content
    assert isinstance(control, ft.TextButton)
    return control


def transaction_body(card: ft.Control) -> ft.Column:
    assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
    return next(child for child in card.content.controls if isinstance(child, ft.Column))


async def creation_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> tuple[Session, FinanceView, Journal, ft.AlertDialog, IconDraft, str]:
    session, view, journal = await finance_session(tmp_path, monkeypatch)
    view.language = "en"
    view.icons = store_at(tmp_path)
    await view.icons.load()
    if kind in {"asset", "liability"}:
        view._create_account(MagicMock())
        root = "Liabilities" if kind == "liability" else "Assets"
        identifier = f"{root}:New"
        view.account_root.value = root
        view.account_name.value = "New account"
        view.account_id.value = identifier
        view.currency.value = "UAH"
    elif kind.startswith("quick-"):
        view._transaction_dialog(journal)
        transaction = session.page._dialogs.controls[-1]
        assert isinstance(transaction, ft.AlertDialog)
        form = transaction_form(transaction)
        form.choose(kind.removeprefix("quick-"))
        form.amount.value = "125"
        form.description.value = "Parent draft"
        click(form.category_create)
        identifier = "Income:New" if kind == "quick-income" else "Expenses:New"
        dialog = session.page._dialogs.controls[-1]
        for control in walk(dialog):
            if isinstance(control, ft.TextField):
                if control.data == "quick_category_path":
                    control.value = identifier
                elif control.data == "quick_category_name":
                    control.value = "New category"
    else:
        view._create_category(MagicMock())
        root = "Income" if kind == "income" else "Expenses"
        identifier = f"{root}:New"
        view.category_root.value = root
        view.category_name.value = "New category"
        view.category_id.value = identifier
    dialog = session.page._dialogs.controls[-1]
    assert isinstance(dialog, ft.AlertDialog)
    draft = next(control for control in walk(dialog) if isinstance(control, IconDraft))
    return session, view, journal, dialog, draft, identifier


@pytest.mark.parametrize(
    "kind", ["asset", "liability", "expense", "income", "quick-expense", "quick-income"]
)
def test_creation_icon_is_a_draft_until_entity_is_saved_and_survives_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        original = journal.source
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        selected = bundled_icons("bank")[0]
        picker._choose(MagicMock(control=ft.TextButton(data=selected)))
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert not picker.open and parent.open
        assert draft.selected == selected
        assert images(draft)[0].width == images(draft)[0].height == 16
        assert journal.source == original
        assert view.icons is not None and not view.icons.records
        await click_async(action(parent, -1))
        assert not parent.open and view.error_message is None
        assert identifier in view._existing_identifiers(journal)
        assert view.icons.assets[identifier] == selected
        assert images(view.home_balances if kind in {"asset", "liability"} else view.category_list)
        if kind.startswith("quick-"):
            transaction = next(
                dialog
                for dialog in session.page._dialogs.controls
                if isinstance(dialog, ft.AlertDialog) and dialog.open
            )
            assert transaction.open
            form = transaction_form(transaction)
            assert form.category.value == identifier
            assert form.amount.value == "125" and form.description.value == "Parent draft"
            assert form.category.leading_icon is not None
            option = next(option for option in form.category.options if option.key == identifier)
            assert category_content(option).padding == ft.Padding.only(left=0)
            assert images(category_content(option))
        saved = journal.source
        reloaded = store_at(tmp_path)
        await reloaded.load()
        assert reloaded.assets[identifier] == selected
        await journal.load()
        assert journal.source == saved == (tmp_path / FILENAME).read_text()
        assert selected.label not in saved

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
@pytest.mark.parametrize("cancel", ["picker", "creation", "remove"])
def test_creation_icon_cancellation_and_removal_do_not_write_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, cancel: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        original = journal.source
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._choose(MagicMock(control=ft.TextButton(data=asset())))
        await click_async(picker.submit)
        assert draft.selected == asset()
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker) and picker.selected == asset()
        picker._choose(MagicMock(control=ft.TextButton(data=asset("Another"))))
        if cancel == "remove":
            click(picker.remove)
            await click_async(picker.submit)
            assert draft.selected is None and not draft.preview.visible
        else:
            await session.dispatch_event(picker.search._i, "focus", {})
            await press(session, "Escape")
            assert draft.selected == asset()
        if cancel == "creation":
            click(action(parent, 0))
            assert journal.source == original
            assert identifier not in view._existing_identifiers(journal)
        else:
            if cancel == "picker":
                assert journal.source == original
                click(action(parent, 0))
            else:
                await click_async(action(parent, -1))
                assert identifier in view._existing_identifiers(journal)
        assert view.icons is not None and not view.icons.records

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
def test_failed_entity_creation_keeps_icon_draft_without_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        _session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        draft.selected = asset()
        original = journal.source
        method = "create_account" if kind == "asset" else "create_category"
        monkeypatch.setattr(journal, method, AsyncMock(side_effect=StorageError("write failed")))
        await click_async(action(parent, -1))
        assert parent.open and draft.selected == asset()
        assert view.error_message == "write failed"
        assert journal.source == original
        assert identifier not in view._existing_identifiers(journal)
        assert view.icons is not None and not view.icons.records

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
@pytest.mark.parametrize("failure", ["write", "concurrent", "unavailable"])
def test_post_creation_icon_failure_closes_creation_and_reports_saved_entity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failure: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        assert view.icons is not None
        draft.selected = asset("Requested")
        if failure == "write":
            monkeypatch.setattr(
                view.icons, "assign", AsyncMock(side_effect=StorageError("write failed"))
            )
        elif failure == "concurrent":
            await view.icons.assign(identifier, asset("Concurrent"), ())
        else:
            view.icons = None
        await click_async(action(parent, -1))
        assert not parent.open
        assert identifier in view._existing_identifiers(journal)
        assert view.error_message == "icon_create_failed"
        assert view.icon_notice.visible and view.icon_notice.value == view.tr("icon_create_failed")
        assert "created" in (view.message.value or "")
        if failure == "concurrent":
            assert view.icons is not None and view.icons.assets[identifier].label == "Concurrent"
        if kind.startswith("quick-"):
            transaction = next(
                dialog
                for dialog in session.page._dialogs.controls
                if isinstance(dialog, ft.AlertDialog) and dialog.open
            )
            assert transaction.open
            assert transaction_form(transaction).category.value == identifier
        await journal.load()
        assert (
            sum(
                item.id == identifier
                for item in (*journal.ledger.accounts, *journal.ledger.categories)
            )
            == 1
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("case", ["narrated", "blank", "opening-small", "opening-large"])
def test_icon_movements_keep_consistent_columns_with_or_without_inline_amount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compact: bool, case: str
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset("Bank"), ())
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        transaction = journal.ledger.transactions[-1]
        if case == "blank":
            transaction = replace(transaction, narration="")
            await view.icons.assign("Expenses:Food", asset("Food"), ())
        elif case.startswith("opening"):
            amount = Decimal("123.17" if case == "opening-small" else "234601.87")
            transaction = replace(
                transaction,
                kind="opening",
                postings=(
                    replace(transaction.postings[0], account="Assets:Bank", amount=amount),
                    replace(
                        transaction.postings[1], account="Equity:Opening-Balances", amount=-amount
                    ),
                ),
            )
        card = view._transaction_card(
            transaction, {"Assets:Bank": "Bank", "Expenses:Food": "Food"}, compact
        )
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        details = transaction_body(card)
        assert isinstance(details, ft.Column)
        movement = details.controls[-1]
        assert isinstance(movement, ft.Row) and len(movement.controls) == 2
        source, destination = movement.controls
        assert source.expand is True
        assert isinstance(destination, ft.Row) and destination.expand == 2
        arrow, target, *amounts = destination.controls
        assert isinstance(arrow, ft.Text) and arrow.value == "→"
        assert target.expand is True
        assert len(amounts) == (0 if case == "narrated" else 1)
        labels = [
            child
            for control in (source, target)
            for child in walk(control)
            if isinstance(child, ft.Text)
        ]
        assert len(labels) == 2
        assert all(
            label.max_lines == 1 and label.overflow == ft.TextOverflow.ELLIPSIS for label in labels
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("balance", ["-123.17", "0", "234601.87"])
def test_opening_icons_keep_title_and_account_for_assets_and_liabilities(
    tmp_path: Path, language: str, compact: bool, balance: str
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.language = language
        view.icons = store_at(tmp_path)
        amount = Decimal(balance)
        for root in ("Assets", "Liabilities"):
            identifier = f"{root}:Bank"
            await view.icons.assign(identifier, asset("Bank"), ())
            transaction = Transaction(
                "opening",
                date(2026, 1, 1),
                "",
                "",
                "Existing opening narration",
                (Posting(identifier, amount, "UAH"), Posting(EQUITY, -amount, "UAH")),
                "opening",
            )
            card = view._transaction_card(transaction, {identifier: "Bank"}, compact)
            assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
            leading = card.content.controls[0]
            assert isinstance(leading, ft.Container) and isinstance(leading.content, ft.Image)
            assert leading.content.semantics_label == "Bank"
            assert leading.content.width == leading.content.height == 25.92
            details = transaction_body(card)
            assert isinstance(details, ft.Column) and len(details.controls) == 1
            movement = details.controls[0]
            assert isinstance(movement, ft.Row)
            source, destination = movement.controls
            assert isinstance(destination, ft.Row)
            arrow, target, rendered_amount = destination.controls
            labels = [child for child in walk(source) if isinstance(child, ft.Text)]
            assert len(labels) == 1 and labels[0].value == view.tr("kind_opening")
            assert labels[0].size == 10
            assert not images(source)
            assert [child.value for child in walk(target) if isinstance(child, ft.Text)] == ["Bank"]
            assert not images(target)
            assert isinstance(arrow, ft.Text) and arrow.value == "→"
            assert isinstance(rendered_amount, ft.Text)
            assert rendered_amount.value == format_amount(abs(amount), language, "UAH")
            assert source.expand is True and target.expand is True and destination.expand == 2
            assert transaction.narration == "Existing opening narration"
            assert transaction.postings[0].amount == amount

    asyncio.run(scenario())


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize(
    ("kind", "source", "target", "leading_id", "account_id"),
    [
        ("expense", "Assets:Bank", "Expenses:Food", "Expenses:Food", "Assets:Bank"),
        ("income", "Income:Work", "Assets:Bank", "Income:Work", "Assets:Bank"),
        ("reimbursement", "Expenses:Food", "Assets:Bank", "Expenses:Food", "Assets:Bank"),
        ("transfer", "Assets:Bank", "Assets:Other", "Assets:Other", "Assets:Bank"),
        ("opening", EQUITY, "Assets:Bank", "Assets:Bank", None),
        ("opening", EQUITY, "Liabilities:Card", "Liabilities:Card", None),
    ],
)
def test_transaction_icon_leads_the_whole_row_without_duplicating_movement_icon(
    tmp_path: Path,
    compact: bool,
    read_only: bool,
    kind: str,
    source: str,
    target: str,
    leading_id: str,
    account_id: str | None,
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.icons = store_at(tmp_path)
        await view.icons.assign(leading_id, asset("Leading"), ())
        if account_id is not None:
            await view.icons.assign(account_id, asset("Account"), ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Synthetic title",
            (Posting(source, Decimal("-10"), "UAH"), Posting(target, Decimal("10"), "UAH")),
            kind,
        )
        card = view._transaction_card(
            transaction, {source: "Source", target: "Target"}, compact, read_only=read_only
        )
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        leading = card.content.controls[0]
        assert isinstance(leading, ft.Container)
        assert leading.width == 40 and leading.height is None
        assert leading.bgcolor == ft.Colors.SECONDARY_CONTAINER
        assert leading.border_radius == 8
        assert leading.margin == ft.Margin.only(right=8)
        assert leading.alignment == ft.Alignment.CENTER
        image = leading.content
        assert isinstance(image, ft.Image)
        assert image.width == image.height == 25.92
        assert image.semantics_label == "Leading"
        assert image.fit == ft.BoxFit.CONTAIN and image.color == ft.Colors.ON_SURFACE
        assert image.tooltip == "Leading · CC0-1.0"
        assert card.content.intrinsic_height
        assert card.content.vertical_alignment == ft.CrossAxisAlignment.STRETCH
        details = transaction_body(card)
        assert card.content.controls[1] is details
        assert [item.semantics_label for item in images(details)] == (
            ["Account"] if account_id is not None else []
        )
        assert all(item.width == item.height == 16 for item in images(details))
        assert len(card.content.controls[2:]) == (0 if read_only else 1 if compact else 2)
        assert transaction.narration == "Synthetic title"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize("fee_mode", ["added", "deducted"])
def test_leading_cashflow_icon_uses_principal_category_not_commission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, fee_mode: str
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.create_category("Salary", "Salary", root="Income", opened=date(2000, 1, 1))
        await journal.create_category("Fees", "Fees", opened=date(2000, 1, 1))
        category = "Expenses:Food" if kind == "expense" else "Income:Salary"
        view.icons = store_at(tmp_path)
        for identifier, label in (
            (category, "Principal"),
            ("Assets:Bank", "Account"),
            ("Expenses:Fees", "Commission"),
        ):
            await view.icons.assign(identifier, asset(label), ())
        options = {"fee": "5", "fee_category": "Expenses:Fees", "fee_mode": fee_mode}
        if kind == "expense":
            await journal.add_expense(
                "Assets:Bank", category, "100", "", "2026-01-01", "Synthetic", **options
            )
        else:
            await journal.add_incoming(
                "Assets:Bank", category, "100", "", "2026-01-01", kind, "Synthetic", **options
            )
        source = journal.source
        view._refresh()
        for context in (view.home_activity, view.history):
            rendered = images(context)
            assert [image.semantics_label for image in rendered] == ["Principal", "Account"]
            assert [image.width for image in rendered] == [25.92, 16]
        assert journal.source == source == (tmp_path / FILENAME).read_text()

    asyncio.run(scenario())


@pytest.mark.parametrize("with_store", [False, True])
def test_missing_category_icon_preserves_row_without_fallback_slot(
    tmp_path: Path, with_store: bool
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        if with_store:
            view.icons = store_at(tmp_path)
            await view.icons.assign("Assets:Bank", asset("Account"), ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Synthetic title",
            (
                Posting("Assets:Bank", Decimal("-10"), "UAH"),
                Posting("Expenses:Food", Decimal("10"), "UAH"),
            ),
            "expense",
        )
        card = view._transaction_card(transaction, {"Assets:Bank": "Bank", "Expenses:Food": "Food"})
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        assert card.content.controls[0] is transaction_body(card)
        assert not card.content.intrinsic_height
        assert all(image.width == image.height == 16 for image in images(card))

    asyncio.run(scenario())


@pytest.mark.parametrize("artwork", ["monochrome-svg", "colored-raster"])
def test_leading_icon_preserves_artwork_and_does_not_resize_other_surfaces(
    tmp_path: Path, artwork: str
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.icons = store_at(tmp_path)
        selected = asset("Category")
        if artwork == "colored-raster":
            selected = replace(selected, data=normalize_image(raster(48, 96))[0], monochrome=False)
        await view.icons.assign("Expenses:Food", selected, ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Synthetic",
            (
                Posting("Assets:Bank", Decimal("-10"), "UAH"),
                Posting("Expenses:Food", Decimal("10"), "UAH"),
            ),
            "expense",
        )
        card = view._transaction_card(transaction, {})
        image = images(card)[0]
        assert image.width == image.height == 25.92
        assert image.src == selected.data and image.fit == ft.BoxFit.CONTAIN
        assert image.color == (ft.Colors.ON_SURFACE if selected.monochrome else None)
        assert image.tooltip == "Category · CC0-1.0"
        inline = view._entity_icon("Expenses:Food")
        assert isinstance(inline, ft.Image) and inline.width == inline.height == 16
        assert view.icons.assets["Expenses:Food"] == selected

    asyncio.run(scenario())


def test_complex_transaction_does_not_guess_a_leading_category(tmp_path: Path) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.icons = store_at(tmp_path)
        await view.icons.assign("Expenses:Food", asset("Food"), ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Complex entry",
            (
                Posting("Assets:Bank", Decimal("-10"), "UAH"),
                Posting("Expenses:Food", Decimal("5"), "UAH"),
                Posting("Expenses:Other", Decimal("5"), "UAH"),
            ),
            "external",
        )
        card = view._transaction_card(transaction, {})
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        assert card.content.controls[0] is transaction_body(card)
        assert not images(card)

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["Escape", "Enter"])
def test_picker_keys_cancel_or_save_and_leave_parent_draft_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        view._transaction_dialog(journal)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.payee.value = "Synthetic unsaved draft"
        original = (tmp_path / FILENAME).read_bytes()
        view._open_icon_picker(journal, "Assets:Bank")
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._choose(MagicMock(control=ft.TextButton(data=bundled_icons("bank")[0])))
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, key)
        assert not picker.open and parent.open
        assert form.payee.value == "Synthetic unsaved draft"
        assert (tmp_path / FILENAME).read_bytes() == original
        assert bool(view.icons.assets) == (key == "Enter")
        if key == "Enter":
            assert images(view.home_balances)

    asyncio.run(scenario())


def test_failed_save_retains_selection_and_does_not_close_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock(side_effect=IconError("icon_storage"))
        picker = IconPicker(session.page, "en", asset(), IconifySearch(), persist)
        show_dialog(session.page, picker)
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert picker.open and picker.selected == asset()
        assert "permissions" in (picker.status.value or "")
        assert not picker.submit.disabled and not picker.cancel.disabled

    asyncio.run(scenario())


def test_import_resizes_previews_and_requires_explicit_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock()
        data = raster(512, 512)
        monkeypatch.setattr(
            ft.FilePicker,
            "pick_files",
            AsyncMock(
                return_value=[
                    ft.FilePickerFile(id=1, name="synthetic.png", size=len(data), bytes=data)
                ]
            ),
        )
        picker = IconPicker(session.page, "en", None, IconifySearch(), persist)
        show_dialog(session.page, picker)
        await click_async(picker.import_button)
        assert picker.selected is not None
        assert not picker.selected.monochrome
        assert "permission" in (picker.status.value or "")
        assert len(images(picker.results)) == 1
        image = images(picker.results)[0]
        assert image.width == image.height == 16
        assert all(image.width == image.height == 16 for image in images(picker.preview))
        persist.assert_not_awaited()
        await press(session)
        persist.assert_not_awaited()

    asyncio.run(scenario())


def test_invalid_import_never_appears_as_candidate_or_replaces_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        data = raster(16, 16)
        monkeypatch.setattr(
            ft.FilePicker,
            "pick_files",
            AsyncMock(
                return_value=[ft.FilePickerFile(id=1, name="small.png", size=len(data), bytes=data)]
            ),
        )
        picker = IconPicker(session.page, "en", asset(), IconifySearch(), AsyncMock())
        show_dialog(session.page, picker)
        original = list(picker.results.controls)
        await click_async(picker.import_button)
        assert picker.selected == asset()
        assert picker.results.controls == original
        assert "48" in (picker.status.value or "")

    asyncio.run(scenario())


def test_stale_assignment_is_not_overwritten_and_selection_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        view._open_icon_picker(journal, "Assets:Bank")
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker.selected = asset("Draft")
        external = store_at(tmp_path)
        await external.assign("Assets:Bank", asset("External"), ())
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert picker.open and picker.selected.label == "Draft"
        assert "reopen" in (picker.status.value or "")
        assert view.icons.assets["Assets:Bank"].label == "External"

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_entity_icons_reach_lists_settings_transactions_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.language = language
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset("Bank"), ())
        await view.icons.assign("Expenses:Food", asset("Food"), ())
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        view._refresh()
        assert images(view.home_balances)
        assert images(view.account_list)
        assert images(view.category_list)
        assert len(images(view.history)) >= 2
        for context in (view.home_balances, view.account_list, view.category_list):
            assert all(image.width == image.height == 16 for image in images(context))
        assert [image.width for image in images(view.history)] == [25.92, 16]
        view.default_account.value = "Assets:Bank"
        view._render_default_account()
        assert isinstance(view.default_account.leading_icon, ft.Container)
        assert view.default_account.leading_icon.width == 24
        assert images(view.default_account.leading_icon)[0].width == 16
        assert any(
            isinstance(option.leading_icon, ft.Image) for option in view.default_account.options
        )
        report = period_report(
            journal.ledger,
            journal.ledger.transactions[-1].date,
            journal.ledger.transactions[-1].date,
        )
        section = view._report_category_section(
            "report_expense_categories",
            report.expense_categories,
            (),
            "UAH",
            report.currencies[0].expenses,
        )
        assert images(section)
        view._transaction_dialog(journal)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        form = transaction_form(dialog)
        account_option = next(
            option for option in form.account.options if option.key == "Assets:Bank"
        )
        category_option = next(
            option for option in form.category.options if option.key == "Expenses:Food"
        )
        assert isinstance(account_option.leading_icon, ft.Container)
        assert account_option.leading_icon.width == account_option.leading_icon.height == 24
        assert isinstance(account_option.leading_icon.content, ft.Image)
        assert (
            account_option.leading_icon.content.width
            == account_option.leading_icon.content.height
            == 16
        )
        assert category_content(category_option).padding == ft.Padding.only(left=0)
        assert images(category_content(category_option))
        assert all(
            image.width == image.height == 16 for image in images(category_content(category_option))
        )
        for option in (*form.category.options, *form.fee_category.options):
            for label in walk(category_content(option)):
                if isinstance(label, ft.Text):
                    assert label.size is None and label.weight is None
        form.account.value = "Assets:Bank"
        await form.account._trigger_event("select", None)
        assert isinstance(form.account.leading_icon, ft.Container)
        assert form.account.leading_icon.width == form.account.leading_icon.height == 24
        assert isinstance(form.account.leading_icon.content, ft.Image)
        form.category.value = "Expenses:Food"
        await form.category._trigger_event("select", None)
        assert isinstance(form.category.leading_icon, ft.Container)
        assert form.category.leading_icon.width == form.category.leading_icon.height == 24
        assert isinstance(form.category.leading_icon.content, ft.Image)

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [390, 1366])
def test_budget_and_default_selected_icons_keep_shared_dropdown_proportions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view._apply_layout(width)
        view.icons = store_at(tmp_path)
        for identifier in ("Assets:Bank", "Expenses:Food"):
            await view.icons.assign(identifier, asset(identifier), ())
        await journal.save_budget(
            "Expenses:Food", date(2026, 10, 1), "monthly", "50", "UAH", journal.source
        )
        view._refresh()
        before = journal.source
        budget = journal.ledger.budgets[-1]
        view.budgets._form(budget)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog) and isinstance(dialog.content, ft.Control)
        category = next(
            control
            for control in walk(dialog.content)
            if isinstance(control, ft.Dropdown) and control.label == view.tr("budget_category")
        )
        view.default_account.value = "Assets:Bank"
        view.default_fee.value = "Expenses:Food"
        view._render_default_account()
        view._render_fee_settings()
        for dropdown in (category, view.default_account, view.default_fee):
            icon = dropdown.leading_icon
            assert isinstance(icon, ft.Container)
            assert icon.width == icon.height == 24
            assert icon.alignment == ft.Alignment.CENTER
            assert isinstance(icon.content, ft.Image)
            assert icon.content.width == icon.content.height == 16
            if dropdown is not view.default_account:
                for option in dropdown.options:
                    for label in walk(category_content(option)):
                        if isinstance(label, ft.Text):
                            assert label.size is None and label.weight is None
            dropdown.value = ""
            view._entity_dropdown_selected(MagicMock(control=dropdown))
            assert dropdown.leading_icon is None
        category.value = "Expenses:Food"
        view._entity_dropdown_selected(MagicMock(control=category))
        assert isinstance(category.leading_icon, ft.Container)
        session.page.pop_dialog()
        view.budgets._delete(budget)
        deletion = session.page._dialogs.controls[-1]
        assert isinstance(deletion, ft.AlertDialog) and isinstance(deletion.content, ft.Control)
        assert [image.semantics_label for image in images(deletion.content)] == ["Expenses:Food"]
        assert journal.source == before

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["Expenses:Food", "Expenses:Food:Shops"])
def test_parent_category_selected_icons_match_account_dropdowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identifier: str
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.create_category("Food:Shops", "Shops")
        view.icons = store_at(tmp_path)
        await view.icons.assign(identifier, asset(identifier), ())
        view.category_parent.value = identifier
        view._refresh_category_parent_options()
        icon = view.category_parent.leading_icon
        assert isinstance(icon, ft.Container)
        assert icon.width == icon.height == 24
        assert isinstance(icon.content, ft.Image)
        assert icon.content.width == icon.content.height == 16
        for option in view.category_parent.options[1:]:
            labels = [
                control
                for control in walk(category_content(option))
                if isinstance(control, ft.Text)
            ]
            assert labels and all(label.size is None and label.weight is None for label in labels)

    asyncio.run(scenario())


def test_budget_definition_tree_and_filters_use_entity_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Expenses:Food", asset(), ())
        budget = Budget(
            "synthetic",
            "Expenses:Food",
            journal.today().replace(day=1),
            "monthly",
            Decimal(100),
            "UAH",
        )
        assert images(view.budgets._definition(budget))
        view.budgets.category_id = "Expenses:Food"
        view.budgets.category_choices = {"Expenses:Food": "Food"}
        view.budgets._sync_filters()
        assert isinstance(view.budgets.scope.leading_icon, ft.Container)
        assert images(view.budgets.scope.leading_icon)
        category_option = next(
            option for option in view.budgets.scope.options if option.key == "Expenses:Food"
        )
        assert isinstance(category_option.content, ft.Control)
        assert images(category_option.content)
        assert isinstance(view.budgets.active_filters.controls[0], ft.Chip)
        assert isinstance(view.budgets.active_filters.controls[0].leading, ft.Image)
        picker = TreePicker(
            session.page,
            "en",
            "Synthetic",
            {"Expenses:Food": "Food"},
            "",
            "All",
            AsyncMock(),
            icon=view._entity_icon,
        )
        assert isinstance(picker.listing.controls[0], ft.Container)
        button = picker.listing.controls[0].content
        assert isinstance(button, ft.TextButton) and isinstance(button.icon, ft.Image)

    asyncio.run(scenario())


@pytest.mark.parametrize("vector", [True, False])
def test_icons_render_at_16_pixels_without_changing_stored_artwork(vector: bool) -> None:
    original = (
        asset()
        if vector
        else IconAsset(
            "Imported",
            normalize_image(raster())[0],
            False,
            "User-provided",
            "Unverified",
            "Synthetic",
        )
    )
    serialized = original.serialize()
    image = icon_image(original)
    assert image.width == image.height == 16
    assert image.src == original.data
    assert image.fit == ft.BoxFit.CONTAIN
    assert image.color == (ft.Colors.ON_SURFACE if vector else None)
    assert original.serialize() == serialized


def test_icon_read_failure_does_not_disable_financial_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset(), ())
        monkeypatch.setattr(
            view.icons.files, "read_records", AsyncMock(side_effect=IconError("icon_storage"))
        )
        await view._load_icons()
        assert journal.ready
        assert view.icon_notice.visible
        assert view.icons.assets["Assets:Bank"] == asset()

    asyncio.run(scenario())


def test_busy_save_disables_escape_and_duplicate_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        async def persist(_asset: object) -> None:
            started.set()
            await release.wait()

        save = AsyncMock(side_effect=persist)
        picker = IconPicker(session.page, "en", asset(), IconifySearch(), save)
        show_dialog(session.page, picker)
        task = asyncio.create_task(click_async(picker.submit))
        try:
            async with asyncio.timeout(10):
                await started.wait()
                assert picker.cancel.disabled and picker.submit.disabled
                await press(session)
                assert picker.open
                await click_async(picker.submit)
                save.assert_awaited_once()
        finally:
            release.set()
            await task
        assert not picker.open

    asyncio.run(scenario())
