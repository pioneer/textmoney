"""Local provider configuration and direct, asynchronous AI requests."""

import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from typing import Protocol

import httpx
from PIL import Image

from app.infrastructure.preferences import PreferencesError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class Capability(StrEnum):
    UNDERSTAND = "understand"
    GENERATE = "generate"
    NOTIFICATION_RULE = "notification_rule"


class Provider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"
    XAI = "xai"
    ANTHROPIC = "anthropic"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"


PROVIDER_LABELS = {
    Provider.OPENAI: "OpenAI",
    Provider.GEMINI: "Google Gemini",
    Provider.XAI: "xAI",
    Provider.ANTHROPIC: "Anthropic Claude",
    Provider.MISTRAL: "Mistral",
    Provider.DEEPSEEK: "DeepSeek",
}


@dataclass(frozen=True)
class Model:
    provider: Provider
    name: str

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.name}"

    @property
    def label(self) -> str:
        return f"{PROVIDER_LABELS[self.provider]}: {self.name}"


MODELS = {
    Capability.UNDERSTAND: (
        Model(Provider.OPENAI, "gpt-5.4-mini"),
        Model(Provider.OPENAI, "gpt-5.4"),
        Model(Provider.GEMINI, "gemini-2.5-flash"),
        Model(Provider.GEMINI, "gemini-2.5-pro"),
        Model(Provider.XAI, "grok-4.7"),
        Model(Provider.ANTHROPIC, "claude-sonnet-5-5"),
        Model(Provider.ANTHROPIC, "claude-haiku-5-5"),
        Model(Provider.MISTRAL, "mistral-medium-2508"),
        Model(Provider.MISTRAL, "mistral-small-2506"),
        Model(Provider.DEEPSEEK, "deepseek-flash"),
    ),
    Capability.GENERATE: (
        Model(Provider.OPENAI, "gpt-image-2.5-flare"),
        Model(Provider.OPENAI, "gpt-image-2.5-sunburst"),
        Model(Provider.GEMINI, "gemini-3.1-flash-image"),
        Model(Provider.GEMINI, "gemini-3-pro-image"),
        Model(Provider.XAI, "grok-imagine-image-2.0"),
    ),
    Capability.NOTIFICATION_RULE: (Model(Provider.OPENAI, "gpt-5.4-mini"),),
}


class AIError(Exception):
    def __init__(self, code: str, *, provider: Provider | None = None, status: int | None = None):
        self.code = code
        self.provider = provider
        self.status = status
        super().__init__(" / ".join(str(item) for item in (code, provider, status) if item))


class Preferences(Protocol):
    async def get(self, key: str) -> object: ...
    async def set(self, key: str, value: str) -> bool: ...


class Credentials(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def contains_key(self, key: str) -> bool: ...
    async def set(self, key: str, value: str) -> None: ...
    async def remove(self, key: str) -> None: ...


def _key_name(provider: Provider) -> str:
    return f"textmoney.ai.key.{provider}"


class AISettings:
    def __init__(self, preferences: Preferences, credentials: Credentials):
        self.preferences = preferences
        self.credentials = credentials

    async def adapt_generated_icons(self) -> bool:
        try:
            value = await self.preferences.get("textmoney.ai.adapt_generated_icons")
        except (OSError, RuntimeError, PreferencesError) as exc:
            raise AIError("ai_settings_error") from exc
        if value is None:
            return True
        if value not in ("true", "false"):
            raise AIError("ai_settings_error")
        return value == "true"

    async def set_adapt_generated_icons(self, enabled: bool) -> None:
        try:
            saved = await self.preferences.set(
                "textmoney.ai.adapt_generated_icons", "true" if enabled else "false"
            )
        except (OSError, RuntimeError, PreferencesError) as exc:
            raise AIError("ai_settings_error") from exc
        if not saved:
            raise AIError("ai_settings_error")

    async def selection(self, capability: Capability) -> str:
        try:
            value = await self.preferences.get(f"textmoney.ai.model.{capability}")
        except (OSError, RuntimeError, PreferencesError):
            raise AIError("ai_settings_error") from None
        if value is None:
            return ""
        if not isinstance(value, str):
            raise AIError("ai_settings_error")
        return value

    async def select(self, capability: Capability, model_id: str) -> None:
        if model_id and not any(m.id == model_id for m in MODELS[capability]):
            raise AIError("ai_model_unavailable")
        try:
            saved = await self.preferences.set(f"textmoney.ai.model.{capability}", model_id)
        except (OSError, RuntimeError, PreferencesError):
            raise AIError("ai_settings_error") from None
        if not saved:
            raise AIError("ai_settings_error")

    async def has_key(self, provider: Provider) -> bool:
        try:
            return await self.credentials.contains_key(_key_name(provider))
        except (OSError, RuntimeError):
            raise AIError("ai_settings_error", provider=provider) from None

    async def save_key(self, provider: Provider, key: str) -> None:
        key = key.strip()
        if not key or len(key) > 4096 or any(not 33 <= ord(c) <= 126 for c in key):
            raise AIError("ai_invalid_key")
        try:
            await self.credentials.set(_key_name(provider), key)
        except (OSError, RuntimeError):
            raise AIError("ai_settings_error", provider=provider) from None

    async def remove_key(self, provider: Provider) -> None:
        try:
            await self.credentials.remove(_key_name(provider))
        except (OSError, RuntimeError):
            raise AIError("ai_settings_error", provider=provider) from None

    async def resolve(self, capability: Capability) -> tuple[Model, str]:
        model_id = await self.selection(capability)
        if not model_id:
            raise AIError("ai_unconfigured")
        model = next((model for model in MODELS[capability] if model.id == model_id), None)
        if model is None:
            raise AIError("ai_model_unavailable")
        try:
            key = await self.credentials.get(_key_name(model.provider))
        except (OSError, RuntimeError):
            raise AIError("ai_settings_error", provider=model.provider) from None
        if not key:
            raise AIError("ai_key_missing", provider=model.provider)
        return model, key


@dataclass(frozen=True, repr=False)
class GeneratedImage:
    data: bytes
    media_type: str


_CHAT = {
    Provider.MISTRAL: "https://api.mistral.ai/v1/chat/completions",
    Provider.DEEPSEEK: "https://api.deepseek.com/chat/completions",
}
_OPENAI = "https://api.openai.com/v1"
_XAI = "https://api.x.ai/v1"
_ANTHROPIC = "https://api.anthropic.com/v1/messages"
_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"


def _image_type(data: bytes) -> str:
    with Image.open(BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
        if image.width * image.height > 20_000_000:
            raise ValueError("Image too large")
        media_type = image.get_format_mimetype()
        image.verify()
    if media_type not in IMAGE_TYPES:
        raise ValueError("Unsupported image format")
    return media_type


def _valid_prompt(prompt: str) -> None:
    if not prompt.strip() or len(prompt) > 32_000:
        raise AIError("invalid_prompt")


def _text(parts: object) -> str:
    if not isinstance(parts, list):
        raise ValueError
    values = [
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    result = "\n".join(values).strip()
    if not result:
        raise ValueError
    return result


def _gemini_parts(data: dict, provider: Provider) -> list:
    if data.get("promptFeedback", {}).get("blockReason"):
        raise AIError("provider_refusal", provider=provider)
    candidate = data["candidates"][0]
    if candidate.get("finishReason") in {
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "RECITATION",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }:
        raise AIError("provider_refusal", provider=provider)
    return candidate["content"]["parts"]


async def _bad_request_code(response: httpx.Response) -> str:
    # Inspect only documented machine codes, never provider messages or payloads.
    payload = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=8192):
        payload.extend(chunk)
        if len(payload) > 8192:
            return "provider_request_failed"
    try:
        error = json.loads(payload)["error"]
        if error.get("code") in ("moderation_blocked", "content_policy_violation"):
            return "provider_refusal"
        if any(detail.get("reason") == "API_KEY_INVALID" for detail in error.get("details", [])):
            return "invalid_credentials"
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return "provider_request_failed"


class AIService:
    def __init__(self, settings: AISettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def understand_image(self, image: bytes, media_type: str, prompt: str) -> str:
        _valid_prompt(prompt)
        if not image or len(image) > MAX_IMAGE_BYTES or media_type not in IMAGE_TYPES:
            raise AIError("invalid_image")
        try:
            detected_type = await asyncio.to_thread(_image_type, image)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise AIError("invalid_image") from None
        if detected_type != media_type:
            raise AIError("invalid_image")
        model, key = await self.settings.resolve(Capability.UNDERSTAND)
        return await self._understand(model, key, image, media_type, prompt)

    async def understand_structured[T](
        self,
        image: bytes,
        media_type: str,
        prompt: str,
        *,
        schema: dict[str, object],
        validate: Callable[[object], T],
    ) -> T:
        """Request JSON and validate locally. Validators must raise ValueError on invalid data."""
        instructions = f"{prompt}\nReturn only JSON matching this schema:\n{json.dumps(schema)}"
        text = await self.understand_image(image, media_type, instructions)
        try:
            return validate(json.loads(text))
        except ValueError:
            raise AIError("invalid_structured_output") from None

    async def _understand(
        self, model: Model, key: str, image: bytes, media_type: str, prompt: str
    ) -> str:
        provider = model.provider
        encoded = base64.b64encode(image).decode("ascii")
        data_url = f"data:{media_type};base64,{encoded}"
        if provider == Provider.GEMINI:
            data = await self._post(
                model,
                key,
                f"{_GEMINI}/{model.name}:generateContent",
                {
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt},
                                {"inlineData": {"mimeType": media_type, "data": encoded}},
                            ]
                        }
                    ]
                },
            )
        elif provider == Provider.ANTHROPIC:
            data = await self._post(
                model,
                key,
                _ANTHROPIC,
                {
                    "model": model.name,
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": encoded,
                                    },
                                },
                            ],
                        },
                    ],
                },
            )
        elif provider in {Provider.OPENAI, Provider.XAI}:
            endpoint = _OPENAI if provider == Provider.OPENAI else _XAI
            data = await self._post(
                model,
                key,
                f"{endpoint}/responses",
                {
                    "model": model.name,
                    "store": False,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image", "image_url": data_url, "detail": "high"},
                            ],
                        },
                    ],
                },
            )
        else:
            data = await self._post(
                model,
                key,
                _CHAT[provider],
                {
                    "model": model.name,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        },
                    ],
                },
            )
        try:
            if provider == Provider.GEMINI:
                return _text(_gemini_parts(data, provider))
            if provider == Provider.ANTHROPIC:
                if data.get("stop_reason") == "refusal":
                    raise AIError("provider_refusal", provider=provider)
                return _text(data["content"])
            if provider in {Provider.OPENAI, Provider.XAI}:
                parts = [
                    part
                    for item in data["output"]
                    if item.get("type") == "message"
                    for part in item["content"]
                ]
                if any(part["type"] == "refusal" for part in parts):
                    raise AIError("provider_refusal", provider=provider)
                return _text(parts)
            choice = data["choices"][0]
            if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
                raise AIError("provider_refusal", provider=provider)
            content = choice["message"]["content"]
            return _text([{"text": content}] if isinstance(content, str) else content)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            raise AIError("invalid_provider_response", provider=provider) from None

    async def generate_image(
        self, prompt: str, *, reference_image: bytes | None = None
    ) -> GeneratedImage:
        _valid_prompt(prompt)
        media_type = ""
        if reference_image is not None:
            if not reference_image or len(reference_image) > MAX_IMAGE_BYTES:
                raise AIError("invalid_image")
            try:
                media_type = await asyncio.to_thread(_image_type, reference_image)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                raise AIError("invalid_image") from None
        model, key = await self.settings.resolve(Capability.GENERATE)
        provider = model.provider
        if provider == Provider.GEMINI:
            request_parts: list[dict[str, object]] = [{"text": prompt}]
            if reference_image is not None:
                request_parts.append(
                    {
                        "inlineData": {
                            "mimeType": media_type,
                            "data": base64.b64encode(reference_image).decode("ascii"),
                        }
                    }
                )
            data = await self._post(
                model,
                key,
                f"{_GEMINI}/{model.name}:generateContent",
                {
                    "contents": [{"parts": request_parts}],
                    "generationConfig": {"responseModalities": ["IMAGE"]},
                },
            )
        else:
            endpoint = _OPENAI if provider == Provider.OPENAI else _XAI
            options = {"response_format": "b64_json"} if provider == Provider.XAI else {}
            body: dict = {"model": model.name, "prompt": prompt, **options}
            operation = "generations"
            if reference_image is not None:
                encoded_image = base64.b64encode(reference_image).decode("ascii")
                data_url = f"data:{media_type};base64,{encoded_image}"
                operation = "edits"
                if provider == Provider.OPENAI:
                    body["images"] = [{"image_url": data_url}]
                else:
                    body["image"] = {"url": data_url, "type": "image_url"}
            data = await self._post(
                model,
                key,
                f"{endpoint}/images/{operation}",
                body,
            )
        try:
            if provider == Provider.GEMINI:
                parts = _gemini_parts(data, provider)
                encoded = next(part["inlineData"]["data"] for part in parts if "inlineData" in part)
            else:
                encoded = data["data"][0]["b64_json"]
            image = base64.b64decode(encoded, validate=True)
            return GeneratedImage(image, await asyncio.to_thread(_image_type, image))
        except (
            AttributeError,
            IndexError,
            KeyError,
            OSError,
            StopIteration,
            SyntaxError,
            TypeError,
            ValueError,
            Image.DecompressionBombError,
        ):
            raise AIError("invalid_provider_response", provider=provider) from None

    async def _post(self, model: Model, key: str, url: str, body: dict) -> dict:
        provider = model.provider
        if provider == Provider.GEMINI:
            headers = {"x-goog-api-key": key}
        elif provider == Provider.ANTHROPIC:
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {key}"}
        try:
            async with (
                asyncio.timeout(180),
                httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    timeout=httpx.Timeout(120, connect=10),
                ) as client,
                client.stream("POST", url, headers=headers, json=body) as response,
            ):
                if not response.is_success:
                    codes = {
                        401: "invalid_credentials",
                        403: "provider_access_denied",
                        404: "ai_model_unavailable",
                        429: "provider_rate_limit",
                    }
                    status = response.status_code
                    code = (
                        await _bad_request_code(response)
                        if status == 400
                        else codes.get(status, "provider_request_failed")
                    )
                    raise AIError(
                        code,
                        provider=provider,
                        status=status,
                    )
                payload = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    payload.extend(chunk)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise AIError("invalid_provider_response", provider=provider)
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (TimeoutError, httpx.TimeoutException):
            raise AIError("provider_timeout", provider=provider) from None
        except httpx.RequestError:
            raise AIError("provider_network_error", provider=provider) from None
        except ValueError:
            raise AIError("invalid_provider_response", provider=provider) from None
