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
REPO = "pioneer/textmoney"
RELEASES_COMMAND = f"gh api --paginate --slurp repos/{REPO}/releases?per_page=100"
RUNS_COMMAND = (
    f"gh run list --repo {REPO} --workflow release.yml --event workflow_dispatch "
    "--json databaseId,displayTitle,status,conclusion --limit 100"
)


def test_release_requires_exactly_three_named_architecture_apks_and_both_desktops() -> None:
    assert release.artifact_names("0.3.13") == (
        "textmoney-0.3.13-arm64-v8a.apk",
        "textmoney-0.3.13-armeabi-v7a.apk",
        "textmoney-0.3.13-x86_64.apk",
        "TextMoney-0.3.13-x86_64.AppImage",
        "TextMoney-0.3.13-windows-x64-setup.exe",
    )


@pytest.mark.parametrize(
    "platforms,expected",
    [
        ("android", ("android",)),
        ("linux", ("linux",)),
        ("windows", ("windows",)),
        ("android,windows", ("android", "windows")),
        ("windows,android", ("android", "windows")),
        ("all", release.PLATFORMS),
        ("", release.PLATFORMS),
        (None, release.PLATFORMS),
        (("linux",), ("linux",)),
    ],
)
def test_platform_selection_is_validated_and_ordered(
    platforms: str | tuple[str, ...] | None, expected: tuple[str, ...]
) -> None:
    assert release.selected_platforms(platforms) == expected


@pytest.mark.parametrize(
    "platforms,message",
    [
        ("android,ios", "Unknown platform ios"),
        ("all,android", "either all platforms or a subset"),
        (",", "at least one platform"),
        (" , ", "at least one platform"),
    ],
)
def test_platform_selection_rejects_unknown_or_contradictory_choices(
    platforms: str, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        release.selected_platforms(platforms)


def test_repeated_platform_names_collapse_to_one_platform() -> None:
    assert release.selected_platforms("android,android") == ("android",)
    assert release.selected_platforms(("linux", "linux")) == ("linux",)


def test_selective_artifact_names_cover_only_the_chosen_platforms() -> None:
    assert release.artifact_names("0.5.0", "android") == (
        "textmoney-0.5.0-arm64-v8a.apk",
        "textmoney-0.5.0-armeabi-v7a.apk",
        "textmoney-0.5.0-x86_64.apk",
    )
    assert release.artifact_names("0.5.0", "linux") == ("TextMoney-0.5.0-x86_64.AppImage",)
    assert release.artifact_names("0.5.0", "windows") == ("TextMoney-0.5.0-windows-x64-setup.exe",)
    assert release.artifact_names("0.5.0", "linux,windows") == (
        "TextMoney-0.5.0-x86_64.AppImage",
        "TextMoney-0.5.0-windows-x64-setup.exe",
    )


def test_candidate_title_distinguishes_a_selective_release_from_a_full_one() -> None:
    assert release.candidate_title("v0.5.0", SHA) == f"Release v0.5.0 at {SHA}"
    assert release.candidate_title("v0.5.0", SHA, release.PLATFORMS) == (f"Release v0.5.0 at {SHA}")
    assert release.candidate_title("v0.5.0", SHA, ("android",)) == (
        f"Release v0.5.0 (android) at {SHA}"
    )
    assert release.candidate_title("v0.5.0", SHA, ("android", "windows")) == (
        f"Release v0.5.0 (android,windows) at {SHA}"
    )


@pytest.mark.parametrize(
    "current,stable,expected",
    [
        ("0.3.6", (), "0.3.6"),
        ("0.3.6", ("0.3.5",), "0.3.6"),
        ("0.3.6", ("0.3.6",), "0.3.7"),
        ("0.3.6", ("0.3.7",), "0.3.8"),
        ("0.3.9", ("0.3.9", "0.3.10", "0.3.8"), "0.3.11"),
        ("0.4.0", ("0.3.22",), "0.4.0"),
        ("1.0.0", ("0.3.22",), "1.0.0"),
        ("1.0.0", ("1.0.0",), "1.0.1"),
        ("0.3.6", tuple(f"0.3.{patch}" for patch in range(110)), "0.3.110"),
    ],
)
def test_automatic_version_increments_highest_stable_or_keeps_unpublished_project(
    current: str,
    stable: tuple[str, ...],
    expected: str,
) -> None:
    releases: list[release.ReleaseSummary] = [
        {"tagName": f"v{version}", "isDraft": False, "isPrerelease": False} for version in stable
    ]
    releases.extend(
        [
            {"tagName": "v9.0.0", "isDraft": True, "isPrerelease": False},
            {"tagName": "v10.0.0", "isDraft": False, "isPrerelease": True},
            {"tagName": "v11.0.0-rc1", "isDraft": False, "isPrerelease": True},
        ]
    )
    assert release.automatic_release_version(current, releases) == expected


def test_automatic_version_does_not_roll_over_into_another_minor_or_major() -> None:
    releases: list[release.ReleaseSummary] = [
        {"tagName": "v0.3.65535", "isDraft": False, "isPrerelease": False}
    ]
    with pytest.raises(RuntimeError, match="--version"):
        release.automatic_release_version("0.3.65535", releases)


@pytest.mark.parametrize(
    "current,published,expected",
    [
        ("0.5.0-rc1", (), "0.5.0-rc1"),
        ("0.5.0-rc3", ("0.4.22",), "0.5.0-rc3"),
        ("0.5.0-rc1", ("0.5.0-rc1", "0.4.22"), "0.5.0"),
        ("0.5.0-rc2", ("0.5.0-rc1", "0.5.0-rc2", "0.4.22"), "0.5.0"),
        ("0.4.23-rc1", ("0.4.23-rc1", "0.4.22"), "0.4.23"),
    ],
)
def test_automatic_version_reuses_an_unpublished_prerelease_and_finalizes_a_published_one(
    current: str, published: tuple[str, ...], expected: str
) -> None:
    releases: list[release.ReleaseSummary] = [
        {"tagName": f"v{version}", "isDraft": False, "isPrerelease": "-" in version}
        for version in published
    ]
    assert release.automatic_release_version(current, releases) == expected


@pytest.mark.parametrize(
    "version,stage,number,is_prerelease",
    [
        ("0.4.22", release.FINAL_STAGE, 0, False),
        ("0.5.0-alpha1", release.STAGES["alpha"], 1, True),
        ("0.5.0-beta12", release.STAGES["beta"], 12, True),
        ("0.5.0-rc3", release.STAGES["rc"], 3, True),
    ],
)
def test_prerelease_versions_parse_and_compare_before_the_final_release(
    version: str, stage: int, number: int, is_prerelease: bool
) -> None:
    parsed = release.parse_version(version)
    assert (parsed.major, parsed.minor, parsed.patch) == (0, 5, 0) or version == "0.4.22"
    assert (parsed.stage, parsed.number, parsed.prerelease) == (stage, number, is_prerelease)
    assert release.is_prerelease(version) is is_prerelease
    assert str(parsed) == version


def test_prerelease_order_is_alpha_then_beta_then_rc_then_release() -> None:
    ordered = [
        "0.5.0-alpha1",
        "0.5.0-alpha2",
        "0.5.0-beta1",
        "0.5.0-rc1",
        "0.5.0-rc2",
        "0.5.0",
        "0.5.1-rc1",
    ]
    parsed = [release.parse_version(version) for version in ordered]
    assert parsed == sorted(parsed)
    assert len(set(parsed)) == len(parsed)


@pytest.mark.parametrize(
    "published,stage,expected",
    [
        ((), "rc", "0.5.0-rc1"),
        (("0.4.22", "0.5.0-rc1"), "rc", "0.5.0-rc2"),
        (("0.4.22", "0.5.0-alpha1", "0.5.0-beta1"), "beta", "0.5.0-beta2"),
        (("0.4.22", "0.5.0-rc3"), "rc", "0.5.0-rc4"),
        (("0.4.22", "0.5.0-rc1"), "beta", "0.5.0-beta1"),
        (("0.4.22",), "alpha", "0.5.0-alpha1"),
    ],
)
def test_staged_release_version_counts_from_published_prereleases(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    published: tuple[str, ...],
    stage: str,
    expected: str,
) -> None:
    command = commands(
        workspace,
        **{
            RELEASES_COMMAND: json.dumps(
                [
                    [
                        {
                            "tag_name": f"v{version}",
                            "draft": False,
                            "prerelease": "-" in version,
                        }
                        for version in published
                    ]
                ]
            )
        },
    )
    monkeypatch.setattr(release, "run", command)
    assert release.staged_release_version(workspace, stage, base="0.5.0", resume=False) == expected


def test_staged_release_version_refuses_after_a_final_release(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = commands(
        workspace,
        **{
            RELEASES_COMMAND: json.dumps(
                [[{"tag_name": "v0.5.0", "draft": False, "prerelease": False}]]
            )
        },
    )
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(RuntimeError, match="already published"):
        release.staged_release_version(workspace, "rc", base="0.5.0", resume=False)


def test_staged_release_version_resume_requires_matching_stage(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "pyproject.toml").write_text(
        '[project]\nversion = "0.5.0-rc2"\n[tool.flet]\nbuild_number = 29\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "run", commands(workspace))
    assert release.staged_release_version(workspace, "rc", resume=True) == "0.5.0-rc2"
    with pytest.raises(RuntimeError, match="Resume requires a beta"):
        release.staged_release_version(workspace, "beta", resume=True)


@pytest.mark.parametrize(
    "versions,expected,next_expected",
    [
        (("0.4.8", "0.4.9"), "0.4.9", "0.4.10"),
        (("0.4.9", "0.4.10", "0.4.8"), "0.4.10", "0.4.11"),
    ],
)
@pytest.mark.parametrize("next_patch", [False, True])
def test_published_version_uses_all_pages_and_numeric_stable_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    versions: tuple[str, ...],
    expected: str,
    next_expected: str,
    next_patch: bool,
) -> None:
    pages = [
        [
            {"tag_name": "v99.0.0", "draft": True, "prerelease": False},
            {"tag_name": "v100.0.0-rc1", "draft": False, "prerelease": True},
        ],
        [{"tag_name": f"v{version}", "draft": False, "prerelease": False} for version in versions],
    ]
    run = MagicMock(return_value=json.dumps(pages))
    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "repository", lambda _: REPO)
    assert release.published_release_version(tmp_path, next_patch=next_patch) == (
        next_expected if next_patch else expected
    )
    run.assert_called_once_with(
        tmp_path, "gh", "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100"
    )


@pytest.mark.parametrize(
    "pages",
    [
        [[]],
        [
            [
                {"tag_name": "v1.0.0", "draft": True, "prerelease": False},
                {"tag_name": "v2.0.0", "draft": False, "prerelease": True},
            ]
        ],
    ],
)
def test_published_version_without_stable_releases_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, object]]]
) -> None:
    monkeypatch.setattr(release, "run", MagicMock(return_value=json.dumps(pages)))
    monkeypatch.setattr(release, "repository", lambda _: REPO)
    with pytest.raises(RuntimeError, match="No published stable release"):
        release.published_release_version(tmp_path)


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        "{}",
        "[{}]",
        "[[{}]]",
        '[[{"tag_name":"v0.4.9","draft":"false","prerelease":false}]]',
    ],
)
def test_release_history_rejects_invalid_json_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    monkeypatch.setattr(release, "run", MagicMock(return_value=response))
    with pytest.raises(RuntimeError, match="GitHub returned invalid|GitHub returned an invalid"):
        release.release_summaries(tmp_path, REPO)


def test_published_release_propagates_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("gh command failed")
    monkeypatch.setattr(release, "run", MagicMock(side_effect=failure))
    monkeypatch.setattr(release, "repository", lambda _: REPO)
    with pytest.raises(RuntimeError, match="gh command failed") as error:
        release.published_release_version(tmp_path)
    assert error.value is failure


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "textmoney"\nversion = "0.3.6"\n'
        "[tool.flet]\nbuild_number = 29\n"
        '[tool.textmoney]\nother = "preserve"\n',
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
        RELEASES_COMMAND: "[[]]",
        "git tag --list v0.3.7": "",
        "git diff --name-only": "pyproject.toml\nuv.lock",
        "git ls-files --others --exclude-standard": "",
        RUNS_COMMAND: "[]",
    } | overrides

    def run(directory: Path, *args: str) -> str:
        assert directory == root
        command = " ".join(args)
        if command == "uv lock":
            (root / "uv.lock").write_text("new lock\n", encoding="utf-8")
        return responses.get(command, "")

    return MagicMock(side_effect=run)


@pytest.mark.parametrize(
    "version,expected",
    [
        ("", "0.3.7"),
        ("0.3.7", "0.3.7"),
        ("0.3.10", "0.3.10"),
        ("0.4.0", "0.4.0"),
        ("1.0.0", "1.0.0"),
    ],
)
@pytest.mark.parametrize("with_screenshots", [False, True])
def test_release_prepares_version_files_and_dispatches_candidate_without_tag(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    expected: str,
    with_screenshots: bool,
) -> None:
    run = commands(
        workspace,
        **{
            RELEASES_COMMAND: (
                '[[{"tag_name":"v0.3.5","draft":false,"prerelease":false}],'
                '[{"tag_name":"v0.3.6","draft":false,"prerelease":false}]]'
            ),
        },
    )
    check = MagicMock()
    previous = MagicMock(return_value=31)
    wait = MagicMock()
    capture = MagicMock(return_value=("docs/screenshots/home-desktop.png",))
    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        workspace,
        version,
        resume=False,
        check=check,
        previous_build=previous,
        capture_screenshots=capture if with_screenshots else None,
    )
    project = tomllib.loads((workspace / "pyproject.toml").read_text())
    assert project["project"]["version"] == expected
    assert project["tool"]["flet"]["build_number"] == 32
    assert project["tool"]["textmoney"]["other"] == "preserve"
    check.assert_called_once()
    if with_screenshots:
        capture.assert_called_once_with(expected)
    else:
        capture.assert_not_called()
    previous.assert_called_once_with(f"v{expected}")
    calls = [call.args[1:] for call in run.call_args_list]
    assert ("uv", "lock") in calls
    assert ("git", "add", "--", "pyproject.toml", "uv.lock") in calls
    assert ("git", "commit", "-m", f"Release v{expected}", "-m", release.TRAILER) in calls
    assert (
        "git",
        "push",
        "origin",
        "HEAD:refs/heads/main",
    ) in calls
    assert not any(command[:2] == ("git", "tag") and "-a" in command for command in calls)
    assert (
        "gh",
        "workflow",
        "run",
        "release.yml",
        "--repo",
        REPO,
        "--ref",
        "main",
        "-f",
        f"version={expected}",
        "-f",
        f"sha={SHA}",
        "-f",
        "platforms=android,linux,windows",
    ) in calls
    assert not any(command[0] in {"flutter", "flet", "java"} for command in calls)
    wait.assert_called_once_with(
        workspace, REPO, f"v{expected}", SHA, resume=False, platforms=release.PLATFORMS
    )


def test_prerelease_prepares_its_version_and_build_number_and_dispatches_that_tag(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "textmoney"\nversion = "0.4.22"\n[tool.flet]\nbuild_number = 29\n',
        encoding="utf-8",
    )
    run = commands(
        workspace,
        **{
            RELEASES_COMMAND: (
                '[[{"tag_name":"v0.4.22","draft":false,"prerelease":false}],'
                '[{"tag_name":"v0.5.0-rc1","draft":false,"prerelease":true}]]'
            ),
            "git tag --list v0.5.0-rc2": "",
        },
    )
    monkeypatch.setattr(release, "run", run)
    wait = MagicMock()
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        workspace,
        "0.5.0-rc2",
        resume=False,
        check=MagicMock(),
        previous_build=lambda tag: 30,
    )
    project = tomllib.loads((workspace / "pyproject.toml").read_text())
    assert project["project"]["version"] == "0.5.0-rc2"
    assert project["tool"]["flet"]["build_number"] == 31
    calls = [call.args[1:] for call in run.call_args_list]
    assert ("git", "commit", "-m", "Release v0.5.0-rc2", "-m", release.TRAILER) in calls
    assert (
        "gh",
        "workflow",
        "run",
        "release.yml",
        "--repo",
        REPO,
        "--ref",
        "main",
        "-f",
        "version=0.5.0-rc2",
        "-f",
        f"sha={SHA}",
        "-f",
        "platforms=android,linux,windows",
    ) in calls
    wait.assert_called_once_with(
        workspace, REPO, "v0.5.0-rc2", SHA, resume=False, platforms=release.PLATFORMS
    )


def test_prerelease_requires_every_published_release_to_be_older(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (workspace / "pyproject.toml").read_bytes()
    run = commands(
        workspace,
        **{RELEASES_COMMAND: ('[[{"tag_name":"v0.5.0-rc2","draft":false,"prerelease":true}]]')},
    )
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="newer than every published release"):
        release.start_release(
            workspace,
            "0.5.0-rc1",
            resume=False,
            check=MagicMock(),
            previous_build=lambda tag: 30,
        )
    assert (workspace / "pyproject.toml").read_bytes() == before
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in run.call_args_list)


def test_release_stops_before_capture_and_publication_if_lock_fails(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_run = commands(workspace)
    failure = RuntimeError("uv command failed")

    def run(root: Path, *args: str) -> str:
        if args == ("uv", "lock"):
            raise failure
        return original_run(root, *args)

    monkeypatch.setattr(release, "run", run)
    capture = MagicMock()
    check = MagicMock()
    with pytest.raises(RuntimeError, match="uv command failed") as error:
        release.start_release(
            workspace,
            "0.3.7",
            resume=False,
            check=check,
            previous_build=lambda _: 29,
            capture_screenshots=capture,
        )
    assert error.value is failure
    assert (
        tomllib.loads((workspace / "pyproject.toml").read_text())["project"]["version"] == "0.3.7"
    )
    assert (workspace / "uv.lock").read_text() == "old lock\n"
    capture.assert_not_called()
    check.assert_not_called()
    assert not any(
        call.args[1:3] in {("git", "commit"), ("git", "push"), ("gh", "workflow")}
        for call in original_run.call_args_list
    )


@pytest.mark.parametrize("version", ["", "0.3.6"])
@pytest.mark.parametrize("with_screenshots", [False, True])
def test_corrected_candidate_keeps_version_and_build_without_another_commit(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    with_screenshots: bool,
) -> None:
    before = (workspace / "pyproject.toml").read_bytes()
    run = commands(workspace, **{"git diff --name-only": ""})
    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "wait_for_release", MagicMock())
    check = MagicMock()
    capture = MagicMock(return_value=("docs/screenshots/home-desktop.png",))
    release.start_release(
        workspace,
        version,
        resume=False,
        check=check,
        previous_build=lambda tag: 28,
        capture_screenshots=capture if with_screenshots else None,
    )
    assert (workspace / "pyproject.toml").read_bytes() == before
    assert (workspace / "uv.lock").read_text() == "old lock\n"
    check.assert_called_once()
    if with_screenshots:
        capture.assert_called_once_with("0.3.6")
    calls = [call.args[1:] for call in run.call_args_list]
    assert any(command[:3] == ("gh", "workflow", "run") for command in calls)
    assert not any(command[:2] in {("git", "commit"), ("git", "push")} for command in calls)


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
                RELEASES_COMMAND: ('[[{"tag_name":"v0.3.7","draft":true,"prerelease":false}]]'),
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


@pytest.mark.parametrize(
    "override,message",
    [
        (
            {RELEASES_COMMAND: ('[[{"tag_name":"v0.3.6","draft":true,"prerelease":false}]]')},
            "already exists",
        ),
        ({"git tag --list v0.3.6": "v0.3.6"}, "tag already exists"),
        (
            {
                "git ls-remote origin refs/tags/v0.3.6 refs/tags/v0.3.6^{}": (
                    f"{SHA}\trefs/tags/v0.3.6"
                )
            },
            "tag already exists",
        ),
    ],
)
def test_automatic_version_does_not_skip_draft_or_interrupted_publication(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, str],
    message: str,
) -> None:
    before = (workspace / "pyproject.toml").read_bytes()
    run = commands(workspace, **override)
    monkeypatch.setattr(release, "run", run)
    check = MagicMock()
    with pytest.raises(RuntimeError, match=message):
        release.start_release(workspace, "", resume=False, check=check, previous_build=MagicMock())
    check.assert_not_called()
    assert (workspace / "pyproject.toml").read_bytes() == before
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in run.call_args_list)


def test_automatic_version_history_failure_never_prepares_or_dispatches_release(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = (workspace / "pyproject.toml").read_bytes()
    command = commands(workspace)

    def run(root: Path, *args: str) -> str:
        if args[:4] == ("gh", "api", "--paginate", "--slurp"):
            raise RuntimeError("Release history unavailable.")
        return command(root, *args)

    monkeypatch.setattr(release, "run", run)
    check = MagicMock()
    with pytest.raises(RuntimeError, match="history unavailable"):
        release.start_release(workspace, "", resume=False, check=check, previous_build=MagicMock())
    check.assert_not_called()
    assert (workspace / "pyproject.toml").read_bytes() == before
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in command.call_args_list)


@pytest.mark.parametrize(
    "version",
    [
        "v0.3.7",
        "0.3",
        "0.3.7-RC1",
        "0.3.7-rc",
        "0.3.7-pre1",
        "0.3.7-rc01",
        "0.3.7-rc1.1",
        "00.3.7",
        "65536.0.0",
    ],
)
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


def test_screenshot_failure_leaves_prepared_metadata_without_checks_or_publication(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = commands(workspace)
    check = MagicMock()
    capture = MagicMock(side_effect=RuntimeError("synthetic capture failure"))
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="capture failure"):
        release.start_release(
            workspace,
            "0.3.7",
            resume=False,
            check=check,
            previous_build=lambda tag: 29,
            capture_screenshots=capture,
        )
    capture.assert_called_once_with("0.3.7")
    check.assert_not_called()
    assert 'version = "0.3.7"' in (workspace / "pyproject.toml").read_text()
    calls = [call.args[1:] for call in run.call_args_list]
    assert not any(
        command[:2] in {("git", "add"), ("git", "commit"), ("git", "push")}
        or command[:3] == ("gh", "workflow", "run")
        for command in calls
    )


def test_resume_cannot_regenerate_screenshots(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = MagicMock()
    capture = MagicMock()
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="--with-screenshots cannot be combined with --resume"):
        release.start_release(
            workspace,
            "",
            resume=True,
            check=MagicMock(),
            previous_build=MagicMock(),
            capture_screenshots=capture,
        )
    run.assert_not_called()
    capture.assert_not_called()


@pytest.mark.parametrize(
    "overrides,message",
    [
        (
            {"git diff --name-only": "pyproject.toml\nuv.lock\nREADME.md"},
            "Unexpected release changes",
        ),
        (
            {"git diff --name-only": "pyproject.toml\nuv.lock\ndocs/screenshots/unreferenced.png"},
            "Unexpected release changes",
        ),
        (
            {"git ls-files --others --exclude-standard": "untracked.png"},
            "untracked files",
        ),
    ],
)
def test_with_screenshots_does_not_allow_unrelated_or_untracked_release_changes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], message: str
) -> None:
    run = commands(workspace, **overrides)
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match=message):
        release.start_release(
            workspace,
            "0.3.7",
            resume=False,
            check=MagicMock(),
            previous_build=lambda tag: 29,
            capture_screenshots=lambda version: ("docs/screenshots/home-desktop.png",),
        )
    calls = [call.args[1:] for call in run.call_args_list]
    assert not any(
        command[:2] in {("git", "add"), ("git", "commit"), ("git", "push")}
        or command[:3] == ("gh", "workflow", "run")
        for command in calls
    )


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
        "target_commitish": SHA,
        "assets": [
            {"name": path.name, "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in artifacts.iterdir()
        ],
    }
    run = publication_commands(monkeypatch, artifacts)
    original = run.side_effect
    assert callable(original)

    def resumed(root: Path, *args: str) -> str:
        if args == ("gh", "api", f"repos/{REPO}/releases/123"):
            return json.dumps(metadata)
        result = original(root, *args)
        assert isinstance(result, str)
        return result

    run.side_effect = resumed
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
                        "displayTitle": release.candidate_title("v0.3.7", SHA),
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


@pytest.mark.parametrize("version", ["", "0.3.6"])
def test_resume_does_not_bump_commit_or_push(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    run = commands(
        workspace,
        **{
            RUNS_COMMAND: json.dumps(
                [
                    {
                        "databaseId": 123,
                        "displayTitle": release.candidate_title("v0.3.6", SHA),
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ]
            ),
        },
    )
    monkeypatch.setattr(release, "run", run)
    wait = MagicMock()
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        workspace,
        version,
        resume=True,
        check=MagicMock(),
        previous_build=MagicMock(),
    )
    assert (workspace / "uv.lock").read_text() == "old lock\n"
    wait.assert_called_once_with(
        workspace, REPO, "v0.3.6", SHA, resume=True, platforms=release.PLATFORMS
    )
    assert not any(call.args[1:3] == ("git", "push") for call in run.call_args_list)
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in run.call_args_list)


@pytest.mark.parametrize("version", ["", "0.3.6"])
@pytest.mark.parametrize("state", ["in_progress", "queued", "completed"])
def test_existing_candidate_requires_resume_without_dispatch_or_version_edits(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    version: str,
) -> None:
    before = (workspace / "pyproject.toml").read_bytes()
    command = commands(
        workspace,
        **{
            RUNS_COMMAND: json.dumps(
                [
                    {
                        "databaseId": 123,
                        "displayTitle": release.candidate_title("v0.3.6", SHA),
                        "status": state,
                        "conclusion": "failure" if state == "completed" else "",
                    }
                ]
            )
        },
    )
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(RuntimeError, match="resume"):
        release.start_release(
            workspace,
            version,
            resume=False,
            check=MagicMock(),
            previous_build=MagicMock(),
        )
    assert (workspace / "pyproject.toml").read_bytes() == before
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in command.call_args_list)


def test_published_version_cannot_be_rebuilt(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = commands(
        workspace,
        **{
            RELEASES_COMMAND: ('[[{"tag_name":"v0.3.6","draft":false,"prerelease":false}]]'),
        },
    )
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(RuntimeError, match="already exists"):
        release.start_release(
            workspace,
            "0.3.6",
            resume=False,
            check=MagicMock(),
            previous_build=MagicMock(),
        )
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in command.call_args_list)


def test_resume_after_code_changes_requests_new_candidate_not_new_version(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = commands(workspace)
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(RuntimeError, match="same version without --resume"):
        release.start_release(
            workspace,
            "0.3.6",
            resume=True,
            check=MagicMock(),
            previous_build=MagicMock(),
        )
    assert not any(call.args[1:4] == ("gh", "workflow", "run") for call in command.call_args_list)


@pytest.mark.parametrize("version", ["", "0.3.6"])
def test_resume_recognizes_already_published_result_without_rerunning_jobs(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    command = commands(
        workspace,
        **{
            RELEASES_COMMAND: ('[[{"tag_name":"v0.3.6","draft":false,"prerelease":false}]]'),
            (
                f"gh release view v0.3.6 --repo {REPO} "
                "--json tagName,isDraft,isPrerelease,assets,url"
            ): json.dumps(
                {
                    "tagName": "v0.3.6",
                    "isDraft": False,
                    "isPrerelease": False,
                    "assets": [
                        {"name": name} for name in (*release.artifact_names("0.3.6"), "SHA256SUMS")
                    ],
                    "url": "https://example.invalid/release",
                }
            ),
            "git ls-remote origin refs/tags/v0.3.6 refs/tags/v0.3.6^{}": (
                f"{SHA}\trefs/tags/v0.3.6^{{}}"
            ),
        },
    )
    monkeypatch.setattr(release, "run", command)
    release.start_release(
        workspace,
        version,
        resume=True,
        check=MagicMock(),
        previous_build=MagicMock(),
    )
    assert not any(call.args[1:4] == ("gh", "run", "rerun") for call in command.call_args_list)


@pytest.mark.parametrize("sha", ["main", "a" * 7, "g" * 40, "b" * 40])
def test_candidate_rejects_nonexact_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sha: str,
) -> None:
    command = MagicMock(return_value=SHA)
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(RuntimeError, match="candidate|SHA"):
        release.validate_candidate(tmp_path, sha)
    assert not any(call.args[1:3] == ("git", "merge-base") for call in command.call_args_list)


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
                    "displayTitle": release.candidate_title("v0.3.7", SHA),
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
        f"{SHA}\trefs/tags/v0.3.7^{{}}",
    ]
    run = MagicMock(side_effect=results)
    monkeypatch.setattr(release, "run", run)
    release.wait_for_release(tmp_path, REPO, "v0.3.7", SHA, resume=resume)
    calls = [call.args[1:] for call in run.call_args_list]
    listing = calls[0]
    assert "--commit" not in listing and "workflow_dispatch" in listing
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
        if args == ("git", "rev-parse", "HEAD"):
            return SHA
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
            if args[2:4] == ("--method", "POST"):
                return "b" * 40
            return json.dumps(
                {
                    "id": 123,
                    "tag_name": "v0.3.7",
                    "draft": True,
                    "prerelease": False,
                    "target_commitish": SHA,
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
    assert len(uploads) == 6
    assert len((artifacts / "SHA256SUMS").read_text().splitlines()) == 5
    tag_calls = [
        index
        for index, call in enumerate(run.call_args_list)
        if call.args[1:5] == ("gh", "api", "--method", "POST")
    ]
    assert len(tag_calls) == 2
    assert tag_calls[0] > max(
        index
        for index, call in enumerate(run.call_args_list)
        if call.args[1:4] == ("gh", "release", "upload")
    )
    assert f"object={SHA}" in run.call_args_list[tag_calls[0]].args
    assert "ref=refs/tags/v0.3.7" in run.call_args_list[tag_calls[1]].args
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


def test_publish_marks_a_prerelease_tag_as_prerelease_and_creates_it_as_such(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    for name in release.artifact_names("0.5.0-rc1"):
        (directory / name).write_bytes(name.encode())
    tags: list[str] = []
    created: list[list[str]] = []
    drafts: list[dict[str, object]] = []
    assets: list[dict[str, str]] = []

    def run(root: Path, *args: str) -> str:
        if args == ("git", "rev-parse", "HEAD"):
            return SHA
        if args[:3] == ("gh", "release", "list"):
            return "[]"
        if args[:3] == ("gh", "release", "create"):
            created.append(list(args))
            return ""
        if args[:3] == ("gh", "release", "upload"):
            path = Path(args[4])
            assets.append(
                {
                    "name": path.name,
                    "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
            return ""
        if args[:3] == ("gh", "release", "view"):
            return json.dumps({"databaseId": 123, "tagName": "v0.5.0-rc1"})
        if args[:3] == ("gh", "release", "edit"):
            tags.append(str(args[3]))
            return ""
        if args[:2] == ("gh", "api"):
            if args[2:4] == ("--method", "POST"):
                return "b" * 40
            drafts.append(
                {
                    "id": 123,
                    "tag_name": "v0.5.0-rc1",
                    "draft": True,
                    "prerelease": True,
                    "target_commitish": SHA,
                    "assets": assets,
                }
            )
            return json.dumps(drafts[-1])
        return ""

    monkeypatch.setattr(release, "run", MagicMock(side_effect=run))
    monkeypatch.setattr(release, "repository", lambda root: REPO)
    release.publish_release(tmp_path, directory, "v0.5.0-rc1")
    assert created and "--prerelease" in created[0] and "--draft" in created[0]
    assert tags == ["v0.5.0-rc1"]
    edit = [
        call.args[1:]
        for call in release.run.call_args_list  # type: ignore[attr-defined]
        if call.args[1:4] == ("gh", "release", "edit")
    ]
    assert edit == [
        ("gh", "release", "edit", "v0.5.0-rc1", "--repo", REPO, "--draft=false", "--prerelease")
    ]
    assert all(draft["prerelease"] is True for draft in drafts)


def test_publish_rejects_a_draft_created_with_the_wrong_release_kind(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = publication_commands(monkeypatch, artifacts)
    original = command.side_effect
    assert callable(original)

    def run(root: Path, *args: str) -> str:
        result = original(root, *args)
        assert isinstance(result, str)
        if args[:2] == ("gh", "api") and args[2:4] != ("--method", "POST"):
            return json.dumps(json.loads(result) | {"prerelease": True})
        return result

    command.side_effect = run
    with pytest.raises(RuntimeError, match="matching draft of its kind"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in command.call_args_list)


def test_publish_refuses_a_published_prerelease_as_a_stable_tag(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = publication_commands(monkeypatch, artifacts, published=True)
    original = command.side_effect
    assert callable(original)

    def run(root: Path, *args: str) -> str:
        result = original(root, *args)
        assert isinstance(result, str)
        if args[:3] == ("gh", "release", "list"):
            return json.dumps([{"tagName": "v0.3.7", "isDraft": False, "isPrerelease": True}])
        return result

    command.side_effect = run
    with pytest.raises(RuntimeError, match="already published"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")


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
        if args[:2] == ("gh", "api") and args[2:4] != ("--method", "POST"):
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
        {"target_commitish": "b" * 40},
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
        if args[:2] == ("gh", "api") and args[2:4] != ("--method", "POST"):
            reads += 1
            if reads == (2 if after_upload else 1):
                return json.dumps(json.loads(result) | changed)
        return result

    command.side_effect = run
    with pytest.raises(RuntimeError, match="matching draft of its kind"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in command.call_args_list)


@pytest.mark.parametrize(
    "failure", ["missing", "empty", "extra", "universal", "digest", "published", "upload"]
)
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
    elif failure == "universal":
        (artifacts / "textmoney-0.3.7.apk").write_bytes(b"old universal APK")
    with pytest.raises(RuntimeError):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in run.call_args_list)
    assert not any(
        call.args[1:5] == ("gh", "api", "--method", "POST") for call in run.call_args_list
    )


@pytest.mark.parametrize("existing", [SHA, "b" * 40])
def test_final_tag_is_never_moved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: str,
) -> None:
    command = MagicMock(return_value=f"{existing}\trefs/tags/v0.3.7^{{}}")
    monkeypatch.setattr(release, "run", command)
    if existing == SHA:
        release.ensure_release_tag(tmp_path, REPO, "v0.3.7", SHA)
    else:
        with pytest.raises(RuntimeError, match="another commit"):
            release.ensure_release_tag(tmp_path, REPO, "v0.3.7", SHA)
    command.assert_called_once()


def test_final_tag_failure_leaves_verified_draft_unpublished(
    artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = publication_commands(monkeypatch, artifacts)
    original = command.side_effect
    assert callable(original)

    def run(root: Path, *args: str) -> str:
        if args[:4] == ("gh", "api", "--method", "POST"):
            raise RuntimeError("synthetic tag failure")
        result = original(root, *args)
        assert isinstance(result, str)
        return result

    command.side_effect = run
    with pytest.raises(RuntimeError, match="tag failure"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    assert not any(call.args[1:4] == ("gh", "release", "edit") for call in command.call_args_list)


@pytest.mark.parametrize("abi", ["arm64-v8a", "armeabi-v7a", "x86_64"])
def test_missing_any_architecture_prevents_all_publication(
    artifacts: Path, monkeypatch: pytest.MonkeyPatch, abi: str
) -> None:
    (artifacts / f"textmoney-0.3.7-{abi}.apk").unlink()
    run = publication_commands(monkeypatch, artifacts)
    with pytest.raises(RuntimeError, match="must contain exactly the"):
        release.publish_release(artifacts.parent, artifacts, "v0.3.7")
    run.assert_not_called()


def test_single_release_workflow_gates_publication_on_all_platforms() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "workflow_dispatch:" in workflow and 'tags: ["v*"]' not in workflow
    assert "run-name: Release v${{ inputs.version }}" in workflow
    assert "ref: ${{ inputs.sha }}" in workflow
    assert "validate-release" in workflow
    assert '--platforms "$PLATFORMS"' in workflow
    assert 'publish-release --tag "$RELEASE_TAG" --sha "$CANDIDATE_SHA"' in workflow
    assert "needs: [prepare, check, compatibility, android, linux, windows]" in workflow
    assert "runs-on: windows-2022" in workflow
    assert "build-windows-installer" in workflow and "smoke-windows-installer" in workflow
    assert "build-android-release" in workflow and "validate-linux-appimage" in workflow
    for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
        assert (
            f"dist/android/textmoney-${{{{ needs.prepare.outputs.version }}}}-{abi}.apk" in workflow
        )
    assert "path: build/apk/textmoney-" not in workflow
    assert "smoke-linux-appimage" in workflow and "uv run inv check" in workflow
    assert workflow.count("contents: write") == 1
    assert "group: all-platform-release" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "environment: android-production" in workflow
    assert "publish-release" in workflow
    assert not (ROOT / ".github/workflows/android-release.yml").exists()
    assert not (ROOT / ".github/workflows/linux-appimage.yml").exists()


@pytest.mark.parametrize("with_screenshots", [False, True])
def test_release_pushes_candidate_commit_without_tag_in_disposable_repositories(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_screenshots: bool,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is required for the disposable release integration test.")
    remote = workspace / "remote.git"
    project = workspace / "project"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(workspace / name, project / name)
    home = "docs/screenshots/home-desktop.png"
    expense = "docs/screenshots/expense-mobile.png"
    if with_screenshots:
        (project / home).parent.mkdir(parents=True)
        (project / home).write_text("Synthetic home baseline\n")
        (project / expense).write_text("Synthetic unchanged expense\n")

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
        if args == ("uv", "lock"):
            (project / "uv.lock").write_text("new lock\n")
            return ""
        if args[:3] == ("gh", "repo", "view"):
            return REPO
        if args[:3] == ("gh", "auth", "status"):
            return ""
        if args[:3] == ("gh", "api", f"repos/{REPO}/actions/workflows/release.yml"):
            return "active"
        if args[:4] == ("gh", "api", "--paginate", "--slurp"):
            return '[[{"tag_name":"v0.3.6","draft":false,"prerelease":false}]]'
        if args[:3] == ("gh", "run", "list"):
            return "[]"
        if args[:3] == ("gh", "workflow", "run"):
            return ""
        raise AssertionError(f"Unexpected integration-test command: {args}")

    monkeypatch.setattr(release, "run", run)
    wait = MagicMock()
    monkeypatch.setattr(release, "wait_for_release", wait)
    captures: list[str] = []

    def capture(selected_version: str) -> tuple[str, ...]:
        metadata = tomllib.loads((project / "pyproject.toml").read_text())
        assert metadata["project"]["version"] == selected_version
        assert metadata["tool"]["flet"]["build_number"] == 30
        assert (project / "uv.lock").read_text() == "new lock\n"
        captures.append(selected_version)
        (project / home).write_text(f"Synthetic {selected_version} capture {len(captures)}\n")
        return (home, expense)

    def check() -> None:
        if with_screenshots:
            assert captures
            assert (project / home).read_text().endswith(f"capture {len(captures)}\n")

    release.start_release(
        project,
        "",
        resume=False,
        check=check,
        previous_build=lambda tag: 29,
        capture_screenshots=capture if with_screenshots else None,
    )
    sha = git("rev-parse", "HEAD")
    assert git("status", "--porcelain") == ""
    assert git("tag", "--list") == ""
    assert git("ls-remote", "origin", "refs/heads/main").split()[0] == sha
    assert git("ls-remote", "origin", "refs/tags/v0.3.7") == ""
    assert release.TRAILER in git("log", "-1", "--format=%B")
    expected = {"pyproject.toml", "uv.lock"} | ({home} if with_screenshots else set())
    assert (
        set(git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines())
        == expected
    )
    wait.assert_called_once_with(
        project, REPO, "v0.3.7", sha, resume=False, platforms=release.PLATFORMS
    )
    identity = tomllib.loads((project / "pyproject.toml").read_text())
    (project / "fix.txt").write_text("Synthetic CI fix\n")
    git("add", "fix.txt")
    git("commit", "-m", "Correct synthetic CI failure")
    git("push", "origin", "main")
    wait.reset_mock()
    release.start_release(
        project,
        "",
        resume=False,
        check=check,
        previous_build=lambda tag: 29,
        capture_screenshots=capture if with_screenshots else None,
    )
    fixed_sha = git("rev-parse", "HEAD")
    assert fixed_sha != sha
    assert git("log", "-1", "--format=%s") == (
        "Release v0.3.7" if with_screenshots else "Correct synthetic CI failure"
    )
    assert tomllib.loads((project / "pyproject.toml").read_text()) == identity
    assert git("ls-remote", "origin", "refs/tags/v0.3.7") == ""
    assert git("ls-remote", "origin", "refs/heads/main").split()[0] == fixed_sha
    if with_screenshots:
        assert captures == ["0.3.7", "0.3.7"]
        assert git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == home
    wait.assert_called_once_with(
        project, REPO, "v0.3.7", fixed_sha, resume=False, platforms=release.PLATFORMS
    )


def test_selective_release_dispatches_only_the_chosen_platforms(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = commands(
        workspace,
        **{
            RELEASES_COMMAND: (
                '[[{"tag_name":"v0.3.5","draft":false,"prerelease":false}],'
                '[{"tag_name":"v0.3.6","draft":false,"prerelease":false}]]'
            ),
        },
    )
    monkeypatch.setattr(release, "run", run)
    wait = MagicMock()
    monkeypatch.setattr(release, "wait_for_release", wait)
    release.start_release(
        workspace,
        "",
        resume=False,
        check=MagicMock(),
        previous_build=lambda tag: 31,
        platforms="android",
    )
    calls = [call.args[1:] for call in run.call_args_list]
    assert (
        "gh",
        "workflow",
        "run",
        "release.yml",
        "--repo",
        REPO,
        "--ref",
        "main",
        "-f",
        "version=0.3.7",
        "-f",
        f"sha={SHA}",
        "-f",
        "platforms=android",
    ) in calls
    wait.assert_called_once_with(
        workspace, REPO, "v0.3.7", SHA, resume=False, platforms=("android",)
    )


def test_selective_release_resumes_only_its_own_candidate_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "textmoney"\nversion = "0.3.7"\n[tool.flet]\nbuild_number = 30\n',
        encoding="utf-8",
    )
    full_title = release.candidate_title("v0.3.7", SHA)
    run = commands(
        workspace,
        **{
            RELEASES_COMMAND: '[[{"tag_name":"v0.3.6","draft":false,"prerelease":false}]]',
            RUNS_COMMAND: json.dumps(
                [
                    {
                        "databaseId": 1,
                        "displayTitle": full_title,
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ]
            ),
        },
    )
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="platform selection"):
        release.start_release(
            workspace,
            "",
            resume=True,
            check=MagicMock(),
            previous_build=lambda tag: 29,
            platforms="android",
        )


def test_selective_publication_requires_exactly_the_selected_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    for name in release.artifact_names("0.3.7", "android"):
        (directory / name).write_bytes(name.encode())
    publication_commands(monkeypatch, directory)
    release.publish_release(directory.parent, directory, "v0.3.7", "android")
    assert (directory / "SHA256SUMS").read_text().split()[-1] == ("textmoney-0.3.7-x86_64.apk")


def test_selective_publication_rejects_artifacts_from_another_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    for name in release.artifact_names("0.3.7", "android"):
        (directory / name).write_bytes(name.encode())
    (directory / "TextMoney-0.3.7-x86_64.AppImage").write_bytes(b"unexpected")
    publication_commands(monkeypatch, directory)
    with pytest.raises(RuntimeError, match="must contain exactly the Android artifacts"):
        release.publish_release(directory.parent, directory, "v0.3.7", "android")


def test_selective_publication_verification_accepts_the_selected_platform_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [*release.artifact_names("0.3.7", "linux"), "SHA256SUMS"]

    def run(root: Path, *args: str) -> str:
        if args[:3] == ("gh", "release", "view"):
            return json.dumps(
                {
                    "tagName": "v0.3.7",
                    "isDraft": False,
                    "isPrerelease": False,
                    "assets": [{"name": name} for name in names],
                    "url": "https://example.invalid/release",
                }
            )
        return f"{SHA}\trefs/tags/v0.3.7"

    monkeypatch.setattr(release, "run", run)
    release.verify_publication(Path("/tmp"), REPO, "v0.3.7", SHA, ("linux",))
    with pytest.raises(RuntimeError, match="complete published release of Android, Linux"):
        release.verify_publication(Path("/tmp"), REPO, "v0.3.7", SHA, release.PLATFORMS)
