import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from invoke.context import Context
from invoke.exceptions import Exit

import tasks


@pytest.mark.parametrize(
    "host,target", [("Linux", "linux"), ("Windows", "windows"), ("Darwin", "macos")]
)
def test_local_build_dispatch(monkeypatch: pytest.MonkeyPatch, host: str, target: str) -> None:
    build = MagicMock()
    monkeypatch.setattr(tasks.platform, "system", lambda: host)
    monkeypatch.setattr(tasks, "build_target", build)
    tasks.build_local(Context())
    build.assert_called_once_with(target)


def test_host_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks.platform, "system", lambda: "Linux")
    for target in ("windows", "macos", "ipa", "ios-simulator", "ios"):
        with pytest.raises(Exit, match="requires"):
            tasks.require_host(target)


def test_debug_command(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock()
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.debug_web(Context(), port=8765)
    args = execute.call_args.args
    assert "--web" in args and "-v" in args and "--recursive" in args
    assert "8765" in args
    assert execute.call_args.kwargs["env"]["APP_DEBUG"] == "1"
    assert args[args.index("--ignore-dirs") + 1] == ".flet,__pycache__"
    assert execute.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_check_order_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def record(*args: str, **kwargs: object) -> None:
        calls.append(" ".join(args))

    monkeypatch.setattr(tasks, "execute", record)
    tasks.check(Context())
    assert calls == ["ruff format --check .", "ruff check .", "pyright", "pytest"]


def test_deployment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "build/web"
    output.mkdir(parents=True)
    (output / "index.html").touch()
    build = MagicMock()
    execute = MagicMock()
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "build_web", build)
    monkeypatch.setattr(tasks, "execute", execute)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: "/usr/bin/aws")
    monkeypatch.setenv("S3_BUCKET", "example-test-bucket")
    monkeypatch.setenv("S3_PREFIX", "apps/test")
    monkeypatch.setenv("AWS_PROFILE", "test-profile")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    tasks.deploy_web(Context())
    build.assert_called_once()
    assert build.call_args.kwargs["base_url"] == "apps/test"
    assert execute.call_args.args == (
        "aws",
        "s3",
        "sync",
        str(output),
        "s3://example-test-bucket/apps/test/",
        "--delete",
        "--profile",
        "test-profile",
        "--region",
        "eu-west-1",
    )


@pytest.mark.parametrize("prefix", ["../bad", "a//b", "a/./b", "a b", "a?b"])
def test_invalid_prefix(prefix: str) -> None:
    with pytest.raises(Exit):
        tasks.normalize_prefix(prefix)


def test_mobile_debug_marker_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "src/app").mkdir(parents=True)
    marker = tmp_path / "src/app/.debug-mode"
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "stage_assets", lambda: None)

    def fail(*args: str, **kwargs: object) -> None:
        assert marker.is_file()
        assert args == (
            "flet",
            "debug",
            "android",
            "--yes",
            "-v",
            "--no-compile-app",
            "--device-id",
            "test-device",
        )
        raise Exit("simulated device failure", code=1)

    monkeypatch.setattr(tasks, "execute", fail)
    with pytest.raises(Exit, match="simulated device failure"):
        tasks.debug_mobile("android", "test-device", False)
    assert not marker.exists()


def test_release_removes_debug_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "src/app").mkdir(parents=True)
    marker = tmp_path / "src/app/.debug-mode"
    marker.touch()
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "stage_assets", lambda: None)
    monkeypatch.setattr(tasks, "execute", MagicMock())
    tasks.build_target("web")
    assert not marker.exists()


def test_builds_are_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock()
    monkeypatch.setattr(tasks, "stage_assets", lambda: None)
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.build_web(Context(), base_url="/apps/demo/")
    assert execute.call_args.args == ("flet", "build", "web", "--yes", "--base-url", "/apps/demo/")
    assert execute.call_args.kwargs["env"]["APP_DEBUG"] == "0"


def test_adb_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tasks.shutil, "which", lambda name: "/opt/adb")
    assert tasks.find_adb() == "/opt/adb"
    monkeypatch.setattr(tasks.shutil, "which", lambda name: None)
    monkeypatch.setattr(tasks.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tasks.AndroidSDK, "android_home_dir", staticmethod(lambda: tmp_path))
    assert tasks.find_adb() is None
    (tmp_path / "platform-tools").mkdir()
    (tmp_path / "platform-tools/adb").touch()
    assert tasks.find_adb() == str(tmp_path / "platform-tools/adb")
    monkeypatch.setattr(tasks.AndroidSDK, "android_home_dir", staticmethod(lambda: None))
    assert tasks.find_adb() is None


def test_logs_android_uses_sdk_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock()
    monkeypatch.setattr(tasks, "find_adb", lambda: "/sdk/platform-tools/adb")
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.logs_android(Context(), device="emulator-5554")
    assert execute.call_args.args == (
        "/sdk/platform-tools/adb",
        "-s",
        "emulator-5554",
        "logcat",
        "-s",
        "flet.python",
    )
    monkeypatch.setattr(tasks, "find_adb", lambda: None)
    with pytest.raises(Exit, match="adb is missing"):
        tasks.logs_android(Context())


def test_device_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "src/app").mkdir(parents=True)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "stage_assets", lambda: None)
    execute = MagicMock()
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.debug_mobile("android", "", True)
    assert execute.call_args.args[-3:] == ("--device-id", "discover", "--show-devices")
    assert not (tmp_path / "src/app/.debug-mode").exists()


def test_check_stops_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock(side_effect=Exit("format failed", code=1))
    monkeypatch.setattr(tasks, "execute", execute)
    with pytest.raises(Exit, match="format failed"):
        tasks.check(Context())
    execute.assert_called_once_with("ruff", "format", "--check", ".")


def test_command_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tasks.subprocess, "run", MagicMock(side_effect=subprocess.CalledProcessError(7, "flet"))
    )
    with pytest.raises(Exit) as failure:
        tasks.execute("flet", "build", "web")
    assert failure.value.code == 7


@pytest.mark.parametrize("missing", ["aws", "bucket", "index"])
def test_deployment_preflight_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
) -> None:
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    build = MagicMock()
    execute = MagicMock()
    monkeypatch.setattr(tasks, "build_web", build)
    monkeypatch.setattr(tasks, "execute", execute)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: None if missing == "aws" else "aws")
    monkeypatch.setenv("S3_BUCKET", "" if missing == "bucket" else "example-test-bucket")
    monkeypatch.delenv("S3_PREFIX", raising=False)
    with pytest.raises(Exit):
        tasks.deploy_web(Context())
    build.assert_called_once()
    execute.assert_not_called()


def test_asset_staging_and_clean_preserve_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    icon = assets / "icon.png"
    tasks.Image.new("RGBA", (1024, 1024)).save(icon)
    original = icon.read_bytes()
    environment = tmp_path / ".venv"
    environment.mkdir()
    (environment / "keep").touch()
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    tasks.stage_assets()
    assert (tmp_path / "src/assets/icon.png").read_bytes() == original
    tasks.clean(Context())
    assert not (tmp_path / "src/assets").exists()
    assert icon.read_bytes() == original
    assert (environment / "keep").is_file()


def test_invalid_icon_stops_staging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    tasks.Image.new("RGB", (64, 64)).save(tmp_path / "assets/icon.png")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    with pytest.raises(Exit, match="1024 x 1024"):
        tasks.stage_assets()
    assert not (tmp_path / "src/assets").exists()
