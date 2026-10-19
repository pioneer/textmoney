import asyncio
import logging
from concurrent.futures import Future

import flet as ft
from finance_saf import FinanceSaf

from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder, unwrap
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import DesktopPreferences
from app.storage import SAMPLE, StorageError, StorageSession

logger = logging.getLogger(__name__)
FOLDER_KEY = "txncount.diagnostics.folder.v1"


class StorageView(ft.Column):
    def __init__(self) -> None:
        super().__init__(spacing=12)
        self.session: StorageSession | None = None
        self.preferences: ft.SharedPreferences | DesktopPreferences | None = None
        self.bridge: FinanceSaf | None = None
        self.worker: Future[None] | None = None
        self.lock = asyncio.Lock()
        self.location = ft.Text("No folder selected.", selectable=True)
        self.access = ft.Text("Access: not checked.")
        self.result = ft.Text(
            "Use an empty test folder, never real financial data.", selectable=True
        )
        self.changes = ft.Text("External changes: no baseline. Read first.")
        self.hash_label = ft.Text("Last-read SHA-256: none", selectable=True)
        self.editor = ft.TextField(
            label="Synthetic test-file draft",
            value=SAMPLE,
            multiline=True,
            min_lines=7,
            max_lines=12,
            on_change=self._edit,
        )
        self.buttons = [
            ft.Button("Select Folder", on_click=self._select_clicked),
            ft.Button("Read", on_click=self._read_clicked),
            ft.Button("Write", on_click=self._write_clicked),
            ft.Button("Backup", on_click=self._backup_clicked),
        ]
        self.controls.extend(
            [
                ft.Text("Storage diagnostics", size=24),
                ft.Text("Only storage-test.beancount and unique backups/ files are written."),
                ft.Row([*self.buttons], wrap=True),
                self.result,
                self.location,
                self.access,
                self.changes,
                self.hash_label,
                self.editor,
                ft.Text("Read replaces the draft. Copy unsaved text before reloading a conflict."),
            ]
        )

    def did_mount(self) -> None:
        self.worker = self.page.run_task(self._lifecycle)

    def will_unmount(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def _edit(self, event: ft.Event[ft.TextField]) -> None:
        if self.session is not None:
            self.session.draft = self.editor.value or ""

    def _refresh(self) -> None:
        if self.session is not None:
            self.changes.value = (
                "External changes: DETECTED; Read before writing. Draft retained."
                if self.session.external_changes
                else "External changes: not detected (5-second polling after Read)."
                if self.session.loaded
                else "External changes: no baseline. Read first."
            )
            self.hash_label.value = (
                f"Last-read SHA-256: {self.session.last_hash or 'missing / none'}"
            )
        self.update()

    def _show_error(self, exc: Exception) -> None:
        if isinstance(exc, ConflictError):
            self.result.value = str(exc)
            logger.warning("Storage action refused: %s", exc)
            self.page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title="Write blocked: external change",
                    content=ft.Text(
                        "The test file changed outside txncount and was not overwritten. "
                        "Your unsaved text is still in the editor.\n\n"
                        "Copy any text you want to keep before tapping Read: "
                        "Read replaces the draft with the external file."
                    ),
                    actions=[ft.TextButton("Keep editing", on_click=self._close_conflict)],
                )
            )
            return
        if isinstance(exc, StorageError):
            self.result.value = str(exc)
            logger.warning("Storage action refused: %s", exc)
        elif isinstance(exc, TimeoutError):
            self.result.value = (
                "Storage timed out; outcome may be uncertain. Inspect test file/backups and Read "
                "before retrying. For picker timeout, select the folder again."
            )
            if self.session is not None:
                self.session.loaded = False
            logger.warning("Storage transport timed out")
        else:
            self.result.value = (
                "Unexpected storage error. See application logs; do not retry Write."
            )
            if self.session is not None:
                self.session.loaded = False
            logger.exception("Unexpected storage failure")
        self.access.value = "Access: last operation failed; check grant, folder, and free space."

    def _close_conflict(self, event: ft.Event[ft.TextButton]) -> None:
        self.page.pop_dialog()

    async def _attach(self, location: str) -> None:
        if self.bridge is not None:
            folder = AndroidFolder(self.bridge, location)
        else:
            folder = DesktopFolder(location)
        status = await folder.status()
        self.session = StorageSession(folder)
        self.session.draft = self.editor.value or ""
        self.location.value = location
        self.access.value = status

    async def _lifecycle(self) -> None:
        if self.page.platform in {
            ft.PagePlatform.LINUX,
            ft.PagePlatform.WINDOWS,
            ft.PagePlatform.MACOS,
        }:
            self.preferences = DesktopPreferences()
        else:
            self.preferences = ft.SharedPreferences()
        if self.page.platform == ft.PagePlatform.ANDROID:
            self.bridge = FinanceSaf()
        try:
            saved = await self.preferences.get(FOLDER_KEY)
            if isinstance(saved, str):
                self.location.value = saved
                await self._attach(saved)
                self.result.value = (
                    "Remembered folder restored. Read to establish a fresh baseline."
                )
        except Exception as exc:
            self._show_error(exc)
        self._refresh()
        while True:
            await asyncio.sleep(5)
            if self.lock.locked() or self.session is None or not self.session.loaded:
                continue
            async with self.lock:
                try:
                    self.access.value = await self.session.folder.status()
                    await self.session.poll()
                except Exception as exc:
                    self._show_error(exc)
                    # Avoid repeating failed provider calls; an explicit Read retries.
                    self.session.loaded = False
                self._refresh()

    async def _operate(self, operation: str) -> None:
        async with self.lock:
            for button in self.buttons:
                button.disabled = True
            self.editor.disabled = True
            self.update()
            try:
                if operation == "select":
                    if self.preferences is None:
                        raise StorageError("Storage services are starting. Try again shortly.")
                    location = (
                        unwrap(await self.bridge.call("select"))
                        if self.bridge is not None
                        else await ft.FilePicker().get_directory_path()
                    )
                    if location is None:
                        self.result.value = "Folder selection cancelled. Existing draft retained."
                    elif isinstance(location, str):
                        await self._attach(location)
                        if not await self.preferences.set(FOLDER_KEY, location):
                            raise StorageError(
                                "Folder selected but could not remember it for restart."
                            )
                        self.result.value = (
                            "Folder selected. Read before Write; existing draft retained."
                        )
                    else:
                        raise StorageError("Invalid folder picker response.")
                else:
                    if self.session is None:
                        raise StorageError("Select a test folder first.")
                    self.access.value = await self.session.folder.status()
                    if operation == "read":
                        exists = await self.session.read()
                        self.editor.value = self.session.draft
                        self.result.value = (
                            "Read successful; draft replaced."
                            if exists
                            else "Test file missing. Baseline recorded; Write will create it."
                        )
                    elif operation == "write":
                        self.session.draft = self.editor.value or ""
                        backup = await self.session.write()
                        self.result.value = (
                            f"Write verified. Backup: {backup or 'new file; not needed'}."
                        )
                    elif operation == "backup":
                        self.result.value = f"Backup verified: {await self.session.backup()}"
            except Exception as exc:
                self._show_error(exc)
            finally:
                for button in self.buttons:
                    button.disabled = False
                self.editor.disabled = False
                self._refresh()

    async def _select_clicked(self, event: ft.Event[ft.Button]) -> None:
        await self._operate("select")

    async def _read_clicked(self, event: ft.Event[ft.Button]) -> None:
        await self._operate("read")

    async def _write_clicked(self, event: ft.Event[ft.Button]) -> None:
        await self._operate("write")

    async def _backup_clicked(self, event: ft.Event[ft.Button]) -> None:
        await self._operate("backup")


def home_view() -> ft.SafeArea:
    from app.ui.finance import FinanceView

    return ft.SafeArea(expand=True, content=FinanceView())
