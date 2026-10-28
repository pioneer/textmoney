# TxnCount MVP 0.1 - milestone archive

This is a historical development report, not a current format guide or device
checklist. Use [Supported journal format and editing](journal-format.md),
[Data safety](data-safety.md), [Sequential editing](synchronization.md) and
[Testing](testing.md) for current behavior. The old metadata, negative-balance
rejections and lists of missing features below describe milestone builds only.
Current output uses `name`, local `time`, native reimbursement links and
source-bound editing, not generated persistent transaction IDs.

This report records the original MVP and adaptive-UI builds through build 6.
The [CRUD/Fava archive](crud-fava.md) records later milestones, not the current
release contract. Download current binaries from
[GitHub Releases](https://github.com/pioneer/textmoney/releases/latest).

**Android update (2026-10-18):** the user confirmed recording an expense and
seeing the account balance update on a physical phone. Other MVP device checks
below have not been reported, so backup recovery, restart behavior, external
reload, conflict handling, and layout adaptation on devices are not independently
verified. Both earlier PoCs also passed on the user's phone. Use synthetic data
in a separate folder for further testing.

**Build 5 feedback:** language switching and rotation worked on the user's phone,
but the bottom menu and Add expense label stayed English, the expense action did
not open, and theme changes did not appear. Opening-balance transaction titles
also remained English. Build 6 fixes page-level updates for navigation, the
floating action button and theme, and translates opening titles at display time
without modifying journal text. The fixes are verified with automated tests and
a live compact browser UI using an isolated synthetic journal; Android
confirmation of build 6 is pending.

## Implemented at this milestone

- **Accounts**: explicitly create a technical ID, display name, one currency and
  optional nonnegative opening balance; show recalculated Decimal balances.
- **Categories**: explicitly create hierarchical paths, e.g. `Food:Groceries`.
  Creating a path also declares missing parent categories.
- **Add expense**: select an account/category; enter amount, payee and date.
  Each save creates balanced explicit postings, a UUID and timezone-aware
  creation timestamp. Currency comes from the account.
- **History**: displays opening entries and expenses, postings, IDs and timestamps.
- **Persistence**: proven direct SAF/desktop adapters, verified unique backups,
  SHA-256 conflict checks and write read-back. Only the filename allowlist was
  extended; no replacement storage mechanism or private copy was introduced.
- Five-second polling reloads valid external changes and recalculates everything.
  All forms survive reload, conflicts and failures. Failed saves show a modal;
  successful saves clear only the submitted form.
- **Product UI foundation**: Home, Transactions, Accounting and Settings adapt
  between compact bottom navigation, medium navigation rail and expanded rail.
  English, Ukrainian and Russian labels, locale-sensitive amounts/dates, and
  system/light/dark themes are available. Language and theme choices persist;
  desktop settings use the platform user configuration directory while Android
  continues to use Flet SharedPreferences.
- **Accounting**: Accounts and Categories have separate subtabs with matching
  panel spacing, Add actions and existing edit/delete controls. Creation uses
  scrollable dialogs; failed saves retain the draft and stale journal snapshots
  cannot be submitted. Categories retain the expandable expense/income tree.
  The selected subtab survives navigation, refresh, language and width changes
  during the session. Operating currencies remain in Settings.
- Original storage/parser screens remain under **Technical diagnostics**. Their
  `poc-test.beancount` remains separate from the application journal.

Business models/calculations are Flet-free in [ledger](../src/app/domain/ledger.py).
The [codec](../src/app/infrastructure/beancount_journal.py) uses the phone-tested
autobean parser. The small [journal service](../src/app/journal.py) coordinates
append-only persistence through the original storage policy. There is no generic
repository framework or new runtime dependency.

## Historical synchronized format

The selected folder contains **`txncount.beancount`** (UTF-8). Account definitions,
display names and entries share one file, avoiding multi-file write transactions.
An abbreviated example:

```beancount
2026-10-18 open Assets:Cash UAH
  display-name: "Wallet"
2026-10-18 open Expenses:Food
  display-name: "Food"

2026-10-18 open Equity:Opening-Balances
2026-10-18 * "Opening balance"
  id: "2dde05e8-f594-45a8-8c2e-203fb10c230b"
  timestamp: "2026-10-18T10:00:00+03:00"
  Assets:Cash 100.00 UAH
  Equity:Opening-Balances -100.00 UAH

2026-10-18 * "Synthetic shop" "Expense"
  id: "ecb4c169-d344-4251-b8c3-b5221ec7f902"
  timestamp: "2026-10-18T10:01:00+03:00"
  Expenses:Food 12.34 UAH
  Assets:Cash -12.34 UAH
```

New account and category declarations default to today and can be explicitly
backdated. Account opening and opening-balance effective dates are separate;
the balance date cannot precede the account declaration. Category availability
cannot precede an existing parent. Existing historical declarations—including
1970-01-01 dates—are retained and never rewritten just to normalize the file.
Timestamp is creation time; transaction date is the user-entered accounting date.
`Equity:Opening-Balances` is a reserved offset, not an implicitly created spending
account. Its declaration is dated no later than the earliest opening transaction.
Zero opening balance creates no opening transaction.

Technical paths use uppercase-initial ASCII segments separated by colons.
Display names/payees support Unicode. Amounts allow up to 12 integer digits and
two fractional digits; currencies are three-letter codes. No binary floats.
Expenses must be positive and dated from 1970-01-01 through today.
Running balances are checked after each transaction in date/timestamp order,
including backdated entries; negative ordinary-account balances block saving.

### Preservation and restricted profile

Existing bytes are retained as an exact prefix on append, including comments,
metadata, tags, spacing and LF/CRLF. Targeted edits splice only the selected
directive; unrelated bytes remain untouched. Unrelated files are never read or
changed. Known descriptive directives such as
custom/note/event/price/commodity/document are preserved without interpreting them.
External explicit single-currency balanced transactions can be read; absent app
IDs use source-line identities for display only, without rewriting their metadata.

New transactions store their stable `id` and creation `timestamp`, but no longer
serialize the redundant `txncount-kind` field. The reader honors an explicit
legacy kind first, otherwise infers only supported posting patterns using the
ordinary account/category roots and reserved equity accounts. Ambiguous or
unmatched entries stay external/read-only. Transfer fee mode remains explicit
because postings alone cannot distinguish same-currency added versus deducted
fees; reimbursement links retain the original expense ID. Missing
`txncount-active` means active, so edits do not add redundant `TRUE`; explicit
legacy `TRUE` and `FALSE` remain understood.

This is a **dedicated, single-file journal**, not a general Beancount book importer.
Includes, plugins, inherited context, pad/balance assertions, close directives,
liabilities/income accounts, inferred amounts, costs, FX, future-dated transactions,
unknown syntax and unsupported accounting options block loading/saving with an
explicit error. Duplicate IDs, undeclared accounts, currency mismatches,
unbalanced postings and negative running balances also block saving.
The original file is left intact; fix/restore it externally and **Reload**.
No unsupported content is silently dropped to obtain an apparent balance.
The official engine remains a desktop test oracle, not an Android dependency.

## Save and recovery limits

- Existing contents are backed up under
  `backups/txncount-<unique-UUID-hex>.beancount`, verified before modification.
  The separate PoC keeps its `poc-test-...` namespace.
- Hashes are checked before backup and again before writing. A conflict refuses
  the save and retains form input. Reload and review the new balance/history
  before deciding to resubmit.
- A failed backup prevents writing. An uncertain write blocks further saves
  until Reload; inspect history and backup first to avoid duplicate submission.
- Current desktop saves use same-directory staging, file fsync, a final hash
  check and `os.replace()`; parent-directory fsync is attempted where supported
  (not on Windows). Android SAF writes remain **non-atomic**. Neither adapter
  offers compare-and-swap or automatic merging; a concurrent external write
  after the last hash check cannot be excluded. Failed/uncertain writes may
  require manual restoration from the displayed backup. See the current
  [desktop write contract](data-safety.md#desktop-atomic-replacement).
- Android now records save intent before direct writes. Unfinished or damaged
  recovery state blocks modifications across restart, including valid-looking
  truncation. Review in Settings; no automatic restoration or retry. See
  [current Android recovery](android-storage-validation.md#current-android-save-recovery).
- Invalid external data hides stale balances/history and disables saves.
  Disappearance of an already loaded journal blocks reset in that session.
- Folder access is remembered; unsaved forms/baselines are memory-only and do
  **not** survive app termination. External sync belongs to Dropsync/Dropbox or
  another user-managed tool; the app does not schedule or certify synchronization.
- Whole-file parsing, backups and history are deliberately unoptimized for this
  first slice. No account editing/deletion, expense editing, income, transfers,
  FX, loans, budgets, reports, import, database or backend.

## Verification

Commands executed:

| Command | Result |
| --- | --- |
| `uv sync` | Locked environment synchronized; no new runtime dependency |
| `uv run inv format lint typecheck test` | Iterative checks; caught formatting, typing and message-regression issues, fixed before final check |
| `uv run inv check` | Ruff formatting/lint, Pyright, **156 tests passed** |
| `GDK_BACKEND=x11 WAYLAND_DISPLAY= xvfb-run -a -s '-screen 0 1600x1200x24' uv run inv run` | Expanded Linux UI rendered without a NavigationRail error; native folder-picker flow was not completed |
| `uv run inv build-android` | Successful, version code 6; ARM64, ARMv7 and x86_64 |
| `uv run inv check-beancount-apk` | Exact APK Python payload passed parser fixtures and account/category/expense/backup/reload smoke on host CPython 3.14.7; this is not Android runtime verification |
| `/home/pioneer/Android/sdk/build-tools/35.0.0/aapt dump badging build/apk/txncount.apk` | Identity, version code, target SDK and packaged ABIs verified |
| `/home/pioneer/Android/sdk/build-tools/35.0.0/apksigner verify --print-certs build/apk/txncount.apk` | APK signature verified; same existing debug certificate |

Tests cover exact Decimal accounting and negative/backdated balances, timezone
ordering, account/category creation, metadata/UUIDs, hierarchy, supported external
records, official Beancount 3.2.3 validation and matching balances, exact byte
preservation (including CRLF), invalid/unsupported data, backup failures, uncertain
writes, conflicts including changes during backup, external reload/deletion, UI
draft retention/modal feedback and all existing PoC regressions. Tests use only
temporary synthetic data.

Linux expanded layout was rendered under Xvfb without the NavigationRail layout
error. Responsive breakpoints and navigation state are covered at 390, 800 and
1366 px. The build 6 compact browser smoke at 390 x 844 verified translated
bottom navigation, translated Add expense and opening-balance titles, a visible
dark theme and an expense form opened by the floating action button.
It used an isolated configuration directory and a synthetic 100 UAH account,
not the user's financial files. Medium layout has not been visually checked.
Linux accounting/persistence and UI handlers are tested; a full native
picker-driven workflow and Windows execution remain unverified.

## APK and physical-device checklist

**Historical local output:** `build/apk/txncount.apk`,
141,057,234 bytes (about 135 MiB). Identity remains `com.pioneer.financepoc`,
version 0.1.0, build **6**, Android 7.0+ (target API 36), with `arm64-v8a`,
`armeabi-v7a` and `x86_64` ABIs. `apksigner` verified the same existing debug
certificate, SHA-256
`49895518fe2c9679d44893a123a51b12a89d8b7c7605ffa6f0940ad551a82700`.

APK SHA-256:
`dbb0cc8b7ed0d80bc5a0459a88d9431220332606c0922c8e0758afafcacf1d3b`.
Packaged grammar extraction and MVP modules were checked. No Android device or
emulator is connected here; host execution of the APK archives is **not**
Android runtime verification. Physical testing of adaptive navigation,
localization and theme persistence is still pending.

The following checklist belongs to build 6 and contains superseded expectations,
including insufficient-funds rejection and IDs/timestamps. Do not use it to
validate current builds; use [the maintained checklist](testing.md#transaction-and-device-checklist).

1. Open **txncount** and select a dedicated synthetic/synchronized test folder.
   Expect the new-journal prompt and retained read/write access. Do not use real
   financial files.
2. Create `Cash`, display name `Wallet`, currency `UAH`, opening `100`.
   Expect `100.00 UAH` and `txncount.beancount` in that exact selected folder.
   Optionally create an unfunded USD/EUR account; spending from it must fail.
3. Create category `Food:Groceries`, display name `Groceries`. Verify the parent
   `Food` and leaf are both listed.
4. In **Add expense**, choose Wallet/Groceries, `12.34`, a synthetic payee and
   today's date. Save; expect `87.66 UAH` and a History entry with ID/timestamp.
   Inspect `backups/`; previous contents must be preserved there.
5. Try spending `100`: expect a visible insufficient-funds dialog, unchanged
   journal/balance and retained form inputs. Correct the amount and retry.
6. Restart (do not reinstall/uninstall). Confirm the same folder, balance,
   categories and history load; no duplicate opening or expense entry.
7. With an unsaved form, externally append a comment or supported transaction.
   Within about five seconds expect external-reload status, recalculated data
   and unchanged inputs. A valid conflicting save before polling must be refused;
   after polling it is revalidated against the newly loaded balance.
8. Externally introduce an invalid/unsupported entry: expect saves blocked and
   original bytes untouched. Restore a valid file and Reload; forms remain.
   Optionally verify both original PoCs under **Technical diagnostics** still work.
9. Confirm compact phone and medium tablet navigation, then resize/rotate and
   confirm the selected screen remains active.
10. Select English, Ukrainian and Russian and restart after each; verify labels
    persist. Try system, light and dark appearance settings.

MVP Android success requires these actual device results, not tests or APK
compilation. No commits or pushes were made; development stops for user feedback.
