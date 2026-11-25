"""Explicit, editable single-sample consent for notification rule drafting."""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from app.domain.notification_rules import MAX_NOTIFICATION_LENGTH, Rule
from app.infrastructure.ai import AIError, AIService, Model
from app.infrastructure.notification_ai import draft_notification_rule
from app.ui.dialogs import EditorDialog, show_dialog

logger = logging.getLogger(__name__)

_ERRORS = {
    "ai_unconfigured": "ai_unconfigured",
    "ai_model_unavailable": "ai_model_unavailable",
    "ai_model_changed": "notification_ai_model_changed",
    "ai_key_missing": "ai_key_missing",
    "ai_settings_error": "ai_settings_error",
    "invalid_notification": "notification_ai_sample_required",
    "invalid_credentials": "icon_ai_credentials_error",
    "provider_access_denied": "icon_ai_credentials_error",
    "provider_rate_limit": "icon_ai_rate_error",
    "provider_timeout": "icon_ai_network_error",
    "provider_network_error": "icon_ai_network_error",
    "provider_refusal": "notification_ai_refused",
    "invalid_structured_output": "notification_ai_invalid",
}


class NotificationAIConsent:
    def __init__(
        self,
        page: ft.Page,
        translate: Callable[..., str],
        service: AIService,
        model: Model,
        app_id: str,
        *,
        title: str | None,
        body: str | None,
        on_draft: Callable[[Rule, str | None, str | None], None],
    ) -> None:
        self.page = page
        self.tr = translate
        self.service = service
        self.model = model
        self.app_id = app_id
        self.on_draft = on_draft
        self.request: asyncio.Task[object] | None = None
        self.title = ft.TextField(
            label=self.tr("notification_rule_sample_title"),
            value=title or "",
            max_length=MAX_NOTIFICATION_LENGTH,
        )
        self.body = ft.TextField(
            label=self.tr("notification_rule_sample_body"),
            value=body or "",
            multiline=True,
            min_lines=2,
            max_lines=6,
            max_length=MAX_NOTIFICATION_LENGTH,
        )
        self.status = ft.Text(color=ft.Colors.ERROR, selectable=True)
        self.progress = ft.ProgressRing(width=20, height=20, visible=False)
        self.cancel = ft.TextButton(self.tr("crud_cancel"), on_click=self._cancel)
        self.send = ft.TextButton(self.tr("notification_ai_send"), on_click=self._send)
        self.form = ft.Column(
            [
                ft.Text(self.tr("notification_ai_consent", model=model.label), selectable=True),
                self.title,
                self.body,
                self.progress,
                self.status,
            ],
            spacing=12,
        )
        self.dialog: EditorDialog | None = None

    def open(self) -> EditorDialog:
        self.dialog = EditorDialog(
            self.tr("notification_ai_title"),
            self.form,
            [self.cancel, self.send],
            compact=lambda: (self.page.width or 600) <= 599,
            cancel_on_back=self._close,
        )
        show_dialog(self.page, self.dialog)
        return self.dialog

    def _close(self) -> None:
        if self.request is not None and not self.request.done():
            self.request.cancel()
        self.page.pop_dialog()

    async def _cancel(self, event: ft.Event) -> None:
        self._close()

    async def _send(self, event: ft.Event) -> None:
        if self.request is not None:
            return
        title, body = self.title.value or None, self.body.value or None
        self.request = asyncio.current_task()
        self.send.disabled = True
        self.title.disabled = True
        self.body.disabled = True
        self.progress.visible = True
        self.status.value = ""
        if self.dialog and self.dialog.parent is not None:
            self.dialog.update()
        try:
            rule = await draft_notification_rule(
                self.service,
                app_id=self.app_id,
                title=title,
                body=body,
                confirmed=True,
                expected_model_id=self.model.id,
            )
        except AIError as exc:
            logger.warning(
                "Notification rule draft failed (code=%s, provider=%s)", exc.code, exc.provider
            )
            self.status.value = self.tr(_ERRORS.get(exc.code, "notification_ai_failed"))
        else:
            self.request = None
            self.page.pop_dialog()
            self.on_draft(rule, title, body)
        finally:
            self.request = None
            self.send.disabled = False
            self.title.disabled = False
            self.body.disabled = False
            self.progress.visible = False
            if self.dialog and self.dialog.parent is not None:
                self.dialog.update()
