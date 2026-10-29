# CRUD and Fava compatibility - milestone archive

This is a historical development record, not the current user guide or format
contract. Sections describe different milestones and include superseded UI,
metadata, validation and timezone behavior. Old test counts and APK measurements
record those builds only; they do not establish current device compatibility.

Use the maintained guides instead:

- [Supported journal format and editing](journal-format.md)
- [Data safety, backups and recovery](data-safety.md)
- [Sequential editing and synchronization](synchronization.md)
- [Reports](reporting.md) and [Category budgets](budgets.md)
- [Current regression and device checklists](testing.md)
- [Published downloads](https://github.com/pioneer/textmoney/releases/latest)

In particular, current writes use `name`, local `time` and native reimbursement
links, not persistent transaction IDs or the legacy metadata described below.
Fee defaults are local, supported hand-written transactions can be edited, and
negative asset balances warn rather than block saves. Timezone changes do not
convert saved wall-clock times. Do not copy historical examples as a current
journal specification.

## Historical scope and format

### Category editor

The category editor has matching **Expenses** / **Income** sections
(**Витрати** / **Доходи** in Ukrainian), not a mixed list under an expense-only
heading. Both sections use the same typography, spacing and edit/delete actions.
Parent categories expand/collapse to show their children; leaves remain compact
rows. Sections start expanded and category branches start collapsed.
Expansion choices survive refreshes, navigation, resizing and language changes
within the current app session; they are not stored in the financial journal.
Missing intermediate declarations are displayed as structural, non-editable
nodes. Existing category declarations, inactive labels and lifecycle rules are
unchanged, as are category selection labels in the transaction form.

### Account and category code suggestions

New-account and new-category forms suggest Beancount identifiers from the
display name, account type/currency, existing account conventions, and optional
category parent. The offline normalization includes predictable Cyrillic
transliteration and a small set of unambiguous common terms. Existing entities
are not renamed when their display names change. Suggested identifiers remain
editable; after a manual edit, attribute changes do not replace the entered code.
**Restore suggested code** applies the current suggestion again and restores
automatic suggestions after manual edits or selecting an alternative. It does
not create or save an entity. The button is enabled only after a manual edit or
alternative selection, with a nonblank name; restoring the suggestion disables
it again. This also applies to inline category creation. Invalid codes and
collisions are shown and checked again at save time; identifiers are never
silently reused or suffixed.

Inline category creation in the transaction form uses the active operation's
income/expense type and optional existing parent. On success, it selects the new
category while retaining the unsaved transaction draft; creating a category
does not submit that transaction.

Release 0.2.2 (Android build 19): `uv run inv check` passed formatting, Ruff,
Pyright and 481 tests. Transaction form tests use a fixed clock consistent with
their journal fixtures, avoiding wall-clock-dependent date failures.
`uv run inv build-android` produced the APK in `build/apk/`, and
`uv run inv check-beancount-apk` passed. Interactive browser validation remained
blocked by a blank integrated browser; native Android/Windows/macOS runtime
checks were not performed.

### Backup retention

The maintained [backup-retention contract](data-safety.md#backup-retention) is
separate from these milestone results. No auxiliary sync snapshots are created,
read or required; existing contents are ignored and not automatically deleted.

Release 0.2.1 (Android build 18): `uv run inv check` passed formatting, Ruff, Pyright and 476 tests,
including exact tier boundaries, overlap, failure/cancellation protection,
restart, settings persistence, confirmation and Android transport validation.
`uv run inv build-android` produced the APK in `build/apk/` and
`uv run inv check-beancount-apk` passed. A browser
smoke server loaded the synthetic journal, but the integrated browser remained
blank, so visual interaction was not verified. Native SAF retention operations
still require physical-device testing; Windows/macOS runtime checks were not run.

### Universal transaction form

**Додати операцію** opens one form without a transaction-type dropdown:
description first, then unsigned positive amount with **− / + / ↔** immediately
to its left, accounts, category and date. A short hint below the amount explains
the selection: debit from an account, credit to an account, or transfer between
accounts. All dropdowns fill the form width and share aligned edges.
An adjacent **+** button creates a category without leaving the transaction.
The compact dialog uses the current operation's category type, the selected
parent and the same journal-backed creation service to suggest a valid code.
After a successful create, the new category is selected and the transaction's
write baseline is advanced to include the category change. With no matching
categories the selector explains that one can be added. Failed creation keeps
both dialogs and all transaction input available for correction.
**Додатково**
starts collapsed and holds fees, optional counterparty, time and special
receipt choices.

| Button | Accounts | Meaning |
| --- | --- | --- |
| − (default) | One | Expense from the account |
| + | One | Income into the account |
| ↔ | Source and destination, both required | Transfer source → destination |

Only **↔** shows the destination account and transfer fee controls, hiding the
ordinary category. Minus and plus hide the second account completely, and its
retained draft cannot turn an ordinary transaction into a transfer. Switching
buttons preserves numeric amounts, description, fee settings and category drafts.
For different currencies the first amount is the total source debit and the
second is the actual destination credit; neither is automatically replaced.
To reverse a transfer, explicitly change its source/destination accounts and
the exchanged amounts as appropriate. Fees retain their selected currency.
For same-currency fees, the single amount retains its existing nominal
added/deducted meaning; the preview shows actual debited/credited totals.

For **+**, **Додатково → Надходження**
offers income, reimbursement or balance adjustment. Reimbursement additionally
offers the original-expense reference. Hidden special/fee/reference values are
excluded from unrelated saves, but drafts are retained when switching back.
Date is always visible; time and timezone controls retain the existing picker,
DST validation and unchanged-timestamp behavior under Advanced.

Existing transactions use this layout too. Saved accounting kinds remain
protected as explicitly requested: the three-button selector and special-kind
choice are disabled, and submission guards enforce that rule. Transfer edits
allow both account changes but cannot become ordinary transactions. Existing
transfers open with **↔** selected, source first and destination second; no
additional UI state is stored in the journal. Saved fees, payee, kind and time
are summarized on collapsed Advanced.
Opening balances and unsupported constructs keep their existing guarded editors.

Verification: `uv run inv check` passes formatting, Ruff, Pyright and all 439
tests. Focused form tests exercise both signs, same-currency and exchange
directions, fee modes/currencies, draft retention, hidden-field isolation,
saved-kind protection, invalid inputs and round-trip edit/delete through the
actual Beancount loader. Existing reimbursement, adjustment, metadata, reporting
and storage-failure regressions remain passing.

Earlier Ukrainian browser checks at 390×844 and 1366×900 verified the compact default
layout, collapsed Advanced controls, unchanged amounts when switching sign,
and access to reimbursement/adjustment choices. A synthetic exchange with a
5 USD fee was created, reopened and reversed: the 105 USD and 4200 UAH fields
stayed attached to their accounts, the fee currency stayed USD, and the saved
source principal was exactly 100 USD with `@@ 4200.00 UAH`. The UUID and
timestamp survived editing, and visible balances matched persisted postings.
No native Android/Windows run or new APK build was performed for this UI refactor.

The description-first layout and debit/credit hints were also checked in the
browser at both sizes. Dropdown bounds matched exactly: 262 px wide on mobile
and 360 px on desktop, including the Advanced receipt selector. Changing to
plus preserved the entered 250 and updated the explanation and account roles.
Pinned Flet 1.0.1 requires expanded dropdowns inside rows for full-width fields;
its Flutter implementation does not apply the exposed `expanded_insets` value.

The explicit **− / + / ↔** revision was checked at 390×844 and 1366×900.
Minus initially showed only one account. Switching an exchange to plus hid its
destination, exchange preview and fees; returning to transfer restored both
amounts (105 USD / 4200 UAH), accounts and 5 USD fee. Saving and reopening
produced the expected single `@@` transaction and balances, including the
non-blocking overdraft warning. The selector is constrained to 132 px, leaving
130 px for the amount on the checked mobile layout. No native platform run or
APK build was performed for this revision.

### Same-currency transfers

Selecting **↔** enables a transfer with explicit source/destination accounts,
one amount and an optional Advanced fee. Cash, bank and single-currency liability
accounts share account selection by display name. A transfer is one directive:

| Fee mode | Source | Destination | Fee expense |
| --- | --- | --- | --- |
| None, amount 1000 | -1000 | +1000 | None |
| Added, amount 1000, fee 5 | -1005 | +1000 | +5 |
| Deducted, amount 1000, fee 5 | -1000 | +995 | +5 |

Only fees enter expense totals; transfers never enter income totals.
Same-account transfers, inactive/missing declarations,
nonpositive amounts and deducted fees equal to or larger than the amount are
rejected. The form edits source/destination, amount, fee mode/category,
counterparty, description and date/time; confirmed deletion removes all postings.
Unmarked/ambiguous split entries remain read-only rather than being reduced to
two postings.

At this milestone, Settings selected an active expense category as the default
fee category and stored an app-specific default-fee flag on its `open`.
**Superseded:** current defaults are local device preferences, not synchronized
journal metadata; see [current transfers](journal-format.md#transfers-and-exchange-precision).
In the absence of a marked category, this milestone used
`Expenses:Bank-Fees`, created with a localized display name only on
the first nonzero fee, in the same backed-up save as the transfer. An inactive
default must be reactivated or replaced; it is never silently bypassed.
This milestone stored an app-specific fee-mode key with `"added"` or `"deducted"` values;
current writes reconstruct fee presentation from postings without this field.
Unrelated transaction/posting metadata and comments survive edits. If an
annotated fee posting is removed, a zero-amount posting retains its annotations;
new zero-fee transfers have only two postings.

**Approved balance-policy change:** negative asset balances now warn instead
of invalidating the journal. The warning identifies accounts with a negative
balance anywhere in chronological history, even if later funding restored the
current balance. This applies to creation, editing, deletion, opening edits and
external reloads—not just transfers. Balanced postings, explicit declarations,
single-currency amounts, IDs and dates remain strict.

Liability accounts are explicitly created via **Loan / credit**. Enter a positive
opening amount owed; the opening posting is negative in `Liabilities:` and
positive in `Equity:Opening-Balances`. A bank-to-loan transfer credits the bank
negatively and debits the liability positively, reducing debt without creating
income or an expense other than an optional fee. Borrowing reverses those
postings. Liability balances are shown with standard signed Beancount values;
negative debt is not an overdraft warning. No interest, schedules, receivables,
or debt allocation is implemented.

### Cross-currency transfers

The same form automatically shows **Списано** (total debited) and **Зараховано**
(actually credited) when account currencies differ. Both amounts are entered
independently and remain authoritative. The optional fee uses either account's
currency; the added/deducted selector is hidden for exchanges.

| Inputs | Exchanged principal | Derived rate |
| --- | --- | --- |
| Debit 100 USD, credit 4200 UAH, no fee | 100 USD → 4200 UAH | 42 UAH/USD |
| Debit 105 USD, credit 4200 UAH, fee 5 USD | 100 USD → 4200 UAH | 42 UAH/USD |
| Debit 100 USD, credit 4195 UAH, fee 5 UAH | 100 USD → 4200 UAH | 42 UAH/USD |

An exchange uses an exact **total price** (`@@`) on the converted source
principal, never a rounded unit rate or artificial income/expense:

```beancount
2026-01-10 * "Exchange with a source-currency fee"
  Assets:Dollar -100.00 USD @@ 4200.00 UAH
  Assets:Bank 4200.00 UAH
  Expenses:Bank-Fees 5.00 USD
  Assets:Dollar -5.00 USD
```

For a destination-currency fee, the source total price includes the withheld fee:
`-100.00 USD @@ 4200.00 UAH`, destination `4195.00 UAH`, expense `5.00 UAH`.
At this milestone these were one transaction with a UUID and timestamp. Current
writes use local `time`, not these legacy identity fields.
Fees alone affect expenses in their own currency. Balances
sum actual units, not prices. Editing reconstructs totals, principal and fee
currency from postings; no rate or financial state is stored separately.

The milestone model validated exact signed total-price weights plus a bounded
exchange shape and rejected unit prices (`@`). **Superseded:** bounded `@`
exchanges are now readable but remain read-only in the transaction editor;
recognized hand-written transactions do not require an app ID for editing.
See [current exchange support](journal-format.md#transfers-and-exchange-precision).
Costs, inferred units, third-currency fees and ambiguous exchanges remain unsupported.
Amounts retain the existing 12-integer-digit/two-decimal limit. Exchange
serialization uses two decimals without changing monetary values: Beancount
internally converts `@@` to a unit price and needs normal monetary tolerances
for repeating ratios. Rate calculation uses 50-digit Decimal precision; the
preview displays 12 significant digits and marks the rate approximate. The rate
is never used to calculate or round postings.

Existing same-currency transactions need no migration. Source-fee debit and fee
expense annotations are retained on zero postings when their amounts are
removed. External synchronization races and non-atomic provider writes retain
the limitations documented below. No market rates, third-account fees, interest
calculations or automated FX gains/losses are introduced.

At the cross-currency milestone, `uv run inv check` passed formatting, Ruff,
Pyright and 410 tests.
The exchange matrix uses the actual Beancount loader, unit balances and Fava,
including inverse directions, repeating rates, extreme supported amounts and
both fee currencies. Ukrainian browser checks at 390×844 and 1366×900 verified
the source-fee preview (105 USD debited, 100 USD converted, 4200 UAH credited),
editing to a destination fee (100 USD debited, 4195 UAH credited, 5 UAH fee),
and deleting the complete transaction. These are not native Android/Windows
runtime checks; no new APK was built for this extension.

The Ukrainian browser workflow was checked with synthetic accounts at 390×844
and 1366×900: a deducted-fee loan repayment saved through an overdraft warning
and reduced the debt correctly; editing it to an added fee updated both balances
and cleared the warning; confirmed deletion removed the operation and its fee.
Automated tests also cover fee defaults, currency/amount rejection, backup and
conflict failures, metadata preservation and Beancount/Fava validation.
For release 0.2.0 (Android build 17), `uv run inv check` passed formatting, Ruff,
Pyright and all 443 tests. `uv run inv build-android` produced
the APK in `build/apk/`; `uv run inv check-beancount-apk` passed the
packaged Python checks for transfers, cross-currency exchanges, fees,
liabilities, editing and deletion. These checks are not native Android/Windows
runtime verification; no physical Android device was tested.

Transaction lists and recent activity show a one-line description preview below
the movement, truncated with an ellipsis when necessary. Multiline descriptions
are flattened for the preview only; the editor retains the full original text.
Complex entries show the description in their heading without duplicating it.
Opening entries keep their localized opening-balance label.

### Incoming money

The primary **Add transaction** (**Додати операцію**) action opens the universal
form with minus selected. Choose plus for income; use the Advanced receipt
choice for **Expense reimbursement** or **Balance adjustment**.
The same form edits ordinary expenses and receipts, keeping their accounting
kind fixed. Amount, description, counterparty, date/time and account are shared;
category choices are remembered separately when switching sign or special receipt.
Validation and conflict failures keep the draft open. Cancel closes without
saving; a new form starts blank with the current date/time and timezone.

The three receipt choices share the existing two-posting machinery,
verified backups and conflict policy:

| Choice | Debit | Credit | Income/expense effect |
| --- | --- | --- | --- |
| Income | Receiving `Assets:` account | User-selected `Income:` category | Income increases |
| Expense reimbursement | Receiving `Assets:` account | User-selected `Expenses:` category | Net expenses decrease |
| Balance adjustment | Receiving `Assets:` account | `Equity:Balance-Adjustments` | Neither income nor expenses changes |

The adjustment equity declaration is added only when first needed, in the same
validated save as the receipt. Opening-balance transactions and amounts are
not changed. The choice is explicit, never inferred from text or dates.
There is no receivable, debt tracking, automatic matching or currency conversion.

Income categories use the same hierarchy, names, active state and protected
deletion as expense categories; choose the category type when creating them.
Expense selectors exclude income categories. Receipts can be edited and deleted
through the existing icons. Their accounting type stays fixed during editing;
account, category, amount, description, sender and date/time remain editable.
Stable IDs, timestamps left untouched by the user and unrelated metadata/comments
are preserved. Reducing/deleting money already spent now warns if it creates a
negative historical asset balance, but remains allowed.

An optional original-expense selection fills category and full original amount;
change the amount for partial reimbursements and choose any same-currency
receiving account. Linked creation checks category/currency and that the receipt
does not predate the selected expense. At this milestone links were stored as
app-specific expense-reference string metadata. **Superseded:** current writes use native
shared links, not that field; see [current transactions](journal-format.md#transactions-and-editing).
Only ordinary app-editable expenses appear in
the chooser; unlinked receipts remain supported. There is no remaining-amount
limit or enforced allocation. Links survive later edits/deletion of the original
and do not change accounting or cascade deletions; they may refer to old records.

Reimbursements reduce net expenses on the **receipt accounting date**, not
retroactively on the purchase date. A period's net expenses can be negative.
`Ledger.period_totals(start, end)` uses inclusive dates and returns separate
`income` and `expenses` maps keyed by currency. It excludes equity adjustments
and opening balances. There was no dedicated reporting screen at this milestone;
current versions have [Overview](reporting.md) and [Budgets](budgets.md) reports.

Focused synthetic tests cover receipts, partial/unlinked/different-account
reimbursements, pre-tracking adjustments, period boundaries, distinct currencies,
edit/delete safety, metadata preservation, conflicts and storage failures.
Compatibility is checked with official Beancount and Fava; these checks are not
Android device validation.

An isolated browser smoke with synthetic data at 390×844 verified selecting an
original expense, receiving a partial reimbursement into a different account,
and updating the balance immediately. Editing 5 → 7 and confirmed physical
deletion worked; the reference and original purchase were preserved. At 1366×900
the incoming action remained available in the expanded layout. The unified
transaction action now replaces the separate incoming action and expense screen;
regressions cover all four creation/edit/delete modes and translations, draft
retention and the action across all three adaptive layouts. No real data was used.
The unified form was also smoke-tested in the browser at 390×844 and 1366×900:
mode switching retained draft fields and category selection; income creation,
expense creation/editing and a linked partial reimbursement updated the visible
balance correctly. The form stayed scrollable with Save/Cancel accessible.
The APK smoke script also covers income, reimbursement, equity adjustment,
per-currency totals and incoming edit/delete. Release 0.1.3 (Android build 15)
passed `uv run inv check` (321 tests, Ruff and Pyright), `uv run inv build-android`
and `uv run inv check-beancount-apk`. The APK was produced in
`build/apk/`, with SHA-256
`d4f93e9e4a64c17ef84f8650bbe8821e8da91348f36d0b1d80b58ec40f094c2d`.
Its application ID and signing certificate match the preceding releases.
The packaged smoke executes on host CPython, not Android; no native
Android/Windows run is claimed for this release.

This milestone extends the existing app, parser, desktop/SAF adapters and
backup/conflict policy. No real financial files were used during development.
**At the user's explicit choice, journals remain single-file.** Yearly files,
cross-file moves and migration are deferred. Changing a date across a year
boundary changes the date in the same journal, retaining the ID.

The following legacy editing rules predate source-bound editing of recognized
hand-written transactions; use the [current editing contract](journal-format.md#transactions-and-editing).

- **Expenses:** edit an app-marked, persistent-ID, ordinary two-posting expense's
  date/time, amount, account, category, payee and
  description. IDs, tags, links, comments, posting metadata and unrelated
  transaction metadata are preserved. Amounts use Decimal and the chosen
  account's currency, never implicit conversion.
- **Deletion:** permanently remove an ordinary expense after confirmation;
  no reversal is added. Comments from a removed directive are retained as
  standalone comments. Opening amounts can be edited through Accounts or the
  opening entry's pencil in Transactions, using the same validated service;
  split and externally authored/unmarked transactions remain read-only.
- **Accounts:** change display name, opening amount and active state. Technical
  IDs, currency and declaration date are immutable. Opening edits retain the
  existing opening transaction's ID/metadata. A zero-opening account can gain
  an   explicit opening entry. Reducing an opening amount now warns if a historical
  asset balance becomes negative.
- **Categories:** rename or deactivate/reactivate without changing IDs or
  hierarchy. An inactive parent makes all descendants unavailable for expenses.
  Historical balances and names remain visible for inactive items.
- Delete unused declarations only. Any transaction history, including an opening
  entry, prevents account/category deletion; the UI offers the Active checkbox
  instead. Child categories and references from other directives also block
  deletion. No historical transactions are removed when deactivating an item.

This historical example uses the superseded `display-name` key. Current output
uses `name`; boolean `textmoney-active` is the visibility preference:

```beancount
1970-01-01 open Assets:Cash UAH
  display-name: "Wallet"
  textmoney-active: FALSE
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
Home balance sections follow the saved currency order. Account currencies not
listed in these settings appear afterward in alphabetical order; configured
currencies without accounts do not create empty sections. Unsaved settings do
not change the displayed order.

Fava **1.30.16** was added as a desktop development-only test oracle alongside
official Beancount **3.2.3**. A regression reproduces Fava's exact
`No operating currency specified` diagnostic on a legacy synthetic journal,
sets operating currencies via the app service, and reloads it through
`FavaLedger`: the warning is gone and the ledger has no Fava errors.
Renaming/deactivating declarations is also accepted by both engines.
Fava is not bundled on Android, and the app still intentionally rejects
unsupported accounting semantics rather than pretending to be a full engine.

## Safety and conflicts

The maintained contract has moved to [Data safety, backups and recovery](data-safety.md).
Use it for current conflict handling, retention and Android interrupted-save review,
not the older metadata or checklist descriptions in this archive.

### Desktop atomic replacement

See the maintained [desktop replacement sequence and limitations](data-safety.md#desktop-atomic-replacement).
This heading remains for existing links.

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

The milestone used instant-based timestamps, converted their presentation when
the timezone changed and rejected ambiguous/nonexistent DST times.
**Superseded:** current journal times are local wall-clock strings without offsets.
Timezone changes do not convert saved times, and manual DST-gap/overlap wall
times are accepted without resolving an instant. See [current time semantics](journal-format.md#times-and-metadata).

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

### Release packaging: 0.1.1 (13)

- Version 0.1.1, Android version code 13.
- Project version/build in `pyproject.toml` now drives the About display,
  generated packaged metadata and versioned APK filename.
- Active parser module/action/task names now use `beancount_diagnostics` /
  `beancount-diagnostics`. Historical reports were renamed to
  [Android storage validation](android-storage-validation.md) and
  [Beancount compatibility](beancount-compatibility.md), retaining their findings.
- Storage diagnostics uses `storage-test.beancount`, separate folder preferences
  and a separate backup namespace. Legacy synthetic files, real journals and
  existing backups are not migrated or rewritten.
- `uv sync`: succeeded. `uv run inv format check build-android`: **265 tests
  passed**, Ruff/Pyright clean, APK built successfully.
- The first payload check caught stale renamed-module development bytecode in
  the archive. Build staging now removes source-tree bytecode caches before
  packaging; a regression verifies source files survive. The APK was rebuilt.
- Final `uv run inv check-beancount-apk check`: packaged release identity,
  absence of the old module, parser/CRUD/timezone payload checks passed on host
  CPython; **265 tests passed**, Ruff/Pyright clean.
- SDK `aapt dump badging` and `apksigner verify --print-certs` verified the
  package, version and unchanged debug signing certificate. `adb devices`
  listed no devices; this build has not been run on Android or Windows.
- Historical local APK produced in `build/apk/`,
  **141,273,366 bytes**. SHA-256:
  `10d3b93bfa6335fca1371ff49febb559b1b3e23c4ddedab9a15278c89c68e5dc`.
- Nothing was committed, pushed or published. The existing GitHub v0.1.0
  release remains unchanged. Debug signing is still experimental, not a
  production signing/recovery policy.

### System-language startup fix (build 12)

The user reported that System incorrectly selected English on Ukrainian desktop
and Android devices. Startup was reading `locale_configuration.current_locale`,
which is the app override, not the host's locale list; `on_locale_change` alone
does not supply initial device preferences. Startup now awaits Flet's supported
`page.get_device_info()` and resolves its ordered `locales`, then sets the app
and built-in widget locale. Later host events still update System mode; explicit
language choices remain fixed. No native adapter or dependency was added.

The MCP verifies `get_device_info`; its DeviceInfo model is absent from that
index, so the installed Flet 1.0.1 Python model and Flutter implementation were
checked: `locales` comes from `PlatformDispatcher.instance.locales`.

- Added a failing reproduction with an English app override and Ukrainian device
  locales; earlier tests incorrectly used the app override as the device locale.
- `uv run inv format check`: **251 tests passed**, Ruff/Pyright clean, including
  initial language, stored System/manual modes, ordered locale selection,
  unsupported-language fallback and explicit unavailable-device-info errors.
- Isolated browser with simulated `navigator.language=uk-UA` and preferred
  languages `uk-UA,en-US`: first launch was Ukrainian; switching to English
  then System restored Ukrainian. This verifies the actual Flet client device
  information path, not a mocked app locale. No financial files accessed.
- Physical Android and native desktop confirmation of this fix remain pending.
  Install the new APK, select System and fully restart on a Ukrainian device;
  the UI should remain Ukrainian. Then test English → System.
- `uv run inv build-android`: build **12** succeeded.
- `uv run inv check-beancount-apk check`: exact APK payload smoke passed on
  host CPython; final **251 tests passed**, Ruff/Pyright clean. SDK
  `aapt`/`apksigner` verified code 12 and the unchanged signing certificate.

### Opening-balance and heading follow-up (build 11)

- Opening entries in history/recent activity no longer show a date subtitle.
  Their editor hides the original accounting date, timestamp and ID under
  collapsed Details. These stored fields remain unchanged by amount edits.
- The transaction pencil opens a dedicated opening-amount editor, reusing
  `edit_account` and its negative-balance checks, verified backups, stable IDs,
  metadata preservation and stale-source/conflict protection. Account and
  currency cannot be reassigned here; multiple/complex openings stay read-only.
  Account editing remains available as before.
- The small app-name label above page titles is removed; the window/app title
  remains. Settings has only the shared page title, without a duplicate heading.
- Manual checks: edit an opening amount from both Transactions and Accounts,
  verify immediate balances/history, inspect Details, try an insufficient
  opening amount and an external conflict, and check all localized headings.
- `uv run inv format check`: **246 tests passed**, Ruff/Pyright clean. Added
  coverage for opening edits (including zero), dates/ID/comment preservation,
  insufficient balances, external/stale conflicts, backup failures, cancellation,
  complex-opening refusal, hidden row dates and localized nonduplicate headings.
- Isolated synthetic-data browser check at 390 x 844 and 1366 x 900: no app-name
  header or opening-date subtitle, dedicated amount editor and collapsed Details,
  successful 100 → 120 opening edit with immediate 90 → 110 balance update,
  unchanged dates/ID/receipt metadata, and a single Settings page heading.
- `uv run inv build-android`: build **11** succeeded.
- `uv run inv check-beancount-apk check`: packaged Python smoke passed on
  host CPython; final **246 tests passed**, Ruff/Pyright clean. SDK
  `aapt`/`apksigner` verified version code 11 and unchanged signing certificate.
  This follow-up has not been physically tested on Android or Windows.

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

### Historical build 12

Historical local APK produced in `build/apk/`, **build 12**, 141,395,218
bytes (about 135 MiB). Package remains `com.pioneer.financepoc`,
version 0.1.0; Android 7.0+ (target API 36). Packaged ABIs:
`arm64-v8a`, `armeabi-v7a`, `x86_64`.

APK SHA-256:
`8fff603ff4163a39bbb6765461c8cd9a766f0bc3c2701f2f68be6c26fd21796f`.

The signing certificate matches previous builds:
`49895518fe2c9679d44893a123a51b12a89d8b7c7605ffa6f0940ad551a82700`.

## Physical-device checklist

The old checklist used persistent IDs, instant-based timestamps and rejected
negative balances. It is not valid for current builds. Use the maintained
[transaction/device checklist](testing.md#transaction-and-device-checklist),
[sequential-file checklist](testing.md#sequential-file-workflow-regression-checklist)
and [budget checklist](testing.md#category-budget-regression-checklist), always
with a disposable synthetic journal. Historical APKs above are measurements,
not download links; use [published releases](https://github.com/pioneer/textmoney/releases/latest).
