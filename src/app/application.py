import asyncio
import logging

import flet as ft

from app.config import APP_NAME
from app.logging_config import report_async_error
from app.ui.home import home_view
from app.ui.theme import dark_theme, desktop_pointer, light_theme

logger = logging.getLogger(__name__)


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
    if mobile_preview:
        page.window.width = 390
    page.add(home_view(mobile_preview=mobile_preview))
    logger.info("Application ready")
    logger.debug("Page initialization complete")
