"""Build and inspect the relocatable Linux AppImage from Flet's bundled output."""

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

LINUXDEPLOY_URL = (
    "https://github.com/linuxdeploy/linuxdeploy/releases/download/"
    "1-alpha-20251107-1/linuxdeploy-x86_64.AppImage"
)
LINUXDEPLOY_SHA256 = "c20cd71e3a4e3b80c3483cef793cda3f4e990aca14014d23c544ca3ce1270b4d"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)
APPIMAGETOOL_SHA256 = "95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6"
GTK_PLUGIN_URL = (
    "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/"
    "7a3fbc31a9e5075073ff8790f26effbac5f84453/linuxdeploy-plugin-gtk.sh"
)
GTK_PLUGIN_SHA256 = "b0f4cbc684a0103a9651f0955b635eaea0096b3a66c0f5a2c2aa337960375171"


def run_command(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ | (env or {})
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=process_env,
            check=False,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required Linux packaging command not found: {args[0]}") from exc


def _download_verified(url: str, expected_sha256: str, destination: Path) -> None:
    if destination.is_symlink():
        raise RuntimeError(f"Refusing to replace tool symlink: {destination}")
    if destination.is_file() and _sha256(destination) == expected_sha256:
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    digest = hashlib.sha256()
    try:
        with (
            urllib.request.urlopen(url, timeout=90) as response,
            tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
            ) as stream,
        ):
            temporary = Path(stream.name)
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError(f"SHA-256 verification failed for {destination.name}")
        temporary.chmod(0o755)
        temporary.replace(destination)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(
            f"Could not download verified Linux packaging tool {destination.name}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_toolchain(tool_dir: Path) -> tuple[Path, Path, Path]:
    if platform_architecture() != "x86_64":
        raise RuntimeError("TxnCount AppImage packaging currently supports Linux x86_64 only.")
    if not shutil.which("mksquashfs") or not shutil.which("unsquashfs"):
        raise RuntimeError("Install squashfs-tools (mksquashfs and unsquashfs) to build AppImages.")
    if not shutil.which("desktop-file-validate"):
        raise RuntimeError("Install desktop-file-utils to validate the AppImage desktop entry.")
    if not shutil.which("file"):
        raise RuntimeError("The `file` utility is required to validate AppImage architecture.")

    linuxdeploy_appimage = tool_dir / "linuxdeploy-x86_64.AppImage"
    appimagetool = tool_dir / "appimagetool-x86_64.AppImage"
    gtk_plugin = tool_dir / "linuxdeploy-plugin-gtk"
    _download_verified(LINUXDEPLOY_URL, LINUXDEPLOY_SHA256, linuxdeploy_appimage)
    _download_verified(APPIMAGETOOL_URL, APPIMAGETOOL_SHA256, appimagetool)
    _download_verified(GTK_PLUGIN_URL, GTK_PLUGIN_SHA256, gtk_plugin)
    for path in (linuxdeploy_appimage, appimagetool, gtk_plugin):
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    extraction_dir = tool_dir / "linuxdeploy-extract"
    if extraction_dir.is_symlink():
        raise RuntimeError("Refusing to replace the linuxdeploy extraction symlink.")
    if extraction_dir.exists():
        shutil.rmtree(extraction_dir)
    extraction_dir.mkdir()
    extracted = run_command(
        str(linuxdeploy_appimage), "--appimage-extract", cwd=extraction_dir, capture=True
    )
    if extracted.returncode:
        raise RuntimeError("Could not extract the verified linuxdeploy AppImage.")
    linuxdeploy_root = extraction_dir / "squashfs-root"
    linuxdeploy = linuxdeploy_root / "usr/bin/linuxdeploy"
    bundled_strip = linuxdeploy_root / "usr/bin/strip"
    host_strip = shutil.which("strip")
    if not linuxdeploy.is_file() or not bundled_strip.is_file() or host_strip is None:
        raise RuntimeError("Verified linuxdeploy bundle lacks its executable or host strip tool.")
    shutil.copy2(host_strip, bundled_strip)
    linuxdeploy.chmod(0o755)
    return linuxdeploy, appimagetool, gtk_plugin


def platform_architecture() -> str:
    return platform.machine()


def _runtime_from_tool(appimagetool: Path, runtime_path: Path) -> None:
    result = run_command(
        str(appimagetool),
        "--appimage-offset",
        env={"APPIMAGE_EXTRACT_AND_RUN": "1"},
        capture=True,
    )
    if result.returncode:
        raise RuntimeError("Could not inspect the verified appimagetool runtime.")
    try:
        offset = int(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("appimagetool returned an invalid AppImage runtime offset.") from exc
    if offset < 4096 or offset >= appimagetool.stat().st_size:
        raise RuntimeError("appimagetool returned an out-of-range AppImage runtime offset.")
    with appimagetool.open("rb") as source, runtime_path.open("wb") as target:
        target.write(source.read(offset))
    runtime_path.chmod(0o755)
    if runtime_path.read_bytes()[:4] != b"\x7fELF":
        raise RuntimeError("The extracted AppImage runtime is not an ELF executable.")


def _write_appdir(build_dir: Path, app_dir: Path, icon: Path) -> tuple[Path, Path]:
    binary = build_dir / "txncount"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"Flet Linux executable is missing or not executable: {binary}")
    if not icon.is_file():
        raise RuntimeError(f"Linux application icon is missing: {icon}")
    if app_dir.is_symlink():
        raise RuntimeError("Refusing to replace the AppDir symlink.")
    if app_dir.exists():
        shutil.rmtree(app_dir)

    executable_dir = app_dir / "usr/bin"
    executable_dir.parent.mkdir(parents=True)
    shutil.copytree(build_dir, executable_dir)
    shutil.copyfile(icon, app_dir / "com.pioneer.txncount.png")
    desktop_file = app_dir / "com.pioneer.txncount.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Name=TxnCount\n"
        "Comment=Offline-first personal finance journal\n"
        "Exec=txncount\n"
        "Icon=com.pioneer.txncount\n"
        "Terminal=false\n"
        "Type=Application\n"
        "Categories=Office;Utility;\n"
        "StartupWMClass=com.pioneer.txncount\n",
        encoding="utf-8",
    )
    return executable_dir / "txncount", desktop_file


def build_appimage(
    *,
    build_dir: Path,
    project_root: Path,
    version: str,
) -> Path:
    dist_dir = project_root / "dist/linux"
    tool_dir = project_root / "build/linux-appimage-tools"
    app_dir = project_root / "build/linux-appimage.AppDir"
    runtime_path = tool_dir / "runtime-x86_64"
    dist_dir.mkdir(parents=True, exist_ok=True)
    linuxdeploy, appimagetool, gtk_plugin = ensure_toolchain(tool_dir)
    icon = build_dir / "share/icons/hicolor/512x512/apps/com.pioneer.txncount.png"
    executable, desktop_file = _write_appdir(build_dir, app_dir, icon)

    validation = run_command("desktop-file-validate", str(desktop_file), capture=True)
    if validation.returncode:
        raise RuntimeError("Generated AppImage desktop entry failed desktop-file-validate.")

    tool_env = {
        "APPIMAGE_EXTRACT_AND_RUN": "1",
        "ARCH": "x86_64",
        "PATH": f"{gtk_plugin.parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    deployment = run_command(
        str(linuxdeploy),
        "--appdir",
        str(app_dir),
        "--executable",
        str(executable),
        "--desktop-file",
        str(desktop_file),
        "--icon-file",
        str(app_dir / "com.pioneer.txncount.png"),
        "--plugin",
        "gtk",
        env=tool_env,
    )
    if deployment.returncode:
        raise RuntimeError(f"linuxdeploy failed with exit code {deployment.returncode}.")
    if not (app_dir / "AppRun").is_file():
        raise RuntimeError("linuxdeploy did not create an AppRun launcher.")

    _runtime_from_tool(appimagetool, runtime_path)
    artifact = dist_dir / f"TxnCount-{version}-x86_64.AppImage"
    artifact.unlink(missing_ok=True)
    packaging = run_command(
        str(appimagetool),
        str(app_dir),
        str(artifact),
        env=tool_env | {"LDAI_RUNTIME_FILE": str(runtime_path), "LDAI_NO_APPSTREAM": "1"},
        cwd=project_root,
    )
    if packaging.returncode:
        raise RuntimeError(f"appimagetool failed with exit code {packaging.returncode}.")
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError("appimagetool did not produce a nonempty AppImage.")
    artifact.chmod(0o755)
    return artifact


def validate_appimage(artifact: Path, *, version: str, project_root: Path) -> Path:
    expected_name = f"TxnCount-{version}-x86_64.AppImage"
    if artifact.name != expected_name or not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError(f"Expected nonempty AppImage artifact {expected_name}.")
    if not os.access(artifact, os.X_OK):
        raise RuntimeError("AppImage artifact is not executable.")

    file_result = run_command("file", str(artifact), capture=True)
    if (
        file_result.returncode
        or "ELF 64-bit" not in file_result.stdout
        or "x86-64" not in file_result.stdout
    ):
        raise RuntimeError("AppImage runtime is not a 64-bit x86-64 ELF executable.")

    offset_result = run_command(
        str(artifact),
        "--appimage-offset",
        env={"APPIMAGE_EXTRACT_AND_RUN": "1"},
        capture=True,
    )
    if offset_result.returncode:
        raise RuntimeError("AppImage runtime could not report its embedded filesystem offset.")
    try:
        offset = int(offset_result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("AppImage returned an invalid embedded filesystem offset.") from exc

    extracted = project_root / "build/linux-appimage-validated"
    if extracted.is_symlink():
        raise RuntimeError("Refusing to replace the AppImage validation symlink.")
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True)
    unpack = run_command(
        "unsquashfs",
        "-no-progress",
        "-offset",
        str(offset),
        "-dest",
        str(extracted),
        str(artifact),
        capture=True,
    )
    if unpack.returncode:
        raise RuntimeError(f"unsquashfs failed with exit code {unpack.returncode}.")

    app_dir = extracted
    required = (
        app_dir / "AppRun",
        app_dir / "com.pioneer.txncount.desktop",
        app_dir / "com.pioneer.txncount.png",
        app_dir / "usr/bin/txncount",
        app_dir / "usr/bin/lib/libflutter_linux_gtk.so",
        app_dir / "usr/bin/lib/libpython3.14.so.1.0",
        app_dir / "usr/bin/app/app/.release.json",
        app_dir / "usr/bin/app/app/locale/en/LC_MESSAGES/txncount.mo",
    )
    missing = [str(path.relative_to(app_dir)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Extracted AppImage is missing required files: {', '.join(missing)}")
    desktop_check = run_command(
        "desktop-file-validate", str(app_dir / "com.pioneer.txncount.desktop"), capture=True
    )
    if desktop_check.returncode:
        raise RuntimeError("Extracted AppImage desktop entry failed desktop-file-validate.")
    print(f"Validated {artifact} ({artifact.stat().st_size} bytes); extracted bundled runtime.")
    return app_dir


def smoke_test_appimage(artifact: Path, *, timeout_seconds: int = 20) -> None:
    xvfb_run = shutil.which("xvfb-run")
    timeout = shutil.which("timeout")
    if xvfb_run is None or timeout is None:
        raise RuntimeError("AppImage startup smoke test requires xvfb-run and timeout.")
    result = run_command(
        xvfb_run,
        "-a",
        timeout,
        "--signal=TERM",
        "--kill-after=5s",
        f"{timeout_seconds}s",
        str(artifact),
        env={"APPIMAGE_EXTRACT_AND_RUN": "1"},
        capture=True,
    )
    if result.returncode not in {124, 137}:
        raise RuntimeError(
            f"AppImage did not remain running during the {timeout_seconds}s GUI smoke test "
            f"(exit code {result.returncode})."
        )
    print(f"AppImage stayed running for the {timeout_seconds}s Xvfb startup smoke test.")
