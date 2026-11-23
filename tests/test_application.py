import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.messaging.session import Session

from app.application import WINDOW_STATE_KEY, _restore_desktop_window, main, report_page_error
from app.domain import readiness_message
from app.infrastructure.preferences import DesktopPreferences
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
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    platform: ft.PagePlatform,
    mobile_preview: bool,
    desktop: bool,
) -> None:
    monkeypatch.setattr(
        "app.application.DesktopPreferences",
        lambda: DesktopPreferences(tmp_path / "preferences.json"),
    )
    page = MagicMock(spec=ft.Page)
    page.platform = platform
    page.web = False
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
    page.web = False
    page.window = MagicMock(spec=ft.Window)
    monkeypatch.setenv("APP_MOBILE_PREVIEW", "1")
    monkeypatch.setattr(entrypoint, "configure_logging", MagicMock())
    monkeypatch.setattr(entrypoint, "install_exception_hooks", MagicMock())
    launch = MagicMock()
    monkeypatch.setattr(ft, "run", launch)

    entrypoint.run()
    asyncio.run(launch.call_args.args[0](page))

    assert launch.call_args.kwargs["view"] == ft.AppView.FLET_APP_HIDDEN
    assert page.window.width == 390
    assert page.platform == ft.PagePlatform.LINUX


def test_desktop_window_state_is_restored_and_remembered(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        saved = {"width": 1040, "height": 720, "mode": "normal"}
        await preferences.set(WINDOW_STATE_KEY, json.dumps(saved))
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)

        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.web = False
        page.window = MagicMock(spec=ft.Window)
        page.window.maximized = False
        page.window.full_screen = False

        async def ready_to_show() -> None:
            assert page.window.visible is False
            assert page.window.width == 1040
            assert page.window.height == 720
            page.add.assert_called_once()

        page.window.wait_until_ready_to_show = AsyncMock(side_effect=ready_to_show)
        await main(page)

        assert page.window.width == 1040
        assert page.window.height == 720
        assert page.window.visible is True
        page.window.wait_until_ready_to_show.assert_awaited_once()
        page.window.update.assert_called_once()
        page.window.to_front.assert_awaited_once()
        assert page.window.on_event is not None

        page.window.width = 1280
        page.window.height = 800

        async def dispatch(event_type: ft.WindowEventType) -> None:
            event = MagicMock(spec=ft.WindowEvent)
            event.type = event_type
            await page.window.on_event(event)

        await dispatch(ft.WindowEventType.RESIZED)
        value = await preferences.get(WINDOW_STATE_KEY)
        assert value is not None
        saved = json.loads(value)
        assert saved == {"width": 1280.0, "height": 800.0, "mode": "normal"}

        page.window.maximized = True
        await dispatch(ft.WindowEventType.MAXIMIZE)
        value = await preferences.get(WINDOW_STATE_KEY)
        assert value is not None
        saved = json.loads(value)
        assert saved == {"width": 1280.0, "height": 800.0, "mode": "maximized"}

        reopened = MagicMock(spec=ft.Page)
        reopened.platform = ft.PagePlatform.LINUX
        reopened.web = False
        reopened.window = MagicMock(spec=ft.Window)
        await main(reopened)
        assert reopened.window.width == 1280
        assert reopened.window.height == 800
        assert reopened.window.maximized is True

        event = MagicMock(spec=ft.WindowEvent)
        page.window.full_screen = True
        event.type = ft.WindowEventType.ENTER_FULL_SCREEN
        await page.window.on_event(event)
        value = await preferences.get(WINDOW_STATE_KEY)
        assert value is not None
        saved = json.loads(value)
        assert saved == {"width": 1280.0, "height": 800.0, "mode": "fullscreen"}

        fullscreen = MagicMock(spec=ft.Page)
        fullscreen.platform = ft.PagePlatform.LINUX
        fullscreen.web = False
        fullscreen.window = MagicMock(spec=ft.Window)
        await main(fullscreen)
        assert fullscreen.window.width == 1280
        assert fullscreen.window.height == 800
        assert fullscreen.window.full_screen is True

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["maximized", "fullscreen"])
def test_desktop_window_mode_is_sent_with_reveal(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        await preferences.set(
            WINDOW_STATE_KEY, json.dumps({"width": 1040, "height": 720, "mode": mode})
        )
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.web = False
        page.window = MagicMock(spec=ft.Window)
        page.window.maximized = False
        page.window.full_screen = False

        async def ready_to_show() -> None:
            assert page.window.visible is False
            assert page.window.maximized is False
            assert page.window.full_screen is False

        def reveal() -> None:
            assert page.window.visible is True
            assert page.window.maximized is (mode == "maximized")
            assert page.window.full_screen is (mode == "fullscreen")

        page.window.wait_until_ready_to_show = AsyncMock(side_effect=ready_to_show)
        page.window.update.side_effect = reveal
        await main(page)
        page.window.update.assert_called_once()

    asyncio.run(scenario())


def test_invalid_saved_desktop_window_state_is_ignored(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        await preferences.set(WINDOW_STATE_KEY, '{"width":-1,"height":600,"mode":"normal"}')
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)

        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.web = False
        page.window = MagicMock(spec=ft.Window)
        page.window.width = 800
        page.window.height = 600
        await main(page)

        assert page.window.width == 800
        assert page.window.height == 600
        assert page.window.on_event is not None

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "Could not restore desktop window state" in caplog.text


def test_close_does_not_replace_newer_resized_window_size(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)
        await preferences.set(WINDOW_STATE_KEY, '{"width":800,"height":600,"mode":"normal"}')

        previous_size = (800, 600)
        for width, height in ((960, 680), (1100, 750)):
            session = Session(MagicMock())
            session.get_page_patch()
            page = session.page
            await _restore_desktop_window(page)
            original_size = (page.window.width, page.window.height)
            assert original_size == previous_size

            session.apply_patch(page.window._i, {"width": width, "height": height})
            await session.dispatch_event(page.window._i, "event", {"type": "resized"})
            session.apply_patch(
                page.window._i, {"width": original_size[0], "height": original_size[1]}
            )
            await session.dispatch_event(page.window._i, "event", {"type": "close"})

            saved = await preferences.get(WINDOW_STATE_KEY)
            assert saved is not None
            assert json.loads(saved) == {"width": width, "height": height, "mode": "normal"}
            previous_size = (width, height)

    asyncio.run(scenario())


@pytest.mark.parametrize("event_type", ["unmaximize", "maximize"])
def test_fullscreen_survives_maximize_events(
    tmp_path, monkeypatch: pytest.MonkeyPatch, event_type: str
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)
        await preferences.set(WINDOW_STATE_KEY, '{"width":800,"height":600,"mode":"maximized"}')

        session = Session(MagicMock())
        session.get_page_patch()
        page = session.page
        await _restore_desktop_window(page)
        session.apply_patch(page.window._i, {"full_screen": True, "maximized": False})
        await session.dispatch_event(page.window._i, "event", {"type": "enter-full-screen"})
        await session.dispatch_event(page.window._i, "event", {"type": event_type})

        saved = await preferences.get(WINDOW_STATE_KEY)
        assert saved is not None
        assert json.loads(saved) == {"width": 800, "height": 600, "mode": "fullscreen"}

        reopened = Session(MagicMock())
        reopened.get_page_patch()
        state = await _restore_desktop_window(reopened.page)
        assert state.mode == "fullscreen"

    asyncio.run(scenario())


def test_unmaximize_event_preserves_a_maximized_window(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)
        await preferences.set(WINDOW_STATE_KEY, '{"width":800,"height":600,"mode":"maximized"}')

        session = Session(MagicMock())
        session.get_page_patch()
        await _restore_desktop_window(session.page)
        session.apply_patch(session.page.window._i, {"maximized": True, "full_screen": False})
        await session.dispatch_event(session.page.window._i, "event", {"type": "unmaximize"})

        saved = await preferences.get(WINDOW_STATE_KEY)
        assert saved is not None
        assert json.loads(saved)["mode"] == "maximized"

        reopened = Session(MagicMock())
        reopened.get_page_patch()
        state = await _restore_desktop_window(reopened.page)
        assert state.mode == "maximized"

    asyncio.run(scenario())


def test_resize_before_mode_event_does_not_save_fullscreen_dimensions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        preferences = DesktopPreferences(tmp_path / "preferences.json")
        monkeypatch.setattr("app.application.DesktopPreferences", lambda: preferences)
        await preferences.set(WINDOW_STATE_KEY, '{"width":800,"height":600,"mode":"normal"}')

        session = Session(MagicMock())
        session.get_page_patch()
        await _restore_desktop_window(session.page)
        session.apply_patch(
            session.page.window._i, {"width": 1920, "height": 1080, "full_screen": True}
        )
        await session.dispatch_event(session.page.window._i, "event", {"type": "resized"})
        await session.dispatch_event(session.page.window._i, "event", {"type": "enter-full-screen"})

        saved = await preferences.get(WINDOW_STATE_KEY)
        assert saved is not None
        assert json.loads(saved) == {"width": 800, "height": 600, "mode": "fullscreen"}

    asyncio.run(scenario())


def test_desktop_browser_does_not_use_native_window_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            "app.application.DesktopPreferences",
            MagicMock(side_effect=AssertionError("browser must not read desktop settings")),
        )
        page = MagicMock(spec=ft.Page)
        page.platform = ft.PagePlatform.LINUX
        page.web = True
        page.window = MagicMock(spec=ft.Window)
        page.window.visible = True
        page.window.on_event = None

        await main(page)

        assert page.window.visible is True
        assert page.window.on_event is None
        page.window.wait_until_ready_to_show.assert_not_awaited()
        page.window.update.assert_not_called()
        page.window.to_front.assert_not_awaited()

    asyncio.run(scenario())


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
