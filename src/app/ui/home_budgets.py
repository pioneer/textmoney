"""Compact, filter-independent Home budget overview."""

import asyncio
import logging
from concurrent.futures import Future
from typing import TYPE_CHECKING

import flet as ft

from app.domain.budgets import BudgetSummary, current_month_summary
from app.domain.ledger import LedgerError
from app.localization import format_budget_number, translate_plural
from app.ui.budgets import budget_progress_bar

if TYPE_CHECKING:
    from app.ui.finance import FinanceView

logger = logging.getLogger(__name__)


def home_heading(title: str, action: ft.IconButton) -> ft.Row:
    return ft.Row(
        [
            ft.Text(title, size=21, weight=ft.FontWeight.W_600, expand=True, expand_loose=True),
            action,
        ],
        spacing=4,
    )


class HomeBudgets(ft.Column):
    def __init__(self, host: "FinanceView") -> None:
        super().__init__(spacing=8, visible=False)
        self.host = host
        self.worker: Future[None] | None = None
        self.cache_key: tuple[object, ...] | None = None
        self.summary: BudgetSummary | None = None
        self.failed = False
        self.generation = 0
        self.open = ft.IconButton(icon_size=18, width=48, height=48, on_click=self._open)

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
            bool(journal and journal.ready),
            self.host._today().replace(day=1)
            if journal and journal.ready and journal.ledger.budgets
            else None,
            self.host.show_home_budgets,
        )

    def sync(self) -> None:
        if not self.host.startup_complete or self.host.current_screen != "home":
            self.render()
            return
        try:
            key = self._key()
        except LedgerError:
            self.cancel()
            self.cache_key = None
            self.summary = None
            self.failed = True
            logger.warning("Home budget month is unavailable", exc_info=True)
            self.render()
            return
        if key != self.cache_key:
            self.cancel()
            self.cache_key = key
            self.summary = None
            self.failed = False
            journal = self.host.journal
            if self.host.show_home_budgets and journal and journal.ready and journal.ledger.budgets:
                self.worker = self.host.page.run_task(self.refresh)
        self.render()

    async def refresh(self) -> None:
        generation = self.generation
        key = self._key()
        journal = self.host.journal
        if journal is None or not journal.ready or not self.host.show_home_budgets:
            return
        try:
            summary = await asyncio.to_thread(
                current_month_summary, journal.ledger, self.host._today()
            )
        except Exception:
            if generation != self.generation or key != self._key():
                return
            logger.exception("Home budget summary calculation failed")
            self.failed = True
        else:
            if generation != self.generation or key != self._key():
                return
            self.summary = summary
        self.render()
        self.host.page.update()

    def render(self) -> None:
        host = self.host
        summary = self.summary
        self.visible = host.show_home_budgets and (self.failed or bool(summary and summary.total))
        self.open.tooltip = host.tr("home_view_budgets")
        self.open.icon = ft.Icon(
            ft.Icons.ARROW_FORWARD, size=18, semantics_label=host.tr("home_view_budgets")
        )
        self.controls = [home_heading(host.tr("budget_title"), self.open)]
        if self.failed:
            self.controls.append(ft.Text(host.tr("home_budgets_failed"), color=ft.Colors.ERROR))
            return
        if summary is None or not summary.total:
            return
        self.controls.append(
            ft.Text(
                host.tr("home_budgets_count", count=len(summary.overspent), total=summary.total),
                color=ft.Colors.ERROR if summary.overspent else None,
            )
        )
        if not summary.overspent:
            self.controls.append(ft.Text(host.tr("home_budgets_ok")))
        limit = 5 if host.screen_class == "expanded" else 3
        journal = host.journal
        assert journal is not None
        names = {category.id: category.name for category in journal.ledger.categories}
        names["Expenses"] = host.tr("categories_expenses")
        names["Income"] = host.tr("categories_income")
        for budget in summary.preview[:limit]:
            currencies = sorted(budget.budget.keys() | budget.actual.keys())
            progress: list[ft.Control] = []
            for currency in currencies:
                consumed = budget.consumed(currency)
                remaining = budget.remaining(currency)
                overspent = (
                    remaining is not None
                    and remaining < 0
                    and budget.account.startswith("Expenses")
                )
                if consumed is None:
                    message = host.tr(
                        "budget_zero_progress" if currency in budget.budget else "budget_missing"
                    )
                    progress.append(ft.Text(f"{currency}: {message}", size=12))
                    continue
                bar = budget_progress_bar(consumed, overspent)
                bar.expand = True
                bar.height = bar.bar_height = 3
                percentage = f"{format_budget_number(consumed, 2)}%"
                progress.append(
                    ft.Row(
                        [
                            *([ft.Text(currency, size=12)] if len(currencies) > 1 else []),
                            bar,
                            ft.Text(
                                percentage,
                                size=12,
                                color=ft.Colors.ERROR if overspent else None,
                            ),
                        ],
                        spacing=8,
                    )
                )
            self.controls.append(
                ft.Column(
                    [
                        host._entity_label(
                            budget.account,
                            ft.Text(
                                names.get(budget.account, budget.account.split(":")[-1]),
                            ),
                        ),
                        *progress,
                    ],
                    data=budget.account,
                    spacing=4,
                )
            )
        omitted = summary.total - limit
        if omitted > 0:
            self.controls.append(
                ft.Text(
                    translate_plural(
                        host.language, "home_budgets_more", "home_budgets_more.plural", omitted
                    ),
                    size=12,
                )
            )

    def _open(self, event: ft.Event[ft.IconButton]) -> None:
        self.host.budgets.month = self.host._today().replace(day=1)
        self.host.budgets.category_id = ""
        self.host.budgets.currency.value = ""
        self.host.budgets.overspent.value = False
        self.host.budgets.cancel()
        self.host.budgets.cache_key = None
        self.host._navigate("budgets")
