"""Android notification transport, with policies owned by the application."""

import flet as ft


@ft.control("NotificationBridge")
class NotificationBridge(ft.Service):
    async def call(self, method: str, **arguments: object) -> object:
        return await self._invoke_method(method, arguments, timeout=30)
