# Flet and MCP

The app pins Flet 1.1.2 and uses imperative controls with `ft.run(main)`.
Do not copy deprecated APIs or introduce `@ft.component`.
Keep the runtime, `all` extras, MCP, and local SAF extension's Python and Dart
dependencies aligned. Their pins and locked packages are checked by tests.
Desktop startup restores the last normal window size and its normal, maximized,
or full-screen state from device-local preferences before revealing the native
window. Mobile preview keeps its 390-pixel launch width and does not restore
desktop window settings. Normal dimensions are saved on resize rather than on
close, because Flet's close event can report stale window dimensions. The saved
mode is sent with the reveal update, avoiding stale native state between
separate maximize and show updates.
The Flutter pubspec pins `jni` 1.1.2 and `jni_flutter` 1.0.4+1 together.
JNI 1.2.0 was retracted after causing an Android startup crash, and
`jni_flutter` 1.1.0 requires JNI 1.2.0 or later. See
[flet-dev/flet#7105](https://github.com/flet-dev/flet/issues/7105).

[.mcp.json](../.mcp.json) configures Copilot's `flet` stdio server with
`uv run flet mcp`. Run `uv sync` and start Copilot from the repository root so
`uv` finds the project; the configuration does not set a working directory.
Trust the workspace/server when prompted. API, icon, example, documentation, and
CLI tool groups are enabled with the server's verified environment flags.
To verify the connection, ask Copilot to call `get_api("Button", member="on_click")`.
Other agents can use the same stdio command and flags; no credentials are required.

Before implementing a Flet change:

1. Call `get_api(name)` for the exact control/service/event/dataclass. A not-found
   response is definitive; do not invent the symbol. Narrow by member/query.
2. Check enum members with enum tools and icon names with icon search.
3. Search examples/docs first, then retrieve the matching full example/doc.
   Search may return no indexed documentation; use official version-appropriate
   docs and the installed package as a fallback, explicitly noting the limitation.
4. Use `get_cli_help` before changing command flags. Verify packaging metadata
   against current publishing docs/installed CLI, then run a real build.
5. Await methods marked async in an async handler. If the API's package is not
   `flet`, add that actual dependency and verify its target-platform support.

The initial implementation was checked through a real stdio MCP session: Page
properties/events, Text/Image/Column/SafeArea, ScrollMode, RouteUrlStrategy, the
hello-world example, and run/debug/build/serve/doctor commands. Documentation
search returned no matches in this environment, so packaging details were
cross-checked with installed 1.0.1 source and official publishing docs. The
1.1.2 upgrade re-verified the dialog stack, keyboard listeners, text-field
submission, and build CLI. Its MCP API index did not list `Theme.surfaces` or
`ThemeSurfaces`; the installed 1.1.2 package confirmed both. Documentation
search still returned no matches, so the 1.1.0 and 1.1.2 changes were checked
against the official changelog. Re-verify after upgrades instead of treating
this list as permanent API documentation.

Flet packaging expects `src/assets`, while this repository maintains root
`assets`. Invoke stages the copy. Hash routing is configured both for runtime
and packaged web; prefix builds pass the matching base URL. Universal icon
background is `[tool.flet].icon_background`. Flet generates platform-specific
icons from `icon.png`; see [branding](branding.md).

Sources: [Flet documentation](https://flet.dev/docs/),
[publishing](https://flet.dev/docs/publish/),
[MCP cookbook](https://flet.dev/docs/cookbook/flet-mcp).

## Desktop pointer feedback

On desktop and desktop web, standard button themes use the click cursor when
enabled and the basic cursor when disabled. The daily summary supplies the same
state-aware cursor because its local theme replaces the page's text-button
theme. The Add transaction button has a stronger native hover overlay and
elevation, while retaining its gradient and existing touch and focus feedback.
Mobile and mobile preview keep their previous behavior.

Flet 1.1.2 does not expose a cursor setting for `NavigationRail` destinations.
Desktop layouts use native text buttons for the rail instead, retaining selection
semantics, keyboard focus, and clipped button feedback. The rail and its buttons
remain mounted during navigation to preserve keyboard focus. Mobile retains the
original navigation controls.

## Extension-enabled web development

The stock Flet web client does not contain local extensions. Run
`uv run inv build-web-client` once and whenever the extension sources, lockfile,
or manifest change, then use `uv run inv run-web` or `uv run inv debug-web`.
The build requires the Flutter SDK and the same web build dependencies as
`uv run inv build-web`. It copies the compiled Flutter web client and restores
Flet 1.1.2's unconfigured index, so the dynamic server can patch it for its
WebSocket rather than start Pyodide. Invoke sets `FLET_WEB_PATH` to this client
and refuses a missing or stale client. Python changes still hot reload.
`uv run inv build-web` and `uv run inv serve-web` remain the separate static
Pyodide preview.

The extension API is `from browser_history_bridge import BrowserHistory`.
Construct `history = BrowserHistory()` inside the page's `main(page)` or an
event handler: Flet registers the service automatically with that page. Keep
the instance for the session, then `await history.back()` to request one browser
Back. Construction outside a page context does not register it. The await
acknowledges the request, not completion of navigation;
the browser may have no previous entry or may leave the app. Calling it on a
non-web client raises an error. It does not manage application screen history.

Desktop popup keyboard handling also needs the local `popup-keyboard` extension.
`uv run inv run` and `uv run inv debug` automatically build the host client when
it is missing or stale after extension or dependency changes. This requires the
host build toolchain and may take several minutes; a failed build stops the run
instead of launching the stock client without the fix. Flet's unattended build
may install missing SDKs. Ordinary Python edits still hot reload. An explicit
`uv run inv build-local` builds just the host bundle; `uv run inv build-linux`
also packages the Linux AppImage.
Linux retains its isolated branded
development client and icon, now derived from the extension-enabled build.
The icon search suggestions use this extension for route-scoped Up/Down focus
navigation. Enter retains native button activation; nested popups keep their own keys.
The native search route unfocuses its enclosing scope on dismissal. The popup
scope restores focus to the current dialog when no child retains it, so Escape
can cancel the dialog after dismissing or selecting a search suggestion.
If the popup extension's Python API changed, refresh its local wheel with
`uv sync --locked --reinstall-package popup-keyboard` before running checks.

## Flet 1.1.2 upgrade verification

The runtime, CLI, desktop/web clients, platform assets, MCP and local extension
pins were upgraded together from 1.0.5. Flet 1.1.0 changes where Dart Material
and Cupertino APIs are imported, but the local SAF extension uses neither
library. Flet 1.1.0 changes seeded theme surfaces from tonal to neutral. The
dark theme now explicitly keeps tonal surfaces to preserve the app's previous
palette; the light theme already defines its surfaces explicitly. Flet 1.1.2
fixes web reload/close errors while the app's `main()` is still running and
reduces initial web font loading.

- `uv sync --locked` and `uv run inv verify-flet` passed with 1.1.2.
- `uv run inv check`: 2,225 passed, one native-Windows-only test skipped.
- `uv run inv build-android` and `uv run inv check-beancount-apk` passed;
  the APK is `build/apk/textmoney-0.4.9.apk`.
- The SAF extension still compiles without importing the Material/Cupertino
  Dart libraries affected by 1.1.0. Android runtime behavior and interactive
  desktop/browser smoke tests were not run for this upgrade.

Historical validation sections below retain their original versions.

## Home balance cards

On compact Android/iOS displays, Home currency balance cards fill the same
content width as Accounts, retaining the shared 16-pixel padding, border and
12-pixel corners. Resizing or refreshing recalculates their width. Desktop
cards at every window width and medium/expanded mobile layouts retain the
existing 250-pixel wrapping layout.

## Category menu hierarchy

Category menus and the main category tree share 16-pixel hierarchy gutters and
1.25-pixel Material
`OUTLINE_VARIANT` connector lines. Ancestor guides continue through descendants;
the final sibling gets a rounded elbow and its stem stops at the row centre.
Mobile category menu rows draw their guides in Flutter at the rendered row
height, without sending per-option resize events to Python when the menu opens.
Category menu rows match native account menu density: 40 pixels on desktop and
48 pixels on mobile, independent of window width, keeping guides joined.
Child stems start at the
boundary beneath the parent row, independent of its icons or wrapped label.
Existing entity icons are unchanged. Menus have no expand/collapse controls;
the main tree retains its native expansion behavior. Category selectors in
transactions (including fee categories), budgets, category parents, filters and
default-fee settings use a 672-pixel height limit on compact screens and the
default full-height menu on wider screens. Transaction and budget menus retain
name-only labels; category-parent menus in creation and code/parent-editing
dialogs show names and full codes. These rows wrap without a line limit and grow
to fit their content instead of using a fixed height.

Budget card titles and their entity icons align with the first 48-pixel action
row, even when actions wrap below it. The title can grow for longer names.
The percentage uses the normal spacing after the financial amounts, with an
12-pixel gap below it before the progress bar.

Compact transaction and budget menus overlap their category field, extending
above and below it rather than opening only downward. A bounded field measurement
and the option row heights determine the menu alignment, including shorter lists;
Flutter retains screen-edge and keyboard-inset handling. Wide-screen positioning
is unchanged. The compact limit is 40% taller than the former 480 pixels, adding
96 pixels (20% of the former height) on each side when enough space and options
are available.
On mobile (Android/iOS and mobile preview), compact category menus open with
their top edge 20% down the usable screen, measured below safe-area insets and
above the software keyboard. The local popup extension measures the native
field's position and caps the menu to the remaining space. Long lists still
scroll inside the native menu. All these mobile menus use the same usable-screen
width with 16-pixel side margins inside the safe area, independent of the field
width or label length. Desktop and wide layouts are unchanged.

Mobile menu positioning preserves the dropdown's explicit visual style or the
active light/dark dropdown theme, overriding only alignment and padding. Category
and account menus therefore share their themed background, including system-theme
changes; light-mode menus remain white without forcing white in dark mode.

The main Accounting category tree retains its native expansion controls and
actions. Connector gutters replace, rather than add to, its child indentation;
ancestor stems span expanded subtrees while branches remain aligned with each
40-pixel tile header. Menu branches are eleven pixels long with three-pixel
rounded elbows, keeping a one-pixel gap before the existing content. Main-tree
branches remain eight pixels long.

## Required popup keyboard convention

Main transaction, account, category and budget add/edit forms share
`app.ui.dialogs.EditorDialog`. At compact widths (up to 599 logical pixels)
its zero-inset, square-cornered surface fills the available safe area. Wider
layouts retain the existing 360-pixel form width and themed dialog appearance,
with the body capped at 560 pixels to avoid stretching short forms across a
tall desktop window.
The title and Cancel/Save actions remain outside the scrollable body. The body's
preferred compact dimensions fill Flutter's constraints; the native dialog
already subtracts keyboard insets, so there is no hardcoded keyboard height.
The shell updates in place on resize, including while a child popup is open.
An editor first opened over another modal stays an ordinary popup, even after
resizing. Snackbars do not make a main editor nested. Delete confirmations and
other dialogs do not opt into this presentation.

The Flet MCP documentation search had no indexed results for this layout.
The implementation was verified against the pinned Flet 1.1.2 Dart client and
its Material dialog constraints, SafeArea and keyboard-inset handling.

Every new or changed popup must preserve desktop **Escape = Cancel** and
**Enter = Submit** behavior. This is a requirement for future features, not only
the current transaction forms:

- Present all application-owned `AlertDialog` controls through
  `app.ui.dialogs.show_dialog`, not directly through `page.show_dialog`.
- Put the non-destructive **Cancel**, **Close**, or **Keep editing** `TextButton`
  first in `actions`, with its normal click handler. Escape must invoke that
  same handler; it must never choose Save, Delete, or another destructive action.
- Put the **Save**, **Create**, **Delete**, or other primary `TextButton` second.
  Enter (including keypad Enter) must invoke its existing click handler, never a
  separate validation or persistence path. A single-action popup uses its
  Close/Keep editing action for Enter too. Dialogs with more actions need an
  explicit keyboard policy before extending the helper.
- The helper focuses the first input unless an input already requests autofocus.
  In multiline inputs, Enter submits and **Shift+Enter inserts a newline**.
  Reserve their `on_submit` handler for the shared helper.
- A single-line input can explicitly set `shift_enter=True` and its own
  `on_submit` when Enter must invoke an action other than the popup's primary
  action. The AI icon prompt uses this to generate without saving the picker.
- Disabled or hidden actions and disabled inputs must not be submitted. If an
  operation disables cancellation, disable that button so Escape is disabled
  too. Preserve validation, busy-state guards, conflicts and unsaved drafts;
  repeated keyboard events must not duplicate an in-flight submission.
- Both keys affect only the active popup. Closing or submitting a nested popup
  must preserve its parent form and unsaved input. Dropdowns, searchable trees,
  native date/time pickers and focused buttons keep their own keyboard
  activation; selecting an option must not also submit the parent form.
- Native date/time pickers retain Flutter's keyboard behavior. Do not install
  a page-wide hardware-key handler for popup shortcuts.
- Add focused regression coverage and run the
  [desktop popup checklist](testing.md#desktop-popup-keyboard-shortcuts), including
  valid/invalid inputs, action-button focus, nesting and disabled actions.

The shared helper adds `PopupKeyboardListener` controls (subclasses of Flet's
`KeyboardListener`) around content and actions.
Listeners follow the dialog's focus tree, so dropdown menus and nested pickers
can consume keys without also closing/submitting the parent. Action-button Enter
uses native activation, not a second synthetic click. The pinned Material
TextField client emits `submit` only with `shift_enter=True`: multiline and
explicitly configured single-line fields use that event; other single-line fields
use the focus-scoped listener. Input discovery
follows Flet's dataclass control fields, excluding Python-only state.
Flet 1.1.2 has no public programmatic button-click dispatch; the helper uses
`button._trigger_event` and checks the active dialog stack. Regression tests
exercise real sessions and protocol serialization; recheck these compatibility
boundaries and actual keyboard delivery on a Flet upgrade.

Flet 1.1.2's Material dropdown retains an internal Escape shortcut even after its
menu closes. The local popup keyboard extension uses a focus-scoped Flutter
action dispatcher to forward only a **closed** menu's dismissal intent to the
same listener's Escape handler. Open menus keep native Escape/Enter behavior.
Mouse clicks outside an input can also leave a dialog's route itself focused
after a nested date or icon picker closes. Only the content listener enables
focus recovery: if the active route has primary focus with no focused descendant,
the extension focuses a non-traversable Escape fallback. It does not replace
restored input/button focus or handle Enter, and never focuses an obscured route.
There is no page-wide hardware-key handler. The existing Python helper still
enforces active-dialog, disabled-action and busy-state guards.

On native Android, `show_dialog` also wraps dialog content containing dropdowns
in the local `PopupMenuBackScope`. Flet's Material dropdown menu is an overlay rather than
a dialog route, so the scope checks the dialog subtree for an open dropdown
menu and closes it before allowing Back to pop the dialog. With no open menu it
uses the navigator's normal pop checks, including other route guards. Nested
dialogs and native date/time pickers remain separate routes. Android's IME
handles Back before the application when appropriate; web and iOS are not
wrapped. The scope depends on the pinned Flet/Material dropdown widget structure
and blocks predictive route popping while the dialog is open; recheck it on upgrades.

## storage diagnostics verification

`uv run inv verify-flet` opens a real MCP stdio session and queries FilePicker
`get_directory_path`, Service, Page `run_task`/`on_disconnect`,
SharedPreferences, TextField `on_change`, Button `on_click`, and build CLI help.
Phase B also checked `BasePage.show_dialog/pop_dialog`, `AlertDialog` and
`TextButton.on_click` for modal conflict feedback.
Pinned source was also inspected for Service auto-registration, async invocation,
extension discovery and `dev_packages` resolution. The custom service necessarily
uses Flet's protected `_invoke_method` hook, following its official extension
pattern; its compatibility must be rechecked on Flet upgrades.

The standard directory picker is unsupported in web mode; see
[browser preview limitations](deployment.md). On desktop it returns a path-like
string but supplies no directory document
operations or persisted-grant inspection API. Android therefore uses the local
extension instead, with provider operations delegated to existing Flutter SAF
libraries. See [the storage report](android-storage-validation.md).
SAF grants access, not synchronization or offline availability; the selected tree
must meet the [local-folder requirements](synchronization.md#local-folders-and-sync-clients).

## Beancount action

Stage 2 verified Button `on_click`, Text `selectable` and build CLI through MCP.
The existing imperative async-handler pattern runs the fixture experiment via
`asyncio.to_thread`; errors are shown/logged without touching storage state.
Android packaging uses the supported `tool.flet.android.extract_packages`
setting, verified against build CLI/installed 1.0.1 source and actual APK contents,
because the parser libraries read grammar data using filesystem paths.