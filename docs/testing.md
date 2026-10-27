# Testing

`uv run inv check` validates and compiles gettext catalogs, then runs Ruff
format-check, Ruff lint, Pyright and pytest. It stops at the first failure and
preserves command failure status. `uv run inv format` formats Python source;
catalog compilation writes generated MO files, not maintained PO translations.
Run individual Invoke tasks to iterate. Pass focused pytest selectors through
`PYTEST_ADDOPTS`, using your shell's environment-variable syntax, for example on
POSIX shells:

```sh
PYTEST_ADDOPTS='-q -k budget' uv run inv test
```

Tests do not open a window or build Flutter. Current coverage checks
settings parsing, pure readiness decisions, side-effect-free entry point,
imperative view construction, Flet event failure tracebacks, sanitized logging,
Invoke host selection/command contracts, and mobile debug marker cleanup. Flet
event tests use pinned internals to verify the actual failure path; revisit them
on a Flet upgrade.

Stage 1 storage passed user-confirmed physical-phone testing; its focused
conflict/backup/failure tests remain intact. Stage 2 adds synthetic Beancount
fixtures and official Beancount 3 desktop comparisons: parsing, typed metadata,
includes, balances/period totals, lossless edits and temporary-file append,
unsupported/unknown syntax, and Android extraction configuration. All accounting
numbers use Decimal; unit summation is deliberately not a full validator/engine.
`uv run inv beancount-diagnostics` runs the same bounded check as the phone action.
`uv run inv probe-beancount` prints the library capability matrix;
`uv run inv check-beancount-apk` verifies grammar extraction and smoke-tests the
actual APK payload on host CPython. Neither replaces Android device execution.

For new behavior, prefer small domain/service tests with fakes for external I/O.
Add success and meaningful failure cases, cancellation/timeouts when relevant,
and verify no sensitive values appear in logs. Keep UI tests about state and
behavior, not incidental control ordering. Do not mock the decision under test.

Before publishing, also run a real target build and launch it. Unit tests cannot
prove native plugin availability, platform permissions, icon appearance, signing,
or browser/Pyodide compatibility. Check desktop/mobile viewport sizes and inspect
the packaged output, not just a development server. Manual CI build artifacts are
build smoke tests, not store certification.

Browser previews help inspect layout, not local journal access:
`FilePicker.get_directory_path()` is unsupported in web mode. Static/Pyodide
packaging is not a supported financial-storage target; see [Web deployment](deployment.md).
Apple build tasks do not establish device-tested iOS/macOS support.

## Category budget regression checklist

The [budget guide](budgets.md#verification) describes automated parity and failure
coverage. `uv run inv check` includes exact comparisons with pinned Fava for
daily allocation, partial periods, historical definitions, all supported
intervals, hierarchy, currencies and Decimal/display precision. UI/storage
regressions cover management, reports, defaults and retained conflict drafts.

With a disposable synthetic journal, check:

1. **Accounting → Budgets** has **+ Create budget**, pencil and trash actions.
   A new monthly definition defaults to the first of the current month; a manually
   chosen date and an existing definition's date are preserved.
2. **Reports → Budgets** shows the selected calendar month, only explicitly
   budgeted categories and explicit zero budgets, with no automatic parent rows.
   Check friendly names, quick edit/delete, contributing transactions and the
   **Excluding subcategories** figures.
3. Use month navigation/picker, searchable category/account trees, currency
   selection, **Only overspent**, removable chips and reset. The account-filter
   warning must remain visible; currency filtering must not convert amounts.
4. Change/discontinue a budget from a date and recheck earlier/later months and
   each currency independently. Edit/delete historical definitions deliberately;
   verify recalculation and the backup, not automatic rollover.
5. Change the journal externally while a budget form is open. Saving must refuse
   the stale form and retain all input. Reopen after review; never silently rebase.
6. Check tabs, dialogs, scrolling and navigation at 320, 390 and 1366 pixels,
   in maintained languages and both themes. Native Android SAF and full
   interactive Windows budgeting still need their own platform runs.

## Transaction list density

Home recent activity and the Transactions screen group transactions under one
localized accounting-date heading per day, newest first. Dates are not repeated
inside rows. Home still shows only the five most recent transactions, even if
that cuts through a date group. Date headings are centered above their list and
use 14px text. The 1970-01-01 group uses a muted, localized "Initial" placeholder;
other dates retain their normal labels. This is display-only: journal dates and
edit-form dates are unchanged. Check same-day ordering and language changes.
Both lists share compact rows: a 14px single-line summary above 10px account movement,
13px amount, 1px line spacing, and 2px top/bottom padding. Split-posting summaries
remain 14px when there is no account movement. Action icons are 20px; their default touch targets
are unchanged. Check both lists at phone and desktop widths, including long
account/category names, large amounts, blank descriptions, split postings and
exchange fees. Amounts must remain readable; descriptions may use an ellipsis.
Only the dedicated Transactions list in the expanded layout (window width at
least 1024px) has thin, theme-aware bottom separators spanning the details, amount
and action buttons. Home recent activity never has separators, even on desktop.
Narrower layouts have no separators. Resize across 1023/1024px in both directions
and switch between Home and Transactions; date headings must never be underlined.
Check light and dark themes.
The dedicated Transactions view reserves 96px of scrollable space after its
list for the floating Add button. With a long list, scroll fully to the bottom
at phone and desktop widths and check that the final amount and Edit/Delete
actions sit above the button and remain clickable. Empty lists still show their
normal message. Transfers retain exchange/fee details above their account movement;
blank descriptions do not add an empty summary line.

## Reports scrolling clearance

Reports → Overview also reserves 96px of scrollable space after the category breakdowns,
matching Transactions. With a long report, scroll fully to the bottom at phone
and desktop widths and confirm the last category and amount sit above the
floating Add button. Recheck after changing the period and navigating away and
back; empty reports retain their normal message. Recheck the separate Budgets
tab with a long list and its own expanded transaction details.

## Accounting and Settings scrolling clearance

Accounting reserves 96px of scrollable space after each of Accounts, Categories
and Budgets. Settings reserves the same space on medium and expanded layouts,
where the floating Add button is present, including with diagnostics expanded.
Compact Settings has no Add button and needs no extra clearance. Check the final
controls at maximum scroll, then resize across 599/600px and navigate between
screens; padding must update without accumulating or resetting the selected tab.

## Adaptive Settings navigation

At compact widths (up to 599px), verify the bottom bar has Home, Transactions,
Accounting and Reports only. The top-right gear opens Settings; its back arrow
returns to the previous screen, preserving in-memory form/filter state. Settings
hides the bottom bar and transaction action on compact screens. At 600px and
wider, Settings remains in the left rail and the header gear/back arrow is hidden.
Resize while Settings is open in both directions and check selection, return
navigation and localized tooltips without reloading the journal.

## Transaction and device checklist

Use a separate synthetic folder or a disposable copy, never the only copy of
financial data. These are checks to perform, not claims of completed device tests.

1. Set journal display currencies under **Settings → Advanced**; restart and
   confirm persistence. Create an account with another currency and check that
   it is appended without losing existing choices or converting balances.
2. Create and rename accounts/categories. Check declaration/opening effective
   dates and guarded deletion/deactivation. Lower an opening balance enough to
   make historical asset balances negative: a valid save must remain allowed
   with a warning, not an insufficient-funds rejection.
3. Edit a supported hand-written transaction with no ID. Check amounts,
   dates/times, comments, tags, native links, unrelated metadata and the previous
   backup. Confirm new entries use `name`/`time`, not legacy generated metadata.
4. Cancel a delete, then confirm one. It must physically remove the selected
   transaction, not add a reversal. Split/unrecognized and `@` transactions must
   remain read-only while their postings are available for inspection.
5. Add a transaction with a blank payee, then one with a payee under Advanced.
   Edit its amount while Advanced is collapsed; the payee must remain.
   Exercise multiline descriptions, quotes and backslashes; failure/conflict
   must retain all draft fields.
6. Try System, `Europe/Kyiv` and `UTC` default clock settings and restart.
   Existing journal times and bytes must not change. Manually entered DST-gap
   and overlap wall times must be accepted without selecting an offset.
7. Exercise manual date/time entry and calendar/clock pickers in all languages,
   near midnight and after phone timezone changes. A selected date must not
   shift through device-timezone transport.
8. Recheck language/theme persistence, rotation and compact/wide navigation.
   On Android, exercise retained grants, permission failures and the
   [interrupted-save checklist](android-storage-validation.md#limits-and-verification).
   On Windows, perform actual folder selection and CRUD; CI installer/startup
   smoke is not a substitute for this interactive workflow.

## Sequential-file workflow regression checklist

Use only a disposable synthetic journal:

1. Create accounts/categories; confirm `name` labels and local `time` metadata,
   no new `id`/`timestamp`/`txncount-kind` fields, and no `.txncount-sync/`.
2. Add a hand-written supported transaction without an ID. Edit/delete it from
   the UI, checking unrelated comments, tags and metadata remain unchanged.
3. Open a transaction form, change the file externally, then save. Saving must
   refuse the stale baseline and retain every field; reload must not silently
   turn that form into an edit of a different transaction.
4. Review a valid external edit and the read-only diff. Try malformed syntax
   and a valid but unsupported directive; check line/reason diagnostics and
   that stale balances are not presented as current.
5. Add a recognizable conflicted-copy filename, then damaged legacy evidence.
   The filename notice must be nonblocking; legacy evidence must not gate saves.
   Neither sibling nor evidence contents should be read or mutated.
6. Link a partial reimbursement; inspect native shared links on both entries.
   Recheck period totals, transfer fees, FX and loan repayments.
7. Change the default timezone. Saved transaction times must remain unchanged;
   only the default clock for new entries changes. Check native date pickers
   on Android for timezone-induced date shifts.
8. Restart after changing the default fee category. Verify the local preference
   survives without adding journal metadata.
9. On Android, exercise interrupted-save review, retained provider grants and
   permission failures. Confirm backup/read-back failures still block unsafe
   retries. This requires physical-device testing, not just an APK build.

The two-device operating rule remains manual: wait for the external sync tool
before editing elsewhere. Unit tests cannot certify that tool's completion.