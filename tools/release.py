"""One-command release coordination and fail-closed publication of CI artifacts."""

import hashlib
import json
import re
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict

TRAILER = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
ANDROID_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64")


class ReleaseAsset(TypedDict):
    name: str
    digest: NotRequired[str | None]


class ReleaseMetadata(TypedDict):
    id: int
    tag_name: str
    draft: bool
    prerelease: bool
    target_commitish: str
    assets: list[ReleaseAsset]


class ReleaseSummary(TypedDict):
    tagName: str
    isDraft: bool
    isPrerelease: bool


class CandidateRun(TypedDict):
    databaseId: int
    displayTitle: str
    status: str
    conclusion: str


def run(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=root,
            check=True,
            text=True,
            stdout=None if args[:3] == ("gh", "run", "watch") else subprocess.PIPE,
        )
        return "" if result.stdout is None else result.stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"{args[0]} command failed. See tool diagnostics; no automatic rollback was attempted."
        ) from exc


def version_numbers(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise RuntimeError("Use a stable version such as 0.3.7 (without a v prefix).")
    numbers = tuple(int(value) for value in version.split("."))
    if max(numbers) > 65535:
        raise RuntimeError(
            "Version components must fit Windows installer version fields (0..65535)."
        )
    return numbers


def automatic_release_version(current_version: str, releases: list[ReleaseSummary]) -> str:
    current = version_numbers(current_version)
    published = [
        version_numbers(item["tagName"].removeprefix("v"))
        for item in releases
        if not item["isDraft"] and not item["isPrerelease"]
    ]
    if not published or current > max(published):
        return current_version
    major, minor, patch = max(published)
    if patch == 65535:
        raise RuntimeError(
            "The patch version reached the Windows installer limit. "
            "Choose a new minor or major version explicitly with --version."
        )
    return f"{major}.{minor}.{patch + 1}"


def android_apk_names(version: str) -> tuple[str, ...]:
    version_numbers(version)
    return tuple(f"textmoney-{version}-{abi}.apk" for abi in ANDROID_ABIS)


def artifact_names(version: str) -> tuple[str, ...]:
    return (
        *android_apk_names(version),
        f"TextMoney-{version}-x86_64.AppImage",
        f"TextMoney-{version}-windows-x64-setup.exe",
    )


def repository(root: Path) -> str:
    name = run(root, "gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise RuntimeError("Could not determine the GitHub repository.")
    origin = run(root, "git", "remote", "get-url", "origin")
    allowed = {
        f"https://github.com/{name}",
        f"https://github.com/{name}.git",
        f"git@github.com:{name}",
        f"git@github.com:{name}.git",
        f"ssh://git@github.com/{name}",
        f"ssh://git@github.com/{name}.git",
    }
    if origin not in allowed:
        raise RuntimeError("origin must point directly to the same GitHub repository used by gh.")
    return name


def replace_setting(source: str, section: str, key: str, value: str) -> str:
    pattern = rf"(?ms)(^\[{re.escape(section)}\]\s*\n)(.*?)(?=^\[|\Z)"
    match = re.search(pattern, source)
    if match is None:
        raise RuntimeError(f"Missing [{section}] in pyproject.toml.")
    body, count = re.subn(
        rf"(?m)^{re.escape(key)}\s*=\s*[^\r\n]+$",
        f"{key} = {value}",
        match[2],
    )
    if count != 1:
        raise RuntimeError(f"Expected exactly one {key} in [{section}].")
    return source[: match.start(2)] + body + source[match.end(2) :]


def candidate_title(tag: str, sha: str) -> str:
    return f"Release {tag} at {sha}"


def candidate_runs(root: Path, repo: str) -> list[CandidateRun]:
    return json.loads(
        run(
            root,
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            "release.yml",
            "--event",
            "workflow_dispatch",
            "--json",
            "databaseId,displayTitle,status,conclusion",
            "--limit",
            "100",
        )
    )


def remote_tag_sha(root: Path, tag: str) -> str | None:
    refs = dict(
        (ref, sha)
        for sha, ref in (
            line.split()
            for line in run(
                root, "git", "ls-remote", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"
            ).splitlines()
        )
    )
    return refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}"))


def validate_candidate(root: Path, sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("The candidate must identify a full commit SHA.")
    if run(root, "git", "rev-parse", "HEAD") != sha:
        raise RuntimeError("The checkout must match the exact candidate commit.")
    run(root, "git", "merge-base", "--is-ancestor", sha, "origin/main")


def wait_for_release(root: Path, repo: str, tag: str, sha: str, *, resume: bool) -> None:
    for _ in range(30):
        matches = [
            item
            for item in candidate_runs(root, repo)
            if item["displayTitle"] == candidate_title(tag, sha)
        ]
        if matches:
            if len(matches) != 1:
                raise RuntimeError(
                    "Multiple candidate runs match this version/commit; inspect GitHub Actions."
                )
            workflow = matches[0]
            break
        time.sleep(2)
    else:
        raise RuntimeError(
            f"No release workflow appeared for {tag}. Inspect Actions, then use --resume."
        )
    run_id = str(workflow["databaseId"])
    print(f"Release workflow: https://github.com/{repo}/actions/runs/{run_id}", flush=True)
    if resume and workflow["status"] == "completed" and workflow["conclusion"] != "success":
        run(root, "gh", "run", "rerun", run_id, "--failed", "--repo", repo)
    run(root, "gh", "run", "watch", run_id, "--repo", repo, "--exit-status", "--interval", "15")
    verify_publication(root, repo, tag, sha)


def verify_publication(root: Path, repo: str, tag: str, sha: str) -> None:
    result = json.loads(
        run(
            root,
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "tagName,isDraft,isPrerelease,assets,url",
        )
    )
    expected = {*artifact_names(tag.removeprefix("v")), "SHA256SUMS"}
    if (
        result["tagName"] != tag
        or result["isDraft"]
        or result["isPrerelease"]
        or {item["name"] for item in result["assets"]} != expected
        or remote_tag_sha(root, tag) != sha
    ):
        raise RuntimeError("CI finished without the expected complete published release.")
    print(f"Published all three platforms: {result['url']}", flush=True)


def start_release(
    root: Path,
    version: str,
    *,
    resume: bool,
    check: Callable[[], None],
    previous_build: Callable[[str], int],
) -> None:
    if version:
        version_numbers(version)
    if run(root, "git", "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(
            "Commit or set aside all work before releasing; the worktree must be clean."
        )
    if run(root, "git", "branch", "--show-current") != "main":
        raise RuntimeError("Start releases from the main branch.")
    run(root, "gh", "auth", "status")
    repo = repository(root)
    run(root, "git", "fetch", "origin", "main", "--tags")
    sha = run(root, "git", "rev-parse", "HEAD")
    if sha != run(root, "git", "rev-parse", "origin/main"):
        raise RuntimeError("main must match origin/main exactly. Synchronize it before releasing.")
    if (
        run(root, "gh", "api", f"repos/{repo}/actions/workflows/release.yml", "--jq", ".state")
        != "active"
    ):
        raise RuntimeError(
            "Commit and push the enabled unified release workflow before using release."
        )
    project_path = root / "pyproject.toml"
    source = project_path.read_text(encoding="utf-8")
    project = tomllib.loads(source)
    pages = json.loads(
        run(
            root,
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/releases?per_page=100",
        )
    )
    latest: list[ReleaseSummary] = [
        {
            "tagName": item["tag_name"],
            "isDraft": item["draft"],
            "isPrerelease": item["prerelease"],
        }
        for page in pages
        for item in page
    ]
    if not version:
        version = (
            project["project"]["version"]
            if resume
            else automatic_release_version(project["project"]["version"], latest)
        )
    requested = version_numbers(version)
    tag = f"v{version}"
    if resume:
        if project["project"]["version"] != version:
            raise RuntimeError("Resume requires the matching release version checked out on main.")
        if any(item["tagName"] == tag and not item["isDraft"] for item in latest):
            verify_publication(root, repo, tag, sha)
            return
        if not any(
            item["displayTitle"] == candidate_title(tag, sha) for item in candidate_runs(root, repo)
        ):
            raise RuntimeError(
                "No candidate run matches the current commit. After committing fixes, "
                "run release with the same version without --resume."
            )
        wait_for_release(root, repo, tag, sha, resume=True)
        return
    current = version_numbers(project["project"]["version"])
    if requested < current:
        raise RuntimeError("The release version cannot be older than the current project version.")
    for release in latest:
        if release["tagName"] == tag:
            raise RuntimeError(
                "A release with this tag already exists; inspect it instead. "
                "Use --resume for the same candidate's interrupted draft. An unpublished "
                "draft from a different commit needs explicit removal before retrying."
            )
        if (
            not release["isDraft"]
            and not release["isPrerelease"]
            and requested <= version_numbers(release["tagName"].removeprefix("v"))
        ):
            raise RuntimeError("The version must be newer than all listed stable releases.")
    if run(root, "git", "tag", "--list", tag) or remote_tag_sha(root, tag):
        raise RuntimeError(
            "The release tag already exists; use --resume for an interrupted publication. "
            "Existing final tags are never moved."
        )
    runs = candidate_runs(root, repo)
    if any(item["status"] != "completed" for item in runs):
        raise RuntimeError("A release candidate is still active; wait or resume it first.")
    if any(item["displayTitle"] == candidate_title(tag, sha) for item in runs):
        raise RuntimeError("This candidate already ran; use --resume to retry its failed jobs.")
    previous = previous_build(tag)
    build = project["tool"]["flet"]["build_number"]
    next_build = max(build, previous) + 1 if requested > current else build
    if next_build <= previous:
        raise RuntimeError("The candidate build number must exceed the prior published release.")
    if next_build > 2100000000:
        raise RuntimeError("The Android build number would exceed its supported maximum.")
    print(
        f"Preparing {tag}, Android build {next_build}. No platform SDK is needed locally.",
        flush=True,
    )
    if requested > current:
        source = replace_setting(source, "project", "version", json.dumps(version))
        source = replace_setting(source, "tool.flet", "build_number", str(next_build))
        project_path.write_text(source, encoding="utf-8")
        run(root, "uv", "lock", "--offline")
    check()
    changed = run(root, "git", "diff", "--name-only").splitlines()
    if set(changed) != ({"pyproject.toml", "uv.lock"} if requested > current else set()):
        raise RuntimeError(
            "Unexpected release changes. Inspect the worktree; nothing was committed."
        )
    if run(root, "git", "ls-files", "--others", "--exclude-standard"):
        raise RuntimeError("Checks created untracked files. Inspect them before releasing.")
    run(root, "git", "diff", "--cached", "--exit-code")
    run(root, "git", "diff", "--check")
    if requested > current:
        run(root, "git", "add", "--", "pyproject.toml", "uv.lock")
        run(root, "git", "commit", "-m", f"Release v{version}", "-m", TRAILER)
        run(root, "git", "push", "origin", "HEAD:refs/heads/main")
    sha = run(root, "git", "rev-parse", "HEAD")
    run(
        root,
        "gh",
        "workflow",
        "run",
        "release.yml",
        "--repo",
        repo,
        "--ref",
        "main",
        "-f",
        f"version={version}",
        "-f",
        f"sha={sha}",
    )
    wait_for_release(root, repo, tag, sha, resume=False)


def ensure_release_tag(root: Path, repo: str, tag: str, sha: str) -> None:
    existing = remote_tag_sha(root, tag)
    if existing is not None:
        if existing != sha:
            raise RuntimeError(
                "The final release tag identifies another commit; nothing was moved."
            )
        return
    tag_sha = run(
        root,
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{repo}/git/tags",
        "-f",
        f"tag={tag}",
        "-f",
        f"message=TextMoney {tag}",
        "-f",
        f"object={sha}",
        "-f",
        "type=commit",
        "--jq",
        ".sha",
    )
    if not re.fullmatch(r"[0-9a-f]{40}", tag_sha):
        raise RuntimeError("GitHub returned an invalid annotated tag identity.")
    run(
        root,
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{repo}/git/refs",
        "-f",
        f"ref=refs/tags/{tag}",
        "-f",
        f"sha={tag_sha}",
    )


def publish_release(root: Path, directory: Path, tag: str) -> None:
    version = tag.removeprefix("v")
    names = artifact_names(version)
    if tag != f"v{version}":
        raise RuntimeError("Publication requires a stable vX.Y.Z release tag.")
    if {path.name for path in directory.iterdir()} - {"SHA256SUMS"} != set(names):
        raise RuntimeError(
            "Release artifacts must contain exactly the three architecture APKs, "
            "AppImage and installer."
        )
    digests = {}
    for name in names:
        path = directory / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"Missing or unsafe release artifact: {name}.")
        with path.open("rb") as stream:
            digests[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = directory / "SHA256SUMS"
    if manifest.is_symlink():
        raise RuntimeError("Refusing to replace a checksum manifest symlink.")
    manifest.write_text(
        "".join(f"{digests[name]}  {name}\n" for name in names),
        encoding="utf-8",
    )
    digests[manifest.name] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    repo = repository(root)
    sha = run(root, "git", "rev-parse", "HEAD")
    validate_candidate(root, sha)
    existing_tag = remote_tag_sha(root, tag)
    if existing_tag is not None and existing_tag != sha:
        raise RuntimeError("The final release tag identifies another commit; nothing was moved.")
    releases = json.loads(
        run(
            root,
            "gh",
            "release",
            "list",
            "--repo",
            repo,
            "--limit",
            "100",
            "--json",
            "tagName,isDraft,isPrerelease",
        )
    )
    existing = next((item for item in releases if item["tagName"] == tag), None)
    if existing is not None and (not existing["isDraft"] or existing["isPrerelease"]):
        raise RuntimeError("Refusing to modify an already published or prerelease release.")
    if existing is None:
        run(
            root,
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--draft",
            "--target",
            sha,
            "--title",
            f"TextMoney {tag}",
            "--generate-notes",
        )
    identity = json.loads(
        run(
            root,
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "databaseId,tagName",
        )
    )
    release_id = identity["databaseId"]
    if type(release_id) is not int or release_id <= 0 or identity["tagName"] != tag:
        raise RuntimeError("GitHub returned an invalid release identity.")
    # The tag endpoint can return 404 for drafts; resolve once and keep using its ID.
    endpoint = f"repos/{repo}/releases/{release_id}"

    def read_draft() -> ReleaseMetadata:
        metadata: ReleaseMetadata = json.loads(run(root, "gh", "api", endpoint))
        if (
            metadata["id"] != release_id
            or metadata["tag_name"] != tag
            or metadata["draft"] is not True
            or metadata["prerelease"] is not False
            or metadata["target_commitish"] != sha
        ):
            raise RuntimeError(
                "Release must remain the matching stable draft until all artifacts are verified."
            )
        return metadata

    metadata = read_draft()
    for asset in metadata["assets"]:
        name = asset["name"]
        if name not in digests or asset.get("digest") != f"sha256:{digests[name]}":
            raise RuntimeError(f"Existing draft asset differs: {name}. Nothing was overwritten.")
    present = {asset["name"] for asset in metadata["assets"]}
    for name in sorted(digests.keys() - present):
        run(root, "gh", "release", "upload", tag, str(directory / name), "--repo", repo)
    metadata = read_draft()
    observed = {asset["name"]: asset.get("digest") for asset in metadata["assets"]}
    if observed != {name: f"sha256:{value}" for name, value in digests.items()}:
        raise RuntimeError("Uploaded artifact digests differ; release left unpublished.")
    ensure_release_tag(root, repo, tag, sha)
    run(root, "gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest")
    print(f"Published {tag} with verified Android, Linux and Windows assets.", flush=True)
