import hashlib
import io
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools import linux_appimage


def _valid_appdir(app_dir: Path) -> Path:
    files = (
        "AppRun",
        "com.pioneer.textmoney.desktop",
        "com.pioneer.textmoney.png",
        "usr/bin/textmoney",
        "usr/bin/lib/libflutter_linux_gtk.so",
        "usr/bin/lib/libpython3.14.so.1.0",
        "usr/bin/app/app/.release.json",
        "usr/bin/app/app/locale/en/LC_MESSAGES/textmoney.mo",
    )
    for relative in files:
        path = app_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
    hook = app_dir / "apprun-hooks/linuxdeploy-plugin-gtk.sh"
    hook.parent.mkdir()
    hook.write_text(linux_appimage.GTK_THEME_SETUP.read_text())
    return app_dir


def test_write_appdir_supports_paths_with_spaces_and_unicode(tmp_path: Path) -> None:
    root = tmp_path / "Finance records ö"
    build = root / "build/linux"
    icon = build / "share/icons/hicolor/512x512/apps/com.pioneer.textmoney.png"
    icon.parent.mkdir(parents=True)
    (build / "textmoney").write_bytes(b"elf")
    (build / "textmoney").chmod(0o755)
    icon.write_bytes(b"png")

    executable, desktop = linux_appimage._write_appdir(build, root / "dist/TextMoney.AppDir", icon)

    assert executable == root / "dist/TextMoney.AppDir/usr/bin/textmoney"
    assert executable.read_bytes() == b"elf"
    assert (desktop.parent / "com.pioneer.textmoney.png").read_bytes() == b"png"
    contents = desktop.read_text(encoding="utf-8")
    assert "Name=TextMoney\n" in contents
    assert "Exec=textmoney\n" in contents
    assert "Categories=Office;Utility;\n" in contents
    assert "StartupWMClass=com.pioneer.textmoney\n" in contents


def test_download_verified_pins_download_and_rejects_bad_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"verified packaging tool"
    destination = tmp_path / "cache/tool"
    opener = MagicMock(side_effect=lambda *args, **kwargs: io.BytesIO(payload))
    monkeypatch.setattr(linux_appimage.urllib.request, "urlopen", opener)

    linux_appimage._download_verified(
        "https://example.invalid/tool",
        hashlib.sha256(payload).hexdigest(),
        destination,
    )

    assert destination.read_bytes() == payload
    assert os.access(destination, os.X_OK)
    opener.assert_called_once()
    with pytest.raises(RuntimeError, match="SHA-256 verification failed"):
        linux_appimage._download_verified(
            "https://example.invalid/tool",
            "0" * 64,
            tmp_path / "cache/bad-tool",
        )


def test_download_verified_refuses_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"keep")
    destination = tmp_path / "tool"
    destination.symlink_to(real)
    with pytest.raises(RuntimeError, match="symlink"):
        linux_appimage._download_verified("unused", "0" * 64, destination)
    assert real.read_bytes() == b"keep"


def test_build_appimage_orchestrates_pinned_toolchain_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Finance folder ö"
    build = root / "build/linux"
    icon = build / "share/icons/hicolor/512x512/apps/com.pioneer.textmoney.png"
    icon.parent.mkdir(parents=True)
    (build / "textmoney").write_bytes(b"executable")
    (build / "textmoney").chmod(0o755)
    icon.write_bytes(b"icon")
    tool_dir = root / "build/linux-appimage-tools"
    linuxdeploy = tool_dir / "linuxdeploy.AppImage"
    appimagetool = tool_dir / "appimagetool.AppImage"
    gtk_plugin = tool_dir / "linuxdeploy-plugin-gtk"
    for path in (linuxdeploy, gtk_plugin):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tool")
    appimagetool.write_bytes(b"\x7fELF" + b"x" * 8192)
    monkeypatch.setattr(
        linux_appimage,
        "ensure_toolchain",
        lambda directory: (linuxdeploy, appimagetool, gtk_plugin),
    )

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[1:] == ("--appimage-offset",):
            return subprocess.CompletedProcess(args, 0, "4096\n", "")
        if args[0] == str(linuxdeploy):
            (root / "build/linux-appimage.AppDir/AppRun").write_text("#!/bin/sh\n")
            directory = root / "build/linux-appimage.AppDir/apprun-hooks"
            directory.mkdir()
            (directory / "linuxdeploy-plugin-gtk.sh").write_text(
                '#! /usr/bin/env bash\n\nCOLOR_SCHEME="synthetic"\n'
                'APPIMAGE_GTK_THEME="${APPIMAGE_GTK_THEME:-"Adwaita:$GTK_THEME_VARIANT"}"\n'
                'export GTK_DATA_PREFIX="$APPDIR"\n'
                'export GTK_THEME="$APPIMAGE_GTK_THEME" # Custom themes are broken\n'
                "export GDK_BACKEND=x11\n"
                'export XDG_DATA_DIRS="$APPDIR/usr/share:/usr/share:$XDG_DATA_DIRS" '
                "# g_get_system_data_dirs() from GLib\n"
                'export GTK_PATH="$APPDIR/usr/lib/gtk-3.0"\n'
            )
        if args[0] == str(appimagetool):
            hook = root / "build/linux-appimage.AppDir/apprun-hooks/linuxdeploy-plugin-gtk.sh"
            assert linux_appimage.GTK_THEME_SETUP.read_text() in hook.read_text()
            assert 'export GTK_PATH="$APPDIR/usr/lib/gtk-3.0"' in hook.read_text()
            Path(args[-1]).write_bytes(b"AppImage")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(linux_appimage, "run_command", run)
    artifact = linux_appimage.build_appimage(build_dir=build, project_root=root, version="1.2.3")

    assert artifact == root / "dist/linux/TextMoney-1.2.3-x86_64.AppImage"
    assert artifact.read_bytes() == b"AppImage"
    assert (root / "build/linux-appimage-tools/runtime-x86_64").read_bytes() == (
        b"\x7fELF" + b"x" * (4096 - 4)
    )


def test_validate_appimage_extracts_and_checks_bundled_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "TextMoney-1.2.3-x86_64.AppImage"
    artifact.write_bytes(b"AppImage")
    artifact.chmod(0o755)

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "file":
            return subprocess.CompletedProcess(args, 0, "ELF 64-bit LSB pie executable, x86-64", "")
        if args[1:] == ("--appimage-offset",):
            return subprocess.CompletedProcess(args, 0, "4096\n", "")
        if args[0] == "unsquashfs":
            destination = Path(args[args.index("-dest") + 1])
            _valid_appdir(destination)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(linux_appimage, "run_command", run)

    app_dir = linux_appimage.validate_appimage(artifact, version="1.2.3", project_root=tmp_path)

    assert app_dir == tmp_path / "build/linux-appimage-validated"


def test_validate_appimage_rejects_wrong_name_and_architecture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong_name = tmp_path / "textmoney.AppImage"
    wrong_name.write_bytes(b"not empty")
    with pytest.raises(RuntimeError, match="Expected nonempty AppImage"):
        linux_appimage.validate_appimage(wrong_name, version="1.2.3", project_root=tmp_path)

    artifact = tmp_path / "TextMoney-1.2.3-x86_64.AppImage"
    artifact.write_bytes(b"AppImage")
    artifact.chmod(0o755)
    monkeypatch.setattr(
        linux_appimage,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, "ELF 64-bit LSB pie executable, aarch64", ""
        ),
    )
    with pytest.raises(RuntimeError, match="x86-64"):
        linux_appimage.validate_appimage(artifact, version="1.2.3", project_root=tmp_path)


def test_validate_hook_refuses_an_unpatched_package(tmp_path: Path) -> None:
    directory = tmp_path / "apprun-hooks"
    directory.mkdir()
    (directory / "linuxdeploy-plugin-gtk.sh").write_text('export GTK_THEME="Adwaita:light"\n')
    with pytest.raises(RuntimeError, match="system GTK theme"):
        linux_appimage._validate_gtk_hook(tmp_path)


def test_smoke_test_requires_app_to_remain_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "TextMoney.AppImage"
    monkeypatch.setattr(linux_appimage.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        linux_appimage,
        "run_command",
        MagicMock(return_value=subprocess.CompletedProcess([], 124, "", "")),
    )

    linux_appimage.smoke_test_appimage(artifact)

    monkeypatch.setattr(
        linux_appimage,
        "run_command",
        MagicMock(return_value=subprocess.CompletedProcess([], 1, "", "")),
    )
    with pytest.raises(RuntimeError, match="did not remain running"):
        linux_appimage.smoke_test_appimage(artifact)
