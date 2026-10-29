# Supported journal format and editing

This is the current format contract, not the original MVP profile. TextMoney uses
`autobean-refactor` for lossless parsing and targeted edits, with its own bounded
accounting model. It is not a full Beancount loader or booking engine.
Fava and official Beancount are development-time references, not runtime engines.

## One journal

The selected folder contains one UTF-8 financial journal, `textmoney.beancount`.
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
- An absent `textmoney-active` means active. Explicit boolean `TRUE` and `FALSE`
  remain supported; this is visibility/reactivation, not an accounting `close`.
  An inactive category parent makes its descendants unavailable for new entries.
- Accounts/categories with history cannot be deleted; deactivate them instead.
  Children and other directive references can also prevent deletion.

New codes are suggested from names and existing conventions, but remain editable
before creation. Existing codes are not automatically renamed when display names change.
Inline category creation retains the unsaved transaction form.

### Moving and renaming an existing category

Ordinary **Edit** changes a category's display name and visibility; historical
screens resolve the new name without rewriting posting codes. To change the
technical code or parent, open **Edit → Advanced: change category code or hierarchy →
Move or rename category branch**. Choose an active existing parent (or no parent)
within the same Expenses/Income type and enter one new code component.

The separate review shows the source/destination and affected category,
transaction, budget and reference counts. Read the warning, stop all other
writers and pause synchronization, then acknowledge it to enable migration.
Cancel changes nothing. Unsaved display-name/visibility edits in the original
editor are not applied by this separate operation.

Migration moves the entire subtree, including inactive descendants, preserving
display names, declaration dates, amounts, posting order, tags, links, comments,
formatting and line endings. All typed Beancount account references and both
bare/quoted Fava budget category references are migrated. Unrelated quoted
strings and arbitrary app-specific string conventions are not interpreted or
globally replaced. Existing accounting/format restrictions still apply.

There are no merges, Expenses/Income conversions or moves into the branch itself.
An already referenced destination subtree is rejected, even without an explicit
declaration. Candidate validation can also reject a new parent whose declaration
date is later than the child. Local commission branches/defaults follow the move;
moving a subbranch outside a selected ancestor retains its fee eligibility.
Other devices' local settings are not migrated.

This feature requires desktop filesystem storage and
[atomic replacement with a verified backup](data-safety.md#category-migration).
It is blocked on Android SAF, not silently replaced by a non-atomic write.

## Transactions and editing

Transactions require explicit, balanced postings in declared accounts/categories
and cannot predate their declarations. Posting amounts use Decimal, with at most
12 integer digits and two fractional digits. App transaction forms accept
positive amounts and dates from `1970-01-01` through today, using the selected
default clock timezone.

Supported operations include expenses, income, reimbursements, opening balances,
balance adjustments and transfers, including bounded currency exchanges.
Settings offers an optional **Default transaction account**, saved only on the
current device, not in the journal. It preselects an active asset/liability
account for new expenses and receipts, or the source account of a transfer;
the destination still requires a separate choice. The form remains editable,
and editing saved transactions always uses their own accounts. Choose
**No default account** and save to disable preselection. Missing/inactive defaults
leave new forms unselected and show an explanation in Settings; they are not
silently replaced. The setting is device-wide and applies only when its account
code is active in the currently selected journal.

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

New entries do not generate `id`, `timestamp`, `textmoney-kind`,
`textmoney-expense-id` or `textmoney-fee-mode` metadata. Fee-category defaults are
local device preferences, not journal settings. Unrelated existing user metadata,
tags, links and comments are preserved; legacy app fields are not globally
migrated, stripped or used as authoritative transaction identities.

### Purchase and income commissions

Ordinary expenses and income support an optional fixed-amount commission in the
selected account's currency, in the same transaction. No percentages, separate
fee currency or fee-mode metadata are used. With amount 100 and fee 5:

| Kind / mode | Primary category | Account movement | Fee expense |
| --- | --- | --- | --- |
| Expense / added | Expenses +100 | -105 | +5 |
| Expense / deducted | Expenses +95 | -100 | +5 |
| Income / added | Income -105 | +100 | +5 |
| Income / deducted | Income -100 | +95 | +5 |

Purchase, income and transfer commissions may use any active expense category.
Settings offers a hierarchical multi-select of commission category branches;
selecting a parent includes all descendants, including future ones. All expense
categories remain available until this list is narrowed. The separate default must
belong to a selected branch; it initially falls back to `Expenses:Bank-Fees`.
Branches and default are saved together as one local device preference, with no
journal metadata. Older default-only preferences are read without changing their
meaning. Fee selectors (including the Settings default) use path order and nesting
indentation. An existing transaction's fee remains available when editing even if
later excluded; inactive categories retain the existing save validation.
A missing `Expenses:Bank-Fees`
is declared only on first nonzero use, in the same guarded save.

Recognition requires one ordinary account, one primary category, and optionally
one nonnegative fee posting, balanced in one currency without price annotations.
The maintainer-approved metadata-free convention identifies the **first expense
posting as the purchase and the second as its commission**, even if both use the
same category. Generated purchases place the account posting between them.
The locally configured category list filters selectors only; it does not change
posting recognition, reports or budgets, nor rewrite existing transactions.
Matching hand-written two-category splits are intentionally interpreted this way;
larger or unsupported splits remain read-only. Keep expense posting order intact
when editing externally if these UI roles should be preserved. Income and transfer
fees have distinct accounting roots and do not need this convention. Reopening
uses canonical Added presentation (purchase principal or net income); the original
mode cannot be recovered from native postings. Removing an annotated fee retains
its posting at zero, preserving its role and annotations.
Reimbursements and balance adjustments do not support commissions.

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
