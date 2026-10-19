# TxnCount branding

The canonical [assets/icon.png](../assets/icon.png) is original minimal artwork:
a green folder and white document on a dark blue background, 1024 x 1024 PNG.
It replaces the template's crossed square and is intentionally temporary.
Regenerate it with `uv run inv app-icon`; validate with `uv run inv icon-check`.
There are no maintained duplicate platform icons. Invoke stages the root assets
for Flet, which generates launcher/window icons and splash screens.

Product name: **TxnCount**. Package/bundle: `com.pioneer.txncount`, starting with
0.1.1 (13). This intentionally differs from the original experimental Android
identity; see the [migration checklist](../README.md#one-time-migration-from-the-original-android-app).
The new Android app must be granted folder access again; shared journals remain
unchanged. Future releases must retain the new identity and signing key.
The generated/installed Android icon and display name still need phone inspection;
the canonical PNG has been visually inspected. No public release branding,
store signing, or production identity work is implied.
