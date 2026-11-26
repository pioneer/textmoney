"""Validated Android notification-bridge responses, without UI or journal access."""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.infrastructure.notification_store import MAX_SELECTED_APPS, IncomingNotification


class NotificationBridgeError(RuntimeError):
    """The native notification service is unavailable or returned invalid data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Bridge(Protocol):
    async def call(self, method: str, **arguments: object) -> object: ...


@dataclass(frozen=True)
class NotificationAccess:
    granted: bool
    connected: bool


@dataclass(frozen=True)
class NotificationApp:
    id: str
    name: str


class AndroidNotifications:
    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    async def _call(self, method: str, **arguments: object) -> object:
        response = await self.bridge.call(method, **arguments)
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise NotificationBridgeError("invalid_response")
        if not response["ok"]:
            code = response.get("error")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                raise NotificationBridgeError("invalid_response")
            raise NotificationBridgeError(code)
        if "value" not in response:
            raise NotificationBridgeError("invalid_response")
        return response["value"]

    async def status(self) -> NotificationAccess:
        result = await self._call("status")
        if (
            not isinstance(result, dict)
            or type(result.get("access")) is not bool
            or type(result.get("connected")) is not bool
            or (result["connected"] and not result["access"])
        ):
            raise NotificationBridgeError("invalid_response")
        return NotificationAccess(result["access"], result["connected"])

    async def open_settings(self) -> None:
        if await self._call("open_settings") is not None:
            raise NotificationBridgeError("invalid_response")

    async def enable(self) -> None:
        if await self._call("enable") is not None:
            raise NotificationBridgeError("invalid_response")

    async def disable(self) -> None:
        if await self._call("disable") is not None:
            raise NotificationBridgeError("invalid_response")

    async def storage_dir(self) -> Path:
        result = await self._call("storage_dir")
        # The bridge reports an Android POSIX path, but Path.is_absolute() is
        # host-dependent and rejects "/data/..." on Windows. Accept a path that is
        # absolute in either form so the Android path and host-native test paths work.
        if (
            not isinstance(result, str)
            or not result
            or not (PurePosixPath(result).is_absolute() or Path(result).is_absolute())
        ):
            raise NotificationBridgeError("invalid_response")
        return Path(result)

    async def apps(self) -> tuple[NotificationApp, ...]:
        result = await self._call("apps")
        if not isinstance(result, list) or len(result) > 10_000:
            raise NotificationBridgeError("invalid_response")
        apps: list[NotificationApp] = []
        seen: set[str] = set()
        for item in result:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("name"), str)
                or not item["id"]
                or len(item["id"]) > 1024
                or not item["name"].strip()
                or len(item["name"]) > 512
                or item["id"] in seen
            ):
                raise NotificationBridgeError("invalid_response")
            seen.add(item["id"])
            apps.append(NotificationApp(item["id"], item["name"]))
        return tuple(apps)

    async def active(self, selected: tuple[str, ...]) -> tuple[IncomingNotification, ...]:
        if (
            not selected
            or len(selected) > MAX_SELECTED_APPS
            or any(not item or len(item) > 1024 for item in selected)
            or len(set(selected)) != len(selected)
        ):
            raise NotificationBridgeError("invalid_apps")
        result = await self._call("active", apps=list(selected))
        if not isinstance(result, list) or len(result) > 10_000:
            raise NotificationBridgeError("invalid_response")
        notifications: list[IncomingNotification] = []
        for item in result:
            if (
                not isinstance(item, dict)
                or item.get("app_id") not in selected
                or not isinstance(item.get("key"), str)
                or not item["key"]
                or len(item["key"]) > 1024
                or type(item.get("posted")) is not int
                or item["posted"] <= 0
                or type(item.get("posted_millis")) is not int
                or item["posted_millis"] // 1000 != item["posted"]
                or not isinstance(item.get("version"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["version"])
                or any(
                    item.get(field) is not None
                    and (not isinstance(item[field], str) or len(item[field]) > 4096)
                    for field in ("title", "body")
                )
                or "title" not in item
                or "body" not in item
            ):
                raise NotificationBridgeError("invalid_response")
            notifications.append(
                IncomingNotification(
                    item["app_id"],
                    item["key"],
                    item["posted"],
                    item["version"],
                    item["title"],
                    item["body"],
                    item["posted_millis"],
                )
            )
        return tuple(notifications)
