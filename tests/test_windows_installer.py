import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from tools import linux_appimage, windows_installer


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.3.7"\n[tool.flet]\nproduct = "TxnCount"\n'
        'company = "Pioneer"\nbundle_id = "com.pioneer.txncount"\n'
    )
    source = tmp_path / "build/windows"
    (source / "data/flutter_assets").mkdir(parents=True)
    for name in ("txncount.exe", "flutter_windows.dll"):
        (source / name).write_bytes(b"synthetic")
    (tmp_path / "assets").mkdir()
    Image.new("RGB", (1024, 1024), "green").save(tmp_path / "assets/icon.png")
    (tmp_path / "src/assets").mkdir(parents=True)
    Image.new("RGBA", (1024, 1024), (0, 128, 0, 128)).save(tmp_path / "src/assets/icon_windows.png")
    return tmp_path


def test_installer_packages_entire_bundle_app_local_runtime_and_generated_icon(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = bundle / "runtime"
    runtime.mkdir()
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"):
        (runtime / name).write_bytes(name.encode())
    monkeypatch.setattr(windows_installer, "runtime_directory", lambda: runtime)
    monkeypatch.setattr(windows_installer, "compiler_path", lambda: Path("ISCC.exe"))

    def run(*args: str, capture: bool = True) -> str:
        assert not capture
        defines = dict(arg[2:].split("=", 1) for arg in args if arg.startswith("/D"))
        assert defines["AppId"] == "com.pioneer.txncount"
        assert defines["AppVersion"] == "0.3.7"
        source = Path(defines["SourceDir"])
        assert (source / "data/flutter_assets").is_dir()
        assert (source / "concrt140.dll").read_bytes() == b"concrt140.dll"
        assert (source / "txncount.exe").read_bytes() == b"synthetic"
        with Image.open(defines["SetupIcon"]) as icon:
            assert icon.format == "ICO"
            pixel = icon.convert("RGBA").getpixel((0, 0))
            assert isinstance(pixel, tuple)
            assert pixel[3] == 128
        output = Path(defines["OutputDir"]) / f"{defines['OutputName']}.exe"
        output.write_bytes(b"synthetic installer")
        return ""

    monkeypatch.setattr(windows_installer, "run", run)
    result = windows_installer.build_installer(bundle)
    assert result == bundle / "dist/windows/TxnCount-0.3.7-windows-x64-setup.exe"
    assert result.read_bytes() == b"synthetic installer"
    assert list((bundle / "build/windows-installer").iterdir()) == []


def test_missing_bundle_prevents_compilation(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (bundle / "build/windows/flutter_windows.dll").unlink()
    run = MagicMock()
    monkeypatch.setattr(windows_installer, "run", run)
    with pytest.raises(RuntimeError, match="bundle is incomplete"):
        windows_installer.build_installer(bundle)
    run.assert_not_called()


def test_missing_desktop_icon_prevents_compilation(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (bundle / "src/assets/icon_windows.png").unlink()
    command = MagicMock()
    monkeypatch.setattr(windows_installer, "run", command)
    with pytest.raises(RuntimeError, match="staged Windows desktop icon is missing"):
        windows_installer.build_installer(bundle)
    command.assert_not_called()


def test_missing_compiler_is_an_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_installer.shutil, "which", lambda name: None)
    monkeypatch.setenv("PROGRAMFILES(X86)", "/missing-synthetic-program-files")
    with pytest.raises(RuntimeError, match="Inno Setup 6"):
        windows_installer.compiler_path()


def test_missing_runtime_prevents_installer_compilation(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = bundle / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(windows_installer, "runtime_directory", lambda: runtime)
    command = MagicMock()
    monkeypatch.setattr(windows_installer, "run", command)
    with pytest.raises(RuntimeError, match="runtime is incomplete"):
        windows_installer.build_installer(bundle)
    command.assert_not_called()


def test_successful_compiler_without_installer_output_fails_explicitly(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = bundle / "runtime"
    runtime.mkdir()
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        (runtime / name).write_bytes(b"synthetic runtime")
    monkeypatch.setattr(windows_installer, "runtime_directory", lambda: runtime)
    monkeypatch.setattr(windows_installer, "compiler_path", lambda: Path("ISCC.exe"))
    monkeypatch.setattr(windows_installer, "run", MagicMock(return_value=""))
    with pytest.raises(RuntimeError, match="nonempty installer"):
        windows_installer.build_installer(bundle)


def test_compiler_output_is_streamed_for_build_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=None))
    monkeypatch.setattr(windows_installer.subprocess, "run", command)
    assert windows_installer.run("ISCC.exe", capture=False) == ""
    assert command.call_args.kwargs["stdout"] is None


def test_installer_smoke_is_restricted_to_disposable_ci(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="disposable GitHub runners"):
        windows_installer.smoke_installer(bundle)


def test_installer_smoke_installs_twice_and_preserves_disposable_data(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    artifact = bundle / "dist/windows/TxnCount-0.3.7-windows-x64-setup.exe"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"installer")
    installed: Path | None = None
    calls: list[tuple[str, ...]] = []
    uninstall_child_running = False
    temporary_directory = windows_installer.tempfile.TemporaryDirectory

    @contextmanager
    def stage_directory(*, prefix: str) -> Iterator[str]:
        with temporary_directory(prefix=prefix) as directory:
            yield directory
            assert not uninstall_child_running, "Uninstaller child still holds uninstall.log open"

    def run(*args: str) -> str:
        nonlocal installed, uninstall_child_running
        calls.append(args)
        if args[0] == str(artifact):
            installed = Path(next(arg[5:] for arg in args if arg.startswith("/DIR=")))
            installed.mkdir(exist_ok=True)
            (installed / "txncount.exe").write_bytes(b"exe")
            (installed / "unins000.exe").write_bytes(b"uninstaller")
        elif args[0].endswith("unins000.exe"):
            assert installed is not None
            (installed / "txncount.exe").unlink()
            uninstall_child_running = True
        elif args[0] == "powershell":
            if "Start-Process" in args[-1]:
                assert "-Wait" in args[-1] and "-PassThru" in args[-1]
                assert "exit $process.ExitCode" in args[-1]
                assert installed is not None
                (installed / "txncount.exe").unlink()
                uninstall_child_running = False
                return ""
            return "ready"
        return ""

    process = MagicMock()
    process.pid = 123
    process.poll.return_value = None
    popen = MagicMock()
    popen.return_value.__enter__.return_value = process
    monkeypatch.setattr(windows_installer, "run", run)
    monkeypatch.setattr(windows_installer.tempfile, "TemporaryDirectory", stage_directory)
    monkeypatch.setattr(windows_installer.subprocess, "Popen", popen)
    monkeypatch.setattr(windows_installer.time, "sleep", lambda seconds: None)
    windows_installer.smoke_installer(bundle)
    assert len([call for call in calls if call[0] == str(artifact)]) == 2
    assert any("unins000.exe" in call[-1] and "Start-Process" in call[-1] for call in calls)
    assert popen.call_args.kwargs["env"]["APPDATA"] != os.environ.get("APPDATA")
    process.terminate.assert_called_once()


def test_packaging_command_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        windows_installer.subprocess,
        "run",
        MagicMock(side_effect=subprocess.CalledProcessError(1, ["ISCC.exe"])),
    )
    with pytest.raises(RuntimeError, match="Windows packaging command failed") as error:
        windows_installer.run("ISCC.exe")
    assert isinstance(error.value.__cause__, subprocess.CalledProcessError)


def test_uninstall_waits_for_descendants_with_literal_paths_and_propagates_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(r"C:\Runner's temp\$stage; [test]\installed\unins000.exe")
    log = Path(r"C:\Runner's temp\$stage; [test]\uninstall.log")
    command = MagicMock(return_value="")
    monkeypatch.setattr(windows_installer, "run", command)
    windows_installer.uninstall(executable, log)
    command.assert_called_once_with(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$ErrorActionPreference = 'Stop'; "
        "$process = Start-Process "
        r"-FilePath 'C:\Runner''s temp\$stage; [test]\installed\unins000.exe' "
        "-ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART "
        r"""/LOG="C:\Runner''s temp\$stage; [test]\uninstall.log"' """
        "-Wait -PassThru; exit $process.ExitCode",
    )


def test_uninstall_failure_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        windows_installer.subprocess,
        "run",
        MagicMock(side_effect=subprocess.CalledProcessError(5, ["powershell"])),
    )
    with pytest.raises(RuntimeError, match="Windows packaging command failed") as error:
        windows_installer.uninstall(Path("unins000.exe"), Path("uninstall.log"))
    assert isinstance(error.value.__cause__, subprocess.CalledProcessError)
    assert error.value.__cause__.returncode == 5


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows process-tree waiting.")
def test_uninstall_waits_for_real_child_process_on_windows(tmp_path: Path) -> None:
    stage = tmp_path / "uninstaller's temporary files"
    stage.mkdir()
    log = stage / "uninstall.log"
    finished = stage / "child-finished"
    child = stage / "child.py"
    child.write_text(
        "import time\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('w') as stream:\n"
        "    stream.write('synthetic uninstaller log')\n"
        "    stream.flush()\n"
        "    time.sleep(1)\n"
        f"Path({str(finished)!r}).write_text('done')\n",
        encoding="utf-8",
    )
    parent = stage / "uninstaller.cmd"
    parent.write_text(
        f'@echo off\nstart "" /b "{sys.executable}" "{child}"\nexit /b 0\n',
        encoding="utf-8",
    )
    windows_installer.uninstall(parent, log)
    assert finished.read_text() == "done"
    log.unlink()


def test_installer_is_per_user_and_never_deletes_data() -> None:
    script = (Path(__file__).resolve().parents[1] / "tools/windows-installer.iss").read_text()
    assert "AppId={#AppId}" in script
    assert "PrivilegesRequired=lowest" in script
    assert "DefaultDirName={localappdata}\\Programs\\{#AppName}" in script
    assert "ArchitecturesAllowed=x64compatible" in script
    assert "recursesubdirs createallsubdirs" in script
    assert "[UninstallDelete]" not in script and "[InstallDelete]" not in script
    assert "preferences.json" not in script and ".beancount" not in script
    assert "desktopicon" in script and "uninsdeletekey" not in script


def test_appimage_architecture_does_not_use_posix_only_uname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(linux_appimage.platform, "machine", lambda: "x86_64")
    assert linux_appimage.platform_architecture() == "x86_64"
