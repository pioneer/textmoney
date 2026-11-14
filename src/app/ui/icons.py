"""Offline icon catalogue and image picker."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable

import flet as ft

from app.domain.storage import ConflictError
from app.icons import DISPLAY_SIZE, MAX_INPUT, IconAsset, IconError, normalize_image
from app.infrastructure.icon_sources import search_icons
from app.localization import translate
from app.storage import StorageError
from app.ui.dialogs import show_dialog

logger = logging.getLogger(__name__)


def icon_image(asset: IconAsset) -> ft.Image:
    return ft.Image(
        src=asset.data,
        width=DISPLAY_SIZE,
        height=DISPLAY_SIZE,
        fit=ft.BoxFit.CONTAIN,
        color=ft.Colors.ON_SURFACE if asset.monochrome else None,
        semantics_label=asset.label,
        tooltip=f"{asset.label} · {asset.license}",
    )


class IconDraft(ft.Column):
    def __init__(
        self, page: ft.Page, language: str, existing_icons: Iterable[IconAsset] = ()
    ) -> None:
        self.owner = page
        self.language = language
        self.existing_icons = tuple(existing_icons)
        self.selected: IconAsset | None = None
        self.preview = ft.Row(spacing=8, visible=False)
        self.choose_button = ft.TextButton(
            translate(language, "icon_choose"),
            icon=ft.Icons.IMAGE_SEARCH,
            on_click=self._choose,
        )
        super().__init__(
            controls=[self.choose_button, self.preview],
            spacing=4,
        )

    def _choose(self, event: ft.Event[ft.TextButton]) -> None:
        async def select(asset: IconAsset | None) -> None:
            self.selected = asset
            self.preview.controls = (
                [icon_image(asset), ft.Text(asset.label, expand=True)] if asset else []
            )
            self.preview.visible = asset is not None

        show_dialog(
            self.owner,
            IconPicker(
                self.owner,
                self.language,
                self.selected,
                select,
                existing_icons=self.existing_icons,
            ),
        )


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
    ) -> None:
        self.owner = page
        self.language = language
        self.persist = save
        self.selected = current
        self.existing_icons = tuple(existing_icons)
        self.busy = False
        self.search = ft.TextField(label=self.tr("icon_search"), on_change=self._local)
        self.import_button = ft.TextButton(self.tr("icon_import"), on_click=self._import)
        self.remove = ft.TextButton(self.tr("icon_remove"), on_click=self._remove)
        self.results = ft.Column(spacing=4)
        self.results_limit = ft.Text(self.tr("icon_results_limit"), size=12)
        self.preview = ft.Row(spacing=8)
        self.credit = ft.Text(size=12, selectable=True)
        self.license_text = ft.Text(size=12, selectable=True)
        self.status = ft.Text(size=12, selectable=True)
        self.progress = ft.ProgressRing(visible=False, width=20, height=20)
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.submit = ft.TextButton(self.tr("crud_save"), on_click=self._save)
        super().__init__(
            modal=True,
            scrollable=True,
            title=self.tr("icon_choose"),
            content=ft.Column(
                [
                    self.search,
                    ft.Text(self.tr("icon_privacy"), size=12),
                    self.import_button,
                    ft.Text(self.tr("icon_conflicts"), visible=conflict, color=ft.Colors.ERROR),
                    self.progress,
                    self.status,
                    self.preview,
                    self.remove,
                    ft.ExpansionTile(
                        title=self.tr("icon_attribution"),
                        controls=[self.credit, self.license_text],
                        maintain_state=True,
                    ),
                    self.results_limit,
                    self.results,
                ],
                width=420,
                tight=True,
                spacing=8,
            ),
            actions=[self.cancel, self.submit],
        )
        self._selection()
        self._show(search_icons("", self.existing_icons))

    def tr(self, key: str) -> str:
        return translate(self.language, key)

    def _selection(self) -> None:
        asset = self.selected
        self.preview.controls = (
            [icon_image(asset), ft.Text(asset.label, expand=True)]
            if asset
            else [ft.Text(self.tr("icon_none"))]
        )
        self.credit.value = (
            f"{asset.source}\n{asset.attribution}\n{asset.license}\n{asset.license_url}"
            if asset
            else ""
        )
        self.license_text.value = asset.license_text if asset else ""
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

    def _choose(self, event: ft.Event[ft.TextButton]) -> None:
        if self.busy:
            return
        self.selected = event.control.data
        self._selection()
        self.owner.update()

    def _remove(self, event: ft.Event[ft.TextButton]) -> None:
        if self.busy:
            return
        self.selected = None
        self._selection()
        self.owner.update()

    def _local(self, event: ft.Event[ft.TextField]) -> None:
        if not self.busy:
            self.status.value = ""
            self._show(search_icons(self.search.value or "", self.existing_icons))
            self.owner.update()

    async def _run(self, action: Callable[[], Awaitable[None]]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.value = ""
        self.progress.visible = True
        for control in (
            self.search,
            self.import_button,
            self.cancel,
            self.submit,
            self.results,
            self.remove,
        ):
            control.disabled = True
        self.owner.update()
        try:
            await action()
        except (IconError, ConflictError) as exc:
            logger.warning("Icon operation failed (%s)", str(exc))
            self.status.value = self.tr(str(exc))
        except (StorageError, TimeoutError) as exc:
            logger.warning("Icon storage operation failed (%s)", type(exc).__name__)
            self.status.value = self.tr("icon_storage")
        except Exception:
            logger.exception("Unexpected icon operation failure")
            self.status.value = self.tr("icon_failed")
        finally:
            self.busy = False
            self.progress.visible = False
            for control in (
                self.search,
                self.import_button,
                self.cancel,
                self.submit,
                self.results,
            ):
                control.disabled = False
            self._selection()
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
            self._show([asset])
            self.selected = asset
            self._selection()
            self.status.value = self.tr("icon_import_notice")

        await self._run(pick)

    async def _save(self, event: ft.Event[ft.TextButton]) -> None:
        async def save() -> None:
            await self.persist(self.selected)
            self.owner.pop_dialog()

        await self._run(save)

    def _cancel(self, event: ft.Event[ft.TextButton]) -> None:
        if not self.busy:
            self.owner.pop_dialog()
