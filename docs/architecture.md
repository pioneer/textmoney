# Architecture

The entry point is [src/main.py](../src/main.py). Importing it does not launch UI.
It loads central settings, configures logging and exception hooks, and calls the
imperative Flet application. No declarative component framework.

```text
src/main.py                 composition / startup
src/app/config.py          validated environment configuration
src/app/version.py         source/packaged version from project configuration
src/app/logging_config.py  logging policy and exception reporting
src/app/application.py     page lifecycle and event boundary
src/app/ui/finance.py      MVP screens, forms, polling and lifecycle
src/app/ui/home.py         home composition and preserved storage diagnostics
src/app/localization.py    gettext catalog loading, runtime language discovery and locale formatting
src/app/timekeeping.py     default clock and native date-picker timezone transport
src/app/ui/datetime_fields.py shared manual/date-picker/time-picker controls
src/app/ui/beancount.py    separate bundled-fixture action; lazy parser import
src/app/beancount_diagnostics.py   pure-Python syntax/editing/quantity experiment, not a full engine
src/app/fixtures/beancount/ synthetic bundled books, never user storage
src/app/domain/storage.py  SHA-256 and conflict error, no Flet imports
src/app/domain/backups.py  timestamped backup identities and tiered retention
src/app/domain/recovery.py checksummed, hash-only Android save records and classification
src/app/domain/ledger.py   explicit accounts, Decimal postings and running-balance checks
src/app/journal.py         one-journal load/append/save coordination, no Flet
src/app/infrastructure/beancount_journal.py lossless reading, directive splices and generation
src/app/storage.py         shared read/backup/conflict/write policy and draft
src/app/infrastructure/    desktop I/O and Android bridge transport
extensions/finance-saf/    local Python/Dart service; existing native SAF libraries
assets/                   canonical maintained assets
tests/                    non-GUI behavior and task contract tests
tasks.py                  the sole project automation interface
```

Dependencies point inward: UI -> journal/domain and shared storage policy.
The small storage
session owns its `Folder` protocol and accepts desktop or Android adapters, plus
test fakes. It is intentionally a single module, not a service framework. Do not create empty
packages, generic repositories, dependency injection containers, or abstract
base classes in anticipation of features.

UI owns controls and navigation, not business decisions or raw HTTP/database
calls. GNU gettext PO files in `locales/` are the editable translation source;
Invoke compiles them to packaged MO catalogs in `src/app/locale/`. Runtime
language choices are discovered from those compiled catalogs, with English as
the required fallback. See the [localization guide](localization.md) for
validation, compilation and Weblate setup. One width classifier selects bottom navigation for compact displays and
a navigation rail for medium/expanded displays. Compact layouts have four bottom
destinations and a top-right Settings gear. Settings opens separately with a back
arrow and no bottom bar or transaction action; Back restores the previous screen.
Medium/expanded layouts retain Settings as the fifth rail destination without
the header gear. This follows available width, not the operating system.
The same screens and form state
are retained through resizing. Translation resources are separate from controls;
language/theme choices are saved locally. Domain functions use Python types and return results or raise meaningful
exceptions. Services decide retry, timeout, cancellation, and user-facing failure
semantics. Adapters expose expected errors without hiding unexpected failures.

System language is resolved from `await page.get_device_info()` and its ordered
client `locales`, not `locale_configuration.current_locale` (the app override).
`on_locale_change` keeps that cached device choice current even while an explicit
app language is selected. The resolved language also configures Flet's own widgets.
Startup initially mounts only a language-neutral progress ring. Translated content,
navigation and the transaction action are revealed together after initial preferences
and journal restoration finish, avoiding a flash of the default English UI.
Startup failures exit the loading state and use the existing visible error reporting.

Configuration reads are centralized in `Settings.from_env`. Explicit `APP_DEBUG`
overrides the temporary packaged debug marker. Invalid values fail early, without
echoing potentially sensitive input. There is no dotenv loader or automatic
credential discovery in application code.

Do not block Flet's event loop with slow I/O. Use async APIs where supported and
await their results; own and observe background tasks, including cancellation.
Never leave detached failures as an invisible state change. UI handlers translate
known failures into an appropriate state; log unexpected failures with traceback
at the boundary and preserve the failure rather than silently returning success.

`assets/` is the single source. Flet packaging expects assets inside its app path,
so Invoke copies it to ignored `src/assets/` before build/mobile debug. Never edit
the staged copy. Generated Flutter files and native icons live under `build/`.
Run one build/mobile-debug task at a time in a checkout because they share staging.
`pyproject.toml` owns the app version and build number. Invoke generates ignored
`src/app/.release.json` for packaged About/version access and renames Android
development output to `txncount-<version>.apk`. Production builds collect one
standalone APK per ABI as `dist/android/txncount-<version>-<abi>.apk`, all with the
same project version code. Source runs read the project configuration;
no second maintained version constant is needed.

The journal format is standard Beancount wherever possible. Format extensions
(app-specific metadata, `custom` directives, reserved account conventions) need
explicit maintainer approval before they are introduced or changed. Approved:
`name` on `open` for display names (as read by the refried Fava extension and
used on commodities in Beancount's example; it is to replace `display-name`),
transaction `time` (quoted local `"HH:MM"` or
`"HH:MM:SS"`, as written by beangulp and sorted by autobean-format; it is to
replace `timestamp`), and Fava `custom "budget"` directives. `txncount-active`
remains an approved visibility preference: it does not mean accounting closure.
New transactions have no persistent ID metadata. A source position identifies
an entry only within its captured source baseline. Native shared links connect
an expense and its reimbursements. Legacy/user metadata is preserved, not
globally stripped or treated as an authoritative transaction identity.
Transfer edit forms reconstruct economic amounts from postings, not stored
fee-presentation metadata. The default fee category is a local device preference.
Budget directives are permitted, but budget/envelope calculation is not implemented.
Transaction times are wall-clock values, not instants: no offset is persisted
and changing display/default timezone must not convert saved transaction times.

Stage 2's retained fixture parser action runs off the UI loop, imports its
dependencies lazily so failures do not prevent storage diagnostics startup, and returns
explicit PASS/FAIL diagnostics. The official Beancount engine is development-only:
tests compare shared explicit-unit totals and lossless edits against its loader
and inventory calculations. Stage 3 reuses that parser for a deliberately bounded
single-file expense ledger. Parsing and validation run off the UI loop; existing
source text is retained verbatim outside targeted changes. New declarations and
transactions are appended; CRUD uses the parser's token positions to splice only
selected directives, preserving other bytes. Original parser round-trips must be
exact before an edit is allowed. Unsupported engine semantics block saving. There is no full booking
engine, include resolver or generic persistence framework. See the
[Beancount report](beancount-compatibility.md) and [MVP format](mvp-0.1.md).

Folder/language/theme/timezone preferences use Flet SharedPreferences on Android and a
local configuration file on desktop; Android's grant is separately retained by
SAF. Main and diagnostic folder choices use independent preference keys.
Operating currencies and active-state metadata are synchronized in the
Beancount journal. Edit dialogs capture the journal instance and source baseline:
poll/reload/folder changes cannot silently rebase an old draft. File contents are accessed directly through
the chosen adapter, never mirrored into app-private storage. Unsaved drafts and
desktop hash baselines are in memory only; Android additionally stores save hashes
and backup identifiers for crash review, never a second ledger. See the [report](android-storage-validation.md) for
backup safety, provider limitations, and device verification status. The same
adapters allow only `storage-test.beancount` and `txncount.beancount`, with separate
backup namespaces. Cloud transport still belongs to the user's external folder
tool; external-change notices are described below. No
analytics or authentication is included.

External synchronization is deliberately sequential: finish editing and let the
external tool finish syncing before opening another device. TxnCount does not
know whether synchronization is complete. Polling observes the main journal
and lists sibling names for a nonblocking possible-conflict notice; it never
reads sibling contents automatically or scans `.txncount-sync/`. No ancestry,
merge, acknowledgement, or auxiliary evidence inventory gates a save.
Stale form baselines still refuse saving, retain input, and require explicit
review/reload rather than automatic rebasing. Read-only source differences help
explain external changes but are never logged. Neither this model nor the final
hash comparison eliminates the race with an external writer after that check.
See [synchronization guarantees](synchronization.md).

Backup retention keeps 20 recent snapshots plus daily/monthly tiers. The
storage session finalizes a timestamped `pending` backup only after verified
journal write/read-back; unfinished and legacy backups are never eligible for
deletion. Adapters list, finalize and delete only their own backup namespace,
using SAF document operations on Android. Cleanup failure is a post-save warning,
not a failed financial transaction. The automatic-cleanup preference is local
to each device. See [retention and recovery](crud-fava.md#backup-retention).

Desktop writes exclusively stage a same-directory temporary file, preserve file
permissions, flush/fsync it, recheck the supplied baseline hash, then commit with
`os.replace()`. Parent-directory fsync follows on supported POSIX filesystems;
Windows lacks that step. Explicit write errors distinguish pre-commit,
post-replacement and uncertain outcomes. Shared read-back, backup finalization
and retention remain unchanged. Android SAF retains its direct non-atomic write,
but `RecoveryFolder` adds a verified save intent before writing and completion
after shared verification/maintenance. `Journal.load` blocks unresolved states
before publishing balances or allowing edits. Explicit recovery acceptance
validates and snapshots the reviewed journal without rewriting it. The native
bridge serializes operations across service instances so timeout/cancellation
does not let inspection overtake still-running I/O in the same Flutter process.
Neither adapter provides locking or compare-and-swap. See
[write guarantees](crud-fava.md#desktop-atomic-replacement) and
[Android recovery](android-storage-validation.md#current-android-save-recovery).

The CRUD milestone intentionally remains single-file at the user's direction;
no native SAF changes, migration, or multi-file transactions were introduced.
Fava and the official engine serve only as desktop test oracles. See the
[CRUD guide](crud-fava.md) for guarded expense editing and deletion rules.

Incoming transactions use the same explicit two-posting model and guarded
transaction edit/delete pipeline. Income credits user-managed `Income:`
categories; reimbursements credit `Expenses:` categories; balance-only receipts
credit `Equity:Balance-Adjustments`, separate from opening balances. No receivable
or automatic reimbursement allocation is maintained. Optional
native shared links are informational references, not foreign-key
constraint: editing/deleting the original never cascades into a receipt.
`Ledger.period_totals` uses inclusive accounting dates and separate per-currency
income/net-expense sums; reimbursements affect their receipt period and equity
does not enter these totals. The Reports screen builds category breakdowns from
those same validated postings, retaining transfer fees while excluding asset,
liability and equity movements. Each currency remains separate; no conversion
rate is inferred. See the [reporting guide](reporting.md).

One **Add transaction** action opens the same scrollable universal form used
for editing. Description is the first field. A compact **− / + / ↔** selector
sits beside the unsigned positive amount, with a localized debit/credit/transfer
explanation below. Minus and plus show one account and an appropriate category;
only transfer mode shows required source and destination accounts.
Dropdowns stretch to the same form width, including Advanced controls.
Account labels explicitly identify debit/source and credit/destination roles.
The selected button alone determines the ordinary operation; a hidden destination
draft never changes expense/income semantics. Mode changes do not replace amounts.
Transfers always send from the first account to the second; exchange fields are
source debit and destination credit. Account selectors determine currencies; the
existing transfer builder still owns fee and accounting validation.

Special receipts (reimbursement and balance adjustment) are explicit contextual
choices under collapsed Advanced for positive, non-transfer transactions. They
never become ordinary income implicitly. Hidden fee/reference/category values
do not enter unrelated saves. Category selections and common draft fields
survive sign/account/special-receipt changes. Advanced also holds fee settings,
payee and time, with saved-value summaries when editing. `DateTimeFields` offers
a compact date row and a separate time-control group using the same state,
pickers and wall-clock preservation logic.

Failed saves retain the form. Each dialog captures its journal and source
baseline, including new expenses. As explicitly approved, editing keeps the
accounting kind fixed: the mode selector is disabled, and saved-kind checks also
protect submission. Transfers can change both accounts and amounts to reverse
direction; this does not turn them into another kind.
Opening balances still use their guarded account-opening editor. Split and
unrecognized externally authored entries retain full read-only posting details; no new
two-posting restriction is imposed on loading. Same-currency transfers extend
this form and use two account postings plus an optional expense-fee posting.
Standard `Liabilities:` accounts use the existing account model, with credit
(negative) debt balances and positive repayment postings. Opening amount owed
is entered as a positive number and stored as a negative liability posting.
Cross-currency transfers extend the same posting model with an optional exact
total price (`@@`) or unit price (`@`) and currency. A bounded exchange shape
validates Beancount weights: one negative priced principal, one positive
destination, an optional expense fee and, for source-currency fees, a separate
unpriced debit from that same account. A fee-free unit-price annotation on the
positive destination is also supported. Balances sum posting units while
transaction validation uses the annotation's official Beancount weight. Existing
`@@` transfer serialization is unchanged. Transactions containing `@` remain
read-only in TxnCount's transaction editor/deleter, while unrelated lossless
edits preserve the original source. Costs/lots, inferred postings and other
unsupported constructs are rejected rather than approximated. No booking engine
or new runtime dependency is added. Loan schedules remain out of scope.

For exchanges the shared form takes actual total debited and net credited
amounts plus an optional source/destination-currency fee. These amounts are
never recalculated from a rate. Source fees reduce the converted principal;
destination fees increase its total price. A shared Decimal calculation derives
the principal, gross received amount and rate for the preview and builder.
Exchange edits reconstruct these from stored postings, including both amount
fields and fee currency; the same guarded splicing and deletion pipeline is used.
See [the accounting examples and precision policy](crud-fava.md#cross-currency-transfers).

Negative asset balances, including historical ones, now produce visible warnings
instead of blocking loading or saving, as explicitly approved for overdrafts and
backdated operations. This applies consistently to all operations; financial
validation (balanced explicit amounts, declarations, currency and dates)
remains strict. Liability credit balances are normal and do not produce an
insufficient-funds warning.

The default transfer fee category is a local device preference.
Without it, `Expenses:Bank-Fees` is the default and is declared only when first
used with a nonzero fee, within the same validated save. Editing derives amounts
from postings with canonical added-fee presentation, preserving unrelated
annotations rather than persisting a fee-presentation mode. A removed fee posting
with metadata or an inline comment is retained at zero to preserve annotations.

The compact UI keeps one movement amount in ordinary transaction rows and
opens details/editors through icon actions. The shared date/time control
shows one date/time group. It retains the original accounting date and exact
wall-clock time until the date or time is edited; an edit synchronizes both from the
visible date/time. Display dates use explicit
English/Ukrainian/Russian month names, never the Python host's process locale.
Manual formats and picker locales follow app language. Transaction times have
no UTC offset; a timezone preference cannot move existing transactions.
Optional payee fields are collapsed and blank for new expenses; saved payees
survive unrelated edits. Description validation allows line breaks within
parser-generated quoted journal strings.
Journal display currencies live under Advanced settings and account creation
appends a missing currency in the same validated, backed-up save as its declaration.

`timekeeping` uses standard-library `zoneinfo` plus bundled pure-Python `tzdata`
for Android/Windows. The existing bridge exposes one independent `timezone`
method through `flutter_timezone` 5.1.1; SAF operations are unchanged. Android
system timezone is obtained from the Flutter client, not guessed from embedded
Python. Desktop system mode uses OS date-specific timezone rules. A journal's
injected `today` callable keeps UI validation, parsing and balance checks on the
same device/selected-zone date, including around midnight. System timezone
failures are explicit; an explicit named zone permits manual entry, while date
pickers require a known client timezone to decode Flet's UTC date transport.
Picker dates are sent at client-local noon and returned instants are converted
back to the client timezone, independently of the app's chosen display zone.
Ambiguous/nonexistent DST wall times are rejected, not guessed.