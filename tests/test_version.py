import json
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

import tasks
from app import version
from app.ui.finance import FinanceView


def test_source_version_matches_build_and_about() -> None:
    assert version.release_identity() == tasks.release_identity()
    release, build = tasks.release_identity()
    view = FinanceView()
    about = view.settings_screen.controls[2]
    assert isinstance(about, ft.Container)
    assert isinstance(about.content, ft.Column)
    assert any(
        isinstance(control, ft.Text) and f"{release} ({build})" in control.value
        for control in about.content.controls
    )


@pytest.mark.parametrize("width", [390, 800, 1366])
@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_version_visible_in_every_screen_header(
    monkeypatch: pytest.MonkeyPatch, width: int, language: str
) -> None:
    page = MagicMock(spec=ft.Page)
    page.width = width
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    view.language = language
    view.startup_complete = True
    view._apply_layout(width)
    release, build = tasks.release_identity()
    for screen in ("home", "transactions", "accounts", "reports", "settings"):
        view._navigate(screen)
        assert view.body in view.controls
        header = view.body.controls[0]
        assert isinstance(header, ft.Container)
        assert isinstance(header.content, ft.Row)
        assert header.content.controls == [view.page_title, view.release_label]
        assert header.content.wrap
        assert view.page_title.value == view.tr(f"nav_{screen}")
        assert view.release_label.value == f"v{release} ({build})"
        assert view.release_label.visible


def test_packaged_version_does_not_need_project_or_distribution_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "src/app"
    app.mkdir(parents=True)
    monkeypatch.setattr(version, "__file__", str(app / "version.py"))
    monkeypatch.setattr(version, "files", lambda _: app)
    metadata = app / ".release.json"
    metadata.write_text(json.dumps({"version": "0.1.1", "build_number": 13}))
    assert version.release_identity() == ("0.1.1", 13)
    assert FinanceView().release_label.value == "v0.1.1 (13)"
    metadata.write_text(json.dumps({"version": "0.1.1", "build_number": 0}))
    with pytest.raises(ValueError, match="version metadata"):
        version.release_identity()
    metadata.unlink()
    with pytest.raises(FileNotFoundError):
        version.release_identity()


def test_source_configuration_overrides_stale_packaged_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "src/app"
    app.mkdir(parents=True)
    monkeypatch.setattr(version, "__file__", str(app / "version.py"))
    monkeypatch.setattr(version, "files", lambda _: app)
    (app / ".release.json").write_text('{"version": "0.0.1", "build_number": 1}')
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.2.0"\n[tool.flet]\nbuild_number = 20\n'
    )
    assert version.release_identity() == ("0.2.0", 20)


@pytest.mark.parametrize(
    "metadata",
    [
        '{"version": "", "build_number": 13}',
        '{"version": "0.1.1", "build_number": true}',
        '{"version": "0.1.1", "build_number": "13"}',
        '{"version": "0.1.1"}',
        "invalid JSON",
    ],
)
def test_invalid_packaged_metadata_fails_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: str,
) -> None:
    app = tmp_path / "src/app"
    app.mkdir(parents=True)
    monkeypatch.setattr(version, "__file__", str(app / "version.py"))
    monkeypatch.setattr(version, "files", lambda _: app)
    (app / ".release.json").write_text(metadata)
    with pytest.raises((ValueError, KeyError)):
        version.release_identity()
