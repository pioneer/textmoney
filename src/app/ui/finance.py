import asyncio
import inspect
import logging
import re
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from heapq import nlargest
from itertools import groupby
from typing import Literal, cast
from uuid import uuid4
from zoneinfo import available_timezones

import flet as ft
from browser_history_bridge import BrowserHistory
from finance_saf import FinanceSaf
from notification_bridge import NotificationBridge
from popup_keyboard import CategoryMenuConnector

from app.domain.category_migration import CategoryMigration, migrated_category
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
    Ledger,
    LedgerError,
    OrdinaryBatchValidationError,
    OrdinaryTransactionDraft,
    Posting,
    Transaction,
    cashflow_amounts,
    draft_movements,
    exchange_values,
    money,
)
from app.domain.notification_rules import Draft as NotificationDraft
from app.domain.notification_rules import Rule as NotificationRule
from app.domain.operations import PAGE_SIZE, OperationScope, transaction_key
from app.domain.reporting import (
    AccountBalance,
    CategoryReport,
    CurrencyReport,
    PeriodReport,
    account_balances,
    currency_totals,
    period_bounds,
    period_report,
    shift_month,
)
from app.domain.storage import ConflictError
from app.icons import DISPLAY_SIZE, IconAsset, IconError, IconStore
from app.infrastructure.ai import AIError, AIService, AISettings, Capability
from app.infrastructure.android_storage import AndroidFolder, unwrap
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.icon_files import AndroidIconFiles, DesktopIconFiles
from app.infrastructure.notification_import import (
    journal_identity,
    matching_rule_id,
    read_review_draft,
    save_reviewed_notification,
)
from app.infrastructure.notification_store import (
    NotificationStore,
    NotificationStoreError,
    QueueItem,
)
from app.infrastructure.notifications_android import AndroidNotifications, NotificationBridgeError
from app.infrastructure.preferences import (
    DesktopPreferences,
    FeePreferences,
    PreferencesError,
    fee_category_in_branches,
    migrated_fee_branches,
    parse_default_account,
)
from app.infrastructure.record_files import AndroidRecordFiles, DesktopRecordFiles
from app.journal import FILENAME, Journal
from app.localization import (
    LANGUAGES,
    date_input,
    format_amount,
    format_date,
    format_timestamp,
    localized_error,
    system_language,
    translate,
    translate_plural,
)
from app.shared_defaults import POLICY as DEFAULTS_POLICY
from app.shared_defaults import DefaultValue, SharedDefaults, SharedDefaultsError
from app.storage import RecoveryRequired, StorageError
from app.timekeeping import local_datetime, named_zone
from app.ui.ai_settings import AISettingsPanel
from app.ui.budgets import BudgetView
from app.ui.datetime_fields import DateTimeFields
from app.ui.dialogs import EditorDialog, deletion_title, show_dialog
from app.ui.home_budgets import HomeBudgets, home_heading
from app.ui.icons import (
    IconDraft,
    IconField,
    IconPicker,
    IconReference,
    IconRemovalDialog,
    IconTarget,
    icon_image,
)
from app.ui.layout import (
    HoverRow,
    action_toolbar,
    month_navigation,
    settings_expander,
    settings_field,
    settings_section,
)
from app.ui.notification_ai import NotificationAIConsent
from app.ui.notification_apply import NotificationRuleApplication
from app.ui.notification_inbox import NotificationInbox
from app.ui.notification_recovery import NotificationRecovery
from app.ui.notification_rules import AIStarter, DraftCallback, NotificationRuleEditor
from app.ui.notification_settings import NotificationSettingsPanel
from app.ui.operations import OperationsLocation, OperationsView
from app.ui.theme import (
    DARK_NAVIGATION_SURFACE,
    NAVIGATION_SURFACE,
    action_gradient,
    click_cursor,
    desktop_pointer,
    selection_gradient,
    summary_theme,
)
from app.ui.tree_style import (
    TREE_MENU_ROW_HEIGHT,
    TREE_MOBILE_MENU_ROW_HEIGHT,
    TREE_ROW_HEIGHT,
    CategoryConnectorRow,
    CategoryMenuAnchor,
    compact_tree_leaf,
    compact_tree_theme,
    compact_tree_tile,
)
from app.version import release_identity

logger = logging.getLogger(__name__)
FOLDER_KEY = "textmoney.folder.v1"
LANGUAGE_KEY = "textmoney.language.v1"
THEME_KEY = "textmoney.theme.v1"
TIMEZONE_KEY = "textmoney.timezone.v1"
BACKUP_CLEANUP_KEY = "textmoney.backup-cleanup.v1"
HOME_BUDGETS_KEY = "textmoney.home-budgets.v1"
DEFAULT_FEE_KEY = "textmoney.default-fee-category.v1"
DEFAULT_ACCOUNT_KEY = "textmoney.default-account.v1"
FEE_CATEGORIES_KEY = "textmoney.fee-categories.v1"
COMPACT_MAX_WIDTH = 599
MEDIUM_MAX_WIDTH = 1023
MAX_CONTENT_WIDTH = 1180
RAIL_DESTINATIONS = (
    "home",
    "transactions",
    "budgets",
    "reports",
    "accounts",
    "categories",
    "settings",
)


def mobile_destination_groups() -> tuple[tuple[str, ...], tuple[str, ...]]:
    destinations = tuple(screen for screen in RAIL_DESTINATIONS if screen != "settings")
    return destinations[:4], (*destinations[4:], "settings")


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


@dataclass(frozen=True)
class NavigationVisit:
    screen: str
    operations: OperationsLocation | None = None
    notification_inbox: bool = False


@dataclass
class NotificationFormState:
    sign: list[str]
    special: str | None
    account: str | None
    category: str | None
    amount: str
    destination: str | None
    destination_amount: str
    currency: str
    description: str
    payee: str
    date: str
    time: str
    fee_enabled: bool
    fee: str
    fee_mode: str | None
    fee_category: str | None
    fee_currency: str | None
    reference: str | None
    category_values: dict[str, str | None]
    fee_drafts: dict[str, tuple[bool, str, str | None, str | None, str | None]]


@dataclass
class NotificationReview:
    store: NotificationStore
    item: QueueItem
    snapshot: str = ""
    state: NotificationFormState | None = None


@dataclass
class BatchRow:
    when: DateTimeFields
    amount: ft.TextField
    description: ft.TextField
    account: ft.Dropdown
    category: ft.Dropdown
    override_payee: ft.Checkbox
    payee: ft.TextField
    title: ft.Text
    details_button: ft.TextButton
    remove_button: ft.IconButton
    details: ft.Column
    error: ft.Text
    container: ft.Column


class FinanceView(ft.Row):
    def __init__(self, *, mobile_preview: bool = False) -> None:
        super().__init__(expand=True, spacing=0, alignment=ft.MainAxisAlignment.CENTER)
        self.journal: Journal | None = None
        self.icons: IconStore | None = None
        self.icon_revision = 0
        self.next_icon_poll = 0.0
        self._rendered_key: tuple[object, ...] | None = None
        self.icon_notice = ft.Text("", visible=False, color=ft.Colors.ERROR, selectable=True)
        self.preferences: ft.SharedPreferences | DesktopPreferences | None = None
        self._ai_settings: AISettings | None = None
        self.ai_panel = AISettingsPanel(
            self.tr, self.ai_settings, notification_rules=lambda: self.notification_supported
        )
        self.bridge: FinanceSaf | None = None
        self.notification_store: NotificationStore | None = None
        self.notification_inbox: NotificationInbox | None = None
        self.notification_settings: NotificationSettingsPanel | None = None
        self.notification_section: ft.Container | None = None
        self.notification_supported = False
        self.notification_initializing = False
        self.notification_failed = False
        self.notification_retry = ft.TextButton(on_click=self._retry_notification_setup)
        self.notification_inbox_open = False
        self.notification_reviews: dict[int, NotificationReview] = {}
        self.notification_ai_busy = False
        self.notification_import_button = ft.TextButton(on_click=self._open_notification_inbox)
        self.worker: Future[None] | None = None
        self.lock = asyncio.Lock()
        self.language = "en"
        self.manual_language = False
        self.system_language_name = "en"
        self.theme_name = "system"
        self.category_menu_row_height = TREE_MENU_ROW_HEIGHT
        self.timezone_name = "system"
        self.auto_backup_cleanup = True
        self.show_home_budgets = True
        self.default_account_id: str | None = None
        self.default_account_original: str | None = None
        self.shared_defaults: SharedDefaults | None = None
        self.shared_defaults_journal: Journal | None = None
        self.shared_defaults_error: str | None = None
        self.default_account_heads: tuple[str, ...] = ()
        self.default_fee_heads: tuple[str, ...] = ()
        self.legacy_default_account: str | None = None
        self.legacy_fee_preferences = FeePreferences()
        self.legacy_defaults_invalid = False
        self.shared_notice = ft.Text("", visible=False, color=ft.Colors.ERROR, selectable=True)
        self.shared_details = ft.Text("", selectable=True)
        self.shared_review = ft.TextButton(on_click=self._review_shared_defaults)
        self.fee_preferences = FeePreferences()
        self.fee_branches_draft = self.fee_preferences.branches
        self.fee_branches_original = self.fee_preferences.branches
        self.possible_conflict_copies: list[str] = []
        self.sibling_scan_failed = False
        self.system_zone = "system"
        self.screen_class = ScreenClass.COMPACT
        self.current_screen = "home"
        self._history = [NavigationVisit("home")]
        self.history_index = 0
        self.desktop_navigation = False
        self._mobile_preview = mobile_preview
        self._mobile_item_actions = False
        self._desktop_pointer = False
        self.browser_routes: list[str] = []
        self._browser_route_prefix = uuid4().hex
        self._browser_route_sequence = 0
        self._browser_pending_routes: dict[str, asyncio.Event] = {}
        self._browser_route_lock = asyncio.Lock()
        self._browser_back_waiter: asyncio.Event | None = None
        self._browser_history: BrowserHistory | None = None
        self.message_key = "no_folder"
        self.error_message: str | None = None
        self.diagnostics_visible = False
        self.startup_complete = False
        self.startup_spinner = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.ProgressRing(),
        )

        self.message = ft.Text("", selectable=True, expand=True)
        self.message_icon = ft.Icon(
            ft.Icons.INFO_OUTLINE, size=18, color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.message_status = ft.Row(
            [self.message_icon, self.message],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        self.folder_label = ft.Text("", selectable=True)
        self.access = ft.Text("")
        self.balance_warning = ft.Text("", color=ft.Colors.ERROR, visible=False)
        self.external_notice = ft.Column(spacing=4, visible=False)
        self.conflict_copy_notice = ft.Text("", selectable=True, visible=False)
        self.account_root = ft.Dropdown(
            value="Assets", expand=True, on_select=self._account_type_changed
        )
        self.default_fee = ft.Dropdown(expand=True, on_select=self._entity_dropdown_selected)
        self.default_fee_row = ft.Row([self.default_fee])
        self.default_account = ft.Dropdown(
            expand=True, value="", on_select=self._entity_dropdown_selected
        )
        self.default_account_save = ft.Button(on_click=self._save_default_account)
        self.default_account_notice = ft.Text("", visible=False)
        self.default_account_journal: Journal | None = None
        self.fee_category_choices = ft.Column(spacing=0)
        self.fee_choices_state: tuple[tuple[str, str, bool, bool], ...] = ()
        self.fee_category_settings = ft.ExpansionTile(
            title="", controls=[self.fee_category_choices], maintain_state=True
        )
        self.default_fee_save = ft.Button(on_click=self._save_default_fee)
        self.default_fee_notice = ft.Text("", visible=False)
        self.backup_cleanup = ft.Checkbox(value=True, on_change=self._backup_cleanup_changed)
        self.home_budgets_label = ft.Text()
        self.home_budgets_toggle = ft.Switch(
            label=self.home_budgets_label, value=True, on_change=self._home_budgets_changed
        )
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
        self.operating_currencies = ft.TextField(width=320, margin=ft.Margin.only(top=8))
        self.operating_save = ft.Button(on_click=self._save_operating_currencies)
        self.operating_cancel = ft.TextButton(on_click=self._reset_operating_currencies)
        self.advanced_settings = ft.ExpansionTile(
            title=self.tr("advanced"),
            controls=[
                ft.Text(self.tr("crud_operating_help")),
                self.operating_currencies,
                ft.Text(self.tr("display_currency_codes"), size=12),
                action_toolbar(self.operating_cancel, self.operating_save),
            ],
            maintain_state=True,
            controls_padding=16,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.ai_section = settings_expander(self.ai_panel.control)
        self.advanced_section = settings_expander(self.advanced_settings)
        self.operating_source = ""
        self.operating_original = ""
        self.operating_journal: Journal | None = None
        self.account_list = ft.Column(
            spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
        self.category_list = ft.Column(spacing=4)
        self.category_expansion: dict[str, bool] = {}
        self.operations = OperationsView(self)
        self.history = self.operations.history
        self.operations_account = ft.Dropdown(
            value="",
            expand=True,
            text_style=ft.TextStyle(size=16),
            on_select=self._operations_filter_changed,
        )
        self.operations_category = ft.Dropdown(
            value="",
            expand=True,
            text_style=ft.TextStyle(size=16),
            on_select=self._operations_filter_changed,
        )
        self.operations_category_row = ft.Row([self.operations_category])
        self.operations_filter_toggle = ft.IconButton(
            icon=ft.Icons.FILTER_LIST,
            on_click=self._toggle_operations_filters,
        )
        self.operations_filters = ft.Column(
            [
                ft.Row([self.operations_account]),
                self.operations_category_row,
                *self.operations.extra_filters,
            ],
            spacing=8,
            visible=False,
        )
        self.operations_totals_values = ft.Row(spacing=10, tight=True)
        self.operations_totals_label = ft.Text(
            size=14, color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True
        )
        self.operations_totals = ft.Container(
            visible=False,
            theme=summary_theme(),
            dark_theme=summary_theme(dark=True),
            content=ft.Container(
                bgcolor=ft.Colors.SURFACE,
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Row(
                    [self.operations_totals_label, self.operations_totals_values],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )
        today = self._today()
        self.report_period = ft.Dropdown(
            expand=True,
            value="current_month",
            on_select=self._report_period_changed,
        )
        self.report_month: date | None = None
        self.report_previous = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT, on_click=self._report_previous_month
        )
        self.report_next = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT, on_click=self._report_next_month
        )
        self.report_month_label = ft.Text(
            text_align=ft.TextAlign.CENTER,
            size=16,
            weight=ft.FontWeight.W_500,
            color=ft.Colors.PRIMARY,
        )
        self.report_month_navigation = month_navigation(
            self.report_previous,
            ft.Row(
                [
                    ft.Icon(ft.Icons.CALENDAR_TODAY, size=16, color=ft.Colors.PRIMARY),
                    self.report_month_label,
                ],
                expand=True,
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            self.report_next,
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
        self.report_balances = ft.Column(
            spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
        self.report_categories = ft.Column(spacing=16)
        self.report_overview = ft.Column(
            spacing=16,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Row([self.report_period]),
                self.report_month_navigation,
                self.report_range,
                self.report_error,
                self.report_summary,
                self.report_categories,
                ft.Container(content=self.report_balances, padding=ft.Padding.only(bottom=96)),
            ],
        )
        self._report_ledger: object | None = None
        self._report_range: tuple[date, date] | None = None
        self._report_data: PeriodReport | None = None
        self._report_balances: tuple[AccountBalance, ...] = ()
        self.budgets = BudgetView(self)
        self.reports_screen = ft.Column([self.report_overview], spacing=16)
        self.budgets_screen = ft.Column([self.budgets], spacing=16)
        self.home_balances = ft.Row(
            wrap=True,
            spacing=12,
            run_spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        self.home_activity = ft.Column(spacing=0)
        self.home_budgets = HomeBudgets(self)
        self.home_transactions_open = ft.TextButton(on_click=self._home_transactions)
        self.home_accounts_open = ft.TextButton(on_click=self._home_accounts)
        self.accounts_panel_content = ft.Column(spacing=16)
        self.categories_panel_content = ft.Column(spacing=16)
        self.accounts_panel = ft.Container(
            content=self.accounts_panel_content,
        )
        self.categories_panel = ft.Container(
            theme=compact_tree_theme(),
            content=self.categories_panel_content,
        )
        self.account_save = ft.Button(
            icon=ft.Icons.ADD, width=240, height=48, on_click=self._create_account
        )
        self.category_save = ft.Button(
            icon=ft.Icons.ADD, width=240, height=48, on_click=self._create_category
        )
        self.fab_label = ft.Text(
            self.tr("add_transaction"),
            color=ft.Colors.ON_PRIMARY,
            size=14,
            weight=ft.FontWeight.W_500,
        )
        self.fab_surface = ft.Container(
            content=ft.Row(
                [ft.Icon(ft.Icons.ADD, color=ft.Colors.ON_PRIMARY), self.fab_label],
                tight=True,
                spacing=8,
            ),
            gradient=action_gradient(),
            border_radius=16,
            padding=ft.Padding.symmetric(horizontal=16),
            height=56,
            # Keep the native button's ink/focus overlay visible through the fill.
            opacity=0.38,
        )
        self.fab = ft.FloatingActionButton(
            content=self.fab_surface,
            on_click=self._new_transaction,
            disabled=True,
        )
        self.select_button = ft.Button(on_click=self._select)
        self.welcome_select_button = ft.Button(on_click=self._select)
        self.reload_button = ft.Button(on_click=self._reload)
        self.diagnostics_toggle = ft.TextButton(on_click=self._diagnostics)
        self.diagnostics = ft.Column(visible=False, spacing=12)
        self.language_choice = ft.Dropdown(
            expand=True,
            options=[],
            value="system",
            on_select=self._language_changed,
        )
        self.theme_choice = ft.Dropdown(
            expand=True,
            options=[],
            value=self.theme_name,
            on_select=self._theme_changed,
        )
        self.timezone_choice = ft.Dropdown(
            expand=True,
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
        self.categories_screen = ft.Column(spacing=16)
        self.settings_screen = ft.Column(
            spacing=16, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
        self.screen_host = ft.Container(expand=True, content=self.home_screen)

        self.page_title = ft.Text(size=25, weight=ft.FontWeight.W_600)
        version, build = release_identity()
        self.release_label = ft.Text(f"v{version} ({build})", size=12, color=ft.Colors.OUTLINE)
        self.navigation_back = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            on_click=self._back_clicked,
            visible=False,
        )
        self.navigation_forward = ft.IconButton(
            icon=ft.Icons.ARROW_FORWARD,
            on_click=self._forward_clicked,
            visible=False,
        )
        self.overflow_menu = ft.PopupMenuButton(
            padding=0,
            menu_position=ft.PopupMenuPosition.OVER,
        )
        self.more_semantics = ft.Semantics(content=self.overflow_menu)
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
        self.desktop_rail = ft.Column(spacing=0, tight=True, scroll=ft.ScrollMode.AUTO)
        self.desktop_rail_surface = ft.Container(
            theme=ft.Theme(color_scheme=ft.ColorScheme(surface=NAVIGATION_SURFACE)),
            dark_theme=ft.Theme(color_scheme=ft.ColorScheme(surface=DARK_NAVIGATION_SURFACE)),
            content=ft.Container(
                bgcolor=ft.Colors.SURFACE,
                padding=ft.Padding.symmetric(horizontal=5),
                content=self.desktop_rail,
            ),
        )
        self.desktop_rail_divider = ft.VerticalDivider(width=1)
        self.desktop_rail_buttons: list[ft.TextButton] = []
        self.desktop_rail_semantics: list[ft.Semantics] = []
        self.bottom_buttons: list[ft.TextButton] = []
        self.bottom_semantics: list[ft.Semantics] = []
        self.bottom_items: list[tuple[ft.Container, ft.Icon, ft.Text]] = []
        self.navigation_row = ft.Row(spacing=0)
        # A Flet height here also constrains the outer widget, leaving no room
        # for BottomAppBar's system-safe-area padding.
        self.navigation_bar = ft.BottomAppBar(
            content=self.navigation_row,
            padding=0,
            elevation=0,
        )
        self.body = ft.Column(
            expand=True,
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20, vertical=16),
                    content=ft.Row(
                        [
                            self.navigation_back,
                            self.navigation_forward,
                            self.header_titles,
                        ],
                    ),
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20, vertical=8),
                    content=ft.Column(
                        [self.message_status, self.icon_notice, self.shared_notice], spacing=4
                    ),
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
                self._screen_frame(),
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
            self.default_account,
            self.default_account_save,
            self.fee_category_settings,
        ]
        self._refresh_labels()
        self._refresh()

    def did_mount(self) -> None:
        self.notification_supported = (
            self.page.platform == ft.PagePlatform.ANDROID and not self.page.web
        )
        self._desktop_pointer = desktop_pointer(self.page, mobile_preview=self._mobile_preview)
        self._set_fab_ready(not self.fab.disabled)
        self._mobile_item_actions = self._mobile_preview or self.page.platform in {
            ft.PagePlatform.ANDROID,
            ft.PagePlatform.IOS,
        }
        self.page.padding = 0
        self.page.on_resize = self._resized
        self.page.on_locale_change = self._host_locale_changed
        self.desktop_navigation = (
            not self._mobile_preview
            and not self.page.web
            and self.page.platform
            in {ft.PagePlatform.LINUX, ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS}
        )
        if self.desktop_navigation:
            self.page.on_keyboard_event = self._navigation_key
        if self.page.web:
            self._setup_browser_navigation()
        if self.page.platform == ft.PagePlatform.ANDROID and not self.page.web:
            root = self.page.views[0]
            root.can_pop = False
            root.on_confirm_pop = self._system_back
            self.page.on_app_lifecycle_state_change = self._notification_lifecycle_changed
        self.worker = self.page.run_task(self._lifecycle)
        self._apply_layout(self.page.width or self.page.window.width)
        self.page.update()

    def _set_fab_ready(self, ready: bool) -> None:
        self.fab.disabled = not ready
        self.fab_surface.opacity = 0.9 if ready else 0.38
        self.fab.mouse_cursor = ft.MouseCursor.CLICK if ready and self._desktop_pointer else None

    def will_unmount(self) -> None:
        if self.page.on_keyboard_event == self._navigation_key:
            self.page.on_keyboard_event = None
        if self.page.web:
            self.page.on_route_change = None
        if self.page.platform == ft.PagePlatform.ANDROID and not self.page.web:
            root = self.page.views[0]
            root.can_pop = True
            root.on_confirm_pop = None
            if self.page.on_app_lifecycle_state_change == self._notification_lifecycle_changed:
                self.page.on_app_lifecycle_state_change = None
        self.budgets.cancel()
        self.home_budgets.cancel()
        if self.worker is not None:
            self.worker.cancel()

    def tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def ai_settings(self) -> AISettings:
        if self.page.web:
            raise AIError("ai_native_only")
        if self.preferences is None:
            raise AIError("ai_settings_error")
        if self._ai_settings is None:
            from flet_secure_storage import SecureStorage

            self._ai_settings = AISettings(self.preferences, SecureStorage())
        return self._ai_settings

    def _set_message(self, key: str) -> None:
        self.message_key = key
        self.error_message = None
        self._refresh_message()

    def _refresh_message(self) -> None:
        self.message.value = (
            localized_error(self.language, self.error_message)
            if self.error_message is not None
            else self.tr(self.message_key)
        )
        if (
            self.error_message is not None
            or self.message_key.startswith("error_")
            or self.message_key == "backups_saved_warning"
        ):
            self.message_icon.icon = ft.Icons.ERROR_OUTLINE
            self.message_icon.color = ft.Colors.ERROR
        elif self.message_key in {
            "journal_loaded",
            "journal_new",
            "external_reloaded",
            "reloaded",
            "saved",
            "saved_backup",
            "saved_new",
            "timezone_saved",
            "backups_setting_saved",
            "backups_cleaned",
            "default_account_saved",
            "default_fee_saved",
        }:
            self.message_icon.icon = ft.Icons.CHECK_CIRCLE_OUTLINE
            self.message_icon.color = ft.Colors.PRIMARY
        else:
            self.message_icon.icon = ft.Icons.INFO_OUTLINE
            self.message_icon.color = ft.Colors.ON_SURFACE_VARIANT
        self.message_status.visible = bool(self.message.value)

    def _set_error(self, exc: Exception) -> None:
        if isinstance(exc, NotificationStoreError):
            self.message_key = "notification_state_error"
            self.error_message = None
            logger.warning("Notification state operation refused (%s)", type(exc).__name__)
        elif isinstance(
            exc, (ConflictError, LedgerError, PreferencesError, StorageError, IconError)
        ):
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
        self._refresh_message()

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
        self.default_account.label = self.tr("default_account")
        self.default_account_save.content = self.tr("crud_save")
        self._render_default_account()
        self.page_title.value = self.tr(
            {
                "home": "nav_home",
                "transactions": "nav_transactions",
                "accounts": "accounts_title",
                "categories": "categories_title",
                "reports": "nav_reports",
                "budgets": "budget_title",
                "settings": "nav_settings",
            }[self.current_screen]
        )
        self._refresh_message()
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
        self.advanced_settings.title = ft.Text(self.tr("advanced"), size=18)
        self.advanced_settings.controls = [
            ft.Text(self.tr("crud_operating_help")),
            self.operating_currencies,
            ft.Text(self.tr("display_currency_codes"), size=12),
            action_toolbar(self.operating_cancel, self.operating_save),
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
        self._shared_defaults_labels()
        self._sync_report_period()
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
        for button, key in (
            (self.home_transactions_open, "home_view_transactions"),
            (self.home_accounts_open, "home_view_accounts"),
        ):
            button.tooltip = self.tr(key)
        self.home_budgets.sync()
        self._build_home_layout()
        self.budgets.sync()
        if self.current_screen == "budgets":
            self.budgets.request_refresh()

    def _build_navigation(self) -> None:
        bottom, overflow = mobile_destination_groups()
        pending = (
            self.notification_inbox.pending_count()
            if self.notification_supported and self.notification_inbox is not None
            else 0
        )
        badge = ("99+" if pending > 99 else str(pending)) if pending else None
        self.notification_import_button.badge = badge
        self.notification_import_button.tooltip = (
            self.tr("notification_pending_count", count=pending) if pending else None
        )
        destinations = {
            "home": ("nav_home", ft.Icons.HOME_OUTLINED, ft.Icons.HOME),
            "transactions": (
                "nav_transactions",
                ft.Icons.RECEIPT_LONG_OUTLINED,
                ft.Icons.RECEIPT_LONG,
            ),
            "budgets": ("budget_title", ft.Icons.BAR_CHART_OUTLINED, ft.Icons.BAR_CHART),
            "accounts": (
                "accounts_title",
                ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED,
                ft.Icons.ACCOUNT_BALANCE_WALLET,
            ),
            "categories": ("categories_title", ft.Icons.CATEGORY_OUTLINED, ft.Icons.CATEGORY),
            "reports": ("nav_reports", ft.Icons.INSIGHTS, ft.Icons.INSIGHTS),
            "settings": ("nav_settings", ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS),
        }
        selected = self._compact_selected_index()
        if tuple(button.data for button in self.bottom_buttons) != bottom:
            self.bottom_buttons = []
            self.bottom_items = []
            for screen in bottom:
                label, icon, _selected_icon = destinations[screen]
                content, indicator, icon_control, label_control = self._bottom_item_content(
                    self.tr(label), icon
                )
                self.bottom_buttons.append(
                    ft.TextButton(
                        content=content,
                        data=screen,
                        height=80,
                        style=ft.ButtonStyle(
                            padding=0,
                            shape=ft.RoundedRectangleBorder(radius=0),
                        ),
                        on_click=self._bottom_selected,
                    )
                )
                self.bottom_items.append((indicator, icon_control, label_control))
            self.bottom_semantics = [ft.Semantics(content=button) for button in self.bottom_buttons]
            # Announce each button and its selection as one accessible action.
            self.navigation_row.controls = [
                ft.Container(expand=1, content=ft.MergeSemantics(content=semantics))
                for semantics in (*self.bottom_semantics, self.more_semantics)
            ]
        for index, (button, semantics, screen, item) in enumerate(
            zip(self.bottom_buttons, self.bottom_semantics, bottom, self.bottom_items, strict=True)
        ):
            label, icon, selected_icon = destinations[screen]
            indicator, icon_control, label_control = item
            is_selected = index == selected
            semantics.selected = index == selected
            semantics.label = (
                f"{self.tr(label)}, {self.tr('notification_pending_count', count=pending)}"
                if screen == "transactions" and pending
                else None
            )
            button.tooltip = ft.Tooltip(self.tr(label), exclude_from_semantics=True)
            indicator.gradient = selection_gradient() if is_selected else None
            icon_control.icon = selected_icon if is_selected else icon
            icon_control.badge = badge if screen == "transactions" else None
            icon_control.color = (
                ft.Colors.ON_SECONDARY_CONTAINER if is_selected else ft.Colors.ON_SURFACE_VARIANT
            )
            label_control.value = self.tr(label)
        self.nav_rail.destinations = [
            ft.NavigationRailDestination(
                label=(
                    ft.Semantics(
                        content=ft.Text(self.tr(label)),
                        label=f"{self.tr(label)}, "
                        f"{self.tr('notification_pending_count', count=pending)}",
                        exclude_semantics=True,
                    )
                    if screen == "transactions" and pending
                    else self.tr(label)
                ),
                icon=ft.Icon(icon, badge=badge) if screen == "transactions" and badge else icon,
                selected_icon=ft.Container(
                    content=ft.Icon(
                        selected_icon,
                        color=ft.Colors.ON_SECONDARY_CONTAINER,
                        badge=badge if screen == "transactions" else None,
                    ),
                    width=56,
                    height=32,
                    alignment=ft.Alignment.CENTER,
                    border_radius=16,
                    gradient=selection_gradient(),
                ),
            )
            for screen in RAIL_DESTINATIONS
            for label, icon, selected_icon in [destinations[screen]]
        ]
        self.nav_rail.selected_index = RAIL_DESTINATIONS.index(self.current_screen)
        if self._desktop_pointer:
            if tuple(button.data for button in self.desktop_rail_buttons) != RAIL_DESTINATIONS:
                self.desktop_rail_buttons = [
                    ft.TextButton(
                        data=screen,
                        on_click=self._bottom_selected,
                        style=ft.ButtonStyle(
                            padding=0,
                            shape=ft.RoundedRectangleBorder(radius=24),
                            mouse_cursor=click_cursor(),
                        ),
                    )
                    for screen in RAIL_DESTINATIONS
                ]
                self.desktop_rail_semantics = [
                    ft.Semantics(content=button, container=True)
                    for button in self.desktop_rail_buttons
                ]
                self.desktop_rail.controls = [
                    ft.MergeSemantics(content=semantics)
                    for semantics in self.desktop_rail_semantics
                ]
            expanded = self.screen_class == ScreenClass.EXPANDED
            self.desktop_rail.width = 224 if expanded else 78
            for button, semantics, screen in zip(
                self.desktop_rail_buttons,
                self.desktop_rail_semantics,
                RAIL_DESTINATIONS,
                strict=True,
            ):
                label, icon, selected_icon = destinations[screen]
                is_selected = screen == self.current_screen
                indicator = ft.Container(
                    content=ft.Icon(
                        selected_icon if is_selected else icon,
                        size=24,
                        color=(
                            ft.Colors.ON_SECONDARY_CONTAINER
                            if is_selected
                            else ft.Colors.ON_SURFACE_VARIANT
                        ),
                    ),
                    width=56,
                    height=32,
                    alignment=ft.Alignment.CENTER,
                    border_radius=16,
                    gradient=selection_gradient() if is_selected else None,
                )
                text = ft.Text(
                    self.tr(label),
                    size=14 if expanded else 12,
                    color=ft.Colors.ON_SURFACE,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=expanded,
                )
                button.content = (
                    ft.Row(
                        [
                            ft.Container(
                                width=78, content=indicator, alignment=ft.Alignment.CENTER
                            ),
                            text,
                        ],
                        spacing=0,
                    )
                    if expanded
                    else ft.Column(
                        [indicator, text],
                        spacing=4,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    )
                )
                button.width = 224 if expanded else 78
                button.height = 48 if expanded else 68
                button.tooltip = ft.Tooltip(self.tr(label), exclude_from_semantics=True)
                semantics.selected = is_selected
        compact = self.screen_class == ScreenClass.COMPACT
        if tuple(item.data for item in self.overflow_menu.items if item.data) != overflow:
            items: list[ft.PopupMenuItem] = []
            for screen in overflow:
                if screen == "settings" and items:
                    items.append(ft.PopupMenuItem())
                items.append(ft.PopupMenuItem(data=screen, on_click=self._overflow_selected))
            self.overflow_menu.items = items
        for item in self.overflow_menu.items:
            if isinstance(item.data, str):
                label, icon, _selected_icon = destinations[item.data]
                item.content = self.tr(label)
                item.icon = icon
        self.overflow_menu.tooltip = ft.Tooltip(self.tr("nav_more"), exclude_from_semantics=True)
        more_content = self._bottom_content(
            self.tr("nav_more"), ft.Icons.MORE_HORIZ, selected == len(bottom)
        )
        self.overflow_menu.content = (
            ft.GestureDetector(content=more_content, mouse_cursor=ft.MouseCursor.CLICK)
            if self._desktop_pointer
            else more_content
        )
        self.more_semantics.selected = selected == len(bottom)
        self.overflow_menu.visible = compact and bool(overflow)
        desktop = self.desktop_navigation
        self.navigation_back.visible = desktop
        self.navigation_back.disabled = self.history_index == 0
        self.navigation_back.tooltip = self.tr("nav_back")
        self.navigation_forward.visible = desktop
        self.navigation_forward.disabled = self.history_index + 1 == len(self._history)
        self.navigation_forward.tooltip = self.tr("nav_forward")

    @staticmethod
    def _bottom_content(label: str, icon: ft.IconData, selected: bool) -> ft.Column:
        return ft.Column(
            [
                ft.Container(
                    content=ft.Icon(
                        icon,
                        size=24,
                        color=(
                            ft.Colors.ON_SECONDARY_CONTAINER
                            if selected
                            else ft.Colors.ON_SURFACE_VARIANT
                        ),
                    ),
                    width=64,
                    height=32,
                    alignment=ft.Alignment.CENTER,
                    border_radius=16,
                    gradient=selection_gradient() if selected else None,
                ),
                ft.Text(
                    label,
                    size=12,
                    color=ft.Colors.ON_SURFACE,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            height=80,
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    @staticmethod
    def _bottom_item_content(
        label: str, icon: ft.IconData
    ) -> tuple[ft.Column, ft.Container, ft.Icon, ft.Text]:
        icon_control = ft.Icon(icon, size=24)
        indicator = ft.Container(
            content=icon_control,
            width=64,
            height=32,
            alignment=ft.Alignment.CENTER,
            border_radius=16,
        )
        label_control = ft.Text(
            label,
            size=12,
            color=ft.Colors.ON_SURFACE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        return (
            ft.Column(
                [indicator, label_control],
                height=80,
                spacing=4,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            indicator,
            icon_control,
            label_control,
        )

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
            home_heading(
                self.tr("home_balances"),
                self.home_accounts_open,
                left_padding=12,
            ),
            self.home_balances,
            ft.Text(self.tr("home_recent"), size=21, weight=ft.FontWeight.W_600),
            self.home_activity,
        ]
        self.fab_label.value = self.tr("add_transaction")
        self._set_fab_ready(ready)
        self.transactions_screen.controls = [
            action_toolbar(self.operations.reset, self.operations_filter_toggle),
            self.operations_filters,
            self.operations.chips,
            self.operations_totals,
            self.operations.summary_help,
            self.operations.navigation[0],
            self.history,
            self.operations.status,
            ft.Container(content=self.operations.navigation[1], padding=ft.Padding.only(bottom=96)),
        ]
        if self.notification_supported:
            self.notification_import_button.content = self.tr("notification_inbox_title")
            self.transactions_screen.controls.insert(
                0, action_toolbar(self.notification_import_button)
            )
        if self.notification_inbox_open and self.notification_inbox is not None:
            self.notification_inbox.render()
            self.transactions_screen.controls = [self.notification_inbox.control]
        self.accounts_panel_content.controls = [
            action_toolbar(self.account_save),
            self.account_list,
        ]
        self.categories_panel_content.controls = [
            action_toolbar(self.category_save),
            self.category_list,
        ]
        self.accounts_screen.controls = [self.accounts_panel]
        self.categories_screen.controls = [self.categories_panel]
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
        if self.current_screen != "settings":
            self.ai_panel.clear_draft()
        self.ai_panel.render()
        self.settings_screen.controls = [
            settings_section(
                ft.Column(
                    [
                        ft.Text(self.tr("storage_section"), size=18),
                        ft.Text(self.tr("storage_explanation")),
                        ft.Text(self.tr("storage_filename")),
                        self.folder_label,
                        self.access,
                        action_toolbar(self.select_button, self.reload_button),
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
                        action_toolbar(self.backup_clean_button),
                    ],
                    spacing=12,
                ),
            ),
            settings_section(
                ft.Column(
                    [
                        ft.Text(self.tr("device_settings"), size=18),
                        ft.Text(self.tr("language"), size=18),
                        settings_field(ft.Row([self.language_choice])),
                        ft.Text(self.tr("appearance"), size=18),
                        settings_field(ft.Row([self.theme_choice])),
                        self.home_budgets_toggle,
                        ft.Text(self.tr("timezone"), size=18),
                        ft.Text(self.tr("timezone_help")),
                        settings_field(ft.Row([self.timezone_choice])),
                        action_toolbar(self.timezone_save),
                        ft.Divider(),
                        ft.Text(self.tr("journal_defaults"), size=18),
                        ft.Text(self.tr("shared_settings_help"), size=12),
                        self.shared_details,
                        self.shared_review,
                        settings_field(ft.Row([self.default_account])),
                        ft.Text(self.tr("default_account_help"), size=12),
                        action_toolbar(self.default_account_save),
                        self.default_account_notice,
                        ft.Divider(),
                        self.fee_category_settings,
                        ft.Text(self.tr("fee_categories_help"), size=12),
                        settings_field(self.default_fee_row),
                        action_toolbar(self.default_fee_save),
                        self.default_fee_notice,
                    ],
                    spacing=12,
                ),
            ),
            settings_section(
                ft.Column(
                    [
                        ft.Text(self.tr("about"), size=18),
                        ft.Text(self.tr("about_body")),
                        ft.Text(f"{self.tr('app_version')}: {version} ({build})"),
                    ],
                    spacing=12,
                ),
            ),
            self.ai_section,
            self.advanced_section,
            ft.Row([self.diagnostics_toggle]),
            self.diagnostics,
        ]
        if self.notification_supported:
            if self.notification_section is not None:
                self.settings_screen.controls.insert(2, self.notification_section)
            else:
                self.notification_retry.content = self.tr("notification_settings_refresh")
                self.notification_retry.disabled = self.notification_initializing
                self.settings_screen.controls.insert(
                    2,
                    settings_section(
                        ft.Column(
                            [
                                ft.Text(self.tr("notification_settings_title"), size=18),
                                ft.Text(
                                    self.tr(
                                        "notification_state_error"
                                        if self.notification_failed
                                        else "ai_loading"
                                    )
                                ),
                                self.notification_retry,
                            ]
                        )
                    ),
                )
        self.backup_cleanup.label = self.tr("backups_automatic")
        self.backup_cleanup.value = self.auto_backup_cleanup
        self.home_budgets_label.value = self.tr("home_budgets_show")
        self.home_budgets_toggle.value = self.show_home_budgets
        self.backup_clean_button.content = self.tr("backups_clean")
        self.backup_clean_button.disabled = not folder_ready
        screens = {
            "home": self.home_screen,
            "transactions": self.transactions_screen,
            "accounts": self.accounts_screen,
            "categories": self.categories_screen,
            "reports": self.reports_screen,
            "budgets": self.budgets_screen,
            "settings": self.settings_screen,
        }
        self.screen_host.content = screens[self.current_screen]

    def _selected_index(self) -> int | None:
        if self.screen_class != ScreenClass.COMPACT:
            return RAIL_DESTINATIONS.index(self.current_screen)
        return self._compact_selected_index()

    def _compact_selected_index(self) -> int | None:
        bottom, overflow = mobile_destination_groups()
        if self.current_screen in overflow:
            return len(bottom)
        return bottom.index(self.current_screen) if self.current_screen in bottom else None

    def _apply_layout(self, width: float | None) -> None:
        self.screen_class = screen_class(width)
        self.vertical_alignment = (
            ft.CrossAxisAlignment.STRETCH
            if self._desktop_pointer and self.screen_class != ScreenClass.COMPACT
            else ft.CrossAxisAlignment.CENTER
        )
        self._set_category_menu_row(self.operations_category_row, self.operations_category)
        self._set_category_menu_row(self.default_fee_row, self.default_fee)
        self.budgets.update_category_menu_layout()
        _bottom, overflow = mobile_destination_groups()
        secondary = self.current_screen in overflow
        if not self.startup_complete:
            self.controls = [self.startup_spinner]
            if self.page:
                self.page.bottom_appbar = None
                self.page.floating_action_button = None
            return
        if self.screen_class == ScreenClass.COMPACT:
            self.controls = [self.body]
            self.body.width = width
            if self.page:
                self.page.bottom_appbar = self.navigation_bar
            rail_width = 0
        else:
            expanded = self.screen_class == ScreenClass.EXPANDED
            rail_width = 236 if expanded else 90
            self.nav_rail.extended = expanded
            self.nav_rail.label_type = ft.NavigationRailLabelType.ALL if not expanded else None
            self.body.width = min(MAX_CONTENT_WIDTH, max(320, (width or 900) - rail_width))
            self.controls = (
                [self.desktop_rail_surface, self.desktop_rail_divider, self.body]
                if self._desktop_pointer
                else [
                    ft.Container(
                        theme=ft.Theme(color_scheme=ft.ColorScheme(surface=NAVIGATION_SURFACE)),
                        dark_theme=ft.Theme(
                            color_scheme=ft.ColorScheme(surface=DARK_NAVIGATION_SURFACE)
                        ),
                        content=ft.Container(
                            bgcolor=ft.Colors.SURFACE,
                            padding=ft.Padding.symmetric(horizontal=5),
                            content=self.nav_rail,
                        ),
                    ),
                    ft.VerticalDivider(width=1),
                    self.body,
                ]
            )
            if self.page:
                self.page.bottom_appbar = None
        available = max(0, (self.body.width or 390) - 40)
        self.home_budgets_label.width = min(300, max(80, available - 108))
        self.accounts_panel.width = self.categories_panel.width = min(
            MAX_CONTENT_WIDTH - 40, available
        )
        action_width = available if available < 560 else 240
        self.account_save.width = self.category_save.width = action_width
        self.budgets.add.width = self.budgets.history.width = action_width
        self._build_navigation()
        self.home_budgets.sync()
        self._build_home_layout()
        self._update_transaction_separators()
        self.screen_host.padding = (
            ft.Padding.only(bottom=96)
            if (self.current_screen in {"home", "budgets"} or secondary)
            and not (secondary and self.screen_class == ScreenClass.COMPACT)
            else None
        )
        if self.page:
            self.fab_label.value = self.tr("add_transaction")
            self._set_fab_ready(bool(self.journal and self.journal.ready))
            self.page.floating_action_button = (
                None if self.screen_class == ScreenClass.COMPACT and secondary else self.fab
            )

    def _build_home_layout(self) -> None:
        balance_width = (
            self.accounts_panel.width
            if self.screen_class == ScreenClass.COMPACT and self._mobile_item_actions
            else 250
        )
        for card in self.home_balances.controls:
            if isinstance(card, ft.Container):
                card.width = balance_width
        if self.screen_class == ScreenClass.EXPANDED:
            self.home_screen.controls = [
                self.welcome,
                ft.Row(
                    [
                        ft.Container(
                            expand=3,
                            content=ft.Column(
                                [
                                    home_heading(
                                        self.tr("home_balances"),
                                        self.home_accounts_open,
                                        left_padding=12,
                                    ),
                                    self.home_balances,
                                    self.home_budgets,
                                ],
                                spacing=12,
                            ),
                        ),
                        ft.Container(
                            expand=2,
                            content=ft.Column(
                                [
                                    home_heading(
                                        self.tr("home_recent"),
                                        self.home_transactions_open,
                                        left_padding=12,
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
                home_heading(
                    self.tr("home_balances"),
                    self.home_accounts_open,
                    left_padding=12,
                ),
                self.home_balances,
                home_heading(
                    self.tr("home_recent"),
                    self.home_transactions_open,
                    left_padding=12,
                ),
                self.home_activity,
                self.home_budgets,
            ]
        self.home_budgets.render()

    def _refresh_key(self) -> tuple[object, ...]:
        journal = self.journal
        ready = bool(journal and journal.ready)
        return (
            id(journal),
            id(journal.ledger) if journal else None,
            ready,
            journal.today() if journal and ready else None,
            self.language,
            self.icon_revision,
            self.default_account_id,
            self.fee_preferences,
        )

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
        self._sync_operations_filters()
        if not ready or self.journal is None:
            self.account_list.controls = [ft.Text(self.tr("accounts_empty"))]
            self.category_list.controls = [ft.Text(self.tr("categories_empty"))]
            self.operations.sync()
            self.operations_totals.visible = False
            self.operations_totals_values.controls = []
            self.home_balances.controls = [ft.Text(self.tr("home_no_accounts"))]
            self.home_activity.controls = [ft.Text(self.tr("home_no_transactions"))]
            self._refresh_labels()
            self._render_reports()
            self._rendered_key = self._refresh_key()
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
            and self.fee_branches_draft == self.fee_branches_original
        ):
            self.default_fee.value = (
                None
                if self.shared_defaults and "commission" in self.shared_defaults.conflicts
                else self.fee_preferences.default
            )
            self.default_fee_original = self.default_fee.value
            self.default_fee_journal = self.journal
            self.default_fee_source = self.journal.source
            self.fee_branches_draft = self.fee_preferences.branches
            self.fee_branches_original = self.fee_preferences.branches
        if (
            self.operating_journal is not self.journal
            or self.operating_currencies.value == self.operating_original
        ):
            self.operating_original = " ".join(ledger.operating_currencies)
            self.operating_currencies.value = self.operating_original
            self.operating_source = self.journal.source
            self.operating_journal = self.journal
        self._set_fab_ready(True)
        balances = ledger.balances(today=self.journal.today())
        grouped: dict[str, list[Account]] = defaultdict(list)
        for account in ledger.accounts:
            grouped[account.currency].append(account)
        currency_order = {
            currency: index for index, currency in enumerate(ledger.operating_currencies)
        }
        currency_groups = [
            (currency, sorted(accounts, key=lambda account: (account.name, account.id)))
            for currency, accounts in sorted(
                grouped.items(),
                key=lambda item: (currency_order.get(item[0], len(currency_order)), item[0]),
            )
        ]
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
                            ft.Column(
                                [
                                    self._entity_label(
                                        account.id,
                                        ft.Text(
                                            self._entity_display_name(account.id, account.name)
                                        ),
                                    ),
                                    ft.Text(
                                        self._account_balance_text(
                                            account.id, balances[account.id], currency
                                        ),
                                        text_align=ft.TextAlign.RIGHT,
                                    ),
                                ],
                                spacing=2,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            )
                            if account.id.startswith("Liabilities:")
                            else ft.Row(
                                [
                                    self._entity_label(
                                        account.id,
                                        ft.Text(
                                            self._entity_display_name(account.id, account.name),
                                            expand=True,
                                        ),
                                    ),
                                    ft.Text(
                                        format_amount(balances[account.id], self.language, currency)
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            )
                            for account in accounts
                        ],
                    ],
                    spacing=10,
                ),
            )
            for currency, accounts in currency_groups
        ] or [ft.Text(self.tr("home_no_accounts"))]
        self._render_operations_totals(ledger, balances)
        names = {
            account.id: self._entity_display_name(account.id, account.name)
            for account in ledger.accounts
        }
        names.update(
            {
                category.id: self._entity_display_name(category.id, category.name)
                for category in ledger.categories
            }
        )
        transactions = nlargest(
            5,
            ledger.transactions,
            key=self._transaction_sort_key,
        )
        self.operations.sync()
        self.home_activity.controls = self._transaction_groups(
            transactions[:5], names, compact=True
        ) or [ft.Text(self.tr("home_no_transactions"))]
        self._update_transaction_separators()
        self.account_list.controls = [
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                content=ft.Column(
                    [
                        ft.Text(self.tr("currency_group", currency=currency), size=16),
                        ft.Column(
                            [
                                self._entity_card(account, balances[account.id])
                                for account in accounts
                            ],
                            spacing=4,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    ],
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            )
            for currency, accounts in currency_groups
        ] or [ft.Text(self.tr("accounts_empty"))]
        self.category_list.controls = self._category_tree(ledger.categories)
        self._refresh_labels()
        self._build_home_layout()
        if self.current_screen == "reports":
            self._render_reports()
        self._rendered_key = self._refresh_key()

    def _sync_operations_filters(self) -> None:
        ready = self.journal is not None and self.journal.ready
        ledger = self.journal.ledger if ready and self.journal else Ledger()
        self.operations_account.label = self.tr("budget_account")
        self.operations_category.label = self.tr("budget_category")
        for control, items, all_key in (
            (self.operations_account, ledger.accounts, "budget_all_accounts"),
            (self.operations_category, ledger.categories, "operations_all_categories"),
        ):
            identifiers = {item.id for item in items}
            if control is self.operations_category and self.operations.origin and control.value:
                # Undeclared or removed scoped categories must not broaden a drill-down.
                identifiers.add(control.value)
            if control.value not in identifiers:
                control.value = ""
            sorted_items = sorted(items, key=lambda item: item.id.split(":"))
            if control is self.operations_category:
                entities = [(item.id, item.name) for item in sorted_items]
                if control.value and control.value not in identifiers:
                    entities.append((control.value, control.value))
                    entities.sort(key=lambda item: item[0].split(":"))
                control.options = self._category_option_lines(
                    [
                        ft.DropdownOption(key="", text=self.tr(all_key)),
                        *self._category_form_options(entities),
                    ]
                )
            else:
                control.options = [
                    ft.DropdownOption(key="", text=self.tr(all_key)),
                    *self._account_options(
                        [item for item in sorted_items if isinstance(item, Account)],
                        include_currency=False,
                    ),
                ]
            control.leading_icon = self._entity_dropdown_icon(control.value or "")
            control.disabled = not ready
        self.operations_filter_toggle.tooltip = self.tr("operations_filters")
        self.operations_filter_toggle.selected = self.operations_filters.visible
        self.operations_filter_toggle.icon_color = (
            ft.Colors.PRIMARY
            if self.operations_account.value
            or self.operations_category.value
            or self.operations.scope != OperationScope()
            else None
        )

    def _operations_transactions(self, transactions: list[Transaction]) -> list[Transaction]:
        return list(self.operations.matching(transactions))

    def _operations_filter_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        if event.control is self.operations_category:
            self.operations.scope = replace(self.operations.scope, report_root="", unassigned=False)
        self._refresh()
        self.page.update()

    def _toggle_operations_filters(self, event: ft.Event[ft.IconButton]) -> None:
        self.operations_filters.visible = not self.operations_filters.visible
        self.operations_filter_toggle.selected = self.operations_filters.visible
        self.page.update()

    def _show_entity_operations(self, event: ft.Event[ft.IconButton]) -> None:
        kind, identifier = event.control.data
        self._navigate(
            "transactions",
            operations=OperationsLocation(
                account=identifier if kind == "account" else "",
                category=identifier if kind == "category" else "",
            ),
        )

    def _open_scoped_operations(self, category: str, scope: OperationScope, *, origin: str) -> None:
        if self.journal is None or not self.journal.ready:
            self._error(LedgerError("crud_stale"), notify=True)
            return
        self._navigate(
            "transactions",
            operations=OperationsLocation(category=category, scope=scope, origin=origin),
        )

    def _sync_report_period(self) -> None:
        self.report_period.label = self.tr("report_period")
        self.report_period.options = [
            ft.DropdownOption(key="current_month", text=self.tr("report_current_month")),
            ft.DropdownOption(key="previous_month", text=self.tr("report_previous_month")),
            *(
                [
                    ft.DropdownOption(
                        key="month",
                        text=(
                            f"{self.tr(f'date_month_{self.report_month.month:02}')} "
                            f"{self.report_month.year}"
                        ),
                    )
                ]
                if self.report_month is not None
                else []
            ),
            ft.DropdownOption(key="current_year", text=self.tr("report_current_year")),
            ft.DropdownOption(key="custom", text=self.tr("report_custom_range")),
        ]
        self.report_previous.tooltip = self.tr("budget_previous")
        self.report_next.tooltip = self.tr("budget_next")
        self.report_month_navigation.visible = self.report_period.value in {
            "current_month",
            "previous_month",
            "month",
        }

    def _report_dates(self) -> tuple[date, date]:
        preset = self.report_period.value or "current_month"
        if preset == "custom":
            start = date.fromisoformat(self.report_start.values()[0])
            end = date.fromisoformat(self.report_end.values()[0])
            return start, end
        if preset == "month":
            if self.report_month is None:
                raise LedgerError("report_date_range")
            today = self._today()
            if self.report_month == today.replace(day=1):
                return period_bounds("current_month", today)
            return period_bounds("month", self.report_month)
        return period_bounds(preset, self._today())

    def _render_reports(self) -> None:
        self._sync_report_period()
        self.report_range.visible = self.report_period.value == "custom"
        self.report_error.visible = False
        self.report_error.value = ""
        try:
            start, end = self._report_dates()
            if end < start:
                raise LedgerError("report_date_range")
            if self.report_month_navigation.visible:
                self.report_month_label.value = (
                    f"{self.tr(f'date_month_{start.month:02}')} {start.year}"
                )
                self.report_previous.disabled = start == date.min
                self.report_next.disabled = start == date(9999, 12, 1)
        except LedgerError as exc:
            self.report_error.value = localized_error(self.language, str(exc))
            self.report_error.visible = True
            self.report_summary.controls = []
            self.report_balances.controls = []
            self.report_categories.controls = []
            return

        if self.journal is None or not self.journal.ready:
            self.report_summary.controls = [ft.Text(self.tr("report_unavailable"))]
            self.report_balances.controls = []
            self.report_categories.controls = []
            return

        ledger = self.journal.ledger
        if ledger is not self._report_ledger or (start, end) != self._report_range:
            self._report_data = period_report(ledger, start, end)
            self._report_balances = account_balances(ledger, start, end)
            self._report_ledger = ledger
            self._report_range = (start, end)
        report = self._report_data
        if report is None:
            raise RuntimeError("Report cache was not initialized.")
        self.report_balances.controls = self._report_balance_cards(
            self._report_balances, ledger.operating_currencies
        )
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

        def category_section(
            title: str,
            categories: tuple[CategoryReport, ...],
            unassigned: tuple[tuple[str, Decimal], ...],
            currency: str,
            total: Decimal,
            root: str,
        ) -> ft.Column:
            def open_transactions(identifier: str | None, name: str) -> None:
                self._open_scoped_operations(
                    identifier or root,
                    OperationScope(
                        start=start,
                        end=end,
                        currency=currency,
                        report_root=root,
                        unassigned=identifier is None,
                    ),
                    origin="reports",
                )

            return self._report_category_section(
                title, categories, unassigned, currency, total, on_transactions=open_transactions
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
                            "Expenses",
                        ),
                        ft.Divider(),
                        category_section(
                            "report_income_categories",
                            report.income_categories,
                            report.unassigned_income,
                            item.currency,
                            item.income,
                            "Income",
                        ),
                    ],
                    spacing=12,
                ),
            )
            for item in report.currencies
        ]

    def _render_operations_totals(self, ledger: Ledger, balances: dict[str, Decimal]) -> None:
        totals: dict[str, Decimal] = {}
        with localcontext() as context:
            context.prec = 50
            for account in ledger.accounts:
                totals[account.currency] = (
                    totals.get(account.currency, Decimal("0")) + balances[account.id]
                )
        order = {currency: index for index, currency in enumerate(ledger.operating_currencies)}
        self.operations_totals_label.value = self.tr("operations_totals")
        self.operations_totals_values.controls = [
            ft.Text(
                format_amount(amount, self.language, currency),
                size=15,
                weight=ft.FontWeight.W_600,
                no_wrap=True,
            )
            for currency, amount in sorted(
                totals.items(), key=lambda pair: (order.get(pair[0], len(order)), pair[0])
            )
        ]
        self.operations_totals.visible = bool(totals)

    def _report_balance_cards(
        self, balances: tuple[AccountBalance, ...], operating: tuple[str, ...]
    ) -> list[ft.Control]:
        if not balances:
            return []
        grouped: dict[str, list[AccountBalance]] = defaultdict(list)
        for item in balances:
            grouped[item.currency].append(item)
        order = {currency: index for index, currency in enumerate(operating)}

        def row(item: AccountBalance) -> ft.Control:
            change = format_amount(item.change, self.language, item.currency)
            detail = self.tr(
                "report_balance_detail",
                start=self._account_balance_text(item.identifier, item.opening, item.currency),
                change=f"+{change}" if item.change > 0 else change,
            )
            label = self._entity_label(
                item.identifier,
                ft.Text(
                    self._entity_display_name(item.identifier, item.name),
                    expand=True,
                ),
            )
            balance = ft.Text(
                self._account_balance_text(item.identifier, item.closing, item.currency),
                weight=ft.FontWeight.W_500,
            )
            if item.identifier.startswith("Liabilities:"):
                label.expand = None
                balance.text_align = ft.TextAlign.RIGHT
            return ft.Column(
                [
                    ft.Column(
                        [label, balance],
                        spacing=2,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    )
                    if item.identifier.startswith("Liabilities:")
                    else ft.Row(
                        [label, balance],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Text(detail, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                spacing=2,
            )

        return [
            ft.Text(self.tr("report_balances"), size=18, weight=ft.FontWeight.W_600),
            *[
                ft.Container(
                    padding=16,
                    border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=12,
                    content=ft.Column(
                        [
                            ft.Text(self.tr("currency_group", currency=currency), size=18),
                            *[row(item) for item in items],
                        ],
                        spacing=10,
                    ),
                )
                for currency, items in sorted(
                    grouped.items(), key=lambda pair: (order.get(pair[0], len(order)), pair[0])
                )
            ],
        ]

    def _report_category_section(
        self,
        title_key: str,
        categories: tuple[CategoryReport, ...],
        unassigned: tuple[tuple[str, Decimal], ...],
        currency: str,
        total: Decimal,
        *,
        stacked: bool = False,
        on_transactions: Callable[[str | None, str], None] | None = None,
    ) -> ft.Column:
        def percentage(amount: Decimal) -> str:
            if total == 0:
                return self.tr("report_percentage_unavailable")
            with localcontext() as context:
                context.prec = 50
                value = amount * 100 / total
            return f"{format_amount(value, self.language)}%"

        def category_line(
            label: ft.Control, amount: Decimal, identifier: str | None, name: str
        ) -> ft.Control:
            values: list[ft.Control] = [
                ft.Text(format_amount(amount, self.language, currency)),
                ft.Text(percentage(amount), width=72, text_align=ft.TextAlign.RIGHT),
            ]
            actions: list[ft.Control] = []
            if on_transactions is not None:
                callback = on_transactions
                actions.append(
                    ft.IconButton(
                        icon=ft.Icons.RECEIPT_LONG,
                        icon_size=20,
                        tooltip=f"{self.tr('show_operations')}: {name}",
                        on_click=lambda event: callback(identifier, name),
                    )
                )
            if not self._mobile_item_actions:
                values.extend(actions)
            if stacked:
                line: ft.Control = ft.Column(
                    [label, ft.Row(values, wrap=True, spacing=8, run_spacing=0)],
                    tight=True,
                    spacing=0,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                )
            else:
                label.expand = True
                line = ft.Row([label, *values], spacing=8)
            return (
                self._item_menu(line, name, actions)
                if actions and self._mobile_item_actions
                else line
            )

        def node(item: CategoryReport) -> ft.Control | None:
            children = [child for sub in item.children if (child := node(sub)) is not None]
            amount = item.amount(currency)
            if amount == 0 and not children:
                return None
            name = self._entity_display_name(item.identifier, item.name)
            label: ft.Control = self._entity_label(item.identifier, ft.Text(name))
            if not item.active:
                label = ft.Column(
                    [label, ft.Text(self.tr("crud_inactive_label"), size=12)],
                    spacing=0,
                )
            title = category_line(label, amount, item.identifier, name)
            if children:
                return compact_tree_tile(ft.ExpansionTile(title=title, controls=children))
            return compact_tree_leaf(title)

        controls: list[ft.Control] = [
            ft.Text(self.tr(title_key), size=17, weight=ft.FontWeight.W_600)
        ]
        controls.extend(item for category in categories if (item := node(category)) is not None)
        unassigned_amount = dict(unassigned).get(currency, Decimal("0"))
        if unassigned_amount != 0:
            controls.append(
                compact_tree_leaf(
                    category_line(
                        ft.Text(self.tr("report_uncategorized")),
                        unassigned_amount,
                        None,
                        self.tr("report_uncategorized"),
                    )
                )
            )
        if len(controls) == 1:
            controls.append(ft.Text(self.tr("report_categories_empty")))
        return ft.Column(controls, spacing=0)

    def _report_period_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._render_reports()
        if self.page:
            self.page.update()

    def _report_step_month(self, offset: int) -> None:
        if not self.report_month_navigation.visible:
            return
        try:
            self.report_month = shift_month(self._report_dates()[0], offset)
        except LedgerError as exc:
            self.report_error.value = localized_error(self.language, str(exc))
            self.report_error.visible = True
        else:
            self.report_period.value = "month"
            self._render_reports()
        if self.page:
            self.page.update()

    def _report_previous_month(self, event: ft.Event[ft.IconButton]) -> None:
        self._report_step_month(-1)

    def _report_next_month(self, event: ft.Event[ft.IconButton]) -> None:
        self._report_step_month(1)

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

        def node(identifier: str, continued: bool | None = None) -> ft.Control:
            item = items.get(identifier)
            root = identifier in {"Expenses", "Income"}
            title = (
                self.tr("categories_expenses" if identifier == "Expenses" else "categories_income")
                if root
                else self._entity_display_name(item.id, item.name)
                if item
                else identifier.rsplit(":", 1)[-1]
            )
            labels: list[ft.Control] = [
                ft.Text(title, size=14, weight=ft.FontWeight.W_600 if root else None)
            ]
            if item and not item.active:
                labels.append(ft.Text(self.tr("crud_inactive_label"), size=12))
            actions = self._entity_actions(item) if item else []
            row: ft.Control = ft.Row(
                [
                    *self._entity_icon_controls(identifier),
                    ft.Column(labels, spacing=0, expand=True),
                    *([] if self._mobile_item_actions else actions),
                ],
                spacing=0,
            )
            if item and self._mobile_item_actions:
                row = self._item_menu(row, title, actions)
            descendants = sorted(children.get(identifier, ()))
            control: ft.Control
            if descendants or root:
                tile = compact_tree_tile(
                    ft.ExpansionTile(
                        data=identifier,
                        title=row,
                        expanded=self.category_expansion.get(identifier, root),
                        on_change=changed,
                        controls=[
                            node(child, index < len(descendants) - 1)
                            for index, child in enumerate(descendants)
                        ]
                        or [ft.Text(self.tr("categories_empty"), size=12)],
                    )
                )
                tile.controls_padding = 0
                control = tile
            else:
                control = self._action_row(compact_tree_leaf(row, data=identifier))
            if continued is not None:
                return CategoryConnectorRow(
                    ft.Container(control, padding=ft.Padding.only(left=16)),
                    (continued,),
                    header_height=48 if item and self._mobile_item_actions else TREE_ROW_HEIGHT,
                )
            return control

        return [node("Expenses"), node("Income")]

    def _entity_actions(self, item: Account | Category) -> list[ft.Control]:
        kind = "category" if isinstance(item, Category) else "account"
        return [
            ft.IconButton(
                icon=ft.Icons.RECEIPT_LONG,
                tooltip=self.tr("show_operations"),
                data=(kind, item.id),
                icon_size=20,
                on_click=self._show_entity_operations,
                style=ft.ButtonStyle(visual_density=ft.VisualDensity.COMPACT),
            ),
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

    def _account_balance_text(self, identifier: str, balance: Decimal, currency: str) -> str:
        if not identifier.startswith("Liabilities:"):
            return format_amount(balance, self.language, currency)
        return self.tr(
            "account_credit_balance" if balance > 0 else "account_debt_balance",
            amount=format_amount(-balance if balance < 0 else balance, self.language, currency),
        )

    def _action_row(
        self, content: ft.Control, *, padding: ft.PaddingValue | None = None
    ) -> HoverRow:
        return HoverRow(
            content,
            enabled=lambda: self.screen_class != ScreenClass.COMPACT,
            padding=padding,
        )

    def _item_menu_context(self) -> tuple[object, ...]:
        journal = self.journal
        return (
            id(journal),
            journal.source if journal else None,
            bool(journal and journal.ready),
            self.current_screen,
            self.history_index,
            self.operations.location(),
            id(self.operations.page),
            self.operations.busy,
            self.budgets.generation,
            self.budgets.month,
            self.budgets.category_id,
            self.budgets.currency.value,
            self.budgets.overspent.value,
            self.home_budgets.generation,
            self.home_budgets.cache_key,
            self._report_range,
            self.language,
            tuple(id(dialog) for dialog in self.page._dialogs.controls if dialog.open),
        )

    def _item_menu(
        self,
        content: ft.Control,
        name: str,
        actions: list[ft.Control],
        *,
        bgcolor: ft.ColorValue | None = None,
    ) -> ft.ContextMenu:
        mark_open, cancel, claim = self._item_menu_guard()
        tap: ft.TapEvent[ft.Container] | None = None

        def pressed(event: ft.TapEvent[ft.Container]) -> None:
            nonlocal tap
            tap = event

        async def opening(event: ft.Event[ft.Container] | ft.Event[ft.Semantics]) -> None:
            nonlocal tap
            position = tap if isinstance(event.control, ft.Container) else None
            tap = None
            mark_open(event)
            try:
                await menu.open(
                    local_position=position.local_position if position else None,
                    global_position=position.global_position if position else None,
                )
            except Exception as exc:
                cancel(event)
                self._error(exc, notify=True)

        menu = ft.ContextMenu(
            content=ft.MergeSemantics(
                content=ft.Semantics(
                    label=self.tr("item_actions", name=name),
                    button=True,
                    on_tap=opening,
                    content=ft.Container(
                        content,
                        min_height=48,
                        bgcolor=bgcolor,
                        ink=True,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        on_tap_down=pressed,
                        on_click=opening,
                    ),
                ),
            ),
            secondary_trigger=None,
            tertiary_trigger=None,
            items=[
                ft.PopupMenuItem(content=name, disabled=True),
                *self._item_menu_items(actions, claim),
            ],
            on_dismiss=cancel,
        )
        return menu

    def _item_overflow(self, name: str, actions: list[ft.Control]) -> ft.PopupMenuButton:
        opening, cancel, claim = self._item_menu_guard()
        return ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_size=18,
            padding=4,
            tooltip=self.tr("item_actions", name=name),
            items=self._item_menu_items(actions, claim),
            on_open=opening,
            on_cancel=cancel,
        )

    def _item_menu_guard(
        self,
    ) -> tuple[Callable[[object], None], Callable[[object], None], Callable[[], bool]]:
        journal = self.journal
        source = journal.source if journal else None
        opened: tuple[object, ...] | None = None

        def opening(event: object) -> None:
            nonlocal opened
            opened = self._item_menu_context()

        def cancel(event: object) -> None:
            nonlocal opened
            opened = None

        def claim() -> bool:
            nonlocal opened
            current = (
                self.journal is journal
                and journal is not None
                and journal.ready
                and journal.source == source
                and opened is not None
                and opened == self._item_menu_context()
            )
            opened = None
            return current

        return opening, cancel, claim

    def _item_menu_items(
        self, actions: list[ft.Control], claim: Callable[[], bool]
    ) -> list[ft.PopupMenuItem]:
        def item(action: ft.Control) -> ft.PopupMenuItem:
            if isinstance(action, ft.Text):
                return ft.PopupMenuItem(content=action.value, disabled=True)
            if not isinstance(action, (ft.IconButton, ft.TextButton)) or action.on_click is None:
                raise ValueError("Item menus require labeled action buttons.")
            label = action.tooltip if isinstance(action, ft.IconButton) else action.content
            if not isinstance(label, str):
                raise ValueError("Item menu actions require text labels.")

            async def select(event: ft.Event[ft.PopupMenuItem]) -> None:
                try:
                    if not claim() or action.disabled or not action.visible:
                        raise LedgerError("crud_stale")
                    # Flutter pops the native menu before this event. Reuse the
                    # original button handler and payload without mounting it.
                    callback = cast(Callable[..., object], action.on_click)
                    result = callback(ft.Event(control=action, name="click", data=None))
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    self._error(exc, notify=True)

            # Flet's native icon/content row does not constrain the label.
            icon = action.icon
            row: list[ft.Control] = []
            if icon is not None:
                row.append(icon if isinstance(icon, ft.Control) else ft.Icon(icon))
            row.append(ft.Text(label, expand=True))
            return ft.PopupMenuItem(
                content=ft.Row(row, spacing=8),
                data=action.data,
                disabled=action.disabled,
                on_click=select,
            )

        return [item(action) for action in actions]

    def _entity_card(self, item: Account | Category, balance: Decimal | None = None) -> ft.Control:
        lines: list[ft.Control] = [
            ft.Text(self._entity_display_name(item.id, item.name), size=14),
        ]
        if not item.active:
            lines.append(ft.Text(self.tr("crud_inactive_label")))
        if isinstance(item, Account) and balance is not None:
            lines.append(
                ft.Text(self._account_balance_text(item.id, balance, item.currency), size=14)
            )
        actions = self._entity_actions(item)
        content: ft.Control = ft.Row(
            [
                *self._entity_icon_controls(item.id),
                ft.Column(lines, spacing=0, expand=True),
                *([] if self._mobile_item_actions else actions),
            ],
            spacing=0,
        )
        return self._action_row(
            padding=ft.Padding.symmetric(vertical=2),
            content=self._item_menu(content, item.name, actions)
            if self._mobile_item_actions
            else content,
        )

    def _entity_icon(
        self, identifier: str, *, size: float = DISPLAY_SIZE
    ) -> ft.Image | ft.Stack | None:
        asset = self.icons.assets.get(identifier) if self.icons is not None else None
        return icon_image(asset, size=size) if asset is not None else None

    def _entity_dropdown_icon(self, identifier: str) -> ft.Container | None:
        image = self._entity_icon(identifier)
        if image is None:
            return None
        return ft.Container(
            content=image,
            width=24,
            height=24,
            padding=0,
            alignment=ft.Alignment.CENTER,
        )

    def _entity_icon_controls(self, identifier: str) -> list[ft.Control]:
        image = self._entity_icon(identifier)
        return [ft.Container(image, margin=ft.Margin.only(right=8))] if image else []

    def _entity_dropdown_selected(self, event: ft.Event[ft.Dropdown]) -> None:
        event.control.leading_icon = self._entity_dropdown_icon(event.control.value or "")
        self.page.update()

    def _transaction_entity_dropdown_selected(self, event: ft.Event[ft.Dropdown]) -> None:
        event.control.leading_icon = self._entity_dropdown_icon(event.control.value or "")
        self.page.update()

    def _entity_label(self, identifier: str, label: ft.Control) -> ft.Control:
        image = self._entity_icon(identifier)
        if image is None:
            return label
        expand = label.expand
        label.expand = True
        return ft.Row([image, label], spacing=8, expand=expand)

    def _choose_entity_icon(self, event: ft.Event[ft.IconButton]) -> None:
        try:
            journal = self._require_journal()
            identifier = str(event.control.data)
            self._open_icon_picker(journal, identifier)
        except Exception as exc:
            self._error(exc, notify=True)

    def _reference_icons(self, journal: Journal) -> tuple[IconReference, ...]:
        store = self.icons
        if store is None or not store.loaded:
            return ()
        return tuple(
            IconReference(item.name, item.id, store.assets[item.id])
            for item in sorted(
                (*journal.ledger.categories, *journal.ledger.accounts),
                key=lambda item: (item.name.casefold(), item.id),
            )
            if item.id in store.assets
        )

    def _open_icon_picker(
        self,
        journal: Journal,
        identifier: str,
        on_saved: Callable[[IconAsset | None], None] | None = None,
        *,
        remove: bool = False,
    ) -> None:
        store = self.icons
        if store is None or not store.loaded or not journal.ready:
            raise StorageError("icon_storage")
        expected = store.heads.get(identifier, ())
        item = next(
            (
                item
                for item in (*journal.ledger.accounts, *journal.ledger.categories)
                if item.id == identifier
            ),
            None,
        )

        async def save(asset: IconAsset | None) -> None:
            async with self.lock:
                if (
                    self.journal is not journal
                    or self.icons is not store
                    or not journal.ready
                    or identifier not in self._existing_identifiers(journal)
                ):
                    raise ConflictError("icon_stale")
                await store.assign(identifier, asset, expected)
                if on_saved is not None:
                    on_saved(asset)
                self.icon_revision += 1
                self.icon_notice.visible = bool(store.conflicts)
                self.icon_notice.value = self.tr("icon_conflicts") if store.conflicts else ""
                self.fee_choices_state = ()
                self._refresh()
                if self.current_screen == "budgets":
                    await self.budgets.refresh()

        show_dialog(
            self.page,
            IconRemovalDialog(self.page, self.language, save)
            if remove
            else IconPicker(
                self.page,
                self.language,
                store.assets.get(identifier),
                save,
                existing_icons=store.assets.values(),
                conflict=identifier in store.conflicts,
                ai_settings=self.ai_settings,
                reference_icons=self._reference_icons(journal),
                target=IconTarget(
                    item.name if item is not None else "",
                    identifier,
                    isinstance(item, Category)
                    if item is not None
                    else identifier.startswith(("Expenses:", "Income:")),
                ),
            ),
        )

    async def _assign_created_icon(
        self,
        journal: Journal,
        store: IconStore | None,
        draft: IconDraft,
        identifier: str,
        expected: tuple[str, ...],
    ) -> None:
        if draft.selected is None:
            return
        try:
            if self.journal is not journal or self.icons is not store or not journal.ready:
                raise ConflictError("icon_stale")
            if store is None or not store.loaded:
                raise IconError("icon_storage")
            await store.assign(identifier, draft.selected, expected)
        except (IconError, StorageError, TimeoutError, ConflictError) as exc:
            self.icon_notice.value = self.tr("icon_create_failed")
            self.icon_notice.visible = True
            logger.warning("Entity created; icon assignment failed (%s)", type(exc).__name__)
            raise IconError("icon_create_failed") from exc
        self.icon_revision += 1
        self.fee_choices_state = ()

    async def _load_icons(self) -> None:
        if self.icons is None:
            return
        try:
            if await self.icons.load():
                self.icon_revision += 1
                self.fee_choices_state = ()
            self.icon_notice.value = self.tr("icon_conflicts") if self.icons.conflicts else ""
            self.icon_notice.visible = bool(self.icons.conflicts)
        except (IconError, StorageError, TimeoutError) as exc:
            logger.warning("Icon sidecar read failed (%s)", type(exc).__name__)
            self.icon_notice.value = (
                self.tr(str(exc)) if isinstance(exc, IconError) else self.tr("icon_storage")
            )
            self.icon_notice.visible = True

    def _require_shared_defaults(self) -> SharedDefaults:
        if (
            self.shared_defaults is None
            or self.shared_defaults_journal is not self.journal
            or not self.shared_defaults.loaded
            or self.shared_defaults_error is not None
        ):
            raise SharedDefaultsError(self.shared_defaults_error or "shared_settings_not_ready")
        return self.shared_defaults

    async def _load_shared_defaults(
        self, *, reset: tuple[str, ...] = (), seed: bool = False, read_records: bool = True
    ) -> None:
        journal = self.journal
        if journal is None or not journal.ready:
            return
        await self.budgets.load_order()
        if self.shared_defaults_journal is not journal:
            folder = journal.storage.folder
            if not isinstance(folder, (DesktopFolder, AndroidFolder)):
                raise SharedDefaultsError("shared_settings_storage")
            self.shared_defaults = SharedDefaults(
                AndroidRecordFiles(folder, DEFAULTS_POLICY)
                if isinstance(folder, AndroidFolder)
                else DesktopRecordFiles(folder, DEFAULTS_POLICY)
            )
            self.shared_defaults_journal = journal
            reset = ("account", "commission")
        store = self.shared_defaults
        if store is None:
            raise SharedDefaultsError("shared_settings_not_ready")
        account_clean = (self.default_account.value or None) == self.default_account_original
        fee_clean = (
            self.default_fee.value or None
        ) == self.default_fee_original and self.fee_branches_draft == self.fee_branches_original
        try:
            if read_records:
                await store.load()
        except (PreferencesError, StorageError, TimeoutError) as exc:
            logger.exception("Shared defaults could not be read")
            self.shared_defaults_error = (
                str(exc) if isinstance(exc, SharedDefaultsError) else "shared_settings_storage"
            )
            if not store.loaded:
                self.default_account_id = None
                await journal.set_default_fee_category(None, journal.source)
            self._shared_defaults_labels()
            return
        self.shared_defaults_error = None
        account = store.values.get("account")
        self.default_account_id = account if isinstance(account, str) else None
        commission = store.values.get("commission")
        self.fee_preferences = (
            commission if isinstance(commission, FeePreferences) else FeePreferences()
        )
        await self._apply_default_fee()
        if account_clean or "account" in reset:
            self.default_account.value = self.default_account_id or ""
            self.default_account_original = self.default_account_id
            self.default_account_heads = store.heads.get("account", ())
            self.default_account_journal = journal
            if seed and "account" not in store.heads:
                self.default_account.value = self.legacy_default_account or ""
        if fee_clean or "commission" in reset:
            self.default_fee.value = (
                None if "commission" in store.conflicts else self.fee_preferences.default
            )
            self.default_fee_original = self.default_fee.value
            self.fee_branches_draft = self.fee_preferences.branches
            self.fee_branches_original = self.fee_preferences.branches
            self.default_fee_heads = store.heads.get("commission", ())
            self.default_fee_source = journal.source
            self.default_fee_journal = journal
            if seed and "commission" not in store.heads:
                self.fee_branches_draft = self.legacy_fee_preferences.branches
                self.default_fee.value = self.legacy_fee_preferences.default
        self._shared_defaults_labels()

    def _shared_defaults_labels(self) -> None:
        store = self.shared_defaults
        lines: list[str] = []
        stale = False
        if store is not None and store.loaded:
            stale = self.default_account_heads != store.heads.get(
                "account", ()
            ) or self.default_fee_heads != store.heads.get("commission", ())
            for group, label in (
                ("account", "default_account"),
                ("commission", "transfer_fee_category"),
            ):
                choices = store.conflicts.get(group, (store.values.get(group),))
                if group not in store.heads:
                    lines.append(f"{self.tr(label)}: {self.tr('shared_settings_unpublished')}")
                    continue
                formatted = []
                for choice in choices:
                    if isinstance(choice, FeePreferences):
                        formatted.append(f"{choice.default} ({', '.join(choice.branches)})")
                    else:
                        formatted.append(
                            choice if isinstance(choice, str) else self.tr("default_account_none")
                        )
                lines.append(f"{self.tr(label)}: {' / '.join(formatted)}")
            if self.legacy_defaults_invalid and set(store.heads) != {"account", "commission"}:
                lines.append(self.tr("shared_settings_legacy_invalid"))
        key = self.shared_defaults_error
        if key is None and store is not None and store.conflicts:
            key = "shared_settings_conflicts"
        if key is None and stale:
            key = "shared_settings_stale"
        self.shared_notice.value = self.tr(key) if key else ""
        self.shared_notice.visible = key is not None
        self.shared_details.value = "\n".join(lines)
        self.shared_review.content = self.tr("shared_settings_review")
        self.shared_review.visible = bool(stale or (store and store.conflicts))
        self.shared_review.disabled = self.shared_defaults_error is not None

    async def _review_shared_defaults(self, event: ft.Event[ft.TextButton]) -> None:
        async def review() -> None:
            journal = self._require_journal()
            await self._load_shared_defaults()
            store = self._require_shared_defaults()
            heads, source = dict(store.heads), journal.source
            details = self.shared_details.value or ""

            async def accept(event: ft.Event[ft.TextButton]) -> None:
                async def acknowledge() -> None:
                    if self.journal is not journal or self.shared_defaults is not store:
                        raise ConflictError("crud_stale")
                    await store.load()
                    if store.heads != heads or journal.source != source:
                        raise ConflictError("shared_settings_stale")
                    self.default_account_heads = heads.get("account", ())
                    self.default_fee_heads = heads.get("commission", ())
                    self.default_fee_source = source
                    self.page.pop_dialog()

                await self._action(acknowledge)

            show_dialog(
                self.page,
                ft.AlertDialog(
                    title=ft.Text(self.tr("shared_settings_review")),
                    scrollable=True,
                    content=ft.Column(
                        [
                            ft.Text(details, selectable=True),
                            ft.Text(self.tr("shared_settings_review_help")),
                        ],
                        tight=True,
                        width=440,
                    ),
                    actions=[
                        ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss),
                        ft.TextButton(self.tr("shared_settings_keep_draft"), on_click=accept),
                    ],
                ),
            )

        await self._action(review)

    async def _save_shared_default(
        self, group: str, value: DefaultValue, heads: tuple[str, ...]
    ) -> None:
        store = self._require_shared_defaults()
        try:
            await store.save(group, value, heads)
        except ConflictError:
            await self._load_shared_defaults()
            raise
        except (PreferencesError, StorageError, TimeoutError) as exc:
            logger.exception("Shared defaults save failed")
            self.shared_defaults_error = "shared_settings_write_uncertain"
            raise SharedDefaultsError("shared_settings_write_uncertain") from exc
        await self._load_shared_defaults(reset=(group,), read_records=False)

    def _update_transaction_separators(self) -> None:
        border = (
            ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT))
            if self.screen_class == ScreenClass.EXPANDED
            else None
        )
        for control in self.history.controls:
            if (
                isinstance(control, ft.Container)
                and isinstance(control.content, ft.Row)
                and control.data != "transaction-date"
            ):
                control.border = border

    @staticmethod
    def _transaction_sort_key(transaction: Transaction) -> tuple[date, datetime, str]:
        return transaction_key(transaction)

    def _show_category_transactions(
        self,
        identifier: str | None,
        name: str,
        transactions: tuple[Transaction, ...],
        names: dict[str, str],
        *,
        scope: OperationScope | None = None,
    ) -> None:
        listing = ft.Column(spacing=0)
        search = ft.TextField(label=self.tr("budget_search_transactions"))
        truncated = ft.Text(
            self.tr("operations_preview_truncated", limit=PAGE_SIZE), visible=False, size=12
        )
        journal = self.journal

        def render(event: ft.Event[ft.TextField] | None = None) -> None:
            query = (search.value or "").strip().casefold()
            matching = nlargest(
                PAGE_SIZE + 1,
                (
                    transaction
                    for transaction in transactions
                    if not query
                    or query in f"{transaction.payee} {transaction.narration}".casefold()
                ),
                key=self._transaction_sort_key,
            )
            truncated.visible = len(matching) > PAGE_SIZE
            listing.controls = self._transaction_groups(
                matching[:PAGE_SIZE], names, read_only=True
            ) or [ft.Text(self.tr("budget_no_matches"))]
            if event is not None:
                self.page.update()

        search.on_change = render
        render()
        display_name = (
            self._entity_display_name(identifier, name) if identifier is not None else name
        )
        label: ft.Control = ft.Text(display_name, selectable=True)
        if identifier is not None:
            label = self._entity_label(identifier, label)
        labels = [label]
        if scope is not None:
            labels.append(
                ft.Text(
                    f"{format_date(scope.start, self.language) if scope.start else ''}"
                    f" - {format_date(scope.end, self.language) if scope.end else ''}"
                    f" / {scope.currency or self.tr('budget_all_currencies')}",
                    size=12,
                )
            )
        labels.append(truncated)

        def open_operations(event: ft.Event[ft.TextButton]) -> None:
            if self.journal is not journal or journal is None or not journal.ready:
                self._error(LedgerError("crud_stale"), notify=True)
                return
            assert scope is not None
            self.page.pop_dialog()
            self._open_scoped_operations(
                identifier or "", replace(scope, query=search.value or ""), origin="budgets"
            )

        show_dialog(
            self.page,
            ft.AlertDialog(
                title=self.tr("budget_transactions"),
                scrollable=True,
                content=ft.Column(
                    [ft.Column(labels, tight=True), search, listing], tight=True, width=420
                ),
                actions=[
                    ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss),
                    *(
                        [ft.TextButton(self.tr("show_operations"), on_click=open_operations)]
                        if scope is not None
                        else []
                    ),
                ],
            ),
        )

    def _transaction_groups(
        self,
        transactions: list[Transaction],
        names: dict[str, str],
        compact: bool = False,
        *,
        read_only: bool = False,
        daily_summary: bool = False,
        daily_totals: dict[date, tuple[CurrencyReport, ...]] | None = None,
    ) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for day, entries in groupby(transactions, key=lambda item: item.date):
            controls.append(
                ft.Container(
                    data="transaction-date",
                    padding=ft.Padding.only(top=8, bottom=4),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CALENDAR_TODAY,
                                size=16,
                                color=ft.Colors.OUTLINE if day == EPOCH else None,
                            ),
                            ft.Text(
                                self.tr("initial_date")
                                if day == EPOCH
                                else format_date(day, self.language),
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=ft.Colors.OUTLINE if day == EPOCH else None,
                            ),
                        ],
                        spacing=8,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                )
            )
            day_entries = tuple(entries)
            if daily_summary and self.journal is not None:
                controls.append(
                    self._daily_summary(
                        self.journal,
                        day,
                        day_entries,
                        totals=daily_totals.get(day) if daily_totals is not None else None,
                    )
                )
            controls.extend(
                self._transaction_card(item, names, compact, read_only=read_only)
                for item in day_entries
            )
        return controls

    def _daily_summary(
        self,
        journal: Journal,
        day: date,
        transactions: tuple[Transaction, ...],
        *,
        totals: tuple[CurrencyReport, ...] | None = None,
    ) -> ft.Container:
        if totals is None:
            totals = currency_totals(Ledger(transactions=transactions), day, day)
        button = ft.TextButton(
            data=day,
            expand=True,
            content=ft.Column(
                [
                    ft.Text(self.tr("daily_summary"), size=12),
                    self._daily_totals(totals),
                ],
                tight=True,
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            style=ft.ButtonStyle(
                padding=8,
                side=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
                shape=ft.RoundedRectangleBorder(radius=8),
                alignment=ft.Alignment.CENTER_LEFT,
                mouse_cursor=click_cursor() if self._desktop_pointer else None,
            ),
            on_click=lambda event: self._daily_report(journal, day),
        )
        return ft.Container(
            padding=ft.Padding.only(bottom=4),
            theme=summary_theme(),
            dark_theme=summary_theme(dark=True),
            content=ft.Column([ft.Row([button])], spacing=0),
        )

    def _daily_totals(self, currencies: tuple[CurrencyReport, ...]) -> ft.Column:
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            f"{self.tr('report_expenses')}: "
                            f"{format_amount(item.expenses, self.language, item.currency)}",
                            size=13,
                        ),
                        ft.Text(
                            f"{self.tr('report_income')}: "
                            f"{format_amount(item.income, self.language, item.currency)}",
                            size=13,
                        ),
                    ],
                    wrap=True,
                    spacing=16,
                    run_spacing=4,
                )
                for item in currencies
            ]
            or [ft.Text(self.tr("daily_no_cashflow"), size=13)],
            spacing=4,
            tight=True,
        )

    def _daily_report(self, journal: Journal, day: date) -> None:
        try:
            if self.journal is not journal:
                raise ConflictError("crud_stale")
            if not journal.ready:
                raise LedgerError("report_unavailable")
            report = period_report(
                journal.ledger,
                day,
                day,
                transactions=self.operations.matching(journal.ledger.transactions),
            )
            sections: list[ft.Control] = []
            for item in report.currencies:
                sections.extend(
                    [
                        ft.Divider(),
                        ft.Text(self.tr("currency_group", currency=item.currency), size=18),
                        self._report_category_section(
                            "report_expense_categories",
                            report.expense_categories,
                            report.unassigned_expenses,
                            item.currency,
                            item.expenses,
                            stacked=self.screen_class == ScreenClass.COMPACT,
                        ),
                        ft.Divider(),
                        self._report_category_section(
                            "report_income_categories",
                            report.income_categories,
                            report.unassigned_income,
                            item.currency,
                            item.income,
                            stacked=self.screen_class == ScreenClass.COMPACT,
                        ),
                    ]
                )
            show_dialog(
                self.page,
                ft.AlertDialog(
                    title=self.tr(
                        "daily_report",
                        day=self.tr("initial_date")
                        if day == EPOCH
                        else format_date(day, self.language),
                    ),
                    scrollable=True,
                    content=ft.Container(
                        theme=compact_tree_theme(),
                        content=ft.Column(
                            [
                                self._daily_totals(report.currencies),
                                ft.Text(self.tr("daily_report_help"), size=12),
                                *sections,
                            ],
                            tight=True,
                            width=520,
                            spacing=12,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    ),
                    actions=[ft.TextButton(self.tr("daily_report_close"), on_click=self._dismiss)],
                ),
            )
        except Exception as exc:
            self._error(exc, notify=True)

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
        source = target = ""
        source_identifier = target_identifier = ""
        if simple:
            source_identifier, target_identifier = outgoing[0].account, incoming[0].account
            source = self._entity_display_name(
                outgoing[0].account,
                names.get(
                    outgoing[0].account,
                    self.tr(
                        "kind_adjustment" if transaction.kind == "adjustment" else "kind_opening"
                    ),
                ),
            )
            target = self._entity_display_name(
                incoming[0].account,
                names.get(incoming[0].account, self.tr("kind_external")),
            )
            if transaction.kind == "opening":
                opened_account = next(
                    p.account
                    for p in transaction.postings
                    if p.account.startswith(("Assets:", "Liabilities:"))
                )
                source_identifier = next(
                    p.account for p in transaction.postings if p.account != opened_account
                )
                target_identifier = opened_account
                source = self.tr("kind_opening")
                target = self._entity_display_name(
                    opened_account,
                    names.get(opened_account, self.tr("kind_external")),
                )
            title = (
                f"{self.tr('kind_opening')} → {target}"
                if transaction.kind == "opening"
                else f"{source} → {target}"
            )
        movement_icons = simple and any(
            self.icons is not None and posting.account in self.icons.assets
            for posting in (*outgoing, *incoming)
        )
        leading_identifier = ""
        if simple:
            if transaction.kind in {"transfer", "opening"}:
                leading_identifier = target_identifier
            elif transaction.kind in {"expense", "income", "reimbursement"}:
                leading_identifier = next(
                    (
                        posting.account
                        for posting in (*outgoing, *incoming)
                        if posting.account.startswith(("Expenses:", "Income:"))
                    ),
                    "",
                )
        leading_image = self._entity_icon(leading_identifier, size=25.92)
        leading_controls: list[ft.Control] = []
        if leading_image is not None:
            leading_controls.append(
                ft.Container(
                    content=leading_image,
                    width=40,
                    bgcolor=ft.Colors.SECONDARY_CONTAINER,
                    border_radius=8,
                    margin=ft.Margin.only(right=8),
                    alignment=ft.Alignment.CENTER,
                )
            )

        exchange_amounts = (
            (
                format_amount(transfer.debited, self.language, transfer.source.currency),
                format_amount(transfer.target.amount, self.language, transfer.target.currency),
            )
            if transfer and transfer.exchange
            else None
        )
        movement_row = movement_icons or exchange_amounts is not None

        def movement_entity_label(identifier: str, text: str) -> ft.Control:
            label = ft.Text(
                text, size=10, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS
            )
            return (
                label if identifier == leading_identifier else self._entity_label(identifier, label)
            )

        def movement_label(amount: ft.Control | None = None) -> ft.Control:
            if not movement_row:
                return ft.Text(title, size=10, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

            source_cell = movement_entity_label(source_identifier, source)
            amounts = [amount] if amount is not None else []
            return ft.Row(
                [
                    source_cell,
                    ft.Row(
                        [
                            ft.Text("→", size=10),
                            movement_entity_label(target_identifier, target),
                            *amounts,
                        ],
                        spacing=4,
                        expand=2,
                    ),
                ],
                spacing=4,
                expand=True,
            )

        has_summary = simple and transaction.kind != "opening" and bool(summary)
        heading: list[ft.Control] = [
            ft.Text(
                summary
                if has_summary
                else self.tr("kind_transfer")
                if exchange_amounts is not None
                else title,
                size=14 if has_summary or not simple else 10,
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        ]
        if simple:
            movement = abs(cashflow.account.amount) if cashflow else incoming[0].amount
            amount = ft.Text(
                exchange_amounts[1]
                if exchange_amounts is not None
                else format_amount(movement, self.language, incoming[0].currency),
                size=13,
            )
            if exchange_amounts is not None:
                amount.text_align = ft.TextAlign.RIGHT
            heading.append(amount)
        else:
            heading.append(ft.Text(self.tr("multiple_postings"), size=12))
        lines: list[ft.Control] = [
            movement_label(heading[-1])
            if movement_icons and not has_summary and exchange_amounts is None
            else ft.Row(
                heading,
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.BASELINE,
            )
        ]
        fee_details = transfer or cashflow
        fee_line = None
        if fee_details and fee_details.fee:
            fee_line = ft.Text(
                f"{self.tr('transfer_fee')}: "
                f"{format_amount(fee_details.fee, self.language, fee_details.fee_currency)}",
                size=12,
            )
            if exchange_amounts is None:
                lines.append(fee_line)
        if exchange_amounts is not None:
            debited = ft.Text(
                f"−{exchange_amounts[0]}",
                size=10,
                color=ft.Colors.ON_SURFACE_VARIANT,
                style=ft.TextStyle(height=1),
                text_align=ft.TextAlign.RIGHT,
                expand=2,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                tooltip=f"−{exchange_amounts[0]}",
            )
            lines.append(movement_label(debited))
            if fee_line is not None:
                lines.append(fee_line)
        elif has_summary:
            lines.append(movement_label())
        actions: list[ft.Control] = []
        if not read_only:
            edit = ft.IconButton(
                icon=ft.Icons.EDIT_OUTLINED,
                icon_size=20,
                tooltip=self.tr("crud_edit"),
                data=transaction.id,
                on_click=self._edit_transaction,
            )
            delete = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_size=20,
                tooltip=self.tr("crud_delete"),
                data=transaction.id,
                on_click=self._delete_transaction,
            )
            actions = [edit, delete]
            if (
                compact
                and not self._mobile_item_actions
                and self.journal is not None
                and not self.journal.storage.loaded
            ):
                edit.disabled = delete.disabled = True
            elif (compact or self._mobile_item_actions) and self.journal is not None:
                try:
                    self.journal.transaction_for_edit(transaction.id)
                except LedgerError as exc:
                    if str(exc) != "crud_transaction_readonly":
                        raise
                    if self._mobile_item_actions:
                        if transaction.kind != "opening":
                            edit.tooltip = self.tr("details")
                        delete.disabled = True
                    else:
                        actions = [edit]
                        if transaction.kind != "opening" or not self._can_edit_opening(
                            self.journal, transaction
                        ):
                            edit.tooltip = self.tr("details")
                            edit.icon = ft.Icons.INFO_OUTLINE
        inline_actions = [] if self._mobile_item_actions else actions
        if compact and inline_actions:
            inline_actions = [self._item_overflow(display, inline_actions)]
        content: ft.Control = ft.Row(
            [
                *leading_controls,
                ft.Column(
                    lines,
                    spacing=1,
                    expand=True,
                    alignment=ft.MainAxisAlignment.CENTER
                    if leading_controls
                    else ft.MainAxisAlignment.START,
                ),
                *inline_actions,
            ],
            spacing=0,
            intrinsic_height=bool(leading_controls),
            vertical_alignment=ft.CrossAxisAlignment.STRETCH
            if leading_controls
            else ft.CrossAxisAlignment.CENTER,
        )
        if self._mobile_item_actions and not read_only:
            content = self._item_menu(content, display, actions)
        container = ft.Container if read_only else self._action_row
        return container(
            padding=ft.Padding.symmetric(vertical=2),
            content=content,
        )

    async def _lifecycle(self) -> None:
        try:
            if self.page.platform in {
                ft.PagePlatform.LINUX,
                ft.PagePlatform.WINDOWS,
                ft.PagePlatform.MACOS,
            }:
                self.category_menu_row_height = TREE_MENU_ROW_HEIGHT
                self.preferences = DesktopPreferences()
            else:
                self.category_menu_row_height = TREE_MOBILE_MENU_ROW_HEIGHT
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
            saved_home_budgets = await self.preferences.get(HOME_BUDGETS_KEY)
            if saved_home_budgets not in {None, "on", "off"}:
                raise PreferencesError(self.tr("home_budgets_invalid_setting"))
            self.show_home_budgets = saved_home_budgets != "off"
            try:
                fee_settings = await self.preferences.get(FEE_CATEGORIES_KEY)
                if fee_settings is not None:
                    if not isinstance(fee_settings, str):
                        raise PreferencesError("fee_categories_invalid")
                    self.legacy_fee_preferences = FeePreferences.parse(fee_settings)
                else:
                    saved_fee = await self.preferences.get(DEFAULT_FEE_KEY)
                    if saved_fee is not None:
                        if not isinstance(saved_fee, str):
                            raise PreferencesError("default_fee_invalid")
                        self.legacy_fee_preferences = FeePreferences(default=saved_fee)
                self.legacy_default_account = parse_default_account(
                    await self.preferences.get(DEFAULT_ACCOUNT_KEY)
                )
            except PreferencesError:
                logger.exception("Legacy defaults are invalid; shared settings remain independent")
                self.legacy_defaults_invalid = True
                self.legacy_default_account = None
                self.legacy_fee_preferences = FeePreferences()
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
        if self.notification_supported:
            self.page.run_task(self._initialize_notifications)
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
        notices = (tuple(self.possible_conflict_copies), self.sibling_scan_failed)
        await self._scan_conflict_copies()
        try:
            if journal.ready and await journal.poll():
                await self._apply_default_fee()
                self._set_message("external_reloaded")
            if time.monotonic() >= self.next_icon_poll:
                self.next_icon_poll = time.monotonic() + 30
                await self._load_icons()
                await self._load_shared_defaults()
        finally:
            if self._refresh_key() != self._rendered_key:
                self._refresh()
            elif notices != (tuple(self.possible_conflict_copies), self.sibling_scan_failed):
                self._refresh_external_notices()

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

    async def _initialize_notifications(self) -> None:
        if (
            not self.notification_supported
            or self.notification_initializing
            or self.notification_store is not None
        ):
            return
        self.notification_initializing = True
        self.notification_failed = False
        self._refresh_labels()
        self.page.update()
        store: NotificationStore | None = None
        try:
            bridge = AndroidNotifications(NotificationBridge())
            directory = await bridge.storage_dir()
            store = NotificationStore(directory / "notification-import.sqlite")
            store.prune(int(time.time()))
            panel = NotificationSettingsPanel(
                self.page,
                self.tr,
                bridge,
                store,
                ledger=lambda: self.journal.ledger if self.journal and self.journal.ready else None,
                today=self._today,
                on_changed=self._notification_changed,
                on_ai=self._notification_ai_starter,
            )
            inbox = NotificationInbox(
                self.tr,
                bridge,
                store,
                page=self.page,
                journal=lambda: self.journal,
                on_review=self._notification_review,
                on_rules=self._notification_rules,
                on_recovery=self._notification_recovery,
                on_settings=self._notification_open_settings,
                on_changed=self._notification_changed,
                on_ai=self._notification_ai_item,
                access=lambda: panel.access,
                access_failed=lambda: panel.notice == "notification_settings_bridge_error",
                apps=lambda: panel.apps,
                today=self._today,
            )
        except NotificationBridgeError as exc:
            logger.warning("Notification setup failed (code=%s)", exc.code)
            self.notification_failed = True
            if store is not None:
                store.close()
        except (OSError, sqlite3.Error, NotificationStoreError):
            logger.exception("Notification setup storage failed")
            self.notification_failed = True
            if store is not None:
                store.close()
        else:
            self.notification_store = store
            self.notification_settings = panel
            self.notification_inbox = inbox
            self.notification_section = settings_section(panel.control)
        finally:
            self.notification_initializing = False
            self._refresh_labels()
            self.page.update()
        if self.notification_settings is not None and self.current_screen == "settings":
            await self.notification_settings.load()

    async def _retry_notification_setup(self, event: ft.Event) -> None:
        await self._initialize_notifications()

    def _notification_lifecycle_changed(self, event: ft.AppLifecycleStateChangeEvent) -> None:
        if event.state != ft.AppLifecycleState.RESUME or self.notification_store is None:
            return
        try:
            self.notification_store.prune(int(time.time()))
        except sqlite3.Error:
            logger.exception("Notification retention storage failed")
            self._error(NotificationStoreError("Notification storage is unavailable."), notify=True)
            return
        except NotificationStoreError as exc:
            self._error(exc, notify=True)
            return
        self._notification_changed()
        if self.notification_settings is not None and (
            self.current_screen == "settings" or self.notification_inbox_open
        ):
            self.page.run_task(self.notification_settings.load)

    def _notification_changed(self) -> None:
        if self.notification_inbox is not None:
            self.notification_inbox.render()
        if self.notification_store is not None and self.notification_reviews:
            pending = {item.id: item for item in self.notification_store.items(("pending",))}
            for item_id, review in tuple(self.notification_reviews.items()):
                item = pending.get(item_id)
                if item is None or (
                    item.app_id,
                    item.key,
                    item.posted_millis,
                ) != (review.item.app_id, review.item.key, review.item.posted_millis):
                    del self.notification_reviews[item_id]
        self._build_navigation()
        if self.page:
            self.page.update()

    def _open_notification_inbox(self, event: ft.Event) -> None:
        store = self.notification_store
        if self.notification_inbox is None or store is None:
            self._notification_open_settings()
            return
        try:
            needs_setup = not store.selected_apps() and not store.count(
                (
                    "pending",
                    "saving",
                    "unmatched",
                    "ignored",
                    "uncertain",
                    "imported",
                    "skipped",
                    "expired",
                    "cleared",
                )
            )
        except sqlite3.Error:
            logger.exception("Notification inbox storage failed")
            self._error(NotificationStoreError("Notification storage is unavailable."), notify=True)
            return
        except NotificationStoreError as exc:
            self._error(exc, notify=True)
            return
        if needs_setup:
            self._notification_open_settings()
            return
        self._navigate("transactions", notification_inbox=True)
        if self.notification_settings is not None:
            self.page.run_task(self.notification_settings.load)

    def _notification_open_settings(self) -> None:
        self._navigate("settings")

    def _notification_review(self, item: QueueItem) -> None:
        try:
            journal = self._require_journal()
            store = self.notification_store
            if store is None:
                raise NotificationStoreError("Notification storage is unavailable.")
            review = self.notification_reviews.setdefault(item.id, NotificationReview(store, item))

            async def finished() -> None:
                self.notification_reviews.pop(item.id, None)
                self._notification_changed()

            self._transaction_dialog(journal, review=review, after_review=finished)
        except (LedgerError, NotificationStoreError, ValueError) as exc:
            self._error(exc, notify=True)
        except sqlite3.Error:
            logger.exception("Notification review storage failed")
            self._error(NotificationStoreError("Notification storage is unavailable."), notify=True)

    def _notification_rules(self, item: QueueItem) -> None:
        self._notification_rule_editor(item)

    def _notification_rule_editor(
        self,
        item: QueueItem,
        *,
        draft: NotificationRule | None = None,
        sample_title: str | None = None,
        sample_body: str | None = None,
    ) -> None:
        store = self.notification_store
        if store is None:
            return
        journal = self.journal

        def saved(rule: object) -> None:
            try:
                journal = self.journal
                NotificationRuleApplication(
                    self.page,
                    self.tr,
                    store,
                    item,
                    ledger=journal.ledger if journal and journal.ready else None,
                    today=journal.today() if journal and journal.ready else self._today(),
                    journal_id=journal_identity(journal) if journal and journal.ready else None,
                    current_fields=None,
                    on_applied=lambda _id, _outcome: self._notification_changed(),
                ).open()
            except (NotificationStoreError, ValueError) as exc:
                self._error(exc, notify=True)
            except sqlite3.Error:
                logger.exception("Notification rule application preview storage failed")
                self._error(
                    NotificationStoreError("Notification storage is unavailable."), notify=True
                )

        try:
            NotificationRuleEditor(
                self.page,
                self.tr,
                store,
                item.app_id,
                draft=draft,
                sample_title=sample_title if draft is not None else item.title,
                sample_body=sample_body if draft is not None else item.body,
                ledger=journal.ledger if journal and journal.ready else None,
                today=self._today,
                on_saved=saved,
                on_ai=self._notification_ai_starter(item.app_id),
            ).open()
        except NotificationStoreError as exc:
            self._error(exc, notify=True)
        except sqlite3.Error:
            logger.exception("Notification rule editor storage failed")
            self._error(NotificationStoreError("Notification storage is unavailable."), notify=True)

    async def _notification_ai_item(self, item: QueueItem) -> None:
        def drafted(rule: NotificationRule, title: str | None, body: str | None) -> None:
            self._notification_rule_editor(item, draft=rule, sample_title=title, sample_body=body)

        await self._notification_ai_starter(item.app_id)(item.title, item.body, drafted)

    def _notification_ai_starter(self, app_id: str) -> AIStarter:
        async def start(title: str | None, body: str | None, on_draft: DraftCallback) -> None:
            if self.notification_ai_busy:
                return
            self.notification_ai_busy = True
            try:
                settings = self.ai_settings()
                model, _key = await settings.resolve(Capability.NOTIFICATION_RULE)
            except AIError as exc:
                logger.warning("Notification AI setup failed (code=%s)", exc.code)
                self._notification_ai_setup_dialog(exc.code)
            else:
                NotificationAIConsent(
                    self.page,
                    self.tr,
                    AIService(settings),
                    model,
                    app_id,
                    title=title,
                    body=body,
                    on_draft=on_draft,
                ).open()
            finally:
                self.notification_ai_busy = False

        return start

    def _notification_ai_setup_dialog(self, error: str) -> None:
        async def cancel(_: ft.Event) -> None:
            self.page.pop_dialog()

        async def configure(_: ft.Event) -> None:
            self.page.pop_dialog()
            panel = AISettingsPanel(self.tr, self.ai_settings, notification_rules=lambda: True)
            panel.control.expanded = True
            panel.render()

            def close() -> None:
                panel.clear_draft()
                self.page.pop_dialog()

            async def close_clicked(_: ft.Event) -> None:
                close()

            show_dialog(
                self.page,
                EditorDialog(
                    self.tr("ai_settings"),
                    ft.Column([panel.control], spacing=8),
                    [ft.TextButton(self.tr("crud_cancel"), on_click=close_clicked)],
                    compact=lambda: self.screen_class == ScreenClass.COMPACT,
                    cancel_on_back=close,
                ),
            )
            await panel._load(ft.Event(name="load", control=panel.control))

        show_dialog(
            self.page,
            ft.AlertDialog(
                modal=True,
                title=self.tr("notification_ai_title"),
                content=ft.Text(self.tr(error)),
                actions=[
                    ft.TextButton(self.tr("crud_cancel"), on_click=cancel),
                    ft.TextButton(self.tr("ai_settings"), on_click=configure),
                ],
            ),
        )

    def _notification_recovery(self, item: QueueItem) -> None:
        try:
            journal = self._require_journal()
            store = self.notification_store
            if store is None:
                raise NotificationStoreError("Notification storage is unavailable.")
            NotificationRecovery(
                self.page, self.tr, store, item, journal, on_resolved=self._notification_changed
            ).open()
        except (LedgerError, NotificationStoreError, ValueError) as exc:
            self._error(exc, notify=True)

    def _access_status(self) -> str:
        if self.journal is not None and self.journal.ready:
            return self.tr("access_ok")
        return self.tr("access_unchecked") if self.journal is None else self.tr("access_failed")

    def _error(
        self, exc: Exception, notify: bool = False, *, dialog_validation: bool = False
    ) -> None:
        if dialog_validation and notify and isinstance(exc, LedgerError):
            message = localized_error(self.language, str(exc))
            logger.warning("Journal operation refused (%s)", type(exc).__name__)
        else:
            self._set_error(exc)
            message = self.message.value
        if notify:
            conflict = isinstance(exc, ConflictError)
            show_dialog(
                self.page,
                ft.AlertDialog(
                    modal=True,
                    title=self.tr("conflict_title") if conflict else self.tr("action_failed"),
                    content=ft.Text(
                        f"{message}\n\n"
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
        self.icons = IconStore(
            AndroidIconFiles(folder)
            if isinstance(folder, AndroidFolder)
            else DesktopIconFiles(folder)
        )
        self.shared_defaults = SharedDefaults(
            AndroidRecordFiles(folder, DEFAULTS_POLICY)
            if isinstance(folder, AndroidFolder)
            else DesktopRecordFiles(folder, DEFAULTS_POLICY)
        )
        self.shared_defaults_journal = self.journal
        self.shared_defaults_error = None
        self.default_account_id = None
        self.default_account_original = None
        self.fee_preferences = FeePreferences()
        self.possible_conflict_copies = []
        self.sibling_scan_failed = False
        self.journal.storage.auto_cleanup = self.auto_backup_cleanup
        await self.journal.load()
        await self._load_shared_defaults(reset=("account", "commission"), seed=True)
        await self._load_icons()
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
                self.home_budgets_toggle,
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
                    self.home_budgets_toggle,
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
            await self._load_icons()
            await self._load_shared_defaults()
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

    async def _home_budgets_changed(self, event: ft.Event[ft.Switch]) -> None:
        enabled = event.control.value
        previous = self.show_home_budgets
        self.show_home_budgets = enabled
        self.home_budgets.sync()
        self.page.update()

        async def save() -> None:
            try:
                if self.preferences is None or not await self.preferences.set(
                    HOME_BUDGETS_KEY, "on" if enabled else "off"
                ):
                    raise PreferencesError(self.tr("home_budgets_setting_failed"))
            except (PreferencesError, OSError, TimeoutError):
                self.show_home_budgets = previous
                raise

        await self._action(save)

    def _home_transactions(self, event: ft.Event[ft.TextButton]) -> None:
        self._navigate(
            "transactions",
            operations=OperationsLocation(filters_visible=self.operations_filters.visible),
        )

    def _home_accounts(self, event: ft.Event[ft.TextButton]) -> None:
        self._navigate("accounts")

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
                    title=deletion_title(self.tr("backups_clean")),
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
        after_save: Callable[[], Awaitable[None]] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        def cancel_edit() -> None:
            if cancel.disabled:
                return
            if on_cancel is not None:
                on_cancel()
            self.page.pop_dialog()

        cancel = ft.TextButton(self.tr("crud_cancel"), on_click=lambda _: cancel_edit())
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
                try:
                    backup = await save()
                except LedgerError as exc:
                    if not journal.ready or not journal.storage.loaded:
                        raise
                    self._error(exc, notify=True, dialog_validation=True)
                    return
                self.page.pop_dialog()
                self._saved(backup)
                if after_save is not None:
                    await after_save()

            try:
                await self._action(action)
            finally:
                submit.disabled = cancel.disabled = False
                for control, disabled in disabled_states:
                    control.disabled = disabled
                self.page.update()

        submit.on_click = commit
        form = ft.Column(
            controls,
            tight=True,
            spacing=12,
            width=360,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH
            if stretch
            else ft.CrossAxisAlignment.START,
        )
        if not deleting:
            dialog = EditorDialog(
                title,
                form,
                [cancel, submit],
                compact=lambda: self.screen_class == ScreenClass.COMPACT,
                cancel_on_back=cancel_edit if on_cancel is not None else None,
            )
        else:
            dialog = ft.AlertDialog(
                modal=True,
                scrollable=True,
                title=deletion_title(title) if deleting else title,
                content=form,
                actions=[cancel, submit],
            )
        show_dialog(self.page, dialog)

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

    @staticmethod
    def _opening_balance_for_edit(
        journal: Journal, transaction: Transaction
    ) -> tuple[Account, Decimal]:
        accounts = [
            account
            for account in journal.ledger.accounts
            if any(posting.account == account.id for posting in transaction.postings)
        ]
        if len(accounts) != 1:
            raise LedgerError("crud_opening_complex")
        account = accounts[0]
        return account, journal.opening_balance(account.id)

    def _can_edit_opening(self, journal: Journal, transaction: Transaction) -> bool:
        try:
            self._opening_balance_for_edit(journal, transaction)
        except LedgerError as exc:
            if str(exc) != "crud_opening_complex":
                raise
            return False
        return True

    def _edit_opening_balance(self, journal: Journal, transaction: Transaction) -> None:
        account, balance = self._opening_balance_for_edit(journal, transaction)
        amount = ft.TextField(
            label=self.tr("transfer_owed" if account.liability else "amount"),
            value=str(balance),
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
                self._entity_label(
                    account.id,
                    ft.Text(
                        f"{self._entity_display_name(account.id, account.name)} "
                        f"({account.currency})"
                    ),
                ),
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
                names = {
                    account.id: self._entity_display_name(account.id, account.name)
                    for account in journal.ledger.accounts
                }
                names.update(
                    {
                        category.id: self._entity_display_name(category.id, category.name)
                        for category in journal.ledger.categories
                    }
                )

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
                                    self._entity_label(
                                        posting.account, ft.Text(posting_details(posting))
                                    )
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

    def _quick_category_dialog(
        self,
        journal: Journal,
        source: str,
        when: DateTimeFields,
        *,
        roots: tuple[str, *tuple[str, ...]],
        on_created: Callable[[str, str], None],
        fee_branches: tuple[str, ...] | None = None,
    ) -> None:
        icon_store = self.icons
        icon = IconDraft(
            self.page,
            self.language,
            icon_store.assets.values() if icon_store is not None else (),
            ai_settings=self.ai_settings,
            reference_icons=self._reference_icons(journal),
            target=lambda: IconTarget(
                category_name.value or "", category_path.value or "", category=True, draft=True
            ),
        )
        icon_heads = dict(icon_store.heads) if icon_store is not None else {}
        try:
            category_opened_default = date.fromisoformat(when.values()[0])
        except LedgerError:
            category_opened_default = self._today()
        if not EPOCH <= category_opened_default <= self._today():
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
        root_labels = {"Expenses": self.tr("kind_expense"), "Income": self.tr("kind_income")}
        category_root = ft.Dropdown(
            data="quick_category_root",
            label=self.tr("incoming_type"),
            expand=True,
            value=roots[0],
            options=[ft.DropdownOption(key=root, text=root_labels[root]) for root in roots],
        )
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
        inactive = tuple(item.id for item in journal.ledger.categories if not item.active)

        def eligible(identifier: str) -> bool:
            return fee_branches is None or (
                fee_category_in_branches(identifier, fee_branches)
                and not fee_category_in_branches(identifier, inactive)
            )

        def update_suggestion(*, regenerate_code: bool = False) -> None:
            nonlocal code_manual
            if regenerate_code:
                code_manual = False
            regenerate.disabled = not (code_manual and (category_name.value or "").strip())
            root = category_root.value or roots[0]
            parent_options = self._category_parent_options(
                tuple(item for item in journal.ledger.categories if eligible(item.id)), root
            )
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
            suggestion_parent = category_parent.value or None
            if suggestion_parent is None and fee_branches and root not in fee_branches:
                # Journal creation also declares missing ancestors of an eligible path.
                suggestion_parent = fee_branches[0]
                if suggestion_parent not in existing_ids:
                    existing_ids = (*existing_ids, suggestion_parent)
            suggestions = suggest_category_code(
                category_name.value or "",
                root,
                existing_ids,
                parent=suggestion_parent,
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
            if candidate and candidate.valid and not eligible(candidate.code):
                category_status.value = self.tr("fee_category_branch_required")
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
            category_parent.leading_icon = self._entity_dropdown_icon(category_parent.value or "")
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

        def root_changed(event: ft.Event[ft.Dropdown]) -> None:
            category_parent.value = ""
            parent_changed(event)

        category_root.on_select = root_changed
        category_name.on_change = name_changed
        category_path.on_change = path_changed
        category_parent.on_select = parent_changed
        category_alternatives.on_select = alternative_selected
        regenerate.on_click = regenerate_code
        cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
        create = ft.TextButton(self.tr("create_category"))

        async def save_category(event: ft.Event[ft.TextButton]) -> None:
            async def save() -> None:
                if self.journal is not journal or journal.source != source:
                    raise ConflictError("crud_stale")
                root = category_root.value or roots[0]
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
                if not eligible(code.code):
                    raise LedgerError("fee_category_branch_required")
                backup = await journal.create_category(
                    code.code,
                    category_name.value or "",
                    root=root,
                    opened=date.fromisoformat(category_opened.values()[0]),
                )
                identifier = code.code
                updated_source = journal.source
                self.page.pop_dialog()
                self._saved(backup)
                try:
                    await self._assign_created_icon(
                        journal, icon_store, icon, identifier, icon_heads.get(identifier, ())
                    )
                finally:
                    on_created(identifier, updated_source)

            create.disabled = cancel.disabled = True
            icon.disabled = True
            try:
                await self._action(save)
            finally:
                create.disabled = cancel.disabled = False
                icon.disabled = False
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
                        ft.Row([category_root])
                        if len(roots) > 1
                        else ft.Text(root_labels[roots[0]]),
                        *(
                            [ft.Text(self.tr("fee_category_branch_required"))]
                            if fee_branches and "Expenses" not in fee_branches
                            else []
                        ),
                        ft.Row([self._category_menu_anchor(category_parent)]),
                        category_name,
                        icon,
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

    def _batch_dialog(
        self,
        journal: Journal,
        source: str,
        kind: Literal["expense", "income"],
        account_id: str | None,
        category_id: str | None,
        amount_value: str,
        description_value: str,
        payee_value: str,
        original_when: DateTimeFields,
    ) -> None:
        accounts = [item for item in journal.ledger.accounts if item.active]
        currencies = {item.id: item.currency for item in accounts}
        categories = [
            (item.id, item.name)
            for item in sorted(journal.ledger.categories, key=lambda item: item.id.split(":"))
            if journal.ledger.category_active(item.id)
            and item.id.startswith(("Expenses:", "Income:"))
        ]
        shared_kind = ft.Dropdown(
            data="batch_kind",
            expand=True,
            label=self.tr("incoming_type"),
            value=kind,
            options=[
                ft.DropdownOption(key=value, text=self.tr(f"kind_{value}"))
                for value in ("expense", "income")
            ],
        )
        shared_account = ft.Dropdown(
            data="batch_account",
            expand=True,
            label=self.tr("primary_account"),
            value=account_id,
            options=self._account_options(accounts),
        )

        def category_options(selected: str | None) -> list[ft.DropdownOption]:
            root = "Income:" if shared_kind.value == "income" else "Expenses:"
            available = [
                (identifier, name)
                for identifier, name in categories
                if identifier.startswith(root) or identifier == selected
            ]
            return self._category_option_lines(self._category_form_options(available))

        shared_category = ft.Dropdown(
            data="batch_category",
            expand=True,
            label=self.tr("expense_category"),
            value=category_id,
            options=category_options(category_id),
        )
        shared_payee = ft.TextField(
            data="batch_payee",
            label=self.tr("payee"),
            value=payee_value,
            keyboard_type=ft.KeyboardType.NAME,
        )
        row_column = ft.Column(spacing=16, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        summary = ft.Text(data="batch_summary", text_align=ft.TextAlign.CENTER)
        submit_label = ft.Text(text_align=ft.TextAlign.CENTER)
        reconciliation = ft.Text(data="batch_reconciliation", visible=False)
        rows: list[BatchRow] = []
        register_inputs: Callable[[ft.Control], None] | None = None
        saving = False

        def remember_registration(register: Callable[[ft.Control], None]) -> None:
            nonlocal register_inputs
            register_inputs = register

        def update_summary() -> None:
            submit_label.value = self.tr("batch_save", count=len(rows))
            totals: dict[str, Decimal] = {}
            for row in rows:
                currency = currencies.get(row.account.value or shared_account.value or "")
                try:
                    value = money(row.amount.value or "")
                    if not currency or value <= 0:
                        raise LedgerError("crud_amount_invalid")
                except LedgerError:
                    summary.value = self.tr("batch_totals", count=len(rows), totals="—")
                    return
                with localcontext() as context:
                    context.prec = 50
                    totals[currency] = totals.get(currency, Decimal(0)) + value
            text = " · ".join(
                format_amount(value, self.language, currency)
                for currency, value in sorted(totals.items())
            )
            summary.value = self.tr("batch_totals", count=len(rows), totals=text or "—")

        def update_labels() -> None:
            category_label = self.tr(
                "income_category" if shared_kind.value == "income" else "expense_category"
            )
            shared_category.label = category_label
            shared_payee.label = self.tr(
                "incoming_sender" if shared_kind.value == "income" else "payee"
            )
            for row in rows:
                row.category.label = category_label
                overridden = bool(
                    row.account.value or row.category.value or row.override_payee.value
                )
                row.details_button.content = self.tr("batch_row_details") + (
                    f" · {self.tr('batch_overridden')}" if overridden else ""
                )

        def changed(event: ft.Event) -> None:
            update_labels()
            update_summary()
            self.page.update()

        def kind_changed(event: ft.Event[ft.Dropdown]) -> None:
            shared_category.options = category_options(shared_category.value)
            for row in rows:
                row.category.options = [
                    ft.DropdownOption(key="", text=self.tr("batch_use_shared")),
                    *category_options(row.category.value),
                ]
            changed(event)

        shared_kind.on_select = kind_changed
        shared_account.on_select = shared_category.on_select = changed
        shared_payee.on_change = changed

        def make_row(
            *,
            previous_date: str | None = None,
            first: bool = False,
        ) -> BatchRow:
            when = DateTimeFields(
                self.language,
                self._effective_zone(),
                device_zone=self._picker_zone(),
                compact=True,
            )
            if first:
                when.occurred.value = original_when.occurred.value
                when.clock.value = original_when.clock.value
                when.original_occurred = original_when.original_occurred
                when.original_timestamp = original_when.original_timestamp
                when.original_fields = original_when.original_fields
            elif previous_date is not None:
                when.occurred.value = previous_date
            row_amount = ft.TextField(
                data="batch_row_amount",
                label=self.tr("amount"),
                value=amount_value if first else "",
                autofocus=first,
                on_change=changed,
            )
            description = ft.TextField(
                data="batch_row_description",
                label=self.tr("crud_description_label"),
                value=description_value,
                on_change=changed,
            )
            account = ft.Dropdown(
                data="batch_row_account",
                expand=True,
                label=self.tr("primary_account"),
                value="",
                options=[
                    ft.DropdownOption(key="", text=self.tr("batch_use_shared")),
                    *self._account_options(accounts),
                ],
                on_select=changed,
            )
            category = ft.Dropdown(
                data="batch_row_category",
                expand=True,
                label=self.tr("expense_category"),
                value="",
                options=[
                    ft.DropdownOption(key="", text=self.tr("batch_use_shared")),
                    *category_options(None),
                ],
                on_select=changed,
            )
            payee = ft.TextField(data="batch_row_payee", label=self.tr("payee"), visible=False)
            override_payee = ft.Checkbox(
                data="batch_override_payee", label=self.tr("batch_override_payee")
            )

            def payee_changed(event: ft.Event[ft.Checkbox]) -> None:
                payee.visible = bool(override_payee.value)
                changed(event)

            override_payee.on_change = payee_changed
            payee.on_change = changed
            details = ft.Column(
                [
                    ft.Row([account]),
                    ft.Row([self._category_menu_anchor(category)]),
                    override_payee,
                    payee,
                    when.time_controls,
                ],
                data="batch_row_details_body",
                key=ft.ScrollKey(f"batch-row-details-body:{row_amount._i}"),
                visible=False,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )
            title = ft.Text(weight=ft.FontWeight.BOLD, expand=True)
            error = ft.Text(data="batch_row_error", color=ft.Colors.ERROR, visible=False)
            container = ft.Column(
                data="batch_row",
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                spacing=8,
            )
            details_button = ft.TextButton(
                self.tr("batch_row_details"),
                icon=ft.Icons.TUNE,
                data="batch_row_details",
                expand=True,
                key=ft.ScrollKey(f"batch-row-details:{row_amount._i}"),
            )
            add_button = ft.TextButton(
                self.tr("batch_add_row"),
                icon=ft.Icons.ADD,
                data="batch_add_row",
                on_click=add_row,
            )
            remove_button = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                tooltip=self.tr("batch_remove_row"),
            )
            row = BatchRow(
                when,
                row_amount,
                description,
                account,
                category,
                override_payee,
                payee,
                title,
                details_button,
                remove_button,
                details,
                error,
                container,
            )

            async def scroll_to_details(
                event: ft.LayoutSizeChangeEvent[ft.LayoutControl],
            ) -> None:
                if event.control is not details or not details.visible:
                    return
                details.on_size_change = None
                try:
                    # The client must lay out the revealed options before
                    # scroll_to can resolve the target key.
                    await form.scroll_to(scroll_key=details.key, duration=350)
                except Exception as exc:
                    self._error(exc, notify=True)
                self.page.update()

            def toggle_details(event: ft.Event[ft.TextButton]) -> None:
                details.visible = not details.visible
                # Subscribe only while revealing: the client reports the new
                # size once the options have been laid out.
                details.on_size_change = scroll_to_details if details.visible else None
                self.page.update()

            details_button.on_click = toggle_details

            def remove(event: ft.Event[ft.IconButton]) -> None:
                if len(rows) == 1:
                    return
                rows.remove(row)
                row_column.controls.remove(container)
                refresh_rows()
                self.page.update()

            remove_button.on_click = remove
            container.controls = [
                ft.Row([title, remove_button]),
                when,
                row_amount,
                description,
                ft.Row([details_button, add_button], spacing=0),
                details,
                error,
            ]
            return row

        def refresh_rows() -> None:
            for index, row in enumerate(rows, start=1):
                row.title.value = self.tr("batch_row", number=index)
                row.remove_button.disabled = len(rows) == 1
            update_labels()
            update_summary()

        async def add_row(event: ft.Event[ft.TextButton]) -> None:
            row = make_row(previous_date=rows[-1].when.occurred.value)
            rows.append(row)
            row_column.controls.append(row.container)
            if register_inputs is not None:
                register_inputs(row.container)
            refresh_rows()
            self.page.update()
            await row.amount.focus()
            await form.scroll_to(scroll_key=row.details_button.key, duration=350)

        cancel = ft.TextButton(self.tr("crud_cancel"))
        submit = ft.TextButton(
            content=ft.Column(
                [submit_label, ft.Container(summary, width=200, alignment=ft.Alignment.CENTER)],
                spacing=0,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        reload_batch = ft.TextButton(self.tr("batch_reload"), data="batch_reload", visible=False)
        first_row = make_row(first=True)
        rows.append(first_row)
        row_column.controls.append(first_row.container)
        form = ft.Column(
            [
                ft.Text(self.tr("batch_shared"), weight=ft.FontWeight.BOLD),
                ft.Row([shared_kind]),
                ft.Row([shared_account]),
                ft.Row([self._category_menu_anchor(shared_category)]),
                ft.ExpansionTile(
                    data="batch_advanced",
                    title=self.tr("transaction_advanced"),
                    controls=[shared_payee],
                    controls_padding=ft.Padding.only(top=8),
                    maintain_state=True,
                    expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                row_column,
                reconciliation,
                reload_batch,
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        def snapshot() -> tuple[object, ...]:
            return (
                shared_kind.value,
                shared_account.value,
                shared_category.value,
                shared_payee.value,
                *(
                    (
                        row.when.occurred.value,
                        row.when.clock.value,
                        row.amount.value,
                        row.description.value,
                        row.account.value,
                        row.category.value,
                        row.override_payee.value,
                        row.payee.value,
                    )
                    for row in rows
                ),
            )

        initial = snapshot()
        initial_has_input = bool(
            amount_value
            or description_value
            or payee_value
            or category_id
            or account_id != self.default_account_id
            or kind != "expense"
            or not original_when.pristine
        )

        def cancel_batch() -> None:
            if saving:
                return
            if snapshot() == initial and not initial_has_input:
                self.page.pop_dialog()
                return

            def discard(event: ft.Event[ft.TextButton]) -> None:
                self.page.pop_dialog()
                self.page.pop_dialog()

            show_dialog(
                self.page,
                ft.AlertDialog(
                    modal=True,
                    title=self.tr("batch_discard_title"),
                    content=ft.Text(self.tr("batch_discard_body")),
                    actions=[
                        ft.TextButton(self.tr("keep_editing"), on_click=self._dismiss),
                        ft.TextButton(self.tr("batch_discard"), on_click=discard),
                    ],
                ),
            )

        cancel.on_click = lambda _: cancel_batch()

        async def reconcile(event: ft.Event[ft.TextButton]) -> None:
            nonlocal saving
            if saving:
                return
            saving = True
            reload_batch.disabled = cancel.disabled = form.disabled = True
            self.page.update()

            async def reload() -> None:
                if self.journal is not journal:
                    raise ConflictError("crud_stale")
                await journal.load()
                unchanged = journal.source == source
                reconciliation.value = self.tr(
                    "batch_reload_unchanged" if unchanged else "batch_reload_changed"
                )
                reconciliation.visible = True
                submit.disabled = not unchanged
                reload_batch.visible = not unchanged

            try:
                await self._action(reload)
            finally:
                saving = False
                reload_batch.disabled = cancel.disabled = form.disabled = False
                self.page.update()

        reload_batch.on_click = reconcile

        async def commit(event: ft.Event[ft.TextButton]) -> None:
            nonlocal saving
            if saving or submit.disabled:
                return
            saving = True
            submit.disabled = cancel.disabled = form.disabled = True
            for row in rows:
                row.error.visible = False
            self.page.update()

            async def save() -> None:
                if self.journal is not journal:
                    raise ConflictError("crud_stale")
                entries: list[OrdinaryTransactionDraft] = []
                entry_rows: list[int] = []
                invalid_fields: dict[int, ft.TextField] = {}
                if shared_kind.value not in {"expense", "income"}:
                    raise LedgerError("crud_transaction_readonly")
                current_kind: Literal["expense", "income"] = (
                    "income" if shared_kind.value == "income" else "expense"
                )
                for index, row in enumerate(rows):
                    try:
                        occurred, timestamp = row.when.values()
                    except LedgerError as exc:
                        row.error.value = localized_error(self.language, str(exc))
                        row.error.visible = True
                        if str(exc).startswith("crud_time"):
                            row.details.visible = True
                            invalid_fields[index] = row.when.clock
                        else:
                            invalid_fields[index] = row.when.occurred
                        continue
                    entry_rows.append(index)
                    entries.append(
                        OrdinaryTransactionDraft(
                            kind=current_kind,
                            account=row.account.value or shared_account.value or "",
                            category=row.category.value or shared_category.value or "",
                            amount=row.amount.value or "",
                            occurred=occurred,
                            timestamp=timestamp,
                            payee=(
                                row.payee.value or ""
                                if row.override_payee.value
                                else shared_payee.value or ""
                            ),
                            description=row.description.value or "",
                        )
                    )
                if invalid_fields:
                    if entries:
                        try:
                            journal.validate_ordinary_batch(entries)
                        except OrdinaryBatchValidationError as exc:
                            for error in exc.errors:
                                index = entry_rows[error.row - 1]
                                row = rows[index]
                                row.error.value = localized_error(self.language, error.message)
                                row.error.visible = True
                                invalid_fields[index] = row.amount
                    form.disabled = False
                    self.page.update()
                    await invalid_fields[min(invalid_fields)].focus()
                    return
                try:
                    backup = await journal.save_ordinary_batch(entries, source)
                except OrdinaryBatchValidationError as exc:
                    for error in exc.errors:
                        row = rows[error.row - 1]
                        row.error.value = localized_error(self.language, error.message)
                        row.error.visible = True
                    form.disabled = False
                    self.page.update()
                    await rows[exc.errors[0].row - 1].amount.focus()
                    return
                self.page.pop_dialog()
                self._saved(backup)

            try:
                await self._action(save)
            finally:
                saving = False
                submit.disabled = (
                    self.journal is not journal
                    or not journal.ready
                    or not journal.storage.loaded
                    or journal.source != source
                )
                if submit.disabled:
                    reconciliation.value = self.tr("batch_reload_required")
                    reconciliation.visible = True
                    reload_batch.visible = True
                cancel.disabled = form.disabled = False
                self.page.update()

        submit.on_click = commit
        refresh_rows()
        dialog = EditorDialog(
            self.tr("batch_title"),
            form,
            [cancel, submit],
            compact=lambda: self.screen_class == ScreenClass.COMPACT,
            cancel_on_back=cancel_batch,
        )
        self.page.pop_dialog()
        show_dialog(self.page, dialog, register_dynamic_inputs=remember_registration)

    def _transaction_dialog(
        self,
        journal: Journal,
        transaction: Transaction | None = None,
        *,
        review: NotificationReview | None = None,
        after_review: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if review is not None and transaction is not None:
            raise ValueError("A notification review must create a new transaction.")
        initial: NotificationDraft | None = None
        if review is not None:
            initial = read_review_draft(review.item)
            if not review.snapshot:
                item = review.store.begin_review(
                    review.item.id,
                    review.item.review_snapshot,
                    journal_id=journal_identity(journal),
                )
                review.item = item
                review.snapshot = item.review_snapshot
            elif review.item.journal_id != journal_identity(journal):
                raise NotificationStoreError("Draft targets another journal.")
        source = journal.source
        dropdown_rows: list[ft.Row] = []

        def aligned_dropdown(control: ft.Control, *actions: ft.Control) -> ft.Row:
            row = ft.Row([control, *actions])
            dropdown_rows.append(row)
            return row

        def dropdown_row_field(row: ft.Row) -> ft.Control:
            control = row.controls[0]
            return control.dropdown if isinstance(control, CategoryMenuAnchor) else control

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
        initial_fee_category = journal.ledger.default_fee_category or None
        fee_branches = self.fee_preferences.branches
        inactive_categories = tuple(
            item.id for item in journal.ledger.categories if not item.active
        )
        creatable_fee_branches = tuple(
            branch
            for branch in fee_branches
            if not fee_category_in_branches(branch, inactive_categories)
        )
        retained_fee_category = saved_fee.fee_category if saved_fee and saved_fee.fee else None
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
            allow_empty_selection=review is not None,
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
            value=original_asset.account
            if original_asset
            else self.default_account_id
            if transaction is None
            else None,
            options=self._account_options(
                [
                    account
                    for account in journal.ledger.accounts
                    if account.active or (original_asset and original_asset.account == account.id)
                ]
            ),
        )
        if transaction is None and account.value not in {option.key for option in account.options}:
            account.value = None
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
        category_row = ft.Row([self._category_menu_anchor(category), category_create], spacing=0)
        amount = ft.TextField(
            data="amount",
            expand=True,
            label=self.tr("amount"),
            value=str(abs(original_asset.amount)) if original_asset else "",
            autofocus=transaction is None,
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
            on_select=self._entity_dropdown_selected,
            expand=True,
            label=self.tr("transfer_fee_category"),
            value=saved_fee.fee_category if saved_fee and saved_fee.fee else initial_fee_category,
            options=self._fee_category_options(
                journal, fee_branches, retained=retained_fee_category
            ),
        )
        fee_category_create = ft.IconButton(
            data="fee_category_create",
            icon=ft.Icons.ADD,
            tooltip=self.tr("create_category"),
            visible=False,
        )
        fee_category_restriction = ft.Text(
            self.tr("fee_category_branch_required"), size=12, visible=False
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
                aligned_dropdown(self._category_menu_anchor(fee_category), fee_category_create),
                fee_category_restriction,
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
        notification_currency = ft.TextField(
            data="notification_currency",
            label=self.tr("currency"),
            value=initial.currency or "" if initial else "",
        )
        if initial is not None:
            sign.selected = (
                ["-"]
                if initial.kind == "expense"
                else ["+"]
                if initial.kind == "income"
                else ["transfer"]
                if initial.kind == "transfer"
                else []
            )
            account.value = (
                initial.account
                if initial.account in {option.key for option in account.options}
                else None
            )
            category.value = initial.category
            amount.value = str(initial.amount) if initial.amount is not None else ""
            destination.value = initial.destination
            received.value = (
                str(initial.destination_amount) if initial.destination_amount is not None else ""
            )
            description.value = initial.description or ""
            payee.value = initial.payee or ""
            when.occurred.value = (
                date_input(initial.date, self.language) if initial.date is not None else ""
            )
            when.clock.value = initial.time or ""
            if review is not None and review.state is not None:
                state = review.state
                sign.selected = list(state.sign)
                special.value = state.special
                account.value, category.value = state.account, state.category
                amount.value, destination.value = state.amount, state.destination
                received.value, notification_currency.value = (
                    state.destination_amount,
                    state.currency,
                )
                description.value, payee.value = state.description, state.payee
                when.occurred.value, when.clock.value = state.date, state.time
                fee_enabled.value, fee.value = state.fee_enabled, state.fee
                fee_mode.value, fee_category.value = state.fee_mode, state.fee_category
                fee_currency.value = state.fee_currency
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
        if review is not None and review.state is not None:
            reference.value = review.state.reference
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
        category_values: dict[str, str | None] = (
            dict(review.state.category_values) if review is not None and review.state else {}
        )
        fee_drafts: dict[str, tuple[bool, str, str | None, str | None, str | None]] = (
            dict(review.state.fee_drafts) if review is not None and review.state else {}
        )
        batch_button = ft.TextButton(
            self.tr("batch_add"),
            icon=ft.Icons.PLAYLIST_ADD,
            data="batch_start",
            tooltip=self.tr("batch_unavailable"),
            visible=transaction is None and review is None,
        )
        batch_help = ft.Text(self.tr("batch_unavailable"), size=12, visible=False)
        currencies = {a.id: a.currency for a in journal.ledger.accounts}
        names = {
            account.id: self._entity_display_name(account.id, account.name)
            for account in journal.ledger.accounts
        }
        liabilities = {a.id for a in journal.ledger.accounts if a.liability}
        try:
            current_balances = journal.ledger.balances(today=journal.today())
        except LedgerError:
            current_balances = {}
        base_balances = dict(current_balances)
        with localcontext() as context:
            context.prec = 50
            for item in transaction.postings if transaction else ():
                if item.account in base_balances:
                    base_balances[item.account] -= item.amount

        def operation() -> str:
            if sign.selected == ["transfer"]:
                return "transfer"
            if sign.selected == ["-"]:
                return "expense"
            if not sign.selected:
                return ""
            return special.value or "income"

        previous_kind = (
            operation() if review is not None else transaction.kind if transaction else "expense"
        )

        def batch_supported() -> bool:
            return (
                review is None
                and operation() in {"expense", "income"}
                and not fee_enabled.value
                and fee.value in {"", "0"}
                and fee_mode.value in {None, "added"}
                and fee_category.value in {None, initial_fee_category}
                and not fee_currency.value
                and not destination.value
                and not received.value
                and not reference.value
                and not any(
                    value
                    for draft_kind, value in category_values.items()
                    if draft_kind != operation()
                )
                and not any(
                    enabled
                    or draft_fee not in {"", "0"}
                    or mode not in {None, "added"}
                    or category not in {None, initial_fee_category}
                    or bool(currency)
                    for enabled, draft_fee, mode, category, currency in fee_drafts.values()
                )
            )

        def is_exchange() -> bool:
            return bool(
                operation() == "transfer"
                and account.value in currencies
                and destination.value in currencies
                and currencies[account.value] != currencies[destination.value]
            )

        def update_balances() -> None:
            kind = operation()
            source_id = account.value or ""
            target_id = destination.value if kind == "transfer" else None
            exchange = is_exchange()
            try:
                changes = draft_movements(
                    kind,
                    source_id,
                    money(amount.value or ""),
                    fee=money(fee.value or "0")
                    if fee_enabled.value and kind in {"expense", "income", "transfer"}
                    else Decimal("0"),
                    fee_mode=fee_mode.value or "added",
                    destination=target_id,
                    destination_amount=money(received.value or "") if exchange else None,
                    currencies=(
                        currencies[source_id],
                        currencies[target_id],
                        fee_currency.value or currencies[source_id],
                    )
                    if exchange and target_id
                    else None,
                )
            except LedgerError:
                changes = {}
            for field, identifier in ((account, source_id), (destination, target_id)):
                if identifier not in current_balances:
                    field.helper_text = None
                    continue
                currency = currencies[identifier]
                now = self._account_balance_text(identifier, current_balances[identifier], currency)
                after = None
                if identifier in changes:
                    with localcontext() as context:
                        context.prec = 50
                        after = base_balances[identifier] + changes[identifier]
                field.helper_text = (
                    (now if identifier in liabilities else self.tr("form_balance_now", now=now))
                    if after is None
                    else self.tr(
                        "form_liability_balance" if identifier in liabilities else "form_balance",
                        now=now,
                        after=self._account_balance_text(identifier, after, currency),
                    )
                )
                field.helper_style = (
                    ft.TextStyle(color=ft.Colors.ERROR)
                    if after is not None and after < 0 and identifier not in liabilities
                    else None
                )

        def update_exchange() -> None:
            update_balances()
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
                        with localcontext() as context:
                            context.prec = 50
                            reverse = principal / gross
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
                                rate=(
                                    f"1 {source_currency} ≈ {rate:.12g} {target_currency} · "
                                    f"1 {target_currency} ≈ {reverse:.12g} {source_currency}"
                                ),
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

        def refresh_main_category(kind: str) -> None:
            root = "Income:" if kind == "income" else "Expenses:"
            category.options = self._category_option_lines(
                self._category_form_options(
                    [
                        (item.id, item.name)
                        for item in sorted(
                            journal.ledger.categories, key=lambda item: item.id.split(":")
                        )
                        if item.id.startswith(root)
                        and (
                            journal.ledger.category_active(item.id) or item.id == original_category
                        )
                    ]
                )
            )
            category.hint_text = (
                self.tr("category_none_available") if not category.options else None
            )

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
                ) = fee_drafts.get(kind, (False, "0", "added", initial_fee_category, None))
            previous_kind = kind
            batch_button.disabled = not batch_supported()
            batch_help.visible = transaction is None and review is None and batch_button.disabled
            category.label = self.tr("income_category" if kind == "income" else "expense_category")
            payee.label = self.tr(
                "transfer_counterparty"
                if kind == "transfer"
                else "payee"
                if kind == "expense"
                else "incoming_sender"
            )
            destination.options = self._account_options(
                [
                    item
                    for item in journal.ledger.accounts
                    if item.id != account.value
                    and (
                        item.active
                        or item.id
                        in {
                            original_asset.account if original_asset else "",
                            transfer.target.account if transfer else "",
                        }
                    )
                ]
            )
            destination.visible = kind == "transfer"
            if destination.visible and destination.value and destination.value == account.value:
                destination.error_text = self.tr("transfer_accounts")
            else:
                destination.error_text = None
            refresh_main_category(kind)
            category.visible = kind not in {"adjustment", "transfer"}
            category_row.visible = category.visible
            fee_fields.visible = kind in {"expense", "income", "transfer"}
            fee.visible = fee_category.visible = fee_fields.visible and bool(fee_enabled.value)
            fee_category_create.visible = fee_category.visible and bool(creatable_fee_branches)
            fee_category_restriction.visible = fee_category.visible and not creatable_fee_branches
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
                row.visible = dropdown_row_field(row).visible
            for selector in (account, category, destination, fee_category):
                selector.leading_icon = self._entity_dropdown_icon(selector.value or "")
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
                    category.leading_icon = self._entity_dropdown_icon(posting.account)
                    amount.value = str(posting.amount)
                batch_button.disabled = not batch_supported()
                batch_help.visible = (
                    transaction is None and review is None and batch_button.disabled
                )
                self.page.update()
            except Exception as exc:
                self._error(exc, notify=True)

        def create_category_dialog(event: ft.Event[ft.IconButton]) -> None:
            kind = operation()

            def select_created(identifier: str, updated_source: str) -> None:
                nonlocal source
                source = updated_source
                refresh_main_category(kind)
                category_values[kind] = category.value = identifier
                category.leading_icon = self._entity_dropdown_icon(identifier)

            self._quick_category_dialog(
                journal,
                source,
                when,
                roots=("Income" if kind == "income" else "Expenses",),
                on_created=select_created,
            )

        def create_fee_category_dialog(event: ft.Event[ft.IconButton]) -> None:
            def select_created(identifier: str, updated_source: str) -> None:
                nonlocal source
                source = updated_source
                fee_category.options = self._fee_category_options(
                    journal, fee_branches, retained=retained_fee_category
                )
                fee_category.value = identifier
                fee_category.leading_icon = self._entity_dropdown_icon(identifier)
                update_exchange()

            self._quick_category_dialog(
                journal,
                source,
                when,
                roots=("Expenses",),
                on_created=select_created,
                fee_branches=creatable_fee_branches,
            )

        sign.on_change = configure
        special.on_select = configure
        fee_enabled.on_change = configure
        account.on_select = configure
        category.on_select = self._transaction_entity_dropdown_selected
        destination.on_select = configure
        reference.on_select = fill_reference
        category_create.on_click = create_category_dialog
        fee_category_create.on_click = create_fee_category_dialog

        def recalculate(event: ft.Event[ft.TextField] | ft.Event[ft.Dropdown]) -> None:
            update_exchange()
            batch_button.disabled = not batch_supported()
            batch_help.visible = transaction is None and review is None and batch_button.disabled
            for row in dropdown_rows:
                row.visible = dropdown_row_field(row).visible
            self.page.update()

        amount.on_change = received.on_change = fee.on_change = recalculate
        fee_currency.on_select = recalculate
        fee_mode.on_select = recalculate

        def start_batch(event: ft.Event[ft.TextButton]) -> None:
            kind = operation()
            if not batch_supported():
                return
            if self.journal is not journal or journal.source != source:
                self._error(ConflictError("crud_stale"), notify=True)
                return
            self._batch_dialog(
                journal,
                source,
                "income" if kind == "income" else "expense",
                account.value,
                category.value,
                amount.value or "",
                description.value or "",
                payee.value or "",
                when,
            )

        batch_button.on_click = start_batch

        async def save() -> str | None:
            occurred, timestamp = when.values()
            kind = operation()
            if review is not None:
                if not kind:
                    raise LedgerError("notification_kind_required")
                selected_account = next(
                    (entry for entry in journal.ledger.accounts if entry.id == account.value), None
                )
                if (
                    not notification_currency.value
                    or selected_account is None
                    or selected_account.currency != notification_currency.value.strip().upper()
                ):
                    raise LedgerError("notification_currency_mismatch")
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

        def apply_review_rule() -> None:
            if review is None:
                return
            active_review = review
            current_fields = {
                "kind": operation() or None,
                "amount": amount.value or None,
                "currency": notification_currency.value or None,
                "account": account.value,
                "destination": destination.value,
                "destination_amount": received.value or None,
                "category": category.value,
                "payee": payee.value or None,
                "description": description.value or None,
                "date": when.occurred.value or None,
                "time": when.clock.value or None,
                "fee_enabled": str(bool(fee_enabled.value)),
                "fee": fee.value or None,
                "fee_mode": fee_mode.value,
                "fee_category": fee_category.value,
                "fee_currency": fee_currency.value,
                "reference": reference.value,
            }

            def applied(item_id: int, outcome: str) -> None:
                if outcome != "pending":
                    self.notification_reviews.pop(item_id, None)
                    self.page.pop_dialog()
                    self._notification_changed()
                    return
                try:
                    item = next(
                        item
                        for item in active_review.store.items(("pending",))
                        if item.id == item_id
                    )
                    draft = read_review_draft(item)
                    item = active_review.store.begin_review(
                        item.id, item.review_snapshot, journal_id=journal_identity(journal)
                    )
                except (StopIteration, NotificationStoreError, ValueError):
                    self._error(NotificationStoreError("Notification changed."), notify=True)
                    return
                active_review.item = item
                active_review.snapshot = item.review_snapshot
                active_review.state = None
                new_kind = draft.kind or ""
                old_kind = operation()
                fee_drafts[new_kind] = (
                    bool(fee_enabled.value),
                    fee.value,
                    fee_mode.value,
                    fee_category.value,
                    fee_currency.value,
                )
                category_values[new_kind] = draft.category
                sign.selected = (
                    ["-"]
                    if new_kind == "expense"
                    else ["+"]
                    if new_kind == "income"
                    else ["transfer"]
                    if new_kind == "transfer"
                    else []
                )
                if new_kind == "income":
                    special.value = "income"
                account.value = (
                    draft.account
                    if draft.account in {option.key for option in account.options}
                    else None
                )
                if old_kind == new_kind:
                    category.value = draft.category
                amount.value = str(draft.amount) if draft.amount is not None else ""
                destination.value = draft.destination
                received.value = (
                    str(draft.destination_amount) if draft.destination_amount is not None else ""
                )
                notification_currency.value = draft.currency or ""
                description.value = draft.description or ""
                payee.value = draft.payee or ""
                when.occurred.value = (
                    date_input(draft.date, self.language) if draft.date is not None else ""
                )
                when.clock.value = draft.time or ""
                configure()
                self._notification_changed()

            try:
                NotificationRuleApplication(
                    self.page,
                    self.tr,
                    review.store,
                    review.item,
                    ledger=journal.ledger,
                    today=journal.today(),
                    journal_id=journal_identity(journal),
                    current_fields=current_fields,
                    on_applied=applied,
                ).open()
            except (NotificationStoreError, ValueError) as exc:
                self._error(exc, notify=True)

        def review_rule_editor(
            draft: NotificationRule | None = None,
            sample_title: str | None = None,
            sample_body: str | None = None,
            *,
            edit_matching: bool = False,
        ) -> None:
            if review is None:
                return
            try:
                rule_id = matching_rule_id(review.store, review.item) if edit_matching else None
                if edit_matching and rule_id is None:
                    raise NotificationStoreError("Matching rule changed; review it again.")
                NotificationRuleEditor(
                    self.page,
                    self.tr,
                    review.store,
                    review.item.app_id,
                    rule_id=rule_id,
                    draft=draft,
                    sample_title=sample_title if draft is not None else review.item.title,
                    sample_body=sample_body if draft is not None else review.item.body,
                    ledger=journal.ledger,
                    today=journal.today,
                    on_saved=lambda _rule: apply_review_rule(),
                    on_ai=self._notification_ai_starter(review.item.app_id),
                ).open()
            except sqlite3.Error:
                logger.exception("Notification rule lookup storage failed")
                self._error(
                    NotificationStoreError("Notification storage is unavailable."), notify=True
                )
            except NotificationStoreError as exc:
                self._error(exc, notify=True)

        async def review_ai(event: ft.Event) -> None:
            if review is not None:
                await self._notification_ai_starter(review.item.app_id)(
                    review.item.title, review.item.body, review_rule_editor
                )

        controls: list[ft.Control] = [
            *(
                [
                    ft.ExpansionTile(
                        title=self.tr("notification_source"),
                        controls=[
                            ft.Text(review.item.app_id, selectable=True),
                            ft.Text(review.item.rule_name or self.tr("notification_no_rule")),
                            ft.Text(
                                format_timestamp(
                                    local_datetime(
                                        datetime.fromtimestamp(review.item.posted, UTC),
                                        self._effective_zone(),
                                    ).isoformat(),
                                    self.language,
                                )
                            ),
                            ft.Text(review.item.title or self.tr("notification_unavailable")),
                            ft.Text(review.item.body or self.tr("notification_unavailable")),
                            ft.Row(
                                [
                                    ft.TextButton(
                                        self.tr("notification_inbox_rule"),
                                        on_click=lambda _: review_rule_editor(),
                                    ),
                                    ft.TextButton(
                                        self.tr("notification_edit_matching_rule"),
                                        on_click=lambda _: review_rule_editor(edit_matching=True),
                                        visible=bool(
                                            review.item.rule_name and not review.item.stale_rule
                                        ),
                                    ),
                                    ft.TextButton(
                                        self.tr("notification_ai_title"), on_click=review_ai
                                    ),
                                ],
                                wrap=True,
                            ),
                        ],
                    ),
                    ft.Text(
                        self.tr("notification_needs_attention"),
                        visible=bool(initial and (initial.errors or initial.time is None)),
                    ),
                    notification_currency,
                ]
                if review is not None
                else []
            ),
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
            batch_button,
            batch_help,
        ]
        configure()

        def remember_review() -> None:
            if review is None:
                return
            review.state = NotificationFormState(
                list(sign.selected),
                special.value,
                account.value,
                category.value,
                amount.value or "",
                destination.value,
                received.value or "",
                notification_currency.value or "",
                description.value or "",
                payee.value or "",
                when.occurred.value or "",
                when.clock.value or "",
                bool(fee_enabled.value),
                fee.value or "",
                fee_mode.value,
                fee_category.value,
                fee_currency.value,
                reference.value,
                dict(category_values),
                dict(fee_drafts),
            )

        async def submit() -> str | None:
            if review is None:
                return await save()
            try:
                return await save_reviewed_notification(
                    review.store, review.item.id, journal, save, snapshot=review.snapshot
                )
            except LedgerError:
                if journal.ready and journal.storage.loaded and journal.storage.draft == source:
                    pending = next(
                        (
                            item
                            for item in review.store.items(("pending",))
                            if item.id == review.item.id
                        ),
                        None,
                    )
                    if pending is not None:
                        review.store.set_editing(review.item.id, True)
                raise

        self._edit_dialog(
            self.tr("crud_edit" if transaction else "add_transaction"),
            controls,
            journal,
            submit,
            stretch=True,
            after_save=after_review,
            on_cancel=remember_review if review is not None else None,
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
            category_opened: DateTimeFields | None = None
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
            else:
                category_opened = DateTimeFields(
                    self.language,
                    self._effective_zone(),
                    item.opened,
                    device_zone=self._picker_zone(),
                    compact=True,
                    show_accounting_date=True,
                )
                category_opened.occurred.label = self.tr("category_available_date")
            icon = IconField(
                self.language,
                lambda: self._open_icon_picker(journal, identifier, icon.set_icon),
                lambda: self._open_icon_picker(journal, identifier, icon.set_icon, remove=True),
            )
            icon.set_icon(self.icons.assets.get(identifier) if self.icons is not None else None)
            controls: list[ft.Control] = [
                ft.Text(identifier, selectable=True, weight=ft.FontWeight.BOLD),
                ft.Text(self.tr("crud_category_help" if category else "crud_account_help")),
                name,
                active,
                *([category_opened] if category_opened is not None else []),
                icon,
            ]
            if isinstance(item, Account):
                controls.append(ft.Text(item.currency))
                if item.liability:
                    controls.append(
                        ft.Text(
                            self._account_balance_text(
                                item.id,
                                journal.ledger.balances(today=journal.today())[item.id],
                                item.currency,
                            )
                        )
                    )
                controls.append(
                    opening if opening is not None else ft.Text(self.tr("crud_opening_complex"))
                )
                if opening_date is not None:
                    controls.append(opening_date)
            if category:
                controls.append(
                    ft.ExpansionTile(
                        title=self.tr("category_migration_advanced"),
                        maintain_state=True,
                        controls=[
                            ft.Text(self.tr("category_migration_intro")),
                            ft.TextButton(
                                self.tr("category_migration_start"),
                                disabled=not journal.category_migration_available,
                                on_click=lambda _: self._category_migration_dialog(
                                    journal, identifier, source
                                ),
                            ),
                            ft.Text(
                                self.tr("category_migration_atomic_required"),
                                visible=not journal.category_migration_available,
                            ),
                        ],
                    )
                )

            async def save() -> str | None:
                if category:
                    return await journal.edit_category(
                        identifier,
                        name.value or "",
                        bool(active.value),
                        source,
                        opened=(
                            date.fromisoformat(category_opened.values()[0])
                            if category_opened is not None
                            else None
                        ),
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

    def _category_migration_dialog(self, journal: Journal, identifier: str, source: str) -> None:
        if not journal.category_migration_available:
            self._error(StorageError("category_migration_atomic_required"), notify=True)
            return
        root, *components = identifier.split(":")
        parent = ft.Dropdown(
            label=self.tr("code_suggestion_parent"),
            on_select=self._entity_dropdown_selected,
            expand=True,
            options=self._category_parent_options(
                tuple(
                    item
                    for item in journal.ledger.categories
                    if item.id != identifier and not item.id.startswith(identifier + ":")
                ),
                root,
            ),
            value=":".join([root, *components[:-1]]) if len(components) > 1 else "",
        )
        if parent.value not in {option.key for option in parent.options}:
            parent.value = ""
        parent.leading_icon = self._entity_dropdown_icon(parent.value or "")
        code = ft.TextField(label=self.tr("category_migration_code_title"), value=components[-1])
        cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
        review = ft.TextButton(self.tr("category_migration_review"))

        async def preview(event: ft.Event[ft.TextButton]) -> None:
            if review.disabled:
                return
            review.disabled = cancel.disabled = parent.disabled = code.disabled = True

            async def prepare() -> None:
                if self.journal is not journal:
                    raise ConflictError("crud_stale")
                leaf = (code.value or "").strip()
                if not leaf or ":" in leaf:
                    raise LedgerError("category_migration_target_invalid")
                target = f"{parent.value or root}:{leaf}"
                plan = await journal.plan_category_migration(identifier, target, source)
                self._confirm_category_migration(journal, plan)

            try:
                await self._action(prepare)
            finally:
                review.disabled = cancel.disabled = parent.disabled = code.disabled = False
                self.page.update()

        review.on_click = preview
        show_dialog(
            self.page,
            ft.AlertDialog(
                modal=True,
                scrollable=True,
                title=self.tr("category_migration_start"),
                content=ft.Column(
                    [
                        ft.Text(identifier, weight=ft.FontWeight.BOLD, selectable=True),
                        ft.Text(self.tr("category_migration_intro")),
                        ft.Row([self._category_menu_anchor(parent)]),
                        code,
                        ft.Text(self.tr("category_migration_code")),
                    ],
                    width=360,
                    tight=True,
                    spacing=12,
                ),
                actions=[cancel, review],
            ),
        )

    def _confirm_category_migration(self, journal: Journal, plan: CategoryMigration) -> None:
        acknowledged = ft.Checkbox(semantics_label=self.tr("category_migration_ack"), value=False)
        cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
        submit = ft.TextButton(self.tr("category_migration_confirm"), disabled=True)
        busy = False

        def acknowledge(event: ft.Event[ft.Checkbox]) -> None:
            submit.disabled = busy or not acknowledged.value
            self.page.update()

        async def commit(event: ft.Event[ft.TextButton]) -> None:
            nonlocal busy
            if busy:
                return
            if not acknowledged.value:
                self._error(LedgerError("category_migration_ack_required"), notify=True)
                return
            busy = True
            submit.disabled = cancel.disabled = acknowledged.disabled = True

            async def migrate() -> None:
                if self.journal is not journal:
                    raise ConflictError("crud_stale")
                backup = await journal.migrate_category(plan)
                # Close source-bound dialogs before any post-commit preference failure.
                for _ in range(3):
                    self.page.pop_dialog()
                self._saved(backup)
                await self._migrate_category_preferences(plan)

            try:
                await self._action(migrate)
            finally:
                busy = False
                cancel.disabled = acknowledged.disabled = False
                submit.disabled = not acknowledged.value
                self.page.update()

        acknowledged.on_change = acknowledge
        submit.on_click = commit
        show_dialog(
            self.page,
            ft.AlertDialog(
                modal=True,
                scrollable=True,
                title=self.tr("category_migration_review"),
                content=ft.Column(
                    [
                        ft.Text(
                            f"{plan.original} → {plan.target}",
                            selectable=True,
                            size=12,
                            color=ft.Colors.OUTLINE,
                        ),
                        ft.Text(
                            self.tr(
                                "category_migration_counts",
                                categories=plan.categories,
                                transactions=plan.transactions,
                                budgets=plan.budgets,
                                references=plan.references,
                            )
                        ),
                        ft.Text(self.tr("category_migration_warning")),
                        ft.Row(
                            [
                                acknowledged,
                                ft.Text(self.tr("category_migration_ack"), expand=True),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                    ],
                    width=440,
                    tight=True,
                    spacing=12,
                ),
                actions=[cancel, submit],
            ),
        )

    async def _migrate_category_preferences(self, plan: CategoryMigration) -> None:
        def move(identifier: str) -> str:
            return migrated_category(identifier, plan.original, plan.target)

        migrated_settings = self.fee_preferences.migrate(plan.original, plan.target)
        settings_failure: Exception | None = None
        store = self.shared_defaults
        if store is not None:
            try:
                await store.load()
                if "commission" in store.conflicts:
                    raise SharedDefaultsError("shared_settings_conflicts")
                shared = store.values.get("commission")
                if isinstance(shared, FeePreferences):
                    updated = shared.migrate(plan.original, plan.target)
                    if updated != shared:
                        await store.save("commission", updated, store.heads.get("commission", ()))
                        self.default_fee_heads = store.heads.get("commission", ())
                    migrated_settings = updated
            except (PreferencesError, StorageError, TimeoutError, ConflictError) as exc:
                logger.exception("Journal migrated, but shared commission settings failed")
                settings_failure = exc
                self.shared_defaults_error = "shared_settings_migration_failed"
        self.fee_preferences = migrated_settings
        await self._apply_default_fee()
        self.fee_branches_draft = migrated_fee_branches(
            self.fee_branches_draft, plan.original, plan.target
        )
        self.fee_branches_original = migrated_fee_branches(
            self.fee_branches_original, plan.original, plan.target
        )
        if self.default_fee.value:
            self.default_fee.value = move(self.default_fee.value)
        if self.default_fee_original:
            self.default_fee_original = move(self.default_fee_original)
        self.category_parent.value = move(self.category_parent.value or "")
        self.category_expansion = {
            move(identifier): expanded for identifier, expanded in self.category_expansion.items()
        }
        self.budgets.category_id = move(self.budgets.category_id)
        if self.icons is not None:
            try:
                await self.icons.migrate(plan.original, plan.target)
                self.icon_revision += 1
            except (IconError, StorageError, TimeoutError) as exc:
                self.icon_notice.value = self.tr("icon_migration_failed")
                self.icon_notice.visible = True
                logger.warning("Journal migrated; icon migration failed (%s)", type(exc).__name__)
                raise IconError("icon_migration_failed") from exc
        if settings_failure is not None:
            raise SharedDefaultsError("shared_settings_migration_failed") from settings_failure

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
                        title=deletion_title(self.tr("crud_delete_blocked")),
                        content=ft.Column(
                            [
                                ft.Text(
                                    identifier,
                                    selectable=True,
                                    size=12,
                                    color=ft.Colors.OUTLINE,
                                ),
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
                backup = (
                    await journal.delete_category(identifier, source)
                    if kind == "category"
                    else await journal.delete_account(identifier, source)
                )
                if self.icons is not None and identifier in self.icons.heads:
                    try:
                        await self.icons.assign(identifier, None, self.icons.heads[identifier])
                        self.icon_revision += 1
                    except (IconError, StorageError, TimeoutError, ConflictError) as exc:
                        logger.warning(
                            "Entity deleted; icon cleanup failed (%s)", type(exc).__name__
                        )
                        raise IconError("icon_delete_failed") from exc
                return backup

            self._edit_dialog(
                self.tr("crud_confirm"),
                [
                    ft.Text(self.tr("crud_confirm_body")),
                    ft.Text(
                        identifier,
                        selectable=True,
                        size=12,
                        color=ft.Colors.OUTLINE,
                    ),
                ],
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
        self.category_parent.leading_icon = self._entity_dropdown_icon(
            self.category_parent.value or ""
        )
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

    def _category_menu_height(self) -> int | None:
        return 672 if self.screen_class == ScreenClass.COMPACT else None

    def _category_menu_base_style(self) -> ft.MenuStyle | None:
        if self.page is None:
            return None
        dark = self.page.theme_mode == ft.ThemeMode.DARK or (
            self.page.theme_mode != ft.ThemeMode.LIGHT
            and self.page.platform_brightness == ft.Brightness.DARK
        )
        theme = self.page.dark_theme if dark else self.page.theme
        return (
            theme.dropdown_theme.menu_style
            if isinstance(theme, ft.Theme) and theme.dropdown_theme is not None
            else None
        )

    def _category_menu_anchor(self, dropdown: ft.Dropdown) -> ft.Control:
        if self.screen_class != ScreenClass.COMPACT:
            return dropdown
        return CategoryMenuAnchor(
            dropdown,
            menu_style_provider=self._category_menu_base_style,
            mobile=self._mobile_item_actions,
            menu_height=self._category_menu_height(),
        )

    def _set_category_menu_row(self, row: ft.Row, dropdown: ft.Dropdown) -> None:
        current = row.controls[0]
        if isinstance(current, CategoryMenuAnchor):
            current.restore()
        row.controls = [self._category_menu_anchor(dropdown)]

    def _category_option_lines(
        self, options: list[ft.DropdownOption], *, fit_content: bool = False
    ) -> list[ft.DropdownOption]:
        paths = [(option.key or "").split(":") for option in options]
        for index, option in enumerate(options):
            parts = paths[index]
            if len(parts) < 2:
                continue
            depth = len(parts) - 2
            continued = tuple(
                any(
                    len(future) > level + 2
                    and future[: level + 2] == parts[: level + 2]
                    and future[level + 2] != parts[level + 2]
                    for future in paths[index + 1 :]
                )
                for level in range(depth)
            )
            content = option.content
            if not isinstance(content, ft.Container):
                content = ft.Container(
                    self._entity_label(
                        option.key or "",
                        ft.Text((option.text or "").lstrip(), max_lines=2),
                    ),
                    padding=ft.Padding.only(left=16 * depth),
                )
                option.leading_icon = None
            content.height = None if fit_content else self.category_menu_row_height
            content.alignment = ft.Alignment.CENTER_LEFT
            option.content = (
                CategoryMenuConnector(content=content, height=content.height, continued=continued)
                if self._mobile_item_actions
                else CategoryConnectorRow(content, continued, branch_length=11)
            )
            option.style = ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=12))
        return options

    def _category_form_option(self, identifier: str, name: str) -> ft.DropdownOption:
        return self._category_option(identifier, self._entity_display_name(identifier, name))

    @staticmethod
    def _entity_display_name(identifier: str, name: str) -> str:
        return name if name and name != identifier else identifier.rsplit(":", 1)[-1]

    @staticmethod
    def _selector_text(label: str, identifier: str, *, show_identifier: bool) -> ft.Text:
        return ft.Text(
            label,
            max_lines=2,
            spans=(
                [
                    ft.TextSpan(
                        f" ({identifier})",
                        style=ft.TextStyle(size=12, color=ft.Colors.OUTLINE),
                    )
                ]
                if show_identifier
                else None
            ),
        )

    @staticmethod
    def _ambiguous_identifiers(labels: list[tuple[str, str]]) -> set[str]:
        """Identifiers whose name collides with another row under the same visible parent."""
        visible = {identifier for identifier, _ in labels}
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for identifier, label in labels:
            parts = identifier.split(":")
            ancestor = ""
            for length in range(len(parts) - 1, 0, -1):
                candidate = ":".join(parts[:length])
                if candidate in visible:
                    ancestor = candidate
                    break
            groups[(ancestor, label)].append(identifier)
        return {
            identifier
            for identifiers in groups.values()
            if len(identifiers) > 1
            for identifier in identifiers
        }

    def _category_form_options(self, entities: list[tuple[str, str]]) -> list[ft.DropdownOption]:
        labels = [
            (identifier, self._entity_display_name(identifier, name))
            for identifier, name in entities
        ]
        ambiguous = self._ambiguous_identifiers(labels)
        return [
            self._category_option(identifier, label, show_identifier=identifier in ambiguous)
            for identifier, label in labels
        ]

    def _category_option(
        self, identifier: str, label: str, *, show_identifier: bool = False
    ) -> ft.DropdownOption:
        return ft.DropdownOption(
            key=identifier,
            text=f"{label} ({identifier})" if show_identifier else label,
            content=ft.Container(
                self._entity_label(
                    identifier,
                    self._selector_text(label, identifier, show_identifier=show_identifier),
                ),
                padding=ft.Padding.only(left=16 * (identifier.count(":") - 1)),
            ),
        )

    def _account_options(
        self,
        accounts: list[Account],
        *,
        include_currency: bool = True,
        use_dropdown_icon: bool = True,
    ) -> list[ft.DropdownOption]:
        labels = [
            (
                account,
                (
                    f"{self._entity_display_name(account.id, account.name)} ({account.currency})"
                    if include_currency
                    else self._entity_display_name(account.id, account.name)
                ),
            )
            for account in sorted(accounts, key=lambda account: account.id.split(":"))
        ]
        duplicates = Counter(label for _, label in labels)
        return [
            ft.DropdownOption(
                key=account.id,
                text=(f"{label} ({account.id})" if duplicates[label] > 1 else label),
                content=(
                    self._selector_text(label, account.id, show_identifier=True)
                    if duplicates[label] > 1
                    else None
                ),
                leading_icon=(
                    self._entity_dropdown_icon(account.id)
                    if use_dropdown_icon
                    else self._entity_icon(account.id)
                ),
            )
            for account, label in labels
        ]

    def _category_dropdown_option(
        self, item: Category, *, show_identifier: bool = False
    ) -> ft.DropdownOption:
        name = self._entity_display_name(item.id, item.name)
        label = f"{name} ({item.id})" if show_identifier else name
        return ft.DropdownOption(
            key=item.id,
            text=label,
            content=ft.Container(
                self._entity_label(item.id, ft.Text(label)),
                padding=ft.Padding.only(left=16 * (item.id.count(":") - 1), top=8, bottom=8),
            ),
        )

    def _category_parent_options(
        self, categories: tuple[Category, ...], root: str
    ) -> list[ft.DropdownOption]:
        options = [ft.DropdownOption(key="", text=self.tr("category_parent_none"))]
        items = [
            item
            for item in sorted(categories, key=lambda item: item.id.split(":"))
            if item.active and item.id.startswith(f"{root}:")
        ]
        options.extend(
            self._category_option_lines(
                [self._category_dropdown_option(item, show_identifier=True) for item in items],
                fit_content=True,
            )
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
        self.category_parent.leading_icon = self._entity_dropdown_icon(
            self.category_parent.value or ""
        )

    def _create_account(self, event: ft.Event[ft.Button]) -> None:
        journal = self._require_journal()
        source = journal.source
        icon_store = self.icons
        icon = IconDraft(
            self.page,
            self.language,
            icon_store.assets.values() if icon_store is not None else (),
            ai_settings=self.ai_settings,
            reference_icons=self._reference_icons(journal),
            target=lambda: IconTarget(
                self.account_name.value or "",
                self.account_id.value or "",
                category=False,
                draft=True,
            ),
        )
        icon_heads = dict(icon_store.heads) if icon_store is not None else {}
        identifier = ""
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
            nonlocal identifier
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
            identifier = code.code
            self.account_id.value = self.account_name.value = self.opening.value = ""
            self.account_code_manual = False
            return backup

        async def save_icon() -> None:
            await self._assign_created_icon(
                journal, icon_store, icon, identifier, icon_heads.get(identifier, ())
            )

        self._update_account_suggestion()
        self._edit_dialog(
            self.tr("account_form_title"),
            [
                ft.Row([self.account_root]),
                self.account_name,
                icon,
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
            after_save=save_icon,
        )

    def _account_type_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._refresh_labels()
        self._update_account_suggestion()
        self.page.update()

    def _render_default_account(self) -> None:
        journal = self.journal
        ready = journal is not None and journal.ready
        self.default_account.disabled = self.default_account_save.disabled = not ready
        if self.default_account_journal is not journal:
            self.default_account.value = self.default_account_id or ""
            self.default_account_original = self.default_account_id
            self.default_account_journal = journal
        accounts = journal.ledger.accounts if journal is not None else ()
        active = {account.id for account in accounts if account.active}
        options = [ft.DropdownOption(key="", text=self.tr("default_account_none"))]
        options += self._account_options(
            [account for account in accounts if account.active],
            use_dropdown_icon=False,
        )
        selected = self.default_account.value
        if selected and selected not in active:
            unavailable = next((account for account in accounts if account.id == selected), None)
            label = (
                f"{unavailable.name} ({unavailable.currency})"
                if unavailable is not None
                and unavailable.name
                and unavailable.name != unavailable.id
                else selected
            )
            options.append(
                ft.DropdownOption(
                    key=selected,
                    text=self.tr("default_account_unavailable_choice", identifier=label),
                )
            )
        if [
            (o.key, o.text, o.leading_icon.src if isinstance(o.leading_icon, ft.Image) else None)
            for o in options
        ] != [
            (o.key, o.text, o.leading_icon.src if isinstance(o.leading_icon, ft.Image) else None)
            for o in self.default_account.options
        ]:
            self.default_account.options = options
        self.default_account_notice.value = self.tr("default_account_unavailable")
        self.default_account_notice.visible = (
            ready and self.default_account_id is not None and self.default_account_id not in active
        )
        self.default_account.leading_icon = self._entity_dropdown_icon(
            self.default_account.value or ""
        )

    async def _save_default_account(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            journal = self._require_journal()
            if journal is not self.default_account_journal:
                raise ConflictError("crud_stale")
            selected = parse_default_account(self.default_account.value)
            if selected is not None and not any(
                account.id == selected and account.active for account in journal.ledger.accounts
            ):
                raise PreferencesError("default_account_unavailable")
            await self._save_shared_default("account", selected, self.default_account_heads)
            self._set_message("default_account_saved")

        await self._action(save)

    async def _save_default_fee(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            journal = self._require_journal()
            if journal is not self.default_fee_journal:
                raise ConflictError("crud_stale")
            selected = self.default_fee.value or ""
            settings = FeePreferences(self.fee_branches_draft, selected)
            if journal.source != self.default_fee_source:
                raise ConflictError("crud_stale")
            if (
                selected != BANK_FEES or any(c.id == BANK_FEES for c in journal.ledger.categories)
            ) and not journal.ledger.category_active(selected):
                raise LedgerError("incoming_category")
            await self._save_shared_default("commission", settings, self.default_fee_heads)
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
        labels: list[tuple[str, str]] = []
        for identifier in sorted(items, key=lambda identifier: identifier.split(":")):
            category = next(
                (item for item in journal.ledger.categories if item.id == identifier),
                None,
            )
            unavailable = identifier == retained and (
                not journal.ledger.category_active(identifier)
                or not fee_category_in_branches(identifier, branches)
            )
            if unavailable:
                display = (
                    category.name
                    if category is not None and category.name and category.name != identifier
                    else items[identifier]
                    if identifier == BANK_FEES
                    else identifier
                )
                label = self._category_label(
                    identifier,
                    self.tr("default_account_unavailable_choice", identifier=display),
                )
            else:
                label = self._category_label(
                    identifier,
                    self._entity_display_name(identifier, items[identifier]),
                )
            labels.append((identifier, label))
        ambiguous = self._ambiguous_identifiers(
            [(identifier, label.strip()) for identifier, label in labels]
        )
        return self._category_option_lines(
            [
                ft.DropdownOption(
                    key=identifier,
                    text=f"{label} ({identifier})" if identifier in ambiguous else label,
                    content=ft.Container(
                        self._entity_label(
                            identifier,
                            self._selector_text(
                                label.strip(),
                                identifier,
                                show_identifier=identifier in ambiguous,
                            ),
                        ),
                        padding=ft.Padding.only(left=16 * (identifier.count(":") - 1)),
                    ),
                )
                for identifier, label in labels
            ]
        )

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
                self._entity_display_name(identifier, items[identifier]),
                fee_category_in_branches(identifier, self.fee_branches_draft),
                any(identifier.startswith(branch + ":") for branch in self.fee_branches_draft),
            )
            for identifier in sorted(items, key=lambda identifier: identifier.split(":"))
        )
        if choices != self.fee_choices_state:
            self.fee_choices_state = choices
            self.fee_category_choices.controls = [
                ft.Container(
                    self._entity_label(
                        identifier,
                        ft.Checkbox(
                            label=label,
                            data=identifier,
                            value=checked,
                            disabled=inherited,
                            on_change=self._fee_branch_changed,
                        ),
                    ),
                    padding=ft.Padding.only(left=16 * identifier.count(":")),
                )
                for identifier, label, checked, inherited in choices
            ]
        options = self._fee_category_options(
            self.journal, self.fee_branches_draft, retained=self.default_fee.value
        )
        if self.default_fee.value and self.default_fee.value not in {o.key for o in options}:
            unavailable = next(
                (
                    category
                    for category in self.journal.ledger.categories
                    if category.id == self.default_fee.value
                ),
                None,
            )
            label = (
                unavailable.name
                if unavailable is not None and unavailable.name != unavailable.id
                else self.default_fee.value
            )
            options.append(
                ft.DropdownOption(
                    key=self.default_fee.value,
                    text=self.tr("default_account_unavailable_choice", identifier=label),
                )
            )
        self.default_fee.leading_icon = self._entity_dropdown_icon(self.default_fee.value or "")
        if [
            (o.key, o.text, o.leading_icon.src if isinstance(o.leading_icon, ft.Image) else None)
            for o in options
        ] != [
            (o.key, o.text, o.leading_icon.src if isinstance(o.leading_icon, ft.Image) else None)
            for o in self.default_fee.options
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
        if self.shared_defaults is not None and (
            "commission" in self.shared_defaults.conflicts or not self.shared_defaults.loaded
        ):
            await journal.set_default_fee_category(None, journal.source)
            self.default_fee_notice.visible = True
            self.default_fee_notice.value = self.tr("shared_settings_conflicts")
            return
        try:
            await journal.set_default_fee_category(self.default_fee_category, journal.source)
        except LedgerError as exc:
            if str(exc) != "incoming_category":
                raise
            await journal.set_default_fee_category(None, journal.source)
            self.default_fee_notice.value = self.tr("default_fee_unavailable")
            self.default_fee_notice.visible = True
        else:
            self.default_fee_notice.visible = False

    def _create_category(self, event: ft.Event[ft.Button]) -> None:
        journal = self._require_journal()
        source = journal.source
        icon_store = self.icons
        icon = IconDraft(
            self.page,
            self.language,
            icon_store.assets.values() if icon_store is not None else (),
            ai_settings=self.ai_settings,
            reference_icons=self._reference_icons(journal),
            target=lambda: IconTarget(
                self.category_name.value or "",
                self.category_id.value or "",
                category=True,
                draft=True,
            ),
        )
        icon_heads = dict(icon_store.heads) if icon_store is not None else {}
        identifier = ""
        category_opened = DateTimeFields(
            self.language,
            self._effective_zone(),
            device_zone=self._picker_zone(),
            compact=True,
        )
        category_opened.occurred.label = self.tr("category_available_date")

        async def save() -> str | None:
            nonlocal identifier
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
            identifier = code.code
            self.category_id.value = self.category_name.value = ""
            self.category_parent.value = ""
            self.category_code_manual = False
            return backup

        async def save_icon() -> None:
            await self._assign_created_icon(
                journal, icon_store, icon, identifier, icon_heads.get(identifier, ())
            )

        self._refresh_category_parent_options()
        self._update_category_suggestion()
        self._edit_dialog(
            self.tr("category_form_title"),
            [
                ft.Row([self.category_root]),
                ft.Row([self._category_menu_anchor(self.category_parent)]),
                self.category_name,
                icon,
                self.category_id,
                self.category_suggestion_status,
                self.category_suggestion_regenerate,
                self.category_suggestion_alternatives,
                category_opened,
            ],
            journal,
            save,
            stretch=True,
            after_save=save_icon,
        )

    def _category_type_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._refresh_category_parent_options()
        self._update_category_suggestion()
        if self.page:
            self.page.update()

    def _overflow_selected(self, event: ft.Event[ft.PopupMenuItem]) -> None:
        screen = event.control.data
        assert isinstance(screen, str)
        self._navigate(screen)

    def _screen_frame(self) -> ft.Container:
        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            content=self.screen_host,
        )

    def _show_screen(self, screen: str) -> None:
        changed = screen != self.current_screen
        self.current_screen = screen
        self._refresh_labels()
        if screen == "reports":
            self._render_reports()
        self._apply_layout((self.page.width or self.page.window.width) if self.page else 390)
        if changed:
            # A fresh boundary prevents Flet from applying stale offscreen child diffs
            # after it has already serialized the remounted screen.
            self.body.controls[-1] = self._screen_frame()
            if screen == "settings" and self.notification_settings is not None:
                self.page.run_task(self.notification_settings.load)
        if self.page:
            self.page.update()

    @property
    def screen_history(self) -> list[str]:
        return [visit.screen for visit in self._history]

    def _save_visit(self) -> None:
        if self.current_screen == "transactions":
            self._history[self.history_index] = NavigationVisit(
                self.current_screen, self.operations.location(), self.notification_inbox_open
            )

    def _restore_visit(self, index: int) -> None:
        self._save_visit()
        self.history_index = index
        visit = self._history[index]
        self.notification_inbox_open = visit.notification_inbox
        if visit.operations is not None:
            self.operations.restore(visit.operations)
            self._refresh()
        self._show_screen(visit.screen)
        if visit.notification_inbox and self.notification_settings is not None:
            self.page.run_task(self.notification_settings.load)

    def _navigate(
        self,
        screen: str,
        *,
        operations: OperationsLocation | None = None,
        notification_inbox: bool = False,
    ) -> None:
        if self._browser_back_waiter is not None:
            self.page.run_task(self._navigate_after_browser_back, screen, operations)
            return
        self._save_visit()
        if (
            screen == self.current_screen
            and notification_inbox == self.notification_inbox_open
            and (operations is None or operations == self.operations.location())
        ):
            self._show_screen(screen)
            return
        del self._history[self.history_index + 1 :]
        self._history.append(NavigationVisit(screen, operations, notification_inbox))
        self.history_index += 1
        self.notification_inbox_open = notification_inbox
        if self.browser_routes:
            del self.browser_routes[self.history_index :]
            self._browser_route_sequence += 1
            route = f"/history/{self._browser_route_prefix}/{self._browser_route_sequence}"
            self.browser_routes.append(route)
            self._browser_pending_routes[route] = asyncio.Event()
            self.page.run_task(self._push_browser_route, route)
        if operations is not None:
            self.operations.restore(operations)
            self._refresh()
        self._show_screen(screen)

    async def _navigate_after_browser_back(
        self, screen: str, operations: OperationsLocation | None = None
    ) -> None:
        async with self._browser_route_lock:
            self._navigate(screen, operations=operations)

    def _setup_browser_navigation(self) -> None:
        self.browser_routes = [self.page.route]
        self._browser_history = BrowserHistory()
        self.page.on_route_change = self._browser_route_changed

    async def _push_browser_route(self, route: str) -> None:
        async with self._browser_route_lock:
            acknowledgement = self._browser_pending_routes[route]
            try:
                await self.page.push_route(route)
                await asyncio.wait_for(acknowledgement.wait(), timeout=10)
            finally:
                self._browser_pending_routes.pop(route, None)

    def _browser_route_changed(self, event: ft.RouteChangeEvent) -> None:
        acknowledgement = self._browser_pending_routes.pop(event.route, None)
        if acknowledgement is not None:
            acknowledgement.set()
            return
        if event.route not in self.browser_routes:
            logger.warning("Ignoring an unrecognized browser navigation route")
            return
        index = self.browser_routes.index(event.route)
        if index != self.history_index:
            self._restore_visit(index)
        if self._browser_back_waiter is not None:
            self._browser_back_waiter.set()

    def _back(self) -> bool:
        if self.history_index == 0:
            return False
        self._restore_visit(self.history_index - 1)
        return True

    def _forward(self) -> bool:
        if self.history_index + 1 == len(self._history):
            return False
        self._restore_visit(self.history_index + 1)
        return True

    def _back_clicked(self, event: ft.Event[ft.IconButton]) -> None:
        if self.browser_routes:
            self.page.run_task(self._browser_back)
        else:
            self._back()

    def _forward_clicked(self, event: ft.Event[ft.IconButton]) -> None:
        self._forward()

    def _navigation_key(self, event: ft.KeyboardEvent) -> None:
        if not self.desktop_navigation or any(
            not isinstance(dialog, ft.SnackBar) and getattr(dialog, "open", False)
            for dialog in self.page._dialogs.controls
        ):
            return
        if event.shift or event.ctrl:
            return
        if self.page.platform == ft.PagePlatform.MACOS:
            if not event.meta or event.alt:
                return
            back, forward = event.key == "[", event.key == "]"
        else:
            if not event.alt or event.meta:
                return
            back, forward = event.key == "Arrow Left", event.key == "Arrow Right"
        if back:
            self._back()
        elif forward:
            self._forward()

    async def _browser_back(self) -> None:
        async with self._browser_route_lock:
            if self.history_index == 0:
                return
            if self._browser_history is None:
                raise RuntimeError("Browser history is not available.")
            waiter = asyncio.Event()
            self._browser_back_waiter = waiter
            try:
                await self._browser_history.back()
                await asyncio.wait_for(waiter.wait(), timeout=10)
            finally:
                self._browser_back_waiter = None

    async def _system_back(self, event: ft.Event[ft.View]) -> None:
        dialogs = self.page._dialogs.controls
        active = next(
            (
                dialog
                for dialog in reversed(dialogs)
                if not isinstance(dialog, ft.SnackBar) and getattr(dialog, "open", False)
            ),
            None,
        )
        if isinstance(active, EditorDialog) and active.cancel_on_back is not None:
            active.cancel_on_back()
            await event.control.confirm_pop(False)
        elif self.page.pop_dialog() is not None:
            await event.control.confirm_pop(False)
        else:
            await event.control.confirm_pop(not self._back())

    def _bottom_selected(self, event: ft.Event[ft.TextButton]) -> None:
        screen = event.control.data
        assert isinstance(screen, str)
        self._navigate(screen)

    def _rail_changed(self, event: ft.Event[ft.NavigationRail]) -> None:
        if event.control.selected_index is not None:
            self._navigate(RAIL_DESTINATIONS[event.control.selected_index])

    def _go_accounts(self, event: ft.Event[ft.TextButton]) -> None:
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
