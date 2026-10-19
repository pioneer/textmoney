import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import available_timezones

import flet as ft
from finance_saf import FinanceSaf

from app.domain.ledger import Account, Category, LedgerError, Transaction
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder, unwrap
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import DesktopPreferences, PreferencesError
from app.journal import FILENAME, Journal
from app.localization import (
    LANGUAGES,
    format_amount,
    format_date,
    format_timestamp,
    localized_error,
    system_language,
    translate,
)
from app.storage import StorageError
from app.timekeeping import local_datetime, named_zone
from app.ui.datetime_fields import DateTimeFields

logger = logging.getLogger(__name__)
FOLDER_KEY = "financepoc.folder.v1"
LANGUAGE_KEY = "txncount.language.v1"
THEME_KEY = "txncount.theme.v1"
TIMEZONE_KEY = "txncount.timezone.v1"
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
        self.system_zone = "system"
        self.screen_class = ScreenClass.COMPACT
        self.current_screen = "home"
        self.message_key = "no_folder"
        self.error_message: str | None = None
        self.diagnostics_visible = False

        self.message = ft.Text("", selectable=True)
        self.folder_label = ft.Text("", selectable=True)
        self.access = ft.Text("")
        self.account_id = ft.TextField(width=320)
        self.account_name = ft.TextField(width=320, keyboard_type=ft.KeyboardType.NAME)
        self.currency = ft.TextField(width=320, value="UAH")
        self.opening = ft.TextField(width=320)
        self.category_id = ft.TextField(width=320)
        self.category_name = ft.TextField(width=320)
        self.account_choice = ft.Dropdown(width=320, options=[])
        self.category_choice = ft.Dropdown(width=320, options=[])
        self.amount = ft.TextField(width=320)
        self.payee = ft.TextField(width=320, keyboard_type=ft.KeyboardType.NAME)
        self.payee_details = ft.ExpansionTile(
            title=self.tr("payee"),
            controls=[self.payee, ft.Text(self.tr("payee_help"), size=12)],
            maintain_state=True,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
        )
        self.expense_when = DateTimeFields(self.language, "system")
        self.expense_date = self.expense_when.occurred
        self.description = ft.TextField(multiline=True, min_lines=2, max_lines=5, text_size=17)
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
        self.history = ft.Column(spacing=0)
        self.home_balances = ft.Row(wrap=True, spacing=12, run_spacing=12)
        self.home_activity = ft.Column(spacing=0)
        self.accounts_panel_content = ft.Column()
        self.categories_panel_content = ft.Column()
        self.accounts_panel = ft.Container(padding=16, content=self.accounts_panel_content)
        self.categories_panel = ft.Container(padding=16, content=self.categories_panel_content)
        self.account_save = ft.Button(on_click=self._create_account)
        self.category_save = ft.Button(on_click=self._create_category)
        self.expense_save = ft.Button(on_click=self._add_expense)
        self.fab = ft.FloatingActionButton(
            icon=ft.Icons.ADD,
            on_click=self._expense,
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
        self.expense_screen = ft.Column(spacing=16)
        self.settings_screen = ft.Column(spacing=16)
        self.screen_host = ft.Container(expand=True, content=self.home_screen)

        self.page_title = ft.Text(size=25, weight=ft.FontWeight.W_600)
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
                    content=self.page_title,
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=20, vertical=8),
                    content=self.message,
                ),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.symmetric(horizontal=20, vertical=12),
                    content=self.screen_host,
                ),
            ],
        )
        self.controls.append(self.body)
        self.forms = [
            self.account_id,
            self.account_name,
            self.currency,
            self.opening,
            self.category_id,
            self.category_name,
            self.account_choice,
            self.category_choice,
            self.amount,
            self.payee_details,
            self.expense_when,
            self.description,
            self.operating_currencies,
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
        self.page_title.value = self.tr(
            {
                "home": "nav_home",
                "transactions": "nav_transactions",
                "accounts": "nav_accounts",
                "settings": "nav_settings",
                "expense": "expense_title",
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
        self.opening.label = self.tr("opening_balance")
        self.category_id.label = self.tr("category_path")
        self.category_id.hint_text = self.tr("category_path_hint")
        self.category_name.label = self.tr("category_name")
        self.account_choice.label = self.tr("pay_from")
        self.category_choice.label = self.tr("expense_category")
        self.amount.label = self.tr("amount")
        self.payee.label = self.tr("payee")
        self.payee_details.title = self.tr("payee")
        self.payee_details.controls = [self.payee, ft.Text(self.tr("payee_help"), size=12)]
        self.expense_when.set_language(self.language)
        self.description.label = self.tr("crud_description_label")
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
        self.expense_save.content = self.tr("save_expense")
        self.select_button.content = self.tr(
            "change_folder" if self.journal is not None else "select_folder"
        )
        self.welcome_select_button.content = self.tr("select_folder")
        self.reload_button.content = self.tr("reload")
        self.language_choice.label = self.tr("language")
        self.language_choice.options = [
            ft.DropdownOption(key="system", text=self.tr("language_system")),
            *[ft.DropdownOption(key=code, text=self.tr(f"language_{code}")) for code in LANGUAGES],
        ]
        self.language_choice.value = self.language if self.manual_language else "system"
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

    def _build_navigation(self) -> None:
        destinations = [
            ("nav_home", ft.Icons.HOME_OUTLINED, ft.Icons.HOME),
            ("nav_transactions", ft.Icons.RECEIPT_LONG_OUTLINED, ft.Icons.RECEIPT_LONG),
            (
                "nav_accounts",
                ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED,
                ft.Icons.ACCOUNT_BALANCE_WALLET,
            ),
            ("nav_settings", ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS),
        ]
        self.navigation_bar.destinations = [
            ft.NavigationBarDestination(
                label=self.tr(label), icon=icon, selected_icon=selected_icon
            )
            for label, icon, selected_icon in destinations
        ]
        self.navigation_bar.selected_index = self._selected_index()
        self.nav_rail.destinations = [
            ft.NavigationRailDestination(
                label=self.tr(label), icon=icon, selected_icon=selected_icon
            )
            for label, icon, selected_icon in destinations
        ]
        self.nav_rail.selected_index = self._selected_index()

    def _build_screens(self) -> None:
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
        self.fab.content = self.tr("add_expense")
        self.fab.disabled = not ready
        self.transactions_screen.controls = [
            ft.Text(self.tr("transactions_title"), size=21, weight=ft.FontWeight.W_600),
            self.history,
        ]
        self.accounts_panel_content.controls = [
            ft.Text(self.tr("account_list_title"), size=18, weight=ft.FontWeight.W_600),
            self.account_list,
            ft.Divider(),
            ft.Text(self.tr("account_form_title"), size=18, weight=ft.FontWeight.W_600),
            ft.Text(self.tr("account_code"), size=13),
            self.account_id,
            self.account_name,
            self.currency,
            self.opening,
            self.account_save,
        ]
        self.categories_panel_content.controls = [
            ft.Text(self.tr("categories_title"), size=18, weight=ft.FontWeight.W_600),
            self.category_list,
            ft.Divider(),
            ft.Text(self.tr("category_form_title"), size=18, weight=ft.FontWeight.W_600),
            self.category_id,
            self.category_name,
            self.category_save,
        ]
        account_panels: list[ft.Control] = [
            ft.Text(self.tr("accounts_title"), size=21, weight=ft.FontWeight.W_600),
            ft.Row(
                [self.accounts_panel, self.categories_panel],
                wrap=True,
                spacing=16,
                run_spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]
        self.accounts_screen.controls = account_panels
        self.expense_screen.controls = [
            ft.Text(self.tr("expense_title"), size=21, weight=ft.FontWeight.W_600),
            ft.Text(self.tr("expense_help")),
            ft.Row(
                [
                    self.account_choice,
                    self.category_choice,
                    self.amount,
                ],
                wrap=True,
                spacing=12,
                run_spacing=8,
            ),
            self.description,
            self.expense_when,
            self.payee_details,
            ft.Row(
                controls=[
                    self.expense_save,
                    ft.TextButton(self.tr("nav_home"), icon=ft.Icons.CLOSE, on_click=self._go_home),
                ],
                wrap=True,
            ),
        ]
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
                        ft.Text(self.tr("sync_status")),
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
                        ft.Text(f"{self.tr('app_version')}: 0.1.0"),
                    ],
                    spacing=12,
                ),
            ),
            self.advanced_settings,
            self.diagnostics_toggle,
            self.diagnostics,
        ]
        screens = {
            "home": self.home_screen,
            "transactions": self.transactions_screen,
            "accounts": self.accounts_screen,
            "settings": self.settings_screen,
            "expense": self.expense_screen,
        }
        self.screen_host.content = screens[self.current_screen]

    def _selected_index(self) -> int:
        return {
            "home": 0,
            "transactions": 1,
            "accounts": 2,
            "settings": 3,
            "expense": 0,
        }[self.current_screen]

    def _apply_layout(self, width: float | None) -> None:
        self.screen_class = screen_class(width)
        if self.screen_class == ScreenClass.COMPACT:
            self.controls = [self.body]
            self.body.width = width
            self.page.navigation_bar = self.navigation_bar if self.page else None
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
        self.description.width = min(MAX_CONTENT_WIDTH - 48, available)
        self.accounts_panel.width = (
            520 if self.screen_class == ScreenClass.EXPANDED else min(320, available)
        )
        self.categories_panel.width = (
            440 if self.screen_class == ScreenClass.EXPANDED else min(320, available)
        )
        self._build_navigation()
        self._build_home_layout()
        if self.page:
            self.fab.content = self.tr("add_expense")
            self.fab.disabled = not (self.journal and self.journal.ready)
            if self.page:
                self.page.floating_action_button = self.fab

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
        ready = self.journal is not None and self.journal.ready
        for control in (self.account_save, self.category_save, self.expense_save):
            control.disabled = not ready
        self.reload_button.disabled = self.journal is None
        self.operating_currencies.disabled = not ready
        if not ready or self.journal is None:
            self.account_list.controls = [ft.Text(self.tr("accounts_empty"))]
            self.category_list.controls = [ft.Text(self.tr("categories_empty"))]
            self.history.controls = [ft.Text(self.tr("transactions_empty"))]
            self.home_balances.controls = [ft.Text(self.tr("home_no_accounts"))]
            self.home_activity.controls = [ft.Text(self.tr("home_no_transactions"))]
            self._refresh_labels()
            return

        ledger = self.journal.ledger
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
            for currency, accounts in sorted(grouped.items())
        ] or [ft.Text(self.tr("home_no_accounts"))]
        names = {account.id: account.name for account in ledger.accounts}
        names.update({category.id: category.name for category in ledger.categories})
        transactions = sorted(
            ledger.transactions,
            key=lambda item: (
                item.date,
                datetime.fromisoformat(item.timestamp),
                item.id,
            ),
            reverse=True,
        )
        self.history.controls = [self._transaction_card(item, names) for item in transactions] or [
            ft.Text(self.tr("transactions_empty"))
        ]
        self.home_activity.controls = [
            self._transaction_card(item, names, compact=True) for item in transactions[:5]
        ] or [ft.Text(self.tr("home_no_transactions"))]
        self.account_list.controls = [
            self._entity_card(account, balances[account.id]) for account in ledger.accounts
        ] or [ft.Text(self.tr("accounts_empty"))]
        self.category_list.controls = [
            self._entity_card(item) for item in sorted(ledger.categories, key=lambda item: item.id)
        ] or [ft.Text(self.tr("categories_empty"))]
        self.account_choice.options = [
            ft.DropdownOption(key=item.id, text=f"{item.name} ({item.currency})")
            for item in ledger.accounts
            if item.active
        ]
        self.category_choice.options = [
            ft.DropdownOption(
                key=item.id,
                text=self._category_label(item.id, item.name),
            )
            for item in sorted(ledger.categories, key=lambda item: item.id)
            if ledger.category_active(item.id)
        ]
        if self.account_choice.value and self.account_choice.value not in {
            item.id for item in ledger.accounts if item.active
        }:
            self.account_choice.options.append(
                ft.DropdownOption(
                    key=self.account_choice.value,
                    text=self.tr("choose_account"),
                )
            )
        if self.category_choice.value and self.category_choice.value not in {
            category.id for category in ledger.categories if ledger.category_active(category.id)
        }:
            self.category_choice.options.append(
                ft.DropdownOption(
                    key=self.category_choice.value,
                    text=self.tr("choose_category"),
                )
            )
        self._refresh_labels()
        self._build_home_layout()

    def _category_label(self, identifier: str, name: str) -> str:
        depth = identifier.count(":") - 1
        return f"{'  ' * depth}{name}"

    def _entity_card(self, item: Account | Category, balance: Decimal | None = None) -> ft.Control:
        category = isinstance(item, Category)
        kind = "category" if category else "account"
        lines: list[ft.Control] = [
            ft.Text(self._category_label(item.id, item.name) if category else item.name, size=14),
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
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        tooltip=self.tr("crud_edit"),
                        data=(kind, item.id),
                        on_click=self._edit_entity,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        tooltip=self.tr("crud_delete"),
                        data=(kind, item.id),
                        on_click=self._delete_entity,
                    ),
                ],
                spacing=0,
            ),
        )

    def _transaction_card(
        self,
        transaction: Transaction,
        names: dict[str, str],
        compact: bool = False,
    ) -> ft.Control:
        kind_key = {
            "opening": "kind_opening",
            "expense": "kind_expense",
        }.get(transaction.kind, "kind_external")
        display = (
            self.tr(kind_key)
            if transaction.kind == "opening"
            else transaction.narration or self.tr(kind_key)
        )
        outgoing = [p for p in transaction.postings if p.amount < 0]
        incoming = [p for p in transaction.postings if p.amount > 0]
        if (
            transaction.kind == "opening"
            and len(transaction.postings) == 2
            and all(p.amount == 0 for p in transaction.postings)
        ):
            outgoing = [p for p in transaction.postings if p.account.startswith("Equity:")]
            incoming = [p for p in transaction.postings if p.account.startswith("Assets:")]
        simple = (
            len(transaction.postings) == 2
            and len(outgoing) == len(incoming) == 1
            and outgoing[0].currency == incoming[0].currency
            and -outgoing[0].amount == incoming[0].amount
        )
        title = display
        if simple:
            source = names.get(outgoing[0].account, self.tr("kind_opening"))
            target = names.get(incoming[0].account, self.tr("kind_external"))
            title = (
                f"{self.tr('kind_opening')} → {target}"
                if transaction.kind == "opening"
                else f"{source} → {target}"
            )
        heading: list[ft.Control] = [ft.Text(title, size=14, expand=True)]
        if simple:
            heading.append(
                ft.Text(
                    format_amount(incoming[0].amount, self.language, incoming[0].currency),
                    size=14,
                )
            )
        else:
            heading.append(ft.Text(self.tr("multiple_postings"), size=12))
        lines: list[ft.Control] = [ft.Row(heading, spacing=8)]
        if transaction.kind != "opening":
            lines.append(ft.Text(format_date(transaction.date, self.language), size=12))
        actions: list[ft.Control] = [
            ft.IconButton(
                icon=ft.Icons.EDIT_OUTLINED,
                tooltip=self.tr("crud_edit"),
                data=transaction.id,
                on_click=self._edit_transaction,
            ),
        ]
        if not compact:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    tooltip=self.tr("crud_delete"),
                    data=transaction.id,
                    on_click=self._delete_transaction,
                )
            )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=4),
            content=ft.Row(
                [ft.Column(lines, spacing=2, expand=True), *actions],
                spacing=0,
            ),
        )

    async def _lifecycle(self) -> None:
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
        try:
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
            await self._sync_system_zone()
            if self.expense_when.pristine:
                self._reset_expense_clock()
            else:
                self.expense_when.set_zone(self._effective_zone())
            saved_folder = await self.preferences.get(FOLDER_KEY)
            if isinstance(saved_folder, str):
                async with self.lock:
                    await self._attach(saved_folder)
        except Exception as exc:
            self._set_error(exc)
        self._refresh()
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
            if self.lock.locked() or self.journal is None or not self.journal.ready:
                continue
            async with self.lock:
                try:
                    if await self.journal.poll():
                        self._set_message("external_reloaded")
                        self._refresh()
                except Exception as exc:
                    self.journal.ready = False
                    self.journal.storage.loaded = False
                    self._set_error(exc)
                    self._refresh()
                self.page.update()

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
            self.expense_when.set_device_zone(None)
            raise LedgerError("crud_timezone_unavailable") from exc
        if name != self.system_zone:
            self.system_zone = name
            if self.timezone_name == "system" and self.expense_when.pristine:
                self._reset_expense_clock()
            elif self.timezone_name == "system":
                self.expense_when.set_zone(name)
            self.expense_when.set_device_zone(name)
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
            # Validate the draft before changing either the preference or its interpretation.
            self.expense_when.values()
            if name == "system" and self.bridge is not None:
                result = unwrap(await self.bridge.call("timezone"))
                if not isinstance(result, str):
                    raise LedgerError("crud_timezone_unavailable")
                named_zone(result)
                self.system_zone = result
                self.expense_when.set_device_zone(result)
            zone = self.system_zone if name == "system" else name
            if not await self.preferences.set(TIMEZONE_KEY, name):
                raise LedgerError("crud_timezone_save")
            self.expense_when.set_zone(zone)
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
            self.page.show_dialog(
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
                )
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
        await self.journal.load()
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
                self.expense_save,
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
            await self._attach(selected)
            if not await self.preferences.set(FOLDER_KEY, selected):
                raise StorageError("Folder loaded, but could not remember it for restart.")

        await self._action(select)

    async def _reload(self, event: ft.Event[ft.Button]) -> None:
        async def reload() -> None:
            if self.journal is None:
                raise LedgerError("Select a folder first.")
            await self.journal.load()
            self._set_message("reloaded")

        await self._action(reload)

    def _require_journal(self) -> Journal:
        if self.journal is None:
            raise LedgerError("Select a folder first.")
        return self.journal

    def _saved(self, backup: str | None) -> None:
        self._set_message("saved_backup" if backup else "saved_new")

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
    ) -> None:
        cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)
        submit = ft.TextButton(self.tr("crud_delete" if deleting else "crud_save"))

        async def commit(event: ft.Event[ft.TextButton]) -> None:
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
                for control in controls:
                    control.disabled = False
                self.page.update()

        submit.on_click = commit
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                scrollable=True,
                title=title,
                content=ft.Column(controls, tight=True, spacing=12, width=360),
                actions=[cancel, submit],
            )
        )

    def _opening_details(self, transaction: Transaction) -> ft.ExpansionTile:
        return ft.ExpansionTile(
            title=self.tr("details"),
            controls=[
                ft.Text(transaction.id, selectable=True),
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
            label=self.tr("amount"), value=str(journal.opening_balance(account.id))
        )
        source = journal.source

        async def save() -> str | None:
            return await journal.edit_account(
                account.id, account.name, amount.value or "0", account.active, source
            )

        self._edit_dialog(
            self.tr("kind_opening"),
            [
                ft.Text(f"{account.name} ({account.currency})"),
                amount,
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
                transaction = journal.expense_for_edit(identifier)
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
                self.page.show_dialog(
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
                                        ft.Text(transaction.id, selectable=True),
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
                                    ft.Text(
                                        f"{names.get(p.account, p.account)}: "
                                        f"{format_amount(p.amount, self.language, p.currency)}"
                                    )
                                    for p in transaction.postings
                                ],
                            ],
                            tight=True,
                        ),
                        actions=[ft.TextButton(self.tr("crud_cancel"), on_click=self._dismiss)],
                    )
                )
                return
            source = journal.source
            asset = next(p for p in transaction.postings if p.amount < 0)
            expense = next(p for p in transaction.postings if p.amount > 0)
            account = ft.Dropdown(
                label=self.tr("pay_from"),
                value=asset.account,
                options=[
                    ft.DropdownOption(key=a.id, text=f"{a.name} ({a.currency})")
                    for a in journal.ledger.accounts
                    if a.active or a.id == asset.account
                ],
            )
            category = ft.Dropdown(
                label=self.tr("expense_category"),
                value=expense.account,
                options=[
                    ft.DropdownOption(key=c.id, text=self._category_label(c.id, c.name))
                    for c in journal.ledger.categories
                    if journal.ledger.category_active(c.id) or c.id == expense.account
                ],
            )
            amount = ft.TextField(label=self.tr("amount"), value=str(expense.amount))
            payee = ft.TextField(label=self.tr("payee"), value=transaction.payee)
            when = DateTimeFields(
                self.language,
                self._effective_zone(),
                transaction.date,
                transaction.timestamp,
                device_zone=self._picker_zone(),
            )
            description = ft.TextField(
                label=self.tr("crud_description_label"),
                value=transaction.narration,
                multiline=True,
                min_lines=2,
                max_lines=5,
                text_size=17,
            )

            async def save() -> str | None:
                occurred, timestamp = when.values()
                return await journal.edit_expense(
                    transaction.id,
                    account.value or "",
                    category.value or "",
                    amount.value or "",
                    payee.value or "",
                    occurred,
                    timestamp,
                    description.value or "",
                    source,
                )

            self._edit_dialog(
                self.tr("crud_edit"),
                [
                    ft.Text(transaction.id, selectable=True),
                    account,
                    category,
                    amount,
                    description,
                    when,
                    ft.ExpansionTile(
                        title=self.tr("payee"),
                        controls=[payee, ft.Text(self.tr("payee_help"), size=12)],
                        maintain_state=True,
                        expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
                    ),
                ],
                journal,
                save,
            )
        except Exception as exc:
            self._error(exc, notify=True)

    def _delete_transaction(self, event: ft.Event[ft.IconButton]) -> None:
        try:
            journal = self._require_journal()
            transaction = journal.expense_for_edit(str(event.control.data))
            source = journal.source

            async def remove() -> str | None:
                return await journal.delete_expense(transaction.id, source)

            self._edit_dialog(
                self.tr("crud_confirm"),
                [ft.Text(self.tr("crud_confirm_body")), ft.Text(transaction.id, selectable=True)],
                journal,
                remove,
                deleting=True,
            )
        except Exception as exc:
            self._error(exc, notify=True)

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
                        label=self.tr("opening_balance"),
                        value=str(journal.opening_balance(identifier)),
                    )
                except LedgerError as exc:
                    if str(exc) != "crud_opening_complex":
                        raise
            controls: list[ft.Control] = [
                ft.Text(identifier, selectable=True),
                ft.Text(self.tr("crud_category_help" if category else "crud_account_help")),
                name,
                active,
            ]
            if isinstance(item, Account):
                controls.append(ft.Text(item.currency))
                controls.append(
                    opening if opening is not None else ft.Text(self.tr("crud_opening_complex"))
                )

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
                )

            self._edit_dialog(self.tr("crud_edit"), controls, journal, save)
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
                self._edit_entity(event)
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

    async def _create_account(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            await self._require_journal().create_account(
                self.account_id.value or "",
                self.account_name.value or "",
                self.currency.value or "",
                self.opening.value or "",
            )
            self.account_id.value = self.account_name.value = self.opening.value = ""
            self._set_message("account_created")

        await self._action(save)

    async def _create_category(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            await self._require_journal().create_category(
                self.category_id.value or "",
                self.category_name.value or "",
            )
            self.category_id.value = self.category_name.value = ""
            self._set_message("category_created")

        await self._action(save)

    async def _add_expense(self, event: ft.Event[ft.Button]) -> None:
        async def save() -> None:
            self._effective_zone()
            occurred, timestamp = self.expense_when.values()
            await self._require_journal().add_expense(
                self.account_choice.value or "",
                self.category_choice.value or "",
                self.amount.value or "",
                self.payee.value or "",
                occurred,
                self.description.value or "",
                timestamp,
            )
            self.amount.value = ""
            self.payee.value = ""
            self.payee_details.expanded = False
            self.description.value = ""
            self._reset_expense_clock()
            self._set_message("expense_saved")

        await self._action(save)

    def _navigate(self, screen: str) -> None:
        self.current_screen = screen
        self._refresh_labels()
        self._apply_layout((self.page.width or self.page.window.width) if self.page else 390)
        if self.page:
            self.page.update()

    def _bar_changed(self, event: ft.Event[ft.NavigationBar]) -> None:
        screens = ("home", "transactions", "accounts", "settings")
        self._navigate(screens[event.control.selected_index])

    def _rail_changed(self, event: ft.Event[ft.NavigationRail]) -> None:
        if event.control.selected_index is not None:
            screens = ("home", "transactions", "accounts", "settings")
            self._navigate(screens[event.control.selected_index])

    def _go_home(self, event: ft.Event[ft.TextButton]) -> None:
        self._navigate("home")

    def _go_accounts(self, event: ft.Event[ft.TextButton]) -> None:
        self._navigate("accounts")

    def _expense(self, event: ft.Event[ft.FloatingActionButton]) -> None:
        try:
            if (
                not (self.amount.value or self.description.value)
                and not self.payee.value
                and self.expense_when.pristine
            ):
                self._reset_expense_clock()
            self._navigate("expense")
        except Exception as exc:
            self._error(exc, notify=True)

    def _reset_expense_clock(self) -> None:
        old = self.expense_when
        self.expense_when = DateTimeFields(
            self.language,
            self._effective_zone(),
            device_zone=self._picker_zone(),
        )
        self.expense_date = self.expense_when.occurred
        self.forms[self.forms.index(old)] = self.expense_when

    def _picker_zone(self) -> str | None:
        if self.bridge is not None and self.system_zone == "system":
            return None
        return self.system_zone

    def _today(self) -> date:
        return local_datetime(datetime.now().astimezone(), self._effective_zone()).date()

    def _diagnostics(self, event: ft.Event[ft.TextButton]) -> None:
        if not self.diagnostics.controls:
            from app.ui.beancount import BeancountPocView
            from app.ui.home import StorageView

            self.diagnostics.controls.extend([BeancountPocView(), StorageView()])
        self.diagnostics_visible = not self.diagnostics_visible
        self.diagnostics.visible = self.diagnostics_visible
        self._refresh_labels()
        self.page.update()
