# TxnCount to TextMoney

This is an intentional clean break, not an installed-app upgrade. No live
journal or private settings are discovered or migrated automatically.
The checkout's directory name is irrelevant; renaming the local VS Code folder
is left to the maintainer.

## Identity

| Surface | Old | New |
| --- | --- | --- |
| Product | TxnCount | TextMoney |
| Project / repository | `txncount` / `pioneer/txncount` | `textmoney` / `pioneer/textmoney` |
| Application / installer ID | `com.pioneer.txncount` | `com.pioneer.textmoney` |
| Main journal | `txncount.beancount` | `textmoney.beancount` |
| Visibility metadata on `open` | `txncount-active` | `textmoney-active` |
| Backup prefix | `txncount-` | `textmoney-` |
| Android recovery record | `.txncount.beancount.recovery.json` | `.textmoney.beancount.recovery.json` |
| Desktop config directory / preference keys | `txncount` / `txncount.*` | `textmoney` / `textmoney.*` |
| Gettext domain | `txncount` | `textmoney` |
| Linux source-client identity | `io.txncount.local` | `dev.textmoney.app` |

New artifacts are `textmoney-<version>-<abi>.apk`,
`TextMoney-<version>-x86_64.AppImage` and
`TextMoney-<version>-windows-x64-setup.exe`. Windows installs
`textmoney.exe` in `%LOCALAPPDATA%\Programs\TextMoney`.
The original artwork, generic Python `app` package and `finance-saf` extension
name remain unchanged.

Historical Git tags remain intact. The old TxnCount GitHub releases and their
download assets are retired after the first TextMoney release succeeds.
Through v0.3.15/build 38, binaries used the old identity; TextMoney starts with
v0.3.16/build 39.
Version/build numbering continues; the production Android signing key is
retained. Release validation permits only the explicitly approved transition
from v0.3.15/build 38 with the old ID to the new ID and identical signing
configuration. Other application-ID changes remain blocked. This exception is
not a claim that Android can upgrade across different IDs.

## Copy the journal

1. Close both apps and stop editing on every device. Let synchronization finish,
   then pause it for the transfer. Resolve any interrupted-save review in the old
   app first.
2. Keep an independent backup of the entire old folder, including hidden files
   and `backups/`. Do not modify that archive.
3. Use an existing local source folder and a **nonexistent**, separate destination.
   The destination's parent must already exist. Use direct filesystem paths, not
   symlinks, directory junctions or Android `content://` URIs.

```sh
uv run inv migrate-txncount \
  --source "/absolute/path/to/old-folder" \
  --destination "/absolute/path/to/new-folder"
```

The command reads only the explicitly selected legacy journal and recovery
inventory. It changes only `txncount-active` metadata keys on `open` directives,
then verifies that accounting data, visibility and budgets are unchanged.
Comments, whitespace, LF/CRLF, descriptions, unrelated metadata and existing
native reimbursement links (including `txncount-...` links) are preserved.
The source is never written. An invalid/unsupported journal, ambiguous keys,
prepared/damaged save record, or unacknowledged pending backups blocks transfer.
The journal and recovery inventory are rechecked before success.

The new folder contains only the converted journal. Old backups, pending files,
legacy `.txncount-sync/` evidence and recovery records remain in the original
folder, not renamed into new recovery history. New backups use the new namespace.
On Android, the existing explicit one-time recovery review for a journal without
a recovery record still applies; inspect the validated journal before accepting.

Failures after output creation leave `.textmoney-migration-incomplete`; both
desktop and Android reject that folder. Keep the original and investigate the
failure, then retry into another new destination. Do not remove the marker merely
to bypass review. The command does not lock other apps or the sync tool and
does not promise a distributed or crash-atomic migration.

## Desktop settings

Settings are separate from the selected journal folder. New locations:

- Linux: `${XDG_CONFIG_HOME:-~/.config}/textmoney/preferences.json`
- Windows: `%APPDATA%\textmoney\preferences.json`
- macOS (unvalidated app target): `~/Library/Application Support/textmoney/preferences.json`,
  or `$XDG_CONFIG_HOME/textmoney/preferences.json` when explicitly configured.

Optionally copy explicitly selected old desktop settings into a **nonexistent**
new settings file, after the journal migration succeeds:

```sh
uv run inv migrate-txncount-preferences \
  --source "/absolute/path/to/txncount/preferences.json" \
  --destination "/absolute/path/to/textmoney/preferences.json" \
  --journal-folder "/absolute/path/to/new-folder"
```

This maps language, theme, timezone, automatic backup cleanup and default fee
category. It replaces the main folder preference with the new folder's absolute
path. It does not copy diagnostic-folder access or unknown preference keys;
the original settings remain unchanged. Output is read back, but this is a
local copy command, not a settings sync mechanism.

## Install and reconnect devices

- **Android:** install the new ID separately, copy/synchronize the converted
  folder, select it through SAF and grant access again. Old app-private settings,
  permissions and caches do not transfer. Reapply settings manually. Do not treat
  an APK build as physical-device SAF validation.
- **Linux:** run the new AppImage and select the new folder, or use the migrated
  desktop settings. Old generated development-client entries are not reused.
- **Windows:** the new installer is a separate installation. Check the name,
  executable, install path, shortcuts and uninstall entry on native Windows.
- **Obtainium:** use `pioneer/textmoney` and the new Android ID after publication;
  recreate the old tracking entry rather than assuming a cross-ID update.
- **Dropbox/Dropsync:** update the synchronized folder selection explicitly.
  Complete download before opening a journal on the next device.

Compare account balances per currency, inactive accounts/categories, transaction
history, reimbursement links and budget reports before resuming editing.
Keep the old app and complete source archive until these checks pass. No private
user data has been transferred simply by implementing this command.

## Rename verification

- `uv sync`: dependency lock refreshed for the renamed project.
- Synthetic migration tests check lossless LF/CRLF conversion, balances,
  inactive visibility, native reimbursement links, budgets, source preservation,
  settings mapping and failures. Production runtime does not accept the old
  journal filename as an alias.
- `uv run inv build-android` and `uv run inv check-beancount-apk`: development
  APK built and its actual Python payload executed on Flet's host CPython.
  Android package inspection confirmed `com.pioneer.textmoney`, label
  `TextMoney`, version `0.3.15` / build `38`, and all three supported ABIs.
  This local development APK is not a production-signed release.
- `uv run inv build-linux-appimage`, `uv run inv validate-linux-appimage` and
  `uv run inv smoke-linux-appimage`: local AppImage built, structurally validated
  and kept running for the 20-second Xvfb startup check.
- GitHub rename retained the existing repository ID; origin now uses
  `https://github.com/pioneer/textmoney.git`. Old release downloads are being
  retired separately, without removing Git tags.

No Android device was attached. Physical SAF re-selection/restart/write/recovery
and native Windows install/uninstall checks for the new identity remain pending.
Historical device and Windows CI results do not certify the renamed binaries.
The v0.3.16 release workflow verifies production Android packages and native
Linux/Windows packaging before publication. No private data has been migrated.
