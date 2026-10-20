# TxnCount

An offline-first personal expense tracker that keeps your data in
plain-text [Beancount](https://beancount.github.io/) files in a folder you choose.
No sign-up, backend service, or app-managed cloud storage.

**Alpha software:** features and file support are still limited, and bugs are
possible. Keep independent backups of important financial data.

TxnCount is being developed with substantial AI assistance.

## Implemented

- Record and edit expenses with an account, category, amount, date/time and
  description. Add an optional shop/recipient when needed.
- Record income, expense reimbursements and balance-only adjustments. Partial
  reimbursements can go to a different account; linking the original expense
  is optional. Reimbursements reduce net expenses rather than count as income.
- Manage single-currency accounts and opening balances.
- Transfer between own accounts, including cash, loan repayments and currency
  exchanges. Enter both actual amounts for exchanges; the rate is calculated
  excluding optional fees in either account's currency.
- Organize expenses with hierarchical categories.
- Review balances and transaction history in compact phone and desktop layouts.
- Delete expenses after confirmation; deactivate accounts and categories with
  history rather than losing their records.
- Use system, light or dark appearance, and system or explicit timezones.
- Available translations: **English, Ukrainian and Russian**.
- Keep verified backups and detect conflicting external changes before saving.
- Use standard journal display currencies for compatible Beancount viewers,
  without converting or combining different currencies.

## Future plans

Planned areas include budgets, reports and split-expense editing. These features are **not implemented
yet**; priorities and scope may change during alpha, with no release dates promised.

## Get the app

Download the Android APK from **[GitHub Releases](https://github.com/pioneer/txncount/releases/latest)**.
Android 7.0 or newer is required. You can also track this repository with
[Obtainium](https://github.com/ImranR98/Obtainium) for updates.

Android is the currently distributed platform. Linux can run from source;
Windows support is implemented but has not yet been validated.
Current APKs use a development signing key; production signing is not yet
established.

## Getting started

1. Open TxnCount and choose a dedicated local data folder.
2. Create an account with its currency and, optionally, an opening balance.
3. Add expense categories under **Accounts**.
4. Use **Add transaction** (**Додати операцію**), enter a description in the first
   field, then a positive amount, select the account and category, and save.
   The default **−** records spending; the hint below the amount explains the sign.

Switch to **+** for ordinary income; the numeric amount stays unchanged and
the category selector shows income categories. For special receipts, use
**Advanced → Money received** (**Додатково → Надходження**):
**Expense reimbursement** reduces an expense category, while
**Balance adjustment** records receipts that should affect neither
income nor current expenses (for example, recovery of a pre-tracking purchase).
Create income categories under Accounts by selecting the Income category type.
Reimbursements reduce expenses on the date received, which can produce negative
net expenses in that period. A linked expense is a reference, not a tracked debt.

Choose **↔** beside **− / +** to transfer money. Transfer mode shows required
**source** and **destination** accounts; minus and plus only show one account.
The hint explains debit, credit or transfer without calling any account "main".
The preview shows the transfer direction. For same-currency transfers,
enable a fee under Advanced and choose whether it is added on top or deducted
from the amount. Change its category for that transfer, or set the default
under Settings. The bank-fee category is created automatically on first use.
For loans, choose **Loan / credit** when creating the account and enter the
opening amount owed. Balances use standard Beancount signs: debt is negative;
a transfer into the loan account reduces it.

For accounts in different currencies, enter **Total debited** (**Списано**) and
**Actually credited** (**Зараховано**). Neither amount is calculated or replaced.
If there is a fee, select its currency: a source-currency fee is included in the
debited total; a destination-currency fee has already been withheld from the
credited amount. The preview shows the exchanged principal and derived rate
excluding fees. For example, 105 USD debited with a 5 USD fee and 4200 UAH
credited means 100 USD exchanged at 42 UAH/USD.
Changing the selected mode never changes entered amounts. To reverse a transfer,
change its source/destination accounts and exchanged amounts explicitly.
Fee currency remains explicit under Advanced.

Negative cash/bank balances—including past overdrafts—show a warning but do not
prevent saving. Invalid amounts, account selections and accounting still block saving.

Use the pencil and trash icons to edit or delete transactions. The same form
opens for editing, with the operation type fixed to preserve its accounting
treatment; transfer direction can still be reversed. Advanced starts collapsed,
with a summary of saved fees, payee and time when editing. Date remains visible;
time and optional counterparty are under Advanced. Opening balances can
be edited from either the account or its opening transaction. Accounts and
categories with history can be deactivated and reactivated; their records remain.

Settings lets you choose appearance and timezone. **Advanced → Journal display
currencies** controls how compatible applications organize amounts; it does not
exchange currencies or combine balances.

## Your data and synchronization

Your selected folder contains a single UTF-8 journal, `txncount.beancount`, and
a `backups/` directory. TxnCount accesses these files directly; it does not upload
them or maintain a separate database. Edits preserve transaction IDs and unrelated
comments and metadata.

Dropbox, Dropsync or another folder synchronization tool can synchronize the
folder separately. Synchronization is not built into TxnCount. Let it finish
before editing on another device, and avoid simultaneous edits.

Backups and conflict checks reduce risk, but writes are **not atomic** and cannot
prevent every concurrent external change. Backups are not automatic undo or a
substitute for an independent copy.

TxnCount supports a limited, single-file Beancount profile, not arbitrary
Beancount books. Unsupported accounting features or invalid data block saving
rather than being silently discarded. See the [format guide](docs/mvp-0.1.md)
and [editing and data-safety guide](docs/crud-fava.md) for details.

## Version history

| Version | Highlights |
| --- | --- |
| [0.1.4](https://github.com/pioneer/txncount/releases/tag/v0.1.4) | Same-currency transfers, optional fees and loan repayments; configurable fee category and overdraft warnings. |
| [0.1.3](https://github.com/pioneer/txncount/releases/tag/v0.1.3) | Income, reimbursements and balance adjustments; unified transaction form, description previews and improved Android/Linux icons. |
| [0.1.2](https://github.com/pioneer/txncount/releases/tag/v0.1.2) | Refreshed money-and-text icon, correct icon for Linux source runs and clearer alpha documentation. |
| [0.1.1](https://github.com/pioneer/txncount/releases/tag/v0.1.1) | TxnCount Android app identity, versioned APK downloads and consistent in-app version display. |
| 0.1.0 | Initial alpha: accounts, categories, expense editing/deletion, backups, translations and adaptive layouts. |

## Development

Requires **Python 3.13+** and [uv](https://docs.astral.sh/uv/).
Clone the repository, then use Invoke for project operations:

```sh
git clone https://github.com/pioneer/txncount.git
cd txncount
uv sync
uv run inv run
```

Other useful commands:

```sh
uv run inv run-web        # Run in a local browser
uv run inv check          # Format check, lint, Pyright and tests
uv run inv build-android  # Build an Android APK
uv run inv build-local    # Build for the current desktop OS
uv run inv --list         # All available tasks
```

The APK is written to `build/apk/txncount-<version>.apk`. Native builds require
platform toolchains; see [development and packaging](docs/development.md).
See also [architecture](docs/architecture.md) and [testing](docs/testing.md).
