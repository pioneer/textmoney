import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.domain.category_migration import migrated_category
from app.domain.ledger import BANK_FEES, LedgerError, account_path


class PreferencesError(RuntimeError):
    pass


def parse_default_account(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.startswith(("Assets:", "Liabilities:")):
        raise PreferencesError("default_account_invalid")
    try:
        if account_path(value, value.split(":")[0]) != value:
            raise PreferencesError("default_account_invalid")
    except LedgerError as exc:
        raise PreferencesError("default_account_invalid") from exc
    return value


def fee_category_in_branches(identifier: str, branches: tuple[str, ...]) -> bool:
    return any(identifier == branch or identifier.startswith(branch + ":") for branch in branches)


def migrated_fee_branches(branches: tuple[str, ...], original: str, target: str) -> tuple[str, ...]:
    updated = tuple(
        dict.fromkeys(migrated_category(branch, original, target) for branch in branches)
    )
    if fee_category_in_branches(original, branches) and not fee_category_in_branches(
        target, updated
    ):
        updated += (target,)
    return updated


@dataclass(frozen=True)
class FeePreferences:
    branches: tuple[str, ...] = ("Expenses",)
    default: str = BANK_FEES

    def __post_init__(self) -> None:
        try:
            for identifier in (*self.branches, self.default):
                if identifier != "Expenses" and (
                    not identifier.startswith("Expenses:")
                    or account_path(identifier, "Expenses") != identifier
                ):
                    raise PreferencesError("fee_categories_invalid")
        except LedgerError as exc:
            raise PreferencesError("fee_categories_invalid") from exc
        if (
            not self.branches
            or self.default == "Expenses"
            or not fee_category_in_branches(self.default, self.branches)
        ):
            raise PreferencesError("fee_categories_invalid")

    def serialize(self) -> str:
        return json.dumps({"branches": self.branches, "default": self.default})

    def migrate(self, original: str, target: str) -> "FeePreferences":
        return FeePreferences(
            migrated_fee_branches(self.branches, original, target),
            migrated_category(self.default, original, target),
        )

    @classmethod
    def parse(cls, value: str) -> "FeePreferences":
        try:
            settings = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PreferencesError("fee_categories_invalid") from exc
        if (
            not isinstance(settings, dict)
            or set(settings) != {"branches", "default"}
            or not isinstance(settings["branches"], list)
            or any(not isinstance(branch, str) for branch in settings["branches"])
            or not isinstance(settings["default"], str)
        ):
            raise PreferencesError("fee_categories_invalid")
        return cls(tuple(dict.fromkeys(settings["branches"])), settings["default"])


def desktop_preferences_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        root = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / "Library" / "Application Support")
        )
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "textmoney" / "preferences.json"


class DesktopPreferences:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or desktop_preferences_path()
        self.lock = asyncio.Lock()

    def _read(self) -> dict[str, str]:
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise PreferencesError(
                "Could not read TextMoney settings. Check the settings file and permissions."
            ) from exc
        if not isinstance(values, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
        ):
            raise PreferencesError("The TextMoney settings file has an invalid format.")
        return values

    async def get(self, key: str) -> str | None:
        async with self.lock:
            return (await asyncio.to_thread(self._read)).get(key)

    async def set(self, key: str, value: str) -> bool:
        async with self.lock:
            values = await asyncio.to_thread(self._read)
            values[key] = value
            serialized = json.dumps(values, ensure_ascii=False, indent=2) + "\n"
            temporary = self.path.with_suffix(".tmp")
            try:
                await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(temporary.write_text, serialized, encoding="utf-8")
                await asyncio.to_thread(temporary.replace, self.path)
            except OSError as exc:
                raise PreferencesError(
                    "Could not save TextMoney settings. Check disk space and permissions."
                ) from exc
            return True
