import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import linux_appimage

LEGACY_PREFIX = """#! /usr/bin/env bash

COLOR_SCHEME="$(dbus-send --session --dest=org.freedesktop.portal.Desktop --type=method_call --print-reply --reply-timeout=1000 /org/freedesktop/portal/desktop org.freedesktop.portal.Settings.Read 'string:org.freedesktop.appearance' 'string:color-scheme' 2> /dev/null | tail -n1 | cut -b35- | cut -d' ' -f2 || printf '')"
if [ -z "$COLOR_SCHEME" ]; then
    COLOR_SCHEME="$(gsettings get org.gnome.desktop.interface color-scheme 2> /dev/null || printf '')"
fi
case "$COLOR_SCHEME" in
    "1"|"'prefer-dark'")  GTK_THEME_VARIANT="dark";;
    "2"|"'prefer-light'") GTK_THEME_VARIANT="light";;
    *)                    GTK_THEME_VARIANT="light";;
esac
APPIMAGE_GTK_THEME="${APPIMAGE_GTK_THEME:-"Adwaita:$GTK_THEME_VARIANT"}" # Allow user to override theme (discouraged)

export APPDIR="${APPDIR:-"$(dirname "$(realpath "$0")")"}" # Workaround to run extracted AppImage
export GTK_DATA_PREFIX="$APPDIR"
export GTK_THEME="$APPIMAGE_GTK_THEME" # Custom themes are broken
"""  # noqa: E501
LEGACY_SUFFIX = """export GDK_BACKEND=x11 # Crash with Wayland backend on Wayland
export XDG_DATA_DIRS="$APPDIR/usr/share:/usr/share:$XDG_DATA_DIRS" # g_get_system_data_dirs() from GLib
export GSETTINGS_SCHEMA_DIR="$APPDIR/usr/share/glib-2.0/schemas"
export GTK_PATH="$APPDIR/usr/lib/gtk-3.0"
export GTK_IM_MODULE_FILE="$APPDIR/usr/lib/gtk-3.0/3.0.0/immodules.cache"
export GDK_PIXBUF_MODULE_FILE="$APPDIR/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"
"""  # noqa: E501


@pytest.fixture
def hook(tmp_path: Path) -> Path:
    directory = tmp_path / "AppDir with spaces/apprun-hooks"
    directory.mkdir(parents=True)
    path = directory / "linuxdeploy-plugin-gtk.sh"
    path.write_text(LEGACY_PREFIX + LEGACY_SUFFIX)
    linux_appimage._patch_gtk_hook(directory.parent)
    return path


def run_hook(
    hook: Path, tmp_path: Path, settings: bool = True, /, **overrides: str
) -> dict[str, str]:
    bash = shutil.which("bash")
    if bash is None or os.name == "nt":
        pytest.skip("AppImage launch hook requires Linux Bash.")
    commands = tmp_path / "commands"
    commands.mkdir(exist_ok=True)
    if settings:
        gsettings = commands / "gsettings"
        gsettings.write_text(
            "#!/bin/sh\n"
            'if [ "${MOCK_FAIL:-}" = 1 ]; then exit 1; fi\n'
            'case "$3" in\n'
            'gtk-theme) printf "%s\\n" "$MOCK_GTK_THEME";;\n'
            'color-scheme) if [ "${MOCK_SCHEME_FAIL:-}" = 1 ]; then exit 1; fi\n'
            'printf "%s\\n" "$MOCK_COLOR_SCHEME";;\n'
            "*) exit 1;;\nesac\n"
        )
        gsettings.chmod(0o755)
    env = {
        "PATH": str(commands),
        "HOME": str(tmp_path),
        "APPDIR": str(hook.parent.parent),
        "XDG_CURRENT_DESKTOP": "GNOME",
        "MOCK_GTK_THEME": "'Orchis'",
        "MOCK_COLOR_SCHEME": "'default'",
    } | overrides
    names = ("GTK_THEME", "GTK_DATA_PREFIX", "XDG_DATA_DIRS", "GTK_PATH", "GDK_BACKEND")
    output = subprocess.run(
        [
            bash,
            "-ec",
            '. "$1"; printf "%s\\0" "${GTK_THEME-}" "$GTK_DATA_PREFIX" '
            '"$XDG_DATA_DIRS" "$GTK_PATH" "$GDK_BACKEND"',
            "test-hook",
            str(hook),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return dict(zip(names, output.stdout.split("\0")[:-1], strict=True))


@pytest.mark.parametrize(
    "theme,scheme,expected",
    [
        ("'Orchis'", "'default'", "Orchis"),
        ("'Adwaita'", "'default'", "Adwaita"),
        ("'Other Custom Theme'", "'default'", "Other Custom Theme"),
        ('"Theme\'s name"', "'default'", "Theme's name"),
        ("'Orchis-Dark'", "'default'", "Orchis-Dark"),
        ("'Orchis'", "'prefer-dark'", "Orchis:dark"),
        ("'Orchis:dark'", "'default'", "Orchis:dark"),
        ("'Orchis-Dark'", "'prefer-light'", "Orchis-Dark"),
        ("", "'default'", ""),
        ("invalid-output", "'default'", ""),
    ],
)
def test_system_theme_is_not_replaced_by_a_color_scheme_default(
    hook: Path, tmp_path: Path, theme: str, scheme: str, expected: str
) -> None:
    values = run_hook(hook, tmp_path, MOCK_GTK_THEME=theme, MOCK_COLOR_SCHEME=scheme)
    assert values["GTK_THEME"] == expected


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"APPIMAGE_GTK_THEME": "Chosen:dark", "GTK_THEME": "Ignored"}, "Chosen:dark"),
        ({"APPIMAGE_GTK_THEME": "Chosen Light"}, "Chosen Light"),
        ({"GTK_THEME": "Chosen:dark"}, "Chosen:dark"),
        ({"APPIMAGE_GTK_THEME": "", "GTK_THEME": "Chosen"}, "Chosen"),
    ],
)
def test_explicit_overrides_take_precedence(
    hook: Path, tmp_path: Path, overrides: dict[str, str], expected: str
) -> None:
    assert run_hook(hook, tmp_path, MOCK_FAIL="1", **overrides)["GTK_THEME"] == expected


def test_missing_color_scheme_does_not_discard_the_selected_theme(
    hook: Path, tmp_path: Path
) -> None:
    assert run_hook(hook, tmp_path, MOCK_SCHEME_FAIL="1")["GTK_THEME"] == "Orchis"


def test_gnome_session_name_can_supply_desktop_detection(hook: Path, tmp_path: Path) -> None:
    values = run_hook(hook, tmp_path, XDG_CURRENT_DESKTOP="", XDG_SESSION_DESKTOP="gnome")
    assert values["GTK_THEME"] == "Orchis"


@pytest.mark.parametrize(
    "settings,overrides",
    [
        (False, {}),
        (False, {"GTK_THEME": ""}),
        (True, {"MOCK_FAIL": "1"}),
        (True, {"XDG_CURRENT_DESKTOP": "KDE"}),
    ],
)
def test_optional_gnome_settings_fall_back_to_native_gtk(
    hook: Path, tmp_path: Path, settings: bool, overrides: dict[str, str]
) -> None:
    assert run_hook(hook, tmp_path, settings, **overrides)["GTK_THEME"] == ""


@pytest.mark.parametrize("data_dirs", ["", "/host/share:/another data/share"])
def test_host_data_paths_are_kept_ahead_of_bundle_and_native_modules_preserved(
    hook: Path, tmp_path: Path, data_dirs: str
) -> None:
    values = run_hook(hook, tmp_path, XDG_DATA_DIRS=data_dirs)
    appdir = hook.parent.parent
    assert values["XDG_DATA_DIRS"] == (
        f"{data_dirs or '/usr/local/share:/usr/share'}:{appdir}/usr/share"
    )
    assert values["GTK_DATA_PREFIX"] == f"{appdir}/usr"
    assert values["GTK_PATH"] == f"{appdir}/usr/lib/gtk-3.0"
    assert values["GDK_BACKEND"] == "x11"
    assert 'export GTK_IM_MODULE_FILE="$APPDIR/usr/lib/gtk-3.0/3.0.0/immodules.cache"' in (
        hook.read_text()
    )


def test_changed_upstream_hook_is_refused_not_partially_rewritten(tmp_path: Path) -> None:
    directory = tmp_path / "apprun-hooks"
    directory.mkdir()
    path = directory / "linuxdeploy-plugin-gtk.sh"
    original = LEGACY_PREFIX.replace("Adwaita:", "Changed:") + LEGACY_SUFFIX
    path.write_text(original)
    with pytest.raises(RuntimeError, match="GTK.*hook"):
        linux_appimage._patch_gtk_hook(tmp_path)
    assert path.read_text() == original
