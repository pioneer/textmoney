import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.device_info import DeviceInfo

from app.domain.ledger import Transaction
from app.infrastructure.preferences import DesktopPreferences
from app.localization import (
    LANGUAGES,
    catalog_keys,
    format_amount,
    format_date,
    localized_error,
    system_language,
    translate,
)
from app.ui.finance import (
    COMPACT_MAX_WIDTH,
    MEDIUM_MAX_WIDTH,
    FinanceView,
    ScreenClass,
    screen_class,
)


def test_all_locales_have_a_nonempty_translation_for_each_ui_key() -> None:
    keys = catalog_keys()
    assert set(keys) == set(LANGUAGES)
    assert len({keys[language] for language in LANGUAGES}) == 1
    for language in LANGUAGES:
        for key in keys[language]:
            assert translate(
                language,
                key,
                currency="UAH",
                zone="Europe/Kyiv",
                identifier="synthetic-id",
                debited="105 USD",
                credited="4200 UAH",
                principal="100 USD",
                gross="4200 UAH",
                rate="1 USD = 42 UAH",
                count=3,
                size=1024,
            ).strip()


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["uk-UA"], "uk"),
        (["uk"], "uk"),
        (["ru-RU"], "ru"),
        (["ru"], "ru"),
        (["en-GB"], "en"),
        (["de-DE"], "en"),
        ([], "en"),
    ],
)
def test_system_language_selection(tags: list[str], expected: str) -> None:
    assert system_language(tags) == expected


def test_locale_sensitive_formatting_does_not_change_decimal_value() -> None:
    amount = Decimal("1234.50")
    assert format_amount(amount, "en", "UAH") == "1,234.50 UAH"
    assert format_amount(amount, "uk", "UAH") == "1\u202f234,50 UAH"
    assert format_amount(Decimal("-0.25"), "ru", "EUR") == "−0,25 EUR"
    assert format_date(date(2026, 10, 18), "uk") == "18 жовт. 2026"
    assert format_date(date(2026, 10, 18), "ru") == "18 окт. 2026"
    assert format_date(date(2026, 10, 18), "en") == "Oct 18, 2026"
    assert amount == Decimal("1234.50")


@pytest.mark.parametrize(
    ("language", "label"), [("en", "Accounting"), ("uk", "Облік"), ("ru", "Учёт")]
)
def test_accounting_navigation_label(language: str, label: str) -> None:
    assert translate(language, "nav_accounts") == label


def test_known_accounting_errors_are_localized() -> None:
    message = "Insufficient funds: an ordinary account would become negative."
    assert localized_error("en", message) == (
        "There is not enough money in this account for that expense."
    )
    assert localized_error("uk", message) == "На рахунку недостатньо коштів для цієї витрати."
    assert localized_error("ru", message) == "На счёте недостаточно средств для этого расхода."


def test_screen_class_breakpoints_cover_requested_widths() -> None:
    assert screen_class(390) == ScreenClass.COMPACT
    assert screen_class(800) == ScreenClass.MEDIUM
    assert screen_class(1366) == ScreenClass.EXPANDED
    assert screen_class(COMPACT_MAX_WIDTH) == ScreenClass.COMPACT
    assert screen_class(COMPACT_MAX_WIDTH + 1) == ScreenClass.MEDIUM
    assert screen_class(MEDIUM_MAX_WIDTH) == ScreenClass.MEDIUM
    assert screen_class(MEDIUM_MAX_WIDTH + 1) == ScreenClass.EXPANDED


def test_navigation_has_consistent_destinations_and_adapts_on_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = FinanceView()
    view.startup_complete = True
    page = MagicMock(spec=ft.Page)
    page.width = 390
    monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
    view.update = MagicMock()
    view._apply_layout(390)
    assert [item.label for item in view.navigation_bar.destinations] == [
        "Home",
        "Transactions",
        "Accounting",
        "Settings",
    ]
    assert len(view.navigation_bar.destinations) == len(view.nav_rail.destinations) == 4
    assert view.screen_class == ScreenClass.COMPACT
    assert page.navigation_bar is view.navigation_bar

    view._apply_layout(800)
    assert view.screen_class == ScreenClass.MEDIUM
    assert not view.nav_rail.extended
    assert isinstance(view.controls[0], ft.Container)
    assert view.controls[0].content is view.nav_rail
    assert page.navigation_bar is None

    view._apply_layout(1366)
    assert view.screen_class == ScreenClass.EXPANDED
    assert view.nav_rail.extended


def test_manual_language_switch_is_immediate_and_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.update = MagicMock()
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=True)
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        event = MagicMock()
        event.control.value = "uk"
        await view._language_changed(event)
        assert view.language == "uk"
        assert view.manual_language
        assert view.account_save.content == "Створити рахунок"
        view.preferences.set.assert_awaited_once_with("txncount.language.v1", "uk")
        assert page.locale_configuration.current_locale.language_code == "uk"

    import asyncio

    asyncio.run(scenario())


def test_system_language_option_tracks_device_even_after_manual_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        page.locale_configuration = ft.LocaleConfiguration()
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=True)
        assert view.language_choice.value == "system"
        assert [option.key for option in view.language_choice.options] == [
            "system",
            "en",
            "uk",
            "ru",
        ]
        locale_event = MagicMock()
        locale_event.locales = [ft.Locale(language_code="uk", country_code="UA")]
        view._host_locale_changed(locale_event)
        assert view.language == "uk"
        assert view.language_choice.value == "system"
        assert view.language_choice.options[0].text == "Системна"
        event = MagicMock()
        event.control.value = "en"
        await view._language_changed(event)
        locale_event.locales = [ft.Locale(language_code="ru")]
        view._host_locale_changed(locale_event)
        assert view.language == "en"
        assert view.language_choice.value == "en"
        event.control.value = "system"
        await view._language_changed(event)
        assert view.language == "ru"
        assert not view.manual_language
        assert view.language_choice.value == "system"
        assert page.locale_configuration.current_locale == ft.Locale(language_code="ru")
        view.preferences.set.assert_awaited_with("txncount.language.v1", "system")
        locale_event.locales = [ft.Locale(language_code="de")]
        view._host_locale_changed(locale_event)
        assert view.language == "en"
        assert view.language_choice.value == "system"
        assert view.language_choice.options[0].text == "System"

    asyncio.run(scenario())


@pytest.mark.parametrize("saved", [None, "system", "en", "uk", "ru"])
def test_language_mode_restores_on_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved: str | None,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        if saved is not None:
            await DesktopPreferences().set("txncount.language.v1", saved)
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.width = 390
        page.locale_configuration = ft.LocaleConfiguration(
            current_locale=ft.Locale(language_code="en"),
        )
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(locales=[ft.Locale(language_code="uk", country_code="UA")])
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        manual = saved in LANGUAGES
        assert view.manual_language == manual
        assert view.language == (saved if manual else "uk")
        assert view.language_choice.value == (saved if manual else "system")
        assert view.system_language_name == "uk"
        assert page.locale_configuration.current_locale == ft.Locale(
            language_code=saved if saved in LANGUAGES and saved is not None else "uk"
        )
        page.get_device_info.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "existing_new_key,save_fails", [(False, False), (True, False), (False, True)]
)
def test_legacy_folder_preference_is_migrated_without_overriding_current_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_new_key: bool,
    save_fails: bool,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        preferences = DesktopPreferences()
        await preferences.set("financepoc.folder.v1", "/synthetic/legacy")
        if existing_new_key:
            await preferences.set("txncount.folder.v1", "/synthetic/current")
        if save_fails:
            monkeypatch.setattr(DesktopPreferences, "set", AsyncMock(return_value=False))
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.width = 390
        page.locale_configuration = ft.LocaleConfiguration()
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(locales=[ft.Locale(language_code="uk")])
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view = FinanceView()
        view._attach = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        if save_fails:
            view._attach.assert_not_awaited()
            assert view.error_message == "The selected folder could not be saved. Select it again."
            assert await preferences.get("txncount.folder.v1") is None
            return
        selected = "/synthetic/current" if existing_new_key else "/synthetic/legacy"
        view._attach.assert_awaited_once_with(selected)
        assert await preferences.get("txncount.folder.v1") == selected
        assert await preferences.get("financepoc.folder.v1") == "/synthetic/legacy"

    asyncio.run(scenario())


def test_missing_device_language_info_is_reported_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.width = 390
        page.get_device_info = AsyncMock(return_value=None)
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        assert view.error_message == "error_system_language"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tags, expected",
    [
        (["uk-UA", "en-US"], "uk"),
        (["ru-RU", "uk-UA"], "ru"),
        (["de-DE"], "en"),
        ([], "en"),
    ],
)
def test_startup_system_language_uses_client_locale_order_not_app_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tags: list[str],
    expected: str,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.width = 390
        page.locale_configuration = ft.LocaleConfiguration()
        page.get_device_info = AsyncMock(
            return_value=DeviceInfo(
                locales=[ft.Locale(language_code=tag.split("-")[0]) for tag in tags]
            )
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view = FinanceView()
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        assert view.language == expected
        assert view.language_choice.value == "system"
        assert page.locale_configuration.current_locale == ft.Locale(language_code=expected)
        assert not view.error_message

    asyncio.run(scenario())


@pytest.mark.parametrize("language", LANGUAGES)
def test_storage_explanation_names_beancount_format(language: str) -> None:
    assert "Beancount" in translate(language, "storage_explanation")


def test_page_owned_controls_receive_language_theme_and_transaction_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
        view = FinanceView()
        view.update = MagicMock()
        view.startup_complete = True
        view._apply_layout(390)
        view.preferences = MagicMock()
        view.preferences.set = AsyncMock(return_value=True)

        event = MagicMock()
        event.control = view.language_choice
        event.control.value = "uk"
        await view._language_changed(event)
        assert page.navigation_bar.destinations[0].label == "Головна"
        assert page.floating_action_button.content == "Додати операцію"
        page.update.assert_called()

        page.update.reset_mock()
        event.control = view.theme_choice
        event.control.value = "dark"
        await view._theme_changed(event)
        assert page.theme_mode == ft.ThemeMode.DARK
        page.update.assert_called()

        # The same page-owned button must be patched when journal readiness changes.
        view.journal = MagicMock()
        view.journal.ready = True
        view._refresh()
        view._navigate("accounts")
        assert not view.fab.disabled
        page.update.assert_called()
        view._new_transaction(MagicMock())
        assert view.current_screen == "accounts"
        assert page.show_dialog.call_args.args[0].title == "Додати операцію"

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["uk", "ru"])
def test_opening_transaction_label_is_localized_without_rewriting_source(language: str) -> None:
    view = FinanceView()
    view.language = language
    transaction = Transaction(
        id="synthetic-opening",
        date=date(2026, 1, 1),
        timestamp="2026-01-01T00:00:00+00:00",
        payee="",
        narration="Opening balance",
        postings=(),
        kind="opening",
    )
    card = view._transaction_card(transaction, {})
    assert isinstance(card, ft.Container)
    assert isinstance(card.content, ft.Row)
    body = card.content.controls[0]
    assert isinstance(body, ft.Column)
    heading = body.controls[0]
    assert isinstance(heading, ft.Row)
    title = heading.controls[0]
    assert isinstance(title, ft.Text)
    assert translate(language, "kind_opening") in title.value
    assert transaction.narration == "Opening balance"
