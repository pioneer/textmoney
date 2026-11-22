"""Focus-scoped keyboard delivery after dropdowns and nested dialogs close."""

import flet as ft


@ft.control("PopupKeyboardListener")
class PopupKeyboardListener(ft.KeyboardListener):
    """Keep normal key delivery and recover Escape when popup focus is lost."""

    restore_focus: bool = False


@ft.control("MobileCategoryMenu")
class MobileCategoryMenu(ft.Container):
    """Constrain a category dropdown's native overlay to the reachable viewport."""

    menu_height_limit: float = 672


@ft.control("CategoryMenuConnector")
class CategoryMenuConnector(ft.Container):
    """Draw a mobile category row's hierarchy guides in the client."""

    continued: tuple[bool, ...] = ()


@ft.control("PopupMenuBackScope")
class PopupMenuBackScope(ft.Container):
    """Dismiss an open dropdown before its Android dialog."""


@ft.control("SearchSuggestionsKeyboard")
class SearchSuggestionsKeyboard(ft.Container):
    """Navigate search suggestions with native focus, scoped to their route."""
