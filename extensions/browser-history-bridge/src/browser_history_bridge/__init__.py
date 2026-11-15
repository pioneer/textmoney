"""Flet service for requesting browser history traversal."""

import flet as ft


@ft.control("BrowserHistory")
class BrowserHistory(ft.Service):
    async def back(self) -> None:
        """Request one browser Back navigation; completion is asynchronous."""
        await self._invoke_method("back", timeout=10)
