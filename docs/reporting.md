# Financial reports

The **Reports** screen shows the financial overview described here.
Monthly budget reports and definitions are on the separate **Budgets** screen;
see [Category budgets](budgets.md) for its calculations and filters.

Reports are derived in memory from the currently validated `Ledger`; the
Beancount journal remains the only source of financial data. Overview offers
the current month, previous month, year to date, and an inclusive custom date
range. Changing or reloading the ledger invalidates the small in-memory report
cache.

The month label and left/right arrows browse calendar months, matching the
Budgets navigation. They disappear for year-to-date and custom ranges.
Browsing adds the selected month to the period dropdown; choosing Current month
or Previous month returns to the corresponding relative preset. The current
month ends today; other browsed months include their last calendar day.
The browsed month and custom date drafts survive refreshes, language changes
and navigation within the app.

Income and expense amounts come from `Income:` and `Expenses:` postings, not
transaction-kind labels. Income postings are shown as positive values.
Reimbursements remain expense postings with their recorded date and reduce the
category they credit; a negative net expense is valid. Transfer and exchange
principal, loan principal, opening balances and balance adjustments use asset,
liability or equity accounts and are not counted. Expense postings for transfer
fees are counted once.

Totals are calculated independently per currency using `Decimal`; the
application does not infer exchange rates or convert amounts. Category parents
show their own postings plus descendants as a display subtotal in Overview.
This does not add automatic parent rows to the Budgets report. Only direct
category postings contribute to the overall metric, preventing hierarchy
roll-ups from double-counting. Uncategorized postings are shown separately.
Category headers and leaves use the same compact 40-pixel row minimum in
Overview and Accounting categories, without extra expandable-header padding.
Inactive labels and larger text can grow the row to avoid clipping.

The report is an operational summary, not a full accounting statement. It does
not calculate FX gains/losses, historical valuations, or balances in a reporting
currency.

## Account balances

Overview ends with every account that is open by the period end, grouped by
account currency in operating-currency order. Each row shows the balance at the
period end, with the balance before the first day and the signed change within
the inclusive range underneath. The list stays visible for periods without
income or expenses. Inactive accounts appear only when they had a balance or a
movement. As on Home and the normal accounts list, negative liability balances
are labeled **Outstanding debt** with a positive amount. Zero is zero outstanding
debt; a positive liability balance is labeled **Credit balance** (overpayment),
not debt. Opening balances use the same labels; the period's change stays signed
in Beancount terms (a positive change reduces debt). The journal is unchanged.

Operations starts with a one-line strip of today's total balance per currency:
the signed sum of all account balances, so liabilities reduce it. Amounts in
different currencies are never combined; the line scrolls horizontally when it
does not fit.

While creating or editing a transaction, each selected account shows
`Balance: now → after`. The after value applies the draft with the same
commission and exchange rules as the journal builders. When editing, the
original transaction is excluded first. It shows only the current balance until
the draft is valid. A negative asset result is highlighted. Saving remains
allowed, as with other negative-balance warnings.
Liability hints use the same debt/credit labels for both values on separate lines,
including drafts that repay the debt completely or overpay it. Negative liabilities never trigger
an insufficient-funds warning; the asset funding a repayment still can.

## Daily Operations summaries

Below each date in Operations, a compact clickable block shows **net expenses**
and **income** separately for every currency. It follows the same posting-based
rules as Overview: commissions on purchases, receipts and transfers are expenses;
reimbursements reduce expenses on their recorded accounting date. Income is not
reduced by its commission. There is no cross-currency combined total. Days with
only transfers, opening balances or adjustments show no expenses or income.

Click the block to open a scrollable, read-only category report for that date.
Expense and income trees are separate, retain inactive categories and include
descendant subtotals without double-counting. On narrow screens, category names
sit above their amounts and percentages. Close, Escape or Enter dismisses
the popup without writing to the journal. The popup is a snapshot of the
currently loaded ledger; reopen it after changes. List totals refresh after
edits, deletions and external journal reloads.

Daily blocks appear only in Operations, not in the truncated Home activity
list or filtered budget transaction details. Compact totals scan each day's
entries once; category trees are built only when a daily report is opened.
