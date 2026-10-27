# Supported journal format and editing

This is the current format contract, not the original MVP profile. TxnCount uses
`autobean-refactor` for lossless parsing and targeted edits, with its own bounded
accounting model. It is not a full Beancount loader or booking engine.
Fava and official Beancount are development-time references, not runtime engines.

## One journal

The selected folder contains one UTF-8 financial journal, `txncount.beancount`.
Declarations, transactions, journal options and budgets live in that file.
There is no private working ledger, database, include traversal or separate budget
store. Backups and Android recovery records are described in [Data safety](data-safety.md).
Storage diagnostics use a separate `storage-test.beancount` file and backup
namespace; legacy `poc-test.beancount` files are not migrated or rewritten.

Example using the current output conventions:

```beancount
option "operating_currency" "UAH"

2026-01-01 open Assets:Cash UAH
  name: "Wallet"
2026-01-01 open Expenses:Food
  name: "Food"
2026-01-01 open Equity:Opening-Balances

2026-01-01 * "Opening balance"
  time: "09:00:00"
  Assets:Cash 100.00 UAH
  Equity:Opening-Balances -100.00 UAH

2026-01-02 * "Synthetic shop" "Lunch"
  time: "12:30:00"
  Expenses:Food 12.34 UAH
  Assets:Cash -12.34 UAH

2026-01-01 custom "budget" Expenses:Food "monthly" 1000 UAH
```

Budget dates may be in the future; accounting transactions may not. Budget
allocation follows effective dates, not the directive's physical position in the
file. See [Category budgets](budgets.md) for intervals, precision and history.

## Accounts, categories and dates

- Ordinary accounts are `Assets:` or `Liabilities:`, with exactly one declared
  three-letter uppercase currency. Negative liability balances represent debt.
- Categories are hierarchical `Expenses:` or `Income:` declarations.
  Technical codes use colon-separated ASCII segments starting with `A-Z`;
  display names support Unicode and use `name` metadata on `open`.
- `Equity:Opening-Balances` offsets opening balances.
  `Equity:Balance-Adjustments` offsets balance-only receipts, which are neither
  income nor expenses. These are the existing reserved equity accounts, not
  general-purpose equity-account support.
- New declaration dates default to today and can be backdated. Opening-balance
  effective dates are separate and cannot predate the account. A child category
  cannot become available before its declared parent.
- New reserved equity declarations use the effective transaction date and move
  earlier only when a new backdated posting requires it. Existing historical
  dates, including `1970-01-01`, are not normalized on load.
- An absent `txncount-active` means active. Explicit boolean `TRUE` and `FALSE`
  remain supported; this is visibility/reactivation, not an accounting `close`.
  An inactive category parent makes its descendants unavailable for new entries.
- Accounts/categories with history cannot be deleted; deactivate them instead.
  Children and other directive references can also prevent deletion.

New codes are suggested from names and existing conventions, but remain editable
before creation. Existing codes are not renamed when display names change.
Inline category creation retains the unsaved transaction form.

## Transactions and editing

Transactions require explicit, balanced postings in declared accounts/categories
and cannot predate their declarations. Posting amounts use Decimal, with at most
12 integer digits and two fractional digits. App transaction forms accept
positive amounts and dates from `1970-01-01` through today, using the selected
default clock timezone.

Supported operations include expenses, income, reimbursements, opening balances,
balance adjustments and transfers, including bounded currency exchanges.
Reimbursements credit an expense category on the receipt date; they are not
income. An optional native shared link can associate one with an expense.
This does not track outstanding debt, cap reimbursement amounts or cascade edits
and deletions into linked records.

Transaction kind is inferred from postings, not an app metadata flag. Recognized
hand-written transactions can be edited without an app-generated ID. Source
positions identify entries only within the captured unchanged journal.
Unrecognized/split transactions can remain readable if they satisfy the supported
accounting validation, but their transaction editor/deleter is unavailable.
Opening balances use their separate guarded editor. Editing keeps the transaction
kind fixed; it does not turn an expense into income.

Negative asset balances, including historical overdrafts caused by backdated
changes, produce warnings rather than blocking a valid save. Balanced amounts,
declarations, currencies and date validation remain mandatory. Negative
liability debt balances are not overdraft warnings.

### Times and metadata

Transaction `time` is a quoted local `"HH:MM"` or `"HH:MM:SS"` wall-clock string.
If absent, the reader uses midnight without adding metadata on load. There is no
stored creation instant or offset. Changing the default clock timezone does not
convert saved transaction times. Manually entered wall times in DST gaps or
overlaps are accepted as wall-clock labels, not resolved into instants.
Native date-picker transport separately uses the device timezone to preserve the
selected calendar day.

New entries do not generate `id`, `timestamp`, `txncount-kind`,
`txncount-expense-id` or `txncount-fee-mode` metadata. Fee-category defaults are
local device preferences, not journal settings. Unrelated existing user metadata,
tags, links and comments are preserved; legacy app fields are not globally
migrated, stripped or used as authoritative transaction identities.

### Transfers and exchange precision

Same-currency transfers move principal between accounts; only explicit fee
postings affect expenses. Without a local default fee category,
`Expenses:Bank-Fees` is declared when first needed for a nonzero fee.
Fee presentation is reconstructed from postings rather than saved as metadata.

For exchanges, enter actual total debited and net credited amounts. A fee may use
either account's currency. The app writes a total price (`@@`) on the converted
source principal. For example, a 105 USD debit including a 5 USD fee:

```beancount
2026-01-10 * "Exchange with a source-currency fee"
  time: "10:00:00"
  Assets:Dollar -100.00 USD @@ 4200.00 UAH
  Assets:Bank 4200.00 UAH
  Expenses:Bank-Fees 5.00 USD
  Assets:Dollar -5.00 USD
```

This excerpt requires the three referenced accounts/categories to be declared.
For a 5 UAH destination fee, the source price is still `4200.00 UAH`, the
destination gets `4195.00 UAH` and the expense is `5.00 UAH`. Balances sum units;
exchange validation uses price weights. Serialization keeps two decimal places.
The derived rate uses 50-digit Decimal arithmetic; its preview is approximate,
not an input used to round postings.

Bounded externally authored unit-price (`@`) exchanges are also readable: one
priced negative source with supported fees, or a fee-free priced positive
destination. Transactions containing `@` are read-only in the transaction
editor/deleter. Unrelated edits retain their original text. Multiple prices,
third-currency fees, costs/lots and arbitrary exchange shapes are not supported.

## Journal display settings and preserved directives

**Settings → Advanced → Journal display currencies** writes standard
`option "operating_currency"` entries. New account creation appends a missing
currency in the same backed-up save. Loading alone does not add options.
Home balance sections follow the configured currency order; this never converts
or combines amounts.

Supported descriptive directives such as `note`, `event`, `document`,
`commodity`, `price` and `custom` are retained. Native budget directives are
interpreted; commodity precision and applicable Fava options inform budget
formatting/reference calculations as described in [Category budgets](budgets.md).
Preservation of other custom data does not implement its external semantics.

## Unsupported accounting and failure behavior

Includes, plugins, inherited tag/metadata context, `pad`/`balance` assertions,
`close` directives, booking methods, inferred postings, costs/lots, unsupported
account types/options and unknown syntax block normal loading/saving. Invalid
budgets or supported-accounting validation failures also block saving.
There is no automatic conversion to the supported subset or silent data removal.
Fix or restore the journal externally, then reload.

Lossless edits preserve unrelated bytes, including comments and LF/CRLF line
endings. A candidate is validated before storage changes, but preservation alone
is not full Beancount compatibility. See [Data safety](data-safety.md) for backup,
conflict and recovery behavior, [Reports](reporting.md) for currency-separated
totals, and [Compatibility testing](beancount-compatibility.md) for reference scope.
