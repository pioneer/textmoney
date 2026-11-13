# Category budgets

Manage limits and monitor spending on the single **Budgets** screen, opened
directly from primary navigation.
Budgets has its own selected item in both compact bottom navigation and the
medium/wide sidebar. Reports has no Budgets shortcut;
there is no back-to-Reports action.
The selected month, filters and open budget drafts survive navigation, saving
and layout changes.
Accounts and Categories have separate screens. Budgets live exclusively in the selected
`textmoney.beancount` journal:

```beancount
2026-01-01 custom "budget" Expenses:Food "monthly" 10000 UAH
2026-02-01 custom "budget" Expenses:Food "monthly" 12000 UAH
```

TextMoney retains its existing single-file explicit-posting accounting model.
Fava **1.30.16** is the pinned development-time numerical reference, not a runtime
dependency. No alternate engine, separate budget store, proprietary budget
metadata, artificial transactions or license change is involved. Adapted Fava
MIT algorithms and their complete license notice are shipped in
[`app/notices/fava.txt`](../src/app/notices/fava.txt).

## Management and history

- Use **+ Create budget** above the cards for new definitions, even when the
  selected month or filters show no cards.
- Each card shows the latest applicable limit's amount, currency, interval and
  effective date, separately for each displayed directly budgeted currency.
  Monthly allocation figures still include any changes within that month.
- Cards offer **Edit**, **Delete**, **Change from a date** and
  **Discontinue from a date**. Change and discontinue append a dated definition
  (zero for discontinuation), preserving earlier records. Saving does not reset
  the selected month or filters.
- Open **Budget history**, next to **Create budget**, for a dialog containing all
  dated records, including earlier limits superseded within the same month,
  future and stopped limits, with the same editing actions. Both buttons remain
  available when no cards are shown. Closing history preserves the month and filters.
  The two buttons have equal widths and stay right-aligned when they wrap on mobile.
  Their 240-by-48-pixel size and toolbar spacing match Create on Accounts and Categories.
- Each history definition has **View in reports**. It opens the cards at the
  current month when the definition applies during any part of that month;
  otherwise it opens the month the definition starts. It closes history, selects its currency and clears category
  and overspending filters. Budgeted child rows are always visible with indentation;
  the report scrolls to it and highlights it. Changing the month or filters clears
  the highlight. Reports remain monthly category totals, not a separate report
  for each definition: other definitions effective during that month contribute
  normally.
- Budget lists, reports and dialogs show category display names from `name`
  metadata on `open`, with the raw account code in a tooltip. Without a display
  name, the account code remains the fallback. Journal directives still use the
  original account codes.
- Create a definition for an existing category, with amount, Beancount commodity
  code, interval and effective date. Future budget definitions are allowed without
  enabling future accounting transactions. New monthly budgets default to the
  first day of the current month in the app's selected timezone, including
  **Change from a date** and **Discontinue from a date**. Other intervals default
  to today. Switching intervals adjusts only the untouched suggested date;
  manually chosen dates and dates of historical definitions being edited are
  preserved.
- **Change from a date** appends a definition; it does not overwrite past budgets.
  Each currency changes independently. Adding an EUR budget does not cancel USD.
- **Discontinue from a date** appends zero in that currency. Previously allocated
  days remain in reports. There is no automatic expiration, reset or rollover.
- **Edit** intentionally changes a historical definition; the dialog warns that
  historical reports will recalculate.
- **Delete** removes that definition only. An earlier definition can become active
  again, so deletion is not the same as discontinuation.

The list explains how to change future budgets without changing past months,
and that unused amounts are not added to the next month. Currency-specific
behavior is explained in the create/change dialog; the stop dialog explains
that earlier dates and other currencies are unaffected.

Saving uses the existing validated, lossless splice/append, verified backup and
hash-conflict pipeline. Comments and unrelated metadata/directives are preserved.
Stale forms are rejected without discarding input, including after an external
reload. Budget changes reload through the normal journal polling mechanism.
No stronger atomic-write guarantee is introduced.
Unchanged background polls leave rendered controls intact. When recalculating
the same report after a journal change, existing cards stay visible until their
replacements are ready. Changing the month or filters shows a loading indicator
instead of the previous selection's figures. Calculation failures clear old
results and report an error.

## Allocation and reports

The report defaults to the current calendar month in the app's selected timezone
and preserves manually selected months during navigation and refreshes.
All calculation ranges are half-open
internally: the first day of that month through, but not including, the first day
of the next month.

For **every day**, select the last effective definition per account and currency,
divide by the calendar length of its interval and add the resulting Decimal.
Definitions are date-ordered with stable same-date source precedence. All Fava
intervals and aliases are read, case-insensitively: day/daily, week/weekly,
month/monthly, quarter/quarterly, year/yearly.

The reference context is 28-digit, half-even Decimal arithmetic. Daily addition
order is preserved, not replaced with nominal amounts or a single multiplication.
For example, January's monthly `100 USD` allocates
`99.99999999999999999999999992 USD`. Currency precision determines its displayed
value. Budget, spent and remaining amounts do not expose the unrounded calculation
values as hover/long-press hints; the underlying arithmetic is unchanged.

The monthly overview shows **Budget**, **Spent**, **Remaining**, percentage used
and expense overspending, separately for each currency. Budget rows do not expand
or collapse. The transactions icon in the card's action row opens contributing
transactions; nested budgeted categories remain visible with indentation. Figures include
subcategories; there is no separate **Excluding subcategories** subitem.
There is no report-mode selector or reporting-frequency selector. Definition
frequencies are unchanged: daily, weekly, monthly, quarterly and yearly budgets
are all allocated into the displayed month.

The Budgets report shows only categories with their own effective budget in the
selected period. Automatic parent totals are hidden: budgeting Food does not
create an Expenses row. A parent appears only when it has its own explicit
budget. Budgeted descendants remain visible even if their parents are hidden.
Explicit zero budgets remain visible. This is a presentation filter only:
Fava-compatible actuals, allocation and hierarchy totals are unchanged. Manage
all definitions, including future ones, through the **Budget history** dialog.
Edit, Delete, Change from date and Stop from date sit beside the transactions
button, right-aligned alongside the category title. The action area wraps on
narrow screens. Actions target the displayed dated limit in its
own currency, labeled when several currencies are shown.
Report cards use a 16-pixel content inset on every side, with an additional
16-pixel left indent per visible hierarchy level. Within that area the progress
bar stays full-width; the category heading and figures have a further 14-pixel
inset, matching the centered 20-pixel action icons in 48-pixel click/touch targets.
When a currency filter is selected, only that currency's limit actions appear.

Category trees use real account components for actuals. Fava budget aggregation
and interval chart actuals use literal `startswith` matching: a query for
`Expenses:Food` can include `Expenses:FoodOther`. This behavior is preserved,
not silently “corrected.” Parent and child budgets are added, not overridden by
the most-specific budget. Missing budgets and explicit zero budgets remain
distinct.

Consumed percentage is a **TextMoney presentation ratio**, `actual / budget * 100`,
not a Fava-provided financial field. It is undefined for zero/missing budgets.
Only the visual bar is clamped; amounts and percentages are not capped.
Overspending does not block transactions.

## Home budget preview

Home calculates the current calendar month in the app's configured timezone,
independently of Budgets' selected month, filters and manual category order.
Only categories with their own effective budget in that month appear; the count
is categories, not dated definitions or currency lines.

- Wide layout: below **Your balances** in the left column, with **Recent
  transactions** at the top of the right column; up to five budget rows.
- Single column: balances, recent transactions, then budgets; up to three rows.
- The summary shows how many categories are overspent out of the month's total.
  Rows rank overspent categories first, then remaining categories by percentage
  used, highest first. Multi-currency categories use their highest applicable
  usage for ranking, while their displayed figures stay separate by currency.
  Categories with no defined percentage and no overspending come last.
- Each row has its category name, a thin progress bar and percentage used.
  It uses the same monthly allocation, actuals, precision and display rules
  as Budgets, separately labeled when multiple currencies are present. Zero
  limits retain the undefined-percentage message with no invented ratio or
  progress bar; a zero budget with positive expense spending is overspent.
  Unbudgeted currencies retain the missing-budget message. Missing and zero
  budgets remain distinct, and numeric percentages are not capped.
- If more categories exist, a localized **N more budgets** count is informational,
  not clickable. When none are overspent, a short status line is shown and
  within-limit rows still appear. With no current-month budgets, the section is hidden.
- Matching arrow-only buttons beside **Budgets** and **Recent transactions** have
  localized accessible labels and comfortable touch targets. The Budgets arrow
  opens the full screen at the current month with category/currency/overspending
  filters cleared; the transaction arrow opens Transactions.

**Settings → Show budgets on Home** is enabled by default, applies immediately
and persists locally. A persistence failure reports an error and restores the
previous setting rather than pretending it saved. The toggle does not remove
budgets or affect their calculations.

## Filters, currencies and settings

- Use month arrows, click the month heading to choose a month/year, or select
  **Current month** (using the app timezone). No Fava date notation is required.
- Filters are hidden initially. Use the filter icon, as in Operations, to show
  category, currency and **Only overspent**, plus active chips and reset.
  Collapsing the panel keeps its selections; the icon is highlighted while any
  filter is active. Month navigation and **Current month** remain visible.
  Current month and the filter toggle share a row beneath the month navigation;
  its heading and arrow targets match Reports.
- **Category** is an Operations-style dropdown with friendly names, assigned
  icons and hierarchy guides. Selecting a parent includes its descendants, not similarly named
  siblings. Only categories budgeted in this month and their ancestors appear
  in the selector; selecting an ancestor does not create an aggregate report row.
- Budgets has no Account filter; spending includes all accounts.
- **Only overspent** keeps expense rows whose remaining amount is negative in
  any displayed budget currency. Zero budgets with positive spending qualify;
  missing budgets do not. Income is not treated as overspending.
- Active category/currency selections have removable chips.
  **Reset filters** clears all selections and the overspending toggle, without
  changing the month. Changes apply immediately; there is no Apply button.
- Payee/description search is in **Contributing transactions**, not in the
  headline budget totals. It is case-insensitive and does not change those totals
  or any budget definition. The tag filter is removed; native journal tags are
  still parsed and preserved.
- Expense actuals come from signed expense postings, including splits, refunds
  and explicit exchange fees. Income, own-account transfers and balance
  adjustments are not reclassified as expenses.
- **Currency** filters displayed figures, not calculations. Choose **All currencies**
  or one currency present in the month's budget/spending. It is hidden when only
  one currency is relevant, unless a previous selection needs clearing. It never
  converts either budgets or spending, never sums unlike currencies, and never
  uses journal exchange rates. A currency selection also narrows contributing
  transactions to those with matching category postings; the whole source
  transaction remains available for context.
- Journal-observed most-common currency precision and commodity `precision`
  metadata control formatting. The journal's Fava `locale` setting is honored
  independently of app language. The frontend-compatible number conversion is
  confined to presentation; accounting calculations stay Decimal.
- Category reports retain signed accounting values, including income and refunds.

The domain retains Fava-compatible partial ranges, fiscal notation, reporting
intervals, cumulative/chart/income series, filters and price conversion for
reference tests; they are not alternate UI modes. Native journal prices are the
only conversion rates in those tests, never `@`/`@@` posting annotations.
The fiscal-year-end and income-inversion settings affect those reference series,
not the calendar-month overview. Default literal-prefix Fava filter semantics
remain unchanged; the UI opts into component-aware account selection.

Unsupported costs/lots, inferred postings, plugins, includes and other unsupported
accounting semantics still block saving as before. Budgeting does not pretend to
implement the whole Beancount engine.

## Verification

Budgeting was implemented alongside Android, Linux and Windows packaging.
The observations below distinguish earlier development
checks from device verification; published binaries are available from
[GitHub Releases](https://github.com/pioneer/textmoney/releases/latest).

Run all formatting, lint, type and regression checks:

```sh
uv run inv check
```

The Fava comparison suite uses the same synthetic journal in both applications.
It covers the original 20 scenarios: simple/multiple budgets, hierarchy and
overlaps, refunds, split postings, mid-month activation/replacement, history,
daily/weekly/quarterly/yearly allocation, leap years and 28/29/30/31-day months,
multiple currencies and missing rates, ranges/filters, cumulative reports,
income, zero/missing budgets and externally authored native definitions.
Additional assertions cover native price conversion, direct/subtree versus chart
prefix behavior, net-profit/inversion, fiscal ranges, same-date precedence,
retrospective posting changes and exact display precision.

The JavaScript formatting comparison checks Fava's `Number.toFixed`/Intl
presentation behavior when Node is available (including exact rounding edges).
It exercises no-locale and en/uk/ru/de/fr/hi settings; it is explicitly skipped
without Node, while Python/Fava reference comparisons still run.

UI/storage tests cover localized compact/wide management navigation and budget reporting,
budgeted-only report visibility, month selection/boundaries/leap days, searchable
tree selection and sibling isolation, unconverted multi-currency figures,
account-filter warnings, active-chip removal/reset, zero-budget overspending,
transaction-only search, monthly definition defaults, future definitions,
discontinuation, external reloads, polling control stability, backup/write
failures and retention of unsaved conflict input.

Implementation validation on Linux:

- `uv run inv check`: translations, formatting, Ruff, Pyright and regression
  tests, including exact Fava comparisons.
- Before the simplified monthly UI: `uv run inv build-android` built a
  development APK. Archive inspection confirmed
  Babel's locale data in the extracted packages and the MIT notice in the app;
  Fava, official Beancount and Beanquery runtime packages were absent.
- Before the simplified monthly UI:
  `uv run inv build-linux-appimage validate-linux-appimage smoke-linux-appimage`:
  built and validated the AppImage. A further 30-second Xvfb startup smoke passed.
- Browser smoke with an isolated synthetic journal exercised a saved native
  definition and its backup, monthly figures, direct/subtree overspending and
  transaction drilldown, including 390- and 320-pixel compact layouts.
- The simplified monthly UI was browser-tested in Ukrainian at 320, 390 and
  1,366 pixels, including month selection, searchable category branches,
  account selection and its persistent warning, currency-only display filtering,
  working removable chips/reset and transaction-dialog search.
- The tab-layout revision was checked at 320, 390 and 1,366 pixels: Accounting
  retains its selected management tab, Reports has a separate Budgets
  tab, unbudgeted categories are hidden, explicit zero budgets remain, and the
  new monthly definition dialog starts on the first day of the current month.

An APK build is not proof of device behavior. Physical Android interaction/SAF
testing is still required. The v0.3.15 release workflow passed native Windows
tests and installer/startup smoke checks, but not a full interactive budgeting
workflow. Earlier development builds listed above are not the published binaries.
