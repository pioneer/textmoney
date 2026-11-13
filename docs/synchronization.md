# Sequential editing and external synchronization

TextMoney is a local-first UI frontend to an ordinary local Beancount file and
works fully offline. `textmoney.beancount` is the sole active financial journal.
TextMoney does not perform journal synchronization, access cloud-storage APIs,
merge financial changes, resolve journal conflicts or certify synchronization completion.

## Sync safely between devices

1. Choose a dedicated records folder with a **fully accessible local copy** on
   every device. Confirm it remains readable/writable offline, including backup
   and recovery operations.
2. Configure an external client to synchronize the **whole folder**, not just
   the journal. Include `backups/`, `textmoney-icons/`, `textmoney-settings/`,
   `textmoney-budget-order/` when present, and Android's hidden recovery files.
3. Finish saving and close the editing session on the first device. Wait for its
   client to finish uploading before switching.
4. Wait for the next device's client to finish downloading the folder, then open
   TextMoney and review the received journal before editing. Use only one writer.
5. If the client creates conflicts or TextMoney reports an interrupted/uncertain
   save, stop editing and pause synchronization. Compare copies externally and
   follow [backup and recovery steps](data-safety.md#recover-from-an-interrupted-or-uncertain-save).

Keep independent backups; cloud version history depends on provider retention.
TextMoney cannot tell you that a sync client has finished, and a possible-conflict
notice is not proof that every conflict has been found.

## Local folders and sync clients

Dropbox, Google Drive, Syncthing and other synchronization tools are supported
only when an appropriate local client provides a **fully accessible local copy**
on each device. The journal and its folder must support offline reads, writes,
backup creation and recovery-file operations. Cloud-only documents, placeholders
and on-demand access without a complete local copy are not sufficient.

Google Drive requires an appropriate local synchronization client. Its official
Android app alone does not provide the accessible local folder required by
TextMoney; marking a document available offline in that app is not a substitute.

On desktop, TextMoney uses filesystem paths. On Android, select the local folder
through SAF and grant read/write access. A `content://` URI is a provider
capability, not a filesystem path or proof that the files are local. A cloud
provider appearing in the picker does not establish offline folder access.
TextMoney does not download a private working copy.

The journal is `textmoney.beancount`. `backups/` holds journal snapshots;
icon assignments, published defaults and budget ordering use the optional
sidecar directories listed above.
The [optional icon sidecars](entity-icons.md#storage-and-synchronization) retain
their own immutable history and conflict notices, without changing financial
journal synchronization rules. This is a local-file workflow, not a native
integration with or certification of any synchronization service or client.

[Shared account and commission defaults](shared-settings.md) use journal-folder
sidecars with explicit publication and conflict review. They are separate from
financial journal synchronization; let the whole folder finish syncing.
[Budget ordering](shared-settings.md#budget-ordering) uses its own sidecar history.
Do not prune individual ancestor records from any immutable sidecar directory.

## Reviewing external changes

The app compares the main file with its SHA-256 baseline before saving and
again at the platform write boundary. Detected changes refuse the stale save.
Input remains visible; review the changes, reload, and explicitly reopen/review
the edit before submitting again. Reload does not silently rebase an old form.
Drafts are in memory and do not survive app termination.

Polling checks the journal approximately every five seconds and reloads valid
external changes without merging. Invalid syntax or
unsupported accounting semantics disable saving with an actionable error; the
original file is not rewritten. Read-only differences can contain private
financial data and are shown only in the UI, never diagnostic logs.

## Possible-conflict notices

Adapters list sibling names, not financial contents. Recognizable journal
duplicate/conflict names produce an advisory notice. Names cannot establish
that a file belongs to this journal, that its contents differ, or that no other
conflict exists. Arbitrarily renamed copies are not reliably detectable.
Compare versions externally before deciding which journal to use. The app
never merges, deletes, or automatically activates a sibling.

No auxiliary synchronization-evidence directory is created, read, or required.
Existing evidence is ignored and is not automatically deleted: it may contain
full private snapshots. Archive or remove it only after reviewing its contents.

## Local save protection retained

See [Data safety, backups and recovery](data-safety.md) for the maintained save
sequence, retention rules and recovery decisions.

- Previous contents are backed up and read-verified before modification.
- Desktop uses same-directory staging, fsync, and atomic replacement where the
  filesystem supports it; Android SAF direct writes are **not atomic**.
- Android interrupted-save intent/review remains in place, including restart
  protection. Its markers are not moved or simplified in this change.
- Read-back verifies the saved bytes. Uncertain outcomes require inspection,
  not an automatic retry that might duplicate a financial operation.
- Existing backup retention is unchanged.

These measures are not locking or compare-and-swap. An external write after
the final hash comparison can still race the commit. Changes made while the
app was closed have no app-maintained ancestry or automatic rollback detector.
Real-provider, multi-device and power-loss behavior requires physical testing.

## Journal conventions

See [Supported journal format and editing](journal-format.md) for declarations,
display names, wall-clock times, native reimbursement links, transaction identity,
visibility, fees and preserved user metadata. [Category budgets](budgets.md) covers
native Fava-compatible budget directives and history on the **Budgets** screen.
Neither sidecar synchronization nor a successful sync client extends the supported
financial format or permits silent rewriting of unsupported features.

## Validation of this simplification

On the Linux development host, 2026-10-25 (host-specific paths shown portably):

- `uv run inv check`: format, lint, Pyright, catalogs and **669 tests passed**.
- `uv run inv check-compat --strict --rustfava-appimage="$HOME/AppImages/rustfava.appimage"`:
  synthetic Beancount/Beanquery/Fava/RustLedger comparisons passed; rustfava
  runtime discovery passed, not its GUI.
- `uv run inv build-android` and `uv run inv check-beancount-apk`: passed.
  The APK was produced in `build/apk/`. The payload check runs on host CPython,
  not an Android device.
- `uv run inv build-linux-appimage`, `uv run inv validate-linux-appimage`, and
  `uv run inv smoke-linux-appimage`: passed; the AppImage remained running for
  the 20-second Xvfb startup test.
  The AppImage was produced in `dist/linux/`.

These are unpublished development builds under the unchanged project version,
not replacements for the published production-signed release. No Android
device was connected. Android provider behavior, real two-device synchronization,
Windows execution and interactive GUI journal workflows remain unverified for
this change. Follow the [manual regression checklist](testing.md).
