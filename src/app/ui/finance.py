import asyncio
import logging
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from datetime import date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from itertools import groupby
from zoneinfo import available_timezones

import flet as ft
from finance_saf import FinanceSaf

from app.domain.code_suggestions import (
    CodeCandidate,
    suggest_account_code,
    suggest_category_code,
    validate_account_code,
    validate_category_code,
)
from app.domain.ledger import (
    ADJUSTMENTS,
    BANK_FEES,
    EPOCH,
    Account,
    Category,
    LedgerError,
    Posting,
    Transaction,
    cashflow_amounts,
    exchange_values,
    money,
)
from app.domain.reporting import CategoryReport, PeriodReport, period_bounds, period_report
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder, unwrap
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import (
    DesktopPreferences,
    FeePreferences,
    PreferencesError,
    fee_category_in_branches,
)
from app.journal import FILENAME, Journal
from app.localization import (
    LANGUAGES,
    format_amount,
    format_date,
    format_timestamp,
    localized_error,
    system_language,
    translate,
    translate_plural,
)
from app.storage import RecoveryRequired, StorageError
from app.timekeeping import local_datetime, named_zone
from app.ui.budgets import BudgetView
from app.ui.datetime_fields import DateTimeFields
from app.ui.dialogs import show_dialog
from app.ui.tree_style import compact_tree_theme, compact_tree_tile
from app.version import release_identity

logger = logging.getLogger(__name__)
FOLDER_KEY = "textmoney.folder.v1"
LANGUAGE_KEY = "textmoney.language.v1"
THEME_KEY = "textmoney.theme.v1"
TIMEZONE_KEY = "textmoney.timezone.v1"
BACKUP_CLEANUP_KEY = "textmoney.backup-cleanup.v1"
DEFAULT_FEE_KEY = "textmoney.default-fee-category.v1"
FEE_CATEGORIES_KEY = "textmoney.fee-categories.v1"
COMPACT_MAX_WIDTH = 599
MEDIUM_MAX_WIDTH = 1023
MAX_CONTENT_WIDTH = 1180


class ScreenClass(StrEnum):
    COMPACT = "compact"
    MEDIUM = "medium"
    EXPANDED = "expanded"


def screen_class(width: float | None) -> ScreenClass:
    if width is None or width <= 0 or width <= COMPACT_MAX_WIDTH:
        return ScreenClass.COMPACT
    if width <= MEDIUM_MAX_WIDTH:
        return ScreenClass.MEDIUM
    return ScreenClass.EXPANDED


class FinanceView(ft.Row):
    def __init__(self) -> None:
        super().__init__(expand=True, spacing=0, alignment=ft.MainAxisAlignment.CENTER)
        self.journal: Journal | None = None
        self.preferences: ft.SharedPreferences | DesktopPreferences | None = None
        self.bridge: FinanceSaf | None = None
        self.worker: Future[None] | None = None
        self.lock = asyncio.Lock()
        self.language = "en"
        self.manual_language = False
        self.system_language_name = "en"
        self.theme_name = "system"
        self.timezone_name = "system"
        self.auto_backup_cleanup = True
        self.fee_preferences = FeePreferences()
        self.fee_branches_draft = self.fee_preferences.branches
        self.possible_conflict_copies: list[str] = []
        self.sibling_scan_failed = False
        self.system_zone = "system"
        self.screen_class = ScreenClass.COMPACT
        self.current_screen = "home"
        self.settings_return_screen = "home"
        self.message_key = "no_folder"
        self.error_message: str | None = None
        self.diagnostics_visible = False
        self.startup_complete = False
        self.startup_spinner = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.ProgressRing(),
        )

        self.message = ft.Text("", selectable=True)
        self.folder_label = ft.Text("", selectable=True)
        self.access = ft.Text("")
        self.balance_warning = ft.Text("", color=ft.Colors.ERROR, visible=False)
        self.external_notice = ft.Column(spacing=4, visible=False)
        self.conflict_copy_notice = ft.Text("", selectable=True, visible=False)
        self.account_root = ft.Dropdown(
            value="Assets", expand=True, on_select=self._account_type_changed
        )
        self.default_fee = ft.Dropdown(expand=True)
        self.fee_category_choices = ft.Column(spacing=0)
        self.fee_choices_state: tuple[tuple[str, str, bool, bool], ...] = ()
        self.fee_category_settings = ft.ExpansionTile(
            title="", controls=[self.fee_category_choices], maintain_state=True
        )
        self.default_fee_save = ft.Button(on_click=self._save_default_fee)
        self.default_fee_notice = ft.Text("", visible=False)
        self.backup_cleanup = ft.Checkbox(value=True, on_change=self._backup_cleanup_changed)
        self.backup_clean_button = ft.Button(on_click=self._preview_backup_cleanup)
        self.default_fee_source = ""
        self.default_fee_original: str | None = None
        self.default_fee_journal: Journal | None = None
        self.account_id = ft.TextField(width=320)
        self.account_name = ft.TextField(width=320, keyboard_type=ft.KeyboardType.NAME)
        self.currency = ft.TextField(width=320, value="UAH")
        self.opening = ft.TextField(width=320)
        self.category_id = ft.TextField(width=320)
        self.category_name = ft.TextField(width=320)
        self.category_root = ft.Dropdown(value="Expenses", expand=True)
        self.category_parent = ft.Dropdown(value="", expand=True)
        self.account_suggestion_status = ft.Text(size=12, color=ft.Colors.OUTLINE)
        self.category_suggestion_status = ft.Text(size=12, color=ft.Colors.OUTLINE)
        self.account_suggestion_alternatives = ft.Dropdown(
            expand=True,
            visible=False,
            on_select=self._account_suggestion_selected,
        )
        self.category_suggestion_alternatives = ft.Dropdown(
            width=320,
            visible=False,
            on_select=self._category_suggestion_selected,
        )
        self.account_suggestion_regenerate = ft.TextButton(
            disabled=True, on_click=self._regenerate_account_suggestion
        )
        self.category_suggestion_regenerate = ft.TextButton(
            disabled=True, on_click=self._regenerate_category_suggestion
        )
        self.account_code_manual = False
        self.category_code_manual = False
        self.account_id.on_change = self._account_code_changed
        self.account_name.on_change = self._account_attributes_changed
        self.currency.on_change = self._account_attributes_changed
        self.category_id.on_change = self._category_code_changed
        self.category_name.on_change = self._category_attributes_changed
        self.category_parent.on_select = self._category_attributes_changed
        self.category_root.on_select = self._category_type_changed
        self.operating_currencies = ft.TextField(width=320)
        self.operating_save = ft.Button(on_click=self._save_operating_currencies)
        self.operating_cancel = ft.TextButton(on_click=self._reset_operating_currencies)
        self.advanced_settings = ft.ExpansionTile(
            title=self.tr("advanced"),
            controls=[
                ft.Text(self.tr("crud_operating_help")),
                self.operating_currencies,
                ft.Text(self.tr("display_currency_codes"), size=12),
                ft.Row([self.operating_save, self.operating_cancel], wrap=True),
            ],
            maintain_state=True,
            controls_padding=16,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
        )
        self.operating_source = ""
        self.operating_original = ""
        self.operating_journal: Journal | None = None
        self.account_list = ft.Column(spacing=4)
        self.category_list = ft.Column(spacing=4)
        self.category_expansion: dict[str, bool] = {}
        self.history = ft.Column(spacing=0)
        today = self._today()
        self.report_period = ft.Dropdown(
            expand=True,
            value="current_month",
            on_select=self._report_period_changed,
        )
        self.report_start = DateTimeFields(
            self.language,
            self.system_zone,
            date(today.year, today.month, 1),
            compact=True,
            show_accounting_date=True,
        )
        self.report_end = DateTimeFields(
            self.language,
            self.system_zone,
            today,
            compact=True,
            show_accounting_date=True,
        )
        self.report_start.occurred.on_change = self._report_date_changed
        self.report_end.occurred.on_change = self._report_date_changed
        self.report_range = ft.Row(
            [self.report_start, self.report_end],
            wrap=True,
            spacing=12,
            run_spacing=8,
            visible=False,
        )
        self.report_error = ft.Text(color=ft.Colors.ERROR, visible=False)
        self.report_summary = ft.Column(
            spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
        self.report_categories = ft.Column(spacing=16)
        self.report_overview = ft.Column(
            spacing=16,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Row([self.report_period]),
                self.report_range,
                self.report_error,
                self.report_summary,
                ft.Container(content=self.report_categories, padding=ft.Padding.only(bottom=96)),
            ],
        )
        self._report_ledger: object | None = None
        self._report_range: tuple[date, date] | None = None
        self._report_data: PeriodReport | None = None
        self.budgets = BudgetView(self)
        self.budgets.visible = False
        self.report_tab_bar = ft.TabBar(tabs=[], scrollable=False, secondary=True)
        self.report_tabs = ft.Tabs(
            length=2,
            selected_index=0,
            on_change=self._report_tab_changed,
            content=ft.Column(
                [self.report_tab_bar, self.report_overview, self.budgets], spacing=16
            ),
        )
        self.reports_screen = ft.Column([self.report_tabs], spacing=16)
        self.home_balances = ft.Row(wrap=True, spacing=12, run_spacing=12)
        self.home_activity = ft.Column(spacing=0)
        self.accounts_panel_content = ft.Column(spacing=16)
        self.categories_panel_content = ft.Column(spacing=16)
        self.accounts_panel = ft.Container(
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=12,
            content=self.accounts_panel_content,
        )
        self.categories_panel = ft.Container(
            theme=compact_tree_theme(),
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=12,
            content=self.categories_panel_content,
            visible=False,
        )
        self.budgets_panel = ft.Container(
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=12,
            content=self.budgets.management,
            visible=False,
        )
        self.account_save = ft.Button(icon=ft.Icons.ADD, on_click=self._create_account)
        self.category_save = ft.Button(icon=ft.Icons.ADD, on_click=self._create_category)
        self.accounting_tab_bar = ft.TabBar(tabs=[], scrollable=False, secondary=True)
        self.accounting_tabs = ft.Tabs(
            length=3,
            selected_index=0,
            on_change=self._accounting_tab_changed,
            content=ft.Column(
                [
                    self.accounting_tab_bar,
                    self.accounts_panel,
                    self.categories_panel,
                    self.budgets_panel,
                ],
                spacing=16,
            ),
        )
        self.fab = ft.FloatingActionButton(
            icon=ft.Icons.ADD,
            on_click=self._new_transaction,
            disabled=True,
        )
        self.select_button = ft.Button(on_click=self._select)
        self.welcome_select_button = ft.Button(on_click=self._select)
        self.reload_button = ft.Button(on_click=self._reload)
        self.diagnostics_toggle = ft.TextButton(on_click=self._diagnostics)
        self.diagnostics = ft.Column(visible=False, spacing=12)
        self.language_choice = ft.Dropdown(
            options=[],
            value="system",
            on_select=self._language_changed,
        )
        self.theme_choice = ft.Dropdown(
            options=[],
            value=self.theme_name,
            on_select=self._theme_changed,
        )
        self.timezone_choice = ft.Dropdown(
            width=320,
            value="system",
            editable=True,
            enable_filter=True,
            options=[
                ft.DropdownOption(key="system", text=self.tr("timezone_system")),
                *[ft.DropdownOption(key=name, text=name) for name in sorted(available_timezones())],
            ],
        )
        self.timezone_save = ft.Button(on_click=self._save_timezone)

        self.welcome_content = ft.Column(spacing=12)
        self.welcome = ft.Container(
            padding=20,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=16,
            content=self.welcome_content,
        )
        self.home_screen = ft.Column(spacing=20)
        self.transactions_screen = ft.Column(spacing=12)
        self.accounts_screen = ft.Column(spacing=16)
        self.settings_screen = ft.Column(spacing=16)
        self.screen_host = ft.Container(expand=True, content=self.home_screen)

        self.page_title = ft.Text(size=25, weight=ft.FontWeight.W_600)
        version, build = release_identity()
        self.release_label = ft.Text(f"v{version} ({build})", size=12, color=ft.Colors.OUTLINE)
        self.settings_button = ft.IconButton(
            icon=ft.Icons.SETTINGS_OUTLINED,
            icon_size=20,
            on_click=self._open_settings,
        )
        self.settings_back = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            on_click=self._back_from_settings,
            visible=False,
        )
        self.header_titles = ft.Row(
            [self.page_title, self.release_label],
            expand=True,
            wrap=True,
            run_spacing=4,
        )
        self.nav_rail = ft.NavigationRail(
            min_width=78,
            min_extended_width=224,
            selected_index=0,
            on_change=self._rail_changed,
            destinations=[],
        )
        self.navigation_bar = ft.NavigationBar(
            selected_index=0,
            on_change=self._bar_changed,
            destinations=[],
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
        )
        self.body = ft.Column(
            expand=True,
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20, vertical=16),
                    content=ft.Row(
                        [self.settings_back, self.header_titles, self.settings_button],
                    ),
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20, vertical=8),
                    content=self.message,
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20), content=self.balance_warning
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20), content=self.external_notice
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20), content=self.conflict_copy_notice
                ),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.symmetric(horizontal=20, vertical=12),
                    content=self.screen_host,
                ),
            ],
        )
        self.controls.append(self.startup_spinner)
        self.forms = [
            self.account_id,
            self.account_root,
            self.account_name,
            self.currency,
            self.opening,
            self.category_parent,
            self.category_id,
            self.category_name,
            self.operating_currencies,
            self.default_fee,
            self.default_fee_save,
            self.fee_category_settings,
        ]
        self._refresh_labels()
        self._refresh()

    def did_mount(self) -> None:
        self.page.padding = 0
        self.page.on_resize = self._resized
        self.page.on_locale_change = self._host_locale_changed
        self.worker = self.page.run_task(self._lifecycle)
        self._apply_layout(self.page.width or self.page.window.width)
        self.page.update()

    def will_unmount(self) -> None:
        self.budgets.cancel()
        if self.worker is not None:
            self.worker.cancel()

    def tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _set_message(self, key: str) -> None:
        self.message_key = key
        self.error_message = None
        self.message.value = self.tr(key)

    def _set_error(self, exc: Exception) -> None:
        if isinstance(exc, (ConflictError, LedgerError, PreferencesError, StorageError)):
            self.message_key = ""
            self.error_message = str(exc)
            logger.warning("Journal operation refused (%s)", type(exc).__name__)
        elif isinstance(exc, TimeoutError):
            self.message_key = "error_timeout"
            self.error_message = None
            if self.journal is not None:
                self.journal.ready = False
                self.journal.storage.loaded = False
            logger.warning("Journal transport timed out")
        else:
            self.message_key = "error_unexpected"
            self.error_message = None
            if self.journal is not None:
                self.journal.ready = False
                self.journal.storage.loaded = False
            logger.exception("Unexpected journal operation failure")
        self.message.value = (
            localized_error(self.language, self.error_message)
            if self.error_message is not None
            else self.tr(self.message_key)
        )

    def _refresh_labels(self) -> None:
        self.account_root.label = self.tr("transfer_account_type")
        self.account_root.options = [
            ft.DropdownOption(key="Assets", text=self.tr("transfer_asset")),
            ft.DropdownOption(key="Liabilities", text=self.tr("transfer_liability")),
        ]
        self.default_fee.label = self.tr("transfer_default_fee")
        self.fee_category_settings.title = self.tr("fee_categories")
        self._render_fee_settings()
        self.default_fee_save.content = self.tr("crud_save")
        self.page_title.value = self.tr(
            {
                "home": "nav_home",
                "transactions": "nav_transactions",
                "accounts": "nav_accounts",
                "reports": "nav_reports",
                "settings": "nav_settings",
            }[self.current_screen]
        )
        self.message.value = (
            localized_error(self.language, self.error_message)
            if self.error_message is not None
            else self.tr(self.message_key)
        )
        self.account_id.label = self.tr("account_code")
        self.account_id.hint_text = self.tr("account_code_hint")
        self.account_name.label = self.tr("account_name")
        self.currency.label = self.tr("currency")
        self.opening.label = self.tr(
            "transfer_owed" if self.account_root.value == "Liabilities" else "opening_balance"
        )
        self.category_id.label = self.tr("category_path")
        self.category_id.hint_text = self.tr("category_path_hint")
        self.category_name.label = self.tr("category_name")
        self.category_parent.label = self.tr("code_suggestion_parent")
        self.account_suggestion_alternatives.label = self.tr("code_suggestion_alternatives")
        self.category_suggestion_alternatives.label = self.tr("code_suggestion_alternatives")
        self.account_suggestion_regenerate.content = self.tr("code_suggestion_regenerate")
        self.category_suggestion_regenerate.content = self.tr("code_suggestion_regenerate")
        self.operating_currencies.label = self.tr("crud_operating")
        self.advanced_settings.title = self.tr("advanced")
        self.advanced_settings.controls = [
            ft.Text(self.tr("crud_operating_help")),
            self.operating_currencies,
            ft.Text(self.tr("display_currency_codes"), size=12),
            ft.Row([self.operating_save, self.operating_cancel], wrap=True),
        ]
        self.operating_save.content = self.tr("crud_save")
        self.operating_save.disabled = not (self.journal and self.journal.ready)
        self.operating_cancel.content = self.tr("crud_cancel")
        self.operating_cancel.disabled = self.operating_save.disabled
        self.account_save.content = self.tr("create_account")
        self.category_save.content = self.tr("create_category")
        self.select_button.content = self.tr(
            "change_folder" if self.journal is not None else "select_folder"
        )
        self.welcome_select_button.content = self.tr("select_folder")
        self.reload_button.content = self.tr("reload")
        self.language_choice.label = self.tr("language")
        self.language_choice.options = [
            ft.DropdownOption(key="system", text=self.tr("language_system")),
            *[
                ft.DropdownOption(key=code, text=translate(code, "language_name"))
                for code in LANGUAGES
            ],
        ]
        self.language_choice.value = self.language if self.manual_language else "system"
        self.report_period.label = self.tr("report_period")
        self.report_period.options = [
            ft.DropdownOption(key="current_month", text=self.tr("report_current_month")),
            ft.DropdownOption(key="previous_month", text=self.tr("report_previous_month")),
            ft.DropdownOption(key="current_year", text=self.tr("report_current_year")),
            ft.DropdownOption(key="custom", text=self.tr("report_custom_range")),
        ]
        self.report_start.set_language(self.language)
        self.report_end.set_language(self.language)
        self.report_start.occurred.label = self.tr("report_start_date")
        self.report_end.occurred.label = self.tr("report_end_date")
        self.theme_choice.label = self.tr("appearance")
        self.theme_choice.options = [
            ft.DropdownOption(key="system", text=self.tr("theme_system")),
            ft.DropdownOption(key="light", text=self.tr("theme_light")),
            ft.DropdownOption(key="dark", text=self.tr("theme_dark")),
        ]
        self.theme_choice.value = self.theme_name
        self.timezone_choice.label = self.tr("timezone")
        self.timezone_choice.options[0].text = self.tr("timezone_system")
        self.timezone_save.content = self.tr("crud_save")
        self.diagnostics_toggle.content = self.tr(
            "diagnostics_hide" if self.diagnostics_visible else "diagnostics_show"
        )
        self._build_navigation()
        self._build_screens()
        self.budgets.sync()
        if self.current_screen == "reports" and self.report_tabs.selected_index == 1:
            self.budgets.request_refresh()

    def _build_navigation(self) -> None:
        destinations = [
            ("nav_home", ft.Icons.HOME_OUTLINED, ft.Icons.HOME),
            ("nav_transactions", ft.Icons.RECEIPT_LONG_OUTLINED, ft.Icons.RECEIPT_LONG),
            (
                "nav_accounts",
                ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED,
                ft.Icons.ACCOUNT_BALANCE_WALLET,
            ),
            ("nav_reports", ft.Icons.INSIGHTS, ft.Icons.INSIGHTS),
            ("nav_settings", ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS),
        ]
        self.navigation_bar.destinations = [
            ft.NavigationBarDestination(
                label=self.tr(label), icon=icon, selected_icon=selected_icon
            )
            for label, icon, selected_icon in destinations[:-1]
        ]
        self.navigation_bar.selected_index = (
            ("home", "transactions", "accounts", "reports").index(self.settings_return_screen)
            if self.current_screen == "settings"
            else self._selected_index()
        )
        self.nav_rail.destinations = [
            ft.NavigationRailDestination(
                label=self.tr(label), icon=icon, selected_icon=selected_icon
            )
            for label, icon, selected_icon in destinations
        ]
        self.nav_rail.selected_index = self._selected_index()
        compact = self.screen_class == ScreenClass.COMPACT
        self.settings_button.visible = compact and self.current_screen != "settings"
        self.settings_button.tooltip = self.tr("nav_settings")
        self.settings_back.visible = compact and self.current_screen == "settings"
        self.settings_back.tooltip = self.tr("nav_back")

    def _build_screens(self) -> None:
        version, build = release_identity()
        self.welcome_content.controls = [
            ft.Text(self.tr("welcome_title"), size=22, weight=ft.FontWeight.W_600),
            ft.Text(self.tr("welcome_body"), selectable=True),
            ft.Text(self.tr("welcome_step")),
            self.welcome_select_button,
            ft.TextButton(
                self.tr("create_account"),
                icon=ft.Icons.PERSON_ADD,
                on_click=self._go_accounts,
                disabled=not (self.journal and self.journal.ready),
            ),
        ]
        ready = self.journal is not None and self.journal.ready
        self.welcome.visible = not ready or not bool(self.journal and self.journal.ledger.accounts)
        self.home_screen.controls = [
            self.welcome,
            ft.Text(self.tr("home_balances"), size=21, weight=ft.FontWeight.W_600),
            self.home_balances,
            ft.Text(self.tr("home_recent"), size=21, weight=ft.FontWeight.W_600),
            self.home_activity,
        ]
        self.fab.content = self.tr("add_transaction")
        self.fab.disabled = not ready
        self.transactions_screen.controls = [
            ft.Text(self.tr("transactions_title"), size=21, weight=ft.FontWeight.W_600),
            ft.Container(content=self.history, padding=ft.Padding.only(bottom=96)),
        ]
        self.accounts_panel_content.controls = [
            ft.Row([self.account_save], alignment=ft.MainAxisAlignment.END, wrap=True),
            self.account_list,
        ]
        self.categories_panel_content.controls = [
            ft.Row([self.category_save], alignment=ft.MainAxisAlignment.END, wrap=True),
            self.category_list,
        ]
        self.accounting_tab_bar.tabs = [
            ft.Tab(label=self.tr("accounts_title")),
            ft.Tab(label=self.tr("categories_title")),
            ft.Tab(label=self.tr("budget_title")),
        ]
        self.report_tab_bar.tabs = [
            ft.Tab(label=self.tr("report_overview")),
            ft.Tab(label=self.tr("budget_title")),
        ]
        self.accounts_screen.controls = [self.accounting_tabs]
        self.category_root.label = self.tr("incoming_type")
        self.category_root.options = [
            ft.DropdownOption(key="Expenses", text=self.tr("kind_expense")),
            ft.DropdownOption(key="Income", text=self.tr("kind_income")),
        ]
        self._refresh_category_parent_options()
        folder_ready = self.journal is not None and self.journal.ready
        self.access.value = self.tr("access_ok") if folder_ready else self.tr("access_unchecked")
        self.folder_label.value = (
            self.journal.storage.folder.root.as_posix()
            if self.journal is not None and isinstance(self.journal.storage.folder, DesktopFolder)
            else self.tr("no_folder")
            if self.journal is None
            else self.tr("folder_selected")
        )
        self.settings_screen.controls = [
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("storage_section"), size=18),
                        ft.Text(self.tr("storage_explanation")),
                        ft.Text(self.tr("storage_filename")),
                        self.folder_label,
                        self.access,
                        ft.Row([self.select_button, self.reload_button], wrap=True),
                        ft.TextButton(
                            self.tr("recovery_review"),
                            on_click=self._review_recovery,
                            visible=self.journal is not None
                            and self.journal.storage.recovery is not None
                            and self.journal.storage.recovery.required,
                        ),
                        ft.Divider(),
                        ft.Text(self.tr("backups_title"), size=18),
                        self.backup_cleanup,
                        ft.Text(self.tr("backups_policy")),
                        ft.Text(self.tr("backups_protected"), size=12),
                        self.backup_clean_button,
                    ],
                    spacing=12,
                ),
            ),
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("language"), size=18),
                        self.language_choice,
                        ft.Text(self.tr("appearance"), size=18),
                        self.theme_choice,
                        ft.Text(self.tr("timezone"), size=18),
                        ft.Text(self.tr("timezone_help")),
                        self.timezone_choice,
                        self.timezone_save,
                        self.fee_category_settings,
                        ft.Text(self.tr("fee_categories_help"), size=12),
                        ft.Row([self.default_fee]),
                        self.default_fee_save,
                        self.default_fee_notice,
                    ],
                    spacing=12,
                ),
            ),
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("about"), size=18),
                        ft.Text(self.tr("about_body")),
                        ft.Text(f"{self.tr('app_version')}: {version} ({build})"),
                    ],
                    spacing=12,
                ),
            ),
            self.advanced_settings,
            self.diagnostics_toggle,
            self.diagnostics,
        ]
        self.backup_cleanup.label = self.tr("backups_automatic")
        self.backup_cleanup.value = self.auto_backup_cleanup
        self.backup_clean_button.content = self.tr("backups_clean")
        self.backup_clean_button.disabled = not folder_ready
        screens = {
            "home": self.home_screen,
            "transactions": self.transactions_screen,
            "accounts": self.accounts_screen,
            "reports": self.reports_screen,
            "settings": self.settings_screen,
        }
        self.screen_host.content = screens[self.current_screen]

    def _selected_index(self) -> int:
        return {
            "home": 0,
            "transactions": 1,
            "accounts": 2,
            "reports": 3,
            "settings": 4,
        }[self.current_screen]

    def _apply_layout(self, width: float | None) -> None:
        self.screen_class = screen_class(width)
        if not self.startup_complete:
            self.controls = [self.startup_spinner]
            if self.page:
                self.page.navigation_bar = None
                self.page.floating_action_button = None
            return
        if self.screen_class == ScreenClass.COMPACT:
            self.controls = [self.body]
            self.body.width = width
            if self.page:
                self.page.navigation_bar = (
                    None if self.current_screen == "settings" else self.navigation_bar
                )
            rail_width = 0
        else:
            expanded = self.screen_class == ScreenClass.EXPANDED
            rail_width = 236 if expanded else 90
            self.nav_rail.extended = expanded
            self.nav_rail.label_type = ft.NavigationRailLabelType.ALL if not expanded else None
            self.body.width = min(MAX_CONTENT_WIDTH, max(320, (width or 900) - rail_width))
            self.controls = [
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=5),
                    content=self.nav_rail,
                ),
                ft.VerticalDivider(width=1),
                self.body,
            ]
            if self.page:
                self.page.navigation_bar = None
        available = max(320, (width or 390) - rail_width - 48)
        self.accounts_panel.width = self.categories_panel.width = self.budgets_panel.width = min(
            MAX_CONTENT_WIDTH - 40, available
        )
        self._build_navigation()
        self._build_home_layout()
        self._update_transaction_separators()
        self.screen_host.padding = (
            ft.Padding.only(bottom=96)
            if self.current_screen in {"home", "accounts"}
            or (self.current_screen == "reports" and self.report_tabs.selected_index == 1)
            or (self.current_screen == "settings" and self.screen_class != ScreenClass.COMPACT)
            else None
        )
        if self.page:
            self.fab.content = self.tr("add_transaction")
            self.fab.disabled = not (self.journal and self.journal.ready)
            self.page.floating_action_button = (
                None
                if self.screen_class == ScreenClass.COMPACT and self.current_screen == "settings"
                else self.fab
            )

    def _build_home_layout(self) -> None:
        if self.screen_class == ScreenClass.EXPANDED:
            self.home_screen.controls = [
                self.welcome,
                ft.Row(
                    [
                        ft.Container(
                            expand=3,
                            content=ft.Column(
                                [
                                    ft.Text(
                                        self.tr("home_balances"),
                                        size=21,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    self.home_balances,
                                ],
                                spacing=12,
                            ),
                        ),
                        ft.Container(
                            expand=2,
                            content=ft.Column(
                                [
                                    ft.Text(
                                        self.tr("home_recent"),
                                        size=21,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    self.home_activity,
                                ],
                                spacing=12,
                            ),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    spacing=24,
                ),
            ]
        elif self.current_screen == "home":
            self.home_screen.controls = [
                self.welcome,
                ft.Text(self.tr("home_balances"), size=21, weight=ft.FontWeight.W_600),
                self.home_balances,
                ft.Text(self.tr("home_recent"), size=21, weight=ft.FontWeight.W_600),
                self.home_activity,
            ]

    def _refresh(self) -> None:
        self._refresh_external_notices()
        ready = self.journal is not None and self.journal.ready
        for control in (self.account_save, self.category_save):
            control.disabled = not ready
        self.reload_button.disabled = self.journal is None
        self.operating_currencies.disabled = not ready
        self.default_fee.disabled = self.default_fee_save.disabled = not ready
        self.fee_category_settings.disabled = not ready
        self.balance_warning.visible = False
        if not ready or self.journal is None:
            self.account_list.controls = [ft.Text(self.tr("accounts_empty"))]
            self.category_list.controls = [ft.Text(self.tr("categories_empty"))]
            self.history.controls = [ft.Text(self.tr("transactions_empty"))]
            self.home_balances.controls = [ft.Text(self.tr("home_no_accounts"))]
            self.home_activity.controls = [ft.Text(self.tr("home_no_transactions"))]
            self._refresh_labels()
            self._render_reports()
            return

        ledger = self.journal.ledger
        warnings = ledger.balance_warnings(today=self.journal.today())
        self.balance_warning.visible = bool(warnings)
        self.balance_warning.value = (
            self.tr("transfer_negative")
            + " "
            + ", ".join(a.name for a in ledger.accounts if a.id in warnings)
        )
        if self.default_fee_journal is not self.journal or (
            self.default_fee.value == self.default_fee_original
            and self.fee_branches_draft == self.fee_preferences.branches
        ):
            self.default_fee.value = ledger.default_fee_category
            self.default_fee_original = ledger.default_fee_category
            self.default_fee_journal = self.journal
            self.default_fee_source = self.journal.source
            self.fee_branches_draft = self.fee_preferences.branches
        if (
            self.operating_journal is not self.journal
            or self.operating_currencies.value == self.operating_original
        ):
            self.operating_original = " ".join(ledger.operating_currencies)
            self.operating_currencies.value = self.operating_original
            self.operating_source = self.journal.source
            self.operating_journal = self.journal
        self.fab.disabled = False
        balances = ledger.balances(today=self.journal.today())
        grouped: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
        for account in ledger.accounts:
            grouped[account.currency].append((account.name, balances[account.id]))
        currency_order = {
            currency: index for index, currency in enumerate(ledger.operating_currencies)
        }
        self.home_balances.controls = [
            ft.Container(
                width=250,
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("currency_group", currency=currency), size=16),
                        *[
                            ft.Row(
                                [
                                    ft.Text(name, expand=True),
                                    ft.Text(format_amount(amount, self.language, currency)),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            )
                            for name, amount in sorted(accounts)
                        ],
                    ],
                    spacing=10,
                ),
            )
            for currency, accounts in sorted(
                grouped.items(),
                key=lambda item: (currency_order.get(item[0], len(currency_order)), item[0]),
            )
        ] or [ft.Text(self.tr("home_no_accounts"))]
        names = {account.id: account.name for account in ledger.accounts}
        names.update({category.id: category.name for category in ledger.categories})
        transactions = sorted(
            ledger.transactions,
            key=self._transaction_sort_key,
            reverse=True,
        )
        self.history.controls = self._transaction_groups(transactions, names) or [
            ft.Text(self.tr("transactions_empty"))
        ]
        self.home_activity.controls = self._transaction_groups(
            transactions[:5], names, compact=True
        ) or [ft.Text(self.tr("home_no_transactions"))]
        self._update_transaction_separators()
        self.account_list.controls = [
            self._entity_card(account, balances[account.id]) for account in ledger.accounts
        ] or [ft.Text(self.tr("accounts_empty"))]
        self.category_list.controls = self._category_tree(ledger.categories)
        self._refresh_labels()
        self._build_home_layout()
        if self.current_screen == "reports":
            self._render_reports()

    def _report_dates(self) -> tuple[date, date]:
        preset = self.report_period.value or "current_month"
        if preset == "custom":
            start = date.fromisoformat(self.report_start.values()[0])
            end = date.fromisoformat(self.report_end.values()[0])
            return start, end
        return period_bounds(preset, self._today())

    def _render_reports(self) -> None:
        self.report_range.visible = self.report_period.value == "custom"
        self.report_error.visible = False
        self.report_error.value = ""
        if self.journal is None or not self.journal.ready:
            self.report_summary.controls = [ft.Text(self.tr("report_unavailable"))]
            self.report_categories.controls = []
            return
        try:
            start, end = self._report_dates()
            if end < start:
                raise LedgerError("report_date_range")
        except LedgerError as exc:
            self.report_error.value = localized_error(self.language, str(exc))
            self.report_error.visible = True
            self.report_summary.controls = []
            self.report_categories.controls = []
            return

        ledger = self.journal.ledger
        if ledger is not self._report_ledger or (start, end) != self._report_range:
            self._report_data = period_report(ledger, start, end)
            self._report_ledger = ledger
            self._report_range = (start, end)
        report = self._report_data
        if report is None:
            raise RuntimeError("Report cache was not initialized.")
        if not report.currencies:
            self.report_summary.controls = [ft.Text(self.tr("report_empty_period"))]
            self.report_categories.controls = []
            return

        def metric(label_key: str, value: Decimal, currency: str) -> ft.Control:
            return ft.Container(
                width=160,
                content=ft.Column(
                    [
                        ft.Text(self.tr(label_key), size=13),
                        ft.Text(format_amount(value, self.language, currency), size=18),
                    ],
                    spacing=4,
                ),
            )

        self.report_summary.controls = [
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("currency_group", currency=item.currency), size=18),
                        ft.Row(
                            [
                                metric("report_income", item.income, item.currency),
                                metric("report_expenses", item.expenses, item.currency),
                                metric("report_net", item.net, item.currency),
                            ],
                            wrap=True,
                            spacing=12,
                            run_spacing=8,
                        ),
                    ],
                    spacing=12,
                ),
            )
            for item in report.currencies
        ]

        def percentage(amount: Decimal, total: Decimal) -> str:
            if total == 0:
                return self.tr("report_percentage_unavailable")
            with localcontext() as context:
                context.prec = 50
                value = amount * 100 / total
            return f"{format_amount(value, self.language)}%"

        def category_controls(
            categories: tuple[CategoryReport, ...], currency: str, total: Decimal
        ) -> list[ft.Control]:
            def node(item: CategoryReport) -> ft.Control | None:
                children = [child for sub in item.children if (child := node(sub)) is not None]
                amount = item.amount(currency)
                if amount == 0 and not children:
                    return None
                labels: list[ft.Control] = [
                    ft.Text(item.name, expand=True),
                    ft.Text(format_amount(amount, self.language, currency)),
                    ft.Text(percentage(amount, total), width=72, text_align=ft.TextAlign.RIGHT),
                ]
                if not item.active:
                    labels[0] = ft.Column(
                        [labels[0], ft.Text(self.tr("crud_inactive_label"), size=12)],
                        spacing=0,
                        expand=True,
                    )
                title = ft.Row(labels, spacing=8)
                if children:
                    return compact_tree_tile(ft.ExpansionTile(title=title, controls=children))
                return ft.Container(
                    padding=0,
                    content=title,
                )

            return [item for category in categories if (item := node(category)) is not None]

        def category_section(
            title_key: str,
            categories: tuple[CategoryReport, ...],
            unassigned: tuple[tuple[str, Decimal], ...],
            currency: str,
            total: Decimal,
        ) -> ft.Control:
            controls: list[ft.Control] = [
                ft.Text(self.tr(title_key), size=17, weight=ft.FontWeight.W_600)
            ]
            controls.extend(category_controls(categories, currency, total))
            unassigned_amount = dict(unassigned).get(currency, Decimal("0"))
            if unassigned_amount != 0:
                controls.append(
                    ft.Row(
                        [
                            ft.Text(self.tr("report_uncategorized"), expand=True),
                            ft.Text(format_amount(unassigned_amount, self.language, currency)),
                            ft.Text(
                                percentage(unassigned_amount, total),
                                width=72,
                                text_align=ft.TextAlign.RIGHT,
                            ),
                        ],
                        spacing=8,
                    )
                )
            if len(controls) == 1:
                controls.append(ft.Text(self.tr("report_categories_empty")))
            return ft.Column(controls, spacing=0)

        self.report_categories.controls = [
            ft.Container(
                theme=compact_tree_theme(),
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("currency_group", currency=item.currency), size=18),
                        category_section(
                            "report_expense_categories",
                            report.expense_categories,
                            report.unassigned_expenses,
                            item.currency,
                            item.expenses,
                        ),
                        ft.Divider(),
                        category_section(
                            "report_income_categories",
                            report.income_categories,
                            report.unassigned_income,
                            item.currency,
                            item.income,
                        ),
                    ],
                    spacing=12,
                ),
            )
            for item in report.currencies
        ]

    def _report_period_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._render_reports()
        if self.page:
            self.page.update()

    def _report_date_changed(self, event: ft.Event[ft.TextField]) -> None:
        if self.report_period.value == "custom":
            self._render_reports()
            if self.page:
                self.page.update()

    def _category_label(self, identifier: str, name: str) -> str:
        depth = identifier.count(":") - 1
        label = f"{'  ' * depth}{name}"
        return f"{self.tr('kind_income')} · {label}" if identifier.startswith("Income:") else label

    def _category_tree(self, categories: tuple[Category, ...]) -> list[ft.Control]:
        items = {item.id: item for item in categories}
        children: dict[str, set[str]] = defaultdict(set)
        for item in categories:
            parts = item.id.split(":")
            for length in range(1, len(parts)):
                children[":".join(parts[:length])].add(":".join(parts[: length + 1]))

        def changed(event: ft.Event[ft.ExpansionTile]) -> None:
            self.category_expansion[str(event.control.data)] = event.data is True

        def node(identifier: str) -> ft.Control:
            item = items.get(identifier)
            root = identifier in {"Expenses", "Income"}
            title = (
                self.tr("categories_expenses" if identifier == "Expenses" else "categories_income")
                if root
                else item.name
                if item
                else identifier.rsplit(":", 1)[-1]
            )
            labels: list[ft.Control] = [
                ft.Text(title, size=14, weight=ft.FontWeight.W_600 if root else None)
            ]
            if item and not item.active:
                labels.append(ft.Text(self.tr("crud_inactive_label"), size=12))
            row = ft.Row(
                [
                    ft.Column(labels, spacing=0, expand=True),
                    *(self._entity_actions(item) if item else []),
                ],
                spacing=0,
            )
            descendants = sorted(children.get(identifier, ()))
            if descendants or root:
                return compact_tree_tile(
                    ft.ExpansionTile(
                        data=identifier,
                        title=row,
                        expanded=self.category_expansion.get(identifier, root),
                        on_change=changed,
                        controls=[node(child) for child in descendants]
                        or [ft.Text(self.tr("categories_empty"), size=12)],
                    )
                )
            return ft.Container(
                data=identifier,
                padding=0,
                content=row,
            )

        return [node("Expenses"), node("Income")]

    def _entity_actions(self, item: Account | Category) -> list[ft.Control]:
        kind = "category" if isinstance(item, Category) else "account"
        return [
            ft.IconButton(
                icon=ft.Icons.EDIT_OUTLINED,
                tooltip=self.tr("crud_edit"),
                data=(kind, item.id),
                style=ft.ButtonStyle(visual_density=ft.VisualDensity.COMPACT)
                if kind == "category"
                else None,
                on_click=self._edit_entity,
            ),
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                tooltip=self.tr("crud_delete"),
                data=(kind, item.id),
                style=ft.ButtonStyle(visual_density=ft.VisualDensity.COMPACT)
                if kind == "category"
                else None,
                on_click=self._delete_entity,
            ),
        ]

    def _entity_card(self, item: Account | Category, balance: Decimal | None = None) -> ft.Control:
        lines: list[ft.Control] = [
            ft.Text(item.name, size=14),
        ]
        if not item.active:
            lines.append(ft.Text(self.tr("crud_inactive_label")))
        if isinstance(item, Account) and balance is not None:
            lines.append(ft.Text(format_amount(balance, self.language, item.currency), size=14))
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=2),
            content=ft.Row(
                [
                    ft.Column(lines, spacing=0, expand=True),
                    *self._entity_actions(item),
                ],
                spacing=0,
            ),
        )

    def _update_transaction_separators(self) -> None:
        border = (
            ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT))
            if self.screen_class == ScreenClass.EXPANDED
            else None
        )
        for control in self.history.controls:
            if isinstance(control, ft.Container) and isinstance(control.content, ft.Row):
                control.border = border

    @staticmethod
    def _transaction_sort_key(transaction: Transaction) -> tuple[date, datetime, str]:
        return transaction.date, datetime.fromisoformat(transaction.timestamp), transaction.id

    def _transaction_groups(
        self,
        transactions: list[Transaction],
        names: dict[str, str],
        compact: bool = False,
        *,
        read_only: bool = False,
    ) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for day, entries in groupby(transactions, key=lambda item: item.date):
            controls.append(
                ft.Container(
                    padding=ft.Padding.only(top=8, bottom=4),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        self.tr("initial_date")
                        if day == EPOCH
                        else format_date(day, self.language),
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color=ft.Colors.OUTLINE if day == EPOCH else None,
                    ),
                )
            )
            controls.extend(
                self._transaction_card(item, names, compact, read_only=read_only)
                for item in entries
            )
        return controls

    def _transaction_card(
        self,
        transaction: Transaction,
        names: dict[str, str],
        compact: bool = False,
        *,
        read_only: bool = False,
    ) -> ft.Control:
        kind_key = {
            "opening": "kind_opening",
            "expense": "kind_expense",
            "income": "kind_income",
            "reimbursement": "kind_reimbursement",
            "adjustment": "kind_adjustment",
        }.get(transaction.kind, "kind_external")
        summary = " ".join(transaction.narration.split())
        display = (
            self.tr(kind_key) if transaction.kind == "opening" else summary or self.tr(kind_key)
        )
        outgoing = [p for p in transaction.postings if p.amount < 0]
        incoming = [p for p in transaction.postings if p.amount > 0]
        if (
            transaction.kind == "opening"
            and len(transaction.postings) == 2
            and all(p.amount == 0 for p in transaction.postings)
        ):
            outgoing = [p for p in transaction.postings if p.account.startswith("Equity:")]
            incoming = [
                p for p in transaction.postings if p.account.startswith(("Assets:", "Liabilities:"))
            ]
        simple = (
            len(transaction.postings) == 2
            and len(outgoing) == len(incoming) == 1
            and outgoing[0].currency == incoming[0].currency
            and -outgoing[0].amount == incoming[0].amount
        )
        transfer = None
        cashflow = None
        if transaction.kind in {"expense", "income"} and self.journal is not None:
            cashflow = self.journal.ledger.cashflow_details(transaction)
            outgoing, incoming = (
                ([cashflow.account], [cashflow.category])
                if cashflow.kind == "expense"
                else ([cashflow.category], [cashflow.account])
            )
            simple = True
        if transaction.kind == "transfer" and self.journal is not None:
            try:
                transfer = self.journal.transfer_details(transaction)
            except LedgerError as exc:
                if str(exc) != "crud_transaction_readonly":
                    raise
            if transfer:
                outgoing, incoming = [transfer.source], [transfer.target]
                simple = True
        title = display
        if simple:
            source = names.get(
                outgoing[0].account,
                self.tr("kind_adjustment" if transaction.kind == "adjustment" else "kind_opening"),
            )
            target = names.get(incoming[0].account, self.tr("kind_external"))
            if transaction.kind == "opening":
                opened_account = next(
                    p.account
                    for p in transaction.postings
                    if p.account.startswith(("Assets:", "Liabilities:"))
                )
                target = names.get(opened_account, self.tr("kind_external"))
            title = (
                f"{self.tr('kind_opening')} → {target}"
                if transaction.kind == "opening"
                else f"{source} → {target}"
            )
        has_summary = simple and transaction.kind != "opening" and bool(summary)
        heading: list[ft.Control] = [
            ft.Text(
                summary if has_summary else title,
                size=14 if has_summary or not simple else 10,
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        ]
        if simple:
            movement = abs(cashflow.account.amount) if cashflow else incoming[0].amount
            heading.append(
                ft.Text(
                    format_amount(movement, self.language, incoming[0].currency),
                    size=13,
                )
            )
        else:
            heading.append(ft.Text(self.tr("multiple_postings"), size=12))
        lines: list[ft.Control] = [ft.Row(heading, spacing=8)]
        if transfer and transfer.exchange:
            credit = format_amount(transfer.target.amount, self.language, transfer.target.currency)
            lines.append(
                ft.Text(
                    f"{format_amount(transfer.debited, self.language, transfer.source.currency)}"
                    f" → {credit}",
                    size=12,
                )
            )
        fee_details = transfer or cashflow
        if fee_details and fee_details.fee:
            lines.append(
                ft.Text(
                    f"{self.tr('transfer_fee')}: "
                    f"{format_amount(fee_details.fee, self.language, fee_details.fee_currency)}",
                    size=12,
                )
            )
        if has_summary:
            lines.append(
                ft.Text(
                    title,
                    size=10,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )
        actions: list[ft.Control] = (
            []
            if read_only
            else [
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    icon_size=20,
                    tooltip=self.tr("crud_edit"),
                    data=transaction.id,
                    on_click=self._edit_transaction,
                ),
            ]
        )
        if not compact and not read_only:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=20,
                    tooltip=self.tr("crud_delete"),
                    data=transaction.id,
                    on_click=self._delete_transaction,
                )
            )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=2),
            content=ft.Row(
                [ft.Column(lines, spacing=1, expand=True), *actions],
                spacing=0,
            ),
        )

    async def _lifecycle(self) -> None:
        try:
            if self.page.platform in {
                ft.PagePlatform.LINUX,
                ft.PagePlatform.WINDOWS,
                ft.PagePlatform.MACOS,
            }:
                self.preferences = DesktopPreferences()
            else:
                self.preferences = ft.SharedPreferences()
            if self.page.platform == ft.PagePlatform.ANDROID:
                self.bridge = FinanceSaf()
            device = await self.page.get_device_info()
            if device is None:
                raise LedgerError("error_system_language")
            self.system_language_name = system_language(
                [locale.language_tag for locale in device.locales]
            )
            saved_language = await self.preferences.get(LANGUAGE_KEY)
            if isinstance(saved_language, str) and saved_language in LANGUAGES:
                self.manual_language = True
                self.language = saved_language
            else:
                self.manual_language = False
                self.language = self.system_language_name
            configuration = self.page.locale_configuration
            if configuration is not None:
                configuration.current_locale = ft.Locale(language_code=self.language)
            saved_theme = await self.preferences.get(THEME_KEY)
            if isinstance(saved_theme, str) and saved_theme in {"system", "light", "dark"}:
                self.theme_name = saved_theme
                self._apply_theme(saved_theme)
            saved_timezone = await self.preferences.get(TIMEZONE_KEY)
            if isinstance(saved_timezone, str) and saved_timezone != "system":
                named_zone(saved_timezone)
                self.timezone_name = saved_timezone
            self.timezone_choice.value = self.timezone_name
            saved_cleanup = await self.preferences.get(BACKUP_CLEANUP_KEY)
            if saved_cleanup not in {None, "on", "off"}:
                raise PreferencesError(self.tr("backups_invalid_setting"))
            self.auto_backup_cleanup = saved_cleanup != "off"
            fee_settings = await self.preferences.get(FEE_CATEGORIES_KEY)
            if fee_settings is not None:
                if not isinstance(fee_settings, str):
                    raise PreferencesError("fee_categories_invalid")
                self.fee_preferences = FeePreferences.parse(fee_settings)
            else:
                saved_fee = await self.preferences.get(DEFAULT_FEE_KEY)
                if saved_fee is not None:
                    if not isinstance(saved_fee, str) or not saved_fee.startswith("Expenses:"):
                        raise PreferencesError("default_fee_invalid")
                    self.fee_preferences = FeePreferences(default=saved_fee)
            self.fee_branches_draft = self.fee_preferences.branches
            await self._sync_system_zone()
            saved_folder = await self.preferences.get(FOLDER_KEY)
            if isinstance(saved_folder, str):
                async with self.lock:
                    await self._attach(saved_folder)
        except Exception as exc:
            self._set_error(exc)
        self._refresh()
        self.startup_complete = True
        self._apply_layout(self.page.width or self.page.window.width)
        if self.page:
            self.page.update()
        while True:
            await asyncio.sleep(5)
            if self.lock.locked():
                continue
            try:
                await self._sync_system_zone()
            except Exception as exc:
                self._set_error(exc)
                self.page.update()
            if self.lock.locked() or self.journal is None:
                continue
            async with self.lock:
                try:
                    await self._poll_journal()
                except Exception as exc:
                    self.journal.ready = False
                    self.journal.storage.loaded = False
                    self._set_error(exc)
                    self._refresh()
                self.page.update()

    async def _poll_journal(self) -> None:
        journal = self._require_journal()
        await self._scan_conflict_copies()
        try:
            if journal.ready and await journal.poll():
                await self._apply_default_fee()
                self._set_message("external_reloaded")
        finally:
            self._refresh()

    def _effective_zone(self) -> str:
        if self.timezone_name != "system":
            return self.timezone_name
        if self.bridge is not None and self.system_zone == "system":
            raise LedgerError("crud_timezone_unavailable")
        return self.system_zone

    async def _sync_system_zone(self) -> None:
        if self.bridge is None:
            return
        try:
            name = unwrap(await self.bridge.call("timezone"))
            if not isinstance(name, str):
                raise LedgerError("crud_timezone_unavailable")
            named_zone(name)
        except (StorageError, LedgerError, TimeoutError) as exc:
            self.system_zone = "system"
            raise LedgerError("crud_timezone_unavailable") from exc
        if name != self.system_zone:
            self.system_zone = name
            self._refresh()
            self.page.update()

    async def _save_timezone(self, event: ft.Event[ft.Button]) -> None:
        try:
            name = (
                self.timezone_choice.text
                if self.timezone_choice.text is not None
                else self.timezone_choice.value or ""
            ).strip()
            if name in {translate(language, "timezone_system") for language in LANGUAGES}:
                name = "system"
            if name != "system":
                named_zone(name)
            if self.preferences is None:
                raise LedgerError("crud_timezone_save")
            if name == "system" and self.bridge is not None:
                result = unwrap(await self.bridge.call("timezone"))
                if not isinstance(result, str):
                    raise LedgerError("crud_timezone_unavailable")
                named_zone(result)
                self.system_zone = result
            if not await self.preferences.set(TIMEZONE_KEY, name):
                raise LedgerError("crud_timezone_save")
            self.timezone_name = name
            self.timezone_choice.value = name
            self._set_message("timezone_saved")
            self._refresh()
        except Exception as exc:
            self._error(exc, notify=True)
        self.page.update()

    def _apply_theme(self, value: str) -> None:
        self.theme_name = value
        if self.page is not None:
            self.page.theme_mode = {
                "system": ft.ThemeMode.SYSTEM,
                "light": ft.ThemeMode.LIGHT,
                "dark": ft.ThemeMode.DARK,
            }[value]

    async def _language_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        language = event.control.value
        if language not in ("system", *LANGUAGES):
            return
        self.manual_language = language != "system"
        self.language = language if self.manual_language else self.system_language_name
        configuration = self.page.locale_configuration
        if configuration is not None:
            configuration.current_locale = ft.Locale(language_code=self.language)
        self._refresh()
        self.page.update()
        if self.preferences is None:
            self._set_message("error_language_save")
            self.page.update()
            return
        if not await self.preferences.set(LANGUAGE_KEY, language):
            self._set_message("error_language_save")
            self.page.update()

    async def _theme_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        value = event.control.value
        if value not in {"system", "light", "dark"}:
            return
        self._apply_theme(value)
        self.page.update()
        if self.preferences is None or not await self.preferences.set(THEME_KEY, value):
            self._set_message("error_theme_save")
            self.page.update()

    def _host_locale_changed(self, event: ft.LocaleChangeEvent) -> None:
        self.system_language_name = system_language(
            [locale.language_tag for locale in event.locales]
        )
        if self.manual_language:
            return
        self.language = self.system_language_name
        configuration = self.page.locale_configuration
        if configuration is not None:
            configuration.current_locale = ft.Locale(language_code=self.language)
        self._refresh()
        self.page.update()

    def _resized(self, event: ft.PageResizeEvent) -> None:
        self._apply_layout(event.width)
        if self.page:
            self.page.update()

    def _access_status(self) -> str:
        if self.journal is not None and self.journal.ready:
            return self.tr("access_ok")
        return self.tr("access_unchecked") if self.journal is None else self.tr("access_failed")

    def _error(self, exc: Exception, notify: bool = False) -> None:
        self._set_error(exc)
        if notify:
            conflict = isinstance(exc, ConflictError)
            show_dialog(
                self.page,
                ft.AlertDialog(
                    modal=True,
                    title=self.tr("conflict_title") if conflict else self.tr("action_failed"),
                    content=ft.Text(
                        f"{self.message.value}\n\n"
                        f"{self.tr('conflict_body') if conflict else self.tr('reload_hint')}"
                    ),
                    actions=[
                        ft.TextButton(self.tr("keep_editing"), on_click=self._dismiss),
                    ],
                ),
            )

    def _dismiss(self, event: ft.Event[ft.TextButton]) -> None:
        self.page.pop_dialog()

    async def _attach(self, location: str) -> None:
        folder = (
            AndroidFolder(self.bridge, location, FILENAME)
            if self.bridge is not None
            else DesktopFolder(location, FILENAME)
        )
        await folder.status()
        self.journal = Journal(folder, today=self._today)
        self.possible_conflict_copies = []
        self.sibling_scan_failed = False
        self.journal.storage.auto_cleanup = self.auto_backup_cleanup
        await self.journal.load()
        await self._apply_default_fee()
        await self._scan_conflict_copies()
        self.folder_label.value = (
            location if isinstance(folder, DesktopFolder) else self.tr("folder_selected")
        )
        self._set_message("journal_loaded" if self.journal.existed else "journal_new")

    async def _action(self, action: Callable[[], Awaitable[None]]) -> None:
        async with self.lock:
            for control in [
                *self.forms,
                self.select_button,
                self.welcome_select_button,
                self.reload_button,
                self.account_save,
                self.category_save,
                self.backup_clean_button,
                self.backup_cleanup,
            ]:
                control.disabled = True
            self.page.update()
            try:
                await action()
            except Exception as exc:
                self._error(exc, notify=True)
            finally:
                for control in [
                    *self.forms,
                    self.select_button,
                    self.welcome_select_button,
                    self.reload_button,
                    self.backup_cleanup,
                ]:
                    control.disabled = False
                self._refresh()
                self.page.update()

    async def _select(self, event: ft.Event[ft.Button]) -> None:
        async def select() -> None:
            if self.preferences is None:
                raise StorageError("Folder service is starting. Try again shortly.")
            selected = (
                unwrap(await self.bridge.call("select"))
                if self.bridge is not None
                else await ft.FilePicker().get_directory_path()
            )
            if selected is None:
                self._set_message("cancelled")
                return
            if not isinstance(selected, str):
                raise StorageError("Invalid folder picker response.")
            self.journal = None
            try:
                await self._attach(selected)
            except RecoveryRequired:
                if not await self.preferences.set(FOLDER_KEY, selected):
                    raise StorageError(
                        "Recovery is required, but the selected folder could not be remembered."
                    ) from None
                raise
            if not await self.preferences.set(FOLDER_KEY, selected):
                raise StorageError("Folder loaded, but could not remember it for restart.")

        await self._action(select)

    async def _reload(self, event: ft.Event[ft.Button]) -> None:
        async def reload() -> None:
            if self.journal is None:
                raise LedgerError("Select a folder first.")
            await self.journal.load()
            await self._apply_default_fee()
            await self._scan_conflict_copies()
            self._set_message("reloaded")

        await self._action(reload)

    async def _review_recovery(self, event: ft.Event[ft.TextButton]) -> None:
        async def review() -> None:
            journal = self._require_journal()
            try:
                await journal.load()
            except RecoveryRequired:
                state = journal.storage.recovery
                if state is None or not state.required:
                    raise
            else:
                self._set_message("reloaded")
                return
            state = journal.storage.recovery
            assert state is not None
            summary = self.tr(
                "recovery_summary",
                state=self.tr(f"recovery_{state.classification}"),
                count=journal.recovery_transactions,
                valid=self.tr("recovery_valid" if journal.recovery_valid else "recovery_invalid"),
                backup=state.record.backup
                if state.record and state.record.backup
                else "\n".join(f"backups/{name}" for name in state.pending) or "-",
                verified=self.tr(
                    "recovery_verified" if journal.recovery_backup_valid else "recovery_unverified"
                ),
            )

            async def accept(event: ft.Event[ft.TextButton]) -> None:
                async def confirm() -> None:
                    if self.journal is not journal or journal.storage.recovery is not state:
                        raise ConflictError("crud_stale")
                    await journal.accept_recovery()
                    self.page.pop_dialog()
                    self._set_message("reloaded")

                await self._action(confirm)

            show_dialog(
                self.page,
                ft.AlertDialog(
                    modal=True,
                    scrollable=True,
                    title=self.tr("recovery_review"),
                    content=ft.Text(f"{summary}\n\n{self.tr('recovery_help')}", selectable=True),
                    actions=[
                        ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss),
                        ft.TextButton(
                            self.tr("recovery_keep"),
                            on_click=accept,
                            disabled=not journal.recovery_valid,
                        ),
                    ],
                ),
            )

        await self._action(review)

    def _require_journal(self) -> Journal:
        if self.journal is None:
            raise LedgerError("Select a folder first.")
        return self.journal

    def _refresh_external_notices(self) -> None:
        journal = self.journal
        self.external_notice.visible = bool(journal and journal.external_changes)
        self.external_notice.controls = (
            [
                ft.Text(self.tr("external_notice")),
                ft.TextButton(self.tr("external_details"), on_click=self._review_external),
            ]
            if self.external_notice.visible
            else []
        )
        self.conflict_copy_notice.visible = bool(
            self.possible_conflict_copies or self.sibling_scan_failed
        )
        self.conflict_copy_notice.value = (
            self.tr("conflict_copies") + "\n" + "\n".join(self.possible_conflict_copies)
            if self.possible_conflict_copies
            else self.tr("conflict_copy_scan_failed")
            if self.sibling_scan_failed
            else ""
        )

    def _review_external(self, event: ft.Event[ft.TextButton]) -> None:
        journal = self._require_journal()
        controls: list[ft.Control] = [ft.Text(self.tr("external_notice"))]
        if journal.external_observed_at is not None:
            observed = journal.external_observed_at
            controls.append(
                ft.Text(
                    self.tr("external_observed", observed=observed.strftime("%Y-%m-%d %H:%M:%S %Z"))
                )
            )
        summary = journal.external_summary
        if summary:
            for kind in ("accounts", "categories", "transactions", "budgets"):
                controls.append(
                    ft.Text(
                        self.tr(
                            "external_summary",
                            kind=self.tr(
                                {
                                    "accounts": "accounts_title",
                                    "categories": "categories_title",
                                    "transactions": "nav_transactions",
                                    "budgets": "budget_title",
                                }[kind]
                            ),
                            added=summary.get(f"{kind}_added", 0),
                            removed=summary.get(f"{kind}_removed", 0),
                            changed=summary.get(f"{kind}_changed", 0),
                        )
                    )
                )
        else:
            controls.append(ft.Text(self.tr("external_summary_unavailable")))
        controls.extend(
            [
                ft.Text(self.tr("external_diff")),
                ft.Text(journal.external_changes, selectable=True),
            ]
        )
        show_dialog(
            self.page,
            ft.AlertDialog(
                scrollable=True,
                title=self.tr("external_details"),
                content=ft.Column(controls, tight=True, width=560),
                actions=[ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)],
            ),
        )

    async def _scan_conflict_copies(self) -> None:
        journal = self._require_journal()
        folder = journal.storage.folder
        listing = getattr(folder, "list_siblings", None)
        if listing is None:
            self.possible_conflict_copies = []
            self.sibling_scan_failed = False
            return
        filename = getattr(folder, "filename", FILENAME)
        stem, suffix = filename.rsplit(".", 1)
        pattern = re.compile(
            rf"{re.escape(stem)}(?: \([^/\\]*conflicted copy[^/\\]*\)|"
            rf" \([1-9]\d*\)|[ -][1-9]\d*)\.{re.escape(suffix)}",
            re.IGNORECASE,
        )
        try:
            names = await listing()
            self.possible_conflict_copies = sorted(
                {name for name in names if isinstance(name, str) and pattern.fullmatch(name)}
            )
            self.sibling_scan_failed = False
        except (StorageError, OSError, TimeoutError) as exc:
            self.sibling_scan_failed = True
            logger.warning("Optional journal sibling-name scan failed (%s)", type(exc).__name__)

    def _saved(self, backup: str | None) -> None:
        self._set_message(
            "backups_saved_warning"
            if self.journal is not None and self.journal.storage.cleanup_error
            else "saved_backup"
            if backup
            else "saved_new"
        )

    async def _backup_cleanup_changed(self, event: ft.Event[ft.Checkbox]) -> None:
        enabled = bool(event.control.value)

        async def save() -> None:
            if self.preferences is None:
                raise PreferencesError(self.tr("backups_setting_failed"))
            if not await self.preferences.set(BACKUP_CLEANUP_KEY, "on" if enabled else "off"):
                raise PreferencesError(self.tr("backups_setting_failed"))
            self.auto_backup_cleanup = enabled
            if self.journal is not None:
                self.journal.storage.auto_cleanup = enabled
            self._set_message("backups_setting_saved")

        await self._action(save)

    async def _preview_backup_cleanup(self, event: ft.Event[ft.Button]) -> None:
        async def preview() -> None:
            journal = self._require_journal()
            source = journal.source
            plan = await journal.storage.preview_cleanup()
            cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
            confirm = ft.TextButton(self.tr("backups_clean"), disabled=not plan.remove)

            async def clean(event: ft.Event[ft.TextButton]) -> None:
                confirm.disabled = cancel.disabled = True

                async def perform() -> None:
                    if self.journal is not journal or journal.source != source:
                        raise ConflictError("crud_stale")
                    await journal.storage.cleanup_backups(plan)
                    self.page.pop_dialog()
                    self._set_message("backups_cleaned")

                try:
                    await self._action(perform)
                finally:
                    confirm.disabled = not plan.remove
                    cancel.disabled = False
                    self.page.update()

            confirm.on_click = clean
            show_dialog(
                self.page,
                ft.AlertDialog(
                    modal=True,
                    scrollable=True,
                    title=self.tr("backups_clean"),
                    content=ft.Text(
                        translate_plural(
                            self.language,
                            "backups_preview",
                            "backups_preview.plural",
                            len(plan.remove),
                            size=plan.size,
                        )
                    ),
                    actions=[cancel, confirm],
                ),
            )

        await self._action(preview)

    async def _save_operating_currencies(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            journal = self._require_journal()
            if journal is not self.operating_journal:
                raise ConflictError("crud_stale")
            backup = await journal.set_operating_currencies(
                self.operating_currencies.value or "", self.operating_source
            )
            self.operating_original = self.operating_currencies.value or ""
            self._saved(backup)

        await self._action(save)

    def _reset_operating_currencies(self, event: ft.Event[ft.TextButton]) -> None:
        self.operating_journal = None
        self._refresh()
        self.page.update()

    def _edit_dialog(
        self,
        title: str,
        controls: list[ft.Control],
        journal: Journal,
        save: Callable[[], Awaitable[str | None]],
        *,
        deleting: bool = False,
        stretch: bool = False,
    ) -> None:
        cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
        submit = ft.TextButton(self.tr("crud_delete" if deleting else "crud_save"))

        async def commit(event: ft.Event[ft.TextButton]) -> None:
            disabled_states = [(control, control.disabled) for control in controls]
            submit.disabled = cancel.disabled = True
            for control in controls:
                control.disabled = True
            self.page.update()

            async def action() -> None:
                if self.journal is not journal:
                    raise ConflictError("crud_stale")
                backup = await save()
                self.page.pop_dialog()
                self._saved(backup)

            try:
                await self._action(action)
            finally:
                submit.disabled = cancel.disabled = False
                for control, disabled in disabled_states:
                    control.disabled = disabled
                self.page.update()

        submit.on_click = commit
        show_dialog(
            self.page,
            ft.AlertDialog(
                modal=True,
                scrollable=True,
                title=title,
                content=ft.Column(
                    controls,
                    tight=True,
                    spacing=12,
                    width=360,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH
                    if stretch
                    else ft.CrossAxisAlignment.START,
                ),
                actions=[cancel, submit],
            ),
        )

    def _transaction_code(self, transaction: Transaction) -> ft.Text:
        return ft.Text(
            self.tr("transaction_code", identifier=transaction.id),
            selectable=True,
            italic=True,
            size=12,
        )

    def _opening_details(self, transaction: Transaction) -> ft.ExpansionTile:
        return ft.ExpansionTile(
            title=self.tr("details"),
            controls=[
                self._transaction_code(transaction),
                ft.Text(f"{self.tr('date')}: {format_date(transaction.date, self.language)}"),
                ft.Text(
                    format_timestamp(transaction.timestamp, self.language, self._effective_zone())
                ),
            ],
            maintain_state=True,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
        )

    def _edit_opening_balance(self, journal: Journal, transaction: Transaction) -> None:
        accounts = [
            account
            for account in journal.ledger.accounts
            if any(posting.account == account.id for posting in transaction.postings)
        ]
        if len(accounts) != 1:
            raise LedgerError("crud_opening_complex")
        account = accounts[0]
        amount = ft.TextField(
            label=self.tr("transfer_owed" if account.liability else "amount"),
            value=str(journal.opening_balance(account.id)),
        )
        opening_date = DateTimeFields(
            self.language,
            self._effective_zone(),
            transaction.date,
            device_zone=self._picker_zone(),
            compact=True,
            show_accounting_date=True,
        )
        opening_date.occurred.label = self.tr("opening_balance_date")
        source = journal.source

        async def save() -> str | None:
            return await journal.edit_account(
                account.id,
                account.name,
                amount.value or "0",
                account.active,
                source,
                opening_date=date.fromisoformat(opening_date.values()[0]),
            )

        self._edit_dialog(
            self.tr("kind_opening"),
            [
                ft.Text(f"{account.name} ({account.currency})"),
                amount,
                opening_date,
                ft.Text(self.tr("crud_opening_help"), size=12),
                self._opening_details(transaction),
            ],
            journal,
            save,
        )

    def _edit_transaction(self, event: ft.Event[ft.IconButton]) -> None:
        try:
            journal = self._require_journal()
            identifier = str(event.control.data)
            try:
                transaction = journal.transaction_for_edit(identifier)
            except LedgerError as exc:
                if str(exc) != "crud_transaction_readonly":
                    raise
                transaction = next(
                    (t for t in journal.ledger.transactions if t.id == identifier), None
                )
                if transaction is None:
                    raise LedgerError("crud_missing") from exc
                if transaction.kind == "opening":
                    try:
                        self._edit_opening_balance(journal, transaction)
                    except LedgerError as opening_error:
                        if str(opening_error) != "crud_opening_complex":
                            raise
                    else:
                        return
                names = {a.id: a.name for a in journal.ledger.accounts}
                names.update({c.id: c.name for c in journal.ledger.categories})

                def posting_details(posting: Posting) -> str:
                    description = (
                        f"{names.get(posting.account, posting.account)}: "
                        f"{format_amount(posting.amount, self.language, posting.currency)}"
                    )
                    if posting.unit_price is not None and posting.price_currency is not None:
                        price = format_amount(
                            posting.unit_price, self.language, posting.price_currency
                        )
                        description += f" @ {price}"
                    elif posting.total_price is not None and posting.price_currency is not None:
                        price = format_amount(
                            posting.total_price, self.language, posting.price_currency
                        )
                        description += f" @@ {price}"
                    return description

                show_dialog(
                    self.page,
                    ft.AlertDialog(
                        title=self.tr("details"),
                        scrollable=True,
                        content=ft.Column(
                            [
                                ft.Text(
                                    self.tr(
                                        "crud_opening_complex"
                                        if transaction.kind == "opening"
                                        else "crud_transaction_readonly"
                                    )
                                ),
                                *(
                                    [self._opening_details(transaction)]
                                    if transaction.kind == "opening"
                                    else [
                                        self._transaction_code(transaction),
                                        ft.Text(
                                            format_timestamp(
                                                transaction.timestamp,
                                                self.language,
                                                self._effective_zone(),
                                            )
                                        ),
                                    ]
                                ),
                                ft.Text(
                                    self.tr("kind_opening")
                                    if transaction.kind == "opening"
                                    else transaction.narration,
                                    size=17,
                                ),
                                ft.ExpansionTile(
                                    title=self.tr("payee"),
                                    controls=[ft.Text(transaction.payee, selectable=True)],
                                    maintain_state=True,
                                ),
                                *[
                                    ft.Text(posting_details(posting))
                                    for posting in transaction.postings
                                ],
                            ],
                            tight=True,
                        ),
                        actions=[ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)],
                    ),
                )
                return
            self._transaction_dialog(journal, transaction)
        except Exception as exc:
            self._error(exc, notify=True)

    def _delete_transaction(self, event: ft.Event[ft.IconButton]) -> None:
        try:
            journal = self._require_journal()
            transaction = journal.transaction_for_edit(str(event.control.data))
            source = journal.source

            async def remove() -> str | None:
                return await journal.delete_transaction(transaction.id, source)

            self._edit_dialog(
                self.tr("crud_confirm"),
                [ft.Text(self.tr("crud_confirm_body")), self._transaction_code(transaction)],
                journal,
                remove,
                deleting=True,
            )
        except Exception as exc:
            self._error(exc, notify=True)

    def _new_transaction(self, event: ft.Event[ft.FloatingActionButton]) -> None:
        try:
            self._transaction_dialog(self._require_journal())
        except Exception as exc:
            self._error(exc, notify=True)

    def _transaction_dialog(self, journal: Journal, transaction: Transaction | None = None) -> None:
        source = journal.source
        dropdown_rows: list[ft.Row] = []

        def aligned_dropdown(control: ft.Dropdown) -> ft.Row:
            row = ft.Row([control])
            dropdown_rows.append(row)
            return row

        transfer = (
            journal.transfer_details(transaction)
            if transaction and transaction.kind == "transfer"
            else None
        )
        cashflow = (
            journal.ledger.cashflow_details(transaction)
            if transaction and transaction.kind in {"expense", "income"}
            else None
        )
        saved_fee = transfer or cashflow
        account_ids = {a.id for a in journal.ledger.accounts}
        original_asset = (
            transfer.source
            if transfer
            else cashflow.account
            if cashflow
            else next(p for p in transaction.postings if p.account in account_ids)
            if transaction
            else None
        )
        original_category = (
            cashflow.category.account
            if cashflow
            else next(p.account for p in transaction.postings if p.account not in account_ids)
            if transaction and not transfer
            else None
        )
        sign = ft.SegmentedButton(
            data="sign",
            width=132,
            segments=[
                ft.Segment(value="-", label="−"),
                ft.Segment(value="+", label="+"),
                ft.Segment(value="transfer", label="↔"),
            ],
            selected=[
                "transfer"
                if transfer
                else "-"
                if not transaction or transaction.kind == "expense"
                else "+"
            ],
            show_selected_icon=False,
            style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8)),
            disabled=transaction is not None,
        )
        special = ft.Dropdown(
            data="special",
            expand=True,
            label=self.tr("special_receipt"),
            value=transaction.kind
            if transaction and transaction.kind in {"income", "reimbursement", "adjustment"}
            else "income",
            disabled=transaction is not None,
            options=[
                ft.DropdownOption(key=k, text=self.tr(f"kind_{k}"))
                for k in ("income", "reimbursement", "adjustment")
            ],
        )
        account = ft.Dropdown(
            data="account",
            expand=True,
            label=self.tr("primary_account"),
            value=original_asset.account if original_asset else None,
            options=[
                ft.DropdownOption(key=a.id, text=f"{a.name} ({a.currency})")
                for a in journal.ledger.accounts
                if a.active or (original_asset and original_asset.account == a.id)
            ],
        )
        category = ft.Dropdown(
            data="category",
            expand=True,
            label=self.tr("expense_category"),
            value=original_category,
        )
        category_create = ft.IconButton(
            data="category_create",
            icon=ft.Icons.ADD,
            tooltip=self.tr("create_category"),
        )
        category_row = ft.Row([category, category_create], spacing=0)
        amount = ft.TextField(
            data="amount",
            expand=True,
            label=self.tr("amount"),
            value=str(abs(original_asset.amount)) if original_asset else "",
        )
        sign_hint = ft.Text(data="sign_hint", size=12)
        destination = ft.Dropdown(
            data="second_account",
            expand=True,
            label=self.tr("second_account"),
            disabled=transaction is not None and transfer is None,
            value=transfer.target.account if transfer else None,
        )
        fee_enabled = ft.Checkbox(
            data="fee_enabled",
            label=self.tr("transfer_fee_enabled"),
            value=bool(saved_fee and saved_fee.fee),
        )
        fee = ft.TextField(
            data="fee",
            label=self.tr("transfer_fee"),
            value=str(saved_fee.fee) if saved_fee else "0",
        )
        fee_mode = ft.Dropdown(
            data="fee_mode",
            expand=True,
            label=self.tr("transfer_mode"),
            value=transfer.mode if transfer else "added",
            options=[
                ft.DropdownOption(key=k, text=self.tr(f"transfer_{k}"))
                for k in ("added", "deducted")
            ],
        )
        fee_category = ft.Dropdown(
            data="fee_category",
            expand=True,
            label=self.tr("transfer_fee_category"),
            value=saved_fee.fee_category
            if saved_fee and saved_fee.fee
            else journal.ledger.default_fee_category,
            options=self._fee_category_options(
                journal,
                self.fee_preferences.branches,
                retained=saved_fee.fee_category if saved_fee and saved_fee.fee else None,
            ),
        )
        if transfer:
            amount.value = str(
                transfer.debited
                if transfer.exchange or transfer.mode == "deducted"
                else transfer.target.amount
            )
        elif cashflow:
            amount.value = str(cashflow.amount)
        received = ft.TextField(
            data="second_amount",
            value=str(transfer.target.amount) if transfer and transfer.exchange else "",
        )
        fee_currency = ft.Dropdown(
            data="fee_currency",
            expand=True,
            label=self.tr("exchange_fee_currency"),
            value=transfer.fee_currency if transfer and transfer.exchange else None,
        )
        preview = ft.Text(data="preview", size=12, selectable=True)
        fee_fields = ft.Column(
            [
                fee_enabled,
                fee,
                aligned_dropdown(fee_mode),
                aligned_dropdown(fee_currency),
                aligned_dropdown(fee_category),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        description = ft.TextField(
            data="description",
            label=self.tr("crud_description_label"),
            value=transaction.narration if transaction else "",
            multiline=True,
            min_lines=1,
            max_lines=4,
            text_size=17,
        )
        payee = ft.TextField(
            data="payee",
            value=transaction.payee if transaction else "",
            keyboard_type=ft.KeyboardType.NAME,
        )
        when = DateTimeFields(
            self.language,
            self._effective_zone(),
            transaction.date if transaction else None,
            transaction.timestamp if transaction else None,
            device_zone=self._picker_zone(),
            compact=True,
        )
        reference = ft.Dropdown(
            data="reference",
            expand=True,
            label=self.tr("incoming_reference_label"),
            value="",
            options=[ft.DropdownOption(key="", text=self.tr("incoming_no_reference"))],
        )
        for item in sorted(journal.ledger.transactions, key=lambda t: (t.date, t.id), reverse=True):
            try:
                journal.expense_for_edit(item.id)
            except LedgerError as exc:
                if str(exc) != "crud_transaction_readonly":
                    raise
                continue
            posting = journal.ledger.cashflow_details(item).category
            reference.options.append(
                ft.DropdownOption(
                    key=item.id,
                    text=f"{format_date(item.date, self.language)} · "
                    f"{item.narration or self.tr('kind_expense')} · "
                    f"{format_amount(posting.amount, self.language, posting.currency)}",
                )
            )
        reference.visible = False
        reference.disabled = transaction is not None
        advanced = ft.ExpansionTile(
            data="advanced",
            title=self.tr("transaction_advanced"),
            controls=[
                ft.Column(
                    [
                        aligned_dropdown(special),
                        aligned_dropdown(reference),
                        fee_fields,
                        payee,
                        when.time_controls,
                        *([self._transaction_code(transaction)] if transaction else []),
                        *(
                            [
                                ft.Text(
                                    self.tr(
                                        "incoming_linked", identifier=transaction.related_expense
                                    )
                                )
                            ]
                            if transaction and transaction.related_expense
                            else []
                        ),
                    ],
                    spacing=16,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ],
            controls_padding=ft.Padding.only(top=8),
            maintain_state=True,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        category_values: dict[str, str | None] = {}
        fee_drafts: dict[str, tuple[bool, str, str | None, str | None, str | None]] = {}
        currencies = {a.id: a.currency for a in journal.ledger.accounts}
        names = {a.id: a.name for a in journal.ledger.accounts}

        def operation() -> str:
            if sign.selected == ["transfer"]:
                return "transfer"
            if sign.selected == ["-"]:
                return "expense"
            return special.value or "income"

        previous_kind = transaction.kind if transaction else "expense"

        def is_exchange() -> bool:
            return bool(
                operation() == "transfer"
                and account.value in currencies
                and destination.value in currencies
                and currencies[account.value] != currencies[destination.value]
            )

        def update_exchange() -> None:
            exchange = is_exchange()
            transferring = operation() == "transfer"
            sign_hint.value = self.tr(
                "amount_transfer_hint"
                if transferring
                else "amount_debit_hint"
                if sign.selected == ["-"]
                else "amount_credit_hint"
            )
            account.label = self.tr(
                "source_account"
                if transferring or sign.selected == ["-"]
                else "destination_account"
            )
            destination.label = self.tr("destination_account")
            primary_currency = currencies.get(account.value or "", "")
            second_currency = currencies.get(destination.value or "", "")
            source_currency, target_currency = primary_currency, second_currency
            source_amount, target_amount = amount.value, received.value
            primary_label = self.tr("exchange_debited")
            second_label = self.tr("exchange_credited")
            transfer_label = self.tr(
                "exchange_credited" if fee_mode.value == "added" else "exchange_debited"
            )
            kind = operation()
            ordinary_fee = kind in {"expense", "income"} and bool(fee_enabled.value)
            if ordinary_fee:
                transfer_label = self.tr(
                    "commission_purchase"
                    if kind == "expense" and fee_mode.value == "added"
                    else "exchange_debited"
                    if kind == "expense"
                    else "exchange_credited"
                    if fee_mode.value == "added"
                    else "commission_gross"
                )
            amount.label = (
                f"{primary_label} ({primary_currency})"
                if exchange
                else f"{transfer_label} ({primary_currency})"
                if (transferring and fee_enabled.value or ordinary_fee) and primary_currency
                else f"{self.tr('amount')} ({primary_currency})"
                if primary_currency
                else self.tr("amount")
            )
            received.label = f"{second_label} ({second_currency})"
            received.visible = exchange
            fee_mode.visible = (
                (transferring or ordinary_fee) and bool(fee_enabled.value) and not exchange
            )
            fee_currency.visible = transferring and bool(fee_enabled.value) and exchange
            fee_currency.options = [
                ft.DropdownOption(key=c, text=c)
                for c in dict.fromkeys([source_currency, target_currency])
                if c
            ]
            if transferring and fee_currency.value not in {o.key for o in fee_currency.options}:
                fee_currency.value = source_currency or None
            fee.label = (
                f"{self.tr('transfer_fee')} ({fee_currency.value})"
                if exchange
                else self.tr("transfer_fee")
            )
            preview.visible = transferring or ordinary_fee
            if preview.visible:
                sender, receiver = account.value, destination.value
                movement = (
                    f"{names.get(sender or '', '…')} → {names.get(receiver or '', '…')}"
                    if transferring
                    else names.get(account.value or "", "…")
                )
                try:
                    commission = money(fee.value or "0") if fee_enabled.value else Decimal("0")
                    if ordinary_fee:
                        principal, total = cashflow_amounts(
                            money(amount.value or ""), commission, fee_mode.value or "", kind
                        )
                        preview.value = (
                            movement
                            + "\n"
                            + self.tr(
                                "commission_expense_preview"
                                if kind == "expense"
                                else "commission_income_preview",
                                principal=format_amount(principal, self.language, primary_currency),
                                total=format_amount(total, self.language, primary_currency),
                            )
                        )
                    elif exchange:
                        principal, gross, rate = exchange_values(
                            money(source_amount or ""),
                            money(target_amount or ""),
                            commission,
                            source_currency,
                            target_currency,
                            fee_currency.value or source_currency,
                        )
                        preview.value = (
                            movement
                            + "\n"
                            + self.tr(
                                "exchange_preview",
                                debited=format_amount(
                                    money(source_amount or ""), self.language, source_currency
                                ),
                                credited=format_amount(
                                    money(target_amount or ""), self.language, target_currency
                                ),
                                principal=format_amount(principal, self.language, source_currency),
                                gross=format_amount(gross, self.language, target_currency),
                                rate=f"1 {source_currency} ≈ {rate:.12g} {target_currency}",
                            )
                        )
                    else:
                        nominal = money(amount.value or "")
                        debit = nominal + commission if fee_mode.value == "added" else nominal
                        credit = nominal - commission if fee_mode.value == "deducted" else nominal
                        if nominal <= 0 or credit <= 0:
                            raise LedgerError("transfer_amount")
                        preview.value = (
                            movement
                            + "\n"
                            + self.tr(
                                "transfer_preview",
                                debited=format_amount(debit, self.language, source_currency),
                                credited=format_amount(credit, self.language, target_currency),
                            )
                        )
                except LedgerError as exc:
                    preview.value = movement + "\n" + localized_error(self.language, str(exc))

        def configure(
            event: ft.Event[ft.Dropdown]
            | ft.Event[ft.Checkbox]
            | ft.Event[ft.SegmentedButton]
            | None = None,
        ) -> None:
            nonlocal previous_kind
            kind = operation()
            changed = previous_kind != kind
            if changed:
                category_values[previous_kind] = category.value
                category.value = category_values.get(kind)
                fee_drafts[previous_kind] = (
                    bool(fee_enabled.value),
                    fee.value,
                    fee_mode.value,
                    fee_category.value,
                    fee_currency.value,
                )
                (
                    fee_enabled.value,
                    fee.value,
                    fee_mode.value,
                    fee_category.value,
                    fee_currency.value,
                ) = fee_drafts.get(
                    kind, (False, "0", "added", journal.ledger.default_fee_category, None)
                )
            previous_kind = kind
            root = "Income:" if kind == "income" else "Expenses:"
            category.label = self.tr("income_category" if kind == "income" else "expense_category")
            payee.label = self.tr(
                "transfer_counterparty"
                if kind == "transfer"
                else "payee"
                if kind == "expense"
                else "incoming_sender"
            )
            destination.options = [
                ft.DropdownOption(key=a.id, text=f"{a.name} ({a.currency})")
                for a in journal.ledger.accounts
                if a.id != account.value
                and (
                    a.active
                    or a.id
                    in {
                        original_asset.account if original_asset else "",
                        transfer.target.account if transfer else "",
                    }
                )
            ]
            destination.visible = kind == "transfer"
            if destination.visible and destination.value and destination.value == account.value:
                destination.error_text = self.tr("transfer_accounts")
            else:
                destination.error_text = None
            category.options = [
                ft.DropdownOption(key=c.id, text=self._category_label(c.id, c.name))
                for c in journal.ledger.categories
                if c.id.startswith(root)
                and (journal.ledger.category_active(c.id) or c.id == original_category)
            ]
            category.hint_text = (
                self.tr("category_none_available") if not category.options else None
            )
            category.visible = kind not in {"adjustment", "transfer"}
            category_row.visible = category.visible
            fee_fields.visible = kind in {"expense", "income", "transfer"}
            fee.visible = fee_category.visible = fee_fields.visible and bool(fee_enabled.value)
            if fee_category.value not in {option.key for option in fee_category.options}:
                fee_category.value = None
            if kind == "adjustment":
                category.value = ADJUSTMENTS
            elif category.value not in {o.key for o in category.options}:
                category.value = None
            update_exchange()
            reference.visible = kind == "reimbursement" and transaction is None
            special.visible = sign.selected == ["+"]
            details = []
            if transaction:
                details.append(self.tr(f"kind_{transaction.kind}"))
                if transaction.payee:
                    details.append(transaction.payee)
                if saved_fee and saved_fee.fee:
                    details.append(
                        f"{self.tr('transfer_fee')}: "
                        f"{format_amount(saved_fee.fee, self.language, saved_fee.fee_currency)}"
                    )
                details.append(when.clock.value or "")
            advanced.subtitle = " · ".join(details) or None
            for row in dropdown_rows:
                row.visible = row.controls[0].visible
            if event is not None:
                self.page.update()

        def fill_reference(event: ft.Event[ft.Dropdown]) -> None:
            try:
                if self.journal is not journal or journal.source != source:
                    raise ConflictError("crud_stale")
                if reference.value:
                    original = journal.expense_for_edit(reference.value)
                    posting = journal.ledger.cashflow_details(original).category
                    category.value = posting.account
                    amount.value = str(posting.amount)
                self.page.update()
            except Exception as exc:
                self._error(exc, notify=True)

        def create_category_dialog(event: ft.Event[ft.IconButton]) -> None:
            try:
                category_opened_default = date.fromisoformat(when.values()[0])
            except LedgerError:
                category_opened_default = self._today()
            category_opened = DateTimeFields(
                self.language,
                self._effective_zone(),
                category_opened_default,
                device_zone=self._picker_zone(),
                compact=True,
                show_accounting_date=True,
            )
            category_opened.occurred.label = self.tr("category_available_date")
            category_path = ft.TextField(
                data="quick_category_path",
                label=self.tr("category_path"),
                hint_text=self.tr("category_path_hint"),
                autofocus=True,
            )
            category_name = ft.TextField(
                data="quick_category_name",
                label=self.tr("category_name"),
                keyboard_type=ft.KeyboardType.NAME,
            )
            category_parent = ft.Dropdown(
                data="quick_category_parent",
                label=self.tr("code_suggestion_parent"),
                value="",
                expand=True,
            )
            category_status = ft.Text(size=12, color=ft.Colors.OUTLINE)
            category_alternatives = ft.Dropdown(
                label=self.tr("code_suggestion_alternatives"),
                width=320,
                visible=False,
            )
            regenerate = ft.TextButton(self.tr("code_suggestion_regenerate"), disabled=True)
            code_manual = False

            def update_suggestion(*, regenerate_code: bool = False) -> None:
                nonlocal code_manual
                if regenerate_code:
                    code_manual = False
                regenerate.disabled = not (code_manual and (category_name.value or "").strip())
                root = "Income" if operation() == "income" else "Expenses"
                parent_options = self._category_parent_options(journal.ledger.categories, root)
                category_parent.options = parent_options
                if category_parent.value not in {item.key for item in parent_options}:
                    category_parent.value = ""
                if not (category_name.value or "").strip():
                    if not code_manual:
                        category_path.value = ""
                    category_status.value = ""
                    category_status.visible = False
                    category_alternatives.options = []
                    category_alternatives.visible = False
                    return
                existing_ids = self._existing_identifiers(journal)
                suggestions = suggest_category_code(
                    category_name.value or "",
                    root,
                    existing_ids,
                    parent=category_parent.value or None,
                )
                if not code_manual:
                    category_path.value = suggestions.recommended.code
                current = (category_path.value or "").strip()
                candidate = (
                    validate_category_code(
                        current,
                        root,
                        existing_ids,
                        parent=category_parent.value or None,
                    )
                    if current
                    else None
                )
                self._show_code_candidate(
                    category_status,
                    candidate,
                    manually_edited=code_manual,
                )
                category_alternatives.options = [
                    ft.DropdownOption(key=item.code, text=item.code)
                    for item in suggestions.alternatives
                ]
                category_alternatives.value = None
                category_alternatives.visible = bool(suggestions.alternatives)

            def name_changed(event: ft.Event[ft.TextField]) -> None:
                update_suggestion()
                self.page.update()

            def path_changed(event: ft.Event[ft.TextField]) -> None:
                nonlocal code_manual
                code_manual = True
                update_suggestion()
                self.page.update()

            def parent_changed(event: ft.Event[ft.Dropdown]) -> None:
                update_suggestion()
                self.page.update()

            def alternative_selected(event: ft.Event[ft.Dropdown]) -> None:
                nonlocal code_manual
                selected = event.control.value
                if selected is None:
                    return
                category_path.value = selected
                code_manual = True
                update_suggestion()
                self.page.update()

            def regenerate_code(event: ft.Event[ft.TextButton]) -> None:
                update_suggestion(regenerate_code=True)
                self.page.update()

            category_name.on_change = name_changed
            category_path.on_change = path_changed
            category_parent.on_select = parent_changed
            category_alternatives.on_select = alternative_selected
            regenerate.on_click = regenerate_code
            cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
            create = ft.TextButton(self.tr("create_category"))

            async def save_category(event: ft.Event[ft.TextButton]) -> None:
                nonlocal source

                async def save() -> None:
                    nonlocal source
                    if self.journal is not journal or journal.source != source:
                        raise ConflictError("crud_stale")
                    root = "Income" if operation() == "income" else "Expenses"
                    code = validate_category_code(
                        category_path.value or "",
                        root,
                        self._existing_identifiers(journal),
                        parent=category_parent.value or None,
                    )
                    if code.collision:
                        raise LedgerError("crud_code_collision")
                    if not code.valid:
                        raise LedgerError("crud_code_invalid")
                    backup = await journal.create_category(
                        code.code,
                        category_name.value or "",
                        root=root,
                        opened=date.fromisoformat(category_opened.values()[0]),
                    )
                    source = journal.source
                    identifier = code.code
                    category.options = [
                        ft.DropdownOption(
                            key=item.id, text=self._category_label(item.id, item.name)
                        )
                        for item in journal.ledger.categories
                        if item.id.startswith(f"{root}:") and item.active
                    ]
                    category.hint_text = None
                    category.value = identifier
                    category_values[operation()] = identifier
                    self.page.pop_dialog()
                    self._saved(backup)

                create.disabled = cancel.disabled = True
                try:
                    await self._action(save)
                finally:
                    create.disabled = cancel.disabled = False
                    self.page.update()

            create.on_click = save_category
            update_suggestion()
            show_dialog(
                self.page,
                ft.AlertDialog(
                    modal=True,
                    scrollable=True,
                    title=self.tr("category_form_title"),
                    content=ft.Column(
                        [
                            ft.Text(
                                self.tr(
                                    "kind_income" if operation() == "income" else "kind_expense"
                                )
                            ),
                            ft.Row([category_parent]),
                            category_name,
                            category_path,
                            category_opened,
                            category_status,
                            regenerate,
                            category_alternatives,
                        ],
                        tight=True,
                        spacing=12,
                        width=360,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    actions=[cancel, create],
                ),
            )

        sign.on_change = configure
        special.on_select = configure
        fee_enabled.on_change = configure
        account.on_select = configure
        destination.on_select = configure
        reference.on_select = fill_reference
        category_create.on_click = create_category_dialog

        def recalculate(event: ft.Event[ft.TextField] | ft.Event[ft.Dropdown]) -> None:
            update_exchange()
            for row in dropdown_rows:
                row.visible = row.controls[0].visible
            self.page.update()

        amount.on_change = received.on_change = fee.on_change = recalculate
        fee_currency.on_select = recalculate
        fee_mode.on_select = recalculate

        async def save() -> str | None:
            occurred, timestamp = when.values()
            kind = operation()
            if transaction and kind != transaction.kind:
                raise LedgerError("crud_transaction_readonly")
            if kind == "transfer":
                return await journal.save_transfer(
                    account.value or "",
                    destination.value or "",
                    amount.value or "",
                    (fee.value or "0") if fee_enabled.value else "0",
                    fee_category.value or "",
                    fee_mode.value or "",
                    occurred,
                    timestamp,
                    description.value or "",
                    payee.value or "",
                    source,
                    identifier=transaction.id if transaction else None,
                    default_fee_name=self.tr("transfer_bank_fees"),
                    destination_amount=(received.value or "") if is_exchange() else None,
                    fee_currency=fee_currency.value if is_exchange() else None,
                )
            if transaction:
                return await journal.edit_transaction(
                    transaction.id,
                    account.value or "",
                    category.value or "",
                    amount.value or "",
                    payee.value or "",
                    occurred,
                    timestamp,
                    description.value or "",
                    source,
                    fee=(fee.value or "0")
                    if kind in {"expense", "income"} and fee_enabled.value
                    else "0",
                    fee_category=fee_category.value or "",
                    fee_mode=fee_mode.value or "added",
                    default_fee_name=self.tr("transfer_bank_fees"),
                )
            if journal.source != source:
                raise ConflictError("crud_stale")
            if kind == "expense":
                return await journal.add_expense(
                    account.value or "",
                    category.value or "",
                    amount.value or "",
                    payee.value or "",
                    occurred,
                    description.value or "",
                    timestamp,
                    fee=(fee.value or "0") if fee_enabled.value else "0",
                    fee_category=fee_category.value or "",
                    fee_mode=fee_mode.value or "added",
                    default_fee_name=self.tr("transfer_bank_fees"),
                )
            return await journal.add_incoming(
                account.value or "",
                category.value or "",
                amount.value or "",
                payee.value or "",
                occurred,
                kind,
                description.value or "",
                timestamp,
                related_expense=reference.value
                if kind == "reimbursement" and reference.value
                else None,
                expected_source=source,
                fee=(fee.value or "0") if kind == "income" and fee_enabled.value else "0",
                fee_category=fee_category.value or "",
                fee_mode=fee_mode.value or "added",
                default_fee_name=self.tr("transfer_bank_fees"),
            )

        controls: list[ft.Control] = [
            description,
            ft.Row([sign, amount], spacing=8),
            sign_hint,
            aligned_dropdown(account),
            category_row,
            aligned_dropdown(destination),
            received,
            preview,
            when,
            advanced,
        ]
        configure()
        self._edit_dialog(
            self.tr("crud_edit" if transaction else "add_transaction"),
            controls,
            journal,
            save,
            stretch=True,
        )

    def _edit_entity(self, event: ft.Event[ft.IconButton]) -> None:
        try:
            journal = self._require_journal()
            kind, identifier = event.control.data
            category = kind == "category"
            items = journal.ledger.categories if category else journal.ledger.accounts
            item = next((item for item in items if item.id == identifier), None)
            if item is None:
                raise LedgerError("crud_missing")
            source = journal.source
            name = ft.TextField(
                label=self.tr("category_name" if category else "account_name"), value=item.name
            )
            active = ft.Checkbox(label=self.tr("crud_active"), value=item.active)
            opening: ft.TextField | None = None
            if not category:
                try:
                    opening = ft.TextField(
                        label=self.tr(
                            "transfer_owed"
                            if isinstance(item, Account) and item.liability
                            else "opening_balance"
                        ),
                        value=str(journal.opening_balance(identifier)),
                    )
                except LedgerError as exc:
                    if str(exc) != "crud_opening_complex":
                        raise
            opening_transaction = next(
                (
                    transaction
                    for transaction in journal.ledger.transactions
                    if transaction.kind == "opening"
                    and any(posting.account == identifier for posting in transaction.postings)
                ),
                None,
            )
            opening_date: DateTimeFields | None = None
            if isinstance(item, Account) and opening is not None:
                opening_date = DateTimeFields(
                    self.language,
                    self._effective_zone(),
                    opening_transaction.date if opening_transaction else None,
                    device_zone=self._picker_zone(),
                    compact=True,
                    show_accounting_date=opening_transaction is not None,
                )
                opening_date.occurred.label = self.tr("opening_balance_date")
            controls: list[ft.Control] = [
                ft.Text(
                    identifier,
                    selectable=True,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(self.tr("crud_category_help" if category else "crud_account_help")),
                name,
                active,
            ]
            if isinstance(item, Account):
                controls.append(ft.Text(item.currency))
                controls.append(
                    opening if opening is not None else ft.Text(self.tr("crud_opening_complex"))
                )
                if opening_date is not None:
                    controls.append(opening_date)

            async def save() -> str | None:
                if category:
                    return await journal.edit_category(
                        identifier, name.value or "", bool(active.value), source
                    )
                return await journal.edit_account(
                    identifier,
                    name.value or "",
                    (opening.value or "0") if opening is not None else None,
                    bool(active.value),
                    source,
                    opening_date=(
                        date.fromisoformat(opening_date.values()[0])
                        if opening_date is not None
                        else None
                    ),
                )

            self._edit_dialog(self.tr("crud_edit"), controls, journal, save, stretch=True)
        except Exception as exc:
            self._error(exc, notify=True)

    def _delete_entity(self, event: ft.Event[ft.IconButton]) -> None:
        try:
            journal = self._require_journal()
            kind, identifier = event.control.data
            has_history = any(
                posting.account == identifier
                for transaction in journal.ledger.transactions
                for posting in transaction.postings
            )
            has_children = kind == "category" and any(
                category.id.startswith(identifier + ":") for category in journal.ledger.categories
            )
            if has_history or has_children:

                def edit(edit_event: ft.Event[ft.TextButton]) -> None:
                    self._dismiss(edit_event)
                    self._edit_entity(event)

                show_dialog(
                    self.page,
                    ft.AlertDialog(
                        modal=True,
                        scrollable=True,
                        title=self.tr("crud_delete_blocked"),
                        content=ft.Column(
                            [
                                ft.Text(identifier, selectable=True, weight=ft.FontWeight.BOLD),
                                *([ft.Text(self.tr("crud_has_history"))] if has_history else []),
                                *([ft.Text(self.tr("crud_children"))] if has_children else []),
                            ],
                            tight=True,
                            spacing=12,
                            width=360,
                        ),
                        actions=[
                            ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss),
                            ft.TextButton(self.tr("crud_edit"), on_click=edit),
                        ],
                    ),
                )
                return
            source = journal.source

            async def remove() -> str | None:
                if kind == "category":
                    return await journal.delete_category(identifier, source)
                return await journal.delete_account(identifier, source)

            self._edit_dialog(
                self.tr("crud_confirm"),
                [ft.Text(self.tr("crud_confirm_body")), ft.Text(identifier, selectable=True)],
                journal,
                remove,
                deleting=True,
            )
        except Exception as exc:
            self._error(exc, notify=True)

    @staticmethod
    def _existing_identifiers(journal: Journal) -> tuple[str, ...]:
        return tuple(item.id for item in (*journal.ledger.accounts, *journal.ledger.categories))

    def _show_code_candidate(
        self,
        status: ft.Text,
        candidate: CodeCandidate | None,
        *,
        manually_edited: bool,
    ) -> None:
        if candidate is None:
            status.value = ""
            status.visible = False
            return
        if candidate.collision:
            status.value = self.tr("code_suggestion_collision", code=candidate.collision)
            status.color = ft.Colors.ERROR
        elif not candidate.valid:
            status.value = self.tr("code_suggestion_invalid")
            status.color = ft.Colors.ERROR
        else:
            status.value = self.tr(
                "code_suggestion_manual" if manually_edited else "code_suggestion_status"
            )
            status.color = ft.Colors.OUTLINE
        status.visible = True

    def _update_account_suggestion(self, *, regenerate: bool = False) -> None:
        if regenerate:
            self.account_code_manual = False
        journal = self.journal
        self.account_suggestion_regenerate.disabled = not (
            self.account_code_manual
            and journal is not None
            and journal.ready
            and (self.account_name.value or "").strip()
        )
        if journal is None or not journal.ready or not (self.account_name.value or "").strip():
            if not self.account_code_manual:
                self.account_id.value = ""
            self.account_suggestion_status.value = ""
            self.account_suggestion_status.visible = False
            self.account_suggestion_alternatives.options = []
            self.account_suggestion_alternatives.visible = False
            return
        root = self.account_root.value or "Assets"
        existing = self._existing_identifiers(journal)
        suggestions = suggest_account_code(
            self.account_name.value or "",
            root,
            self.currency.value or "",
            existing,
        )
        if not self.account_code_manual:
            self.account_id.value = suggestions.recommended.code
        current = (self.account_id.value or "").strip()
        candidate = validate_account_code(current, root, existing) if current else None
        self._show_code_candidate(
            self.account_suggestion_status,
            candidate,
            manually_edited=self.account_code_manual,
        )
        self.account_suggestion_alternatives.options = [
            ft.DropdownOption(key=item.code, text=item.code) for item in suggestions.alternatives
        ]
        self.account_suggestion_alternatives.value = None
        self.account_suggestion_alternatives.visible = bool(suggestions.alternatives)

    def _update_category_suggestion(self, *, regenerate: bool = False) -> None:
        if regenerate:
            self.category_code_manual = False
        journal = self.journal
        self.category_suggestion_regenerate.disabled = not (
            self.category_code_manual
            and journal is not None
            and journal.ready
            and (self.category_name.value or "").strip()
        )
        if journal is None or not journal.ready:
            if not self.category_code_manual:
                self.category_id.value = ""
            self.category_suggestion_status.value = ""
            self.category_suggestion_status.visible = False
            self.category_suggestion_alternatives.options = []
            self.category_suggestion_alternatives.visible = False
            return
        if not (self.category_name.value or "").strip():
            if not self.category_code_manual:
                self.category_id.value = ""
            self.category_suggestion_status.value = ""
            self.category_suggestion_status.visible = False
            self.category_suggestion_alternatives.options = []
            self.category_suggestion_alternatives.visible = False
            return
        root = self.category_root.value or "Expenses"
        existing = self._existing_identifiers(journal)
        parent = self.category_parent.value or None
        suggestions = suggest_category_code(
            self.category_name.value or "",
            root,
            existing,
            parent=parent,
        )
        if not self.category_code_manual:
            self.category_id.value = suggestions.recommended.code
        current = (self.category_id.value or "").strip()
        candidate = (
            validate_category_code(current, root, existing, parent=parent) if current else None
        )
        self._show_code_candidate(
            self.category_suggestion_status,
            candidate,
            manually_edited=self.category_code_manual,
        )
        self.category_suggestion_alternatives.options = [
            ft.DropdownOption(key=item.code, text=item.code) for item in suggestions.alternatives
        ]
        self.category_suggestion_alternatives.value = None
        self.category_suggestion_alternatives.visible = bool(suggestions.alternatives)

    def _account_attributes_changed(
        self, event: ft.Event[ft.TextField] | ft.Event[ft.Dropdown]
    ) -> None:
        self._update_account_suggestion()
        if self.page:
            self.page.update()

    def _account_code_changed(self, event: ft.Event[ft.TextField]) -> None:
        self.account_code_manual = True
        self._update_account_suggestion()
        if self.page:
            self.page.update()

    def _category_attributes_changed(
        self, event: ft.Event[ft.TextField] | ft.Event[ft.Dropdown]
    ) -> None:
        self._update_category_suggestion()
        if self.page:
            self.page.update()

    def _category_code_changed(self, event: ft.Event[ft.TextField]) -> None:
        self.category_code_manual = True
        self._update_category_suggestion()
        if self.page:
            self.page.update()

    def _account_suggestion_selected(self, event: ft.Event[ft.Dropdown]) -> None:
        selected = event.control.value
        if selected is None:
            return
        self.account_id.value = selected
        self.account_code_manual = True
        self._update_account_suggestion()
        if self.page:
            self.page.update()

    def _category_suggestion_selected(self, event: ft.Event[ft.Dropdown]) -> None:
        selected = event.control.value
        if selected is None:
            return
        self.category_id.value = selected
        self.category_code_manual = True
        self._update_category_suggestion()
        if self.page:
            self.page.update()

    def _regenerate_account_suggestion(self, event: ft.Event[ft.TextButton]) -> None:
        self._update_account_suggestion(regenerate=True)
        if self.page:
            self.page.update()

    def _regenerate_category_suggestion(self, event: ft.Event[ft.TextButton]) -> None:
        self._update_category_suggestion(regenerate=True)
        if self.page:
            self.page.update()

    def _category_parent_options(
        self, categories: tuple[Category, ...], root: str
    ) -> list[ft.DropdownOption]:
        options = [ft.DropdownOption(key="", text=self.tr("category_parent_none"))]
        options.extend(
            ft.DropdownOption(
                key=item.id,
                text=f"{item.name} ({item.id})",
                content=ft.Container(
                    ft.Text(
                        item.name,
                        size=16,
                        spans=[ft.TextSpan(f" ({item.id})", style=ft.TextStyle(size=12))],
                    ),
                    padding=ft.Padding.only(left=16 * (item.id.count(":") - 1)),
                ),
            )
            for item in sorted(categories, key=lambda item: item.id.split(":"))
            if item.active and item.id.startswith(f"{root}:")
        )
        return options

    def _refresh_category_parent_options(self) -> None:
        root = self.category_root.value or "Expenses"
        categories = (
            self.journal.ledger.categories
            if self.journal is not None and self.journal.ready
            else ()
        )
        options = self._category_parent_options(categories, root)
        self.category_parent.options = options
        if self.category_parent.value and self.category_parent.value not in {
            option.key for option in options
        }:
            self.category_parent.value = ""
        self.category_parent.label = self.tr("code_suggestion_parent")

    def _create_account(self, event: ft.Event[ft.Button]) -> None:
        journal = self._require_journal()
        source = journal.source
        today = self._today()
        account_opened = DateTimeFields(
            self.language,
            self._effective_zone(),
            today,
            device_zone=self._picker_zone(),
            compact=True,
            show_accounting_date=True,
        )
        account_opened.occurred.label = self.tr("account_opened_date")
        opening_effective = DateTimeFields(
            self.language,
            self._effective_zone(),
            today,
            device_zone=self._picker_zone(),
            compact=True,
            show_accounting_date=True,
        )
        opening_effective.occurred.label = self.tr("opening_balance_date")

        async def save() -> str | None:
            if journal.source != source:
                raise ConflictError("crud_stale")
            root = self.account_root.value or "Assets"
            code = validate_account_code(
                self.account_id.value or "",
                root,
                self._existing_identifiers(journal),
            )
            if code.collision:
                raise LedgerError("crud_code_collision")
            if not code.valid:
                raise LedgerError("crud_code_invalid")
            backup = await journal.create_account(
                code.code,
                self.account_name.value or "",
                self.currency.value or "",
                self.opening.value or "",
                root=root,
                opened=date.fromisoformat(account_opened.values()[0]),
                opening_date=date.fromisoformat(opening_effective.values()[0]),
            )
            self.account_id.value = self.account_name.value = self.opening.value = ""
            self.account_code_manual = False
            return backup

        self._update_account_suggestion()
        self._edit_dialog(
            self.tr("account_form_title"),
            [
                ft.Row([self.account_root]),
                self.account_name,
                self.account_id,
                self.account_suggestion_status,
                self.account_suggestion_regenerate,
                ft.Row([self.account_suggestion_alternatives]),
                self.currency,
                self.opening,
                account_opened,
                opening_effective,
            ],
            journal,
            save,
            stretch=True,
        )

    def _account_type_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._refresh_labels()
        self._update_account_suggestion()
        self.page.update()

    async def _save_default_fee(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            journal = self._require_journal()
            if journal is not self.default_fee_journal:
                raise ConflictError("crud_stale")
            previous = journal.default_fee_category
            selected = self.default_fee.value or ""
            settings = FeePreferences(self.fee_branches_draft, selected)
            await journal.set_default_fee_category(selected, self.default_fee_source)
            try:
                if self.preferences is None or not await self.preferences.set(
                    FEE_CATEGORIES_KEY, settings.serialize()
                ):
                    raise PreferencesError("default_fee_save_failed")
            except Exception as exc:
                await journal.set_default_fee_category(previous, journal.source)
                self.default_fee.value = previous
                if isinstance(exc, PreferencesError):
                    raise
                raise PreferencesError("default_fee_save_failed") from exc
            self.fee_preferences = settings
            self.default_fee_original = self.default_fee.value
            self.default_fee_notice.visible = False
            self._set_message("default_fee_saved")

        await self._action(save)

    @property
    def default_fee_category(self) -> str:
        return self.fee_preferences.default

    def _fee_category_options(
        self, journal: Journal, branches: tuple[str, ...], *, retained: str | None = None
    ) -> list[ft.DropdownOption]:
        items = {
            category.id: category.name
            for category in journal.ledger.categories
            if category.id.startswith("Expenses:")
            and (
                category.id == retained
                or (
                    journal.ledger.category_active(category.id)
                    and fee_category_in_branches(category.id, branches)
                )
            )
        }
        if not any(
            c.id == BANK_FEES for c in journal.ledger.categories
        ) and fee_category_in_branches(BANK_FEES, branches):
            items[BANK_FEES] = self.tr("transfer_bank_fees")
        return [
            ft.DropdownOption(
                key=identifier, text=self._category_label(identifier, items[identifier])
            )
            for identifier in sorted(items, key=lambda identifier: identifier.split(":"))
        ]

    def _render_fee_settings(self) -> None:
        if self.journal is None or not self.journal.ready:
            return
        items = {
            category.id: category.name
            for category in self.journal.ledger.categories
            if category.id.startswith("Expenses:")
        }
        if BANK_FEES not in items:
            items[BANK_FEES] = self.tr("transfer_bank_fees")
        for identifier in (*items, *self.fee_branches_draft):
            parts = identifier.split(":")
            for length in range(1, len(parts) + 1):
                path = ":".join(parts[:length])
                items.setdefault(path, parts[length - 1])
        items["Expenses"] = self.tr("fee_categories_all")
        choices = tuple(
            (
                identifier,
                items[identifier],
                fee_category_in_branches(identifier, self.fee_branches_draft),
                any(identifier.startswith(branch + ":") for branch in self.fee_branches_draft),
            )
            for identifier in sorted(items, key=lambda identifier: identifier.split(":"))
        )
        if choices != self.fee_choices_state:
            self.fee_choices_state = choices
            self.fee_category_choices.controls = [
                ft.Container(
                    ft.Checkbox(
                        label=label,
                        tooltip=identifier,
                        data=identifier,
                        value=checked,
                        disabled=inherited,
                        on_change=self._fee_branch_changed,
                    ),
                    padding=ft.Padding.only(left=16 * identifier.count(":")),
                )
                for identifier, label, checked, inherited in choices
            ]
        options = self._fee_category_options(self.journal, self.fee_branches_draft)
        if [(o.key, o.text) for o in options] != [
            (o.key, o.text) for o in self.default_fee.options
        ]:
            self.default_fee.options = options

    def _fee_branch_changed(self, event: ft.Event[ft.Checkbox]) -> None:
        identifier = str(event.control.data)
        branches = set(self.fee_branches_draft)
        if event.control.value:
            branches = {branch for branch in branches if not branch.startswith(identifier + ":")}
            branches.add(identifier)
        else:
            branches.discard(identifier)
        self.fee_branches_draft = tuple(sorted(branches))
        if not fee_category_in_branches(self.default_fee.value or "", self.fee_branches_draft):
            self.default_fee.value = None
        self._render_fee_settings()
        self.page.update()

    async def _apply_default_fee(self) -> None:
        journal = self._require_journal()
        try:
            await journal.set_default_fee_category(self.default_fee_category, journal.source)
        except LedgerError as exc:
            if str(exc) != "incoming_category":
                raise
            self.default_fee_notice.value = self.tr("default_fee_unavailable")
            self.default_fee_notice.visible = True
        else:
            self.default_fee_notice.visible = False

    def _create_category(self, event: ft.Event[ft.Button]) -> None:
        journal = self._require_journal()
        source = journal.source
        category_opened = DateTimeFields(
            self.language,
            self._effective_zone(),
            device_zone=self._picker_zone(),
            compact=True,
        )
        category_opened.occurred.label = self.tr("category_available_date")

        async def save() -> str | None:
            if journal.source != source:
                raise ConflictError("crud_stale")
            root = self.category_root.value or "Expenses"
            parent = self.category_parent.value or None
            code = validate_category_code(
                self.category_id.value or "",
                root,
                self._existing_identifiers(journal),
                parent=parent,
            )
            if code.collision:
                raise LedgerError("crud_code_collision")
            if not code.valid:
                raise LedgerError("crud_code_invalid")
            backup = await journal.create_category(
                code.code,
                self.category_name.value or "",
                root=root,
                opened=date.fromisoformat(category_opened.values()[0]),
            )
            self.category_id.value = self.category_name.value = ""
            self.category_parent.value = ""
            self.category_code_manual = False
            return backup

        self._refresh_category_parent_options()
        self._update_category_suggestion()
        self._edit_dialog(
            self.tr("category_form_title"),
            [
                ft.Row([self.category_root]),
                ft.Row([self.category_parent]),
                self.category_name,
                self.category_id,
                self.category_suggestion_status,
                self.category_suggestion_regenerate,
                self.category_suggestion_alternatives,
                category_opened,
            ],
            journal,
            save,
            stretch=True,
        )

    def _category_type_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._refresh_category_parent_options()
        self._update_category_suggestion()
        if self.page:
            self.page.update()

    def _accounting_tab_changed(self, event: ft.Event[ft.Tabs]) -> None:
        self.accounts_panel.visible = event.control.selected_index == 0
        self.categories_panel.visible = event.control.selected_index == 1
        self.budgets_panel.visible = event.control.selected_index == 2
        self.page.update()

    def _report_tab_changed(self, event: ft.Event[ft.Tabs]) -> None:
        self.report_overview.visible = event.control.selected_index == 0
        self.budgets.visible = event.control.selected_index == 1
        if self.budgets.visible:
            self.budgets.request_refresh()
        self._apply_layout(self.page.width or self.page.window.width)
        self.page.update()

    def _navigate(self, screen: str) -> None:
        if screen == "settings" and self.current_screen != "settings":
            self.settings_return_screen = self.current_screen
        self.current_screen = screen
        self._refresh_labels()
        if screen == "reports":
            self._render_reports()
        self._apply_layout((self.page.width or self.page.window.width) if self.page else 390)
        if self.page:
            self.page.update()

    def _bar_changed(self, event: ft.Event[ft.NavigationBar]) -> None:
        screens = ("home", "transactions", "accounts", "reports")
        self._navigate(screens[event.control.selected_index])

    def _open_settings(self, event: ft.Event[ft.IconButton]) -> None:
        self._navigate("settings")

    def _back_from_settings(self, event: ft.Event[ft.IconButton]) -> None:
        self._navigate(self.settings_return_screen)

    def _rail_changed(self, event: ft.Event[ft.NavigationRail]) -> None:
        if event.control.selected_index is not None:
            screens = ("home", "transactions", "accounts", "reports", "settings")
            self._navigate(screens[event.control.selected_index])

    def _go_accounts(self, event: ft.Event[ft.TextButton]) -> None:
        self.accounting_tabs.selected_index = 0
        self.accounts_panel.visible = True
        self.categories_panel.visible = False
        self.budgets_panel.visible = False
        self._navigate("accounts")

    def _picker_zone(self) -> str | None:
        if self.bridge is not None and self.system_zone == "system":
            return None
        return self.system_zone

    def _today(self) -> date:
        return local_datetime(datetime.now().astimezone(), self._effective_zone()).date()

    def _diagnostics(self, event: ft.Event[ft.TextButton]) -> None:
        if not self.diagnostics.controls:
            from app.ui.beancount import BeancountDiagnosticsView
            from app.ui.home import StorageView

            self.diagnostics.controls.extend([BeancountDiagnosticsView(), StorageView()])
        self.diagnostics_visible = not self.diagnostics_visible
        self.diagnostics.visible = self.diagnostics_visible
        self._refresh_labels()
        self.page.update()
