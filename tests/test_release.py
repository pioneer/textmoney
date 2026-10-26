import hashlib
import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools import release

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
REPO = "pioneer/txncount"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "txncount"\nversion = "0.3.6"\n'
        "[tool.flet]\nbuild_number = 29\n"
        '[tool.txncount]\nother = "preserve"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("old lock\n", encoding="utf-8")
    return tmp_path


def commands(root: Path, **overrides: str) -> MagicMock:
    responses = {
        "git status --porcelain --untracked-files=all": "",
        "git branch --show-current": "main",
        "gh repo view --json nameWithOwner --jq .nameWithOwner": REPO,
        "git remote get-url origin": f"https://github.com/{REPO}.git",
        "git rev-parse HEAD": SHA,
        "git rev-parse origin/main": SHA,
        f"gh api repos/{REPO}/actions/workflows/release.yml --jq .state": "active",
        f"gh release list --repo {REPO} --limit 100 --json tagName,isDraft,isPrerelease": "[]",
        "git tag --list v0.3.7": "",
        "git diff --name-only": "pyproject.toml\nuv.lock",
        "git ls-files --others --exclude-standard": "",
    } | overrides

    def run(directory: Path, *args: str) -> str:
        assert directory == root
        command = " ".join(args)
        if command == "uv lock --offline":
            (root / "uv.lock").write_text("new lock\n", encoding="utf-8")
        return responses.get(command, "")

    return MagicMock(side_effect=run)


def test_release_prepares_only_version_files_and_pushes_atomically(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = commands(workspace)
    check = MagicMock()
    previous = MagicMock(return_value=31)
    wait = MagicMock()
    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        workspace,
        "0.3.7",
        resume=False,
        check=check,
        previous_build=previous,
    )
    project = tomllib.loads((workspace / "pyproject.toml").read_text())
    assert project["project"]["version"] == "0.3.7"
    assert project["tool"]["flet"]["build_number"] == 32
    assert project["tool"]["txncount"]["other"] == "preserve"
    check.assert_called_once()
    previous.assert_called_once_with("v0.3.7")
    calls = [call.args[1:] for call in run.call_args_list]
    assert ("git", "add", "--", "pyproject.toml", "uv.lock") in calls
    assert ("git", "commit", "-m", "Release v0.3.7", "-m", release.TRAILER) in calls
    assert (
        "git",
        "push",
        "--atomic",
        "origin",
        "HEAD:refs/heads/main",
        "refs/tags/v0.3.7",
    ) in calls
    assert not any(command[0] in {"flutter", "flet", "java"} for command in calls)
    wait.assert_called_once_with(workspace, REPO, "v0.3.7", SHA, resume=False)


@pytest.mark.parametrize(
    "override,message",
    [
        ({"git status --porcelain --untracked-files=all": " M user.txt"}, "worktree must be clean"),
        ({"git branch --show-current": "feature"}, "main branch"),
        ({"git rev-parse origin/main": "b" * 40}, "match origin/main"),
        ({"git remote get-url origin": "https://example.invalid/other"}, "origin must point"),
        ({"git tag --list v0.3.7": "v0.3.7"}, "tag already exists"),
        ({f"gh api repos/{REPO}/actions/workflows/release.yml --jq .state": "disabled"}, "enabled"),
        (
            {
                f"gh release list --repo {REPO} --limit 100 --json tagName,isDraft,isPrerelease": (
                    '[{"tagName":"v0.3.7","isDraft":true,"isPrerelease":false}]'
                ),
            },
            "already exists",
        ),
    ],
)
def test_release_preflight_failure_never_changes_files(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, str],
    message: str,
) -> None:
    before = (workspace / "pyproject.toml").read_bytes()
    run = commands(workspace, **override)
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match=message):
        release.start_release(
            workspace,
            "0.3.7",
            resume=False,
            check=MagicMock(),
            previous_build=lambda tag: 29,
        )
    assert (workspace / "pyproject.toml").read_bytes() == before
    assert not any(call.args[1:3] == ("git", "push") for call in run.call_args_list)


@pytest.mark.parametrize("version", ["v0.3.7", "0.3", "0.3.7-rc1", "00.3.7", "65536.0.0"])
def test_invalid_versions_fail_before_commands(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    run = MagicMock()
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError):
        release.start_release(
            workspace,
            version,
            resume=False,
            check=MagicMock(),
            previous_build=MagicMock(),
        )
    run.assert_not_called()


def test_check_failure_retains_version_changes_without_commit_or_push(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = commands(workspace)
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="synthetic check failure"):
        release.start_release(
            workspace,
            "0.3.7",
            resume=False,
            check=MagicMock(side_effect=RuntimeError("synthetic check failure")),
            previous_build=lambda tag: 29,
        )
    assert 'version = "0.3.7"' in (workspace / "pyproject.toml").read_text()
    assert not any(call.args[1:3] == ("git", "commit") for call in run.call_args_list)
    assert not any(call.args[1:3] == ("git", "push") for call in run.call_args_list)


def test_existing_draft_with_matching_assets_resumes_without_reupload(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = release.artifact_names("0.3.7")
    checksum = artifacts / "SHA256SUMS"
    checksum.write_text(
        "".join(
            f"{hashlib.sha256((artifacts / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        )
    )
    metadata = {
        "id": 123,
        "tag_name": "v0.3.7",
        "draft": True,
        "prerelease": False,
        "assets": [
            {"name": path.name, "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in artifacts.iterdir()
        ],
    }
    run = MagicMock(
        side_effect=[
            '[{"tagName":"v0.3.7","isDraft":true,"isPrerelease":false}]',
            '{"databaseId":123,"tagName":"v0.3.7"}',
            json.dumps(metadata),
            json.dumps(metadata),
            "",
        ]
    )
    monkeypatch.setattr(release, "repository", lambda root: REPO)
    monkeypatch.setattr(release, "run", run)
    release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "upload") for call in run.call_args_list)
    assert run.call_args.args[1:4] == ("gh", "release", "edit")


def test_workflow_failure_is_not_reported_as_release_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(
        side_effect=[
            json.dumps(
                [
                    {
                        "databaseId": 123,
                        "headSha": SHA,
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ]
            ),
            RuntimeError("CI failed"),
        ]
    )
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="CI failed"):
        release.wait_for_release(tmp_path, REPO, "v0.3.7", SHA, resume=False)
    assert not any(call.args[1:4] == ("gh", "release", "view") for call in run.call_args_list)


def test_resume_does_not_bump_commit_or_push(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = commands(
        workspace,
        **{
            "git rev-parse refs/tags/v0.3.6^{commit}": SHA,
            "git ls-remote origin refs/tags/v0.3.6^{}": f"{SHA}\trefs/tags/v0.3.6^{{}}",
        },
    )
    monkeypatch.setattr(release, "run", run)
    wait = MagicMock()
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        workspace,
        "0.3.6",
        resume=True,
        check=MagicMock(),
        previous_build=MagicMock(),
    )
    assert (workspace / "uv.lock").read_text() == "old lock\n"
    wait.assert_called_once_with(workspace, REPO, "v0.3.6", SHA, resume=True)
    assert not any(call.args[1:3] == ("git", "push") for call in run.call_args_list)


@pytest.mark.parametrize("resume", [False, True])
def test_wait_tracks_exact_run_and_verifies_complete_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume: bool,
) -> None:
    names = [*release.artifact_names("0.3.7"), "SHA256SUMS"]
    results = [
        json.dumps(
            [
                {
                    "databaseId": 123,
                    "headSha": SHA,
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        ),
        *([""] if resume else []),
        "",
        json.dumps(
            {
                "tagName": "v0.3.7",
                "isDraft": False,
                "isPrerelease": False,
                "assets": [{"name": name} for name in names],
                "url": "https://example.invalid/release",
            }
        ),
    ]
    run = MagicMock(side_effect=results)
    monkeypatch.setattr(release, "run", run)
    release.wait_for_release(tmp_path, REPO, "v0.3.7", SHA, resume=resume)
    calls = [call.args[1:] for call in run.call_args_list]
    assert (
        ("gh", "run", "rerun", "123", "--failed", "--repo", REPO) in calls
        if resume
        else (not any(call[:3] == ("gh", "run", "rerun") for call in calls))
    )
    assert (
        "gh",
        "run",
        "watch",
        "123",
        "--repo",
        REPO,
        "--exit-status",
        "--interval",
        "15",
    ) in calls


def test_watch_executes_with_streamed_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=None))
    monkeypatch.setattr(release.subprocess, "run", command)
    release.run(tmp_path, "gh", "run", "watch", "123")
    command.assert_called_once()
    assert command.call_args.kwargs["stdout"] is None


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    for name in release.artifact_names("0.3.7"):
        (directory / name).write_bytes(name.encode())
    return directory


def publication_commands(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    *,
    bad_digest: bool = False,
    published: bool = False,
    fail_upload: bool = False,
) -> MagicMock:
    assets: list[dict[str, str]] = []

    def run(root: Path, *args: str) -> str:
        if args[:3] == ("gh", "release", "list"):
            return json.dumps(
                [
                    {
                        "tagName": "v0.3.7",
                        "isDraft": not published,
                        "isPrerelease": False,
                    }
                ]
            )
        if args[:3] == ("gh", "release", "upload"):
            if fail_upload:
                raise RuntimeError("synthetic upload failure")
            path = Path(args[4])
            assets.append(
                {
                    "name": path.name,
                    "digest": "sha256:"
                    + ("0" * 64 if bad_digest else hashlib.sha256(path.read_bytes()).hexdigest()),
                }
            )
        if args[:3] == ("gh", "release", "view"):
            return json.dumps({"databaseId": 123, "tagName": "v0.3.7"})
        if args[:2] == ("gh", "api"):
            return json.dumps(
                {
                    "id": 123,
                    "tag_name": "v0.3.7",
                    "draft": True,
                    "prerelease": False,
                    "assets": assets,
                }
            )
        return ""

    result = MagicMock(side_effect=run)
    monkeypatch.setattr(release, "run", result)
    monkeypatch.setattr(release, "repository", lambda root: REPO)
    return result


def test_publish_uploads_exact_artifacts_and_checksums_before_publishing(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = publication_commands(monkeypatch, artifacts)
    release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    uploads = [call for call in run.call_args_list if call.args[1:4] == ("gh", "release", "upload")]
    assert len(uploads) == 4
    assert len((artifacts / "SHA256SUMS").read_text().splitlines()) == 3
    assert run.call_args.args[1:] == (
        "gh",
        "release",
        "edit",
        "v0.3.7",
        "--repo",
        REPO,
        "--draft=false",
        "--latest",
    )


@pytest.mark.parametrize("new_draft", [False, True])
def test_publish_reads_draft_by_id_when_tag_endpoint_is_not_available(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    new_draft: bool,
) -> None:
    command = publication_commands(monkeypatch, artifacts)
    original = command.side_effect
    assert callable(original)

    def run(root: Path, *args: str) -> str:
        if new_draft and args[:3] == ("gh", "release", "list"):
            return "[]"
        if args[:3] == ("gh", "release", "view"):
            return json.dumps({"databaseId": 123, "tagName": "v0.3.7"})
        if args[:2] == ("gh", "api"):
            assert args[2] == f"repos/{REPO}/releases/123", "Tag endpoint cannot read this draft"
        result = original(root, *args)
        assert isinstance(result, str)
        return result

    command.side_effect = run
    release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert command.call_args.args[1:4] == ("gh", "release", "edit")
    assert (
        any(call.args[1:4] == ("gh", "release", "create") for call in command.call_args_list)
        == new_draft
    )


@pytest.mark.parametrize(
    "identity",
    [
        {"databaseId": 0, "tagName": "v0.3.7"},
        {"databaseId": True, "tagName": "v0.3.7"},
        {"databaseId": "123", "tagName": "v0.3.7"},
        {"databaseId": 123, "tagName": "v0.3.8"},
    ],
)
def test_publish_rejects_invalid_resolved_identity(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: dict[str, object],
) -> None:
    command = publication_commands(monkeypatch, artifacts)
    original = command.side_effect
    assert callable(original)

    def run(root: Path, *args: str) -> str:
        if args[:3] == ("gh", "release", "view"):
            return json.dumps(identity)
        result = original(root, *args)
        assert isinstance(result, str)
        return result

    command.side_effect = run
    with pytest.raises(RuntimeError, match="invalid release identity"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:3] == ("gh", "api") for call in command.call_args_list)
    assert not any(call.args[1:4] == ("gh", "release", "upload") for call in command.call_args_list)
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in command.call_args_list)


@pytest.mark.parametrize(
    "changed",
    [
        {"id": 456},
        {"tag_name": "v0.3.8"},
        {"draft": False},
        {"prerelease": True},
    ],
)
@pytest.mark.parametrize("after_upload", [False, True])
def test_publish_requires_matching_stable_draft_before_and_after_upload(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: dict[str, object],
    after_upload: bool,
) -> None:
    command = publication_commands(monkeypatch, artifacts)
    original = command.side_effect
    assert callable(original)
    reads = 0

    def run(root: Path, *args: str) -> str:
        nonlocal reads
        result = original(root, *args)
        assert isinstance(result, str)
        if args[:2] == ("gh", "api"):
            reads += 1
            if reads == (2 if after_upload else 1):
                return json.dumps(json.loads(result) | changed)
        return result

    command.side_effect = run
    with pytest.raises(RuntimeError, match="matching stable draft"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in command.call_args_list)


@pytest.mark.parametrize("failure", ["missing", "empty", "extra", "digest", "published", "upload"])
def test_publish_failures_never_publish(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    run = publication_commands(
        monkeypatch,
        artifacts,
        bad_digest=failure == "digest",
        published=failure == "published",
        fail_upload=failure == "upload",
    )
    first = artifacts / release.artifact_names("0.3.7")[0]
    if failure == "missing":
        first.unlink()
    elif failure == "empty":
        first.write_bytes(b"")
    elif failure == "extra":
        (artifacts / "unexpected.txt").write_text("must not publish")
    with pytest.raises(RuntimeError):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in run.call_args_list)


def test_single_release_workflow_gates_publication_on_all_platforms() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "needs: [prepare, android, linux, windows]" in workflow
    assert "runs-on: windows-2022" in workflow
    assert "build-windows-installer" in workflow and "smoke-windows-installer" in workflow
    assert "build-android-release" in workflow and "validate-linux-appimage" in workflow
    assert "smoke-linux-appimage" in workflow and "uv run inv check" in workflow
    assert workflow.count("contents: write") == 1
    assert "group: all-platform-release" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "environment: android-production" in workflow
    assert "publish-release" in workflow
    assert not (ROOT / ".github/workflows/android-release.yml").exists()
    assert not (ROOT / ".github/workflows/linux-appimage.yml").exists()


def test_release_creates_real_commit_tag_and_atomic_push_only_in_disposable_repositories(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is required for the disposable release integration test.")
    remote = workspace / "remote.git"
    project = workspace / "project"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(workspace / name, project / name)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=project,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    git("init", "--bare", str(remote))
    git("init", "-b", "main")
    git("config", "user.name", "Release test")
    git("config", "user.email", "release-test@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")
    git("config", "core.hooksPath", str(workspace / "no-hooks"))
    git("add", ".")
    git("commit", "-m", "Synthetic baseline")
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")
    real_run = release.run

    def run(root: Path, *args: str) -> str:
        assert root == project
        if args[:4] == ("git", "remote", "get-url", "origin"):
            return f"https://github.com/{REPO}.git"
        if args[0] == "git":
            return real_run(root, *args)
        if args == ("uv", "lock", "--offline"):
            (project / "uv.lock").write_text("new lock\n")
            return ""
        if args[:3] == ("gh", "repo", "view"):
            return REPO
        if args[:3] == ("gh", "auth", "status"):
            return ""
        if args[:2] == ("gh", "api"):
            return "active"
        if args[:3] == ("gh", "release", "list"):
            return "[]"
        raise AssertionError(f"Unexpected integration-test command: {args}")

    monkeypatch.setattr(release, "run", run)
    wait = MagicMock()
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        project,
        "0.3.7",
        resume=False,
        check=lambda: None,
        previous_build=lambda tag: 29,
    )
    sha = git("rev-parse", "HEAD")
    assert git("status", "--porcelain") == ""
    assert git("rev-parse", "refs/tags/v0.3.7^{commit}") == sha
    assert git("ls-remote", "origin", "refs/heads/main").split()[0] == sha
    assert git("ls-remote", "origin", "refs/tags/v0.3.7^{}").split()[0] == sha
    assert release.TRAILER in git("log", "-1", "--format=%B")
    assert set(git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()) == {
        "pyproject.toml",
        "uv.lock",
    }
    wait.assert_called_once_with(project, REPO, "v0.3.7", sha, resume=False)
