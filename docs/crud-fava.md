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

**Settings → Advanced → Journal display currencies** accepts one or more
space-separated currency codes, e.g. `UAH USD EUR`. The UI explains their purpose
without requiring knowledge of Fava:

> Currencies used to organize amounts when viewing this journal in compatible
> applications. Accounts keep their own currencies; this setting does not
> convert money or combine balances.

The root file contains separate standard Beancount options:

```beancount
option "operating_currency" "UAH"
option "operating_currency" "USD"
option "operating_currency" "EUR"
```

Loading an existing file does not modify its configuration. Saving settings
updates only operating-currency directives and retains titles, comments and all
other source. Blank/invalid lists are rejected. New account creation appends
its currency if missing, retaining existing choices and their order. The option
and account declaration use one validated, backed-up save, never separate writes.
Loading older journals alone never adds options. Existing options are not
removed when an unused account is deleted.
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

## Compact lists and simpler expense entry (build 9)

Ordinary expenses show `account → category`, one unsigned movement amount and a
small localized accounting-date subtitle, without payee. Opening entries show a localized opening
label and one amount. Split/multi-currency entries show a multiple-postings label
instead of inventing a combined amount; their complete postings remain visible
in read-only details. Account rows retain name/balance and inactive state;
category rows retain names/hierarchy. Technical IDs, descriptions and timestamps
are kept out of lists. Edit/delete are icons with translated tooltips;
confirmation and historical-deletion safeguards are unchanged.

Settings has a device-local **Timezone** (System by default, or a named IANA zone).
Expense forms have a prominent full-width multiline description and a collapsed
optional **Shop / recipient** section. This is the recipient, not the payer or
category. New payees start blank; the earlier default-payee setting is removed
and its stored preference is ignored. Saved payees survive unrelated edits even
while hidden. Successful new saves clear/collapse this section; failures retain
the draft and its expansion state. Descriptions permit line breaks (500-character
limit); disallowed control characters are rejected. Line breaks stay within
quoted journal strings; embedded quotes and backslashes are escaped.

Display dates follow the selected app language, e.g. `Oct 18, 2026`,
`18 жовт. 2026`, `18 окт. 2026`. Manual date fields use `MM/DD/YYYY` for English
and `DD.MM.YYYY` for Ukrainian/Russian, with visible format hints. All three
locales use explicit 24-hour times. Forms show just one date and time, both
manually editable with calendar/clock pickers. They initially show the timestamp
in the selected timezone. If untouched, the exact original timestamp and
accounting date remain intact, including existing differences. Editing either
date or time sets both the accounting date and timestamp from the visible group.
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

### Build 9 simpler-form verification

- `uv run inv format lint typecheck`: Ruff/Pyright clean.
- Focused `PYTEST_ADDOPTS='-q tests/test_crud.py tests/test_time_ui.py' uv run inv test`:
  blank payee, multiline narration, and currency auto-add safety passed.
- `uv run inv check`: **226 tests passed**, including official Beancount/Fava
  checks, hidden-payee preservation, explicit payee clearing, newline/quote/
  backslash round-trips, invalid descriptions, unchanged differing dates and
  exact `Z`/subsecond timestamp preservation, and failed account creation never
  saving currency options separately.
- Live isolated synthetic-data web smoke at 390 x 844 and 1366 x 900:
  one date plus time, a larger full-width description, blank hidden payee and
  successful blank-payee save, multiline source read-back, an amount edit with
  saved hidden payee/date/ID/receipt metadata preserved, compact payee-free rows,
  Ukrainian calendar selection without a day shift, and Advanced currencies
  retaining expansion through language change. No real financial data used.
- `uv run inv format check build-android`: **226 tests passed**, APK build
  succeeded with version code **9**.
- `uv run inv check-beancount-apk check`: exact APK Python payload passed
  blank-payee, multiline-description, currency-auto-add, parser/CRUD and bundled
  timezone smoke checks on host CPython 3.14.7; final **226 tests passed**.
- SDK `aapt dump badging` and `apksigner verify --print-certs`: build identity
  and unchanged debug certificate verified. `adb devices`: no device attached.

Android/Windows runtime behavior for this follow-up still requires device
testing. SAF storage behavior is unchanged.

## APK

### Build 10 Settings follow-up

The user accepted the simplified iteration. Settings now explicitly describes
the journal as Beancount format in all three languages. Language choices are
System, English, Ukrainian and Russian. System follows host locale changes
(English fallback); explicit choices remain fixed. Both modes persist across
restarts, and switching back to System uses the latest reported device language.
Verify selecting System, restarting, changing the device language and switching
to/from an explicit app language on Android.

- `uv run inv format lint typecheck`: clean.
- `PYTEST_ADDOPTS='-q tests/test_localization.py' uv run inv test`: 25 passed.
- Live isolated compact browser check: Beancount wording, all four language
  choices, Ukrainian selection and switching back to System; the stored
  preference was `system`. No financial files were accessed.
- `uv run inv check build-android`: **235 tests passed**, build **10** succeeded.
- `uv run inv check-beancount-apk check`: exact APK payload smoke passed on
  host CPython; final **235 tests passed**, Ruff/Pyright clean.
- SDK `aapt`/`apksigner`: version code 10 and unchanged signing certificate
  verified. This is not physical Android or Windows runtime verification.

[build/apk/txncount.apk](../build/apk/txncount.apk), **build 10**, 141,391,482
bytes (about 135 MiB). Package remains `com.pioneer.financepoc`, label
`TxnCount`, version 0.1.0; Android 7.0+ (target API 36). Packaged ABIs:
`arm64-v8a`, `armeabi-v7a`, `x86_64`.

APK SHA-256:
`6460e828d6918b46f340e9b9291bb510b92ffdd606d78cf1b36abb66f00bb66c`.

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
   Find the field under **Advanced → Journal display currencies**. Create an
   account with a missing currency: it must be appended without losing choices.
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
11. Add an expense without opening Shop / recipient; it should save blank.
    Expand the section and set a recipient on another expense. Edit its amount
    while the recipient stays collapsed: the saved recipient must remain.
    A failed/conflicting save keeps all draft text. Confirm no payee in lists
    or default-payee setting; new expenses start blank after restart.
12. Confirm only one visible date and time; try manual input and both pickers.
    Save a description with multiple lines, quotes and backslashes; reopen it
    and check the text, ID, metadata, backup and balance. Editing a legacy
    entry without touching its date/time must retain both stored dates exactly.
