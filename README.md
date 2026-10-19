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
- Manage single-currency accounts and opening balances.
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

Planned areas include income, transfers between accounts, currency exchange,
budgets, reports and split-expense editing. These features are **not implemented
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
4. Use **Add expense** to record your spending.

Use the pencil and trash icons to edit or delete expenses. Opening balances can
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

## Versions and Android identity

The app ID is `com.pioneer.txncount`. The maintained version is
`[project].version` in `pyproject.toml`; `[tool.flet].build_number` is the
independently increasing Android version code. These values drive the packaged
version display and APK filename.

Use patch releases for small changes and minor releases for milestones during
alpha. Every published update gets a matching Git tag, a new app version and a
greater Android build number. Never replace an existing release APK with
different code.

For each release, add a short entry to **Version history** above and a short,
user-facing **What's new** summary to the GitHub release description. Keep
installation requirements, important warnings and known limitations brief and
relevant; detailed implementation and verification records belong in the docs.
