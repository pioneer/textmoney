# Financial reports

The Reports screen has **Overview** and **Budgets** tabs. This page describes Overview;
see [Category budgets](budgets.md) for the separate monthly budget report and its
filters. Budget definitions are managed under **Accounting → Budgets**.

Reports are derived in memory from the currently validated `Ledger`; the
Beancount journal remains the only source of financial data. Overview offers
the current month, previous month, year to date, and an inclusive custom date
range. Changing or reloading the ledger invalidates the small in-memory report
cache.

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

The report is an operational summary, not a full accounting statement. It does
not calculate FX gains/losses, historical valuations, or balances in a reporting
currency.

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
