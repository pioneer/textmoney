# Development

Install uv, then run the three commands at the start of the
[README](../README.md). `.python-version` selects Python 3.13 for development;
the application supports Python 3.13 and newer. CI checks 3.13 on every desktop
OS and 3.14 on Linux.

`uv run inv setup` uses `uv sync --locked`. Add runtime dependencies with
`uv add PACKAGE` and development tools with `uv add --dev PACKAGE`; retain both
pyproject.toml and uv.lock. Review upgrades deliberately, verify current Flet MCP
contracts, then run `uv run inv check` and a real build. Flet runtime, CLI extras,
and MCP are pinned together to stable 1.0.1 in this template.

Use `uv run inv run` for desktop or `uv run inv run-web` for browser development.
Both hot-reload the source tree. Stop with Ctrl+C. Web binds to localhost, not the
LAN. Debug variants and log locations are documented in [debugging](debugging.md).
On Linux, `run` and `debug` prepare a branded client under
`build/linux-dev-client` and register a hidden desktop entry under
`$XDG_DATA_HOME/applications` (default `~/.local/share/applications`).
Its GTK identity is `io.txncount.local`, separate from the packaged app and other
Flet apps. This gives the development window a generated transparent, tightly
framed version of the canonical app icon without
modifying Flet's shared client. The adapter checks the pinned client's binary
identity before patching the local copy and fails explicitly if it changes.
The runner supplies `FLET_APP_ID=io.txncount.local` so Wayland's application
identity matches that desktop entry. It starts Flet from `src` with an absolute
script path: otherwise Flet prefers an existing `build/linux` executable over
`FLET_VIEW_PATH`, bypassing the development client after a packaging run.
The desktop entry references a generated icon filename containing its content
hash, so GNOME/Wayland switchers do not reuse the previous artwork's cached
filename. Each launch refreshes this generated copy from the canonical asset.
Restart an already-running development window after changing the artwork.
Linux builds stage the same derivative as `src/assets/icon_linux.png`; it is
generated, not maintained, and other targets retain the original Android-safe
icon. See [branding](branding.md) for the silhouette and framing policy.
`.env.example` is documentation, not automatically loaded configuration. Set
variables in your shell/IDE; keep local `.env` files out of version control.

## Packaging

All builds go through Invoke so asset staging, icon validation, host checks, and
release debug-marker removal happen consistently. `build-local` maps Linux to
Linux, Windows to Windows, and Darwin to macOS; it does not emulate another OS.
Platform-specific tasks reject incompatible hosts before starting a build.
The [version metadata](#version-metadata) below describes the authoritative
version/build fields.
APK builds produce `build/apk/txncount-<project.version>.apk`; generated packaged
version metadata is staged alongside assets and removed by `clean`.
Linux AppImage builds reuse Flet's bundled Linux output and produce
`dist/linux/TxnCount-<project.version>-x86_64.AppImage`.
Build staging also clears source-tree `__pycache__` directories: Flet's relative
path exclusions do not recursively match nested caches, which could otherwise
ship obsolete renamed-module bytecode. Source files are preserved.

| Tasks | Host / tools | Output |
| --- | --- | --- |
| `build-web` | Linux, Windows, macOS; Flutter | `build/web` |
| `build-android`, `build-android-aab` | Linux, Windows, macOS; JDK 17, Android SDK | `build/apk`, `build/aab` |
| `build-ios` | macOS; Xcode 15+, CocoaPods 1.16+, provisioning/certificates | `build/ipa` |
| `build-ios-simulator` | macOS; Xcode and CocoaPods, no signing required | `build/ios-simulator` |
| `build-macos` | macOS; Xcode and CocoaPods | `build/macos` |
| `build-windows` | Windows; Visual Studio Desktop development with C++ | `build/windows` |
| `build-windows-installer` | Windows x64; Visual Studio C++ and Inno Setup 6 | `dist/windows/TxnCount-<version>-windows-x64-setup.exe` |
| `build-linux`, `build-linux-appimage` | Linux x86_64; Flet toolchain, GTK, media and AppImage build tools | `dist/linux/TxnCount-<version>-x86_64.AppImage` |

Flet installs its required Flutter version when unavailable, and JDK/Android SDK
components for Android targets. Build and mobile-debug tasks pass `--yes`, so
those installs (including SDK license acceptance) proceed without a prompt; allow
disk space and downloads. `uv run flet --version --json` lists the Debian/Ubuntu
packages Flet requires for Linux builds (`linux_dependencies`, including `lld`);
CI installs exactly that list. Other distributions need the equivalent packages.
Plugins may add native
requirements. A WSL Linux environment builds Linux, not Windows; GUI use needs
WSLg or an appropriate display. On Apple Silicon some tools require Rosetta.
Run `uv run inv doctor` to inspect the actual host.

### Linux AppImage

`uv run inv build-linux-appimage` builds Flet's Linux bundle and packages it as
a relocatable AppImage. `uv run inv build-linux` is an alias for the same
distribution build. The commands are Linux x86_64 only. Run
`uv run inv validate-linux-appimage` to extract the artifact and check its
architecture, launcher, desktop entry, icon, Python runtime and localization
resources. `uv run inv smoke-linux-appimage` launches it under Xvfb for a short
startup check. These checks do not exercise journal selection or real user data.

The packager obtains the official linuxdeploy and appimagetool AppImages and
the GTK bundling plugin into ignored `build/linux-appimage-tools/`. Each
download is pinned to a SHA-256 digest in `tools/linux_appimage.py` and checked
before execution. The verified appimagetool's embedded runtime is reused, so
AppImage generation does not need an unverified runtime download. The Flet
bundle and dependencies are staged under a temporary AppDir in `build/`; the
final AppImage alone is placed in `dist/linux/`. `uv run inv clean` removes
these outputs and cached tools.

The unified release workflow builds on Ubuntu 24.04, validates and smoke-tests
the AppImage, and uploads it as a workflow artifact. The final publication job
attaches all three platform artifacts to a draft and publishes only after every
required build succeeds. See [one-command releases](#one-command-releases).

The Ubuntu 24.04 build target requires the packages reported by
`uv run flet --version --json`, plus `desktop-file-utils`, `file`,
`libgirepository1.0-dev`, `librsvg2-dev`, `squashfs-tools`, and `xvfb`.
Manjaro developers need the corresponding Flet GTK/GStreamer build
dependencies, `desktop-file-utils`, `squashfs-tools`, `file`, `curl`,
`gobject-introspection`, and `librsvg`; install them with the distribution
package manager. No Arch package or AUR helper is used for TxnCount packaging.
Running the AppImage on Manjaro needs a compatible x86_64 userspace and may
need the `fuse2` compatibility package; `APPIMAGE_EXTRACT_AND_RUN=1` is a
fallback when FUSE mounting is unavailable. The host still supplies glibc,
display-server integration, and graphics drivers. Ubuntu 24.04+ and Manjaro
are intended targets, but the runtime compatibility matrix has not been
exhaustively tested.

Flet selects the highest bundled Python satisfying `requires-python` (currently
3.14.7 for this range), not necessarily the local 3.13 interpreter. Web uses the
corresponding Pyodide runtime. Check Flet's build output when upgrading; add an
explicit supported build Python version only if a dependency requires it. Native
extensions need target-specific Android/iOS wheels or Pyodide support. Desktop
installation success alone is not proof of mobile/browser compatibility.

uv.lock fixes the development environment. Flet's target packager separately
resolves `[project].dependencies`; it does not consume uv.lock as a target lock.
Runtime Flet is pinned, but transitive target dependencies may still change.
Retain build logs/artifacts and test packaged outputs before releases. Do not
describe builds as bit-for-bit reproducible based on uv.lock alone.

The app uses `finance-saf` as a local uv path dependency. Flet's
`tool.flet.dev_packages` maps it to `extensions/finance-saf`, relative to the
project root (not `tool.flet.app.path`). Flet packages its Python module and
`flutter/finance_saf` package into native builds. Changing the Dart bridge
requires rebuilding the APK; the stock Flet desktop/mobile client does not
contain it. The bridge is only instantiated on Android.

`tool.flet.flutter.pubspec.dependency_overrides.jni = "1.1.0"` overrides the
Flet shell's stale 1.0.0 pin: current Android SharedPreferences bindings require
JNI 1.1. The initial build reproduced that compile failure. Revisit the override
when upgrading Flet. SAF Dart dependencies are explicitly version-pinned in the
local extension's pubspec.

Stage 2 runtime dependencies are the pinned pure-Python Beancount parser
candidates and Lark. Official Beancount and pip are development-only (desktop
oracle and binary-availability probe). Android's
`tool.flet.android.extract_packages` extracts `autobean_refactor`,
`beancount_parser` and `lark`, which load bundled grammars through filesystem
paths. Do not remove this setting just because APK compilation succeeds.
`uv run inv check-beancount-apk` checks the actual archive layout and executes
the payload on Flet's downloaded host CPython 3.14.7; a different Flet/Python
release may require updating that probe's host executable path. A host payload
test is not phone testing. See [the Beancount report](beancount-compatibility.md).

Release app sources are precompiled (`compile.app=true`). Flet's packager removes
original `.py` files during compilation even when `cleanup.app=false`; traceback
frames still retain function/file/line information, but source text is absent.
Mobile debug disables app compilation for source-rich diagnostics. Local Flet
storage is excluded from packaging and hot reload. Generated output is ignored.
`uv run inv clean` removes builds and staging/caches, not the canonical icon,
virtual environment, or local Flet user data.

## Version metadata

`[project].version` in `pyproject.toml` is the maintained app version;
`[tool.flet].build_number` is the independent Android version code.
Invoke stages these values for the packaged About display and names APKs
`txncount-<version>.apk`. The Android app ID is `com.pioneer.txncount`.
Every main screen also shows `v<version> (<build_number>)` beside its title,
using the same source or packaged metadata as Settings → About. The header wraps
on narrow screens.

## Signing and CI

### One-command releases

After committing and pushing the release tooling to `main`, prepare and publish
Android, Linux and Windows together from a clean, synchronized `main` checkout:

```sh
uv run inv release --version X.Y.Z
```

Replace `X.Y.Z` with a new stable version, for example `0.3.7`. This is an
explicit publication command: it updates `[project].version` and the Android
build number in `pyproject.toml`, refreshes `uv.lock` offline, runs
`uv run inv check`, commits those two files, creates an annotated `vX.Y.Z` tag,
and atomically pushes `main` and that tag to `origin`. It waits for the exact
release workflow run and reports its result. The requested version must exceed
the current project version and listed published stable versions. The build
number increases beyond both the current and prior stable release's code.

The local prerequisites are Git, uv, the locked development dependencies
(`uv run inv setup`), and GitHub CLI authenticated with permission to push
`main`/release tags and read Actions (`gh auth login`). The local command does
not compile platform binaries: no Flutter, Visual Studio, Java or Android SDK
is needed locally. Branch protection must allow the release commit/tag push;
the command never bypasses protection or force-pushes. Commit and push any
release notes or application changes before running it.

The [All-platform release workflow](../.github/workflows/release.yml) runs:

1. Exact tag/version/commit and increasing Android build-number validation.
2. Format, lint, Pyright and tests on Linux Python 3.13/3.14 and Windows
   Python 3.13, plus the existing Beancount compatibility checks.
3. Parallel builds from that same commit:
   - Production-signed Android APK on Ubuntu, using only the protected
     `android-production` environment's existing signing secrets.
   - Linux x86_64 AppImage on Ubuntu 24.04, with structure and isolated Xvfb
     startup checks.
   - Windows x64 per-user EXE installer on `windows-2022`, with install,
     same-version reinstall, responsive-window startup and uninstall checks.
4. A publication job with release-write permission, only after all three
   platform jobs succeed. It creates a draft, uploads the three artifacts and
   `SHA256SUMS`, checks GitHub's uploaded SHA-256 digests, then publishes the
   complete release as latest. Existing assets are never replaced. A failed
   build cannot publish a partial release; an upload/verification failure
   leaves any created draft unpublished.

Expected downloads are `txncount-X.Y.Z.apk`,
`TxnCount-X.Y.Z-x86_64.AppImage`, and
`TxnCount-X.Y.Z-windows-x64-setup.exe`. macOS, iOS, web and Android AAB are not
part of this release command. Native build tasks remain available separately.
GitHub environment approval rules still apply, so the command may wait for
maintainer approval rather than bypassing it.
Release workflows share a concurrency group so different versions cannot race
to publish as latest. Do not queue several releases at once: GitHub may replace
an older pending run with a newer pending run.

After a pushed tag's CI/network failure, inspect Actions and reconnect with:

```sh
uv run inv release --version X.Y.Z --resume
```

Resume requires the matching version/tag commit at synchronized `main`. It does
not increment versions, create another commit/tag, or push. It watches the exact
existing run and reruns failed jobs if the run has completed unsuccessfully.
Successful jobs/artifacts are retained. A draft can resume uploading only when
its existing assets have identical digests. If code must change to fix a failed
build, use a new version/tag; never move an existing release tag.

Failures before the push leave local edits, commits or tags intact for explicit
inspection; there is no automatic reset, deletion or rollback. `--resume` is
only for tags already pushed successfully. If the push failed, resolve the
reported problem and inspect/push the prepared commit and tag explicitly before
resuming. Do not rerun without inspecting a possibly completed publication.

CI compilation/startup checks are not physical-device certification or a full
interactive GUI test. Test install/update, folder selection, journal CRUD,
conflicts and backups on disposable data on the actual target OS. The Windows
installer implementation has not yet been executed on a Windows runner; its
first native build and smoke test are required before claiming Windows support.

### Windows installer

For a local Windows build, install Visual Studio 2022 with **Desktop development
with C++**, its recommended Windows SDK/CMake components, and Inno Setup 6.
Enable Windows Developer Mode for Flutter plugin symlinks. VS Code is optional
and does not provide the native compiler. Flet downloads its required Flutter
SDK; Android Studio, Android SDK and Java are not required for this target.

```sh
uv run inv setup
uv run inv check
uv run inv build-windows-installer
```

The installer includes the full Flet bundle and app-local Visual C++ runtime
DLLs taken from the installed Visual Studio redistributable directory. It uses
the stable bundle ID as its installer identity and derives its version/publisher
from `pyproject.toml`. The installer icon is generated from `assets/icon.png`,
not a separately maintained icon source. It installs under
`%LOCALAPPDATA%\Programs\TxnCount`, without elevation, and registers a Start
Menu shortcut, an optional desktop shortcut and an uninstaller. The package
targets x64-compatible Windows 10 1809+ systems; Windows ARM emulation and older
Windows versions are not validated.

Uninstall removes installer-managed application files, not the selected
journal/backups or `%APPDATA%\txncount` preferences. Keep financial data outside
the application installation directory. The CI-only
`uv run inv smoke-windows-installer` task uses a disposable install directory and
synthetic journal/preferences; it checks a same-version reinstall, not an upgrade
from an older released installer. It is restricted to disposable GitHub runners
because Inno Setup registers the normal application identity.

The initial installer is **unsigned**. Android signing keys cannot sign Windows
executables. Windows may show unknown-publisher/SmartScreen warnings; trusted
Windows signing requires a separate certificate/signing service and CI setup.
Users do not need Python, uv, Flutter or Visual Studio to run the installed app.
For local cross-platform static analysis, use
`uv run inv typecheck --platform Windows`; it does not emulate a Windows build.

### One-time setup helper

Run `uv run inv setup-android-release` in an interactive terminal to check Java
(17+), keytool, Android Build Tools, GitHub authentication, and the existing
production keystore. It privately prompts for both passwords, checks the pinned
certificate and verifies private-key access without modifying the keystore.
No key is generated. Use `--keystore /path/to/existing.jks` for another location.
The default is `~/.local/share/pioneer-signing/android-release.jks`.

To upload signing secrets, first create the `android-production` GitHub environment
and configure its reviewers and release-tag restrictions in repository settings.
Then run:

```sh
uv run inv setup-android-release --upload-secrets
```

The helper checks that the environment exists and asks you to type `UPLOAD`
before replacing its three signing secrets. Passwords and the encoded keystore
are passed through stdin/environment, never command arguments or generated files.
Uploads are not atomic: if a later upload fails, earlier secrets may already
have changed; rerun the command to complete setup. The helper does not configure
environment protection, install tools, change system Java, build, or publish.

If tools are missing, install them yourself in a console. On Manjaro:

```sh
sudo pacman -S --needed jdk17-openjdk github-cli
archlinux-java status
sudo archlinux-java set java-17-openjdk
java -version
gh auth login
```

Install missing Android SDK Build Tools using the SDK manager and set
`ANDROID_HOME` to your SDK directory. Keep an encrypted offline backup of your
existing key and passwords before configuring release signing.

### Build and release

Android builds without a configured upload key use a debug key and cannot go to
Google Play. `uv run inv build-android` deliberately ignores configured
production-signing variables and continues to produce a development APK.
`uv run inv build-android-release --tag vX.Y.Z` is the separate, fail-closed
production task. It requires `FLET_ANDROID_SIGNING_KEY_STORE`,
`FLET_ANDROID_SIGNING_KEY_ALIAS`, `FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD`,
and `FLET_ANDROID_SIGNING_KEY_PASSWORD`; the alias must be `android-release`.
The task checks the tagged project version and requires the Android version code
to exceed the code in the latest prior stable GitHub Release. It then verifies
the APK signature, pinned certificate SHA-256, application ID, version name/code,
filename, and nonzero size. It never falls back to debug signing.

The approved production certificate fingerprint is pinned in
`[tool.txncount.android_release]` in pyproject.toml. Keep the existing JKS file
and an encrypted offline backup; never commit it, encode it into logs, or place
passwords in repository files. In Bash, configure local signing without putting
passwords in command history:

```sh
export FLET_ANDROID_SIGNING_KEY_STORE="$HOME/.local/share/pioneer-signing/android-release.jks"
export FLET_ANDROID_SIGNING_KEY_ALIAS=android-release
read -r -s -p "Keystore password: " FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD
printf '\n'
read -r -s -p "Key password: " FLET_ANDROID_SIGNING_KEY_PASSWORD
printf '\n'
export FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD FLET_ANDROID_SIGNING_KEY_PASSWORD
uv run inv build-android-release --tag vX.Y.Z
unset FLET_ANDROID_SIGNING_KEY_STORE FLET_ANDROID_SIGNING_KEY_ALIAS
unset FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD FLET_ANDROID_SIGNING_KEY_PASSWORD
```

The release task also needs `gh` authenticated to
`pioneer/txncount` and Android SDK Build Tools (`apksigner` and `aapt`).
Requires JDK 17.

The unified `release.yml` workflow runs only for pushed `v*` tags, checks the
project and release version before accessing signing secrets, and verifies the
APK before handing it to the final all-platform publication job. Configure the GitHub
`android-production` environment with required reviewers and restrict its
deployment branches/tags to release tags. Add environment secrets
`ANDROID_KEYSTORE_BASE64` (base64-encoded existing JKS),
`ANDROID_KEYSTORE_PASSWORD`, and `ANDROID_KEY_PASSWORD`. The workflow creates a
mode-0600 temporary keystore and removes it after the build. The pinned
certificate check remains the trust anchor even if the secret is replaced.

Releases retain the existing production certificate so installed production APKs
can update in place. A successful build does not substitute for
install/update testing on a physical Android device.

Physical-device release checklist:

1. Back up and verify the journal; use a disposable journal copy for testing.
2. Install the production APK, confirm the app ID/version and select the
  external test journal folder.
3. Create or edit only disposable test data, then close/reopen and confirm it
  persists in the selected external folder.
4. Install the next production APK over it (for example, `adb install -r
  build/apk/txncount-X.Y.Z.apk`) and confirm Android updates the existing app
  without uninstalling, the test data remains, and the app reports the new
  version.

`build-ios` may produce only an unsigned archive without configured
certificates/profiles; an installable/exportable IPA needs matching signing.
macOS local builds are ad-hoc signed; public distribution normally needs Developer
ID signing and notarization. Configure platform metadata in pyproject.toml, but
keep passwords, key files, and credentials in external stores or CI secrets.

Push/PR CI runs format-check, lint, Pyright, and pytest on Linux, Windows, and
macOS with Python 3.13, plus Linux with Python 3.14. Manual
workflow dispatch adds web, APK/AAB, Linux, Windows, macOS, and unsigned iOS
simulator builds on the proper runners. It does not publish or install signing
secrets. Only the Android job in the unified release workflow receives production signing
secrets. A green simulator job does not certify a
physical device or store release.

References: [Flet publishing](https://flet.dev/docs/publish/),
[Android](https://flet.dev/docs/publish/android),
[iOS](https://flet.dev/docs/publish/ios),
[Linux](https://flet.dev/docs/publish/linux),
[Windows](https://flet.dev/docs/publish/windows),
[macOS](https://flet.dev/docs/publish/macos).