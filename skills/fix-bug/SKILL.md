---
name: fix-bug
description: "Fix reproducible defects at their root cause using a failing regression, narrow changes, explicit failure semantics, and verified checks."
---

# Fix Bug

Read [architecture](../../docs/architecture.md) and [debugging](../../docs/debugging.md).
Reproduce the problem and state one falsifiable cause. Follow the nearest
controlling boundary, inspect the actual traceback/state, and add a regression
that fails for the original defect.

Repair the cause without unrelated refactoring. Verify Flet contracts through MCP
when involved. Preserve tracebacks and explicit errors. `except Exception: pass`,
bare silent catches, broad suppression, fake success returns, and retries hiding
unknown failures are prohibited. Catch only when implementing a defined recovery,
translation, or logging-and-reraise policy.

Rerun the regression and original reproduction. Check no credentials/private data
were added to logs or fixtures. Update docs only if behavior changed. Finish with
`uv run inv check` and state any remaining platform/reproduction limitation.