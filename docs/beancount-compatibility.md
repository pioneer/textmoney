# Beancount 3 compatibility - Stage 2 history

This report preserves the original experiment and its device-test findings.
Old package IDs, synthetic filenames, build numbers and checksums below describe
those historical builds, not the current release. Module/task references and
links use current diagnostic names. See
[current version metadata](development.md#version-metadata).

**Verdict: PASS for the bounded fixture profile (user-confirmed Android phone
test, 2026-10-18).** At the start of Stage 3 the user confirmed both PoCs passed.
The supplied screenshot shows Python 3.14.7 on Android completing the bundled
parser, Decimal balances, metadata, edit and append checks. This does not establish
a complete Beancount engine: the scope and unsupported accounting below remain.

Stage 1 storage is **PASS, user-confirmed on a physical phone on 2026-10-18**.
Its implementation, package ID, preference key and native SAF bridge are
preserved. In Stage 3 both original screens remain under **Technical diagnostics**.
The **Run Beancount diagnostics** button accesses only bundled synthetic fixtures, never the
selected folder or real financial files.

## Libraries evaluated

| Library | Evidence and capability | Decision |
| --- | --- | --- |
| LaunchPlatform `beancount-parser` **1.2.3** | MIT; pure `py3-none-any` wheel; only dependency Lark. Latest release/repository push 2024-05-09. Flat Lark syntax tree with comments/source positions, not grouped accounting objects or a validator. Basic fixture parses, but valid `pushtag`/`pushmeta` fixture fails at line 7. | Useful syntax candidate/control, not preferred for new production parsing. Older maintenance activity and incomplete tested Beancount 3 syntax. |
| `autobean-refactor` **0.3.1** | MIT; release/repository push 2026-05-17. Pure `py3-none-any` wheel; Lark and typing-extensions. Typed, lossless token/tree model; basic and advanced fixtures round-trip exactly. No booking, balancing validation or complete accounting engine. | Preferred tested candidate for source-aware reading/editing; bounded Android device test passed. |
| Lark **1.3.1** | MIT, pure Python. Exact version pinned because autobean uses Lark internals. No optional native regex backend required by this experiment. | Shared runtime parser dependency; revalidate on upgrades. |
| Official `beancount` **3.2.3** | Full desktop loader, booking, validation and inventory calculations. Depends on click, python-dateutil and regex; parser is a native `_parser...so` here. Published wheels cover Linux/macOS/Windows, not Android. | Desktop development/test oracle only; **not** in APK dependencies. |

These are tested versions, not a claim of full Beancount 3 conformance. Source
inspection confirmed the official loader unconditionally imports its native
parser. Many inventory/booking modules are Python, but a supported complete
pure-Python loader/engine was **not verified** among these candidates.

### Official engine on Android

`uv run inv probe-beancount-android` performs pip's binary-only dry run for
CPython 3.14 / `android_24_arm64_v8a`, checking PyPI and Flet's wheel index.
It returns the expected failure: **No matching distribution found for
beancount==3.2.3**. PyPI release inspection also showed no Android wheels.
Linux manylinux wheels cannot substitute for Android/Bionic wheels.

The upstream source build uses Meson, a C toolchain and Flex/Bison-generated
parser sources. No source cross-compilation was attempted: Android wheels for
the chosen CPython/ABIs, dependency compatibility and device validation would
be a separate native-packaging effort. Alternatively, translating lossless
models to official engine directives and repackaging selected Python modules
would require substantial semantic/integration work, not a drop-in supported
engine. Neither route is implemented or claimed successful here.

## Fixtures and verified behavior

Six small [synthetic files](../src/app/fixtures/beancount) are bundled:

- `main` includes `accounts`, `january`, `february`: 11 accounts, 14 transactions,
  opening balances, salary, expenses, UAH/USD/EUR, cash transfers, conversions
  with `@` and `@@`, fees, loan receipt/principal/interest, refund and split expense.
- IDs and ISO timestamps are **custom metadata conventions** (timestamps remain
  strings, not native intraday Beancount transaction dates). Tested metadata:
  strings, bool, date, Decimal, account token; tags, links, posting metadata,
  inline/block comments and descriptive custom/event/note/price directives.
- `advanced`: plugin declaration, inherited tag/metadata, inferred amount,
  cost lot, pad and balance assertion. Official engine loads it without errors;
  lossless parsing/editing preserves it; our small calculator **refuses** it.
- `unknown`: unknown dated `quantum` directive, rejected by both candidates and
  official Beancount. Rejection produces no rewritten output.

The [shared experiment](../src/app/beancount_diagnostics.py) does only syntax, editing and
explicit posting-quantity aggregation using `Decimal` (local precision 50).
It sums units by **account and currency**, with half-open date ranges
`[start, end)`. Price annotations are preserved, **not used for valuation**.
No currency mixing, floating-point arithmetic, inferred postings or lot-booking
rules are implemented.

Desktop comparisons use official `loader.load_file` (zero errors for both valid
books) and `ops.summarize.balance_by_account`. All account/currency balances and
period totals, account declarations, transactions and tested metadata agree.
Official booking may reorder postings by currency; comparisons use account/unit
identity rather than assuming source order. Autobean's account-valued metadata
is a typed `Account` token, while official metadata is a string; tests explicitly
normalize that known model difference.

| Result | Shared Decimal result = official Beancount 3 |
| --- | --- |
| Closing Bank / Cash | 47180 UAH / 1750 UAH |
| Closing USD / EUR | 150 USD / 85 EUR |
| Loan liability | -4000 UAH |
| January net expenses / signed income | 1120 UAH / -30000 UAH |
| February net expenses / signed income | 400 UAH / -10000 UAH |
| After appending 12.34 UAH expense | Bank 47167.66 UAH; Food 1112.34 UAH |

Credit-sign income is negative, per Beancount conventions. The refund reduces
expenses. These are **unit balances/movements**, not a converted net-worth report.

## Safe writing and editing scope

- A new synthetic transaction is built with autobean's model/Decimal APIs and
  appended without reserializing the original text. Tests write this to temporary
  files and re-load it with the official engine. Android checks the same operation
  in memory only. Append preserves even unknown text but cannot make invalid
  input valid; it is not validation.
- Existing transaction narration is edited by a unique `id`. Tests compare the
  entire result to a single expected string replacement: comments, whitespace,
  metadata, tags, include lines and unsupported accounting directives are
  otherwise identical. LF and CRLF preservation are tested. Missing/duplicate
  IDs and unknown syntax explicitly refuse editing.
- No complete rewriter, arbitrary field editing, merge, file mutation action or
  new storage integration was added. A future save must still use Stage 1
  conflict checks/backups and validate the resulting book.
- The calculator refuses plugins, pad/balance directives, inherited context,
  cost lots and missing/partial posting amounts, even outside the requested
  period. Includes resolve only literal relative names in an explicit in-memory
  bundle; missing files, cycles, absolute/traversal paths and globs fail explicitly.
  Production SAF include traversal is **not** implemented.
- Neither pure parser validates accounting. A regression deliberately supplies
  an unbalanced transaction: parsers and unit summation accept it; the official
  engine rejects it. This is an important boundary, not a complete engine PASS.

## Commands and results

All project commands ran through uv/Invoke; package additions used `uv add`.

| Command | Result |
| --- | --- |
| `uv add 'beancount-parser==1.2.3' 'autobean-refactor==0.3.1' 'lark==1.3.1'` | Pure runtime candidates added and locked |
| `uv add --dev 'beancount==3.2.3'` | Desktop oracle installed |
| `uv run inv probe-beancount-android` | Initially missing pip; after `uv add --dev 'pip>=25,<27'`, expected no Android binary distribution |
| `uv sync` | Successful; lock retained |
| `uv run inv probe-beancount` | Basic/advanced/unknown capability matrix; official main: 30 entries, 0 errors; advanced: 18 entries, 0 errors; unknown: 1 lexer error |
| `uv run inv beancount-diagnostics` | Bounded experiment PASS on Linux CPython 3.13.15 |
| `uv run inv format check` / `uv run inv check` | Final format/lint/Pyright pass; **94 tests passed**, including 28 new parser/packaging tests and existing storage regressions |
| `uv run inv build-android` | Build 3 succeeded; then rebuilt with grammar-resource extraction (below) |
| `uv run inv check-beancount-apk` | Initial APK failed grammar-extraction check; final APK passed and its exact Python payload executed successfully on host CPython 3.14.7 |
| `aapt dump badging`, `apksigner verify --print-certs`, `sha256sum` on final APK | Correct txncount identity/build 3, same debug signing certificate, checksum below |
| `/home/pioneer/Android/sdk/platform-tools/adb devices -l` | No connected Android device; no phone parser run |

Flet MCP verified Button/Text APIs and build CLI; documentation search for async
handlers had no indexed results, so the pinned imperative event pattern was reused.

### Android packaging finding

Flet 1.0.1 uses zipimport for pure packages on Android. Both parsers open `.lark`
grammar files through **filesystem-relative `__file__` paths**, which fails when
their code/data exist only in a ZIP. Lark also loads grammar resources.
The first compiled APK revealed this issue during payload inspection.

The supported `[tool.flet.android].extract_packages` setting now lists
`autobean_refactor`, `beancount_parser`, `lark`. Final APK inspection proves these
packages and grammars are in `assets/extract.zip`, not just sitepackages.zip.
All six fixtures and compiled action code are in `assets/app.zip`.
`check-beancount-apk` checks this layout and runs those **exact APK archives**
with Flet's host CPython 3.14.7, including zipped application-resource reads.
This provides packaging/Python-version evidence, **not Android runtime evidence**.

Official Beancount, regex and pip remain development-only and are not required
by the button. Android packaging succeeded for ARM64, ARMv7 and x86_64. Windows
wheels exist for the official oracle, but Windows execution was not tested.

## Recommendation and remaining effort

1. **Use Beancount 3 text as the candidate interchange/source format**, with
   autobean-refactor for source-aware parsing and narrow lossless edits. Allow
   roughly **2-4 days** to integrate a deliberately restricted format profile,
   source addressing and the already-tested save protections after phone success.
2. For **general accounting**, prefer the official engine over recreating its
   interpolation, balancing tolerances, inventories, booking, plugins and cost
   semantics. Keep it as the desktop validator/oracle. Next investigate building
   supported Android wheels: estimate **3-7 engineering days for initial
   feasibility**, **1-2 weeks** including ABI/device hardening, with uncertainty.
   Review upstream GPL-2.0-only distribution obligations before production.
3. A shared pure-Python layer is practical for **explicit-unit balances and
   bounded period totals**. A complete shared pure-Python Beancount 3 engine is
   **not established** by this PoC. Do not promote this accumulator to one.
   If Android native packaging is unacceptable, explicitly choose a restricted
   accounting profile or reassess architecture; a parser-to-official-Python-core
   adapter would need several weeks of conformance work, not this small PoC.

The immediate phone-execution blocker was resolved by the user-confirmed PASS.
General engine/native packaging limitations above remain. Stage 3's restricted
expense journal is documented separately in [MVP 0.1](mvp-0.1.md).

## Historical Stage 2 APK and phone test

These build 3 results describe the successful PoC. The output path is reused by
newer builds; use [MVP 0.1](mvp-0.1.md) for the current APK identity and checklist.

- [build/apk/txncount.apk](../build/apk/txncount.apk), approximately 135 MiB.
- txncount 0.1.0, build **3**, package `com.pioneer.financepoc`, Android 7.0+.
- SHA-256: `d1dde3d771bc5df82d08e840dfccba5e5f97128110264760f4971761dd1ef8a7`.

1. Open txncount and tap **Run Beancount diagnostics** (under **Technical diagnostics** in MVP).
   No folder selection
   or real files are needed. Allow several seconds for parser construction.
2. Expect **PASS: bounded Beancount fixture experiment**, versions, 4 files /
   11 accounts / 14 transactions, metadata, balances, period totals, lossless edit,
   append and explicit unsupported/unknown-feature diagnostics.
3. Repeat once; confirm the same results and that existing Storage PoC controls
   still work. Its selected folder/draft are not used by the parser experiment.
4. Share PASS/FAIL text, phone model/Android version and logs on failure:
   `adb logcat -v time -s flet.python flutter AndroidRuntime`. Do not share real
   financial contents. Unexpected failures show FAIL and retain traceback logging.

Even a phone PASS certifies only this **bounded fixture profile**, not arbitrary
Beancount books or availability of the official Android engine. The user has
confirmed this bounded test passed; no commits or pushes were made.

Sources: [LaunchPlatform parser](https://github.com/LaunchPlatform/beancount-parser),
[parser 1.2.3 metadata](https://pypi.org/pypi/beancount-parser/1.2.3/json),
[autobean-refactor](https://github.com/SEIAROTg/autobean-refactor),
[autobean 0.3.1 metadata](https://pypi.org/pypi/autobean-refactor/0.3.1/json),
[official Beancount](https://github.com/beancount/beancount),
[official 3.2.3 wheel metadata](https://pypi.org/pypi/beancount/3.2.3/json),
[Flet wheel index](https://pypi.flet.dev/),
[pinned Flet packaging source](https://github.com/flet-dev/flet/blob/v1.0.1/sdk/python/packages/flet-cli/src/flet_cli/commands/build_base.py).
