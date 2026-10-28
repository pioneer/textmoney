# Localization

TextMoney uses standard GNU gettext monolingual PO files. Stable semantic
`msgid` values (currently prefixed `ui.`) are identifiers, not English UI
strings; translate the `msgstr` values. The maintained base catalogs are
[English](../locales/en.po), [Ukrainian](../locales/uk.po) and
[Russian](../locales/ru.po). [messages.pot](../locales/messages.pot) is generated
from English. Compiled MO files are build artifacts under `src/app/locale/` and
are included in app packaging. Babel is a runtime dependency for budget number
formatting and journal locale settings; its catalog-authoring tools are also used
during development. Android extracts Babel's bundled locale data through
`tool.flet.android.extract_packages`.

## Maintainer commands

Run project operations through Invoke:

```sh
uv run inv translations-validate
uv run inv translations-template
uv run inv translations-compile
uv run inv translations-init --language=de
uv run inv check
```

When adding UI strings, use stable keys through `translate()` or `translate_plural()`.
Add their English source translations to `locales/en.po`, then refresh
`messages.pot`. Add the same keys to all maintained PO catalogs, translate them,
and run validation. The check verifies key parity, named placeholders, plural
form counts and POT freshness. `inv check`, test, run and build workflows compile
the catalogs before consuming or packaging them.

To add a language, run `uv run inv translations-init --language=<code>` using a
two- or three-letter ISO language code (for example `de`). Translate its
`msgstr` entries, including `ui.language_name`, then run validation and
compilation. Runtime discovery, system-language matching and the language
selector use the compiled catalogs, so adding a language does not require
editing application Python. An untranslated entry falls back to English; the
English catalog itself must remain complete.

## Weblate configuration

When Weblate is introduced, connect it directly to this Git repository with:

- File format: **Gettext PO file (monolingual)**
- File mask: `locales/*.po`
- Base language file: `locales/en.po`
- Translation template: `locales/messages.pot`

Keep stable `msgid` keys unchanged across releases. Preserve translator comments,
named placeholders and standard gettext plural entries. Do not edit generated
`.mo` files or use another translation format. Weblate hosting and repository
synchronization are not configured yet.
