---
name: debug
description: "Diagnose Python/Flet desktop, web, Android, or iOS failures using debug tasks, platform logs, traceback evidence, and sanitized integration context."
---

# Debug

1. Read [debugging](../../docs/debugging.md). Capture target/versions, a minimal
   reproduction, expected and observed results. Run `uv run inv doctor`.
2. Use `debug`, `debug-web`, `debug-android`, or `debug-ios` through Invoke. For
   mobile select the actual device; follow platform prerequisites and log routes.
   Static web failures belong in browser Console/Network, not the server terminal.
3. Identify the failing UI/service/infrastructure boundary. Capture the original
   traceback and safe operation/request metadata. Do not dump environment,
   credentials, headers, signed URLs, payloads, or private user data. Sanitize
   CLI logs too; they do not use the application formatter.
4. Verify APIs/CLI through MCP where relevant. Test a specific hypothesis with
   the cheapest discriminating check; do not add broad speculative catches.
5. Fix the cause, add a regression test, rerun the reproduction, and remove
   temporary instrumentation. Finish with `uv run inv check` and report evidence
   and remaining limitations, not assumptions as facts.