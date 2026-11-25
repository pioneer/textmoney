import asyncio
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.infrastructure.notifications_android import AndroidNotifications, NotificationBridgeError


class FakeBridge:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call(self, method: str, **arguments: object) -> object:
        self.calls.append((method, arguments))
        return self.response


def test_bridge_reads_only_selected_app_notifications() -> None:
    bridge = FakeBridge(
        {
            "ok": True,
            "value": [
                {
                    "app_id": "bank.app",
                    "key": "key:1000",
                    "posted": 1,
                    "posted_millis": 1000,
                    "version": "a" * 64,
                    "title": "Bank",
                    "body": "Paid 5 EUR",
                }
            ],
        }
    )
    items = asyncio.run(AndroidNotifications(bridge).active(("bank.app",)))
    assert len(items) == 1 and items[0].body == "Paid 5 EUR"
    assert bridge.calls == [("active", {"apps": ["bank.app"]})]
    with pytest.raises(NotificationBridgeError, match="invalid_response"):
        asyncio.run(AndroidNotifications(bridge).active(("another.app",)))


@pytest.mark.parametrize(
    "response",
    [
        {"ok": True, "value": {"access": False, "connected": True}},
        {"ok": True, "value": {"access": 1, "connected": False}},
        {"ok": False, "error": "private text / not an error code"},
        {"ok": True},
    ],
)
def test_invalid_bridge_responses_fail_explicitly(response: object) -> None:
    with pytest.raises(NotificationBridgeError, match="invalid_response"):
        asyncio.run(AndroidNotifications(FakeBridge(response)).status())


def test_permission_failure_is_preserved_as_a_stable_error_code() -> None:
    bridge = FakeBridge({"ok": False, "error": "access_required"})
    with pytest.raises(NotificationBridgeError, match="access_required"):
        asyncio.run(AndroidNotifications(bridge).active(("bank.app",)))


def test_listener_can_be_disabled_and_reports_failures() -> None:
    bridge = FakeBridge({"ok": True, "value": None})
    asyncio.run(AndroidNotifications(bridge).disable())
    assert bridge.calls == [("disable", {})]
    bridge.response = {"ok": False, "error": "listener_unavailable"}
    with pytest.raises(NotificationBridgeError, match="listener_unavailable"):
        asyncio.run(AndroidNotifications(bridge).disable())


def test_storage_directory_requires_an_absolute_native_path() -> None:
    bridge = FakeBridge({"ok": True, "value": "/data/user/0/app/no_backup"})
    assert str(asyncio.run(AndroidNotifications(bridge).storage_dir())).endswith("/no_backup")
    bridge.response = {"ok": True, "value": "relative"}
    with pytest.raises(NotificationBridgeError, match="invalid_response"):
        asyncio.run(AndroidNotifications(bridge).storage_dir())


def test_native_listener_is_disabled_until_explicitly_enabled() -> None:
    manifest = (
        Path(__file__).resolve().parents[1]
        / "extensions/notification-bridge/src/flutter/notification_bridge/android"
        / "src/main/AndroidManifest.xml"
    )
    root = ElementTree.parse(manifest).getroot()
    android = "{http://schemas.android.com/apk/res/android}"
    service = root.find("./application/service")
    assert service is not None
    assert service.get(f"{android}enabled") == "false"
    assert service.get(f"{android}permission") == (
        "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"
    )
