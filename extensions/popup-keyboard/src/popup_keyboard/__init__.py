"""Focus-scoped keyboard delivery after a Flet dropdown closes."""

import flet as ft


@ft.control("PopupKeyboardListener")
class PopupKeyboardListener(ft.KeyboardListener):
    """Keep normal key delivery and recover Escape from closed dropdowns."""
