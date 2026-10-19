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
Flet apps. This gives the development window the canonical app icon without
modifying Flet's shared client. The adapter checks the pinned client's binary
identity before patching the local copy and fails explicitly if it changes.
`.env.example` is documentation, not automatically loaded configuration. Set
variables in your shell/IDE; keep local `.env` files out of version control.

## Packaging

All builds go through Invoke so asset staging, icon validation, host checks, and
release debug-marker removal happen consistently. `build-local` maps Linux to
Linux, Windows to Windows, and Darwin to macOS; it does not emulate another OS.
Platform-specific tasks reject incompatible hosts before starting a build.
The [version metadata](#version-metadata) below describes the authoritative
version/build fields. Historical Android migration details are in
[branding](branding.md#historical-android-identity-migration).
APK builds produce `build/apk/txncount-<project.version>.apk`; generated packaged
version metadata is staged alongside assets and removed by `clean`.
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
| `build-linux` | Linux; Flet-reported toolchain, GTK, and media packages | `build/linux` |

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

## Signing and CI

Android builds without a configured upload key use a debug key and cannot go to
Google Play. `build-ios` may produce only an unsigned archive without configured
certificates/profiles; an installable/exportable IPA needs matching signing.
macOS local builds are ad-hoc signed; public distribution normally needs Developer
ID signing and notarization. Configure platform metadata in pyproject.toml, but
keep passwords, key files, and credentials in external stores or CI secrets.

Push/PR CI runs format-check, lint, Pyright, and pytest on Linux, Windows, and
macOS with Python 3.13, plus Linux with Python 3.14. Manual
workflow dispatch adds web, APK/AAB, Linux, Windows, macOS, and unsigned iOS
simulator builds on the proper runners. It does not publish or install signing
secrets. A green simulator job does not certify a physical device or store release.

References: [Flet publishing](https://flet.dev/docs/publish/),
[Android](https://flet.dev/docs/publish/android),
[iOS](https://flet.dev/docs/publish/ios),
[Linux](https://flet.dev/docs/publish/linux),
[Windows](https://flet.dev/docs/publish/windows),
[macOS](https://flet.dev/docs/publish/macos).