---
name: flet
description: "Verify and implement current Flet controls, services, events, enums, icons, async methods, CLI commands, and packaging through the configured Flet MCP server."
---

# Flet

Read [Flet workflow](../../docs/flet.md). Use the workspace stdio server configured
in .vscode/mcp.json; synchronize with uv first. This project uses imperative Flet
1.x, never `@ft.component`.

Call `get_api` first for a known class and inspect the exact member/type. Verify
enums and icon names with the dedicated tools. Search examples/docs, then retrieve
the matching full content. Empty searches are not verification: consult official
version-matching docs and installed source and report the fallback. Use CLI help
for flags and verify packaging metadata against a real build.

Await methods marked async inside async handlers. Add non-core packages through
uv only when the MCP package field requires them and target wheels are available.
Preserve Flet-free domain code and use the existing logging/error boundaries.
Validate changed event behavior, generated icons/assets, and affected platforms.
Finish with `uv run inv check` and disclose any untested target.