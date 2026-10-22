# Static Web Deployment

`run-web` serves a live Python process for development. `build-web` creates a
different deployment: static Flutter/Pyodide files, with Python executing in the
browser. No Python server or backend credentials belong in that bundle.
All packaged source/configuration is publicly downloadable. Browser sandbox,
CORS, supported wheels, storage quotas, and secure-context rules still apply.

```sh
uv run inv build-web
uv run inv serve-web
```

Open http://localhost:8000. Inspect Console/Network for Python/client exceptions,
failed assets, MIME types, and CORS errors. For subdirectory hosting build with
`uv run inv build-web --base-url=/my-app/` and serve it at that same URL prefix;
the simple root `serve-web` preview is for root builds. Hash routes avoid requiring
server rewrites for application routes. They do not repair a wrong asset base URL.