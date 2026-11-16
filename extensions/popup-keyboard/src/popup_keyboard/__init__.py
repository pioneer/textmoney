"""Focus-scoped keyboard delivery after dropdowns and nested dialogs close."""

import flet as ft


@ft.control("PopupKeyboardListener")
class PopupKeyboardListener(ft.KeyboardListener):
    """Keep normal key delivery and recover Escape when popup focus is lost."""

    restore_focus: bool = False
