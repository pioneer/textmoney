"""Flet entry point, shared by development and packaged applications."""

import flet as ft

from app.application import main
from app.config import Settings
from app.logging_config import configure_logging, install_exception_hooks


def run() -> None:
    install_exception_hooks()
    configure_logging(debug=False)
    settings = Settings.from_env()
    configure_logging(settings.debug)
    ft.run(main, route_url_strategy=ft.RouteUrlStrategy.HASH)


if __name__ == "__main__":
    run()
