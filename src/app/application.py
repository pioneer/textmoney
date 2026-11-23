import asyncio
import json
import logging
import math
from dataclasses import dataclass

import flet as ft

from app.config import APP_NAME
from app.infrastructure.preferences import DesktopPreferences, PreferencesError
from app.logging_config import report_async_error
from app.ui.home import home_view
from app.ui.theme import dark_theme, desktop_pointer, light_theme

logger = logging.getLogger(__name__)
WINDOW_STATE_KEY = "textmoney.desktop_window.v1"


@dataclass
class _WindowState:
    width: float | None = None
    height: float | None = None
    mode: str = "normal"

    @classmethod
    def parse(cls, value: str | None) -> "_WindowState":
        if value is None:
            return cls()
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise PreferencesError("Saved desktop window state is invalid.") from exc
        if not isinstance(data, dict) or set(data) != {"width", "height", "mode"}:
            raise PreferencesError("Saved desktop window state is invalid.")

        width, height, mode = data["width"], data["height"], data["mode"]
        if (
            (width is None) != (height is None)
            or not isinstance(mode, str)
            or mode not in {"normal", "maximized", "fullscreen"}
        ):
            raise PreferencesError("Saved desktop window state is invalid.")
        if width is not None and (
            not _valid_window_dimension(width) or not _valid_window_dimension(height)
        ):
            raise PreferencesError("Saved desktop window state is invalid.")
        return cls(
            width=float(width) if width is not None else None,
            height=float(height) if height is not None else None,
            mode=mode,
        )

    def capture_size(self, window: ft.Window) -> None:
        width = _window_dimension(window.width)
        height = _window_dimension(window.height)
        if width is not None and height is not None:
            self.width = width
            self.height = height

    def serialize(self) -> str:
        return json.dumps(
            {"width": self.width, "height": self.height, "mode": self.mode},
            separators=(",", ":"),
        )


def _valid_window_dimension(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        try:
            return math.isfinite(float(value)) and value > 0
        except OverflowError:
            return False
    if not isinstance(value, float):
        return False
    return math.isfinite(value) and value > 0


def _window_dimension(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if _valid_window_dimension(value):
            return float(value)
    except OverflowError:
        return None
    return None


async def _restore_desktop_window(page: ft.Page) -> _WindowState:
    preferences = DesktopPreferences()
    try:
        state = _WindowState.parse(await preferences.get(WINDOW_STATE_KEY))
    except PreferencesError:
        logger.exception("Could not restore desktop window state")
        state = _WindowState()

    if state.width is not None and state.height is not None:
        page.window.width = state.width
        page.window.height = state.height

    async def on_window_event(event: ft.WindowEvent) -> None:
        if event.type == ft.WindowEventType.RESIZED:
            if state.mode != "normal" or page.window.maximized or page.window.full_screen:
                return
            state.capture_size(page.window)
        elif event.type in {
            ft.WindowEventType.MAXIMIZE,
            ft.WindowEventType.UNMAXIMIZE,
            ft.WindowEventType.ENTER_FULL_SCREEN,
            ft.WindowEventType.LEAVE_FULL_SCREEN,
        }:
            if page.window.full_screen:
                state.mode = "fullscreen"
            elif page.window.maximized:
                state.mode = "maximized"
            else:
                state.mode = "normal"
        else:
            return

        try:
            if not await preferences.set(WINDOW_STATE_KEY, state.serialize()):
                raise PreferencesError("Could not save desktop window state.")
        except (OSError, PreferencesError):
            logger.exception("Could not save desktop window state")

    page.window.on_event = on_window_event
    return state


def report_page_error(event: ft.Event[ft.Page]) -> None:
    logger.error("Unhandled Flet error: %s", event.data)


async def main(page: ft.Page, *, mobile_preview: bool = False) -> None:
    asyncio.get_running_loop().set_exception_handler(report_async_error)
    page.on_error = report_page_error
    page.title = APP_NAME
    page.padding = 0
    desktop = desktop_pointer(page, mobile_preview=mobile_preview)
    page.theme = light_theme(desktop=desktop)
    page.dark_theme = dark_theme(desktop=desktop)
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[
            ft.Locale(language_code="en"),
            ft.Locale(language_code="uk"),
            ft.Locale(language_code="ru"),
        ]
    )
    native_desktop = not page.web and page.platform in {
        ft.PagePlatform.LINUX,
        ft.PagePlatform.WINDOWS,
        ft.PagePlatform.MACOS,
    }
    desktop = native_desktop and not mobile_preview
    if native_desktop:
        page.window.visible = False
    state = None
    if mobile_preview:
        page.window.width = 390
    elif desktop:
        state = await _restore_desktop_window(page)
    page.add(home_view(mobile_preview=mobile_preview))
    if native_desktop:
        await page.window.wait_until_ready_to_show()
        if state is not None:
            # Send the mode and visibility together, after earlier window updates.
            page.window.maximized = state.mode == "maximized"
            page.window.full_screen = state.mode == "fullscreen"
        page.window.visible = True
        page.window.update()
        await page.window.to_front()
    logger.info("Application ready")
    logger.debug("Page initialization complete")
