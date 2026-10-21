import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.device_info import DeviceInfo

from app.infrastructure.preferences import DesktopPreferences
from app.ui.finance import FinanceView


@pytest.mark.parametrize("saved,expected", [(None, "uk"), ("ru", "ru"), ("en", "en")])
@pytest.mark.parametrize("width", [390, 1366])
def test_startup_shows_only_spinner_until_preferences_and_language_are_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved: str | None,
    expected: str,
    width: int,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        if saved:
            await DesktopPreferences().set("txncount.language.v1", saved)
        page = MagicMock(spec=ft.Page)
        page.width = width
        page.platform = ft.PagePlatform.LINUX
        page.locale_configuration = ft.LocaleConfiguration()
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        assert view.controls == [view.startup_spinner]
        assert isinstance(view.startup_spinner.content, ft.ProgressRing)

        def check_loading() -> None:
            assert not view.startup_complete
            assert view.controls == [view.startup_spinner]
            assert page.navigation_bar is None
            assert page.floating_action_button is None

        async def device() -> DeviceInfo:
            check_loading()
            view._apply_layout(width)
            check_loading()
            return DeviceInfo(locales=[ft.Locale(language_code="uk")])

        async def timezone() -> None:
            check_loading()
            assert view.language == expected
            view._refresh()
            page.update()
            check_loading()

        page.get_device_info = AsyncMock(side_effect=device)
        view._sync_system_zone = timezone
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        view._apply_layout(width)
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        assert view.startup_complete
        assert view.startup_spinner not in view.controls
        assert view.language == expected
        assert page.locale_configuration.current_locale == ft.Locale(language_code=expected)
        assert view.navigation_bar.destinations[0].label == view.tr("nav_home")
        assert page.floating_action_button is view.fab
        if width == 390:
            assert page.navigation_bar is view.navigation_bar
        else:
            assert page.navigation_bar is None
            assert view.body in view.controls

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["device", "preferences", "folder"])
def test_startup_failure_exits_spinner_and_displays_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        await DesktopPreferences().set("txncount.language.v1", "uk")
        page = MagicMock(spec=ft.Page)
        page.width = 390
        page.platform = ft.PagePlatform.LINUX
        page.locale_configuration = ft.LocaleConfiguration()
        page.get_device_info = AsyncMock(
            return_value=None
            if failure == "device"
            else DeviceInfo(locales=[ft.Locale(language_code="uk")])
        )
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        if failure == "preferences":
            monkeypatch.setattr(
                DesktopPreferences, "get", AsyncMock(side_effect=OSError("synthetic failure"))
            )
        elif failure == "folder":
            await DesktopPreferences().set("txncount.folder.v1", "/synthetic/missing")
        view = FinanceView()
        if failure == "folder":
            view._attach = AsyncMock(side_effect=OSError("synthetic folder failure"))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await view._lifecycle()
        assert view.startup_complete and view.startup_spinner not in view.controls
        assert view.body in view.controls
        assert view.error_message or view.message_key == "error_unexpected"
        assert view.message.value
        if failure == "folder":
            assert view.language == "uk"
            assert view.message.value == view.tr("error_unexpected")

    asyncio.run(scenario())
