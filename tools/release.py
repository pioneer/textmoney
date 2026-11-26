"""One-command release coordination and fail-closed publication of CI artifacts."""

import hashlib
import json
import re
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

TRAILER = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
ANDROID_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64")
PLATFORMS = ("android", "linux", "windows")
PLATFORM_LABELS = {"android": "Android", "linux": "Linux", "windows": "Windows"}
STAGES = {"alpha": 0, "beta": 1, "rc": 2}
FINAL_STAGE = len(STAGES)
VERSION_PATTERN = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([A-Za-z]+)(0|[1-9]\d*))?"
)


class Version(NamedTuple):
    """Comparable version; a prerelease always sorts before its final release."""

    major: int
    minor: int
    patch: int
    stage: int
    number: int

    @property
    def prerelease(self) -> bool:
        return self.stage != FINAL_STAGE

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if not self.prerelease:
            return base
        label = next(name for name, stage in STAGES.items() if stage == self.stage)
        return f"{base}-{label}{self.number}"


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


def version_numbers(version: str) -> tuple[int, int, int]:
    """Return the stable X.Y.Z numbers, accepting a trailing alpha/beta/rc label."""
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise RuntimeError(
            "Use a version such as 0.3.7, or a prerelease such as 0.4.0-rc1 (without a v prefix)."
        )
    if match[4] is not None and match[4] not in STAGES:
        raise RuntimeError(
            "Prerelease labels must be lowercase alpha, beta or rc, such as 0.4.0-rc1."
        )
    numbers = (int(match[1]), int(match[2]), int(match[3]))
    if max(numbers) > 65535:
        raise RuntimeError(
            "Version components must fit Windows installer version fields (0..65535)."
        )
    return numbers


def parse_version(version: str) -> Version:
    """Parse a stable or prerelease version into a comparable identity."""
    version_numbers(version)
    match = VERSION_PATTERN.fullmatch(version)
    assert match is not None
    numbers = (int(match[1]), int(match[2]), int(match[3]))
    if match[4] is None:
        return Version(*numbers, FINAL_STAGE, 0)
    return Version(*numbers, STAGES[match[4]], int(match[5]))


def is_prerelease(version: str) -> bool:
    version_numbers(version)
    return "-" in version


def automatic_release_version(current_version: str, releases: list[ReleaseSummary]) -> str:
    """Pick the next stable version; a published prerelease is finalized instead."""
    current = parse_version(current_version)
    if current.prerelease:
        if not any(
            item["tagName"] == f"v{current_version}" and not item["isDraft"] for item in releases
        ):
            return current_version
        return str(Version(*current[:3], FINAL_STAGE, 0))
    published = [
        version_numbers(item["tagName"].removeprefix("v"))
        for item in releases
        if not item["isDraft"] and not item["isPrerelease"]
    ]
    if not published or current[:3] > max(published):
        return current_version
    return next_patch(max(published))


def next_patch(published: tuple[int, int, int]) -> str:
    major, minor, patch = published
    if patch == 65535:
        raise RuntimeError(
            "The patch version reached the Windows installer limit. "
            "Choose a new minor or major version explicitly with --version."
        )
    return f"{major}.{minor}.{patch + 1}"


def staged_release_version(root: Path, stage: str, *, base: str = "", resume: bool = False) -> str:
    """Return the next alpha/beta/rc version of the base release.

    The base defaults to the project version; an explicit ``base`` (or a
    prerelease project version) selects the upcoming final release.
    """
    if stage not in STAGES:
        raise RuntimeError("Release stages must be alpha, beta or rc.")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    current_version = project["project"]["version"]
    current = parse_version(current_version)
    if resume:
        if current.stage != STAGES[stage]:
            raise RuntimeError(f"Resume requires a {stage} version checked out on main.")
        return current_version
    target = parse_version(base or current_version)[:3]
    final = Version(*target, FINAL_STAGE, 0)
    published = [
        parse_version(item["tagName"].removeprefix("v"))
        for item in release_summaries(root, repository(root))
        if not item["isDraft"]
    ]
    if any(version >= final for version in published):
        raise RuntimeError(
            f"{final} is already published; a prerelease must precede its final release."
        )
    number = (
        max(
            (
                version.number
                for version in published
                if version[:3] == target and version.stage == STAGES[stage]
            ),
            default=0,
        )
        + 1
    )
    return str(Version(*target, STAGES[stage], number))


def android_apk_names(version: str) -> tuple[str, ...]:
    version_numbers(version)
    return tuple(f"textmoney-{version}-{abi}.apk" for abi in ANDROID_ABIS)


def platform_artifact_names(platform: str, version: str) -> tuple[str, ...]:
    """Return the published artifact names contributed by one platform."""
    version_numbers(version)
    if platform == "android":
        return android_apk_names(version)
    if platform == "linux":
        return (f"TextMoney-{version}-x86_64.AppImage",)
    if platform == "windows":
        return (f"TextMoney-{version}-windows-x64-setup.exe",)
    raise RuntimeError(f"Unknown release platform {platform!r}.")


def selected_platforms(platforms: str | tuple[str, ...] | None) -> tuple[str, ...]:
    """Resolve a requested platform selection to a validated, ordered tuple.

    An omitted or blank selection means every platform, matching the CLI and workflow
    defaults. A selection that names no platform at all is rejected rather than
    silently widened to a full release.
    """
    if platforms is None:
        return PLATFORMS
    if isinstance(platforms, str):
        if not platforms.strip():
            return PLATFORMS
        requested = [item.strip() for item in platforms.split(",")]
    else:
        requested = [item.strip() for item in platforms]
    selected = [item for item in requested if item]
    if not selected:
        raise RuntimeError(
            f"Choose at least one platform for a selective release: {', '.join(PLATFORMS)}, or all."
        )
    if "all" in selected:
        if len(selected) != 1:
            raise RuntimeError("Choose either all platforms or a subset, not both.")
        return PLATFORMS
    unknown = sorted({item for item in selected if item not in PLATFORMS})
    if unknown:
        raise RuntimeError(
            f"Unknown platform {', '.join(unknown)}. Choose from {', '.join(PLATFORMS)}."
        )
    return tuple(platform for platform in PLATFORMS if platform in selected)


def artifact_names(version: str, platforms: str | tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Return every published artifact for a version, limited to the selected platforms."""
    return tuple(
        name
        for platform in selected_platforms(platforms)
        for name in platform_artifact_names(platform, version)
    )


def describe_platforms(platforms: tuple[str, ...]) -> str:
    return ", ".join(PLATFORM_LABELS[platform] for platform in platforms)


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


def release_summaries(root: Path, repo: str) -> list[ReleaseSummary]:
    try:
        pages = json.loads(
            run(root, "gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub returned invalid release history JSON.") from exc
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise RuntimeError("GitHub returned an invalid release history.")
    releases: list[ReleaseSummary] = []
    for page in pages:
        for item in page:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("tag_name"), str)
                or type(item.get("draft")) is not bool
                or type(item.get("prerelease")) is not bool
            ):
                raise RuntimeError("GitHub returned invalid release metadata.")
            releases.append(
                {
                    "tagName": item["tag_name"],
                    "isDraft": item["draft"],
                    "isPrerelease": item["prerelease"],
                }
            )
    return releases


def published_release_version(root: Path, *, next_patch: bool = False) -> str:
    releases = release_summaries(root, repository(root))
    stable = [
        item["tagName"].removeprefix("v")
        for item in releases
        if not item["isDraft"] and not item["isPrerelease"]
    ]
    if not stable:
        raise RuntimeError("No published stable release was found.")
    current = max(stable, key=version_numbers)
    return automatic_release_version(current, releases) if next_patch else current


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


def candidate_title(tag: str, sha: str, platforms: tuple[str, ...] = PLATFORMS) -> str:
    """Name a candidate run, naming a selective platform set so runs stay distinct."""
    scope = "" if platforms == PLATFORMS else f" ({','.join(platforms)})"
    return f"Release {tag}{scope} at {sha}"


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


def wait_for_release(
    root: Path,
    repo: str,
    tag: str,
    sha: str,
    *,
    resume: bool,
    platforms: tuple[str, ...] = PLATFORMS,
) -> None:
    title = candidate_title(tag, sha, platforms)
    for _ in range(30):
        matches = [item for item in candidate_runs(root, repo) if item["displayTitle"] == title]
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
    verify_publication(root, repo, tag, sha, platforms)


def verify_publication(
    root: Path, repo: str, tag: str, sha: str, platforms: tuple[str, ...] = PLATFORMS
) -> None:
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
    expected = {*artifact_names(tag.removeprefix("v"), platforms), "SHA256SUMS"}
    if (
        result["tagName"] != tag
        or result["isDraft"]
        or result["isPrerelease"] != is_prerelease(tag.removeprefix("v"))
        or {item["name"] for item in result["assets"]} != expected
        or remote_tag_sha(root, tag) != sha
    ):
        raise RuntimeError(
            "CI finished without the expected complete published release of "
            f"{describe_platforms(platforms)}."
        )
    kind = " as a prerelease" if result["isPrerelease"] else ""
    print(
        f"Published {describe_platforms(platforms)}{kind}: {result['url']}",
        flush=True,
    )


def start_release(
    root: Path,
    version: str,
    *,
    resume: bool,
    check: Callable[[], None],
    previous_build: Callable[[str], int],
    capture_screenshots: Callable[[str], tuple[str, ...]] | None = None,
    platforms: str | tuple[str, ...] | None = None,
) -> None:
    selected = selected_platforms(platforms)
    if resume and capture_screenshots is not None:
        raise RuntimeError(
            "--with-screenshots cannot be combined with --resume; "
            "resume uses the existing candidate's screenshots."
        )
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
    latest = release_summaries(root, repo)
    if not version:
        version = (
            project["project"]["version"]
            if resume
            else automatic_release_version(project["project"]["version"], latest)
        )
    requested = parse_version(version)
    tag = f"v{version}"
    if resume:
        if parse_version(project["project"]["version"]) != requested:
            raise RuntimeError("Resume requires the matching release version checked out on main.")
        if any(item["tagName"] == tag and not item["isDraft"] for item in latest):
            verify_publication(root, repo, tag, sha, selected)
            return
        if not any(
            item["displayTitle"] == candidate_title(tag, sha, selected)
            for item in candidate_runs(root, repo)
        ):
            raise RuntimeError(
                "No candidate run matches the current commit and platform selection. After "
                "committing fixes, run release with the same version without --resume."
            )
        wait_for_release(root, repo, tag, sha, resume=True, platforms=selected)
        return
    current = parse_version(project["project"]["version"])
    if requested < current:
        raise RuntimeError("The release version cannot be older than the current project version.")
    for release in latest:
        existing_tag = release["tagName"]
        if existing_tag == tag:
            raise RuntimeError(
                "A release with this tag already exists; inspect it instead. "
                "Use --resume for the same candidate's interrupted draft. An unpublished "
                "draft from a different commit needs explicit removal before retrying."
            )
        if release["isDraft"]:
            continue
        published = parse_version(existing_tag.removeprefix("v"))
        if published == requested:
            raise RuntimeError(
                f"{existing_tag} already published version {requested}; publish a new version "
                "for a temporary release instead."
            )
        if requested < published:
            raise RuntimeError(
                "The version must be newer than every published release, including prereleases. "
                f"{existing_tag} is already published; a final release of the same "
                "version may follow its own prerelease."
            )
    if run(root, "git", "tag", "--list", tag) or remote_tag_sha(root, tag):
        raise RuntimeError(
            "The release tag already exists; use --resume for an interrupted publication. "
            "Existing final tags are never moved."
        )
    runs = candidate_runs(root, repo)
    if any(item["status"] != "completed" for item in runs):
        raise RuntimeError("A release candidate is still active; wait or resume it first.")
    if any(item["displayTitle"] == candidate_title(tag, sha, selected) for item in runs):
        raise RuntimeError("This candidate already ran; use --resume to retry its failed jobs.")
    previous = previous_build(tag)
    build = project["tool"]["flet"]["build_number"]
    next_build = max(build, previous) + 1 if requested > current else build
    if next_build <= previous:
        raise RuntimeError("The candidate build number must exceed the prior published release.")
    if next_build > 2100000000:
        raise RuntimeError("The Android build number would exceed its supported maximum.")
    print(
        f"Preparing {tag} for {describe_platforms(selected)}, Android build {next_build}."
        + (
            " Refreshing documentation screenshots."
            if capture_screenshots is not None
            else " No platform SDK is needed locally."
        ),
        flush=True,
    )
    if requested > current:
        source = replace_setting(source, "project", "version", json.dumps(version))
        source = replace_setting(source, "tool.flet", "build_number", str(next_build))
        project_path.write_text(source, encoding="utf-8")
        run(root, "uv", "lock")
    generated = set(capture_screenshots(version)) if capture_screenshots is not None else set()
    check()
    changed = set(run(root, "git", "diff", "--name-only").splitlines())
    required = {"pyproject.toml", "uv.lock"} if requested > current else set()
    if not required <= changed or not changed <= required | generated:
        raise RuntimeError(
            "Unexpected release changes. Inspect the worktree; nothing was committed."
        )
    if run(root, "git", "ls-files", "--others", "--exclude-standard"):
        raise RuntimeError("Checks created untracked files. Inspect them before releasing.")
    run(root, "git", "diff", "--cached", "--exit-code")
    run(root, "git", "diff", "--check")
    if changed:
        run(root, "git", "add", "--", *sorted(changed))
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
        "-f",
        f"platforms={','.join(selected)}",
    )
    wait_for_release(root, repo, tag, sha, resume=False, platforms=selected)


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


def publish_release(
    root: Path, directory: Path, tag: str, platforms: str | tuple[str, ...] | None = None
) -> None:
    version = tag.removeprefix("v")
    selected = selected_platforms(platforms)
    names = artifact_names(version, selected)
    if tag != f"v{version}":
        raise RuntimeError("Publication requires a vX.Y.Z or vX.Y.Z-label tag.")
    prerelease = is_prerelease(version)
    if {path.name for path in directory.iterdir()} - {"SHA256SUMS"} != set(names):
        raise RuntimeError(
            f"Release artifacts must contain exactly the {describe_platforms(selected)} artifacts."
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
    if existing is not None and (not existing["isDraft"] or existing["isPrerelease"] != prerelease):
        raise RuntimeError("Refusing to modify an already published release or its kind.")
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
            *(["--prerelease"] if prerelease else []),
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
            or metadata["prerelease"] is not prerelease
            or metadata["target_commitish"] != sha
        ):
            raise RuntimeError("Release must remain the matching draft of its kind until verified.")
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
    run(
        root,
        "gh",
        "release",
        "edit",
        tag,
        "--repo",
        repo,
        "--draft=false",
        "--prerelease" if prerelease else "--latest",
    )
    kind = "prerelease" if prerelease else "release"
    print(
        f"Published {tag} as a {kind} with verified {describe_platforms(selected)} assets.",
        flush=True,
    )
