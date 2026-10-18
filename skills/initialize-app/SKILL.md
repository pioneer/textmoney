---
name: initialize-app
description: "Initialize a real application from this template: identity, package and bundle metadata, canonical icon, documentation, and release-readiness checks."
---

# Initialize App

1. Read [architecture](../../docs/architecture.md) and [branding](../../docs/branding.md).
   Establish product name, package slug, organization, stable bundle ID, target
   platforms, and branding direction. Ask only for missing decisions that matter.
2. Update project name/description/version and Flet product/company/org/bundle_id/
   build_number in pyproject.toml; update `APP_NAME` and the README. Keep the Python
   `app` package generic unless renaming solves a real need. Refresh uv.lock with uv.
3. Replace assets/icon.png with original project-specific 1024-square PNG artwork.
   Use image generation if available; otherwise make a simple project-specific
   temporary icon and explicitly document final branding as outstanding. Never
   keep the template X or substitute a Flet/third-party logo.
4. Set universal icon background; preserve full-canvas artwork/transparency where
   appropriate. Let Flet generate platform outputs. Do not add duplicate icon
   sets or mandatory splash artwork. Verify any packaging changes through MCP.
5. Run `uv run inv icon-check`, `uv run inv check`, and an available platform build.
   Inspect the generated/installed icons and visible product name, not just PNG
   dimensions. Confirm output does not contain the default Flet logo.
6. Search maintained source, metadata, and docs for template names, `com.example`,
   and `Template Organization`; eliminate accidental leftovers. Visually verify
   icon replacement. Update README status, platform prerequisites, and outstanding
   branding/signing work. Do not claim an unbuilt target is validated.
7. Finish with `uv run inv check`. Do not commit or publish without authorization.