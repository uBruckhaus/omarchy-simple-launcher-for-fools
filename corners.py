"""The "Corners" setting, shared by App Manager and Theme Manager.

"windows" (the default) follows Hyprland's window rounding, which Omarchy
sets in this order, the last one winning: Omarchy's default (square), the
current theme's hyprland.lua, then your own ~/.config/hypr/looknfeel.lua.
"slight", "rounded" and "square" ignore it. The stored values stay the
same strings, so older settings files read as before. No process is started here: the caller
reads `hyprctl getoption decoration:rounding -j` its own bounded way and
passes the text to parse_rounding()."""
import json
import os
import re
from pathlib import Path

CHOICES = ("windows", "slight", "rounded", "square")
SLIGHT = 6        # "Slightly rounded": round controls beside square or tiled windows.
ROUNDED = 12      # "Rounded": Theme Manager's corners up to now.
MAX_RADIUS = 24
MAX_LUA_BYTES = 256 * 1024
THEME_LUA = Path.home() / ".local/state/omarchy/current/theme/hyprland.lua"
USER_LUA = Path.home() / ".config/hypr/looknfeel.lua"
# `rounding = 8` outside a comment; "gradient_rounding" and "rounding_power" don't count.
_ROUNDING = re.compile(r"(?<![\w.])rounding\s*=\s*(\d+)")


def choice(value):
    return value if value in CHOICES else "windows"


def parse_rounding(text):
    """Hyprland's window rounding from `hyprctl getoption … -j`, or None."""
    try:
        value = json.loads(text).get("int")
    except (ValueError, TypeError, AttributeError):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(value, MAX_RADIUS))


def radius(setting, window_rounding):
    """The corner radius in px for this setting and the windows' rounding."""
    setting = choice(setting)
    if setting == "slight":
        return SLIGHT
    if setting == "rounded":
        return ROUNDED
    if setting == "square":
        return 0
    return window_rounding if window_rounding is not None else 0


def sizes(radius):
    """The three sizes every control takes from one radius, as in AI Playground:
    (radius, control, small, pill). Cards, the popup, pop-ups, dialogs and
    lists take the radius; buttons, drop-downs and fields at most 8 px; check
    boxes, tracks, small badges and swatches at most 4 px; pills (chips,
    switches) stay round unless the corners are square."""
    radius = max(0, int(radius))
    return radius, min(radius, 8), min(radius, 4), 999 if radius else 0


def lua_rounding(path):
    """The last uncommented `rounding = N` in a Lua config file, or None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_LUA_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_LUA_BYTES:
        return None
    found = None
    for line in data.decode("utf-8", "replace").splitlines():
        code = line.split("--", 1)[0]
        for match in _ROUNDING.finditer(code):
            found = int(match.group(1))
    return found


def rounding_source(theme_lua=THEME_LUA, user_lua=USER_LUA):
    """Who set the windows' rounding: "user" (looknfeel.lua), "theme" or "default"."""
    if lua_rounding(user_lua) is not None:
        return "user"
    if lua_rounding(theme_lua) is not None:
        return "theme"
    return "default"
