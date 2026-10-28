# Flet and MCP

This template pins Flet 1.0.1 and uses imperative controls with `ft.run(main)`.
Do not copy deprecated APIs or introduce `@ft.component`.

[.vscode/mcp.json](../.vscode/mcp.json) starts the installed server with
`uv run flet mcp` from the workspace. Run `uv sync`, trust the workspace/server in
VS Code, and start its `flet` MCP server. API, icon, example, documentation, and
CLI tool groups are enabled with the server's verified environment flags. Other
agents can use the same stdio command and flags; no credentials are required.

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
cross-checked with installed 1.0.1 source and official publishing docs. Re-verify
after upgrades instead of treating this list as permanent API documentation.

Flet packaging expects `src/assets`, while this repository maintains root
`assets`. Invoke stages the copy. Hash routing is configured both for runtime
and packaged web; prefix builds pass the matching base URL. Universal icon
background is `[tool.flet].icon_background`. Flet generates platform-specific
icons from `icon.png`; see [branding](branding.md).

Sources: [Flet documentation](https://flet.dev/docs/),
[publishing](https://flet.dev/docs/publish/),
[MCP cookbook](https://flet.dev/docs/cookbook/flet-mcp).

## Required popup and Escape convention

Every new or changed popup must preserve desktop **Escape = Cancel** behavior.
This is a requirement for future features, not only the current transaction
forms:

- Present application-owned modal `AlertDialog` controls through
  `app.ui.dialogs.show_dialog`, not directly through `page.show_dialog`.
- Put the non-destructive **Cancel**, **Close**, or **Keep editing** `TextButton`
  first in `actions`, with its normal click handler. Escape must invoke that
  same handler; it must never choose Save, Delete, or another destructive action.
- If an operation disables cancellation, disable that button so Escape is
  disabled too. Do not bypass validation, busy-state guards, or draft handling.
- Escape must affect only the active popup/menu/picker. Closing a nested popup
  must preserve the parent form and its unsaved input.
- Nonmodal dialogs and native date/time pickers may use Flutter's native
  dismissal, but verify the same active-popup-only behavior. Do not install a
  page-wide hardware-key handler for popup cancellation.
- Add focused regression coverage and run the
  [desktop popup checklist](testing.md#desktop-popup-cancellation), including
  keyboard focus in inputs and action buttons, nesting, and disabled Cancel.

The shared helper adds `KeyboardListener` controls around content and actions.
Listeners follow the dialog's focus tree, so dropdown menus and nested pickers
can consume Escape without also closing the parent. Cancellation preserves the
button's disabled state and normal Flet click lifecycle. Flet 1.0.1 has no public
programmatic button-click dispatch; the helper uses `button._trigger_event`.
Regression tests exercise real sessions and protocol serialization; recheck
this compatibility boundary on a Flet upgrade.

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

## Beancount action

Stage 2 verified Button `on_click`, Text `selectable` and build CLI through MCP.
The existing imperative async-handler pattern runs the fixture experiment via
`asyncio.to_thread`; errors are shown/logged without touching storage state.
Android packaging uses the supported `tool.flet.android.extract_packages`
setting, verified against build CLI/installed 1.0.1 source and actual APK contents,
because the parser libraries read grammar data using filesystem paths.