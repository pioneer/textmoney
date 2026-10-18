---
name: implement-feature
description: "Implement a scoped application feature with imperative Flet UI, Flet-free domain logic, explicit service boundaries, and regression tests."
---

# Implement Feature

Read [architecture](../../docs/architecture.md). Define observable acceptance
criteria, locate the controlling code, and make the smallest useful change.
Keep decisions in domain/services and external I/O behind an adapter when needed.
Do not add empty architecture or a framework for a single operation.

For Flet changes, follow [flet](../flet/SKILL.md). Await async APIs; avoid blocking
handlers and own background tasks/cancellation. Provide loading, empty, success,
and failure behavior when the feature introduces those states.

Use uv for justified dependencies and retain uv.lock. Add tests at the behavior
boundary, including failures and safe logs. Update canonical docs when contracts
change. Run focused tests, an appropriate UI/platform smoke check, then
`uv run inv check`. Report unsupported or untested target constraints explicitly.