import json
from pathlib import Path

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
