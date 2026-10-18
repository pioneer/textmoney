# Debugging

## Reproduce First

Record the target, Python/Flet versions, task, smallest reproduction, expected
result, and actual behavior. Run `uv run inv doctor`, then the appropriate task:

```sh
uv run inv debug
uv run inv debug-web --port=8550
uv run inv debug-android --device=DEVICE_ID
uv run inv debug-ios --device=SIMULATOR_OR_DEVICE_ID
uv run inv logs-android --device=DEVICE_ID
```

Mobile debug uses official `flet debug`, not the desktop companion app. It needs
Flutter and the platform SDK/device; iOS requires macOS. Enable Android developer
mode/USB debugging and authorize the host. Start an emulator/simulator or connect
a physical device. Use `--list-devices` on a debug task to discover targets. The
task supplies a discovery-only ID because Flet 1.0.1 checks for an ID before
listing; listing does not launch that ID. `logs-android` uses `adb` from `PATH`,
then `ANDROID_HOME`/`ANDROID_SDK_ROOT` or the SDK directory Flet installs, so it
works after any Android build or debug session has provisioned the SDK.

Desktop/web debug sets `APP_DEBUG=1`, unbuffered output, and Flet CLI `-v`.
Mobile debug retains Python app source for traceback lines. Mobile Python does
not inherit arbitrary host variables, so the task stages an
ignored `src/app/.debug-mode` resource while packaging, then removes it even on
failure. The installed debug app retains its marker until rebuilt/reinstalled.
Release build tasks remove any stale marker. An explicit runtime `APP_DEBUG=0`
overrides it. Do not run build/debug tasks concurrently in one checkout.

Normal tasks set `APP_DEBUG=0`: app INFO, Flet WARNING. Debug uses app DEBUG
without enabling global DEBUG. Flet, its transport/web loggers, raw patches, and
HTTP libraries stay at WARNING: Flet's effective level also controls Uvicorn,
whose DEBUG WebSocket logs include raw headers. Do not use `-vv` with private
data. If a Flet DEBUG record reaches the app formatter it retains its origin but
omits payloads. CLI verbosity is a separate process and is not covered by the app
formatter: inspect/redact captured CLI logs before sharing them.

## Exceptions and Logs

Application logging uses stdlib `logging`, timestamp, level, logger name, and
stderr. Python's main/thread hooks and the event loop exception handler preserve
tracebacks. `page.on_error` logs errors reported by the Flet client. Python event
handler failures are a different path: Flet logs them with traceback on its
`flet` logger and reports the failed session. Tests exercise real event dispatch.
Caught integration errors still need an explicit boundary policy; global hooks
cannot report exceptions your code swallowed or never observed.

| Target | Where to look |
| --- | --- |
| Desktop development | Terminal running `debug` |
| Live web (`debug-web`) | Python traceback in terminal; browser DevTools for client/network errors |
| Static web (`build-web`, `serve-web`) | Browser DevTools Console/Network; Python runs in Pyodide, no Python server terminal |
| Android packaged/debug | `uv run inv logs-android`; underlying `adb logcat -s flet.python` |
| iOS simulator | `xcrun simctl spawn booted log stream --style compact --predicate 'sender == "dart_bridge"'` |
| iOS physical device | Console.app device selection, or Xcode Devices and Simulators, Download Container |
| macOS packaged | `log stream --style compact --predicate 'sender == "dart_bridge"'` |
| Packaged native apps | Flet `console.log`; exact location is runtime `FLET_APP_CONSOLE` |

Packaged macOS redirects Python output even when launched from a terminal.
Unsandboxed macOS commonly uses `~/Library/Caches/<bundle-id>/console.log`;
sandboxed paths differ. On iOS inspect the app container's Library/Caches.
Android's private file needs privileged access; prefer non-root logcat. Packaged
Linux apps write `~/.cache/<bundle-id>/console.log` (verified with Flet 1.0.1) and
also echo it to the launching terminal. On Windows find the console file using
Flet's runtime path rather than assuming a working directory. Flet's async `StoragePaths.get_console_log_filename()` is an
optional future in-app diagnostic mechanism, not a dependency of this template.
Verify its API through MCP before adding it.

## Integration Boundaries

Add logging where an operation crosses into a service, SDK, network, filesystem,
or subprocess. Useful context: safe service/operation name, HTTP method, route
template (not a user URL), status code, duration, retry count, and an approved
non-sensitive request correlation ID. For unexpected exceptions use
`logger.exception("service=catalog operation=load failed")` inside the except
block and re-raise or explicitly translate to a tested failure result. Set
timeouts and document cancellation/retry behavior.

Never log passwords, tokens, cookies, authorization headers, signed/query URLs,
raw requests/responses, environment dumps, customer records, or private control
values. The central formatter defensively redacts secret labels, known secret
environment values, auth headers, URL credentials/queries/fragments, including
formatted tracebacks. It cannot recognize arbitrary private data or every secret
encoding. Use safe exception messages too; redaction is defense in depth, not a
data classification system. Traceback source lines may contain literals, so never
put secrets in source.

Find the failing boundary, add a regression test, repair the cause, reproduce
again, and finish with `uv run inv check`. Do not add retries or catch-all handlers
to make symptoms disappear. Never use `except Exception: pass` or return fake
success after an unknown failure.