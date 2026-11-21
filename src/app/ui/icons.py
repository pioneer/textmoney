"""Icon catalogue, import and optional AI generation picker."""

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from io import BytesIO

import flet as ft
from PIL import Image

from app.domain.storage import ConflictError
from app.icons import (
    DISPLAY_SIZE,
    MAX_INPUT,
    IconAsset,
    IconError,
    normalize_generated_image,
    normalize_image,
)
from app.infrastructure.ai import MODELS, AIError, AIService, AISettings, Capability
from app.infrastructure.icon_sources import search_icons
from app.localization import translate
from app.storage import StorageError
from app.ui.dialogs import deletion_title, show_dialog

logger = logging.getLogger(__name__)

_ICON_PROMPT = (
    "Create a single icon suitable for an account or category in a finance app. "
    "Aim for an icon that is recognizable at 16 pixels. "
    "Unless the user explicitly requests a different style, use a simple uncluttered composition "
    "in the monochrome style of Material Icons and Material Symbols, without text, "
    "with a transparent background. Make the artwork fill about 80 percent of the square "
    "canvas, with a small even margin like a Material icon. "
    "Prefer thick, bold strokes over fine lines so they remain visible at 16 pixels. "
    "When in doubt, prefer fewer, larger elements over smaller details. "
    "Enlarge the main recognizable shapes and simplify or omit secondary details that would "
    "be hard to distinguish at 16 pixels, while keeping meaningful gaps between elements. "
    "The user's explicit style instructions take precedence over these default style choices.\n"
    "User's icon description:\n"
)
_REFERENCE_PROMPT = (
    "Use the supplied image as the reference icon. Apply the user's requested changes to it, "
    "preserving its recognizable features unless the user asks otherwise. "
    "The user's requested changes take precedence over the default style instructions.\n"
)
_CAPTURE_TIMEOUT = 10

_AI_ERRORS = {
    "ai_unconfigured": "icon_ai_model_hint",
    "ai_model_unavailable": "icon_ai_model_hint",
    "ai_key_missing": "icon_ai_key_hint",
    "invalid_prompt": "icon_ai_prompt_required",
    "invalid_image": "icon_ai_reference_error",
    "invalid_credentials": "icon_ai_credentials_error",
    "provider_access_denied": "icon_ai_credentials_error",
    "provider_rate_limit": "icon_ai_rate_error",
    "provider_refusal": "icon_ai_refusal",
    "provider_timeout": "icon_ai_network_error",
    "provider_network_error": "icon_ai_network_error",
    "invalid_provider_response": "icon_ai_response_error",
    "provider_request_failed": "icon_ai_request_error",
}


def _log_private_failure(message: str, exc: Exception) -> None:
    frames = traceback.extract_tb(exc.__traceback__)
    locations = "\n".join(f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames)
    logger.error("%s (%s)\n%s", message, type(exc).__name__, locations)


async def _run_icon_action(
    action: Callable[[], Awaitable[None]],
    status: ft.Text,
    language: str,
    *,
    private: bool = False,
) -> None:
    try:
        await action()
    except (IconError, ConflictError) as exc:
        logger.warning("Icon operation failed (%s)", str(exc))
        status.value = translate(language, str(exc))
    except (StorageError, TimeoutError) as exc:
        logger.warning("Icon storage operation failed (%s)", type(exc).__name__)
        status.value = translate(language, "icon_storage")
    except AIError as exc:
        logger.warning("AI icon operation failed (code=%s, provider=%s)", exc.code, exc.provider)
        status.value = translate(language, _AI_ERRORS.get(exc.code, exc.code))
    except Exception as exc:
        if private:
            _log_private_failure("Unexpected AI icon operation failure", exc)
        else:
            logger.exception("Unexpected icon operation failure")
        status.value = translate(language, "icon_failed")


def icon_image(asset: IconAsset, *, size: int = DISPLAY_SIZE) -> ft.Image:
    return ft.Image(
        src=asset.data,
        width=size,
        height=size,
        fit=ft.BoxFit.CONTAIN,
        color=ft.Colors.ON_SURFACE if asset.monochrome else None,
        semantics_label=asset.label,
        tooltip=f"{asset.label} · {asset.license}",
    )


def _validate_reference(data: bytes, *, monochrome: bool = False) -> bytes:
    try:
        data, vector = normalize_image(data)
        if vector:
            raise IconError("icon_ai_reference_error")
        with Image.open(BytesIO(data)) as image:
            alpha = image.convert("RGBA").getchannel("A")
            if alpha.getbbox() is None:
                raise IconError("icon_ai_reference_error")
            if monochrome:
                mask = Image.new("RGBA", image.size, (0, 0, 0, 0))
                mask.putalpha(alpha)
                output = BytesIO()
                mask.save(output, format="PNG")
                data = output.getvalue()
    except IconError as exc:
        raise IconError("icon_ai_reference_error") from exc
    return data


@dataclass(frozen=True)
class IconTarget:
    name: str
    code: str
    category: bool
    draft: bool = False


class IconField(ft.Column):
    def __init__(
        self, language: str, choose: Callable[[], None], remove: Callable[[], None]
    ) -> None:
        self.preview = ft.Container(width=40, height=40, alignment=ft.Alignment.CENTER)
        self.choose_button = ft.IconButton(
            icon=ft.Icons.IMAGE_OUTLINED,
            tooltip=translate(language, "icon_choose"),
            on_click=lambda _: choose(),
        )
        self.remove_button = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip=translate(language, "icon_remove"),
            visible=False,
            on_click=lambda _: remove(),
        )
        super().__init__(
            controls=[
                ft.Text(translate(language, "icon_choose"), size=12),
                ft.Row(
                    [
                        self.preview,
                        ft.Row([self.choose_button, self.remove_button], spacing=0, tight=True),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    spacing=0,
                ),
            ],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def set_icon(self, asset: IconAsset | None) -> None:
        self.preview.content = icon_image(asset) if asset else None
        self.remove_button.visible = asset is not None


class IconDraft(IconField):
    def __init__(
        self,
        page: ft.Page,
        language: str,
        existing_icons: Iterable[IconAsset] = (),
        *,
        ai_settings: Callable[[], AISettings] | None = None,
        target: Callable[[], IconTarget] | None = None,
    ) -> None:
        self.owner = page
        self.language = language
        self.existing_icons = tuple(existing_icons)
        self.ai_settings = ai_settings
        self.target = target
        self.selected: IconAsset | None = None
        super().__init__(
            language,
            lambda: self._choose(),
            lambda: self._choose(remove=True),
        )

    def _choose(self, *, remove: bool = False) -> None:
        async def select(asset: IconAsset | None) -> None:
            self.selected = asset
            self.set_icon(asset)

        show_dialog(
            self.owner,
            IconRemovalDialog(self.owner, self.language, select)
            if remove
            else IconPicker(
                self.owner,
                self.language,
                self.selected,
                select,
                existing_icons=self.existing_icons,
                ai_settings=self.ai_settings,
                target=self.target() if self.target is not None else None,
            ),
        )


class IconRemovalDialog(ft.AlertDialog):
    def __init__(
        self, page: ft.Page, language: str, save: Callable[[IconAsset | None], Awaitable[None]]
    ) -> None:
        self.owner = page
        self.language = language
        self.persist = save
        self.busy = False
        self.status = ft.Text(size=12, selectable=True)
        self.cancel = ft.TextButton(translate(language, "crud_cancel"), on_click=self._cancel)
        self.submit = ft.TextButton(translate(language, "crud_delete"), on_click=self._save)
        super().__init__(
            modal=True,
            scrollable=True,
            title=deletion_title(translate(language, "icon_remove")),
            content=ft.Column(
                [ft.Text(translate(language, "icon_remove_confirm")), self.status],
                tight=True,
                spacing=12,
                width=360,
            ),
            actions=[self.cancel, self.submit],
        )

    async def _save(self, event: ft.Event[ft.TextButton]) -> None:
        if self.busy:
            return

        async def remove() -> None:
            await self.persist(None)
            self.owner.pop_dialog()

        self.busy = True
        try:
            # Reset busy even if the disabling update fails, or every action stays ignored.
            self.status.value = ""
            self.cancel.disabled = self.submit.disabled = True
            self.owner.update()
            await _run_icon_action(remove, self.status, self.language)
        finally:
            self.busy = False
            self.cancel.disabled = self.submit.disabled = False
            self.owner.update()

    def _cancel(self, event: ft.Event[ft.TextButton]) -> None:
        if not self.busy:
            self.owner.pop_dialog()


class IconPicker(ft.AlertDialog):
    def __init__(
        self,
        page: ft.Page,
        language: str,
        current: IconAsset | None,
        save: Callable[[IconAsset | None], Awaitable[None]],
        *,
        existing_icons: Iterable[IconAsset] = (),
        conflict: bool = False,
        ai_settings: Callable[[], AISettings] | None = None,
        target: IconTarget | None = None,
    ) -> None:
        self.owner = page
        self.language = language
        self.persist = save
        self.selected = current
        self.reference = current
        self.generated: IconAsset | None = None
        self.existing_icons = tuple(existing_icons)
        self.busy = False
        self.ai_settings = ai_settings
        self.ai_load_task: Future[None] | None = None
        self.generation_task: asyncio.Task[object] | None = None
        self.results = ft.Column(spacing=4)
        self.results_limit = ft.Text(self.tr("icon_results_limit"), size=12)
        self.search = ft.SearchBar(
            bar_hint_text=self.tr("icon_search"),
            view_hint_text=self.tr("icon_search"),
            bar_leading=ft.Icon(ft.Icons.SEARCH),
            controls=[self.results_limit, self.results],
            view_size_constraints=ft.BoxConstraints(max_height=320),
            autofocus=True,
            on_tap=self._open_search,
            on_change=self._local,
        )
        self.import_button = ft.TextButton(
            self.tr("icon_import"), icon=ft.Icons.UPLOAD_FILE, on_click=self._import
        )
        self.ai_hint = ft.Text("", size=12, visible=False)
        self.ai_prompt = ft.TextField(
            label=self.tr("icon_ai_prompt"),
            max_length=2000,
            max_lines=1,
            shift_enter=True,
            on_submit=self._generate,
        )
        self.ai_generate = ft.FilledButton(self.tr("icon_ai_generate"), on_click=self._generate)
        self.ai_cancel = ft.TextButton(
            self.tr("icon_ai_cancel"), on_click=self._cancel_generation, visible=False
        )
        self.ai_progress = ft.ProgressRing(
            visible=False, width=20, height=20, semantics_label=self.tr("icon_ai_generating")
        )
        self.ai_status = ft.Text(size=12)
        self.ai_feedback = ft.Semantics(
            content=self.ai_status, live_region=True, exclude_semantics=True
        )
        self.ai_reference = ft.Checkbox(value=False, semantics_label=self.tr("icon_ai_reference"))
        self.ai_reference_tile = ft.ListTile(
            title=self.tr("icon_ai_reference"),
            subtitle=self.tr("icon_ai_reference_hint"),
            leading=self.ai_reference,
            toggle_inputs=True,
            content_padding=0,
            dense=True,
            expand=True,
        )
        self.reference_preview = (
            ft.Screenshot(
                ft.Image(
                    src=current.data,
                    width=48,
                    height=48,
                    fit=ft.BoxFit.CONTAIN,
                    color=ft.Colors.ON_SURFACE if current.monochrome else None,
                    semantics_label=current.label,
                )
            )
            if current
            else None
        )
        self.ai_reference_row = ft.Row(
            [self.reference_preview, self.ai_reference_tile] if self.reference_preview else [],
            visible=current is not None,
            spacing=8,
        )
        self.ai_section = ft.ExpansionTile(
            title=self.tr("icon_ai_title"),
            controls=[
                ft.Container(
                    ft.Text(self.tr("icon_ai_notice"), size=12),
                    padding=ft.Padding.only(bottom=8),
                ),
                self.ai_reference_row,
                self.ai_prompt,
                ft.Row([self.ai_generate, self.ai_progress, self.ai_cancel], wrap=True),
                self.ai_feedback,
            ],
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
            maintain_state=True,
            visible=False,
        )
        self.remove = ft.TextButton(
            self.tr("icon_remove"), icon=ft.Icons.DELETE_OUTLINE, on_click=self._remove
        )
        self.preview = ft.Row(spacing=8)
        self.selection_license = ft.Text(size=12)
        self.selection_rights = ft.Text(self.tr("icon_ai_rights_notice"), size=12, visible=False)
        self.credit = ft.Text(size=12, selectable=True)
        self.license_text = ft.Text(size=12, selectable=True)
        self.status = ft.Text(size=12)
        self.feedback = ft.Semantics(content=self.status, live_region=True, exclude_semantics=True)
        self.progress = ft.ProgressRing(visible=False, width=20, height=20)
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.submit = ft.TextButton(self.tr("crud_save"), on_click=self._save)
        self.selection_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text(self.tr("icon_selected"), weight=ft.FontWeight.W_600),
                    self.preview,
                    self.selection_license,
                    self.selection_rights,
                    self.remove,
                    ft.ExpansionTile(
                        title=self.tr("icon_attribution"),
                        controls=[self.credit, self.license_text],
                        maintain_state=True,
                    ),
                ],
                tight=True,
                spacing=8,
            ),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border_radius=12,
            padding=12,
            key=ft.ScrollKey("icon-selection"),
        )
        target_controls: list[ft.Control] = []
        if target is not None:
            name_label = self.tr("category_name" if target.category else "account_name")
            code_label = self.tr("category_path" if target.category else "account_code")
            details: list[ft.Control] = []
            if target.draft:
                details.append(ft.Text(self.tr("icon_target_draft"), size=12))
            details.extend(
                [
                    ft.Text(f"{name_label}: {target.name.strip() or self.tr('icon_target_empty')}"),
                    ft.Text(
                        f"{code_label}: {target.code.strip() or self.tr('icon_target_empty')}",
                        size=12,
                    ),
                ]
            )
            target_controls.append(ft.Column(details, tight=True, spacing=2))
        self.body = ft.Column(
            [
                *target_controls,
                self.search,
                ft.Text(self.tr("icon_privacy"), size=12),
                self.import_button,
                self.ai_hint,
                self.ai_section,
                ft.Text(self.tr("icon_conflicts"), visible=conflict, color=ft.Colors.ERROR),
                self.progress,
                self.feedback,
                self.selection_panel,
            ],
            width=420,
            max_height=560,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
            spacing=8,
        )
        super().__init__(
            modal=True,
            title=self.tr("icon_choose"),
            content=self.body,
            actions=[self.cancel, self.submit],
        )
        self._selection()
        self._show(search_icons("", self.existing_icons))

    def did_mount(self) -> None:
        if self.ai_settings is not None:
            self.ai_load_task = self.owner.run_task(self._load_ai)

    def will_unmount(self) -> None:
        if self.ai_load_task is not None:
            self.ai_load_task.cancel()
        if self.generation_task is not None:
            self.generation_task.cancel()

    async def _load_ai(self) -> None:
        if self.ai_settings is None:
            return
        try:
            settings = self.ai_settings()
            selection = await settings.selection(Capability.GENERATE)
            models = MODELS[Capability.GENERATE]
            model = next((item for item in models if item.id == selection), None)
            if model is not None:
                has_key = await settings.has_key(model.provider)
                hint = "" if has_key else "icon_ai_key_hint"
            else:
                has_key = any(
                    [await settings.has_key(provider) for provider in {m.provider for m in models}]
                )
                hint = "icon_ai_model_hint" if has_key else "icon_ai_key_hint"
            self.ai_section.visible = model is not None and has_key
            self.ai_hint.value = self.tr(hint) if hint else ""
            self.ai_hint.visible = bool(hint)
        except AIError as exc:
            logger.warning("Could not load AI icon settings (code=%s)", exc.code)
            self.ai_hint.value = self.tr(exc.code)
            self.ai_hint.visible = True
        except Exception as exc:
            _log_private_failure("Could not load AI icon settings", exc)
            self.ai_hint.value = self.tr("ai_settings_error")
            self.ai_hint.visible = True
        self.owner.update()

    def tr(self, key: str) -> str:
        return translate(self.language, key)

    def _feedback(self) -> None:
        self.ai_feedback.label = self.ai_status.value
        self.feedback.label = self.status.value

    def _selection(self) -> None:
        asset = self.selected
        self.preview.controls = (
            [icon_image(asset, size=48), ft.Text(asset.label, expand=True)]
            if asset
            else [ft.Text(self.tr("icon_none"))]
        )
        self.credit.value = (
            f"{asset.source}\n{asset.attribution}\n{asset.license}\n{asset.license_url}"
            if asset
            else ""
        )
        self.license_text.value = asset.license_text if asset else ""
        self.selection_license.value = asset.license if asset else ""
        self.selection_rights.visible = asset is not None and asset is self.generated
        self.remove.disabled = asset is None

    def _show(self, assets: list[IconAsset]) -> None:
        self.results.controls = [
            ft.TextButton(
                content=ft.Row(
                    [icon_image(asset), ft.Text(asset.label, expand=True)],
                    spacing=8,
                ),
                tooltip=f"{asset.license} · {asset.attribution}",
                data=asset,
                on_click=self._choose,
            )
            for asset in assets
        ] or [ft.Text(self.tr("icon_no_results"))]

    async def _open_search(self, event: ft.Event[ft.SearchBar]) -> None:
        if not self.busy:
            await self.search.open_view()

    async def _choose(self, event: ft.Event[ft.TextButton]) -> None:
        if not self.busy:
            await self.search.close_view()
            self._select(event.control.data)

    def _select(self, asset: IconAsset) -> None:
        if self.busy:
            return
        self.selected = asset
        self.ai_status.value = ""
        self._feedback()
        self._selection()
        self.owner.update()

    def _remove(self, event: ft.Event[ft.TextButton]) -> None:
        if self.busy:
            return
        self.selected = None
        self.ai_status.value = ""
        self._feedback()
        self._selection()
        self.owner.update()

    def _local(self, event: ft.Event[ft.SearchBar]) -> None:
        if not self.busy:
            self.status.value = ""
            self._feedback()
            self._show(search_icons(self.search.value or "", self.existing_icons))
            self.owner.update()

    async def _run(self, action: Callable[[], Awaitable[None]], *, private: bool = False) -> None:
        if self.busy:
            return
        self.busy = True
        status = self.ai_status if private else self.status
        progress = self.ai_progress if private else self.progress
        try:
            # Reset busy even if the disabling update fails, or every action stays ignored.
            self.status.value = ""
            self.ai_status.value = ""
            self._feedback()
            progress.visible = True
            for control in (
                self.search,
                self.import_button,
                self.cancel,
                self.submit,
                self.results,
                self.remove,
                self.ai_prompt,
                self.ai_generate,
                self.ai_reference,
                self.ai_reference_tile,
            ):
                control.disabled = True
            self.owner.update()
            await _run_icon_action(action, status, self.language, private=private)
        finally:
            self.busy = False
            progress.visible = False
            for control in (
                self.search,
                self.import_button,
                self.cancel,
                self.submit,
                self.results,
                self.ai_prompt,
                self.ai_generate,
                self.ai_reference,
                self.ai_reference_tile,
            ):
                control.disabled = False
            self._selection()
            self._feedback()
            self.owner.update()

    async def _import(self, event: ft.Event[ft.TextButton]) -> None:
        async def pick() -> None:
            files = await ft.FilePicker().pick_files(
                allow_multiple=False,
                with_data=True,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["png", "jpg", "jpeg", "webp", "svg"],
            )
            if not files:
                return
            file = files[0]
            if file.size > MAX_INPUT:
                raise IconError("icon_size")
            if file.bytes is None:
                raise IconError("icon_import_failed")
            data, _ = await asyncio.to_thread(normalize_image, file.bytes)
            asset = IconAsset(
                self.tr("icon_imported"),
                data,
                False,
                self.tr("icon_imported"),
                self.tr("icon_user_license"),
                self.tr("icon_import_notice"),
            )
            self.selected = asset
            self._selection()
            self.status.value = self.tr("icon_import_notice")

        await self._run(pick)

    async def _generate(self, event: ft.Event) -> None:
        settings_factory = self.ai_settings
        if (
            self.busy
            or settings_factory is None
            or not self.open
            or not self.owner._dialogs.controls
            or self.owner._dialogs.controls[-1] is not self
            or self.disabled
            or self.ai_section.disabled
            or not self.ai_section.visible
            or self.ai_prompt.disabled
            or self.ai_generate.disabled
        ):
            return
        description = self.ai_prompt.value.strip()
        if not description:
            self.ai_status.value = self.tr("icon_ai_prompt_required")
            self._feedback()
            self.owner.update()
            return

        generated = False

        async def generate() -> None:
            nonlocal generated
            reference = self.reference if self.ai_reference.value else None
            attribution = self.tr("icon_ai_rights_notice")
            if reference is not None:
                attribution += (
                    f"\n{self.tr('icon_ai_reference_source')}: {reference.label}\n"
                    f"{reference.source}\n{reference.attribution}\n"
                    f"{reference.license}\n{reference.license_url}"
                )
                if len(attribution) > 32768:
                    raise IconError("icon_ai_reference_error")
                if self.reference_preview is None:
                    raise IconError("icon_ai_reference_error")
                reference_data = reference.data
                vector = reference_data.lstrip().startswith((b"<svg", b"<?xml"))
                if vector:
                    try:
                        reference_data = await asyncio.wait_for(
                            self.reference_preview.capture(pixel_ratio=2), timeout=_CAPTURE_TIMEOUT
                        )
                    except TimeoutError as exc:
                        raise IconError("icon_ai_reference_error") from exc
                reference_data = await asyncio.to_thread(
                    _validate_reference, reference_data, monochrome=vector and reference.monochrome
                )
                image = await AIService(settings_factory()).generate_image(
                    _REFERENCE_PROMPT + _ICON_PROMPT + description,
                    reference_image=reference_data,
                )
            else:
                image = await AIService(settings_factory()).generate_image(
                    _ICON_PROMPT + description
                )
            data, monochrome = await asyncio.to_thread(normalize_generated_image, image.data)
            asset = IconAsset(
                self.tr("icon_ai_generated"),
                data,
                monochrome,
                self.tr("icon_ai_generated"),
                self.tr("icon_ai_rights"),
                attribution,
                reference.license_url if reference else "",
                reference.license_text if reference else "",
            )
            self.generated = asset
            self.selected = asset
            self._selection()
            self.ai_generate.content = self.tr("icon_ai_generate_again")
            self.ai_status.value = self.tr("icon_ai_ready")
            generated = True

        self.generation_task = asyncio.current_task()
        self.ai_cancel.visible = True
        try:
            await self._run(generate, private=True)
        except asyncio.CancelledError:
            self.ai_status.value = self.tr("icon_ai_cancelled")
            self._feedback()
            self.owner.update()
        finally:
            self.generation_task = None
            self.ai_cancel.visible = False
            self.owner.update()
        if (
            generated
            and self.open
            and self.owner._dialogs.controls
            and self.owner._dialogs.controls[-1] is self
        ):
            await self.body.scroll_to(scroll_key=self.selection_panel.key, duration=350)

    def _cancel_generation(self, event: ft.Event[ft.TextButton]) -> None:
        if self.generation_task is not None:
            self.generation_task.cancel()

    async def _save(self, event: ft.Event[ft.TextButton]) -> None:
        async def save() -> None:
            await self.persist(self.selected)
            self.owner.pop_dialog()

        await self._run(save)

    def _cancel(self, event: ft.Event[ft.TextButton]) -> None:
        if not self.busy:
            self.owner.pop_dialog()
