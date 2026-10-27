# Category budgets

Manage definitions in the **Accounting → Budgets** tab. Monitor spending and
overspending in the **Reports → Budgets** tab, alongside the existing report
**Overview**. Budgets live exclusively in the selected
`txncount.beancount` journal:

```beancount
2026-01-01 custom "budget" Expenses:Food "monthly" 10000 UAH
2026-02-01 custom "budget" Expenses:Food "monthly" 12000 UAH
```

TxnCount retains its existing single-file explicit-posting accounting model.
Fava **1.30.16** is the pinned development-time numerical reference, not a runtime
dependency. No alternate engine, separate budget store, proprietary budget
metadata, artificial transactions or license change is involved. Adapted Fava
MIT algorithms and their complete license notice are shipped in
[`app/notices/fava.txt`](../src/app/notices/fava.txt).

## Management and history

- Use **+ Create budget** at the top of **Accounting → Budgets** for new
  definitions. Existing definitions use the same CRUD affordances as Accounts
  and Categories: pencil for edit and trash for delete.
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

## Allocation and reports

The report shows one calendar month. All calculation ranges are half-open
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
value; long-press/hover the amount to inspect its exact Decimal.

The monthly overview shows **Budget**, **Spent**, **Remaining**, percentage used
and expense overspending, separately for each currency. Expand a category for
contributing transactions and, when different from the totals including
subcategories, an **Excluding subcategories** section for this category alone.
There is no report-mode selector or reporting-frequency selector. Definition
frequencies are unchanged: daily, weekly, monthly, quarterly and yearly budgets
are all allocated into the displayed month.

The Budgets report shows only categories with their own effective budget in the
selected period. Automatic parent totals are hidden: budgeting Food does not
create an Expenses row. A parent appears only when it has its own explicit
budget. Budgeted descendants remain visible even if their parents are hidden.
Explicit zero budgets remain visible. This is a presentation filter only:
Fava-compatible actuals, allocation and hierarchy totals are unchanged. Manage
all definitions, including future ones, in **Accounting → Budgets**.
For budgeted rows with an active direct definition in the selected period,
Reports also shows pencil/trash actions for quick edit/delete.
When a currency filter is selected, those actions target the latest definition
in that currency; without it, they target the latest definition for the category.

Category trees use real account components for actuals. Fava budget aggregation
and interval chart actuals use literal `startswith` matching: a query for
`Expenses:Food` can include `Expenses:FoodOther`. This behavior is preserved,
not silently “corrected.” Parent and child budgets are added, not overridden by
the most-specific budget. Missing budgets and explicit zero budgets remain
distinct.

Consumed percentage is a **TxnCount presentation ratio**, `actual / budget * 100`,
not a Fava-provided financial field. It is undefined for zero/missing budgets.
Only the visual bar is clamped; amounts and percentages are not capped.
Overspending does not block transactions.

## Filters, currencies and settings

- Use month arrows, click the month heading to choose a month/year, or select
  **Current month** (using the app timezone). No Fava date notation is required.
- **Category** opens a searchable tree with friendly names and expandable
  branches. Selecting a parent includes its descendants, not similarly named
  siblings. Only categories budgeted in this month and their ancestors appear
  in the selector; selecting an ancestor does not create an aggregate report row.
- Expand **Filters** for the optional **Account** tree, containing only ordinary
  asset/liability accounts and their branches. It selects complete transactions
  involving that account or a descendant. A warning remains visible outside the
  collapsed section: other accounts' spending is excluded, while budget amounts
  are unchanged. Displayed totals must not be mistaken for total spending.
- **Only overspent** keeps expense rows whose remaining amount is negative in
  any displayed budget currency. Zero budgets with positive spending qualify;
  missing budgets do not. Income is not treated as overspending.
- Active category/account/currency selections have removable chips.
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

UI/storage tests cover localized compact/wide Accounting and Reports tabs,
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

An APK build is not proof of device behavior: Android interaction/SAF and Windows
execution require their respective platforms. These development builds are not
a published release.
