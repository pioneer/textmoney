"""Budget controls; all arithmetic and journal mutation stay outside the UI."""

import asyncio
import logging
from concurrent.futures import Future
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import flet as ft

from app.budget_order import POLICY as ORDER_POLICY
from app.budget_order import BudgetOrderStore
from app.domain.budget_order import budget_parent, move_budget, ordered_budgets
from app.domain.budgets import (
    INTERVALS,
    Budget,
    BudgetRow,
    definition_report_month,
    interval_next,
    rows,
)
from app.domain.ledger import LedgerError
from app.domain.reporting import shift_month
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import PreferencesError
from app.infrastructure.record_files import AndroidRecordFiles, DesktopRecordFiles
from app.localization import date_input, format_budget_number, format_date, localized_error
from app.shared_defaults import SharedDefaultsError
from app.storage import StorageError
from app.ui.datetime_fields import DateTimeFields
from app.ui.dialogs import show_dialog
from app.ui.layout import action_toolbar, month_navigation
from app.ui.tree_style import compact_tree_theme

if TYPE_CHECKING:
    from app.ui.finance import FinanceView

_REPORT_TEXT_INSET = 14
logger = logging.getLogger(__name__)


def budget_progress_bar(consumed: Decimal, overspent: bool) -> ft.ProgressBar:
    return ft.ProgressBar(
        value=float(max(Decimal(0), min(Decimal(100), consumed))) / 100,
        color=ft.Colors.ERROR if overspent else None,
    )


class BudgetView(ft.Column):
    def __init__(self, host: "FinanceView") -> None:
        super().__init__(spacing=16)
        self.host = host
        self.worker: Future[None] | None = None
        self.generation = 0
        self.cache_key: tuple[object, ...] | None = None
        self.model_key: tuple[object, ...] | None = None
        self.filter_key: tuple[object, ...] | None = None
        self.highlighted: tuple[date, str] | None = None
        self.reveal_requested = False
        self.scroll_target: ft.ListTile | None = None
        self.month = host._today().replace(day=1)
        self.category_id = ""
        self.category_choices: dict[str, str] = {}
        self.report_rows: tuple[BudgetRow, ...] = ()
        self.order: tuple[str, ...] = ()
        self.order_store: BudgetOrderStore | None = None
        self.order_journal = host.journal
        self.order_error = False
        self.order_notice = ft.Text(color=ft.Colors.ERROR, visible=False)
        self.order_button = ft.IconButton(on_click=self._show_order)
        self.period = ft.TextButton(
            icon=ft.Icon(ft.Icons.CALENDAR_TODAY, size=16),
            expand=True,
            height=48,
            style=ft.ButtonStyle(text_style=ft.TextStyle(size=16, weight=ft.FontWeight.W_500)),
            on_click=self._pick_month,
        )
        self.current = ft.TextButton(on_click=self._current_month)
        self.scope = ft.Dropdown(
            value="",
            expand=True,
            text_style=ft.TextStyle(size=16),
            on_select=self._category_changed,
        )
        self.currency = ft.Dropdown(value="", width=220, on_select=self._changed, visible=False)
        self.overspent = ft.Checkbox(value=False, on_change=self._changed)
        self.reset = ft.TextButton(on_click=self._reset)
        self.active_filters = ft.Row(wrap=True)
        self.error = ft.Text(color=ft.Colors.ERROR, visible=False, selectable=True)
        self.loading = ft.ProgressRing(visible=False)
        self.results = ft.Column(spacing=12)
        self.definitions = ft.Column(spacing=8)
        self.add = ft.Button(icon=ft.Icons.ADD, width=240, height=48, on_click=self._new)
        self.previous = ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, on_click=self._previous)
        self.next = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, on_click=self._next)
        self.filter_toggle = ft.IconButton(
            icon=ft.Icons.FILTER_LIST,
            on_click=self._toggle_filters,
        )
        self.filters = ft.Column(
            [
                ft.Row([self.scope]),
                self.currency,
                self.overspent,
                self.active_filters,
                self.reset,
            ],
            spacing=8,
            visible=False,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.history_help = ft.Text(size=12)
        self.history = ft.Button(width=240, height=48, on_click=self._show_history)
        self.controls = [
            action_toolbar(self.add, self.history),
            ft.Column(
                [
                    month_navigation(self.previous, self.period, self.next),
                    ft.Row(
                        [
                            ft.Container(
                                self.order_button,
                                expand=True,
                                alignment=ft.Alignment.CENTER_LEFT,
                            ),
                            self.current,
                            ft.Container(
                                self.filter_toggle,
                                expand=True,
                                alignment=ft.Alignment.CENTER_RIGHT,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=0,
            ),
            self.filters,
            self.order_notice,
            self.error,
            self.loading,
            ft.Container(self.results, theme=compact_tree_theme()),
        ]

    def sync(self) -> None:
        tr = self.host.tr
        journal = self.host.journal
        ready = bool(journal and journal.ready)
        self.add.content = tr("create_budget")
        self.add.disabled = not ready
        self.history.content = tr("budget_limit_history")
        self.history.disabled = not ready
        self.order_button.tooltip = tr("budget_order")
        self.order_button.icon = ft.Icon(ft.Icons.SORT, semantics_label=tr("budget_order"))
        self._order_status()
        self.order_button.visible = bool(
            ready
            and journal
            and (
                len({budget.account for budget in journal.ledger.budgets}) > 1
                or self.order_notice.visible
            )
        )
        self.order_button.disabled = not ready
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
        self.history_help.value = tr("budget_history_help")
        model_key = (
            id(journal),
            id(journal.ledger) if journal else None,
            ready,
            self.host.language,
            self.host.icon_revision,
        )
        if model_key != self.model_key:
            self.model_key = model_key
            if journal and journal.ready:
                self.definitions.controls = [
                    self._definition(budget)
                    for budget in sorted(journal.ledger.budgets, key=lambda b: (b.account, b.date))
                ] or [ft.Text(tr("budget_empty"))]
            else:
                self.category_choices = {}
                self.definitions.controls = [ft.Text(tr("report_unavailable"))]
        self.scope.disabled = not ready
        self._sync_filters()

    def _sync_filters(self) -> None:
        tr = self.host.tr
        category = self.category_choices.get(self.category_id, self.category_id)
        self.scope.label = tr("budget_category")
        self.scope.value = self.category_id
        self.scope.tooltip = self.category_id or None
        self.scope.leading_icon = self.host._entity_dropdown_icon(self.category_id)
        self.filter_toggle.tooltip = tr("operations_filters")
        self.filter_toggle.selected = self.filters.visible
        self.filter_toggle.icon_color = (
            ft.Colors.PRIMARY
            if self.category_id or self.currency.value or self.overspent.value
            else None
        )
        filter_key = (
            self.host.language,
            self.host.icon_revision,
            self.category_id,
            category,
            self.currency.value,
            self.overspent.value,
            self.host.body.width,
            tuple(sorted(self.category_choices.items())),
        )
        if self.filter_key == filter_key:
            return
        self.filter_key = filter_key
        self.scope.options = self.host._category_option_lines(
            [
                ft.DropdownOption(key="", text=tr("operations_all_categories")),
                *[
                    self.host._category_form_option(identifier, name)
                    for identifier, name in sorted(
                        self.category_choices.items(), key=lambda item: item[0].split(":")
                    )
                ],
            ]
        )
        chips: list[ft.Control] = []
        for key, label in (
            ("category", f"{tr('budget_category')}: {category}" if category else ""),
            ("currency", str(self.currency.value or "")),
        ):
            if label:
                chips.append(
                    ft.Chip(
                        leading=self.host._entity_icon(self.category_id)
                        if key == "category"
                        else None,
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

    def _tree_items(self, identifiers: set[str]) -> dict[str, str]:
        names = self._account_names()
        items: dict[str, str] = {}
        for identifier in identifiers:
            parts = identifier.split(":")
            for length in range(1, len(parts) + 1):
                key = ":".join(parts[:length])
                items[key] = names.get(key, parts[length - 1])
        return items

    def _toggle_filters(self, event: ft.Event[ft.IconButton]) -> None:
        self.filters.visible = not self.filters.visible
        self.filter_toggle.selected = self.filters.visible
        self.host.page.update()

    async def _category_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        await self._select_category(str(self.scope.value or ""))

    def _show_history(self, event: ft.Event[ft.Button]) -> None:
        self.sync()
        show_dialog(
            self.host.page,
            ft.AlertDialog(
                title=self.host.tr("budget_limit_history"),
                scrollable=True,
                content=ft.Column([self.history_help, self.definitions], tight=True, width=640),
                actions=[
                    ft.TextButton(self.host.tr("daily_report_close"), on_click=self.host._dismiss)
                ],
            ),
        )

    def _order_status(self) -> None:
        key = (
            "budget_order_unavailable"
            if self.order_error
            else "budget_order_conflict"
            if self.order_store and self.order_store.conflicts
            else None
        )
        self.order_notice.value = self.host.tr(key) if key else ""
        self.order_notice.visible = key is not None

    async def load_order(self) -> None:
        journal = self.host.journal
        if self.order_journal is not journal:
            self.order_journal = journal
            self.order_store = None
            self.order = ()
            self.order_error = False
        if journal is None or not journal.ready:
            return
        if self.order_store is None:
            folder = journal.storage.folder
            if not isinstance(folder, (DesktopFolder, AndroidFolder)):
                raise SharedDefaultsError("budget_order_unavailable")
            self.order_store = BudgetOrderStore(
                AndroidRecordFiles(folder, ORDER_POLICY)
                if isinstance(folder, AndroidFolder)
                else DesktopRecordFiles(folder, ORDER_POLICY)
            )
        store = self.order_store
        try:
            await store.load()
        except (PreferencesError, StorageError, TimeoutError):
            logger.exception("Budget order could not be read")
            if self.order_journal is journal:
                self.order_error = True
                self._order_status()
            return
        if self.host.journal is not journal or self.order_store is not store:
            return
        self.order_error = False
        value = store.values.get("budget_order")
        previous = self.order
        if "budget_order" not in store.conflicts:
            self.order = value if isinstance(value, tuple) else ()
        self._order_status()
        if (
            previous != self.order
            and self.report_rows
            and self.cache_key
            and self.cache_key[0] == id(journal)
        ):
            self._render(self.report_rows)

    async def _show_order(self, event: ft.Event[ft.IconButton]) -> None:
        async def open_dialog() -> None:
            journal = self.host._require_journal()
            await self.load_order()
            if self.host.journal is not journal:
                raise ConflictError("budget_order_stale")
            store = self.order_store
            if store is None or self.order_error:
                raise SharedDefaultsError("budget_order_unavailable")
            accounts = {budget.account for budget in journal.ledger.budgets}
            names = self._account_names()
            draft = ordered_budgets(accounts, self.order)
            heads = store.heads.get("budget_order", ())
            reset_order = False
            busy = False
            write_failed = False
            error = ft.Text(color=ft.Colors.ERROR, visible=False)
            height = self.host.page.height
            available = height - 400 if isinstance(height, (int, float)) else 360
            listing = ft.ReorderableListView(
                show_default_drag_handles=False,
                height=min(360, max(96, available), max(1, len(accounts)) * 48),
                spacing=0,
            )
            items: dict[str, ft.Row] = {}
            buttons: dict[str, tuple[ft.IconButton, ft.IconButton]] = {}

            def render() -> None:
                listing.controls = [items[account] for account in draft]
                for account in draft:
                    siblings = [
                        item
                        for item in draft
                        if budget_parent(item, accounts) == budget_parent(account, accounts)
                    ]
                    index = siblings.index(account)
                    up, down = buttons[account]
                    up.disabled, down.disabled = index == 0, index == len(siblings) - 1

            def move(source: str, target: str) -> None:
                nonlocal draft, reset_order
                try:
                    draft = move_budget(draft, source, target)
                except LedgerError as exc:
                    error.value = self.host.tr(str(exc))
                    error.visible = True
                else:
                    reset_order = False
                    error.visible = False
                render()
                self.host.page.update()

            def step(event: ft.Event[ft.IconButton]) -> None:
                account, direction = event.control.data
                siblings = [
                    item
                    for item in draft
                    if budget_parent(item, accounts) == budget_parent(account, accounts)
                ]
                index = siblings.index(account) + direction
                if not 0 <= index < len(siblings):
                    raise LedgerError("budget_order_stale")
                move(account, siblings[index])

            def reorder(event: ft.OnReorderEvent) -> None:
                if (
                    event.old_index is None
                    or event.new_index is None
                    or not 0 <= event.old_index < len(draft)
                    or not 0 <= event.new_index < len(draft)
                ):
                    raise LedgerError("budget_order_stale")
                move(draft[event.old_index], draft[event.new_index])

            listing.on_reorder = reorder
            for account in draft:
                parent = budget_parent(account, accounts)
                depth = 0
                while parent:
                    depth += 1
                    parent = budget_parent(parent, accounts)
                up, down = (
                    ft.IconButton(
                        icon=ft.Icon(icon, semantics_label=self.host.tr(label)),
                        tooltip=self.host.tr(label),
                        data=(account, direction),
                        size_constraints=ft.BoxConstraints(min_width=48, min_height=48),
                        on_click=step,
                    )
                    for icon, label, direction in (
                        (ft.Icons.ARROW_UPWARD, "budget_order_up", -1),
                        (ft.Icons.ARROW_DOWNWARD, "budget_order_down", 1),
                    )
                )
                buttons[account] = up, down
                items[account] = ft.Row(
                    [
                        ft.ReorderableDragHandle(
                            ft.Container(
                                ft.Icon(
                                    ft.Icons.DRAG_HANDLE,
                                    semantics_label=self.host.tr("budget_order_drag"),
                                ),
                                width=48,
                                height=48,
                                alignment=ft.Alignment.CENTER,
                            )
                        ),
                        ft.Container(width=12 * depth),
                        *self.host._entity_icon_controls(account),
                        ft.Text(
                            names.get(account, account),
                            expand=True,
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip=account,
                        ),
                        up,
                        down,
                    ],
                    key=account,
                    data=account,
                    spacing=0,
                )

            def restore(event: ft.Event[ft.TextButton]) -> None:
                nonlocal draft, reset_order
                draft, reset_order = ordered_budgets(accounts, ()), True
                error.visible = False
                render()
                self.host.page.update()

            restore_button = ft.TextButton(self.host.tr("budget_order_reset"), on_click=restore)
            cancel = ft.TextButton(self.host.tr("crud_cancel"), on_click=self.host._dismiss)

            async def save(event: ft.Event[ft.TextButton]) -> None:
                nonlocal busy
                if busy:
                    return

                async def persist() -> None:
                    nonlocal write_failed
                    if write_failed:
                        raise SharedDefaultsError("budget_order_save_failed")
                    if (
                        self.host.journal is not journal
                        or self.order_store is not store
                        or {budget.account for budget in journal.ledger.budgets} != accounts
                    ):
                        raise ConflictError("budget_order_stale")
                    try:
                        await store.save("budget_order", () if reset_order else draft, heads)
                    except ConflictError as exc:
                        raise ConflictError("budget_order_stale") from exc
                    except (PreferencesError, StorageError, TimeoutError) as exc:
                        logger.exception("Budget order save failed")
                        write_failed = True
                        self.order_error = True
                        self._order_status()
                        raise SharedDefaultsError("budget_order_save_failed") from exc
                    self.order = () if reset_order else draft
                    self._order_status()
                    self._render(self.report_rows)
                    self.host.page.pop_dialog()

                busy = True
                for control in (listing, restore_button, cancel, save_button):
                    control.disabled = True
                try:
                    await self.host._action(persist)
                finally:
                    busy = False
                    for control in (listing, restore_button, cancel, save_button):
                        control.disabled = False
                    save_button.disabled = write_failed
                    self.host.page.update()

            save_button = ft.TextButton(self.host.tr("crud_save"), on_click=save)
            render()
            choices = [
                ft.Text(
                    " → ".join(names.get(account, account) for account in choice),
                    selectable=True,
                )
                for choice in store.conflicts.get("budget_order", ())
                if isinstance(choice, tuple)
            ]
            show_dialog(
                self.host.page,
                ft.AlertDialog(
                    modal=True,
                    inset_padding=ft.Padding.symmetric(horizontal=12, vertical=24),
                    title=self.host.tr("budget_order"),
                    scrollable=True,
                    content=ft.Column(
                        [
                            ft.Text(self.host.tr("budget_order_help"), size=12),
                            *(
                                [ft.Text(self.host.tr("budget_order_conflict")), *choices]
                                if choices
                                else []
                            ),
                            listing,
                            error,
                            restore_button,
                        ],
                        tight=True,
                        width=480,
                    ),
                    actions=[cancel, save_button],
                ),
            )

        await self.host._action(open_dialog)

    async def _select_category(self, identifier: str) -> None:
        self.category_id = identifier
        await self._refresh_filters()

    async def _clear_filter(self, event: ft.Event[ft.Chip]) -> None:
        key = event.control.data
        if key == "category":
            self.category_id = ""
        elif key == "currency":
            self.currency.value = ""
        await self._refresh_filters()

    async def _reset(self, event: ft.Event[ft.TextButton]) -> None:
        self.category_id = ""
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
            self.host.icon_revision,
            self.month,
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
            await self.load_order()
            begin = self.month
            end = interval_next(begin, "monthly")
            ledger = journal.ledger
            results = await asyncio.to_thread(
                rows,
                ledger,
                begin,
                end,
                "monthly",
                tree=True,
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
            chosen = shift_month(self.month, offset)
            if chosen.year > 9998:
                raise LedgerError("budget_month_error")
            self.month = chosen
        except LedgerError:
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
                    selectable=True,
                ),
                ft.Text(
                    f"{tr('budget_actual')}: {actual_text}",
                    selectable=True,
                ),
                ft.Text(
                    f"{tr('budget_remaining')}: {remaining_text}",
                    color=ft.Colors.ERROR if overspent else None,
                    selectable=True,
                ),
            ]
            progress_label = None
            if consumed is not None:
                progress_label = ft.Text(
                    f"{tr('budget_consumed')}: {format_budget_number(consumed, 2)}%"
                )
                lines.extend(
                    [
                        progress_label,
                        budget_progress_bar(consumed, overspent),
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
            controls.append(
                ft.Column(
                    [
                        line
                        if isinstance(line, ft.ProgressBar)
                        else ft.Container(
                            line,
                            padding=ft.Padding(
                                left=_REPORT_TEXT_INSET,
                                right=_REPORT_TEXT_INSET,
                                bottom=8 if line is progress_label else 0,
                            ),
                        )
                        for line in lines
                    ],
                    spacing=4,
                )
            )
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
            by_account = {
                account: by_account[account] for account in ordered_budgets(by_account, self.order)
            }

            def node(
                account: str,
                depth: int = 0,
                *,
                by_account: dict[str, BudgetRow] = by_account,
                begin: date = begin,
            ) -> list[ft.Control]:
                row = by_account[account]
                children = [
                    identifier
                    for identifier in by_account
                    if budget_parent(identifier, set(by_account)) == account
                ]
                key = (begin, account)
                actions = [
                    ft.IconButton(
                        icon=ft.Icons.RECEIPT_LONG,
                        tooltip=tr("budget_transactions"),
                        data=row,
                        icon_size=20,
                        on_click=self._transactions,
                    ),
                ]
                for action in actions:
                    action.padding = _REPORT_TEXT_INSET
                    action.visual_density = ft.VisualDensity.STANDARD
                    action.size_constraints = ft.BoxConstraints(min_width=48, min_height=48)

                highlighted = self.highlighted == key
                tile = ft.ListTile(
                    key=ft.ScrollKey(f"budget-report:{begin.isoformat()}:{account}"),
                    title=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Row(
                                        [
                                            ft.Container(width=_REPORT_TEXT_INSET, height=48),
                                            *self.host._entity_icon_controls(account),
                                            ft.Text(
                                                names.get(account, account.rsplit(":", 1)[-1]),
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                                tooltip=account,
                                                expand=True,
                                            ),
                                        ],
                                        expand=True,
                                        spacing=0,
                                    ),
                                    self._card_actions(row, actions),
                                ],
                                spacing=0,
                                vertical_alignment=ft.CrossAxisAlignment.START,
                            ),
                            *self._limit_controls(row),
                            *(
                                [
                                    ft.Container(
                                        ft.Text(tr("budget_subtree"), size=12),
                                        padding=ft.Padding.symmetric(horizontal=_REPORT_TEXT_INSET),
                                    )
                                ]
                                if row.direct_budget != row.budget
                                or row.direct_actual != row.actual
                                else []
                            ),
                            *self._figures(row),
                        ],
                        spacing=4,
                    ),
                    data=row,
                    dense=True,
                    min_height=40,
                    min_vertical_padding=0,
                    visual_density=ft.VisualDensity.COMPACT,
                    content_padding=ft.Padding(left=16 * (depth + 1), top=16, right=16, bottom=16),
                    shape=ft.RoundedRectangleBorder(),
                    bgcolor=ft.Colors.SECONDARY_CONTAINER if highlighted else None,
                )
                if highlighted and self.reveal_requested:
                    # The client must lay out the target before scroll_to can find its key.
                    tile.on_size_change = self._reveal_report
                    self.scroll_target = tile
                return [
                    self.host._action_row(tile),
                    *[control for child in children for control in node(child, depth + 1)],
                ]

            roots = [
                account for account in by_account if not budget_parent(account, set(by_account))
            ]
            controls.extend(control for root in roots for control in node(root))
        self.results.controls = controls or [ft.Text(tr("budget_report_empty"))]

    def _limit_controls(self, row: BudgetRow) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for currency in sorted(row.direct_budget):
            if self.currency.value and self.currency.value != currency:
                continue
            budget = self._active_budget(row.account, row.end, currency)
            if budget is None:
                continue
            controls.append(
                ft.Container(
                    ft.Column(
                        [
                            ft.Text(
                                self.host.tr(
                                    "budget_limit_effective",
                                    amount=str(budget.amount),
                                    currency=budget.currency,
                                    interval=self.host.tr(f"budget_{budget.interval}"),
                                    date=format_date(budget.date, self.host.language),
                                ),
                                selectable=True,
                            ),
                        ],
                        spacing=0,
                    ),
                    padding=ft.Padding.symmetric(horizontal=_REPORT_TEXT_INSET),
                )
            )
        return controls

    def _card_actions(self, row: BudgetRow, actions: list[ft.IconButton]) -> ft.Row:
        controls: list[ft.Control] = list(actions)
        currencies = [
            currency
            for currency in sorted(row.direct_budget)
            if not self.currency.value or self.currency.value == currency
        ]
        for currency in currencies:
            budget = self._active_budget(row.account, row.end, currency)
            if budget is None:
                continue
            if len(currencies) > 1:
                controls.append(ft.Text(currency, size=12))
            controls.extend(self._budget_actions(budget).controls)
        return ft.Row(
            controls,
            expand=2,
            wrap=True,
            alignment=ft.MainAxisAlignment.END,
            spacing=0,
            run_spacing=0,
        )

    def _show_report(self, event: ft.Event[ft.TextButton]) -> None:
        budget = event.control.data
        assert isinstance(budget, Budget)
        journal = self.host.journal
        if journal is None or not journal.ready or budget not in journal.ledger.budgets:
            self.host._error(LedgerError("crud_stale"), notify=True)
            return
        self.cancel()
        self.month = definition_report_month(journal.ledger.budgets, budget, self.host._today())
        self.category_id = ""
        self.currency.value = budget.currency
        self.overspent.value = False
        self.highlighted = (self.month, budget.account)
        self.reveal_requested = True
        self.cache_key = None
        self.host._navigate("budgets")

    async def _reveal_report(self, event: ft.LayoutSizeChangeEvent[ft.LayoutControl]) -> None:
        if (
            event.control is not self.scroll_target
            or not self.reveal_requested
            or self.host.current_screen != "budgets"
        ):
            return
        self.reveal_requested = False
        self.scroll_target = None
        try:
            await self.host.body.scroll_to(scroll_key=event.control.key, duration=350)
        except Exception as exc:
            self.host._error(exc, notify=True)
            self.host.page.update()

    def _active_budget(self, account: str, end: date, currency: str | None = None) -> Budget | None:
        journal = self.host.journal
        if journal is None or not journal.ready:
            return None
        candidates = [
            (index, budget)
            for index, budget in enumerate(journal.ledger.budgets)
            if budget.account == account
            and budget.date < end
            and (
                not (currency or self.currency.value)
                or budget.currency == (currency or self.currency.value)
            )
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[1].date, item[0]))[1]

    def _transactions(self, event: ft.Event[ft.IconButton]) -> None:
        row = event.control.data
        assert isinstance(row, BudgetRow)
        names = self._account_names()
        listing = ft.Column(spacing=0)
        search = ft.TextField(label=self.host.tr("budget_search_transactions"))

        def render(event: ft.Event[ft.TextField] | None = None) -> None:
            query = (search.value or "").strip().casefold()
            transactions = [
                transaction
                for transaction in sorted(
                    row.transactions, key=self.host._transaction_sort_key, reverse=True
                )
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
            ]
            listing.controls = self.host._transaction_groups(
                transactions, names, read_only=True
            ) or [ft.Text(self.host.tr("budget_no_matches"))]
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
                        self.host._entity_label(
                            row.account,
                            ft.Text(
                                names.get(row.account, row.account),
                                tooltip=row.account,
                                selectable=True,
                            ),
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

    def _budget_actions(self, budget: Budget) -> ft.Row:
        tr = self.host.tr
        return ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    tooltip=tr("crud_edit"),
                    icon_size=20,
                    data=budget,
                    on_click=lambda _: self._form(budget),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    tooltip=tr("crud_delete"),
                    icon_size=20,
                    data=budget,
                    on_click=lambda _: self._delete(budget),
                ),
                ft.TextButton(
                    tr("budget_change"),
                    data=budget,
                    on_click=lambda _: self._form(budget, new=True),
                ),
                ft.TextButton(
                    tr("budget_stop"),
                    data=budget,
                    on_click=lambda _: self._form(budget, new=True, stop=True),
                ),
            ],
            wrap=True,
            spacing=0,
        )

    def _definition(self, budget: Budget) -> ft.Control:
        tr = self.host.tr
        names = self._account_names()
        return self.host._action_row(
            ft.Column(
                [
                    self.host._entity_label(
                        budget.account,
                        ft.Text(
                            f"{names.get(budget.account, budget.account)}"
                            f" · {format_date(budget.date, self.host.language)}",
                            tooltip=budget.account,
                            selectable=True,
                            weight=ft.FontWeight.BOLD,
                        ),
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
                                on_click=self._history_report,
                            ),
                        ],
                        wrap=True,
                        spacing=0,
                    ),
                    self._budget_actions(budget),
                ],
                spacing=4,
            )
        )

    def _new(self, event: ft.Event[ft.Button]) -> None:
        self._form()

    def _history_report(self, event: ft.Event[ft.TextButton]) -> None:
        self.host.page.pop_dialog()
        self._show_report(event)

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
            menu_height=self.host._category_menu_height(),
            label=tr("budget_category"),
            on_select=self.host._entity_dropdown_selected,
            leading_icon=self.host._entity_dropdown_icon(budget.account) if budget else None,
            options=self.host._category_option_lines(
                [
                    self.host._category_form_option(key, identifiers[key])
                    for key in sorted(identifiers, key=lambda identifier: identifier.split(":"))
                ]
            ),
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
                ft.Row([self.host._category_menu_anchor(category)]),
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
                self.host._entity_label(
                    budget.account,
                    ft.Text(
                        names.get(budget.account, budget.account),
                        tooltip=budget.account,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.Text(self.host.tr("budget_delete_help")),
            ],
            journal,
            lambda: journal.delete_budget(budget.id, source),
            deleting=True,
        )
