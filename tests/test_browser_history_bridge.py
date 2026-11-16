import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet_web
import pytest
from browser_history_bridge import BrowserHistory
from flet_web.patch_index import patch_index_html
from invoke.context import Context
from invoke.exceptions import Exit

import tasks


def test_back_awaits_client_and_propagates_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    invoke = AsyncMock(return_value=None)
    monkeypatch.setattr(BrowserHistory, "_invoke_method", invoke)
    service = BrowserHistory()
    assert asyncio.run(service.back()) is None
    invoke.assert_awaited_once_with("back", timeout=10)

    invoke.side_effect = RuntimeError("Client unavailable")
    with pytest.raises(RuntimeError, match="Client unavailable"):
        asyncio.run(service.back())


def test_preview_build_uses_dynamic_index_and_rejects_stale_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text("manifest", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("lock", encoding="utf-8")
    for extension in ("browser-history-bridge", "finance-saf", "popup-keyboard"):
        base = tmp_path / "extensions" / extension
        (base / "src/flutter/example/lib").mkdir(parents=True)
        (base / "pyproject.toml").write_text("extension", encoding="utf-8")
        (base / "src/flutter/example/lib/example.dart").write_text("source", encoding="utf-8")
    web = tmp_path / "build/web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("static index", encoding="utf-8")
    (web / "main.dart.js").write_text("compiled client", encoding="utf-8")
    build = MagicMock()
    monkeypatch.setattr(tasks, "build_target", build)

    with pytest.raises(Exit, match="missing or stale"):
        tasks.require_web_dev_client()
    tasks.build_web_client(Context())
    build.assert_called_once_with("web")
    client = tasks.require_web_dev_client()
    index = client / "index.html"
    assert index.read_text(encoding="utf-8") == (
        Path(flet_web.__file__).parent / "web/index.html"
    ).read_text(encoding="utf-8")
    patch_index_html(str(index), "/", websocket_endpoint_path="/ws")
    assert "flet.pyodide = false;" in index.read_text(encoding="utf-8")
    assert 'flet.webSocketEndpoint="/ws";' in index.read_text(encoding="utf-8")
    flutter = tmp_path / "extensions/popup-keyboard/src/flutter/example"
    (flutter / ".dart_tool").mkdir()
    (flutter / ".dart_tool/generated").write_text("ignored", encoding="utf-8")
    tasks.require_web_dev_client()
    (flutter / "lib/example.dart").write_text("changed", encoding="utf-8")
    with pytest.raises(Exit, match="missing or stale"):
        tasks.require_web_dev_client()
