import asyncio
import logging

import flet as ft

from app.config import APP_NAME
from app.logging_config import report_async_error
from app.ui.home import home_view

logger = logging.getLogger(__name__)


def report_page_error(event: ft.Event[ft.Page]) -> None:
    logger.error("Unhandled Flet error: %s", event.data)


async def main(page: ft.Page) -> None:
    asyncio.get_running_loop().set_exception_handler(report_async_error)
    page.on_error = report_page_error
    page.title = APP_NAME
    page.padding = 0
    page.theme = ft.Theme(color_scheme_seed="#287a6a")
    page.dark_theme = ft.Theme(color_scheme_seed="#81c6b3")
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[
            ft.Locale(language_code="en"),
            ft.Locale(language_code="uk"),
            ft.Locale(language_code="ru"),
        ]
    )
    page.add(home_view())
    logger.info("Application ready")
    logger.debug("Page initialization complete")
