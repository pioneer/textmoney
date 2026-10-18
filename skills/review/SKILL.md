---
name: review
description: "Review changes for correctness, architecture, failure handling, secret-safe logs, branding, platform compatibility, tests, typing, and dependency discipline."
---

# Review

Read [architecture](../../docs/architecture.md) and inspect the diff plus nearby
callers/tests. Prioritize concrete bugs, regressions, security/privacy risks, and
missing failure tests over stylistic preferences. Do not silently edit a review.

Check UI/domain/service boundaries, async lifetimes/cancellation, exception
propagation, traceback preservation, log content, credentials, config defaults,
platform SDK/wheel/browser constraints, and destructive deployment destinations.
Verify Flet symbols against MCP rather than older examples.

For app initialization/release, check names, IDs, background, canonical icon,
generated platform branding, and placeholder status. Reject unnecessary icon sets.
Review uv runtime/dev separation and lock changes; no alternate task runners or
unjustified SDKs/HTTP libraries. Check tests, Ruff, and Pyright through
`uv run inv check` where execution is authorized.

Report findings first, with severity, concrete file locations, impact, and a
specific correction. Say when no findings are identified and disclose test or
platform gaps. Never equate build success with store/device certification.