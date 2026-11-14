import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.device_info import DeviceInfo
from form_controls import click

from app.domain.ledger import LedgerError
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import DesktopPreferences
from app.journal import FILENAME, Journal
from app.localization import translate_plural
from app.ui import finance, home_budgets
from app.ui.finance import HOME_BUDGETS_KEY, FinanceView

REAL_TODAY = FinanceView._today
SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Assets:Euro EUR
2000-01-01 open Expenses:Food
    name: "Їжа"
2000-01-01 open Expenses:Rent
2000-01-01 open Expenses:Travel
2000-01-01 open Expenses:Clothes
2000-01-01 open Expenses:Future
2026-01-01 custom "budget" Expenses:Food "monthly" 100 USD
2026-01-01 custom "budget" Expenses:Rent "monthly" 1000 USD
2026-01-01 custom "budget" Expenses:Travel "monthly" 10 EUR
2026-01-01 custom "budget" Expenses:Clothes "monthly" 50 USD
2026-02-01 custom "budget" Expenses:Future "monthly" 200 USD
2026-01-05 * "Synthetic" "Expense"
    Expenses:Food 150 USD
    Expenses:Rent 1100 USD
    Expenses:Clothes 80 USD
    Assets:Cash -1330 USD
2026-01-05 * "Synthetic" "Travel"
    Expenses:Travel 20 EUR
    Assets:Euro -20 EUR
"""


def many_budgets_source(overspent: bool) -> str:
    source = ['option "operating_currency" "USD"', "2000-01-01 open Assets:Cash USD"]
    for index in range(24):
        account = f"Expenses:Category{index:02}"
        used = 250 if overspent and index == 0 else 110 if overspent and index == 1 else index * 4
        source.extend(
            [
                f"2000-01-01 open {account}",
                f'    name: "Категорія {index:02}"',
                f'2026-01-01 custom "budget" {account} "monthly" 100 USD',
                f'2026-01-05 * "Synthetic" "Expense {index:02}"',
                f"    {account} {used} USD",
                f"    Assets:Cash -{used} USD",
            ]
        )
    return "\n".join(source) + "\n"


def walk(control: ft.Control) -> Iterator[ft.Control]:
    yield control
    if isinstance(control, (ft.Row, ft.Column)):
        for child in control.controls:
            yield from walk(child)
    if isinstance(control, ft.Container) and isinstance(control.content, ft.Control):
        yield from walk(control.content)


@pytest.fixture
def view(monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    monkeypatch.setattr(FinanceView, "_today", lambda _: date(2026, 1, 15))
    host = FinanceView()
    host.startup_complete = True
    host.language = "uk"
    return host, page


async def load(host: FinanceView, path: Path, source: str = SOURCE) -> None:
    (path / FILENAME).write_text(source)
    journal = Journal(DesktopFolder(str(path), FILENAME), today=host._today)
    await journal.load()
    host.journal = journal
    host._refresh()
    await host.home_budgets.refresh()


@pytest.mark.parametrize("width,limit", [(320, 3), (390, 3), (800, 3), (1366, 4)])
def test_home_budget_layout_rank_counts_and_currency(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, width: int, limit: int
) -> None:
    async def scenario() -> None:
        host, page = view
        page.width = width
        await load(host, tmp_path)
        host._apply_layout(width)
        summary = host.home_budgets
        assert summary.visible and summary.summary is not None
        assert summary.summary.total == 4
        assert [budget.account for budget in summary.summary.overspent] == [
            "Expenses:Travel",
            "Expenses:Clothes",
            "Expenses:Food",
            "Expenses:Rent",
        ]
        preview = [control for control in summary.controls if isinstance(control, ft.Column)]
        assert len(preview) == limit
        assert [control.data for control in preview] == [
            budget.account for budget in summary.summary.preview[:limit]
        ]
        texts = [control.value for control in walk(summary) if isinstance(control, ft.Text)]
        assert host.tr("home_budgets_count", count=4, total=4) in texts
        if limit < 4:
            assert (
                translate_plural(
                    host.language, "home_budgets_more", "home_budgets_more.plural", 4 - limit
                )
                in texts
            )
        assert "200.00%" in texts
        assert "Їжа" in texts
        bars = [control for control in walk(summary) if isinstance(control, ft.ProgressBar)]
        assert len(bars) == limit
        assert all(bar.value == 1 and bar.height == bar.bar_height == 3 for bar in bars)
        if width == 1366:
            columns = host.home_screen.controls[1]
            assert isinstance(columns, ft.Row)
            left, right = columns.controls
            assert isinstance(left, ft.Container) and isinstance(left.content, ft.Column)
            assert isinstance(right, ft.Container) and isinstance(right.content, ft.Column)
            assert left.content.controls[-2:] == [host.home_balances, summary]
            balances_heading = left.content.controls[0]
            assert right.content.controls[-1] is host.home_activity
            heading = right.content.controls[0]
        else:
            assert host.home_screen.controls[-2:] == [host.home_activity, summary]
            assert host.home_screen.controls[2] is host.home_balances
            balances_heading = host.home_screen.controls[1]
            heading = host.home_screen.controls[3]
        assert isinstance(balances_heading, ft.Row)
        assert balances_heading.controls[1] is host.home_accounts_open
        assert isinstance(heading, ft.Row)
        assert heading.controls[1] is host.home_transactions_open
        assert isinstance(summary.controls[0], ft.Row)
        assert heading.spacing == summary.controls[0].spacing == 4
        for button, label in (
            (summary.open, "Переглянути бюджети"),
            (host.home_transactions_open, "Переглянути операції"),
            (host.home_accounts_open, "Переглянути рахунки"),
        ):
            assert button.width == button.height == 48 and button.icon_size == 18
            assert button.tooltip == label
            assert isinstance(button.icon, ft.Icon) and button.icon.semantics_label == label
        text = heading.controls[0]
        assert isinstance(text, ft.Text) and text.expand and text.expand_loose
        assert host.home_budgets_label.width is not None
        assert host.home_budgets_label.width + 108 <= (host.body.width or width) - 40

    asyncio.run(scenario())


@pytest.mark.parametrize("width,limit", [(320, 3), (390, 3), (800, 3), (1366, 5)])
@pytest.mark.parametrize("overspent", [True, False], ids=["overspent-first", "within-limits"])
def test_many_budget_preview_ranks_all_budgets_and_has_noninteractive_omitted_count(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    limit: int,
    overspent: bool,
) -> None:
    async def scenario() -> None:
        host, page = view
        page.width = width
        monkeypatch.setattr(host, "_entity_icon", lambda _: ft.Container(width=24, height=24))
        await load(host, tmp_path, many_budgets_source(overspent))
        host._apply_layout(width)
        summary = host.home_budgets.summary
        assert summary is not None and summary.total == 24
        assert len(summary.overspent) == (2 if overspent else 0)
        expected = ([0, 1] if overspent else []) + list(range(23, 1 if overspent else -1, -1))
        assert [row.account for row in summary.preview] == [
            f"Expenses:Category{index:02}" for index in expected
        ]
        preview = [
            control for control in host.home_budgets.controls if isinstance(control, ft.Column)
        ]
        assert len(preview) == limit
        assert [control.data for control in preview] == [
            row.account for row in summary.preview[:limit]
        ]
        for row in preview:
            label = row.controls[0]
            assert isinstance(label, ft.Row) and label.expand is None
            assert isinstance(label.controls[1], ft.Text) and label.controls[1].expand
        bars = [
            control
            for row in preview
            for control in walk(row)
            if isinstance(control, ft.ProgressBar)
        ]
        assert len(bars) == limit
        for bar, report in zip(bars, summary.preview[:limit], strict=True):
            consumed = report.consumed("USD")
            assert consumed is not None
            assert bar.value == pytest.approx(min(100, float(consumed)) / 100)
            assert bar.height == bar.bar_height == 3
        omitted = host.home_budgets.controls[-1]
        assert isinstance(omitted, ft.Text)
        assert omitted.value == translate_plural(
            host.language, "home_budgets_more", "home_budgets_more.plural", 24 - limit
        )
        assert omitted.value == ("Ще 19 бюджетів" if limit == 5 else "Ще 21 бюджет")
        assert not any(
            isinstance(control, (ft.TextButton, ft.Button)) for control in walk(host.home_budgets)
        )
        texts = [
            control.value for control in walk(host.home_budgets) if isinstance(control, ft.Text)
        ]
        assert host.tr("home_budgets_count", count=2 if overspent else 0, total=24) in texts
        assert (host.tr("home_budgets_ok") in texts) == (not overspent)

    asyncio.run(scenario())


def test_summary_does_not_use_budget_filters_and_arrows_open_full_screens(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _page = view
        host.budgets.month = date(2025, 12, 1)
        host.budgets.category_id = "Expenses:Rent"
        host.budgets.currency.value = "USD"
        host.budgets.overspent.value = True
        await load(host, tmp_path)
        assert host.home_budgets.summary is not None
        assert len(host.home_budgets.summary.overspent) == 4
        click(host.home_budgets.open)
        assert host.current_screen == "budgets"
        assert host.budgets.month == date(2026, 1, 1)
        assert not host.budgets.category_id
        assert not host.budgets.currency.value and not host.budgets.overspent.value
        host.operations_account.value = "Assets:Cash"
        host.operations_category.value = "Expenses:Rent"
        click(host.home_transactions_open)
        assert host.current_screen == "transactions"
        assert not host.operations_account.value and not host.operations_category.value
        host._navigate("home")
        click(host.home_accounts_open)
        assert host.current_screen == "accounts"
        assert host.screen_host.content is host.accounts_screen
        host._back_from_management(MagicMock())
        assert host.current_screen == "home"

    asyncio.run(scenario())


def test_income_percentage_does_not_outrank_overspent_expense_budgets(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, _page = view
        source = many_budgets_source(True) + (
            "2000-01-01 open Income:Salary\n"
            '2026-01-01 custom "budget" Income:Salary "monthly" -1000 USD\n'
            '2026-01-06 * "Synthetic" "Income"\n'
            "    Income:Salary -2000 USD\n"
            "    Assets:Cash 2000 USD\n"
        )
        await load(host, tmp_path, source)
        summary = host.home_budgets.summary
        assert summary is not None and len(summary.overspent) == 2
        assert [row.account for row in summary.preview[:4]] == [
            "Expenses:Category00",
            "Expenses:Category01",
            "Income:Salary",
            "Expenses:Category23",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["multiple-currencies", "missing-currency-budget"])
def test_within_limit_preview_uses_highest_currency_percentage_and_puts_zero_limit_last(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, case: str
) -> None:
    async def scenario() -> None:
        host, _page = view
        source = SOURCE[: SOURCE.index("2026-01-05")].replace(
            'Expenses:Travel "monthly" 10 EUR', 'Expenses:Travel "monthly" 0 EUR'
        )
        source += (
            '2026-01-05 * "Synthetic" "Food"\n    Expenses:Food 20 USD\n    Assets:Cash -20 USD\n'
        )
        source += (
            '2026-01-01 custom "budget" Expenses:Food "monthly" 5 EUR\n'
            if case == "multiple-currencies"
            else ""
        )
        source += (
            '2026-01-06 * "Synthetic" "Euro food"\n'
            "    Expenses:Food 4 EUR\n"
            "    Assets:Euro -4 EUR\n"
        )
        await load(host, tmp_path, source)
        summary = host.home_budgets.summary
        assert summary is not None and not summary.overspent
        assert [row.account for row in summary.preview] == [
            "Expenses:Food",
            "Expenses:Clothes",
            "Expenses:Rent",
            "Expenses:Travel",
        ]
        preview_row = next(
            control
            for control in host.home_budgets.controls
            if isinstance(control, ft.Column) and control.data == "Expenses:Food"
        )
        bars = [control for control in walk(preview_row) if isinstance(control, ft.ProgressBar)]
        assert [bar.value for bar in bars] == pytest.approx(
            [0.8, 0.2] if case == "multiple-currencies" else [0.2]
        )
        texts = [control.value for control in walk(preview_row) if isinstance(control, ft.Text)]
        if case == "multiple-currencies":
            assert "EUR" in texts and "USD" in texts
            assert "80.00%" in texts and "20.00%" in texts
        else:
            assert f"EUR: {host.tr('budget_missing')}" in texts

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["zero-limit", "multiple-currencies", "child-spending"])
def test_budget_summary_preserves_report_currency_and_hierarchy_semantics(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, case: str
) -> None:
    async def scenario() -> None:
        host, _page = view
        source = SOURCE
        if case == "zero-limit":
            source = source.replace(
                'Expenses:Travel "monthly" 10 EUR', 'Expenses:Travel "monthly" 0 EUR'
            )
        elif case == "multiple-currencies":
            source += (
                '2026-01-01 custom "budget" Expenses:Food "monthly" 5 EUR\n'
                '2026-01-06 * "Synthetic" "Euro food"\n'
                "    Expenses:Food 10 EUR\n"
                "    Assets:Euro -10 EUR\n"
            )
        else:
            source += (
                "2000-01-01 open Expenses:Food:Meals\n"
                '2026-01-06 * "Synthetic" "Child spending"\n'
                "    Expenses:Food:Meals 100 USD\n"
                "    Assets:Cash -100 USD\n"
            )
        await load(host, tmp_path, source)
        summary = host.home_budgets.summary
        assert summary is not None and summary.total == 4
        if case == "zero-limit":
            assert summary.overspent[0].account == "Expenses:Travel"
            assert summary.overspent[0].percentage.is_infinite()
        else:
            food = next(budget for budget in summary.overspent if budget.account == "Expenses:Food")
            amounts = dict(food.amounts)
            if case == "multiple-currencies":
                assert float(amounts["USD"]) == pytest.approx(50)
                assert float(amounts["EUR"]) == pytest.approx(5)
            else:
                assert float(amounts["USD"]) == pytest.approx(150)
        preview_row = next(
            control
            for control in host.home_budgets.controls
            if isinstance(control, ft.Column)
            and control.data == ("Expenses:Travel" if case == "zero-limit" else "Expenses:Food")
        )
        report = next(row for row in summary.preview if row.account == preview_row.data)
        home_bars = [
            control for control in walk(preview_row) if isinstance(control, ft.ProgressBar)
        ]
        full_bars = [
            control
            for group in host.budgets._figures(report)
            for control in walk(group)
            if isinstance(control, ft.ProgressBar)
        ]
        assert [(bar.value, bar.color) for bar in home_bars] == [
            (bar.value, bar.color) for bar in full_bars
        ]
        texts = [control.value for control in walk(preview_row) if isinstance(control, ft.Text)]
        if case == "zero-limit":
            assert not home_bars
            assert f"EUR: {host.tr('budget_zero_progress')}" in texts
        elif case == "multiple-currencies":
            assert "USD" in texts and "EUR" in texts

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["no-journal", "no-budgets", "not-started", "within-budget"])
def test_empty_and_no_overspending_states(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, state: str
) -> None:
    async def scenario() -> None:
        host, _page = view
        if state == "no-journal":
            host._refresh()
        else:
            source = SOURCE
            if state == "no-budgets":
                source = "\n".join(
                    line for line in source.splitlines() if 'custom "budget"' not in line
                )
            elif state == "not-started":
                source = source.replace("2026-01-01 custom", "2026-02-01 custom")
            else:
                source = source[: source.index("2026-01-05")]
            await load(host, tmp_path, source)
        assert host.home_budgets.visible == (state == "within-budget")
        if state == "within-budget":
            summary = host.home_budgets.summary
            assert summary is not None and summary.total == 4 and not summary.overspent
            texts = [item.value for item in walk(host.home_budgets) if isinstance(item, ft.Text)]
            assert host.tr("home_budgets_ok") in texts

    asyncio.run(scenario())


@pytest.mark.parametrize("saved,expected", [(None, True), ("off", False), ("on", True)])
def test_preference_load_change_and_restart(
    view: tuple[FinanceView, MagicMock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved: str | None,
    expected: bool,
) -> None:
    async def scenario() -> None:
        host, page = view
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        monkeypatch.setattr(finance, "DesktopPreferences", lambda: preferences)
        page.platform = ft.PagePlatform.LINUX
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(locales=[ft.Locale(language_code="uk")])
        )
        if saved:
            await preferences.set(HOME_BUDGETS_KEY, saved)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await host._lifecycle()
        assert host.show_home_budgets == expected
        await load(host, tmp_path)
        host.home_budgets_toggle.value = not expected
        await host._home_budgets_changed(MagicMock(control=host.home_budgets_toggle))
        assert host.show_home_budgets == (not expected)
        assert not host.home_budgets_toggle.disabled
        assert (
            sum(control is host.home_budgets_toggle for control in walk(host.settings_screen)) == 1
        )
        assert await preferences.get(HOME_BUDGETS_KEY) == ("off" if expected else "on")
        restarted = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await restarted._lifecycle()
        assert restarted.show_home_budgets == (not expected)
        assert restarted.home_budgets_toggle.value == (not expected)

    asyncio.run(scenario())


def test_summary_failure_and_stale_result_are_not_success(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, _page = view
        await load(host, tmp_path)
        original = home_budgets.current_month_summary

        def fail(*args: object) -> object:
            raise RuntimeError("synthetic calculation failure")

        monkeypatch.setattr(home_budgets, "current_month_summary", fail)
        await host.home_budgets.refresh()
        assert host.home_budgets.failed
        assert any(
            isinstance(control, ft.Text) and control.value == host.tr("home_budgets_failed")
            for control in host.home_budgets.controls
        )
        monkeypatch.setattr(home_budgets, "current_month_summary", original)

        async def delayed(*args: object) -> object:
            host.home_budgets.cancel()
            return original(host._require_journal().ledger, host._today())

        monkeypatch.setattr(asyncio, "to_thread", delayed)
        host.home_budgets.summary = None
        await host.home_budgets.refresh()
        assert host.home_budgets.summary is None

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["false", "exception", "unavailable"])
def test_preference_failure_restores_previous_visibility_and_reports_error(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, failure: str
) -> None:
    async def scenario() -> None:
        host, _page = view
        await load(host, tmp_path)
        preferences = MagicMock(spec=DesktopPreferences)
        preferences.set = AsyncMock(return_value=False)
        if failure == "exception":
            from app.infrastructure.preferences import PreferencesError

            preferences.set.side_effect = PreferencesError("synthetic settings failure")
        host.preferences = None if failure == "unavailable" else preferences
        host.home_budgets_toggle.value = False
        await host._home_budgets_changed(MagicMock(control=host.home_budgets_toggle))
        assert host.show_home_budgets and host.home_budgets_toggle.value
        assert host.error_message is not None
        assert host.journal is not None and host.journal.ready

    asyncio.run(scenario())


def test_current_month_uses_configured_zone(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BoundaryClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2026, 2, 1, 0, 30, tzinfo=UTC)

    host, _page = view
    monkeypatch.setattr(finance, "datetime", BoundaryClock)
    monkeypatch.setattr(FinanceView, "_today", REAL_TODAY)
    host.timezone_name = "America/Los_Angeles"
    asyncio.run(load(host, tmp_path))
    assert host._today() == date(2026, 1, 31)
    assert host.home_budgets.summary is not None and host.home_budgets.summary.total == 4


def test_unavailable_month_surfaces_summary_error_without_breaking_home(
    view: tuple[FinanceView, MagicMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        host, _page = view
        await load(host, tmp_path)

        def unavailable(self: FinanceView) -> date:
            raise LedgerError("crud_timezone_unavailable")

        monkeypatch.setattr(FinanceView, "_today", unavailable)
        host.home_budgets.sync()
        assert host.home_budgets.failed and host.home_budgets.visible
        assert host.home_budgets.summary is None
        assert host.journal is not None and host.journal.ready

    asyncio.run(scenario())


def test_summary_work_starts_only_after_startup_and_is_cancelled_on_unmount(
    view: tuple[FinanceView, MagicMock], tmp_path: Path
) -> None:
    async def scenario() -> None:
        host, page = view
        host.startup_complete = False
        page.run_task.reset_mock()
        await load(host, tmp_path)
        page.run_task.assert_not_called()
        host.startup_complete = True
        host._apply_layout(390)
        page.run_task.assert_called_once_with(host.home_budgets.refresh)
        worker = host.home_budgets.worker
        assert worker is not None
        host.will_unmount()
        page.run_task.return_value.cancel.assert_called_once()

    asyncio.run(scenario())
