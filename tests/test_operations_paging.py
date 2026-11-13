import asyncio
import gc
import threading
import tracemalloc
import weakref
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click, click_async, transaction_form

from app.domain.ledger import Account, Category, Ledger, Posting, Transaction
from app.domain.operations import OperationPage, PageDirection, operation_page
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.ui import operations as operations_ui
from app.ui.finance import FinanceView


def operations(count: int, *, same_day: bool = False) -> tuple[Transaction, ...]:
    return tuple(
        Transaction(
            f"operation-{index:06}",
            date(2026, 1, 1) if same_day else date(2026, 1, 1) + timedelta(days=index // 3),
            "2026-01-01T12:00:00+00:00",
            "",
            f"Synthetic operation {index:06}",
            (
                Posting("Expenses:Food", Decimal("1"), "USD"),
                Posting("Assets:Cash", Decimal("-1"), "USD"),
            ),
            "expense",
        )
        for index in range(count)
    )


@pytest.fixture
def view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FinanceView:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    host = FinanceView()
    host.startup_complete = True
    host.body.scroll_to = AsyncMock()
    host.journal = Journal(DesktopFolder(str(tmp_path), FILENAME), today=lambda: date(2028, 1, 1))
    host.journal.ledger = Ledger(
        accounts=(Account("Assets:Cash", "Cash", "USD", date(2000, 1, 1)),),
        categories=(Category("Expenses:Food", "Food", date(2000, 1, 1)),),
        transactions=operations(1000),
    )
    host.journal.ready = True
    return host


def test_initial_operations_render_is_bounded(view: FinanceView) -> None:
    rendered = MagicMock(wraps=view._transaction_card)
    view._transaction_card = rendered
    view._refresh()
    assert rendered.call_count <= 55  # One page plus Home's five recent operations.


def test_browse_all_history_and_back_without_retaining_pages(view: FinanceView) -> None:
    async def scenario() -> None:
        view._refresh()
        listing = view.operations
        seen: list[str] = []
        pages = 0
        while True:
            entries = listing.page.transactions
            assert len(entries) <= 50
            seen.extend(item.id for item in entries)
            pages += 1
            if not listing.page.older:
                break
            await listing.move("older")
        assert len(seen) == len(set(seen)) == 1000
        assert pages == 20
        while listing.page.newer:
            await listing.move("newer")
        assert listing.page.transactions[0].id == "operation-000999"

    asyncio.run(scenario())


@pytest.mark.parametrize("direction", ["older", "newer", "latest"])
def test_explicit_paging_scrolls_to_the_full_page_top(
    view: FinanceView, direction: PageDirection
) -> None:
    async def scenario() -> None:
        view._refresh()
        if direction != "older":
            await view.operations.move("older")
        scroll = view.body.scroll_to
        assert isinstance(scroll, AsyncMock)
        scroll.reset_mock()
        await view.operations.move(direction)
        scroll.assert_awaited_once_with(offset=0, duration=0)
        scroll.reset_mock()
        view._refresh()
        scroll.assert_not_awaited()

    asyncio.run(scenario())


def test_busy_day_has_complete_totals_on_every_page(view: FinanceView) -> None:
    async def scenario() -> None:
        assert view.journal is not None
        view.journal.ledger = replace(
            view.journal.ledger, transactions=operations(137, same_day=True)
        )
        view._refresh()
        listing = view.operations
        for _ in range(3):
            assert len(listing.page.transactions) <= 50
            assert listing.page.totals[0][1][0].expenses == Decimal("137")
            if listing.page.older:
                await listing.move("older")

    asyncio.run(scenario())


def test_paging_resets_the_summary_theme_when_a_slot_becomes_a_date(view: FinanceView) -> None:
    async def scenario() -> None:
        view._refresh()
        old = {id(control): control.data for control in view.history.controls}
        await view.operations.move("older")
        recycled_dates = [
            control
            for control in view.history.controls
            if isinstance(control, ft.Container)
            and control.data == "transaction-date"
            and id(control) in old
            and old[id(control)] != "transaction-date"
        ]
        assert recycled_dates
        assert all(
            control.theme is None and control.dark_theme is None for control in recycled_dates
        )
        for control in view.history.controls:
            if isinstance(control, ft.Container) and isinstance(control.content, ft.Column):
                assert control.theme is not None and control.dark_theme is not None

    asyncio.run(scenario())


def test_filters_query_the_whole_history_and_keep_date_totals(view: FinanceView) -> None:
    assert view.journal is not None
    ledger = view.journal.ledger
    entries = list(ledger.transactions)
    entries[5] = replace(
        entries[5],
        postings=(
            Posting("Expenses:Food:Meals", Decimal("7"), "USD"),
            Posting("Assets:Bank", Decimal("-7"), "USD"),
        ),
    )
    entries[6] = replace(
        entries[6],
        postings=(
            Posting("Expenses:FoodOther", Decimal("99"), "USD"),
            Posting("Assets:Bank", Decimal("-99"), "USD"),
        ),
    )
    ledger = replace(ledger, transactions=tuple(entries))
    selected = operation_page(ledger, "Assets:Bank", "Expenses:Food")
    assert [item.id for item in selected.transactions] == ["operation-000005"]
    assert selected.totals[0][1][0].expenses == Decimal("7")
    assert not selected.older and not selected.newer


def test_reload_reanchors_without_reusing_old_source_ids(view: FinanceView) -> None:
    async def scenario() -> None:
        assert view.journal is not None
        view._refresh()
        listing = view.operations
        await listing.move("older")
        anchor = listing.page.transactions[0]
        before = tuple(view.history.controls)
        view._refresh()
        assert tuple(view.history.controls) == before
        view.journal.ledger = replace(
            view.journal.ledger,
            transactions=tuple(
                replace(entry, id=f"new-{entry.id}") for entry in view.journal.ledger.transactions
            ),
        )
        view._refresh()
        assert listing.page.transactions[0].narration == anchor.narration
        assert all(item.id.startswith("new-") for item in listing.page.transactions)
        assert view.history.controls[0] is before[0]
        view.operations_category.value = "Expenses:Food"
        view._operations_filter_changed(MagicMock())
        assert not listing.page.newer
        assert listing.page.transactions[0].id == "new-operation-000999"
        view.journal.ready = False
        view._refresh()
        assert not listing.page.transactions
        assert not any(item.visible for item in listing.navigation)

    asyncio.run(scenario())


def test_page_failure_retains_rows_and_can_be_retried(
    view: FinanceView, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        assert view.journal is not None
        view._refresh()
        listing = view.operations
        original = listing.page
        query = MagicMock(side_effect=RuntimeError("Synthetic paging failure"))
        monkeypatch.setattr(operations_ui, "operation_page", query)
        await listing.move("older")
        assert listing.page is original
        assert listing.error.visible and listing.retry.visible
        assert not listing.busy
        query.side_effect = None
        query.return_value = operation_page(
            view.journal.ledger,
            cursor=operations_ui.transaction_key(original.transactions[-1]),
            direction="older",
        )
        await listing._retry(MagicMock())
        assert listing.page is not original
        assert not listing.error.visible and not listing.retry.visible

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["filter", "screen"])
def test_pending_page_cannot_replace_changed_context(
    view: FinanceView, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async def scenario() -> None:
        assert view.journal is not None
        view._refresh()
        started, release = threading.Event(), threading.Event()
        ledger = view.journal.ledger

        def pending(*args: object, **kwargs: object) -> OperationPage:
            started.set()
            assert release.wait(timeout=5)
            return operation_page(ledger)

        monkeypatch.setattr(operations_ui, "operation_page", pending)
        worker = asyncio.create_task(view.operations.move("older"))
        expected = view.operations.page
        try:
            assert await asyncio.to_thread(started.wait, 2)
            monkeypatch.setattr(operations_ui, "operation_page", operation_page)
            if change == "filter":
                view.operations_category.value = "Expenses:Food"
                view._operations_filter_changed(MagicMock())
            else:
                view._navigate("accounts")
            expected = view.operations.page
        finally:
            release.set()
            await worker
        assert view.operations.page is expected
        assert not view.operations.busy
        assert not view.operations.page.newer

    asyncio.run(scenario())


def test_scroll_failure_does_not_claim_the_previous_page_was_retained(view: FinanceView) -> None:
    async def scenario() -> None:
        view._refresh()
        original = view.operations.page
        view.body.scroll_to = AsyncMock(side_effect=RuntimeError("Synthetic scroll failure"))
        errors = MagicMock(wraps=view._error)
        view._error = errors
        await view.operations.move("older")
        assert view.operations.page is not original
        assert not view.operations.error.visible
        assert not view.operations.retry.visible
        errors.assert_called_once()
        assert errors.call_args.kwargs["notify"]

    asyncio.run(scenario())


def test_repeated_paging_releases_controls_and_bounds_python_memory(view: FinanceView) -> None:
    async def scenario() -> None:
        page = view.page
        scroll = view.body.scroll_to
        assert isinstance(page, MagicMock) and isinstance(scroll, AsyncMock)
        # Allocate the ledger before capture; measure the page's additional memory.
        await asyncio.to_thread(lambda: None)
        gc.collect()
        tracemalloc.start()
        try:
            view.operations.sync()
            last = view.history.controls[-1]
            assert isinstance(last, ft.Container) and isinstance(last.content, ft.Control)
            old = weakref.ref(last.content)
            del last
            for _ in range(3):
                for _ in range(19):
                    await view.operations.move("older")
                    page.reset_mock()
                    scroll.reset_mock()
                await view.operations.move("latest")
                page.reset_mock()
                scroll.reset_mock()
            gc.collect()
            retained, peak = tracemalloc.get_traced_memory()
            assert old() is None
            assert retained < 2 * 1024 * 1024
            # Flet's discarded control cycles can await a later GC generation.
            assert peak < 12 * 1024 * 1024
            assert len(view.operations.page.transactions) <= 50
        finally:
            tracemalloc.stop()

    asyncio.run(scenario())


def test_paged_row_actions_use_current_source_after_external_reload(
    view: FinanceView, tmp_path: Path
) -> None:
    async def scenario() -> None:
        assert view.journal is not None
        journal = view.journal
        page = view.page
        assert isinstance(page, MagicMock)
        source = """2000-01-01 open Assets:Cash USD
2000-01-01 open Expenses:Food
2000-01-01 open Equity:Opening-Balances
2000-01-01 * "Opening"
    Assets:Cash 1000 USD
    Equity:Opening-Balances -1000 USD
"""
        source += "".join(
            f'2026-01-01 * "Synthetic operation {index}"\n'
            "    Expenses:Food 1 USD\n"
            "    Assets:Cash -1 USD\n"
            for index in range(110)
        )
        path = tmp_path / FILENAME
        path.write_text(source, encoding="utf-8")
        await journal.load()
        view._refresh()
        await view.operations.move("older")
        anchor = view.operations.page.transactions[0]
        path.write_text("; External comment\n" + source, encoding="utf-8")
        view.next_icon_poll = float("inf")
        await view._poll_journal()
        current = view.operations.page.transactions[0]
        assert current.narration == anchor.narration and current.id != anchor.id
        card = next(
            control
            for control in view.history.controls
            if isinstance(control, ft.Container)
            and isinstance(control.content, ft.Row)
            and control.data != "transaction-date"
        )
        assert isinstance(card.content, ft.Row)
        edit = next(
            control
            for control in card.content.controls
            if isinstance(control, ft.IconButton) and control.data == current.id
        )
        errors = MagicMock(wraps=view._error)
        view._error = errors
        view._edit_transaction(MagicMock(control=edit))
        assert not errors.called, errors.call_args
        dialog = page.show_dialog.call_args.args[0]
        assert isinstance(dialog, ft.AlertDialog)
        assert transaction_form(dialog).description.value == current.narration
        assert isinstance(dialog.actions[0], ft.TextButton)
        click(dialog.actions[0])
        delete = next(
            control
            for control in card.content.controls
            if isinstance(control, ft.IconButton) and control.tooltip == view.tr("crud_delete")
        )
        view._delete_transaction(MagicMock(control=delete))
        confirmation = page.show_dialog.call_args.args[0]
        assert isinstance(confirmation, ft.AlertDialog)
        confirm = confirmation.actions[-1]
        assert isinstance(confirm, ft.TextButton)
        await click_async(confirm)
        assert all(entry.narration != current.narration for entry in journal.ledger.transactions)
        assert len(journal.ledger.transactions) == 110
        assert len(view.operations.page.transactions) <= 50

    asyncio.run(scenario())
