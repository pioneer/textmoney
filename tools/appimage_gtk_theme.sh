# TextMoney GTK theme policy; sourced before the plugin's native module setup.
export APPDIR="${APPDIR:-"$(dirname "$(realpath "$0")")"}"
export GTK_DATA_PREFIX="$APPDIR/usr"
# GTK searches these for themes, including CSS imports and gresources.
export XDG_DATA_DIRS="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:$APPDIR/usr/share"

if [ -n "${APPIMAGE_GTK_THEME:-}" ]; then
    export GTK_THEME="$APPIMAGE_GTK_THEME"
elif [ -z "${GTK_THEME:-}" ]; then
    unset GTK_THEME
    case "${XDG_CURRENT_DESKTOP:-}:${XDG_SESSION_DESKTOP:-}" in
        *GNOME*|*gnome*)
            if command -v gsettings >/dev/null 2>&1 &&
                _textmoney_theme="$(gsettings get org.gnome.desktop.interface gtk-theme 2>/dev/null)"; then
                # gsettings prints a GVariant string, not an unquoted theme name.
                case "$_textmoney_theme" in
                    \'*\')
                        _textmoney_theme="${_textmoney_theme:1:-1}"
                        _textmoney_theme="${_textmoney_theme//\\\'/\'}"
                        ;;
                    \"*\")
                        _textmoney_theme="${_textmoney_theme:1:-1}"
                        _textmoney_theme="${_textmoney_theme//\\\"/\"}"
                        ;;
                    *) _textmoney_theme="";;
                esac
                _textmoney_theme="${_textmoney_theme//\\\\/\\}"
                if [ -n "$_textmoney_theme" ]; then
                    if [[ "$_textmoney_theme" != *:* ]] &&
                        _textmoney_scheme="$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null)" &&
                        [ "$_textmoney_scheme" = "'prefer-dark'" ]; then
                        _textmoney_theme="$_textmoney_theme:dark"
                    fi
                    export GTK_THEME="$_textmoney_theme"
                fi
            fi
            ;;
    esac
fi
# With no override, GTK can still read XSettings/settings.ini on other desktops.
# Missing themes use GTK's built-in Adwaita fallback, not a bundled theme folder.
unset _textmoney_theme _textmoney_scheme
