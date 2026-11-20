import asyncio
import base64
import json
from io import BytesIO

import flet as ft
import httpx
import pytest
from PIL import Image

from app.infrastructure.ai import MODELS, AIError, AIService, AISettings, Capability, Provider
from app.infrastructure.preferences import DesktopPreferences
from app.localization import translate
from app.ui.ai_settings import AISettingsPanel


class FakeCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def contains_key(self, key: str) -> bool:
        return key in self.values

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def remove(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture
def photo() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "red").save(stream, format="PNG")
    return stream.getvalue()


def test_ai_settings_panel_persists_independent_selections_and_removes_keys(tmp_path):
    preferences = DesktopPreferences(tmp_path / "settings.json")
    credentials = FakeCredentials()
    settings = AISettings(preferences, credentials)
    panel = AISettingsPanel(lambda key: translate("en", key), lambda: settings)

    async def exercise():
        event = ft.Event(name="change", control=panel.control, data=True)
        assert panel.control.on_change is not None
        await panel.control.on_change(event)  # type: ignore[misc]
        assert panel.form.visible
        assert panel.key.password and not panel.key.value

        assert panel.save.on_click is not None and panel.remove.on_click is not None
        for key in ("test-first-key", "test-replacement-key"):
            panel.key.value = key
            await panel.save.on_click(ft.Event(name="click", control=panel.save))  # type: ignore[misc]
            assert panel.key.value == ""

        selections = {
            Capability.UNDERSTAND: "anthropic:claude-sonnet-5-5",
            Capability.GENERATE: "openai:gpt-image-2.5-flare",
        }
        for capability, model in selections.items():
            dropdown = panel.models[capability]
            assert model in {option.key for option in dropdown.options}
            assert next(iter(set(selections.values()) - {model})) not in {
                option.key for option in dropdown.options
            }
            dropdown.value = model
            assert dropdown.on_select is not None
            await dropdown.on_select(ft.Event(name="select", control=dropdown))  # type: ignore[misc]

        reopened = AISettings(DesktopPreferences(preferences.path), credentials)
        assert (await reopened.resolve(Capability.GENERATE))[1] == "test-replacement-key"
        assert await reopened.selection(Capability.UNDERSTAND) == selections[Capability.UNDERSTAND]
        await panel.remove.on_click(ft.Event(name="click", control=panel.remove))  # type: ignore[misc]
        assert panel.model_status[Capability.GENERATE].visible
        assert await reopened.selection(Capability.GENERATE) == selections[Capability.GENERATE]
        with pytest.raises(AIError, match="ai_key_missing"):
            await reopened.resolve(Capability.GENERATE)
        assert "test-" not in preferences.path.read_text()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("provider", "path", "response"),
    [
        (
            Provider.OPENAI,
            "/v1/responses",
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "red"}]}]},
        ),
        (
            Provider.XAI,
            "/v1/responses",
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "red"}]}]},
        ),
        (Provider.ANTHROPIC, "/v1/messages", {"content": [{"type": "text", "text": "red"}]}),
        (
            Provider.GEMINI,
            "/v1beta/models/gemini-2.5-flash:generateContent",
            {"candidates": [{"content": {"parts": [{"text": "red"}]}}]},
        ),
        (Provider.MISTRAL, "/v1/chat/completions", {"choices": [{"message": {"content": "red"}}]}),
        (Provider.DEEPSEEK, "/chat/completions", {"choices": [{"message": {"content": "red"}}]}),
    ],
)
def test_understanding_adapters(tmp_path, photo, provider, path, response):
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), FakeCredentials())
    model = next(model for model in MODELS[Capability.UNDERSTAND] if model.provider == provider)
    encoded = base64.b64encode(photo).decode()

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path and request.url.query == b""
        body = json.loads(request.content)
        if provider == Provider.GEMINI:
            assert request.headers["x-goog-api-key"] == "test-key"
            assert body["contents"][0]["parts"][1]["inlineData"] == {
                "mimeType": "image/png",
                "data": encoded,
            }
        elif provider == Provider.ANTHROPIC:
            assert request.headers["x-api-key"] == "test-key"
            assert body["messages"][0]["content"][1]["source"]["data"] == encoded
        else:
            assert request.headers["authorization"] == "Bearer test-key"
            if provider in {Provider.OPENAI, Provider.XAI}:
                assert body["store"] is False
                assert (
                    body["input"][0]["content"][1]["image_url"]
                    == f"data:image/png;base64,{encoded}"
                )
            else:
                assert body["messages"][0]["content"][1]["image_url"]["url"] == (
                    f"data:image/png;base64,{encoded}"
                )
        return httpx.Response(200, json=response)

    async def exercise():
        await settings.select(Capability.UNDERSTAND, model.id)
        await settings.save_key(provider, "test-key")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        assert await service.understand_image(photo, "image/png", "What color?") == "red"

    asyncio.run(exercise())


@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.GEMINI, Provider.XAI])
def test_generation_adapters_return_image_bytes(tmp_path, photo, provider):
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), FakeCredentials())
    model = next(model for model in MODELS[Capability.GENERATE] if model.provider == provider)
    encoded = base64.b64encode(photo).decode()

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if provider == Provider.GEMINI:
            assert request.url.path.endswith(f"/{model.name}:generateContent")
            assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"inlineData": {"data": encoded, "mimeType": "image/png"}}
                                ]
                            }
                        }
                    ]
                },
            )
        assert request.url.host == ("api.x.ai" if provider == Provider.XAI else "api.openai.com")
        assert request.url.path == "/v1/images/generations"
        assert body["model"] == model.name
        if provider == Provider.XAI:
            assert body["response_format"] == "b64_json"
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    async def exercise():
        await settings.select(Capability.GENERATE, model.id)
        await settings.save_key(provider, "test-key")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        image = await service.generate_image("Draw a red square")
        assert (image.data, image.media_type) == (photo, "image/png")

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "invalid_credentials"),
        (403, "provider_access_denied"),
        (404, "ai_model_unavailable"),
        (429, "provider_rate_limit"),
        (200, "invalid_provider_response"),
        (0, "provider_timeout"),
    ],
)
def test_generation_failures_are_safe(tmp_path, status, code):
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), FakeCredentials())

    def handle(request: httpx.Request) -> httpx.Response:
        if not status:
            raise httpx.ReadTimeout("private-provider-details", request=request)
        return httpx.Response(status, json={"error": "private-provider-details"})

    async def exercise():
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        await settings.save_key(Provider.OPENAI, "test-key")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        with pytest.raises(AIError, match=code) as exc:
            await service.generate_image("private prompt")
        assert "private" not in str(exc.value)
        assert "test-key" not in str(exc.value)

    asyncio.run(exercise())


def test_structured_response_validates_and_rejects_bad_inputs_before_network(tmp_path, photo):
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), FakeCredentials())
    replies = iter(['{"color":"red"}', '{"color":1}'])

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "Return only JSON" in body["messages"][0]["content"][0]["text"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": next(replies)}]})

    def color(value: object) -> str:
        if not isinstance(value, dict) or not isinstance(value.get("color"), str):
            raise ValueError("private validation detail")
        return value["color"]

    async def exercise():
        with pytest.raises(AIError, match="ai_model_unavailable"):
            await settings.select(Capability.GENERATE, "anthropic:claude-sonnet-5-5")
        await settings.select(Capability.UNDERSTAND, "anthropic:claude-sonnet-5-5")
        await settings.save_key(Provider.ANTHROPIC, "test-key")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        with pytest.raises(AIError, match="invalid_image"):
            await service.understand_image(photo, "image/jpeg", "What color?")
        schema = {"type": "object", "properties": {"color": {"type": "string"}}}
        assert (
            await service.understand_structured(
                photo, "image/png", "What color?", schema=schema, validate=color
            )
            == "red"
        )
        with pytest.raises(AIError, match="invalid_structured_output"):
            await service.understand_structured(
                photo, "image/png", "What color?", schema=schema, validate=color
            )

    asyncio.run(exercise())
