import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import flet_desktop
import pytest
from invoke.context import Context
from invoke.exceptions import Exit

import tasks


def test_linux_development_icon_does_not_modify_shared_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    (root / "assets").mkdir(parents=True)
    tasks.Image.new("RGB", (1024, 1024), "green").save(root / "assets/icon.png")
    stock = tmp_path / "cache/flet"
    (stock / "data").mkdir(parents=True)
    (stock / "lib").mkdir()
    (stock / "data/flutter_assets").mkdir()
    (stock / "data/app_icon.png").write_bytes(b"generic icon")
    binary = b"prefix" + tasks.STOCK_CLIENT_APP_ID + b"\0suffix"
    (stock / "flet").write_bytes(binary)
    monkeypatch.setattr(tasks, "ROOT", root)
    monkeypatch.setattr(flet_desktop, "ensure_client_cached", lambda: stock.parent)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))

    client = tasks.prepare_linux_dev_client()
    assert (client / "flet").read_bytes() == (
        b"prefix" + tasks.LINUX_DEV_APP_ID.encode() + b"\0suffix"
    )
    assert (client / "flet").stat().st_mode & 0o111
    assert (client / "data/app_icon.png").read_bytes() == (root / "assets/icon.png").read_bytes()
    assert (client / "lib").resolve() == stock / "lib"
    assert (client / "data/flutter_assets").resolve() == stock / "data/flutter_assets"
    assert (stock / "flet").read_bytes() == binary
    assert (stock / "data/app_icon.png").read_bytes() == b"generic icon"
    entry = tmp_path / "user-data/applications" / f"{tasks.LINUX_DEV_APP_ID}.desktop"
    assert f"Icon={root / 'assets/icon.png'}" in entry.read_text()
    assert f"StartupWMClass={tasks.LINUX_DEV_APP_ID}" in entry.read_text()

    tasks.Image.new("RGB", (1024, 1024), "blue").save(root / "assets/icon.png")
    tasks.prepare_linux_dev_client()
    assert (client / "data/app_icon.png").read_bytes() == (root / "assets/icon.png").read_bytes()
    (stock / "flet").write_bytes(b"changed upstream binary")
    with pytest.raises(Exit, match="identity changed"):
        tasks.prepare_linux_dev_client()


@pytest.mark.parametrize(
    "host,web,branded", [("Linux", False, True), ("Linux", True, False), ("Windows", False, False)]
)
def test_only_linux_native_run_uses_branded_client(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    web: bool,
    branded: bool,
) -> None:
    prepare = MagicMock(return_value=Path("/synthetic/client"))
    execute = MagicMock()
    monkeypatch.setattr(tasks, "stage_release", lambda: None)
    monkeypatch.setattr(tasks.platform, "system", lambda: host)
    monkeypatch.setattr(tasks, "prepare_linux_dev_client", prepare)
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.run_app(web=web, debug=True)
    if branded:
        prepare.assert_called_once()
        assert execute.call_args.kwargs["env"]["FLET_VIEW_PATH"] == "/synthetic/client"
    else:
        prepare.assert_not_called()
        assert "FLET_VIEW_PATH" not in execute.call_args.kwargs["env"]


def test_release_staging_and_versioned_apk_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.1"\n[tool.flet]\nbuild_number = 13\n'
    )
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    tasks.stage_release()
    assert json.loads((tmp_path / "src/app/.release.json").read_text()) == {
        "version": "0.1.1",
        "build_number": 13,
    }
    monkeypatch.setattr(tasks, "stage_assets", lambda: None)

    def build(*args: str, **kwargs: object) -> None:
        assert args == ("flet", "build", "apk", "--yes")
        directory = tmp_path / "build/apk"
        directory.mkdir(parents=True)
        (directory / "txncount.apk").write_bytes(b"synthetic APK")

    monkeypatch.setattr(tasks, "execute", build)
    tasks.build_target("apk")
    assert (tmp_path / "build/apk/txncount-0.1.1.apk").read_bytes() == b"synthetic APK"
    assert not (tmp_path / "build/apk/txncount.apk").exists()


def test_failed_build_does_not_rename_or_replace_apks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "build/apk"
    output.mkdir(parents=True)
    (output / "txncount.apk").write_bytes(b"old unversioned APK")
    (output / "txncount-0.1.1.apk").write_bytes(b"old versioned APK")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "stage_assets", lambda: None)
    monkeypatch.setattr(tasks, "execute", MagicMock(side_effect=Exit("build failed", code=1)))
    with pytest.raises(Exit, match="build failed"):
        tasks.build_target("apk")
    assert (output / "txncount.apk").read_bytes() == b"old unversioned APK"
    assert (output / "txncount-0.1.1.apk").read_bytes() == b"old versioned APK"


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
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.1"\n[tool.flet]\nbuild_number = 13\n'
    )
    cache = tmp_path / "src/app/__pycache__"
    cache.mkdir(parents=True)
    (cache / "removed_module.cpython-313.pyc").write_bytes(b"obsolete development bytecode")
    source = tmp_path / "src/app/keep.py"
    source.write_text("VALUE = 1\n")
    tasks.stage_assets()
    assert (tmp_path / "src/assets/icon.png").read_bytes() == original
    assert not cache.exists()
    assert source.read_text() == "VALUE = 1\n"
    tasks.clean(Context())
    assert not (tmp_path / "src/assets").exists()
    assert icon.read_bytes() == original
    assert (environment / "keep").is_file()
    assert not (tmp_path / "src/app/.release.json").exists()


def test_invalid_icon_stops_staging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    tasks.Image.new("RGB", (64, 64)).save(tmp_path / "assets/icon.png")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    with pytest.raises(Exit, match="1024 x 1024"):
        tasks.stage_assets()
    assert not (tmp_path / "src/assets").exists()
