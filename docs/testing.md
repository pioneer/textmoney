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

## Form and report layout

At desktop and narrow viewport widths, compare the rendered control boundaries,
not only Python `width` values. In Flet 1.0.1, a standalone dropdown can keep its
intrinsic width even in a stretched column; `expand=True` inside a row gives it
the available horizontal space.

- Add account and Add category: dropdowns align with the input fields, including
  the Parent dropdown in nested category creation.
- Add/Edit budget (also Change from date and Stop): Category and Interval
  dropdowns align with the other inputs.
- Add/Edit operation: Category and Date inputs have equal widths, with their
  create-category and calendar buttons aligned.
- Edit account/category: the selectable technical code is bold; all Edit account
  fields and Edit category's name input fill the form width. Account date inputs
  leave only the calendar button's space at the right.
- Transaction edit/delete and read-only/opening details: the selectable
  transaction code has an explicit localized label, italic styling and a
  slightly smaller font. New transactions do not display a code.
- Delete budget: the budget name is bold and
  retains its technical-code tooltip. Accounting / Budgets definition headings
  and Report budget names are bold, including nested budgets and names that
  fall back to technical codes.
- Reports / Overview: Period and each currency summary rectangle fill the
  available report content width, respecting navigation and page padding.
  Custom date ranges, wrapped amount metrics, and bottom scroll clearance remain
  usable on narrow displays.

## Desktop popup keyboard shortcuts

Escape must behave like the active popup's **Cancel** (or **Keep editing**)
button, without saving. Enter must invoke the primary action through the same
validation and save handler as clicking it; a single-action popup uses Close/Keep
editing. Multiline inputs use Shift+Enter for newlines. Automated tests cover
real Flet keyboard/click events, valid/invalid and stale drafts, nested popups,
disabled actions/inputs, repeat protection, native pickers and callback failures.
Mobile shortcuts remain unchanged.

With a disposable journal on a desktop:

1. Open new/edit/delete transaction, account, category and budget popups. Focus
   an input and each action button, then use Escape and compare with **Cancel**;
   no journal write or backup should be created.
   For accounts/categories with history (including opening balances) or categories
   with children, Delete must explain why it is blocked, not open Edit implicitly.
   The explicit Edit action (or Enter) opens the editor. Check both Cancel and
   Escape on the explanation; unused items must retain their deletion confirmation.
2. From a transaction, open **Create category**, or trigger a conflict. Escape
   closes only that popup and keeps the transaction draft intact. After it
   closes, another Escape cancels the parent form.
3. Open calendar/clock pickers, dropdown menus, month selection and searchable
   budget trees. Escape must close only the active picker/menu, not its parent.
4. Enter must save valid new/edit transaction, account, category and budget
   forms exactly once. Invalid amounts, names, dates, times, selections and
   stale sources must follow click validation and preserve unsaved input.
   Check Enter in nested date/time inputs, keypad Enter, and multiline
   descriptions; Shift+Enter must add a newline without saving.
5. Test Enter in delete/cleanup/recovery confirmations and single-action notices.
   A disabled or hidden confirm action must never run. Check report month
   selection with invalid/valid years and searchable-tree keyboard selection.
6. Focus action buttons and dropdowns: Enter must activate the focused control,
   not also submit its parent. A nested popup's Enter must never save a parent.
7. While a save/cleanup disables **Cancel**, Escape must also remain disabled;
   repeated Enter must not submit again while an operation is pending.
   Check Linux and Windows keyboard behavior; headless tests do not certify
   native input delivery.

## AppImage native GTK theme

The Linux Bash hook tests execute the generated hook with controlled GNOME
settings, including custom themes with `color-scheme=default`, Adwaita, dark themes,
explicit variants, both environment overrides, unavailable/failing `gsettings`,
missing color-scheme support, and non-GNOME sessions. They check host data paths
and preserved bundled module setup. Packaging tests check that the replacement
happens after linuxdeploy and before appimagetool; unknown upstream hook layouts
are rejected. Hook execution tests skip on Windows or when Bash is unavailable.

Run `uv run inv build-linux-appimage`, `uv run inv validate-linux-appimage` and
`uv run inv smoke-linux-appimage` to check the actual package. Validation extracts
the artifact and checks its maintained GTK policy; the Xvfb smoke test only
checks startup, not visual theme correctness.

For the GTK integration fix, additional Linux/Xvfb probes loaded the actual
extracted AppImage's GTK library and compared its resolved CSS and headerbar
foreground with host GTK. All 12 scenarios matched: real GNOME settings,
custom-theme/default, Adwaita/default, a custom theme's dark variant, a dark theme
name, both overrides, no `gsettings`, a missing theme, and custom themes in data-home,
legacy-home and data-directory locations. The custom theme probes exercised CSS
imports and checked a distinctive resolved widget color, not just `GTK_THEME`.
The custom theme's CSS differed from Adwaita and its own dark variant; missing
themes loaded the built-in Adwaita CSS. These are local runtime results, not a
cross-distribution visual certification.

Before release, compare the native title bar in `uv run inv run` and the rebuilt
AppImage on the same real desktop:

1. With no theme environment overrides, check GNOME using an installed custom
   GTK3 theme and `color-scheme=default`, then Adwaita.
2. Check a dark GTK theme and `prefer-dark`, restarting both launches after each
   settings change. Do not equate `default` with a forced light theme.
3. Set `APPIMAGE_GTK_THEME` and `GTK_THEME` independently, then together with
   different installed themes. The AppImage-specific override must win.
4. On a non-GNOME desktop or a system without `gsettings`, check native GTK
   configuration and safe fallback. Request a nonexistent theme and verify the
   app still launches with standard GTK styling.
5. Confirm the application still uses native window decorations, its Material
   controls are unchanged, and no manual override is needed for system themes.

Real-desktop visual comparison remains manual; an environment dump, successful
compilation or process surviving Xvfb is not sufficient proof by itself.

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
7. In **Accounting → Budgets**, use **View in reports** on a past, future and
   explicit-zero definition, including a nested category with a budgeted parent.
   Check **Reports → Budgets** opens at its start month and currency, clears
   hiding filters, expands parents, scrolls to the category and highlights it.
   Repeat for another definition and then the same one; only the chosen row is
   highlighted. Changing the month/filters clears the highlight. A delayed layout
   event after navigating elsewhere must not scroll the new screen.

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
Home and the dedicated Transactions view reserve 96px of scrollable space after
their content for the floating Add button. With a long list, scroll fully to the bottom
at phone and desktop widths and check that the final amount and Edit/Delete
actions sit above the button and remain clickable. Recheck Home after navigation,
resizing and label refreshes; the clearance must not disappear or accumulate.
Empty lists still show their
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
   shift through device-timezone transport. Expand **Advanced** in new and edited
   transactions at phone and desktop widths: the floating time label must have
   clear space below the payee field, whether the payee is blank or filled.
   Switch expense/income/transfer and verify the spacing and unsaved input remain.
8. Recheck language/theme persistence, rotation and compact/wide navigation.
   On Android, exercise retained grants, permission failures and the
   [interrupted-save checklist](android-storage-validation.md#limits-and-verification).
   On Windows, perform actual folder selection and CRUD; CI installer/startup
   smoke is not a substitute for this interactive workflow.

## Sequential-file workflow regression checklist

Use only a disposable synthetic journal:

1. Create accounts/categories; confirm `name` labels and local `time` metadata,
   no new `id`/`timestamp`/`textmoney-kind` fields, and no `.textmoney-sync/`.
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

Before a two-device test, verify that each external client provides a
[fully accessible local copy](synchronization.md#local-folders-and-sync-clients):
disconnect the network and confirm folder access, journal reads/writes, backups
and Android recovery operations using disposable data. A cloud-only picker entry
or an app's offline document cache is not sufficient.

The two-device operating rule remains manual: wait for the external sync tool
before editing elsewhere. Unit tests cannot certify that tool's completion.