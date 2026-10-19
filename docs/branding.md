# TxnCount branding

The canonical [assets/icon.png](../assets/icon.png) is original minimal artwork:
a rounded white text document with smooth dollar and euro symbols and a gold
pencil on a dark blue background, 1024 x 1024 PNG. The bold money symbols remain prominent
at launcher sizes, while text lines and the pencil indicate editable plain text.
The symbols use Bezier curves with supersampled rendering, without a font dependency.
Regenerate it with `uv run inv app-icon`; validate with `uv run inv icon-check`.
There are no maintained duplicate platform icons. Invoke stages the root assets
for Flet, which generates launcher/window icons and splash screens.

Linux source runs use a generated branded Flet client and hidden desktop entry;
see [development](development.md). Native X11 smoke testing verified the TxnCount
window title, development WM_CLASS and app icon pixels. Desktop-shell grouping
and Wayland presentation still need confirmation in the user's session.

Product name: **TxnCount**. Package/bundle: `com.pioneer.txncount`, starting with
0.1.1 (13). This intentionally differs from the original experimental Android
identity; see the historical migration notes below.
The new Android app must be granted folder access again; shared journals remain
unchanged. Future releases must retain the new identity and signing key.
The generated/installed Android icon and display name still need phone inspection;
the canonical PNG has been visually inspected. No public release branding,
store signing, or production identity work is implied.

## Historical Android identity migration

The original `com.pioneer.financepoc` installation and `com.pioneer.txncount`
are separate Android apps, not an in-place upgrade. For an old installation:

1. Independently back up the shared folder and finish synchronization.
2. Install the current app and choose the same existing journal folder.
3. Grant folder access again and reapply local settings; app-private preferences
   and SAF grants cannot transfer between app IDs.
4. Verify accounts, history and balances before optionally removing the old app.
   Do not edit the journal concurrently from both installations.
5. If Obtainium still tracks the old package, re-add the repository and confirm
   it tracks `com.pioneer.txncount`.

Desktop preserves its configuration directory and migrates the legacy main-folder
preference only when the new key is absent. Journals and backups are not rewritten
by this identity change.
