"""Focus-scoped desktop modal cancellation and submission."""

import inspect
from collections.abc import Callable, Iterator
from dataclasses import fields
from typing import cast

import flet as ft


def _text_fields(root: ft.Control) -> Iterator[ft.TextField]:
    visited: set[int] = set()

    def visit(control: ft.Control) -> Iterator[ft.TextField]:
        if control._i in visited:
            return
        visited.add(control._i)
        if isinstance(control, ft.TextField):
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


def show_dialog(page: ft.Page, dialog: ft.AlertDialog) -> None:
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
        inputs = list(_text_fields(content))
        focused: ft.TextField | None = None
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

        def bind(field: ft.TextField) -> None:
            original_focus, original_blur = field.on_focus, field.on_blur

            async def track(event: ft.Event[ft.TextField]) -> None:
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
            if field.multiline:
                if field.on_submit is not None:
                    raise ValueError("Modal multiline inputs must use the dialog submit action.")
                field.shift_enter = True

                async def submit_field(event: ft.Event[ft.TextField]) -> None:
                    if _enabled(field):
                        await click(submit)

                field.on_submit = submit_field

        for field in inputs:
            bind(field)
        if inputs and not any(field.autofocus for field in inputs):
            inputs[0].autofocus = True

        async def cancel_key(event: ft.KeyDownEvent) -> None:
            if event.key.casefold() == "escape":
                await click(cancel)

        async def content_key(event: ft.KeyDownEvent) -> None:
            await cancel_key(event)
            if event.key.casefold() in {"enter", "numpad enter"} and (
                not inputs or (focused is not None and not focused.multiline and _enabled(focused))
            ):
                # Flet 1.0.1 only emits TextField.submit with shift_enter=True.
                # Multiline inputs use that event; other focused widgets keep
                # their native Enter handling instead of submitting the form.
                await click(submit)

        # Scope listeners to the route's focus tree, not Page's hardware keys:
        # a dropdown or nested picker must consume Escape before its parent.
        dialog.content = ft.KeyboardListener(
            content,
            autofocus=not inputs,
            include_semantics=False,
            on_key_down=content_key,
        )
        dialog.actions = [
            ft.KeyboardListener(action, include_semantics=False, on_key_down=cancel_key)
            for action in dialog.actions
        ]
    page.show_dialog(dialog)
