import logging
from collections.abc import Awaitable, Callable

import flet as ft

from app.infrastructure.ai import MODELS, PROVIDER_LABELS, AIError, AISettings, Capability, Provider
from app.ui.layout import action_toolbar, settings_field

_FUNCTIONS = {
    Capability.UNDERSTAND: "ai_understand",
    Capability.GENERATE: "ai_generate",
    Capability.NOTIFICATION_RULE: "ai_notification_rule",
}
_UNSET = "unset"
logger = logging.getLogger(__name__)


class AISettingsPanel:
    def __init__(
        self,
        translate: Callable[..., str],
        settings: Callable[[], AISettings],
        *,
        notification_rules: Callable[[], bool] = lambda: False,
    ) -> None:
        self.tr = translate
        self.settings = settings
        self.notification_rules = notification_rules
        self.busy = False
        self.available = False
        self.loaded = False
        self.notice: str | None = None
        self.presence: dict[Provider, bool] = {}
        self.saved: dict[Capability, str] = {}
        self.adaptation = True
        self.adapt_icons = ft.Checkbox(value=True, on_change=self._adaptation_changed)
        self.adapt_title = ft.Text()
        self.adapt_help = ft.Text()
        self.adapt_tile = ft.ListTile(
            title=self.adapt_title,
            subtitle=self.adapt_help,
            leading=self.adapt_icons,
            toggle_inputs=True,
        )
        self.title = ft.Text(size=18)
        self.help = ft.Text()
        self.status = ft.Text()
        self.key_status = ft.Text()
        self.provider = ft.Dropdown(
            expand=True,
            value=Provider.OPENAI,
            options=[
                ft.DropdownOption(key=key, text=name) for key, name in PROVIDER_LABELS.items()
            ],
            on_select=self._provider_changed,
        )
        self.key = ft.TextField(
            expand=True, password=True, max_length=4096, on_submit=self._save_key
        )
        self.save = ft.Button(on_click=self._save_key)
        self.remove = ft.OutlinedButton(on_click=self._remove_key)
        self.retry = ft.TextButton(on_click=self._load)
        self.models = {
            capability: ft.Dropdown(expand=True, on_select=self._model_changed, data=capability)
            for capability in _FUNCTIONS
        }
        self.model_status = {capability: ft.Text() for capability in _FUNCTIONS}
        self.model_rows = {
            capability: settings_field(ft.Row([self.models[capability]]))
            for capability in _FUNCTIONS
        }
        self.form = ft.Column(
            [
                settings_field(ft.Row([self.provider])),
                self.key_status,
                settings_field(ft.Row([self.key])),
                action_toolbar(self.save, self.remove),
                ft.Divider(),
                *(
                    item
                    for capability in _FUNCTIONS
                    for item in (self.model_rows[capability], self.model_status[capability])
                ),
                ft.Divider(),
                self.adapt_tile,
            ],
            visible=False,
            # ExpansionTile stacks children without spacing; keep the dropdown's
            # floating label clear of the help text above.
            margin=ft.Margin.only(top=8),
        )
        self.control = ft.ExpansionTile(
            title=self.title,
            controls=[self.help, self.status, self.retry, self.form],
            controls_padding=ft.Padding.all(16),
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
            on_change=self._expanded,
        )

    def clear_draft(self) -> None:
        if self.key.value:
            self.key.value = ""

    def render(self) -> None:
        self.title.value = self.tr("ai_settings")
        self.help.value = self.tr("ai_settings_help")
        self.provider.label = self.tr("ai_provider")
        self.key.label = self.tr("ai_key")
        self.save.content = self.tr("ai_save_key")
        self.remove.content = self.tr("ai_remove_key")
        self.retry.content = self.tr("ai_retry_settings")
        status = "ai_loading" if self.busy else self.notice
        self.status.value = self.tr(status) if status else ""
        self.status.visible = bool(status)
        self.retry.visible = self.loaded and not self.available and not self.busy
        self.form.visible = self.available
        has_key = any(
            provider == self.provider.value and present
            for provider, present in self.presence.items()
        )
        self.key_status.value = self.tr("ai_key_saved" if has_key else "ai_key_missing")
        self.provider.disabled = self.busy
        self.key.disabled = self.busy
        self.save.disabled = self.busy
        self.remove.disabled = self.busy or not has_key
        self.adapt_title.value = self.tr("ai_adapt_icons")
        self.adapt_help.value = self.tr("ai_adapt_icons_help")
        self.adapt_icons.value = self.adaptation
        self.adapt_icons.semantics_label = self.tr("ai_adapt_icons")
        self.adapt_icons.disabled = self.busy
        self.adapt_tile.disabled = self.busy
        for capability, dropdown in self.models.items():
            self.model_rows[capability].visible = (
                capability != Capability.NOTIFICATION_RULE or self.notification_rules()
            )
            saved = self.saved.get(capability, "")
            options = [ft.DropdownOption(key=_UNSET, text=self.tr("ai_unconfigured"))]
            models = MODELS[capability]
            options.extend(ft.DropdownOption(key=model.id, text=model.label) for model in models)
            selected = next((model for model in models if model.id == saved), None)
            notice = self.model_status[capability]
            if saved and selected is None:
                options.append(ft.DropdownOption(key=saved, text=self.tr("ai_model_unavailable")))
                notice.value = self.tr("ai_model_unavailable")
            elif selected and not self.presence.get(selected.provider):
                notice.value = self.tr("ai_model_needs_key")
            else:
                notice.value = ""
            notice.visible = bool(notice.value) and self.model_rows[capability].visible
            if [(o.key, o.text) for o in dropdown.options] != [(o.key, o.text) for o in options]:
                dropdown.options = options
            dropdown.label = self.tr(_FUNCTIONS[capability])
            dropdown.value = saved or _UNSET
            dropdown.disabled = self.busy

    async def _run(self, action: Callable[[AISettings], Awaitable[None]]) -> None:
        if self.busy:
            return
        self.notice = None
        self.busy = True
        self.render()
        if self.control.parent is not None:
            self.control.update()
        try:
            await action(self.settings())
        except AIError as exc:
            logger.warning("AI settings operation failed (code=%s)", exc.code)
            self.notice = exc.code
        finally:
            self.busy = False
            self.loaded = True
            self.render()
            if self.control.parent is not None:
                self.control.update()

    async def _load(self, event: ft.Event) -> None:
        async def load(settings: AISettings) -> None:
            saved = {capability: await settings.selection(capability) for capability in _FUNCTIONS}
            presence = {provider: await settings.has_key(provider) for provider in Provider}
            adaptation = await settings.adapt_generated_icons()
            self.adaptation = adaptation
            self.saved, self.presence, self.available = saved, presence, True

        await self._run(load)

    async def _expanded(self, event: ft.Event[ft.ExpansionTile]) -> None:
        if event.data is True or event.data == "true":
            await self._load(event)
        else:
            self.clear_draft()

    async def _provider_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self.clear_draft()
        self.notice = None
        self.render()

    async def _save_key(self, event: ft.Event) -> None:
        if self.busy or not self.available:
            return
        provider, key = Provider(self.provider.value), self.key.value or ""
        self.clear_draft()

        async def save(settings: AISettings) -> None:
            await settings.save_key(provider, key)
            self.presence[provider] = True
            self.notice = "ai_key_saved"

        await self._run(save)

    async def _remove_key(self, event: ft.Event) -> None:
        if self.busy or not self.available:
            return
        provider = Provider(self.provider.value)
        self.clear_draft()

        async def remove(settings: AISettings) -> None:
            await settings.remove_key(provider)
            self.presence[provider] = False
            self.notice = "ai_key_removed"

        await self._run(remove)

    async def _model_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        if self.busy or not self.available:
            return
        capability: Capability = event.control.data
        selection = event.control.value or _UNSET
        model_id = "" if selection == _UNSET else selection
        if model_id == self.saved.get(capability, ""):
            return

        async def select(settings: AISettings) -> None:
            await settings.select(capability, model_id)
            self.saved[capability] = model_id
            self.notice = "ai_model_saved"

        await self._run(select)

    async def _adaptation_changed(self, event: ft.Event[ft.Checkbox]) -> None:
        if self.busy or not self.available:
            return
        enabled = self.adapt_icons.value is True

        async def save(settings: AISettings) -> None:
            await settings.set_adapt_generated_icons(enabled)
            self.adaptation = enabled
            self.notice = "ai_adapt_icons_saved"

        await self._run(save)
