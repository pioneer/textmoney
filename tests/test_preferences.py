import asyncio
import json

import pytest

from app.infrastructure.preferences import DesktopPreferences, PreferencesError


def test_desktop_preferences_round_trip_and_preserve_existing_values(tmp_path) -> None:
    async def scenario() -> None:
        store = DesktopPreferences(tmp_path / "txncount" / "preferences.json")
        assert await store.get("folder") is None
        assert await store.set("folder", "/tmp/ledger")
        assert await store.set("language", "uk")
        assert await store.get("folder") == "/tmp/ledger"
        assert await store.get("language") == "uk"

    asyncio.run(scenario())


def test_corrupt_desktop_preferences_are_not_silently_replaced(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text("{broken", encoding="utf-8")
    store = DesktopPreferences(path)

    with pytest.raises(PreferencesError, match="Could not read TxnCount settings"):
        asyncio.run(store.set("language", "ru"))

    assert path.read_text(encoding="utf-8") == "{broken"


def test_desktop_preferences_reject_invalid_value_shapes(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"language": 3}), encoding="utf-8")
    store = DesktopPreferences(path)

    with pytest.raises(PreferencesError, match="invalid format"):
        asyncio.run(store.get("language"))
