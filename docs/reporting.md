# Financial reports

Reports are derived in memory from the currently validated `Ledger`; the
Beancount journal remains the only source of financial data. The screen offers
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
show their own postings plus descendants as a display subtotal. Only direct
category postings contribute to the overall metric, preventing hierarchy
roll-ups from double-counting. Uncategorized postings are shown separately.

The report is an operational summary, not a full accounting statement. It does
not calculate FX gains/losses, historical valuations, or balances in a reporting
currency.
