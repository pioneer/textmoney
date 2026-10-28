# TextMoney branding

The canonical [assets/icon.png](../assets/icon.png) is original minimal artwork:
a rounded white text document with smooth dollar and euro symbols and a gold
pencil on a dark blue background, 1024 x 1024 PNG. The bold money symbols remain prominent
at launcher sizes, while text lines and the pencil indicate editable plain text.
The symbols use Bezier curves with supersampled rendering, without a font dependency.
Artwork is reduced to approximately 65% of its original scale and centered on the full-size
background. A regression checks that visible artwork fits within Android's
central 66/108 adaptive-icon safe circle, leaving room for circular masks.
Regenerate it with `uv run inv app-icon`; validate with `uv run inv icon-check`.
There are no maintained duplicate platform icons. Invoke stages the root assets
for Flet, which generates launcher/window icons and splash screens.

Linux and Windows use a generated transparent derivative of the canonical PNG,
not the opaque Android-safe canvas. An artwork silhouette preserves the dark currency
symbols and pencil outlines while removing only the surrounding background.
The foreground is cropped, centered and scaled to a 944-pixel maximum extent
on a transparent 1024-square canvas. If the artwork geometry changes, update
the silhouette in `tasks.desktop_icon` alongside it.
Builds stage this as ignored `src/assets/icon_linux.png` or
`src/assets/icon_windows.png`, using Flet's platform-specific icon lookup.
This replaces the oversized Android-safe background with artwork framed for desktop sizes.
Other builds stage only the original. The Linux development client uses the same
derivative for its window and desktop entry; the Windows installer generates its
ICO from its staged derivative too. The maintained `assets/icon.png` and Android
output are unchanged.

Linux source runs use a generated branded Flet client and hidden desktop entry;
see [development](development.md).

## Historical Linux validation

The following observations and test count record the icon-development milestone,
not the current release's full validation.
Native X11 smoke testing verified the TxnCount
window title, development WM_CLASS and app icon pixels. After the transparent
Linux derivative change, a real `uv run inv build-linux` and an isolated X11
launch verified the packaged window's 256-square `_NET_WM_ICON`: corner alpha
is zero and visible artwork spans 91.4% of the icon. `uv run inv check` passed
all 488 tests, including transparent framing, unchanged Android source/staging,
Linux-only packaging selection, desktop-entry cache invalidation and development
client selection with a packaged Linux build present. A native Wayland smoke
test of `uv run inv run` with isolated preferences verified
`xdg_toplevel.set_app_id("io.txncount.local")`; GNOME's desktop-entry API resolved
that identity to the existing generated icon. Alt-Tab's final visual presentation
still needs confirmation in the user's session.

## Current release identity

Product name: **TextMoney**. Package/bundle: `com.pioneer.textmoney`.
The old `com.pioneer.txncount` identity was introduced in version 0.1.1
(build 13); releases through v0.3.15/build 38 used it. The TextMoney rename is
not published yet and creates a separate installation; see the
[migration guide](rename-migration.md). Future TextMoney releases must retain
the new ID and maintained production Android signing key.
The [release workflow](development.md#one-command-releases) prepares
production-signed Android APKs, a Linux AppImage and a Windows installer.
Windows installers are currently unsigned; production Android signing is not a
claim of app-store submission or approval.
The canonical PNG has been visually inspected. Native CI packaging/startup checks
do not replace physical-phone inspection of the installed launcher icon and name.
