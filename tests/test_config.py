from pathlib import Path

import pytest

from app import config
from app.config import Settings


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE ", "On"])
def test_debug_enabled(value: str) -> None:
    assert Settings.from_env({"APP_DEBUG": value}).debug


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_debug_disabled(value: str) -> None:
    assert not Settings.from_env({"APP_DEBUG": value}).debug


def test_defaults_and_invalid_settings() -> None:
    assert not Settings.from_env({}).debug
    with pytest.raises(ValueError, match="APP_DEBUG must be a boolean"):
        Settings.from_env({"APP_DEBUG": "invalid"})


def test_packaged_debug_and_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "files", lambda package: tmp_path)
    assert not Settings.from_env({}).debug
    (tmp_path / ".debug-mode").touch()
    assert Settings.from_env({}).debug
    assert not Settings.from_env({"APP_DEBUG": "0"}).debug
