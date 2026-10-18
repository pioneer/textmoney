"""Android-only transport; storage policy lives in the application service."""

import flet as ft


@ft.control("FinanceSaf")
class FinanceSaf(ft.Service):
    async def call(self, method: str, **arguments: object) -> object:
        return await self._invoke_method(method, arguments, timeout=120)
