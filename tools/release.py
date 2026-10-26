"""One-command release coordination and fail-closed publication of CI artifacts."""

import hashlib
import json
import re
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

TRAILER = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"


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


def artifact_names(version: str) -> tuple[str, ...]:
    version_numbers(version)
    return (
        f"txncount-{version}.apk",
        f"TxnCount-{version}-x86_64.AppImage",
        f"TxnCount-{version}-windows-x64-setup.exe",
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


def wait_for_release(root: Path, repo: str, tag: str, sha: str, *, resume: bool) -> None:
    for _ in range(30):
        runs = json.loads(
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
                "push",
                "--commit",
                sha,
                "--json",
                "databaseId,headSha,status,conclusion",
                "--limit",
                "10",
            )
        )
        matches = [item for item in runs if item["headSha"] == sha]
        if matches:
            if len(matches) != 1:
                raise RuntimeError(
                    "Multiple release runs match this commit; inspect GitHub Actions."
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
    requested = version_numbers(version)
    tag = f"v{version}"
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
    if resume:
        if project["project"]["version"] != version:
            raise RuntimeError("Resume requires the matching release version checked out on main.")
        if run(root, "git", "rev-parse", f"refs/tags/{tag}^{{commit}}") != sha:
            raise RuntimeError("The release tag must identify the current main commit.")
        remote = run(root, "git", "ls-remote", "origin", f"refs/tags/{tag}^{{}}")
        if remote.split()[:1] != [sha]:
            raise RuntimeError("The annotated release tag is not present on origin at this commit.")
        wait_for_release(root, repo, tag, sha, resume=True)
        return
    if requested <= version_numbers(project["project"]["version"]):
        raise RuntimeError("The release version must be newer than the current project version.")
    latest = json.loads(
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
    for release in latest:
        if release["tagName"] == tag:
            raise RuntimeError("A release with this tag already exists; inspect it instead.")
        if (
            not release["isDraft"]
            and not release["isPrerelease"]
            and requested <= version_numbers(release["tagName"].removeprefix("v"))
        ):
            raise RuntimeError("The version must be newer than all listed stable releases.")
    if run(root, "git", "tag", "--list", tag):
        raise RuntimeError(
            "The release tag already exists; use --resume for an interrupted release."
        )
    next_build = max(project["tool"]["flet"]["build_number"], previous_build(tag)) + 1
    if next_build > 2100000000:
        raise RuntimeError("The Android build number would exceed its supported maximum.")
    source = replace_setting(source, "project", "version", json.dumps(version))
    source = replace_setting(source, "tool.flet", "build_number", str(next_build))
    print(
        f"Preparing {tag}, Android build {next_build}. No platform SDK is needed locally.",
        flush=True,
    )
    project_path.write_text(source, encoding="utf-8")
    run(root, "uv", "lock", "--offline")
    check()
    changed = run(root, "git", "diff", "--name-only").splitlines()
    if set(changed) != {"pyproject.toml", "uv.lock"}:
        raise RuntimeError(
            "Unexpected release changes. Inspect the worktree; nothing was committed."
        )
    if run(root, "git", "ls-files", "--others", "--exclude-standard"):
        raise RuntimeError("Checks created untracked files. Inspect them before releasing.")
    run(root, "git", "diff", "--cached", "--exit-code")
    run(root, "git", "diff", "--check")
    run(root, "git", "add", "--", "pyproject.toml", "uv.lock")
    run(root, "git", "commit", "-m", f"Release v{version}", "-m", TRAILER)
    run(root, "git", "tag", "-a", tag, "-m", f"TxnCount {tag}")
    run(root, "git", "push", "--atomic", "origin", "HEAD:refs/heads/main", f"refs/tags/{tag}")
    sha = run(root, "git", "rev-parse", "HEAD")
    wait_for_release(root, repo, tag, sha, resume=False)


def publish_release(root: Path, directory: Path, tag: str) -> None:
    version = tag.removeprefix("v")
    names = artifact_names(version)
    if tag != f"v{version}":
        raise RuntimeError("Publication requires a stable vX.Y.Z release tag.")
    if {path.name for path in directory.iterdir()} - {"SHA256SUMS"} != set(names):
        raise RuntimeError(
            "Release artifacts must contain exactly the APK, AppImage and installer."
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
            "--verify-tag",
            "--title",
            f"TxnCount {tag}",
            "--generate-notes",
        )
    metadata = json.loads(run(root, "gh", "api", f"repos/{repo}/releases/tags/{tag}"))
    if not metadata["draft"] or metadata["prerelease"]:
        raise RuntimeError("Release must remain a stable draft until all artifacts are verified.")
    for asset in metadata["assets"]:
        name = asset["name"]
        if name not in digests or asset.get("digest") != f"sha256:{digests[name]}":
            raise RuntimeError(f"Existing draft asset differs: {name}. Nothing was overwritten.")
    present = {asset["name"] for asset in metadata["assets"]}
    for name in sorted(digests.keys() - present):
        run(root, "gh", "release", "upload", tag, str(directory / name), "--repo", repo)
    metadata = json.loads(run(root, "gh", "api", f"repos/{repo}/releases/tags/{tag}"))
    observed = {asset["name"]: asset.get("digest") for asset in metadata["assets"]}
    if observed != {name: f"sha256:{value}" for name, value in digests.items()}:
        raise RuntimeError("Uploaded artifact digests differ; release left unpublished.")
    run(root, "gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest")
    print(f"Published {tag} with verified Android, Linux and Windows assets.", flush=True)
