import asyncio
import logging

import flet as ft

logger = logging.getLogger(__name__)


def _run_bundled_experiment() -> str:
    # Keep parser imports/failures isolated from the already tested storage screen.
    from app.beancount_diagnostics import run_diagnostics

    return run_diagnostics().display()


class BeancountDiagnosticsView(ft.Column):
    def __init__(self) -> None:
        super().__init__(spacing=8)
        self.result = ft.Text(
            "Parser experiment not run. Bundled synthetic files only.", selectable=True
        )
        self.button = ft.Button("Run Beancount diagnostics", on_click=self._run)
        self.controls.extend([self.button, self.result])

    async def _run(self, event: ft.Event[ft.Button]) -> None:
        if self.button.disabled:
            return
        self.button.disabled = True
        self.result.value = "Running parser, Decimal and lossless editing checks..."
        self.update()
        try:
            self.result.value = await asyncio.to_thread(_run_bundled_experiment)
        except Exception as exc:
            logger.exception("Bundled parser experiment failed unexpectedly")
            self.result.value = (
                f"FAIL: parser experiment raised {type(exc).__name__}. "
                "See application logs. Storage files were not accessed."
            )
        finally:
            self.button.disabled = False
            self.update()
