"""Budget controls; all arithmetic and journal mutation stay outside the UI."""

import asyncio
from concurrent.futures import Future
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import flet as ft

from app.domain.budgets import (
    INTERVALS,
    Budget,
    BudgetFilter,
    BudgetRow,
    interval_next,
    rows,
)
from app.domain.ledger import LedgerError
from app.localization import date_input, format_budget_number, format_date, localized_error
from app.ui.datetime_fields import DateTimeFields
from app.ui.dialogs import show_dialog
from app.ui.tree_picker import TreePicker

if TYPE_CHECKING:
    from app.ui.finance import FinanceView


class BudgetView(ft.Column):
    def __init__(self, host: "FinanceView") -> None:
        super().__init__(spacing=12)
        self.host = host
        self.worker: Future[None] | None = None
        self.generation = 0
        self.cache_key: tuple[object, ...] | None = None
        self.model_key: tuple[object, ...] | None = None
        self.filter_key: tuple[object, ...] | None = None
        self.expanded: dict[tuple[date, str], bool] = {}
        self.highlighted: tuple[date, str] | None = None
        self.reveal_requested = False
        self.scroll_target: ft.ExpansionTile | None = None
        self.month = host._today().replace(day=1)
        self.category_id = ""
        self.account_id = ""
        self.category_choices: dict[str, str] = {}
        self.account_choices: dict[str, str] = {}
        self.report_rows: tuple[BudgetRow, ...] = ()
        self.period = ft.TextButton(expand=True, on_click=self._pick_month)
        self.current = ft.TextButton(on_click=self._current_month)
        self.scope = ft.Button(on_click=self._pick_category)
        self.account = ft.Button(on_click=self._pick_account)
        self.currency = ft.Dropdown(value="", width=220, on_select=self._changed, visible=False)
        self.overspent = ft.Checkbox(value=False, on_change=self._changed)
        self.reset = ft.TextButton(on_click=self._reset)
        self.active_filters = ft.Row(wrap=True)
        self.account_warning = ft.Text(size=12, visible=False)
        self.error = ft.Text(color=ft.Colors.ERROR, visible=False, selectable=True)
        self.loading = ft.ProgressRing(visible=False)
        self.results = ft.Column(spacing=12)
        self.definitions = ft.Column(spacing=8)
        self.add = ft.Button(icon=ft.Icons.ADD, on_click=self._new)
        self.previous = ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, on_click=self._previous)
        self.next = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, on_click=self._next)
        self.filters_tile = ft.ExpansionTile(
            title=host.tr("budget_simple_filters"),
            controls=[
                ft.Column(
                    [
                        self.account,
                    ],
                    spacing=16,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ],
            controls_padding=ft.Padding.only(top=8),
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
            maintain_state=True,
        )
        self.history_help = ft.Text(size=12)
        self.management = ft.Column(
            [
                ft.Row([self.add], alignment=ft.MainAxisAlignment.END, wrap=True),
                self.history_help,
                self.definitions,
            ],
            spacing=16,
        )
        self.controls = [
            ft.Row([self.previous, self.period, self.next]),
            ft.Row([self.current], alignment=ft.MainAxisAlignment.CENTER),
            self.scope,
            self.currency,
            self.overspent,
            self.active_filters,
            self.filters_tile,
            self.account_warning,
            self.reset,
            self.error,
            self.loading,
            self.results,
        ]

    def sync(self) -> None:
        tr = self.host.tr
        journal = self.host.journal
        ready = bool(journal and journal.ready)
        self.add.content = tr("create_budget")
        self.add.disabled = not ready
        self.previous.tooltip = tr("budget_previous")
        self.next.tooltip = tr("budget_next")
        self.period.content = f"{tr(f'date_month_{self.month.month:02}')} {self.month.year}"
        self.period.tooltip = tr("budget_choose_month")
        self.previous.disabled = self.month == date(1, 1, 1)
        self.next.disabled = self.month == date(9998, 12, 1)
        self.current.content = tr("report_current_month")
        self.currency.label = tr("currency")
        self.overspent.label = tr("budget_only_overspent")
        self.reset.content = tr("budget_reset_filters")
        self.account_warning.value = tr("budget_account_warning")
        self.account_warning.visible = bool(self.account_id)
        self.history_help.value = tr("budget_history_help")
        self.filters_tile.title = tr("budget_simple_filters")
        model_key = (
            id(journal),
            id(journal.ledger) if journal else None,
            ready,
            self.host.language,
        )
        if model_key != self.model_key:
            self.model_key = model_key
            if journal and journal.ready:
                self.account_choices = self._tree_items(
                    {item.id for item in journal.ledger.accounts}
                )
                self.definitions.controls = [
                    self._definition(budget)
                    for budget in sorted(journal.ledger.budgets, key=lambda b: (b.account, b.date))
                ] or [ft.Text(tr("budget_empty"))]
            else:
                self.account_choices = {}
                self.category_choices = {}
                self.definitions.controls = [ft.Text(tr("report_unavailable"))]
        self.scope.disabled = self.account.disabled = not ready
        self._sync_filters()

    def _sync_filters(self) -> None:
        tr = self.host.tr
        category = self.category_choices.get(self.category_id, self.category_id)
        account = self.account_choices.get(self.account_id, self.account_id)
        self.scope.content = f"{tr('budget_category')}: {category or tr('budget_all')}"
        self.account.content = f"{tr('budget_account')}: {account or tr('budget_all_accounts')}"
        self.scope.tooltip = self.category_id or None
        self.account.tooltip = self.account_id or None
        filter_key = (
            self.host.language,
            self.category_id,
            category,
            self.account_id,
            account,
            self.currency.value,
            self.overspent.value,
            self.host.body.width,
        )
        if self.filter_key == filter_key:
            return
        self.filter_key = filter_key
        chips: list[ft.Control] = []
        for key, label in (
            ("category", f"{tr('budget_category')}: {category}" if category else ""),
            ("account", f"{tr('budget_account')}: {account}" if account else ""),
            ("currency", str(self.currency.value or "")),
        ):
            if label:
                chips.append(
                    ft.Chip(
                        label=ft.Text(
                            label,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            width=min(240, max(80, (self.host.body.width or 390) - 128)),
                        ),
                        tooltip=label,
                        data=key,
                        on_click=self._clear_filter,
                        on_delete=self._clear_filter,
                        delete_icon_tooltip=tr("budget_clear_filter"),
                    )
                )
        self.active_filters.controls = chips
        self.active_filters.visible = bool(chips)
        self.reset.visible = bool(chips) or bool(self.overspent.value)
        self.account_warning.visible = bool(self.account_id)

    def _tree_items(self, identifiers: set[str]) -> dict[str, str]:
        names = self._account_names()
        items: dict[str, str] = {}
        for identifier in identifiers:
            parts = identifier.split(":")
            for length in range(1, len(parts) + 1):
                key = ":".join(parts[:length])
                items[key] = names.get(key, parts[length - 1])
        return items

    def _pick_category(self, event: ft.Event[ft.Button]) -> None:
        show_dialog(
            self.host.page,
            TreePicker(
                self.host.page,
                self.host.language,
                self.host.tr("budget_category"),
                self.category_choices,
                self.category_id,
                self.host.tr("budget_all"),
                self._select_category,
            ),
        )

    def _pick_account(self, event: ft.Event[ft.Button]) -> None:
        show_dialog(
            self.host.page,
            TreePicker(
                self.host.page,
                self.host.language,
                self.host.tr("budget_account"),
                self.account_choices,
                self.account_id,
                self.host.tr("budget_all_accounts"),
                self._select_account,
            ),
        )

    async def _select_category(self, identifier: str) -> None:
        self.category_id = identifier
        await self._refresh_filters()

    async def _select_account(self, identifier: str) -> None:
        self.account_id = identifier
        await self._refresh_filters()

    async def _clear_filter(self, event: ft.Event[ft.Chip]) -> None:
        key = event.control.data
        if key == "category":
            self.category_id = ""
        elif key == "account":
            self.account_id = ""
        elif key == "currency":
            self.currency.value = ""
        await self._refresh_filters()

    async def _reset(self, event: ft.Event[ft.TextButton]) -> None:
        self.category_id = self.account_id = ""
        self.currency.value = ""
        self.overspent.value = False
        await self._refresh_filters()

    async def _refresh_filters(self) -> None:
        self.highlighted = None
        self.reveal_requested = False
        self.scroll_target = None
        self.cancel()
        await self.refresh()

    def request_refresh(self) -> None:
        key = self._key()
        if key == self.cache_key:
            return
        self.cancel()
        self.worker = self.host.page.run_task(self.refresh)

    def cancel(self) -> None:
        self.generation += 1
        if self.worker is not None:
            self.worker.cancel()
            self.worker = None

    def _key(self) -> tuple[object, ...]:
        journal = self.host.journal
        return (
            id(journal),
            id(journal.ledger) if journal else None,
            journal.ready if journal else False,
            self.host.language,
            self.month,
            self.account_id,
            self.category_id,
            self.currency.value,
            self.overspent.value,
        )

    async def refresh(self) -> None:
        self.generation += 1
        generation = self.generation
        self.scroll_target = None
        self.sync()
        key = self._key()
        self.cache_key = key
        self.error.visible = False
        journal = self.host.journal
        if journal is None or not journal.ready:
            self.reveal_requested = False
            self.report_rows = ()
            self.currency.visible = False
            self.results.controls = [ft.Text(self.host.tr("report_unavailable"))]
            self.loading.visible = False
            self.host.page.update()
            return
        self.loading.visible = True
        self.results.controls = []
        self.host.page.update()
        try:
            begin = self.month
            end = interval_next(begin, "monthly")
            filters = BudgetFilter(account=self.account_id, account_tree=True)
            ledger = journal.ledger
            results = await asyncio.to_thread(
                rows,
                ledger,
                begin,
                end,
                "monthly",
                tree=True,
                filters=filters,
            )
            if generation != self.generation or self._key() != key:
                return
            self.report_rows = tuple(row for row in results if row.direct_budget)
            self.category_choices = self._tree_items({row.account for row in self.report_rows})
            currencies = sorted(
                {
                    currency
                    for row in self.report_rows
                    for currency in row.budget.keys() | row.actual.keys()
                }
            )
            selected = str(self.currency.value or "")
            options = sorted(set(currencies) | ({selected} if selected else set()))
            self.currency.options = [
                ft.DropdownOption(key="", text=self.host.tr("budget_all_currencies")),
                *[ft.DropdownOption(key=currency, text=currency) for currency in options],
            ]
            self.currency.visible = len(currencies) > 1 or bool(selected)
            self._sync_filters()
            self._render(self.report_rows)
        except LedgerError as exc:
            self.reveal_requested = False
            self.error.value = localized_error(self.host.language, str(exc))
            self.error.visible = True
        except Exception as exc:
            self.reveal_requested = False
            self.host._error(exc, notify=True)
            self.cache_key = None
        finally:
            if generation == self.generation:
                self.loading.visible = False
                self.host.page.update()

    async def _changed(self, event: ft.Event[ft.Dropdown] | ft.Event[ft.Checkbox]) -> None:
        await self._refresh_filters()

    async def _month(self, offset: int) -> None:
        try:
            index = self.month.year * 12 + self.month.month - 1 + offset
            chosen = date(index // 12, index % 12 + 1, 1)
            if chosen.year > 9998:
                raise ValueError
            self.month = chosen
        except ValueError:
            self.error.value = self.host.tr("budget_month_error")
            self.error.visible = True
            self.host.page.update()
            return
        await self._refresh_filters()

    async def _current_month(self, event: ft.Event[ft.TextButton]) -> None:
        self.month = self.host._today().replace(day=1)
        await self._refresh_filters()

    def _pick_month(self, event: ft.Event[ft.TextButton]) -> None:
        tr = self.host.tr
        month = ft.Dropdown(
            label=tr("budget_month"),
            value=str(self.month.month),
            options=[
                ft.DropdownOption(key=str(number), text=tr(f"date_month_{number:02}"))
                for number in range(1, 13)
            ],
        )
        year = ft.TextField(label=tr("budget_year"), value=str(self.month.year))
        error = ft.Text(color=ft.Colors.ERROR, visible=False)

        async def select(event: ft.Event[ft.TextButton]) -> None:
            try:
                value = int(year.value or "")
                if not 1 <= value <= 9998:
                    raise ValueError
                chosen = date(value, int(month.value or ""), 1)
            except ValueError:
                error.value = tr("budget_month_error")
                error.visible = True
                self.host.page.update()
                return
            self.month = chosen
            self.host.page.pop_dialog()
            await self._refresh_filters()

        show_dialog(
            self.host.page,
            ft.AlertDialog(
                title=tr("budget_choose_month"),
                scrollable=True,
                content=ft.Column([month, year, error], width=320, tight=True, spacing=16),
                actions=[
                    ft.TextButton(tr("crud_cancel"), on_click=self.host._dismiss),
                    ft.TextButton(tr("budget_show_month"), on_click=select),
                ],
            ),
        )

    async def _previous(self, event: ft.Event[ft.IconButton]) -> None:
        await self._month(-1)

    async def _next(self, event: ft.Event[ft.IconButton]) -> None:
        await self._month(1)

    def _number(self, value: Decimal, currency: str) -> str:
        journal = self.host.journal
        assert journal is not None
        ledger = journal.ledger
        precision = dict(ledger.currency_precisions).get(currency, 2)
        return f"{format_budget_number(value, precision, ledger.budget_locale)} {currency}"

    def _figures(self, row: BudgetRow, *, direct: bool = False) -> list[ft.Control]:
        tr = self.host.tr
        budget = row.direct_budget if direct else row.budget
        actual = row.direct_actual if direct else row.actual
        controls: list[ft.Control] = []
        for currency in sorted(budget.keys() | actual.keys()):
            if self.currency.value and currency != self.currency.value:
                continue
            remaining = row.remaining(currency, direct=direct)
            consumed = row.consumed(currency, direct=direct)
            overspent = (
                remaining is not None and remaining < 0 and row.account.startswith("Expenses")
            )
            planned_text = (
                self._number(budget[currency], currency)
                if currency in budget
                else tr("budget_missing")
            )
            actual_text = self._number(actual.get(currency, Decimal(0)), currency)
            remaining_text = (
                self._number(
                    Decimal.from_float(
                        float(budget[currency]) - float(actual.get(currency, Decimal(0)))
                    ),
                    currency,
                )
                if remaining is not None
                else tr("budget_missing")
            )
            lines: list[ft.Control] = [
                ft.Text(
                    f"{tr('budget_planned')}: {planned_text}",
                    tooltip=str(budget.get(currency, "")),
                    selectable=True,
                ),
                ft.Text(
                    f"{tr('budget_actual')}: {actual_text}",
                    tooltip=str(actual.get(currency, Decimal(0))),
                    selectable=True,
                ),
                ft.Text(
                    f"{tr('budget_remaining')}: {remaining_text}",
                    color=ft.Colors.ERROR if overspent else None,
                    tooltip=str(remaining) if remaining is not None else None,
                    selectable=True,
                ),
            ]
            if consumed is not None:
                lines.extend(
                    [
                        ft.Text(f"{tr('budget_consumed')}: {format_budget_number(consumed, 2)}%"),
                        ft.ProgressBar(
                            value=float(max(Decimal(0), min(Decimal(100), consumed))) / 100,
                            color=ft.Colors.ERROR if overspent else None,
                        ),
                    ]
                )
            elif currency in budget:
                lines.append(ft.Text(tr("budget_zero_progress"), size=12))
            if overspent:
                assert remaining is not None
                lines.append(
                    ft.Text(
                        f"{tr('budget_overspent')}: {self._number(remaining.copy_abs(), currency)}",
                        color=ft.Colors.ERROR,
                        selectable=True,
                    )
                )
            controls.append(ft.Column(lines, spacing=4))
        return controls or [ft.Text(tr("budget_missing"))]

    def _account_names(self) -> dict[str, str]:
        journal = self.host._require_journal()
        names = {item.id: item.name for item in journal.ledger.accounts}
        names.update({item.id: item.name for item in journal.ledger.categories})
        names.update(
            {
                "Expenses": self.host.tr("categories_expenses"),
                "Income": self.host.tr("categories_income"),
                "Net Profit": self.host.tr("budget_net_profit"),
                "Assets": self.host.tr("transfer_asset"),
                "Liabilities": self.host.tr("transfer_liability"),
            }
        )
        return names

    def _render(self, results: tuple[BudgetRow, ...]) -> None:
        def shown(row: BudgetRow) -> bool:
            if not row.direct_budget:
                return False
            if self.category_id and not (
                row.account == self.category_id or row.account.startswith(self.category_id + ":")
            ):
                return False
            currencies = row.budget.keys() | row.actual.keys()
            if self.currency.value:
                currencies &= {str(self.currency.value)}
            if not currencies:
                return False
            return (
                not self.overspent.value
                or (row.account.startswith("Expenses:") or row.account == "Expenses")
                and any(
                    (remaining := row.remaining(currency)) is not None and remaining < 0
                    for currency in currencies
                )
            )

        results = tuple(row for row in results if shown(row))
        tr = self.host.tr
        names = self._account_names()
        periods = dict.fromkeys((row.begin, row.end) for row in results)
        controls: list[ft.Control] = []
        for begin, _end in periods:
            by_account = {row.account: row for row in results if row.begin == begin}

            def node(
                account: str, *, by_account: dict[str, BudgetRow] = by_account, begin: date = begin
            ) -> ft.Control:
                row = by_account[account]
                children = [
                    identifier
                    for identifier in by_account
                    if identifier.rpartition(":")[0] == account
                ]
                key = (begin, account)
                detail: list[ft.Control] = [
                    *(
                        [ft.Text(tr("budget_direct"), size=12), *self._figures(row, direct=True)]
                        if row.direct_budget != row.budget or row.direct_actual != row.actual
                        else []
                    ),
                    ft.TextButton(tr("budget_transactions"), data=row, on_click=self._transactions),
                    *[node(child) for child in children],
                ]

                def changed(event: ft.Event[ft.ExpansionTile]) -> None:
                    self.expanded[key] = event.data is True

                budget = self._active_budget(account, row.end)
                actions = (
                    [
                        ft.IconButton(
                            icon=ft.Icons.EDIT_OUTLINED,
                            tooltip=tr("crud_edit"),
                            icon_size=20,
                            on_click=lambda _, current=budget: self._form(current),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip=tr("crud_delete"),
                            icon_size=20,
                            on_click=lambda _, current=budget: self._delete(current),
                        ),
                    ]
                    if budget is not None
                    else []
                )

                highlighted = self.highlighted == key
                tile = ft.ExpansionTile(
                    key=ft.ScrollKey(f"budget-report:{begin.isoformat()}:{account}"),
                    title=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        names.get(account, account.rsplit(":", 1)[-1]),
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                        tooltip=account,
                                        expand=True,
                                    ),
                                    *actions,
                                ],
                                spacing=0,
                            ),
                            *(
                                [ft.Text(tr("budget_subtree"), size=12)]
                                if row.direct_budget != row.budget
                                or row.direct_actual != row.actual
                                else []
                            ),
                            *self._figures(row),
                        ],
                        spacing=4,
                    ),
                    data=row,
                    expanded=self.expanded.get(key, account in {"Expenses", "Income"}),
                    on_change=changed,
                    controls=detail,
                    expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls_padding=12,
                    maintain_state=True,
                    bgcolor=ft.Colors.SECONDARY_CONTAINER if highlighted else None,
                    collapsed_bgcolor=ft.Colors.SECONDARY_CONTAINER if highlighted else None,
                )
                if highlighted and self.reveal_requested:
                    # The client must lay out the target before scroll_to can find its key.
                    tile.on_size_change = self._reveal_report
                    self.scroll_target = tile
                return tile

            roots = [
                account for account in by_account if account.rpartition(":")[0] not in by_account
            ]
            controls.extend(node(root) for root in roots)
        self.results.controls = controls or [ft.Text(tr("budget_report_empty"))]

    def _show_report(self, event: ft.Event[ft.TextButton]) -> None:
        budget = event.control.data
        assert isinstance(budget, Budget)
        journal = self.host.journal
        if journal is None or not journal.ready or budget not in journal.ledger.budgets:
            self.host._error(LedgerError("crud_stale"), notify=True)
            return
        self.cancel()
        self.month = budget.date.replace(day=1)
        self.category_id = self.account_id = ""
        self.currency.value = budget.currency
        self.overspent.value = False
        self.highlighted = (self.month, budget.account)
        self.reveal_requested = True
        parts = budget.account.split(":")
        for length in range(1, len(parts)):
            self.expanded[(self.month, ":".join(parts[:length]))] = True
        self.cache_key = None
        self.host.report_tabs.selected_index = 1
        self.host.report_overview.visible = False
        self.visible = True
        self.host._navigate("reports")

    async def _reveal_report(self, event: ft.LayoutSizeChangeEvent[ft.LayoutControl]) -> None:
        if (
            event.control is not self.scroll_target
            or not self.reveal_requested
            or self.host.current_screen != "reports"
            or self.host.report_tabs.selected_index != 1
        ):
            return
        self.reveal_requested = False
        self.scroll_target = None
        try:
            await self.host.body.scroll_to(scroll_key=event.control.key, duration=350)
        except Exception as exc:
            self.host._error(exc, notify=True)
            self.host.page.update()

    def _active_budget(self, account: str, end: date) -> Budget | None:
        journal = self.host.journal
        if journal is None or not journal.ready:
            return None
        candidates = [
            (index, budget)
            for index, budget in enumerate(journal.ledger.budgets)
            if budget.account == account
            and budget.date < end
            and (not self.currency.value or budget.currency == self.currency.value)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[1].date, item[0]))[1]

    def _transactions(self, event: ft.Event[ft.TextButton]) -> None:
        row = event.control.data
        assert isinstance(row, BudgetRow)
        names = self._account_names()
        listing = ft.Column()
        search = ft.TextField(label=self.host.tr("budget_search_transactions"))

        def render(event: ft.Event[ft.TextField] | None = None) -> None:
            query = (search.value or "").strip().casefold()
            listing.controls = [
                ft.ExpansionTile(
                    title=(
                        f"{format_date(transaction.date, self.host.language)} · {transaction.payee}"
                    ),
                    subtitle=transaction.narration,
                    controls=[
                        ft.Text(
                            f"{names.get(posting.account, posting.account)}"
                            f"  {posting.amount} {posting.currency}",
                            tooltip=posting.account,
                            selectable=True,
                        )
                        for posting in transaction.postings
                    ],
                    expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
                )
                for transaction in row.transactions
                if (not query or query in f"{transaction.payee} {transaction.narration}".casefold())
                and (
                    not self.currency.value
                    or any(
                        posting.currency == self.currency.value
                        and (
                            posting.account == row.account
                            or posting.account.startswith(row.account + ":")
                        )
                        for posting in transaction.postings
                    )
                )
            ] or [ft.Text(self.host.tr("budget_no_matches"))]
            if event is not None:
                self.host.page.update()

        search.on_change = render
        render()
        show_dialog(
            self.host.page,
            ft.AlertDialog(
                title=self.host.tr("budget_transactions"),
                scrollable=True,
                content=ft.Column(
                    [
                        ft.Text(
                            names.get(row.account, row.account),
                            tooltip=row.account,
                            selectable=True,
                        ),
                        search,
                        listing,
                    ],
                    tight=True,
                    width=420,
                ),
                actions=[ft.TextButton(self.host.tr("crud_cancel"), on_click=self.host._dismiss)],
            ),
        )

    def _definition(self, budget: Budget) -> ft.Control:
        tr = self.host.tr
        names = self._account_names()
        return ft.Column(
            [
                ft.Text(
                    f"{names.get(budget.account, budget.account)}"
                    f" · {format_date(budget.date, self.host.language)}",
                    tooltip=budget.account,
                    selectable=True,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    f"{budget.amount} {budget.currency} · {tr(f'budget_{budget.interval}')}",
                    selectable=True,
                ),
                ft.Row(
                    [
                        ft.TextButton(
                            tr("budget_view_report"),
                            icon=ft.Icons.BAR_CHART_OUTLINED,
                            data=budget,
                            on_click=self._show_report,
                        ),
                        ft.TextButton(
                            tr("budget_change"), on_click=lambda _: self._form(budget, new=True)
                        ),
                        ft.IconButton(
                            icon=ft.Icons.EDIT_OUTLINED,
                            tooltip=tr("crud_edit"),
                            icon_size=20,
                            on_click=lambda _: self._form(budget),
                        ),
                        ft.TextButton(
                            tr("budget_stop"),
                            on_click=lambda _: self._form(budget, new=True, stop=True),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip=tr("crud_delete"),
                            icon_size=20,
                            on_click=lambda _: self._delete(budget),
                        ),
                    ],
                    wrap=True,
                    spacing=0,
                ),
            ],
            spacing=4,
        )

    def _new(self, event: ft.Event[ft.Button]) -> None:
        self._form()

    def _form(self, budget: Budget | None = None, *, new: bool = False, stop: bool = False) -> None:
        journal = self.host._require_journal()
        if budget is not None and budget not in journal.ledger.budgets:
            self.host._error(LedgerError("crud_stale"), notify=True)
            return
        source = journal.source
        tr = self.host.tr
        identifiers = {category.id: category.name for category in journal.ledger.categories}
        if budget is not None:
            identifiers.setdefault(budget.account, budget.account)
        category = ft.Dropdown(
            expand=True,
            label=tr("budget_category"),
            options=[ft.DropdownOption(key=key, text=value) for key, value in identifiers.items()],
            value=budget.account if budget else None,
        )
        amount = ft.TextField(
            label=tr("amount"), value="0" if stop else str(budget.amount) if budget else ""
        )
        currency = ft.TextField(
            label=tr("currency"),
            value=budget.currency
            if budget
            else next(iter(journal.ledger.operating_currencies), "UAH"),
        )
        interval = ft.Dropdown(
            expand=True,
            label=tr("budget_interval"),
            value=budget.interval if budget else "monthly",
            options=[ft.DropdownOption(key=name, text=tr(f"budget_{name}")) for name in INTERVALS],
        )
        default_date = self.host._today()
        if interval.value == "monthly":
            default_date = default_date.replace(day=1)
        effective = DateTimeFields(
            self.host.language,
            self.host.system_zone,
            budget.date if budget and not new else default_date,
            compact=True,
            show_accounting_date=True,
            device_zone=self.host._picker_zone(),
        )

        def interval_changed(event: ft.Event[ft.Dropdown]) -> None:
            nonlocal default_date
            if budget is not None and not new:
                return
            if effective.occurred.value != date_input(default_date, self.host.language):
                return
            default_date = self.host._today()
            if interval.value == "monthly":
                default_date = default_date.replace(day=1)
            effective.occurred.value = date_input(default_date, self.host.language)
            self.host.page.update()

        interval.on_select = interval_changed

        async def save() -> str | None:
            return await journal.save_budget(
                category.value or "",
                date.fromisoformat(effective.values()[0]),
                interval.value or "",
                amount.value or "",
                currency.value or "",
                source,
                identifier=budget.id if budget and not new else None,
            )

        self.host._edit_dialog(
            tr("budget_stop" if stop else "crud_edit" if budget and not new else "budget_add"),
            [
                ft.Text(
                    tr(
                        "budget_stop_help"
                        if stop
                        else "budget_edit_help"
                        if budget and not new
                        else "budget_new_help"
                    )
                ),
                ft.Row([category]),
                amount,
                currency,
                ft.Row([interval]),
                ft.Text(tr("budget_effective")),
                effective,
            ],
            journal,
            save,
            stretch=True,
        )

    def _delete(self, budget: Budget) -> None:
        journal = self.host._require_journal()
        source = journal.source
        names = self._account_names()
        self.host._edit_dialog(
            self.host.tr("crud_delete"),
            [
                ft.Text(
                    names.get(budget.account, budget.account),
                    tooltip=budget.account,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(self.host.tr("budget_delete_help")),
            ],
            journal,
            lambda: journal.delete_budget(budget.id, source),
            deleting=True,
        )
