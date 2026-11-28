# TextMoney features

This page is a map of the app's current user-facing capabilities, not a
replacement for the detailed guides. Follow the links for behavior, requirements
and limitations; the guides are the source of truth when details change.

## Money and records

- **Accounts and categories:** Create currency-specific cash, bank and
  liability accounts; record opening balances; organize expense and income
  categories hierarchically; edit when a category becomes available; and assign
  icons. Routine views favor names over account/category codes; codes remain
  fully visible when creating or editing accounts and categories, including
  parent-category choices and inline category creation during transaction entry.
  Selection-only menus hide codes unless needed to distinguish duplicate names
  under the same parent.
  Accounts and categories with history can be deactivated and reactivated.
  See [the user guide](user-guide.md#add-accounts-categories-and-opening-balances),
  [icons](entity-icons.md) and [journal format](journal-format.md).
- **Transactions:** Record expenses, income, reimbursements, balance
  adjustments and transfers, including transfers between currencies. Add fixed
  fees or commissions where supported, enter similar ordinary expenses or
  income together, and track and repay debt. Edit or remove
  supported transactions; some externally authored or split transactions are
  read-only. Operations offers paginated browsing with account, category, date,
  currency and payee/description filters, including scoped report drill-downs.
  A visible **Clear all filters** action removes every restriction at once.
  See [the user guide](user-guide.md) and
  [transaction semantics](journal-format.md#transactions-and-editing).
- **Budgets:** Set dated category limits, review monthly allocation and
  spending, browse budget history, filter results, and drill into contributing
  transactions through a bounded read-only preview or the full Operations
  browser. Home can show a preview of the current month's budgets.
  See [budgeting](budgets.md).
- **Balances and reports:** Review balances and recent activity on Home. Browse
  transaction history and daily summaries, or report income, expenses and
  balances for supported periods, with category and account breakdowns.
  Amounts remain separate by currency; reports do not convert them.
  See [reporting](reporting.md).

## Optional AI features

AI is optional: tracking, reports, budgets, the bundled icon catalogue and image
imports work without a provider or API key. The currently available AI workflow
is **account/category icon generation**, not financial advice or automated
bookkeeping.

- **Describe an icon:** Configure a provider API key and image-generation model
  in **Settings → AI**, then use **Choose icon → Generate with AI**. OpenAI,
  Google Gemini and xAI models are supported. Generate a candidate from your
  description, review it and choose **Save changes** to apply it.
- **Choose a reference:** The optional reference selector defaults to
  **No reference**. Choose the icon present when the picker opened or an icon
  assigned to another category/account in the same journal. Its thumbnail,
  name and code identify the source. Reference selection does not change the
  icon selected for saving.
- **Theme adaptation:** **Adapt generated icons to the theme** is on by default.
  Neutral artwork follows the light/dark theme while colored accents keep their
  colors. This is heuristic and applies only to future generations; turn it off
  to preserve the generated image's original colors.
- **Network, privacy and cost:** Explicitly starting generation sends your
  description, app instructions and any chosen reference image to the selected
  provider. Reference account/category names and codes are not included, and
  the journal is not uploaded. Internet access and your own API key are required;
  provider terms and charges apply. Keys and model choices are device-local,
  not journal-shared; keys require the platform's secure storage. Saving a key
  does not test it with the provider.
- **Review and limits:** Generated-image rights are not verified. Check provider
  terms and your rights to both the result and any reference image. Results remain
  drafts until saved; failures or cancellation keep the previous selection.
  The separate image-understanding model setting is available, but does not
  currently provide receipt parsing or transaction entry.

See [AI setup](user-guide.md#configure-optional-ai-features) and
[icons and AI](entity-icons.md) for the workflow, validation and limitations.

## Storage and settings

- **Item actions:** Android uses single-tap item menus instead of inline
  Edit/Delete/etc. buttons for transactions, accounts, categories, budgets/history
  and report drill-downs. Windows/Linux retain inline controls at every width.
  Whole mobile budget items open their menu, including figures and progress.
  Tappable mobile elements show feedback within the original touched control;
  item-menu triggers use native bounded ink ripples.
  Category expansion and read-only restrictions remain unchanged.
  See [item actions](user-guide.md#item-actions).
- **Desktop navigation:** Shared Back/Forward arrows and keyboard shortcuts
  retrace screen visits, restoring each Operations visit's filters and page.
  Escape cancels the active popup, including after closing a dropdown menu.
  Mobile retains Back navigation without a Forward button; its bottom bar
  groups Accounts, Categories and Settings under a labeled More menu.
  See [navigation](user-guide.md#find-your-way-around).
- **Offline, local records:** Core tracking needs no sign-up or backend. The journal is a
  plain-text Beancount file in a folder you choose. TextMoney validates edits,
  preserves unrelated journal content and uses backups and recovery safeguards.
  See [data safety](data-safety.md) and
  [supported journal format](journal-format.md).
- **Use across devices:** An external sync tool can synchronize the journal
  folder. TextMoney does not provide built-in synchronization; use a fully
  accessible local copy on each device and finish syncing before editing on
  another device.
  See [synchronization](synchronization.md).
- **Personalization:** Choose light, dark or system appearance, set motion to
  system default, reduced or full, and customize language, display and timezone.
  Configure shared
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
