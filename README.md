<p align="center">
  <img src="assets/icon.png" width="96" height="96" alt="TextMoney app icon">
</p>

<h1 align="center">TextMoney</h1>

<p align="center">
An offline-first personal finance app. Track everyday
spending, see what’s left in your accounts, and set monthly budgets for things
like groceries and eating out.
</p>

<p align="center"><strong>Android · Windows · Linux</strong></p>

Core tracking works offline with no app sign-up. Optional
[AI icon generation](docs/features.md#optional-ai-features) requires internet
access and your own provider API key and may incur charges.
Your records are stored in a plain-text
[Beancount](https://beancount.github.io/) file in a folder you choose.
You can [sync the whole folder between Android, Windows and Linux](docs/synchronization.md)
using an external synchronization tool. TextMoney has no built-in sync;
each device needs a fully accessible local copy, and sync must finish before
editing on another device.

<p align="center">
  <a href="https://github.com/pioneer/textmoney/releases/latest"><strong>Download TextMoney</strong></a> ·
  <a href="docs/quickstart.md"><strong>Quickstart</strong></a> ·
  <a href="docs/user-guide.md">User guide</a> ·
  <a href="docs/synchronization.md">Sync between devices</a> ·
  <a href="docs/platform-notes.md">Platform notes</a>
</p>

<p align="center">
  <a href="docs/screenshots/home-desktop.png">
    <img src="docs/screenshots/home-desktop.png" width="780" alt="TextMoney desktop Home showing account balances, recent transactions, and monthly budgets">
  </a>
  <br>
  <em>Desktop layout</em>
</p>

<p align="center">
  <a href="docs/screenshots/expense-mobile.png">
    <img src="docs/screenshots/expense-mobile.png" height="500" alt="TextMoney mobile expense editor with account and category icons">
  </a>
  <a href="docs/screenshots/navigation-mobile.png">
    <img src="docs/screenshots/navigation-mobile.png" height="500" alt="TextMoney bottom More menu with Accounts, Categories and Settings">
  </a>
  <br>
  <em>Mobile layout</em>
</p>

## What you can do

- **See where your money goes.** Record expenses and income, organise spending
  by category, and see how much you spent this month or over a period you choose.
- **Keep track of your accounts.** See how much you have in cash and bank
  accounts, and how much you still owe on credit cards and loans. Record
  transfers between accounts, including exchanges between currencies.
- **Stay within your budget.** Set spending limits by category and see how much
  of each monthly budget you’ve used, right from the home screen.
- **Personalize icons.** Choose from the offline icon library or import an image.
  Optional AI generation can use your description and a chosen reference icon.
  See [icons and AI](docs/entity-icons.md).

Available in English, Ukrainian, and Russian.
Developers are welcome to submit a pull request with translation corrections
or a new language; see the [translation guide](docs/localization.md).
Weblate integration may be added in the future; it is not configured yet.

## Get started

Follow the [Quickstart](docs/quickstart.md) for step-by-step instructions on
Android or desktop, including choosing a records folder and adding your first
transaction.

*TextMoney is in beta and developed with substantial AI assistance.
Keep a separate backup of your financial records.*

## Guides and technical details

Already using Beancount? Check the [supported journal format](docs/journal-format.md)
before opening an existing file. TextMoney supports a subset of Beancount
in a single journal file. TextMoney preserves unrelated journal content.
Unsupported accounting semantics prevent loading or saving without modifying
the file; some supported transactions are view-only.

| You want to… | Read |
| --- | --- |
| Get an overview of current features and limits | [Feature overview](docs/features.md) |
| Record expenses, income, refunds, transfers or debt repayments | [User guide](docs/user-guide.md) |
| Set spending limits and review monthly budgets | [Budgeting](docs/budgets.md) |
| Configure AI and generate account/category icons | [User guide](docs/user-guide.md#configure-optional-ai-features) · [Icons and AI](docs/entity-icons.md) |
| Understand spending reports and currency-separated totals | [Reporting](docs/reporting.md) |
| Use the same records on several devices | [Synchronization](docs/synchronization.md) |
| Keep backups or recover from an interrupted save | [Backups and recovery](docs/data-safety.md) |
| Choose a release file or troubleshoot platform requirements | [Platform notes](docs/platform-notes.md) |
| Run from source or build the app | [Development and builds](docs/development.md) |
| See what changed between releases | [Release notes](https://github.com/pioneer/textmoney/releases) |
