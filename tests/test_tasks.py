import json
import math
import shutil
import subprocess
import sys
from base64 import b64encode
from pathlib import Path
from unittest.mock import MagicMock

import flet_desktop
import pytest
from invoke.context import Context
from invoke.exceptions import Exit

import tasks


def test_linux_icon_is_large_transparent_and_keeps_android_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    source = tasks.ROOT / "assets/icon.png"
    shutil.copyfile(source, assets / "icon.png")
    original = source.read_bytes()
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "compile_translations", lambda: None)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.2.2"\n[tool.flet]\nbuild_number = 19\n'
    )
    image = tasks.linux_icon()
    assert image.mode == "RGBA" and image.size == (1024, 1024)
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    assert bounds is not None
    assert max(bounds[2] - bounds[0], bounds[3] - bounds[1]) >= 940
    assert min(bounds[0], bounds[1], 1024 - bounds[2], 1024 - bounds[3]) >= 36
    assert alpha.getpixel((0, 0)) == alpha.getpixel((1023, 1023)) == 0
    assert alpha.getextrema() == (0, 255)
    colors = image.getcolors(1024 * 1024)
    assert colors is not None
    assert sum(count for count, pixel in colors if pixel == (23, 50, 77, 255)) > 1000
    tasks.stage_assets(linux=True)
    staged = tmp_path / "src/assets"
    with tasks.Image.open(staged / "icon_linux.png") as icon:
        assert icon.tobytes() == image.tobytes()
    assert (assets / "icon.png").read_bytes() == original
    assert (staged / "icon.png").read_bytes() == original
    tasks.stage_assets()
    assert not (staged / "icon_linux.png").exists()
    assert (staged / "icon.png").read_bytes() == original


@pytest.mark.parametrize("target", ["linux", "apk", "web", "windows", "macos"])
def test_only_linux_build_stages_linux_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    stage = MagicMock()
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "require_host", MagicMock())
    monkeypatch.setattr(tasks, "stage_assets", stage)
    monkeypatch.setattr(tasks, "execute", MagicMock(side_effect=Exit("stop after staging")))
    with pytest.raises(Exit, match="stop after staging"):
        tasks.build_target(target)
    stage.assert_called_once_with(linux=target == "linux")


def test_app_icon_generates_canonical_currency_artwork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "assets").mkdir()
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    tasks.app_icon(Context())
    tasks.validate_icon()
    with tasks.Image.open(tmp_path / "assets/icon.png") as image:
        assert image.getpixel((0, 0)) == (23, 50, 77)
        for x, y, color in (
            (640, 240, (105, 185, 167)),
            (260, 300, (255, 255, 255)),
            (340, 340, (23, 50, 77)),
            (450, 440, (23, 50, 77)),
            (780, 500, (244, 207, 118)),
        ):
            assert (
                image.getpixel((180 + round(x * 664 / 1024), 180 + round(y * 664 / 1024))) == color
            )
        # Android guarantees a central 66-dp circle within its 108-dp adaptive layer.
        safe_radius = 1024 * 33 / 108
        for y in range(1024):
            for x in range(1024):
                pixel = image.getpixel((x, y))
                assert isinstance(pixel, tuple)
                if (
                    max(abs(value - base) for value, base in zip(pixel, (23, 50, 77), strict=True))
                    > 8
                ):
                    assert math.hypot(x - 512, y - 512) <= safe_radius


def test_linux_development_icon_does_not_modify_shared_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("Linux development client requires POSIX executable permissions and symlinks.")
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
    with tasks.Image.open(client / "data/app_icon.png") as icon:
        assert icon.tobytes() == tasks.linux_icon().tobytes()
    assert (client / "lib").resolve() == stock / "lib"
    assert (client / "data/flutter_assets").resolve() == stock / "data/flutter_assets"
    assert (stock / "flet").read_bytes() == binary
    assert (stock / "data/app_icon.png").read_bytes() == b"generic icon"
    entry = tmp_path / "user-data/applications" / f"{tasks.LINUX_DEV_APP_ID}.desktop"
    icon_line = next(line for line in entry.read_text().splitlines() if line.startswith("Icon="))
    registered_icon = Path(icon_line.removeprefix("Icon="))
    assert registered_icon.parent == client / "data"
    assert registered_icon.read_bytes() == (client / "data/app_icon.png").read_bytes()
    assert f"StartupWMClass={tasks.LINUX_DEV_APP_ID}" in entry.read_text()

    tasks.Image.new("RGB", (1024, 1024), "blue").save(root / "assets/icon.png")
    tasks.prepare_linux_dev_client()
    with tasks.Image.open(client / "data/app_icon.png") as icon:
        assert icon.tobytes() == tasks.linux_icon().tobytes()
    updated_line = next(line for line in entry.read_text().splitlines() if line.startswith("Icon="))
    assert updated_line != icon_line
    updated_icon = Path(updated_line.removeprefix("Icon="))
    assert updated_icon.read_bytes() == (client / "data/app_icon.png").read_bytes()
    tasks.prepare_linux_dev_client()
    assert updated_line in entry.read_text().splitlines()
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
        assert execute.call_args.kwargs["env"]["FLET_VIEW_PATH"] == str(Path("/synthetic/client"))
        assert execute.call_args.kwargs["env"]["FLET_APP_ID"] == tasks.LINUX_DEV_APP_ID
        assert execute.call_args.kwargs["cwd"] == tasks.ROOT / "src"
    else:
        prepare.assert_not_called()
        assert "FLET_VIEW_PATH" not in execute.call_args.kwargs["env"]


def test_linux_run_selects_development_client_even_after_packaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    packaged = tmp_path / "build/linux/txncount"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"packaged app")
    packaged.chmod(0o755)
    client = tmp_path / "build/linux-dev-client"
    client.mkdir()
    (client / "flet").write_bytes(b"development client")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "stage_release", lambda: None)
    monkeypatch.setattr(tasks.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tasks, "prepare_linux_dev_client", lambda: client)
    monkeypatch.setattr(tasks, "compile_translations", lambda: None)

    def launch(*args: str, env: dict[str, str], cwd: Path = tmp_path) -> None:
        monkeypatch.chdir(cwd)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        locate = getattr(flet_desktop, "__locate_and_unpack_flet_view")
        command, _, _ = locate("http://127.0.0.1:1", str(tmp_path / "assets"), False)
        assert command[0] == str(client / "flet")
        identity_args = getattr(flet_desktop, "__linux_identity_args")
        command, extra = identity_args(command)
        assert command[0] == tasks.LINUX_DEV_APP_ID
        assert extra == {"executable": str(client / "flet")}
        assert Path(args[2]).is_absolute()

    monkeypatch.setattr(tasks, "execute", launch)
    tasks.run_app()


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
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)

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
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "execute", MagicMock(side_effect=Exit("build failed", code=1)))
    with pytest.raises(Exit, match="build failed"):
        tasks.build_target("apk")
    assert (output / "txncount.apk").read_bytes() == b"old unversioned APK"
    assert (output / "txncount-0.1.1.apk").read_bytes() == b"old versioned APK"


def test_normal_apk_build_clears_production_signing_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def build(*args: str, **kwargs: object) -> None:
        output = tmp_path / "build/apk"
        output.mkdir(parents=True)
        (output / "txncount.apk").write_bytes(b"debug apk")

    execute = MagicMock(side_effect=build)
    monkeypatch.setattr(tasks, "require_host", lambda target: None)
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.3.0"\n[tool.flet]\nbuild_number = 22\n'
    )
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.build_target("apk")
    assert execute.call_args.kwargs["unset_env"] == tasks.ANDROID_SIGNING_ENV


def test_release_build_passes_explicit_signing_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def build(*args: str, **kwargs: object) -> None:
        output = tmp_path / "build/apk"
        output.mkdir(parents=True)
        (output / "txncount.apk").write_bytes(b"release apk")

    signing_env = {
        "FLET_ANDROID_SIGNING_KEY_STORE": str(tmp_path / "release.jks"),
        "FLET_ANDROID_SIGNING_KEY_ALIAS": "android-release",
        "FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD": "synthetic-store-password",
        "FLET_ANDROID_SIGNING_KEY_PASSWORD": "synthetic-key-password",
    }
    execute = MagicMock(side_effect=build)
    monkeypatch.setattr(tasks, "require_host", lambda target: None)
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.3.0"\n[tool.flet]\nbuild_number = 22\n'
    )
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.build_target("apk", signing=True, env=signing_env)
    assert execute.call_args.kwargs["env"] == tasks.debug_env(False) | signing_env
    assert execute.call_args.kwargs["unset_env"] == ()


def test_signing_environment_requires_all_values_and_existing_keystore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tasks.ANDROID_SIGNING_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(Exit, match="missing environment variable"):
        tasks.validate_android_signing_environment()

    key_path = tmp_path / "release.jks"
    key_path.touch()
    values = {
        "FLET_ANDROID_SIGNING_KEY_STORE": str(key_path),
        "FLET_ANDROID_SIGNING_KEY_ALIAS": "android-release",
        "FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD": "synthetic-store-password",
        "FLET_ANDROID_SIGNING_KEY_PASSWORD": "synthetic-key-password",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    result = tasks.validate_android_signing_environment()
    assert result["FLET_ANDROID_SIGNING_KEY_STORE"] == str(key_path)
    assert result["FLET_ANDROID_SIGNING_KEY_ALIAS"] == "android-release"

    monkeypatch.setenv("FLET_ANDROID_SIGNING_KEY_ALIAS", "wrong-alias")
    with pytest.raises(Exit, match="signing alias"):
        tasks.validate_android_signing_environment()

    monkeypatch.setenv("FLET_ANDROID_SIGNING_KEY_ALIAS", "android-release")
    monkeypatch.setenv("FLET_ANDROID_SIGNING_KEY_STORE", str(tmp_path / "missing.jks"))
    with pytest.raises(Exit, match="keystore does not exist"):
        tasks.validate_android_signing_environment()


def test_release_version_requires_matching_tag_and_increasing_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.3.1"\n[tool.flet]\nbuild_number = 23\n'
    )
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    with pytest.raises(Exit, match="must match the project version"):
        tasks.validate_android_release_version("v0.3.0")

    monkeypatch.setattr(tasks, "previous_release_build_number", lambda tag: 23)
    with pytest.raises(Exit, match="must be greater than the prior release"):
        tasks.validate_android_release_version("v0.3.1")

    monkeypatch.setattr(tasks, "previous_release_build_number", lambda tag: 22)
    tasks.validate_android_release_version("v0.3.1")


@pytest.mark.parametrize("line_wrapped", [False, True])
def test_previous_release_build_number_reads_published_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
    line_wrapped: bool,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "pioneer/txncount")
    content = b64encode(
        b'[tool.flet]\nbuild_number = 22\nbundle_id = "com.pioneer.txncount"\n'
    ).decode("ascii")
    if line_wrapped:
        content = (
            "\n".join(content[index : index + 60] for index in range(0, len(content), 60)) + "\n"
        )
    responses = iter(
        [
            '[{"tagName":"v0.3.0","isDraft":false,"isPrerelease":false}]',
            content,
        ]
    )
    monkeypatch.setattr(
        tasks,
        "capture_command",
        lambda *args: next(responses),
    )
    assert tasks.previous_release_build_number("v0.3.1") == 22


def test_previous_release_build_number_rejects_invalid_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "pioneer/txncount")
    responses = iter(
        [
            '[{"tagName":"v0.3.0","isDraft":false,"isPrerelease":false}]',
            "invalid!base64",
        ]
    )
    monkeypatch.setattr(tasks, "capture_command", lambda *args: next(responses))
    with pytest.raises(Exit, match="Could not read the Android version code"):
        tasks.previous_release_build_number("v0.3.1")


def test_previous_release_build_number_refuses_an_already_published_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "pioneer/txncount")
    monkeypatch.setattr(
        tasks,
        "capture_command",
        lambda *args: (
            '[{"tagName":"v0.3.1","isDraft":false,"isPrerelease":false},'
            '{"tagName":"v0.3.0","isDraft":false,"isPrerelease":false}]'
        ),
    )
    with pytest.raises(Exit, match="already published"):
        tasks.previous_release_build_number("v0.3.1")


@pytest.fixture
def release_apk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "release_identity", lambda: ("0.3.0", 22))


@pytest.mark.usefixtures("release_apk_identity")
def test_release_apk_verification_checks_signer_identity_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk = tmp_path / "txncount-0.3.0.apk"
    apk.write_bytes(b"synthetic signed apk")
    monkeypatch.setattr(tasks, "android_build_tool", lambda name: name)
    monkeypatch.setattr(
        tasks,
        "capture_command",
        lambda *args: (
            "Signer #1 certificate SHA-256 digest: "
            "0cc03e9537ef041c51eb8ececa1ae9e246c7e5a2c814ff1dd9a7f19ab6c07fa6"
            if args[1] == "verify"
            else "package: name='com.pioneer.txncount' versionCode='22' versionName='0.3.0'\n"
        ),
    )
    tasks.verify_android_release_apk(apk)


@pytest.mark.usefixtures("release_apk_identity")
def test_release_apk_verification_rejects_wrong_signer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk = tmp_path / "txncount-0.3.0.apk"
    apk.write_bytes(b"synthetic signed apk")
    monkeypatch.setattr(tasks, "android_build_tool", lambda name: name)
    monkeypatch.setattr(
        tasks,
        "capture_command",
        lambda *args: (
            "Signer #1 certificate SHA-256 digest: " + "0" * 64
            if args[1] == "verify"
            else "package: name='com.pioneer.txncount' versionCode='22' versionName='0.3.0'\n"
        ),
    )
    with pytest.raises(Exit, match="does not match"):
        tasks.verify_android_release_apk(apk)


@pytest.mark.usefixtures("release_apk_identity")
@pytest.mark.parametrize(
    "bad_badging",
    [
        "package: name='com.other.app' versionCode='22' versionName='0.3.0'\n",
        "package: name='com.pioneer.txncount' versionCode='21' versionName='0.3.0'\n",
        "package: name='com.pioneer.txncount' versionCode='22' versionName='0.2.9'\n",
    ],
)
def test_release_apk_verification_rejects_wrong_package_or_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_badging: str,
) -> None:
    apk = tmp_path / "txncount-0.3.0.apk"
    apk.write_bytes(b"synthetic signed apk")
    monkeypatch.setattr(tasks, "android_build_tool", lambda name: name)
    monkeypatch.setattr(
        tasks,
        "capture_command",
        lambda *args: (
            "Signer #1 certificate SHA-256 digest: "
            "0cc03e9537ef041c51eb8ececa1ae9e246c7e5a2c814ff1dd9a7f19ab6c07fa6"
            if args[1] == "verify"
            else bad_badging
        ),
    )
    with pytest.raises(Exit, match="application ID|version metadata"):
        tasks.verify_android_release_apk(apk)


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
    assert calls == [
        f"{sys.executable} tools/locales.py validate",
        f"{sys.executable} tools/locales.py compile",
        "ruff format --check .",
        "ruff check .",
        "pyright",
        f"{sys.executable} tools/locales.py compile",
        "pytest",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "verify_flet",
        "probe_beancount",
        "beancount_diagnostics",
        "check_beancount_apk",
        "probe_beancount_android",
        "check_compat",
        "translations_validate",
        "translations_compile",
        "translations_template",
        "translations_init",
    ],
)
def test_python_tasks_use_the_invoke_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    execute = MagicMock()
    monkeypatch.setattr(tasks, "execute", execute)
    operation = getattr(tasks, name)
    if name == "translations_init":
        operation(Context(), language="de")
    else:
        operation(Context())
    assert execute.call_args.args[0] == sys.executable


def test_translation_validation_does_not_depend_on_python_path_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    tasks.translations_validate(Context())


@pytest.mark.parametrize("prefix", ["../bad", "a//b", "a/./b", "a b", "a?b"])
def test_invalid_prefix(prefix: str) -> None:
    with pytest.raises(Exit):
        tasks.normalize_prefix(prefix)


def test_mobile_debug_marker_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "src/app").mkdir(parents=True)
    marker = tmp_path / "src/app/.debug-mode"
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)

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
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "execute", MagicMock())
    tasks.build_target("web")
    assert not marker.exists()


def test_builds_are_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock()
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)
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
    monkeypatch.setattr(tasks, "stage_assets", lambda **kwargs: None)
    execute = MagicMock()
    monkeypatch.setattr(tasks, "execute", execute)
    tasks.debug_mobile("android", "", True)
    assert execute.call_args.args[-3:] == ("--device-id", "discover", "--show-devices")
    assert not (tmp_path / "src/app/.debug-mode").exists()


def test_check_stops_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fail_on_format(*args: str, **kwargs: object) -> None:
        calls.append(args)
        if args[:2] == ("ruff", "format"):
            raise Exit("format failed", code=1)

    monkeypatch.setattr(tasks, "execute", fail_on_format)
    with pytest.raises(Exit, match="format failed"):
        tasks.check(Context())
    assert calls == [
        (sys.executable, "tools/locales.py", "validate"),
        (sys.executable, "tools/locales.py", "compile"),
        ("ruff", "format", "--check", "."),
    ]


def test_command_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tasks.subprocess, "run", MagicMock(side_effect=subprocess.CalledProcessError(7, "flet"))
    )
    with pytest.raises(Exit) as failure:
        tasks.execute("flet", "build", "web")
    assert failure.value.code == 7


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
    monkeypatch.setattr(tasks, "compile_translations", lambda: None)
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
