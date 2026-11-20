import asyncio
import logging
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.messaging.session import Session

from app.application import main, report_page_error
from app.domain import readiness_message
from app.ui.home import home_view
from app.ui.theme import dark_theme


def test_import_and_view_without_gui() -> None:
    import main as entrypoint

    assert callable(entrypoint.run)
    assert isinstance(home_view(), ft.SafeArea)
    assert readiness_message(ready=True) == "Ready"
    assert readiness_message(ready=False) == "Not ready"


def test_page_startup() -> None:
    page = MagicMock(spec=ft.Page)
    asyncio.run(main(page))
    assert page.title == "TextMoney"
    assert page.on_error is report_page_error
    assert page.theme_mode == ft.ThemeMode.SYSTEM
    assert page.theme.scaffold_bgcolor == "#ffffff"
    assert page.theme.color_scheme.surface == "#ffffff"
    assert page.theme.floating_action_button_theme.bgcolor == ft.Colors.PRIMARY
    assert page.dark_theme == dark_theme()
    assert [locale.language_code for locale in page.locale_configuration.supported_locales] == [
        "en",
        "uk",
        "ru",
    ]
    page.add.assert_called_once()
    assert page.add.call_args.args[0].expand


@pytest.mark.parametrize(
    "platform,mobile_preview,desktop",
    [
        (ft.PagePlatform.LINUX, False, True),
        (ft.PagePlatform.WINDOWS, False, True),
        (ft.PagePlatform.MACOS, False, True),
        (ft.PagePlatform.ANDROID, False, False),
        (ft.PagePlatform.IOS, False, False),
        (ft.PagePlatform.LINUX, True, False),
    ],
)
def test_pointer_theme_is_scoped_to_desktop(
    platform: ft.PagePlatform, mobile_preview: bool, desktop: bool
) -> None:
    page = MagicMock(spec=ft.Page)
    page.platform = platform
    page.window = MagicMock(spec=ft.Window)
    asyncio.run(main(page, mobile_preview=mobile_preview))
    for theme in (page.theme, page.dark_theme):
        if not desktop:
            assert theme.text_button_theme is None
            assert theme.icon_button_theme is None
            assert theme.floating_action_button_theme.hover_color is None
            continue
        expected = {
            ft.ControlState.DISABLED: ft.MouseCursor.BASIC,
            ft.ControlState.DEFAULT: ft.MouseCursor.CLICK,
        }
        for button_theme in (
            theme.button_theme,
            theme.text_button_theme,
            theme.filled_button_theme,
            theme.outlined_button_theme,
            theme.icon_button_theme,
        ):
            assert button_theme.style.mouse_cursor == expected
        assert theme.popup_menu_theme.mouse_cursor == expected
        assert theme.floating_action_button_theme.hover_color is not None
        assert theme.floating_action_button_theme.hover_elevation == 12


def test_mobile_preview_launches_at_phone_width_without_changing_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main as entrypoint

    page = MagicMock(spec=ft.Page)
    page.platform = ft.PagePlatform.LINUX
    page.window = MagicMock(spec=ft.Window)
    monkeypatch.setenv("APP_MOBILE_PREVIEW", "1")
    monkeypatch.setattr(entrypoint, "configure_logging", MagicMock())
    monkeypatch.setattr(entrypoint, "install_exception_hooks", MagicMock())
    launch = MagicMock()
    monkeypatch.setattr(ft, "run", launch)

    entrypoint.run()
    asyncio.run(launch.call_args.args[0](page))

    assert page.window.width == 390
    assert page.platform == ft.PagePlatform.LINUX


def test_flet_event_exception_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        session = Session(MagicMock())

        def fail(event: ft.PageResizeEvent) -> None:
            raise RuntimeError("intentional event failure")

        session.page.on_resize = fail
        await session.dispatch_event(session.page._i, "resize", {"width": 800, "height": 600})

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "intentional event failure" in caplog.text
    assert "Traceback" in caplog.text


def test_client_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        session = Session(MagicMock())
        session.page.on_error = report_page_error
        await session.dispatch_event(session.page._i, "error", "intentional client failure")

    asyncio.run(scenario())
    assert "Unhandled Flet error: intentional client failure" in caplog.text
