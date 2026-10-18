# Architecture

The entry point is [src/main.py](../src/main.py). Importing it does not launch UI.
It loads central settings, configures logging and exception hooks, and calls the
imperative Flet application. No declarative component framework.

```text
src/main.py                 composition / startup
src/app/config.py          validated environment configuration
src/app/logging_config.py  logging policy and exception reporting
src/app/application.py     page lifecycle and event boundary
src/app/ui/finance.py      MVP screens, forms, polling and lifecycle
src/app/ui/home.py         home composition and preserved storage PoC
src/app/localization.py    English/Ukrainian/Russian catalog and locale formatting
src/app/timekeeping.py     named/system timezone conversion and DST validation
src/app/ui/datetime_fields.py shared manual/date-picker/time-picker controls
src/app/ui/beancount.py    separate bundled-fixture action; lazy parser import
src/app/beancount_poc.py   pure-Python syntax/editing/quantity experiment, not a full engine
src/app/fixtures/beancount/ synthetic bundled books, never user storage
src/app/domain/storage.py  SHA-256 and conflict error, no Flet imports
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
calls. One width classifier selects bottom navigation for compact displays and
a navigation rail for medium/expanded displays. The same screens and form state
are retained through resizing. Translation resources are separate from controls;
language/theme choices are saved locally. Domain functions use Python types and return results or raise meaningful
exceptions. Services decide retry, timeout, cancellation, and user-facing failure
semantics. Adapters expose expected errors without hiding unexpected failures.

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

Stage 2's retained fixture parser action runs off the UI loop, imports its
dependencies lazily so failures do not prevent Storage PoC startup, and returns
explicit PASS/FAIL diagnostics. The official Beancount engine is development-only:
tests compare shared explicit-unit totals and lossless edits against its loader
and inventory calculations. Stage 3 reuses that parser for a deliberately bounded
single-file expense ledger. Parsing and validation run off the UI loop; existing
source text is retained verbatim outside targeted changes. New declarations and
transactions are appended; CRUD uses the parser's token positions to splice only
selected directives, preserving other bytes. Original parser round-trips must be
exact before an edit is allowed. Unsupported engine semantics block saving. There is no full booking
engine, include resolver or generic persistence framework. See the
[Beancount report](poc-beancount.md) and [MVP format](mvp-0.1.md).

Folder/language/theme/timezone/default-payee preferences use Flet SharedPreferences on Android and a
local configuration file on desktop; Android's grant is separately retained by
SAF. Operating currencies and active-state metadata are synchronized in the
Beancount journal. Edit dialogs capture the journal instance and source baseline:
poll/reload/folder changes cannot silently rebase an old draft. File contents are accessed directly through
the chosen adapter, never mirrored into app-private storage. Unsaved drafts and
hash baselines are in memory only. See the [report](poc-android-storage.md) for
backup safety, provider limitations, and device verification status. The same
adapters allow only `poc-test.beancount` and `txncount.beancount`, with separate
backup namespaces. No synchronization implementation, analytics or authentication
is included; synchronization belongs to the user's external folder tool.

The CRUD milestone intentionally remains single-file at the user's direction;
no native SAF changes, migration, or multi-file transactions were introduced.
Fava and the official engine serve only as desktop test oracles. See the
[CRUD guide](crud-fava.md) for guarded expense editing and deletion rules.

The compact UI keeps one movement amount in ordinary transaction rows and
opens details/editors through icon actions. The shared date/time control
separates accounting date from timestamp date/time. Display dates use explicit
English/Ukrainian/Russian month names, never the Python host's process locale.
Manual formats and picker locales follow app language. Unchanged timestamps
retain their original offset and subsecond precision.

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