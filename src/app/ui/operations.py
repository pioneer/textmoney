"""A single bounded Operations page with quiet bidirectional navigation."""

import asyncio
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import flet as ft

from app.domain.ledger import LedgerError, Transaction
from app.domain.operations import (
    OperationPage,
    OperationScope,
    PageDirection,
    TransactionKey,
    matching_transactions,
    operation_page,
    transaction_key,
)
from app.localization import date_input, format_date, localized_error, parse_date_input, translate

if TYPE_CHECKING:
    from app.ui.finance import FinanceView


@dataclass(frozen=True)
class OperationsLocation:
    account: str = ""
    category: str = ""
    scope: OperationScope = OperationScope()
    origin: str = ""
    filters_visible: bool = True
    cursor: TransactionKey | None = None
    journal_id: int | None = None


class OperationsView:
    def __init__(self, host: "FinanceView") -> None:
        self.host = host
        self.page = OperationPage()
        self.key: tuple[object, ...] | None = None
        self.generation = 0
        self.busy = False
        self.failed_direction: PageDirection | None = None
        self.scope = OperationScope()
        self.origin = ""
        self._restore_cursor: TransactionKey | None = None
        self.date_language = host.language
        self.start = ft.TextField(value="", expand=True, on_change=self._dates_changed)
        self.end_date = ft.TextField(value="", expand=True, on_change=self._dates_changed)
        self.currency = ft.Dropdown(value="", expand=True, on_select=self._currency_changed)
        self.search = ft.TextField(value="", expand=True, on_change=self._search_changed)
        # Keep chip slots stable when this screen is remounted after navigation.
        self._filter_chips = {
            key: ft.Chip(
                key=f"operations-filter-{key}",
                label=ft.Text(max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                data=key,
                visible=False,
                on_delete=self._clear,
            )
            for key in ("account", "category", "start", "end", "currency", "query", "report")
        }
        self.chips = ft.Row(list(self._filter_chips.values()), wrap=True, spacing=8, run_spacing=4)
        self.reset = ft.TextButton(on_click=self._reset)
        self.summary_help = ft.Text(size=12)
        self.extra_filters = [
            ft.Row([self.start, self.end_date]),
            ft.Row([self.currency]),
            ft.Row([self.search]),
        ]
        self.history = ft.Column(spacing=0, key="operations-history")
        self.error = ft.Text(visible=False, color=ft.Colors.ERROR)
        self.retry = ft.TextButton(on_click=self._retry, visible=False)
        self.newer = [
            ft.TextButton(icon=ft.Icons.CHEVRON_LEFT, on_click=self._newer) for _ in range(2)
        ]
        self.older = [
            ft.TextButton(icon=ft.Icons.CHEVRON_RIGHT, on_click=self._older) for _ in range(2)
        ]
        self.latest = [ft.TextButton(on_click=self._latest) for _ in range(2)]
        self.end = ft.Text(size=12, color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
        self.loading = [
            ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False) for _ in range(2)
        ]
        self.navigation = [
            ft.Row(
                [self.newer[index], self.latest[index], self.older[index], self.loading[index]],
                alignment=ft.MainAxisAlignment.CENTER,
                wrap=True,
                spacing=0,
            )
            for index in range(2)
        ]
        self.status = ft.Row(
            [self.end, self.error, self.retry],
            alignment=ft.MainAxisAlignment.CENTER,
            wrap=True,
        )

    def _key(self) -> tuple[object, ...]:
        journal = self.host.journal
        return (
            id(journal),
            id(journal.ledger) if journal else None,
            bool(journal and journal.ready),
            self.host.language,
            self.host.icon_revision,
            self.host.operations_account.value,
            self.host.operations_category.value,
            self.scope,
            self.origin,
        )

    def sync(self) -> None:
        key = self._key()
        if key == self.key:
            return
        self._scope_controls()
        previous = self.key
        self.key = key
        self.generation += 1
        self.busy = False
        self.failed_direction = None
        self.error.visible = False
        journal = self.host.journal
        cursor = self._restore_cursor
        self._restore_cursor = None
        if journal is None or not journal.ready:
            self.page = OperationPage()
            self.history.controls = [ft.Text(self.host.tr("transactions_empty"))]
            self._labels()
            return
        account, category = self._filters()
        if (
            cursor is None
            and previous is not None
            and previous[0] == key[0]
            and previous[5:] == key[5:]
            and self.page.newer
            and self.page.transactions
        ):
            anchor = self.page.transactions[0]
            # Source IDs can change on reload; never use an old ID for row actions.
            current = next(
                (
                    entry
                    for entry in self.matching(journal.ledger.transactions)
                    if replace(entry, id=anchor.id) == anchor
                ),
                anchor,
            )
            cursor = transaction_key(current)
        page = operation_page(
            journal.ledger,
            account,
            category,
            scope=self.scope,
            cursor=cursor,
            direction="older",
            inclusive=True,
        )
        if cursor is not None and not page.transactions:
            page = operation_page(
                journal.ledger,
                account,
                category,
                scope=self.scope,
                cursor=cursor,
                direction="newer",
                inclusive=True,
            )
        self._render(page)

    def _filters(self) -> tuple[str, str]:
        return (
            str(self.host.operations_account.value or ""),
            str(self.host.operations_category.value or ""),
        )

    def location(self) -> OperationsLocation:
        cursor = (
            transaction_key(self.page.transactions[0])
            if self.key == self._key() and self.page.newer and self.page.transactions
            else None
        )
        return OperationsLocation(
            *self._filters(),
            scope=self.scope,
            origin=self.origin,
            filters_visible=self.host.operations_filters.visible,
            cursor=cursor,
            journal_id=id(self.host.journal) if cursor is not None else None,
        )

    def restore(self, location: OperationsLocation) -> None:
        self.host.operations_account.value = location.account
        self.host.operations_category.value = location.category
        self.host.operations_filters.visible = location.filters_visible
        self.set_scope(location.scope, origin=location.origin)
        self._restore_cursor = (
            location.cursor if location.journal_id == id(self.host.journal) else None
        )
        self.key = None

    def matching(self, transactions: Iterable[Transaction]) -> Iterator[Transaction]:
        journal = self.host.journal
        return matching_transactions(
            transactions,
            *self._filters(),
            scope=self.scope,
            categories=frozenset(item.id for item in journal.ledger.categories)
            if journal
            else frozenset(),
        )

    def set_scope(self, scope: OperationScope, *, origin: str = "") -> None:
        self.scope = scope
        self.origin = origin
        self.date_language = self.host.language
        self.start.value = date_input(scope.start, self.host.language) if scope.start else ""
        self.end_date.value = date_input(scope.end, self.host.language) if scope.end else ""
        self.start.error = self.end_date.error = None
        self.currency.value = scope.currency
        self.search.value = scope.query

    def _scope_controls(self) -> None:
        host, tr = self.host, self.host.tr
        if self.date_language != host.language:
            try:
                dates = [
                    parse_date_input(field.value, self.date_language) if field.value else None
                    for field in (self.start, self.end_date)
                ]
            except LedgerError:
                # Keep partial date drafts in the format in which they were entered.
                pass
            else:
                for field, value in zip((self.start, self.end_date), dates, strict=True):
                    field.value = date_input(value, host.language) if value else ""
                self.date_language = host.language
        self.start.label = tr("report_start_date")
        self.end_date.label = tr("report_end_date")
        self.start.hint_text = self.end_date.hint_text = translate(self.date_language, "date_hint")
        self.currency.label = tr("currency")
        journal = host.journal
        currencies = (
            {
                posting.currency
                for entry in journal.ledger.transactions
                for posting in entry.postings
            }
            if journal and journal.ready
            else set()
        )
        if self.scope.currency:
            currencies.add(self.scope.currency)
        self.currency.options = [
            ft.DropdownOption(key="", text=tr("budget_all_currencies")),
            *[ft.DropdownOption(key=value, text=value) for value in sorted(currencies)],
        ]
        self.search.label = tr("budget_search_transactions")
        account, category = self._filters()
        names = (
            {item.id: item.name for item in (*journal.ledger.accounts, *journal.ledger.categories)}
            if journal and journal.ready
            else {}
        )
        report_kind = tr(
            "report_income" if self.scope.report_root == "Income" else "report_expenses"
        )
        report_postings = tr(
            "report_uncategorized" if self.scope.unassigned else "operations_categorized"
        )
        labels = {
            "account": names.get(account, account),
            "category": names.get(category, category),
            "start": f"{tr('report_start_date')}: {format_date(self.scope.start, host.language)}"
            if self.scope.start
            else "",
            "end": f"{tr('report_end_date')}: {format_date(self.scope.end, host.language)}"
            if self.scope.end
            else "",
            "currency": self.scope.currency,
            "query": self.scope.query,
            "report": f"{tr('nav_reports')}: {report_kind} / {report_postings}"
            if self.scope.report_root
            else "",
        }
        for key, label in labels.items():
            chip = self._filter_chips[key]
            chip.visible = bool(label)
            assert isinstance(chip.label, ft.Text)
            chip.label.value = label
            chip.label.width = min(240, max(80, (host.body.width or 390) - 128))
            chip.tooltip = label
            chip.delete_icon_tooltip = tr("budget_clear_filter")
        self.chips.visible = any(labels.values())
        self.reset.content = tr("budget_reset_filters")
        self.reset.visible = self.chips.visible
        self.summary_help.value = tr("operations_summary_help")

    def _dates_changed(self, event: ft.Event[ft.TextField]) -> None:
        try:
            scope = replace(
                self.scope,
                start=parse_date_input(self.start.value, self.date_language)
                if self.start.value
                else None,
                end=parse_date_input(self.end_date.value, self.date_language)
                if self.end_date.value
                else None,
            )
        except LedgerError as exc:
            event.control.error = localized_error(self.host.language, str(exc))
            self.host.page.update()
            return
        self.start.error = self.end_date.error = None
        self.scope = scope
        self.host._refresh()
        self.host.page.update()

    def _currency_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self.scope = replace(self.scope, currency=str(self.currency.value or ""))
        self.host._refresh()
        self.host.page.update()

    def _search_changed(self, event: ft.Event[ft.TextField]) -> None:
        self.scope = replace(self.scope, query=self.search.value or "")
        self.host._refresh()
        self.host.page.update()

    def _clear(self, event: ft.Event[ft.Chip]) -> None:
        key = event.control.data
        if key == "account":
            self.host.operations_account.value = ""
        elif key == "category":
            self.host.operations_category.value = ""
            self.scope = replace(self.scope, report_root="", unassigned=False)
        elif key == "report":
            self.scope = replace(self.scope, report_root="", unassigned=False)
        elif key == "start":
            self.scope = replace(self.scope, start=None)
        elif key == "end":
            self.scope = replace(self.scope, end=None)
        elif key == "currency":
            self.scope = replace(self.scope, currency="")
        elif key == "query":
            self.scope = replace(self.scope, query="")
        self.set_scope(self.scope, origin=self.origin)
        self.host._refresh()
        self.host.page.update()

    def _reset(self, event: ft.Event[ft.TextButton]) -> None:
        self.host.operations_account.value = self.host.operations_category.value = ""
        self.set_scope(OperationScope(), origin=self.origin)
        self.host._refresh()
        self.host.page.update()

    def _labels(self) -> None:
        tr = self.host.tr
        for newer, older, latest in zip(self.newer, self.older, self.latest, strict=True):
            newer.content, older.content, latest.content = (
                tr("operations_newer"),
                tr("operations_older"),
                tr("operations_latest"),
            )
            newer.disabled = self.busy or not self.page.newer
            older.disabled = self.busy or not self.page.older
            latest.disabled = self.busy
            latest.visible = self.page.newer
        for navigation in self.navigation:
            navigation.visible = self.page.newer or self.page.older
        for loading in self.loading:
            loading.visible = self.busy
        self.end.value = tr("operations_end")
        self.end.visible = bool(self.page.transactions) and self.page.newer and not self.page.older
        self.retry.content = tr("operations_retry")
        self.retry.visible = self.failed_direction is not None and not self.busy

    def _render(self, page: OperationPage) -> None:
        journal = self.host.journal
        assert journal is not None and journal.ready
        names = {account.id: account.name for account in journal.ledger.accounts}
        names.update({category.id: category.name for category in journal.ledger.categories})
        controls = self.host._transaction_groups(
            list(page.transactions), names, daily_summary=True, daily_totals=dict(page.totals)
        )
        for index, control in enumerate(controls):
            if index >= len(self.history.controls):
                break
            previous = self.history.controls[index]
            if isinstance(control, ft.Container) and type(previous) is type(control):
                assert isinstance(previous, ft.Container)
                previous.content = control.content
                previous.data = control.data
                previous.padding = control.padding
                previous.alignment = control.alignment
                previous.border = control.border
                previous.theme = control.theme
                previous.dark_theme = control.dark_theme
                controls[index] = previous
        self.page = page
        self.history.controls = controls or [
            ft.Text(
                self.host.tr("operations_no_matches")
                if any(self._filters()) or self.scope != OperationScope()
                else self.host.tr("transactions_empty")
            )
        ]
        self._labels()
        self.host._update_transaction_separators()

    async def move(self, direction: PageDirection) -> None:
        if self.busy:
            return
        self.sync()
        journal = self.host.journal
        if journal is None or not journal.ready or not self.page.transactions:
            return
        if direction == "older" and not self.page.older:
            return
        if direction == "newer" and not self.page.newer:
            return
        generation = self.generation
        key = self._key()
        screen = self.host.current_screen
        cursor = (
            None
            if direction == "latest"
            else transaction_key(self.page.transactions[-1 if direction == "older" else 0])
        )
        self.busy = True
        self.error.visible = False
        self.failed_direction = None
        self._labels()
        self.host.page.update()
        try:
            page = await asyncio.to_thread(
                operation_page,
                journal.ledger,
                *self._filters(),
                scope=self.scope,
                cursor=cursor,
                direction=direction,
            )
            if (
                generation != self.generation
                or key != self._key()
                or screen != self.host.current_screen
            ):
                return
            self._render(page)
            self.host.page.update()
            try:
                await self.host.body.scroll_to(offset=0, duration=0)
            except Exception as exc:
                self.host._error(exc, notify=True)
        except Exception as exc:
            if generation == self.generation:
                self.error.value = self.host.tr("operations_load_failed")
                self.error.visible = True
                self.failed_direction = direction
                self.host._error(exc, notify=True)
        finally:
            if generation == self.generation:
                self.busy = False
                self._labels()
                self.host.page.update()

    async def _older(self, event: ft.Event[ft.TextButton]) -> None:
        await self.move("older")

    async def _newer(self, event: ft.Event[ft.TextButton]) -> None:
        await self.move("newer")

    async def _latest(self, event: ft.Event[ft.TextButton]) -> None:
        await self.move("latest")

    async def _retry(self, event: ft.Event[ft.TextButton]) -> None:
        if self.failed_direction is not None:
            await self.move(self.failed_direction)
