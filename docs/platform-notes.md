# Platform requirements and troubleshooting

| Platform | Release file to download | Requirements |
| --- | --- | --- |
| Android | `textmoney-<version>-arm64-v8a.apk`, `textmoney-<version>-armeabi-v7a.apk`, or `textmoney-<version>-x86_64.apk` | Android 7.0+; choose the device's architecture |
| Windows | `TextMoney-<version>-windows-x64-setup.exe` | x64-compatible Windows 10 1809+ |
| Linux | `TextMoney-<version>-x86_64.AppImage` | Linux x86_64; Ubuntu 24.04+ or a compatible, up-to-date Arch Linux system |

Download from **[GitHub Releases](https://github.com/pioneer/textmoney/releases/latest)**.
Releases include `SHA256SUMS` for the five binaries. No Python, uv, or platform
build tools are needed to run these downloads. macOS, iOS and browser storage
are not supported release distributions.

macOS and iOS may be possible future targets, but are not currently released or
validated. The main developer does not have Apple devices to test them; available
build tasks do not establish working application support. See
[development and build targets](development.md#packaging).

TextMoney is beta software. Keep an independent backup of important financial
data before installing or updating. Store your journal outside the application
installation directory. See the [user guide](user-guide.md) for app workflows.

## Android

Choose one standalone APK matching your device:

- `arm64-v8a`: most modern phones.
- `armeabi-v7a`: 32-bit Android devices.
- `x86_64`: compatible emulators/devices.

There is no universal APK; these are not split-install bundles.
The system folder picker must grant SAF read/write access to a local records
folder. A cloud-only provider is not sufficient; see
[local-folder requirements](synchronization.md#local-folders-and-sync-clients).

APKs are production-signed with the maintained TextMoney certificate and use
application ID `com.pioneer.textmoney`. Install a newer matching APK over the
existing app to update in place; do not uninstall first. Updates must retain
the folder grant and external journal. Verify both after updating.

You can track this repository with
[Obtainium](https://github.com/ImranR98/Obtainium). Enable **Attempt to filter
APKs by CPU architecture if possible** to select the matching APK.
Compilation, signing checks and host-payload tests do not certify physical-phone
or every SAF-provider behavior; see [device checks](testing.md#transaction-and-device-checklist).

## Windows

The Windows download is an x64 per-user EXE installer, not a portable ZIP.
It is currently unsigned and may trigger unknown-publisher or SmartScreen
warnings; Android signing does not sign Windows executables.

The app installs under `%LOCALAPPDATA%\Programs\TextMoney` without elevation.
Keep records in the separate folder you selected; preferences live under
`%APPDATA%\textmoney`.
Uninstall removes application files, not those records or preferences.

Windows ARM emulation and older Windows are not validated.
If the installer launches a blank window, close it and reopen TextMoney from
the Start Menu; the cause of the historical first-launch report is not established.
See [Windows packaging and testing limits](development.md#windows-installer)
for native CI coverage and the distinction from interactive/update testing.

## Linux AppImage

The AppImage is built on Ubuntu 24.04; older glibc environments are not validated.
The host supplies graphics drivers, the display server and glibc.

Set the downloaded file executable:

```sh
APPIMAGE="./TextMoney-<version>-x86_64.AppImage"
chmod +x "$APPIMAGE"
"$APPIMAGE"
```

Replacing the AppImage does not move your selected journal folder.
Preferences stay under `${XDG_CONFIG_HOME:-~/.config}/textmoney`.

### FUSE troubleshooting

If FUSE 2 compatibility is missing, install its library alongside the system's
existing FUSE packages.

**Ubuntu 24.04 or newer:**

```sh
sudo apt update
sudo apt install libfuse2t64
```

**Arch Linux:**

```sh
sudo pacman -Syu fuse2
```

If FUSE is unavailable, use the executable path assigned above:

```sh
APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE"
```

See the [AppImage FUSE guide](https://docs.appimage.org/user-guide/troubleshooting/fuse.html).
These requirements are not certification of every distribution or desktop
environment. See [AppImage packaging and testing limits](development.md#linux-appimage).
