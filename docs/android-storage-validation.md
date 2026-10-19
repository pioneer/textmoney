# Android storage validation - Stage 1 history

This report preserves the original experiment and its device-test findings.
Old package IDs, synthetic filenames, build numbers and checksums below describe
those historical builds, not the current release. Module/task references and
links use current diagnostic names. See [current version metadata](development.md#version-metadata)
and [historical migration notes](branding.md#historical-android-identity-migration).

**Verdict: PASS (user-confirmed physical-device testing, 2026-10-18).**
At the start of Stage 2 the user confirmed that the Android Storage PoC
successfully passed on a physical phone, including build 2's conflict feedback
and preservation of unsaved input. Folder selection, direct read/write, same-folder
backups, access after app restart, external-change detection and conflicting
overwrite protection are user-confirmed. No device logs/model/Android version
were supplied, and this is not a claim of independent observation, reboot testing,
or compatibility with every document provider. The tested storage implementation
is preserved for Stage 2. At the start of Stage 3 the user reaffirmed both PoCs
passed. MVP 0.1 reuses this policy/bridge, extending only the filename allowlist
to a separate `txncount.beancount` journal and backup prefix. Current APK and
MVP-specific testing are documented in [MVP 0.1](mvp-0.1.md); build 2 details
below are historical.

## Phase B: first phone feedback and build 2

- User feedback: "1-5 are good"; Write on a conflicting file neither overwrites
  it nor visibly reports the conflict. No Android logs/model/version supplied.
  This establishes reported physical-device success for steps 1-5, not an
  independently observed test or a specific reboot/provider compatibility claim.
- Two UI defects were reproduced with failing tests: no modal conflict
  notification, and Write returning silently if polling held the storage lock.
  The old result label also appeared below the editor, potentially off-screen.
  Without device logs, which issue caused the particular tap is not proven.
- Build 2 moves operation feedback immediately below the buttons, shows a modal
  conflict warning with a **Keep editing** action, and queues a user operation
  behind polling instead of discarding it. A conflict does not misreport folder
  permission failure. Dismissing the warning does not reload or discard the draft.
- App name is now **txncount**, as requested. The package ID
  `com.pioneer.financepoc` and preference key remain unchanged to preserve
  installed app data and SAF grants during `adb install -r`.
- `uv run inv test` first reproduced both failures. After the fix, `uv sync`
  and `uv run inv format check` passed: **66 tests**, including the normal and
  polling-overlap conflict dialog/draft regression. Flet MCP verified
  `BasePage.show_dialog/pop_dialog`, `AlertDialog`, and `TextButton`.
- `uv run inv build-android` succeeded for build 2, followed by another
  successful `uv run inv check` (66 passed). `aapt dump badging` confirmed
  label `txncount`, version code 2 and unchanged package ID; `apksigner verify
  --print-certs` confirmed the same signing-certificate fingerprint as build 1.
  The build 2 APK checksum is below. Its phone retest subsequently passed
  (user-confirmed at the start of Stage 2).
- Phone retest: install build 2 **without uninstalling**, open **txncount**, Read,
  enter a draft, change the test file externally, then tap Write. Expect the
  modal warning, unchanged external file and retained draft. Dismiss with
  **Keep editing** and confirm the draft is still present.

## Implementation

- txncount (`com.pioneer.financepoc`), from the requested template archive,
  without Git history. Original minimal folder/document icon.
- One imperative Flet screen: Select Folder, Read, editable synthetic draft,
  Write, Backup, access/results, last-read SHA-256 and external-change status.
- Only `poc-test.beancount` and unique `backups/poc-test-<UUID>.beancount` files
  are written. No parser, finance application, sync integration, or real data.
- Shared [StorageSession](../src/app/storage.py) owns the hash baseline,
  draft, backup-before-modify policy and five-second polling decisions.
  [DesktopFolder](../src/app/infrastructure/desktop_storage.py) uses Python
  filesystem operations off the UI event loop; the
  [Android adapter](../src/app/infrastructure/android_storage.py) uses SAF only.
- Read is explicit, establishes a baseline (including a missing file), and
  replaces the draft. Write/Backup require that baseline. Conflicts leave both
  external content and unsaved draft intact. Restart remembers the folder,
  not the draft or hash; Read is required again.
- Backups are read-back verified before writing. A second hash check follows
  backup creation; writes are also read-back verified. Backup failure stops the
  write. Failed/uncertain writes require another Read and identify the backup.
  No automatic rollback is attempted, since it could overwrite an external edit.

## APIs actually used and why a bridge is needed

Scoped Storage does not make arbitrary shared directories ordinary writable
Python paths. A `content://` URI is a provider capability, **not** a filesystem
path. The standard Flet `FilePicker.get_directory_path()` exposes no persistent
URI-grant inspection or tree document read/write/backup API. It is used only on
desktop; Android does not use path guessing, broad storage permissions, or an
internal working copy.

The [local extension](../extensions/finance-saf) is a small Python/Dart Flet
Service, not a new Kotlin/Java plugin. It delegates native work to pinned existing
Flutter packages:

| Layer | Used APIs |
| --- | --- |
| Flet 1.0.1 | Imperative controls/events, `FilePicker.get_directory_path` (desktop), `SharedPreferences.get/set`, `Page.run_task`, mount/unmount lifecycle, `Service`/`_invoke_method`, `FletExtension.createService` |
| `saf_util` 3.1.1 | `pickDirectory(writePermission: true, persistablePermission: true)`, `hasPersistedPermission(checkRead: true, checkWrite: true)`, `child`, `mkdirp` |
| `saf_stream` 3.1.0 | `readFileBytes`, `writeFileBytes`, `writeFileUriBytes`, with append disabled |
| Android, through these libraries | `ACTION_OPEN_DOCUMENT_TREE`, read/write/persistable URI flags, `takePersistableUriPermission`, `persistedUriPermissions`, `DocumentFile` and `ContentResolver` input/output streams |

The remembered URI is held in Flet SharedPreferences, separately from the OS
grant. Each operation checks the persisted read+write grant; actual provider I/O
can still fail. Native provider errors are surfaced without raw URI/file-data
messages. Application diagnostics record operations, error categories and
conflicts, never file contents.

Flet MCP was queried through a real stdio client (`verify-flet`), not merely
consulted indirectly. Pinned Flet source, native SAF library source and Android
documentation were also inspected. Flet's protected invocation hook is the
official service-extension pattern but needs compatibility review on upgrades.

## Phase A commands and results

Run from the project root. Final verification used Python 3.13.15; the APK
packager selected CPython 3.14.7 and Flutter 3.44.8.

| Exact command | Result |
| --- | --- |
| `uv sync` | Successful, including local `finance-saf`; retained `uv.lock` |
| `uv run inv --help build-android` | Task exists, no task options |
| `uv run inv verify-flet` | MCP API and build-CLI queries succeeded |
| `uv run inv app-icon format check` | Generated icon; initial lint findings corrected |
| `uv run inv check` / `uv run inv format check` | Iterated through type/test fixes; final checks pass |
| `uv run inv icon-check check` | Icon valid; Ruff format/lint, Pyright, **65 tests passed**, including 19 storage tests |
| `uv run inv build-android` (attempt 1) | Failed: local dev-package path was relative to the wrong root; corrected |
| `uv run inv build-android` (attempt 2) | Failed: Flet shell pinned JNI 1.0.0 while current SharedPreferences bindings require 1.1 |
| `uv run inv build-android` (attempt 3) | **Succeeded** after persistent `tool.flet.flutter.pubspec.dependency_overrides.jni = "1.1.0"` |
| `/home/pioneer/Android/sdk/platform-tools/adb devices -l` | No attached Android devices |
| `/home/pioneer/flutter/3.44.8/bin/flutter devices --machine` | Linux desktop only; no functional Android emulator available |
| `/home/pioneer/Android/sdk/build-tools/35.0.0/aapt dump badging build/apk/*.apk` | Correct package/name/version; min API 24, target API 36 |
| `/home/pioneer/Android/sdk/build-tools/35.0.0/apksigner verify --print-certs build/apk/*.apk` | Signature valid; Android Debug certificate |
| `/home/pioneer/flutter/3.44.8/bin/dart analyze build/flutter/lib` | No errors; one warning and four informational findings in generated Flet shell imports (exit 2), not bridge failures |
| `sha256sum build/apk/*.apk` | Digest below |

Read-only source/docs/tool discovery commands were also used. No emulator was
installed, no financial files were accessed, and nothing was committed/pushed.

## APK and installation (historical build 2)

This section records the APK used for the successful Storage PoC phone test.
Later stages replace the APK at the same path; use the current
[Stage 2 report](beancount-compatibility.md) for the latest build/checksum and installation.

- Output: [build/apk/txncount.apk](../build/apk/txncount.apk), approximately
  134 MiB, debug-key signed, version 0.1.0 / build 2. This replaces the build 1
  output formerly named `finance-poc.apk`.
- Bundles ARM64, ARMv7 and x86_64; Android 7.0 / API 24 or newer.
- SHA-256: `e3a7377f311f6fc0aa81effff9469eec218323f169422d325f970a0d5b0d5778`.

Enable USB debugging on the phone, connect USB, and approve the computer:

```sh
adb devices -l
adb install -r /home/pioneer/workspace/txncount/build/apk/txncount.apk
```

On this host adb is installed at
`/home/pioneer/Android/sdk/platform-tools/adb` if it is not on PATH. Open
**txncount** from the launcher. Alternatively transfer the APK to the phone,
allow installation from the chosen file-manager/browser source, then open it.
Do not uninstall/clear data when testing restart persistence: that removes grants.

## Verified versus unverified

**Physical-device feedback (Phase B):** the first run passed steps 1-5 and
overwrite refusal but exposed missing conflict feedback. The user subsequently
confirmed the Storage PoC passed on a physical phone after build 2. No Android
device is connected to this development host.

**Verified:** Python quality gates; filesystem create/read/modify; unique backups;
hash conflicts for changed/empty/deleted/externally-created files; changes during
backup; permission/read/write/backup failures; read-back mismatch; UTF-8 failures;
unsaved editor preservation; mocked Android transport/permission error handling;
APK compilation, embedded extension registration, identity, ABIs and signature.

**Still unverified:** reboot grant retention specifically, detailed Android
polling/resume behavior, Dropsync interaction, Linux GUI folder selection
and Windows runtime. Android core-scenario results are user-reported successes,
not locally observed runs. No remaining compile blocker in the original build.

## Limitations

- SAF grants access only to the chosen tree. On Android 11+, storage roots,
  Download root and `Android/data` / `Android/obb` are restricted. Choose an
  existing local subfolder, e.g. `Documents/FinancePoCTest`, that Dropsync can
  independently access. This is not a request for `MANAGE_EXTERNAL_STORAGE`.
- No atomic write/compare-and-swap guarantee: another process can change a file
  between the final check and provider write. Partial/truncated writes remain
  possible on I/O failure; retain the verified backup for manual recovery.
- Provider rename, duplicate-name, truncation and permission behavior varies.
  Unexpected created names are reported rather than called successful. Test only
  disposable data. Backups are retained indefinitely for this small experiment.
- Polling reads/hashes the entire small test file every five seconds after Read;
  Android may suspend it in the background. Resume/foreground detection needs
  phone testing. Polling pauses on errors until an explicit retry.
- No file-size cap, streaming UI, merge, background service or crash recovery.
  Unsaved input survives conflicts/errors within the session, not process death.
- No real Dropbox/Dropsync sync was tested. File relocation, revoked grants,
  uninstall or clearing app data can invalidate remembered access.
- Flet target resolution is not fully locked by `uv.lock`; the JNI override is
  necessary for the tested build. Debug signing is for sideloaded testing only.

## Physical-device checklist (Phase B)

1. Create an **empty disposable** local folder, optionally the Dropsync-managed
   test folder. Install/open **txncount**; Select Folder and choose **Use this folder**
   / Allow. Confirm a content URI and persistent read+write grant status.
2. Read with no file present: expect "missing". Write the sample; verify in an
   independent editor/file manager that `poc-test.beancount` is in that exact
   folder, not app-private storage. Read it back.
3. Edit the draft and Write. Inspect `backups/`: the uniquely named file must
   contain the exact previous bytes. Use Backup separately and verify another
   unique file. Check unrelated files remain unchanged.
4. Force-stop and reopen the app, then Read **without selecting again**. Confirm
   the same folder/grant/content. Repeat after reboot if practical.
5. Enter an unsaved draft. Modify the test file in another Android editor (or
   allow a controlled Dropsync change), return and wait at least five seconds.
   Expect external-change detection.
6. Tap Write without Read: expect conflict refusal, external contents unchanged,
   and your unsaved draft still visible. Copy draft elsewhere before Read,
   because Read intentionally reloads the external version.
7. Delete/recreate the synthetic test file externally; check detection/refusal.
   Test picker cancellation and unavailable/revoked folder access; expect clear
   failure, no silent overwrite, and retained in-session draft.
8. Share phone model/Android version, chosen provider/folder type, outcomes, and
   logs if anything fails. Capture `adb logcat -v time -s flet.python flutter
   AndroidRuntime` while reproducing; review/redact device paths or private data
   before sharing. Do not attach real financial contents.

## Remaining effort

No remaining core-scenario blocker after user-confirmed testing. Broader
provider/reboot/Dropsync validation remains optional future work, not a condition
for this narrowly scoped PASS. This result does not certify production write
safety beyond the documented race and non-atomic-write limitations.

Sources: [Android shared documents and persistent grants](https://developer.android.com/training/data-storage/shared/documents-files),
[Scoped Storage](https://developer.android.com/about/versions/11/privacy/storage),
[Flet extensions](https://flet.dev/docs/extend/user-extensions/),
[Flet pinned picker source](https://github.com/flet-dev/flet/blob/v1.0.1/packages/flet/lib/src/services/file_picker.dart),
[saf_util 3.1.1](https://pub.dev/packages/saf_util/versions/3.1.1),
[saf_stream 3.1.0](https://pub.dev/packages/saf_stream/versions/3.1.0).
