"""Adaptive main editors and focus-scoped modal keyboard handling."""

import inspect
from collections.abc import Callable, Iterator
from dataclasses import fields
from typing import cast

import flet as ft
from popup_keyboard import PopupKeyboardListener, PopupMenuBackScope


class EditorDialog(ft.AlertDialog):
    """Keep the editor body scrollable without scrolling its title or actions."""

    def __init__(
        self,
        title: str,
        form: ft.Column,
        actions: list[ft.Control],
        *,
        compact: Callable[[], bool],
        expand_body: Callable[[], bool] = lambda: False,
        cancel_on_back: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(modal=True, title=title, content=form, actions=actions)
        self.form = form
        self._compact = compact
        self._expand_body = expand_body
        self.cancel_on_back = cancel_on_back
        self._top_level: bool | None = None
        self._apply_layout()

    def before_update(self) -> None:
        super().before_update()
        if self._top_level is None:
            # Decide once: dismissing an ancestor must not promote a nested popup.
            self._top_level = True
            for dialog in self.page._dialogs.controls:
                if dialog is self:
                    break
                if not isinstance(dialog, ft.SnackBar) and getattr(dialog, "open", False):
                    self._top_level = False
                    break
        self._apply_layout(self.page.width)

    def _apply_layout(self, width: float | None = None) -> None:
        main = self._top_level is not False
        compact = main and self._compact()
        self.scrollable = not main
        self.form.scroll = ft.ScrollMode.AUTO if main else None
        # Flutter clamps these preferred dimensions to the safe area minus the
        # keyboard, title and actions. No keyboard-height calculation is needed.
        self.form.width = width if compact else 360
        self.form.height = float("inf") if compact else None
        # Keep the client's constraint wrapper mounted across the width boundary
        # so text-field focus, selection and local edits survive resizing.
        use_available_height = compact or self._expand_body()
        self.form.max_height = (float("inf") if use_available_height else 560) if main else None
        self.inset_padding = 0 if compact else None
        self.shape = ft.RoundedRectangleBorder(radius=0) if compact else None
        self.title_padding = ft.Padding.only(left=16, top=16, right=16) if compact else None
        self.content_padding = ft.Padding.symmetric(horizontal=16, vertical=12) if compact else None
        self.actions_padding = ft.Padding.all(8) if compact else None


def deletion_title(title: str) -> ft.Row:
    return ft.Row(
        [
            ft.Icon(ft.Icons.WARNING_AMBER, size=26, color=ft.Colors.ERROR),
            ft.Text(title, expand=True),
        ],
        spacing=10,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _controls(root: ft.Control) -> Iterator[ft.Control]:
    visited: set[int] = set()

    def visit(control: ft.Control) -> Iterator[ft.Control]:
        if control._i in visited:
            return
        visited.add(control._i)
        yield control
        for field in fields(control):
            if field.metadata.get("skip") or field.name.startswith("_"):
                continue
            value = getattr(control, field.name)
            if isinstance(value, ft.Control):
                yield from visit(value)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    if isinstance(child, ft.Control):
                        yield from visit(child)

    yield from visit(root)


def _enabled(control: ft.BaseControl) -> bool:
    current: ft.BaseControl | None = control
    while current is not None:
        if isinstance(current, ft.Control) and (current.disabled or not current.visible):
            return False
        current = current.parent
    return True


def show_dialog(
    page: ft.Page,
    dialog: ft.AlertDialog,
    *,
    register_dynamic_inputs: Callable[[Callable[[ft.Control], None]], None] | None = None,
) -> None:
    if (
        page.platform == ft.PagePlatform.ANDROID
        and not page.web
        and dialog.content is not None
        and any(isinstance(control, ft.Dropdown) for control in _controls(dialog.content))
    ):
        dialog.content = PopupMenuBackScope(dialog.content)
    if page.platform in {
        ft.PagePlatform.LINUX,
        ft.PagePlatform.WINDOWS,
        ft.PagePlatform.MACOS,
    }:
        if not dialog.actions or not isinstance(dialog.actions[0], ft.TextButton):
            raise ValueError("Modal dialogs must put their Cancel/Close action first.")
        cancel = dialog.actions[0]
        if cancel.on_click is None:
            raise ValueError("Modal dialog cancellation requires a click handler.")
        if len(dialog.actions) > 2 or not isinstance(dialog.actions[-1], ft.TextButton):
            raise ValueError("Modal dialogs require one Close action or Cancel and Submit.")
        submit = dialog.actions[-1]
        if submit.on_click is None:
            raise ValueError("Modal dialog submission requires a click handler.")
        content = dialog.content if dialog.content is not None else ft.Container()
        inputs: list[ft.TextField | ft.SearchBar] = []
        focused: ft.TextField | ft.SearchBar | None = None
        dispatching = False

        async def click(button: ft.TextButton) -> None:
            nonlocal dispatching
            if (
                not dispatching
                and dialog.open
                and page._dialogs.controls[-1] is dialog
                and _enabled(button)
            ):
                # Use Flet's normal click dispatch, including async handlers and
                # the button's event payload. Recheck this on Flet upgrades.
                dispatching = True
                try:
                    await button._trigger_event("click", None)
                finally:
                    dispatching = False

        def bind(field: ft.TextField | ft.SearchBar) -> None:
            original_focus, original_blur = field.on_focus, field.on_blur

            async def track(event: ft.Event) -> None:
                nonlocal focused
                if event.name == "focus":
                    focused = field
                elif focused is field:
                    focused = None
                original = original_focus if event.name == "focus" else original_blur
                if original is not None:
                    callback = cast(Callable[..., object], original)
                    result = (
                        callback(event) if inspect.signature(callback).parameters else callback()
                    )
                    if inspect.isawaitable(result):
                        await result

            field.on_focus = field.on_blur = track
            if isinstance(field, ft.TextField) and field.multiline:
                if field.on_submit is not None:
                    raise ValueError("Modal multiline inputs must use the dialog submit action.")
                field.shift_enter = True

                async def submit_field(event: ft.Event[ft.TextField]) -> None:
                    if _enabled(field):
                        await click(submit)

                field.on_submit = submit_field

        def register(root: ft.Control) -> None:
            for field in _controls(root):
                if isinstance(field, (ft.TextField, ft.SearchBar)) and all(
                    field is not existing for existing in inputs
                ):
                    inputs.append(field)
                    bind(field)

        register(content)
        if register_dynamic_inputs is not None:
            register_dynamic_inputs(register)
        if inputs and not any(field.autofocus for field in inputs):
            inputs[0].autofocus = True

        async def cancel_key(event: ft.KeyDownEvent) -> None:
            if event.key.casefold() == "escape":
                await click(cancel)

        async def content_key(event: ft.KeyDownEvent) -> None:
            await cancel_key(event)
            if event.key.casefold() in {"enter", "numpad enter"} and (
                not inputs
                or (
                    focused is not None
                    and (
                        isinstance(focused, ft.SearchBar)
                        or (not focused.multiline and not focused.shift_enter)
                    )
                    and _enabled(focused)
                )
            ):
                # Flet only emits TextField.submit with shift_enter=True.
                # Multiline inputs use that event; other focused widgets keep
                # their native Enter handling instead of submitting the form.
                await click(submit)

        # Scope listeners to the route's focus tree, not Page's hardware keys:
        # a dropdown or nested picker must consume Escape before its parent.
        dialog.content = PopupKeyboardListener(
            content,
            autofocus=not inputs,
            restore_focus=True,
            include_semantics=False,
            on_key_down=content_key,
        )
        dialog.actions = [
            PopupKeyboardListener(action, include_semantics=False, on_key_down=cancel_key)
            for action in dialog.actions
        ]
    page.show_dialog(dialog)
