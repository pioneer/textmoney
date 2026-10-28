import asyncio
import logging
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.messaging.session import Session

from app.application import main, report_page_error
from app.domain import readiness_message
from app.ui.home import home_view


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
    assert [locale.language_code for locale in page.locale_configuration.supported_locales] == [
        "en",
        "uk",
        "ru",
    ]
    page.add.assert_called_once()
    assert page.add.call_args.args[0].expand


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
