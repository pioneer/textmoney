# TextMoney features

This page is a map of the app's current user-facing capabilities, not a
replacement for the detailed guides. Follow the links for behavior, requirements
and limitations; the guides are the source of truth when details change.

## Money and records

- **Accounts and categories:** Create currency-specific cash, bank and
  liability accounts; record opening balances; organize expense and income
  categories hierarchically; and assign icons. Accounts and categories with
  history can be deactivated and reactivated.
  See [the user guide](user-guide.md#add-accounts-categories-and-opening-balances),
  [icons](entity-icons.md) and [journal format](journal-format.md).
- **Transactions:** Record expenses, income, reimbursements, balance
  adjustments and transfers, including transfers between currencies. Add fixed
  fees or commissions where supported, and track and repay debt. Edit or remove
  supported transactions; some externally authored or split transactions are
  read-only.
  See [the user guide](user-guide.md) and
  [transaction semantics](journal-format.md#transactions-and-editing).
- **Budgets:** Set dated category limits, review monthly allocation and
  spending, browse budget history, filter results, and drill into contributing
  transactions. Home can show a preview of the current month's budgets.
  See [budgeting](budgets.md).
- **Balances and reports:** Review balances and recent activity on Home. Browse
  transaction history and daily summaries, or report income, expenses and
  balances for supported periods, with category and account breakdowns.
  Amounts remain separate by currency; reports do not convert them.
  See [reporting](reporting.md).

## Storage and settings

- **Offline, local records:** No sign-up or backend is needed. The journal is a
  plain-text Beancount file in a folder you choose. TextMoney validates edits,
  preserves unrelated journal content and uses backups and recovery safeguards.
  See [data safety](data-safety.md) and
  [supported journal format](journal-format.md).
- **Use across devices:** An external sync tool can synchronize the journal
  folder. TextMoney does not provide built-in synchronization; use a fully
  accessible local copy on each device and finish syncing before editing on
  another device.
  See [synchronization](synchronization.md).
- **Personalization:** Choose light, dark or system appearance; English,
  Ukrainian or Russian; and display and timezone preferences. Configure shared
  defaults for transaction accounts, fees and budget ordering.
  See [the user guide](user-guide.md#adjust-appearance-and-display-settings),
  [shared settings](shared-settings.md) and
  [localization](localization.md).

## Availability and boundaries

TextMoney is distributed for **Android, Windows and Linux**. macOS and iOS
versions may be possible, but are not currently released or validated; the
main developer does not have Apple devices to test them.

The app supports a subset of Beancount in a single journal; it is not a
general-purpose importer for every valid Beancount book. Unsupported
accounting semantics prevent the journal from loading or saving, and some
supported transactions cannot be edited. Split-expense editing is not
currently supported.

See [platform requirements](platform-notes.md) for release and device limits,
[journal format](journal-format.md) for accounting and editing support, and
[the user guide](user-guide.md) for workflows.
