import hashlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from invoke.context import Context
from invoke.exceptions import Exit

import tasks


@pytest.fixture
def setup_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    key = tmp_path / "test.jks"
    key.write_bytes(b"synthetic keystore")
    key.chmod(0o600)
    monkeypatch.setattr(tasks, "check_android_release_tools", lambda: None)
    monkeypatch.setattr(tasks.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(tasks.getpass, "getpass", lambda prompt: "synthetic password")
    monkeypatch.setattr(
        tasks,
        "android_release_configuration",
        lambda: ("com.pioneer.textmoney", "android-release", hashlib.sha256(b"cert").hexdigest()),
    )
    return key


def test_setup_checks_key_without_uploading(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(return_value=b"cert")
    monkeypatch.setattr(tasks, "release_setup_command", run)
    tasks.setup_android_release(Context(), keystore=str(setup_key))
    assert [call.args[1] for call in run.call_args_list] == ["-exportcert", "-certreq"]
    assert setup_key.read_bytes() == b"synthetic keystore"
    for call in run.call_args_list:
        assert "synthetic password" not in call.args


def test_setup_upload_requires_confirmation_and_uses_stdin(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(return_value=b"cert")
    monkeypatch.setattr(tasks, "release_setup_command", run)
    monkeypatch.setattr("builtins.input", lambda prompt: "UPLOAD")
    tasks.setup_android_release(Context(), keystore=str(setup_key), upload_secrets=True)
    uploads = [call for call in run.call_args_list if call.args[:3] == ("gh", "secret", "set")]
    assert [call.args[3] for call in uploads] == [
        "ANDROID_KEYSTORE_BASE64",
        "ANDROID_KEYSTORE_PASSWORD",
        "ANDROID_KEY_PASSWORD",
    ]
    assert all(call.kwargs["body"] for call in uploads)
    assert all("--env" in call.args and "android-production" in call.args for call in uploads)
    assert all("synthetic password" not in call.args for call in uploads)


def test_setup_cancel_does_not_upload(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(return_value=b"cert")
    monkeypatch.setattr(tasks, "release_setup_command", run)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    with pytest.raises(Exit, match="Cancelled"):
        tasks.setup_android_release(Context(), keystore=str(setup_key), upload_secrets=True)
    assert not any(call.args[:3] == ("gh", "secret", "set") for call in run.call_args_list)


def test_setup_wrong_certificate_never_uploads(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(return_value=b"wrong cert")
    monkeypatch.setattr(tasks, "release_setup_command", run)
    with pytest.raises(Exit, match="pinned production certificate"):
        tasks.setup_android_release(Context(), keystore=str(setup_key), upload_secrets=True)
    assert run.call_count == 1


def test_setup_tool_failures_hide_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tasks.subprocess,
        "run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                ["keytool", "-certreq"],
                1,
                b"synthetic secret",
                b"synthetic secret",
            )
        ),
    )
    with pytest.raises(Exit) as failure:
        tasks.release_setup_command("keytool", "-certreq")
    assert "synthetic secret" not in str(failure.value)
    assert "withheld" in str(failure.value)


def test_old_java_gives_manual_installation_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        tasks.subprocess,
        "run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                ["java", "-version"],
                0,
                "",
                'openjdk version "11.0.1"',
            )
        ),
    )
    with pytest.raises(Exit, match="sudo pacman"):
        tasks.check_android_release_tools()


def test_setup_private_key_password_failure_stops_before_upload(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(side_effect=[b"cert", Exit("key password invalid")])
    monkeypatch.setattr(tasks, "release_setup_command", run)
    with pytest.raises(Exit, match="key password invalid"):
        tasks.setup_android_release(Context(), keystore=str(setup_key), upload_secrets=True)
    assert run.call_count == 2


def test_setup_rejects_noninteractive_prompts(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks.sys.stdin, "isatty", lambda: False)
    prompt = MagicMock()
    monkeypatch.setattr(tasks.getpass, "getpass", prompt)
    with pytest.raises(Exit, match="interactive terminal"):
        tasks.setup_android_release(Context(), keystore=str(setup_key))
    prompt.assert_not_called()


def test_setup_upload_failure_does_not_claim_success(
    setup_key: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = MagicMock(side_effect=[b"cert", b"csr", b"{}", b"", Exit("upload failed")])
    monkeypatch.setattr(tasks, "release_setup_command", run)
    monkeypatch.setattr("builtins.input", lambda prompt: "UPLOAD")
    with pytest.raises(Exit, match="upload failed"):
        tasks.setup_android_release(Context(), keystore=str(setup_key), upload_secrets=True)
    output = capsys.readouterr().out
    assert "Uploads are not atomic" in output
    assert "Uploaded ANDROID_KEYSTORE_BASE64" in output
    assert "Signing secrets configured" not in output


def test_setup_verifies_disposable_real_key_without_modifying_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keytool = shutil.which("keytool")
    if keytool is None:
        pytest.skip("keytool is not installed")
    key = tmp_path / "disposable.jks"
    env = {"TEXTMONEY_TEST_PASSWORD": "disposable-test-password"}
    tasks.release_setup_command(
        keytool,
        "-genkeypair",
        "-alias",
        "android-release",
        "-keyalg",
        "RSA",
        "-keysize",
        "2048",
        "-validity",
        "1",
        "-dname",
        "CN=Disposable Test",
        "-keystore",
        str(key),
        "-storetype",
        "JKS",
        "-storepass:env",
        "TEXTMONEY_TEST_PASSWORD",
        "-keypass:env",
        "TEXTMONEY_TEST_PASSWORD",
        env=env,
    )
    key.chmod(0o600)
    original = key.read_bytes()
    certificate = tasks.release_setup_command(
        keytool,
        "-exportcert",
        "-alias",
        "android-release",
        "-keystore",
        str(key),
        "-storepass:env",
        "TEXTMONEY_TEST_PASSWORD",
        env=env,
    )
    monkeypatch.setattr(tasks, "check_android_release_tools", lambda: None)
    monkeypatch.setattr(tasks.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(tasks.getpass, "getpass", lambda prompt: env["TEXTMONEY_TEST_PASSWORD"])
    monkeypatch.setattr(
        tasks,
        "android_release_configuration",
        lambda: (
            "com.pioneer.textmoney",
            "android-release",
            hashlib.sha256(certificate).hexdigest(),
        ),
    )
    tasks.setup_android_release(Context(), keystore=str(key))
    assert key.read_bytes() == original
