# Repository Conventions

- Read [docs/architecture.md](docs/architecture.md) before architectural changes.
  Keep this a small reusable template, not a feature demo or empty enterprise scaffold.
- Use uv for Python/dependencies and `uv run inv ...` for project operations.
  Invoke is the only task runner. Keep configuration in pyproject.toml and retain
  uv.lock. Do not introduce requirements.txt, other package managers, shell task
  wrappers, Makefiles, or alternative task runners.
- Use imperative Flet 1.x. Before changing Flet code, query the configured Flet
  MCP for exact APIs, event types, enums, icons, examples, CLI, and packaging
  settings. Await async methods in async handlers. Do not guess from older Flet.
- Keep domain code Flet-free. UI calls domain/services; integrations belong behind
  service/infrastructure boundaries introduced only when needed. Avoid import
  side effects and hidden global mutable state.
- Never swallow integration failures. Use explicit error semantics and useful
  traceback-preserving logging. Never log credentials, raw headers, private
  payloads, or sensitive URLs; defensive redaction is not permission to log them.
- Add focused tests for changed behavior and failure paths. Run format, lint,
  Pyright, and tests through Invoke. Finish with `uv run inv check`; disclose
  untested platforms or blocked checks.
- For a new app, replace names/identifiers and assets/icon.png. It is the only
  maintained 1024-square icon source; Flet generates platform icons. Do not add
  duplicate icon sets or mandatory splash artwork. Never ship the template icon.
- Read the relevant local skill before working: [initialize-app](skills/initialize-app/SKILL.md),
  [implement-feature](skills/implement-feature/SKILL.md), [fix-bug](skills/fix-bug/SKILL.md),
  [debug](skills/debug/SKILL.md), [review](skills/review/SKILL.md), or
  [flet](skills/flet/SKILL.md). These root skills are explicitly routed here;
  agents need not assume their editor auto-discovers this directory.
- Never commit secrets, generated output, signing keys, or local environments.
  Do not commit or push unless explicitly requested. Preserve unrelated work.