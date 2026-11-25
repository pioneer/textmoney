import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import httpx
import pytest

import app.ui.notification_ai as notification_ai_ui
from app.domain.notification_rules import Pattern, Rule
from app.infrastructure.ai import AIError, AIService, AISettings, Capability, Model, Provider
from app.infrastructure.notification_ai import draft_notification_rule
from app.infrastructure.preferences import DesktopPreferences
from app.ui.notification_ai import NotificationAIConsent


class Credentials:
    async def get(self, key: str) -> str:
        return "test-key"

    async def contains_key(self, key: str) -> bool:
        return True

    async def set(self, key: str, value: str) -> None:
        pass

    async def remove(self, key: str) -> None:
        pass


def test_ai_draft_requires_confirmation_and_returns_disabled_rule(tmp_path) -> None:
    requests: list[dict] = []
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), Credentials())

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        request_body = json.loads(request.content)
        requests.append(request_body)
        response = {
            "name": "Declined payments",
            "action": "ignore",
            "title": None,
            "body": "Payment {amount} declined",
            "mapping": None,
        }
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(response)}],
                    }
                ]
            },
        )

    async def exercise() -> None:
        await settings.select(Capability.NOTIFICATION_RULE, "openai:gpt-5.4-mini")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        with pytest.raises(AIError, match="notification_consent_required"):
            await draft_notification_rule(
                service,
                app_id="bank.app",
                title="Bank",
                body="Payment 5 EUR declined",
                confirmed=False,
                expected_model_id="openai:gpt-5.4-mini",
            )
        assert not requests
        with pytest.raises(AIError, match="ai_model_changed"):
            await draft_notification_rule(
                service,
                app_id="bank.app",
                title="Bank",
                body="Payment 5 EUR declined",
                confirmed=True,
                expected_model_id="openai:another-model",
            )
        assert not requests
        rule = await draft_notification_rule(
            service,
            app_id="bank.app",
            title="Bank",
            body="Payment 5 EUR declined",
            confirmed=True,
            expected_model_id="openai:gpt-5.4-mini",
        )
        assert rule.name == "Declined payments" and not rule.enabled and rule.app_id == "bank.app"
        assert rule.action == "ignore"
        assert requests[0]["store"] is False
        assert json.loads(requests[0]["input"][1]["content"][0]["text"]) == {
            "title": "Bank",
            "body": "Payment 5 EUR declined",
        }
        assert "bank.app" not in json.dumps(requests[0])

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        '{"name":"X","name":"Y","action":"ignore","title":"{x}","body":null,"mapping":null}',
        json.dumps(
            {"name": "X", "action": "ignore", "title": "{x}{y}", "body": None, "mapping": None}
        ),
        json.dumps(
            {
                "name": "X",
                "action": "ignore",
                "title": "{x}",
                "body": None,
                "mapping": None,
                "app_id": "other",
            }
        ),
    ],
)
def test_ai_invalid_rules_are_rejected_without_changing_configuration(
    tmp_path, answer: str
) -> None:
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), Credentials())

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": answer}]}
                ]
            },
        )

    async def exercise() -> None:
        await settings.select(Capability.NOTIFICATION_RULE, "openai:gpt-5.4-mini")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        with pytest.raises(AIError, match="invalid_structured_output"):
            await draft_notification_rule(
                service,
                app_id="bank.app",
                title=None,
                body="declined",
                confirmed=True,
                expected_model_id="openai:gpt-5.4-mini",
            )
        assert await settings.selection(Capability.NOTIFICATION_RULE) == "openai:gpt-5.4-mini"
        assert await settings.selection(Capability.UNDERSTAND) == ""

    asyncio.run(exercise())


def test_ai_provider_failure_does_not_fall_back(tmp_path) -> None:
    settings = AISettings(DesktopPreferences(tmp_path / "settings.json"), Credentials())
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    async def exercise() -> None:
        await settings.select(Capability.NOTIFICATION_RULE, "openai:gpt-5.4-mini")
        service = AIService(settings, transport=httpx.MockTransport(handle))
        with pytest.raises(AIError, match="provider_rate_limit") as exc:
            await draft_notification_rule(
                service,
                app_id="bank.app",
                title="Bank",
                body="Paid",
                confirmed=True,
                expected_model_id="openai:gpt-5.4-mini",
            )
        assert exc.value.provider == Provider.OPENAI and calls == 1

    asyncio.run(exercise())


def test_ai_dialog_sends_only_edited_sample_after_explicit_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = Rule("Draft", "bank.app", "ignore", body=Pattern("Declined"), enabled=False)
    send = AsyncMock(return_value=rule)
    monkeypatch.setattr(notification_ai_ui, "draft_notification_rule", send)
    page = MagicMock(spec=ft.Page)
    callback = MagicMock()
    dialog = NotificationAIConsent(
        page,
        lambda key, **values: key,
        MagicMock(spec=AIService),
        Model(Provider.OPENAI, "gpt-5.4-mini"),
        "bank.app",
        title="Bank",
        body="Private value",
        on_draft=callback,
    )
    send.assert_not_awaited()
    dialog.body.value = "Redacted"
    asyncio.run(dialog._send(MagicMock()))
    send.assert_awaited_once_with(
        dialog.service,
        app_id="bank.app",
        title="Bank",
        body="Redacted",
        confirmed=True,
        expected_model_id="openai:gpt-5.4-mini",
    )
    callback.assert_called_once_with(rule, "Bank", "Redacted")
    page.pop_dialog.assert_called_once()


def test_ai_dialog_failure_keeps_sample_and_cancel_stops_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(side_effect=AIError("provider_refusal", provider=Provider.OPENAI))
    monkeypatch.setattr(notification_ai_ui, "draft_notification_rule", send)
    page = MagicMock(spec=ft.Page)
    callback = MagicMock()
    dialog = NotificationAIConsent(
        page,
        lambda key, **values: key,
        MagicMock(spec=AIService),
        Model(Provider.OPENAI, "gpt-5.4-mini"),
        "bank.app",
        title=None,
        body="Redacted",
        on_draft=callback,
    )
    asyncio.run(dialog._send(MagicMock()))
    assert dialog.status.value == "notification_ai_refused"
    assert dialog.body.value == "Redacted"
    callback.assert_not_called()
    page.pop_dialog.assert_not_called()

    async def cancel_request() -> None:
        started = asyncio.Event()

        async def pending(*args: object, **kwargs: object) -> Rule:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("Cancelled request resumed")

        monkeypatch.setattr(notification_ai_ui, "draft_notification_rule", pending)
        task = asyncio.create_task(dialog._send(MagicMock()))
        await started.wait()
        await dialog._cancel(MagicMock())
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_request())
    callback.assert_not_called()
    assert dialog.body.value == "Redacted"
