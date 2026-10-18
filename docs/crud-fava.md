# CRUD and Fava compatibility

## Scope and format

This milestone extends the existing app, parser, desktop/SAF adapters and
backup/conflict policy. No real financial files were used during development.
**At the user's explicit choice, journals remain single-file.** Yearly files,
cross-file moves and migration are deferred. Changing a date across a year
boundary changes the date in `txncount.beancount`, retaining the ID.

- **Expenses:** edit an app-marked, persistent-ID, ordinary two-posting expense's
  date/time, amount, account, category, payee and
  description. IDs, tags, links, comments, posting metadata and unrelated
  transaction metadata are preserved. Amounts use Decimal and the chosen
  account's currency, never implicit conversion.
- **Deletion:** permanently remove an ordinary expense after confirmation;
  no reversal is added. Comments from a removed directive are retained as
  standalone comments. Opening entries must be managed through Accounts;
  split and externally authored/unmarked transactions remain read-only.
- **Accounts:** change display name, opening amount and active state. Technical
  IDs, currency and declaration date are immutable. Opening edits retain the
  existing opening transaction's ID/metadata. A zero-opening account can gain
  an explicit opening entry. Reducing an opening amount is rejected if any
  historical running balance would become negative.
- **Categories:** rename or deactivate/reactivate without changing IDs or
  hierarchy. An inactive parent makes all descendants unavailable for expenses.
  Historical balances and names remain visible for inactive items.
- Delete unused declarations only. Any transaction history, including an opening
  entry, prevents account/category deletion; the UI offers the Active checkbox
  instead. Child categories and references from other directives also block
  deletion. No historical transactions are removed when deactivating an item.

Active state is ordinary Beancount metadata on the `open` directive:

```beancount
1970-01-01 open Assets:Cash UAH
  display-name: "Wallet"
  txncount-active: FALSE
```

An inactive account is not a Beancount `close`: historical or externally edited
postings remain readable. New/edited expenses using inactive accounts/categories
must first reactivate them. An externally authored or ambiguous/multiple opening
balance may require an external editor; the app refuses unsafe changes.

## Fava and operating currencies

Settings accepts one or more space-separated currency codes, e.g. `UAH USD EUR`.
The root file contains separate standard Beancount options:

```beancount
option "operating_currency" "UAH"
option "operating_currency" "USD"
option "operating_currency" "EUR"
```

Loading an existing file does not modify its configuration. Saving settings
updates only operating-currency directives and retains titles, comments and all
other source. Blank/invalid lists are rejected. New account creation initializes
an operating currency from the account currency only when none is configured;
it does not replace an existing list. Use Settings to extend that list.
These options do not convert or combine currencies.

Fava **1.30.16** was added as a desktop development-only test oracle alongside
official Beancount **3.2.3**. A regression reproduces Fava's exact
`No operating currency specified` diagnostic on a legacy synthetic journal,
sets operating currencies via the app service, and reloads it through
`FavaLedger`: the warning is gone and the ledger has no Fava errors.
Renaming/deactivating declarations is also accepted by both engines.
Fava is not bundled on Android, and the app still intentionally rejects
unsupported accounting semantics rather than pretending to be a full engine.

## Safety and conflicts

Before an edit, the lossless parser must round-trip the original source exactly.
Only selected directive spans are replaced; other bytes, including LF/CRLF and
unrelated descriptive directives, are untouched. Candidate syntax, declarations,
balanced single-currency postings, unique IDs and every chronological balance
are validated before storage is modified. The existing policy checks hashes,
creates and verifies a unique backup, rechecks hashes and compares read-back
bytes to the already validated candidate before updating the in-memory ledger.

Every edit/confirmation captures its opening source baseline and journal object.
Polling, manual reload or a changed folder cannot silently apply old form values
to a newer version. Conflicts leave the draft visible. Cancel/reopen after
reviewing the current data; Settings also has Cancel to reload current currency
settings. Backup/write failures are explicit, not success-shaped fallbacks.

Writes remain **non-atomic** on SAF and desktop. There is no provider
compare-and-swap, automatic merge or rollback. A simultaneous external write in
the final check/write window remains possible. A partial/uncertain write blocks
further saves; inspect the verified backup and restore externally if necessary.
Take an independent backup before upgrading a real-data installation.

## Compact lists, local dates and defaults (build 8)

Ordinary expenses show `account → category`, one unsigned movement amount and a
small accounting-date/payee subtitle. Opening entries show a localized opening
label and one amount. Split/multi-currency entries show a multiple-postings label
instead of inventing a combined amount; their complete postings remain visible
in read-only details. Account rows retain name/balance and inactive state;
category rows retain names/hierarchy. Technical IDs, descriptions and timestamps
are kept out of lists. Edit/delete are icons with translated tooltips;
confirmation and historical-deletion safeguards are unchanged.

Settings adds device-local **Timezone** (System by default, or a named IANA zone)
and **Default payee** (blank by default). A default prefills new expenses but
remains editable; a changed setting never replaces an existing draft or modifies
old transactions. Successful saves reset the next payee to the default; failed
saves retain the draft.

Display dates follow the selected app language, e.g. `Oct 18, 2026`,
`18 жовт. 2026`, `18 окт. 2026`. Manual date fields use `MM/DD/YYYY` for English
and `DD.MM.YYYY` for Ukrainian/Russian, with visible format hints. All three
locales use explicit 24-hour times. Accounting date, timestamp date and time have
manual fields and calendar/clock pickers; the separate dates make existing
entries with different accounting/recording dates editable without data loss.
Invalid partially typed dates retain their original format/hint across a
language change. Date pickers cover 1970–2100; manual fields retain validation.

Timezone changes convert timestamp presentation, not stored instants or
accounting dates. Unchanged timestamp edits preserve the exact original offset
and subsecond precision. Newly typed ambiguous or nonexistent DST times are
refused with an explanation rather than choosing an offset silently. Date/time
drafts retain their visible timezone when opened; reopen an editor to use a
subsequently selected timezone. Changing settings converts the new-expense
draft's time while retaining its instant and accounting date.

The Python runtime adds `tzdata`; the existing native bridge adds only
`flutter_timezone` 5.1.1 to retrieve the device zone. No SAF read/write/grant
operation was redesigned. Flet transports selected dates as UTC instants:
calendar input/output is explicitly converted through the **device** zone,
which can differ from the selected display zone. Desktop System follows the
Python host; the development web preview assumes browser and host share a zone.
Android device-timezone lookup still needs physical-device verification.

## Verification (CRUD build 7 baseline)

- `uv add --dev 'fava>=1.30,<2'`: Fava 1.30.16 installed for desktop validation;
  locked in `uv.lock`, no new runtime/native Android dependency.
- `uv run inv format lint typecheck test`: regression suite passed, covering
  ID/comment/metadata and CRLF preservation; changing currency/account/category
  and year; safe opening changes; unused deletion; hierarchy and active states;
  external conflicts, stale dialogs, cancellation, backup and uncertain-write
  failures; official Beancount and actual Fava compatibility.
- Live web smoke with an isolated synthetic journal/configuration at 390 x 844
  and 1366 x 900: Ukrainian edit/confirmation dialogs, expense amount/description
  edits, opening amount edit, operating currency settings, physical deletion,
  recalculated balances and five verified backups. The resulting file loaded
  cleanly in Fava; unrelated metadata and the opening ID remained intact.
- `uv run inv check`: Ruff format/lint and Pyright clean; **185 tests passed**.
- `uv run inv build-android`: successful, version code **7**.
- `uv run inv check-beancount-apk check`: exact APK Python payload passed the
  original parser/MVP smoke and the new CRUD/lifecycle/operating-currency smoke
  on host CPython 3.14.7; final 185-test check passed again. Fava and the official
  native engine are absent from the APK.
- SDK `aapt dump badging` and `apksigner verify --print-certs`: identity, ABIs
  and the existing debug signing certificate verified.

Host payload checks are **not** physical Android testing. No Android device or
emulator is attached. Windows and this milestone's Android UI are untested.

### Build 8 UI follow-up verification

- `uv sync`: lock resolved successfully; `tzdata` is an explicit runtime dependency.
- `uv run inv check`: **214 tests passed**, Ruff format/lint and Pyright clean.
  New tests cover localized display/manual dates, pickers and their UTC date
  transport, named/system DST rules, invalid/ambiguous/nonexistent times,
  unchanged timestamp precision, device-date accounting validation, compact
  movement rows, read-only details, default payee, settings failures and drafts.
- Live isolated synthetic-data web smoke at compact and expanded widths:
  Ukrainian compact rows, calendar selection without a one-day shift,
  24-hour clock picker, manual time save, separate accounting/recording dates,
  typed timezone saving, default payee persistence and new-expense prefilling.
  Source read-back confirmed the expense ID, unrelated receipt metadata,
  expected dates/offset and backup were retained.
- An initial build attempt was stopped before completion to include a
  browser-discovered editable-dropdown fix; the final build is listed below.

## APK

[build/apk/txncount.apk](../build/apk/txncount.apk), **build 8**, 141,392,626
bytes (about 135 MiB). Package remains `com.pioneer.financepoc`, label
`TxnCount`, version 0.1.0; Android 7.0+ (target API 36). Packaged ABIs:
`arm64-v8a`, `armeabi-v7a`, `x86_64`.

APK SHA-256:
`3103fb3b4c929fdc4a0e72b74e78fb8861b073ecbc37efc2467b0f4175aeaa8c`.

Update without uninstalling:

```sh
adb install -r /home/pioneer/workspace/txncount/build/apk/txncount.apk
```

If adb is not on PATH, use `/home/pioneer/Android/sdk/platform-tools/adb`.
The signing certificate matches previous builds:
`49895518fe2c9679d44893a123a51b12a89d8b7c7605ffa6f0940ad551a82700`.

## Physical-device checklist

Use a separate synthetic folder or a copy, not the only copy of your real data.

1. Update with `adb install -r build/apk/txncount.apk`; do not uninstall and lose
   the existing SAF grant. Verify existing accounts/history still load.
2. Set `UAH USD EUR` in Settings; restart and confirm persistence. Open the same
   file in desktop Fava: the missing-operating-currency warning must be absent.
3. Edit an expense's fields, including date/time and year. Check the same ID,
   metadata/comments, recalculated balances and verified backup.
   Check both manual fields and calendar/clock pickers, especially near midnight.
4. Cancel a delete: nothing changes. Confirm deletion: the entry is physically
   gone, no reversal exists, balances update and the previous file is backed up.
5. Rename an account/category. Lower an opening balance below required funds:
   reject without changing files. Try a safe opening adjustment and verify the
   original opening ID is retained.
6. Delete zero-opening unused accounts and leaf categories. For items with
   history, deactivate instead; check they remain in history but disappear from
   expense choices. Reactivate and verify availability. Try a parent category.
7. Keep an edit dialog open and change the journal externally. Wait for polling,
   then save: reject and retain input. Cancel/reopen to use the new baseline.
8. Check English/Ukrainian/Russian labels and dialogs, rotation, themes, and
   medium/desktop layouts. Test split/unsupported editing refusal explicitly.
9. Keep Timezone on System, then try `Europe/Kyiv` and `UTC`. Restart and confirm
   the selection persists. Confirm displayed timestamps use the chosen zone,
   but accounting dates and existing journal bytes do not change. Test phone
   timezone changes and dates near midnight; selecting a day must not shift it.
10. Check localized month names in history and numeric format hints in forms.
    Exercise date/time manual entry and pickers in every app language. Try DST
    gaps/overlaps: new ambiguous/nonexistent times must be refused explicitly.
11. Save a default payee, restart, add an expense and override it once. The next
    new expense should use the default again. A failed save must keep the
    one-off payee and other draft values; clearing the setting disables it.
