# TxnCount

TxnCount is an offline-first personal expense tracker. It stores your accounts
and expenses as plain-text [Beancount](https://beancount.github.io/) data in a
folder you choose. Your files stay under your control; the app has no account,
database, or backend service.

## What works today

- Create accounts with a currency and optional opening balance; rename them,
  safely adjust opening balances, and deactivate/reactivate them.
- Create and rename hierarchical expense categories; deactivate/reactivate
  categories without changing technical IDs or history.
- Record expenses; edit their date/time, amount, account, category, payee and
  description. Permanently delete ordinary expenses after confirmation.
- Delete unused accounts/categories; keep those with history via deactivation.
- Configure journal display currencies under Advanced settings; new account
  currencies are added automatically without replacing existing choices.
- Review account balances, recent activity, and transaction history.
- Browse the same features in adaptive phone, tablet, and desktop layouts.
- Follow the device language with explicit **System** selection, or choose
  English, Ukrainian, or Russian; follow system light/dark appearance or
  choose a theme explicitly.
- Follow the device timezone or select an IANA timezone such as `Europe/Kyiv`.
  Read dates in the app language; enter dates/times manually or through pickers.
- Enter a prominent multiline description and one date/time group. An optional
  shop/recipient starts blank and stays hidden until expanded.
- Browse compact lists: expenses show account → category and one amount;
  edit/delete use icons with translated tooltips. Details stay in the editor.
- Make verified backups and detect external edits before saving.
- Open the existing storage and Beancount technical diagnostics in Settings.

The app never combines different currencies into a converted total. It does not
yet support income, transfers, currency exchange, loans, budgets, reports,
split-expense editing, yearly-file layouts, or importing arbitrary books.

## Data, privacy, and synchronization

The selected folder contains `txncount.beancount`, a UTF-8 plain-text journal.
TxnCount reads and writes that file directly; it does not copy it into an app
database or upload it. Backups are stored in the same folder under `backups/`.
Writes are not atomic. The app checks for external changes and refuses a
conflicting overwrite, but cannot prevent a simultaneous write by another
program in the final check/write window.

Edits splice only selected directives using the lossless parser; unrelated
comments and metadata stay intact. Existing transaction IDs are retained.
Historical accounts/categories are never deleted. Backups are not an automatic
undo system: preserve an independent copy before updating an app used with real
financial data. See the [CRUD safety and test guide](docs/crud-fava.md).

Dropbox, Dropsync, or another synchronization tool is optional and is not
included. Install and configure a sync provider separately, then select its
local folder in TxnCount. Let synchronization settle before editing the same
journal on multiple devices. Keep independent backups of important data.

The current journal reader supports a deliberately narrow single-file
Beancount profile. Unsupported accounting features, invalid syntax, and
unbalanced transactions block writes rather than being discarded. See the
[MVP format and limitations](docs/mvp-0.1.md) before using existing Beancount
books.

## Platforms

- **Android:** APK available; the user has confirmed recording an expense and
  seeing the balance update on a physical phone. Further device/provider and
  CRUD checks are listed in the current milestone guide; CRUD is awaiting
  physical-device confirmation.
- **Linux:** development and desktop UI are available; the app and accounting
  tests run on Linux.
- **Windows:** filesystem support is implemented, but Windows execution has not
  yet been validated.

## Getting started

On Android, install the APK built for the current version:

```sh
adb install -r build/apk/txncount.apk
```

Open TxnCount, choose a dedicated data folder, and create an account. Add at
least one expense category in **Accounts**, then use the prominent **Add
expense** action. Use a test folder with synthetic data while evaluating the
current restricted Beancount profile.

Use the pencil icon in **Transactions** to update an ordinary expense or the trash icon to
remove it after confirmation. Opening balances are edited under **Accounts**.
The opening entry's pencil in **Transactions** also edits the same amount,
using the same balance and conflict checks. Opening dates are hidden in lists
and available in collapsed **Details**; amount edits retain the stored dates,
ID and metadata. Complex/multiple opening entries remain read-only.
Accounts and categories with history offer an Active checkbox instead of
deletion; inactive items stay in history/balances but cannot be used for new
expenses. Deactivating a parent category also hides its descendants from choices.

For an existing journal, open **Settings → Advanced → Journal display currencies**,
enter e.g. `UAH USD EUR`, and save. Existing currencies are displayed without
modifying the file on load. Creating an account adds its currency if missing,
retaining existing configuration. These standard Beancount `operating_currency`
options organize amounts when viewing the journal in compatible applications;
accounts keep their own currencies. TxnCount never converts money or aggregates
balances across currencies.

On a desktop with Fava installed, open the same root file:

```sh
uv run fava /path/to/your/txncount.beancount
```

Fava and the official Beancount engine are development/test dependencies, not
Android runtime dependencies. The current milestone remains **single-file by
request**: changing an expense's year changes its date in the same file; yearly
files and migration are deferred.

In **Settings**, keep **Timezone → System** to follow this device, or select a
named timezone and save. Timezone preferences are local, not
synchronized journal configuration. Existing timestamps are displayed in the
selected zone without rewriting them. Forms show one date and time with manual
fields and picker buttons. Unchanged values retain the original accounting date
and exact timestamp, even if legacy entries have differing dates. Editing the
date or time sets both from the visible date/time. English manual dates use
`MM/DD/YYYY`; Ukrainian/Russian use
`DD.MM.YYYY`, with 24-hour times. Display dates use localized month names.
Ambiguous/nonexistent daylight-saving times are rejected rather than guessed;
an unchanged existing timestamp remains intact.

**Language → System** follows the device's language preferences, falling back
to English for unsupported languages. Explicit English/Ukrainian/Russian
choices remain fixed until you choose System again. This choice persists locally.

The optional **Shop / recipient** means who receives the money, not who pays or
the expense category. Expand it only when needed; new expenses always start
blank. Existing saved recipients remain intact when editing other fields.
Earlier default-payee preferences are ignored, not applied to new transactions.
Descriptions accept line breaks inside quoted journal strings; embedded quotes
and backslashes are escaped rather than inserting new directives.

For the latest APK, recovery behavior and Android device checklist, read the
[CRUD/Fava guide](docs/crud-fava.md). The underlying restricted file profile is
documented in [MVP 0.1](docs/mvp-0.1.md).

## Development

Requires Python 3.13+ and `uv`. Invoke is the only project task runner:

```sh
uv sync
uv run inv run
uv run inv check
uv run inv build-android
```

`uv run inv --list` shows available tasks. The generated APK is
`build/apk/txncount.apk`. Do not uninstall the existing Android app when
updating if you need to retain its SAF folder permission.

## Project notes

- [Architecture and dependency boundaries](docs/architecture.md)
- [MVP 0.1 journal format, limitations, and device checklist](docs/mvp-0.1.md)
- [CRUD and Fava compatibility, data safety, and test checklist](docs/crud-fava.md)
- [Android Storage Access Framework PoC](docs/poc-android-storage.md)
- [Beancount parser compatibility PoC](docs/poc-beancount.md)
- [Flet 1.x conventions](docs/flet.md)

The PoC reports record earlier technical investigations; they are historical
references, not claims that the restricted MVP supports the full Beancount
language or accounting engine.
