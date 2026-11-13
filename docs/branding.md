# TextMoney branding

Optional account/category artwork is separate from application launcher branding;
see [Entity icons](entity-icons.md) for the picker, sizing and asset licensing.

The canonical [assets/icon.png](../assets/icon.png) is the maintainer's Material-like
redesign: a rounded white document with dollar and euro symbols, a curved mint
fold, mint text lines, and a gold pencil on a shaded circular blue background.
The 1024 x 1024 RGBA PNG preserves the supplied artwork, gradients and shadows,
with clean transparent corners instead of its original black matte.
Visible artwork spans approximately 65% of the circle's diameter. Android's
extra safe-area padding belongs in its generated variant rather than the
canonical preview. Regressions check that Android's artwork fits within
the central 66/108 adaptive-icon safe circle.
Import a replacement circular PNG with
`uv run inv app-icon --source=/path/to/redesign.png`. The importer requires a
square PNG of at least 1024 pixels, removes corner-connected near-black matte
without erasing dark artwork, trims the matted edge, and resamples with transparency.
Without `--source`, `uv run inv app-icon` only validates the current icon; it
never redraws or overwrites the maintainer's design. Validate with
`uv run inv icon-check`.
There are no maintained duplicate platform icons. Invoke stages the root assets
for Flet, which generates launcher/window icons and splash screens.

Android builds and device-debug runs stage a generated, padded
`src/assets/icon_android.png`, retaining the previous approximately 46% foreground
extent on a full-size blue circular badge, to mark the composition as already framed. Without
this platform-specific filename, Flet shrinks a transparent generic icon again,
making the document too small. Legacy launcher PNGs keep the circular badge;
adaptive icons use the same blue `adaptive_icon_background` colour to avoid a
white surround. Modern Android launchers control the final adaptive mask (circle,
squircle, etc.); the app does not override the user's launcher shape.
Flet may warn that the circular background extends outside its adaptive safe
zone; only background is cropped, not the document or pencil.

Linux and Windows use the complete canonical badge, including its gradients and
shadows, resized to 944 pixels and centered on a transparent 1024-square canvas.
No hand-drawn silhouette or replacement solid background is applied; future
artwork changes automatically flow into both desktop variants.
Builds stage this as ignored `src/assets/icon_linux.png` or
`src/assets/icon_windows.png`, using Flet's platform-specific icon lookup.
Other builds stage only the original. The Linux development client uses the same
circular derivative for its window and desktop entry, keeping taskbar and
Alt-Tab identity consistent. Packaged Linux builds generate their window icon
and hicolor menu icons from that derivative; the AppImage copies the generated
menu icon into its desktop integration. The Windows installer generates its
ICO from its staged derivative too.

Linux source runs use a generated branded Flet client and hidden desktop entry;
see [development](development.md).

## Redesign validation

An isolated native X11 source run verified the redesigned 256-square window icon
on light and dark backgrounds, transparent corners, and matching
`dev.textmoney.app` window/desktop-entry identity. The desktop entry resolved to
the new generated PNG. Platform tests cover Android's safe foreground, Linux's
window/hicolor icons, and Windows ICO sizes and transparent circular framing.
Physical Android and Windows appearance, newly packaged Linux presentation,
and Wayland/taskbar/Alt-Tab visual inspection remain pending.

## Previous round-badge validation

An isolated native X11 source run verified the 256-square `_NET_WM_ICON` has
transparent corners and a circular navy badge. Its opaque-pixel silhouette matched
the generated desktop derivative at every pixel. The window's
`dev.textmoney.app` WM_CLASS matched the desktop entry, whose icon resolved to
the same generated PNG. Packaging tests cover the Linux window/hicolor icons,
Android legacy/adaptive icon sizes, and the then-current Windows silhouette behavior.
Physical Android launcher appearance and packaged AppImage presentation still
require native release validation.

## Historical Linux validation

The following observations and test count record the icon-development milestone,
not the current release's full validation.
Native X11 smoke testing verified the
window title, development WM_CLASS and app icon pixels. After the transparent
Linux derivative change, a real `uv run inv build-linux` and an isolated X11
launch verified the packaged window's 256-square `_NET_WM_ICON`: corner alpha
is zero and visible artwork spans 91.4% of the icon. `uv run inv check` passed
all 488 tests, including transparent framing, unchanged Android source/staging,
Linux-only packaging selection, desktop-entry cache invalidation and development
client selection with a packaged Linux build present. A native Wayland smoke
test of `uv run inv run` with isolated preferences verified the development
application ID; GNOME's desktop-entry API resolved that identity to the existing
generated icon. Alt-Tab's final visual presentation
still needs confirmation in the user's session.

## Current release identity

Product name: **TextMoney**. Package/bundle: `com.pioneer.textmoney`.
Releases must retain this ID and the maintained production Android signing key.
The [release workflow](development.md#one-command-releases) prepares
production-signed Android APKs, a Linux AppImage and a Windows installer.
Windows installers are currently unsigned; production Android signing is not a
claim of app-store submission or approval.
The canonical PNG has been visually inspected. Native CI packaging/startup checks
do not replace physical-phone inspection of the installed launcher icon and name.
