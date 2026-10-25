# Testing

`uv run inv check` runs, in order, Ruff format-check, Ruff lint, Pyright, and
pytest. It stops at the first failure and preserves command failure status.
`uv run inv format` is the only modifying quality task. Run individual tasks to
iterate; use pytest's `-k` through `uv run pytest -k NAME` for a one-off focused
diagnostic, not a second maintained task interface.

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

Reports also reserves 96px of scrollable space after the category breakdowns,
matching Transactions. With a long report, scroll fully to the bottom at phone
and desktop widths and confirm the last category and amount sit above the
floating Add button. Recheck after changing the period and navigating away and
back; empty reports retain their normal message.

## Adaptive Settings navigation

At compact widths (up to 599px), verify the bottom bar has Home, Transactions,
Accounting and Reports only. The top-right gear opens Settings; its back arrow
returns to the previous screen, preserving in-memory form/filter state. Settings
hides the bottom bar and transaction action on compact screens. At 600px and
wider, Settings remains in the left rail and the header gear/back arrow is hidden.
Resize while Settings is open in both directions and check selection, return
navigation and localized tooltips without reloading the journal.

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