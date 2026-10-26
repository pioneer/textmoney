"""Package the Flet Windows bundle; keep installer identity in pyproject.toml."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

from PIL import Image


def run(*args: str, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            args, check=True, text=True, stdout=subprocess.PIPE if capture else None
        )
        return "" if result.stdout is None else result.stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"Windows packaging command failed: {args[0]}. See tool diagnostics."
        ) from exc


def installer_name(version: str) -> str:
    return f"TxnCount-{version}-windows-x64-setup.exe"


def compiler_path() -> Path:
    if compiler := shutil.which("ISCC.exe"):
        return Path(compiler)
    candidate = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    candidate /= "Inno Setup 6/ISCC.exe"
    if not candidate.is_file():
        raise RuntimeError("Inno Setup 6 is required; ISCC.exe was not found.")
    return candidate


def runtime_directory() -> Path:
    base = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    vswhere = base / "Microsoft Visual Studio/Installer/vswhere.exe"
    installation = run(
        str(vswhere),
        "-latest",
        "-products",
        "*",
        "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-property",
        "installationPath",
    )
    if not installation:
        raise RuntimeError("Visual Studio with the x64 C++ workload was not found.")
    candidates = list((Path(installation) / "VC/Redist/MSVC").glob("*/x64/Microsoft.VC*.CRT"))
    if not candidates:
        raise RuntimeError("Visual Studio x64 redistributable DLLs were not found.")
    return max(candidates, key=lambda path: tuple(int(p) for p in path.parents[1].name.split(".")))


def build_installer(root: Path) -> Path:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    identity = project["tool"]["flet"]
    source = root / "build/windows"
    for relative in ("txncount.exe", "flutter_windows.dll", "data/flutter_assets"):
        if not (source / relative).exists():
            raise RuntimeError(f"Windows bundle is incomplete: missing {relative}. Build it first.")
    icon_source = root / "src/assets/icon_windows.png"
    if not icon_source.is_file():
        raise RuntimeError("The staged Windows desktop icon is missing. Build through Invoke.")
    output = root / "dist/windows"
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / installer_name(version)
    artifact.unlink(missing_ok=True)
    staging = root / "build/windows-installer"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging) as temporary:
        stage = Path(temporary)
        bundle = stage / "bundle"
        shutil.copytree(source, bundle)
        runtime = runtime_directory()
        for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            if not (runtime / name).is_file():
                raise RuntimeError(f"Visual Studio runtime is incomplete: missing {name}.")
        for dll in runtime.glob("*.dll"):
            shutil.copy2(dll, bundle / dll.name)
        icon = stage / "setup.ico"
        with Image.open(icon_source) as image:
            image.save(icon, format="ICO", sizes=[(n, n) for n in (16, 32, 48, 64, 128, 256)])
        defines = {
            "AppId": identity["bundle_id"],
            "AppName": identity["product"],
            "AppPublisher": identity["company"],
            "AppVersion": version,
            "SourceDir": str(bundle),
            "SetupIcon": str(icon),
            "OutputDir": str(output),
            "OutputName": artifact.stem,
        }
        run(
            str(compiler_path()),
            *(f"/D{key}={value}" for key, value in defines.items()),
            str(root / "tools/windows-installer.iss"),
            capture=False,
        )
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError("Inno Setup did not produce a nonempty installer.")
    return artifact


def uninstall(executable: Path, log: Path) -> None:
    program = str(executable).replace("'", "''")
    arguments = f'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{log}"'.replace("'", "''")
    # Inno's original process can exit before its temporary uninstaller child.
    run(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$ErrorActionPreference = 'Stop'; "
        f"$process = Start-Process -FilePath '{program}' -ArgumentList '{arguments}' "
        "-Wait -PassThru; exit $process.ExitCode",
    )


def smoke_installer(root: Path) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Installer smoke testing is restricted to disposable GitHub runners.")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    artifact = root / "dist/windows" / installer_name(project["project"]["version"])
    if not artifact.is_file():
        raise RuntimeError("Build the Windows installer before running its smoke test.")
    with tempfile.TemporaryDirectory(prefix="txncount-installer-") as temporary:
        stage = Path(temporary)
        install = stage / "installed"
        data = stage / "journal"
        data.mkdir()
        journal = data / "txncount.beancount"
        original = b'; Synthetic installer smoke-test journal.\noption "operating_currency" "EUR"\n'
        journal.write_bytes(original)
        preferences = stage / "config/txncount/preferences.json"
        preferences.parent.mkdir(parents=True)
        preferences.write_text(
            json.dumps({"txncount.folder.v1": str(data), "txncount.language.v1": "en"}),
            encoding="utf-8",
        )
        settings_before = preferences.read_bytes()
        executable = install / "txncount.exe"
        for _ in range(2):
            run(
                str(artifact),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
                f"/DIR={install}",
                f"/LOG={stage / 'install.log'}",
            )
            if not executable.is_file():
                raise RuntimeError("The installer did not install txncount.exe.")
        env = os.environ | {
            "APPDATA": str(stage / "config"),
            "LOCALAPPDATA": str(stage / "local"),
        }
        with subprocess.Popen([str(executable)], env=env, cwd=install) as process:
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Installed application exited during startup.")
                    ready = run(
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"$p=Get-Process -Id {process.pid} -ErrorAction Stop; "
                        "if ($p.MainWindowHandle -ne 0 -and $p.Responding "
                        "-and $p.MainWindowTitle -eq 'TxnCount') { 'ready' }",
                    )
                    if ready == "ready":
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("Installed application did not open a responsive window.")
                time.sleep(5)
                if process.poll() is not None:
                    raise RuntimeError("Installed application exited after opening its window.")
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
        uninstall(install / "unins000.exe", stage / "uninstall.log")
        if executable.exists():
            raise RuntimeError("Uninstall left the application executable installed.")
        if journal.read_bytes() != original or preferences.read_bytes() != settings_before:
            raise RuntimeError("Install/reinstall/startup/uninstall changed the test data.")
    print("Windows installer install/reinstall, responsive-window and uninstall smoke test passed.")
