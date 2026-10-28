"""Focus-scoped Escape cancellation for desktop modal dialogs."""

import flet as ft


def show_dialog(page: ft.Page, dialog: ft.AlertDialog) -> None:
    if dialog.modal and page.platform in {
        ft.PagePlatform.LINUX,
        ft.PagePlatform.WINDOWS,
        ft.PagePlatform.MACOS,
    }:
        if not dialog.actions or not isinstance(dialog.actions[0], ft.TextButton):
            raise ValueError("Modal dialogs must put their Cancel/Close action first.")
        cancel = dialog.actions[0]
        if cancel.on_click is None:
            raise ValueError("Modal dialog cancellation requires a click handler.")

        async def key_down(event: ft.KeyDownEvent) -> None:
            if (
                event.key.casefold() == "escape"
                and dialog.open
                and not dialog.disabled
                and not cancel.disabled
            ):
                # Use Flet's normal click dispatch, including async handlers and
                # the button's event payload. Recheck this on Flet upgrades.
                await cancel._trigger_event("click", None)

        # Scope listeners to the route's focus tree, not Page's hardware keys:
        # a dropdown or nested picker must consume Escape before its parent.
        dialog.content = ft.KeyboardListener(
            dialog.content if dialog.content is not None else ft.Container(),
            autofocus=True,
            include_semantics=False,
            on_key_down=key_down,
        )
        dialog.actions = [
            ft.KeyboardListener(action, include_semantics=False, on_key_down=key_down)
            for action in dialog.actions
        ]
    page.show_dialog(dialog)
