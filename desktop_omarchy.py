"""Omarchy (Hyprland): desktop.py's interface for the bar popup.

- window: a full-screen transparent layer-shell surface with the card in it
- theme: ~/.local/state/omarchy/current/theme/colors.toml; rounding from
  `hyprctl getoption decoration:rounding -j`
- windows: `hyprctl clients -j`, closed through a Hyprland dispatcher;
  Hyprland tiles, so an app can be opened floating (`hl.exec_cmd` with the
  float rule)
- Omarchy-only features: shell plugins, the dock, Hyprland shortcuts, the
  menus watch and `omarchy-update`.
"""

import json
import shutil
import re
import subprocess
import tomllib
from pathlib import Path

NAME = "omarchy"
APP_ID = "org.omarchy.simplelauncherforfools"
COLORS_FILE = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
OLD_COLOR_KEYS = {"red": "color1", "green": "color2", "yellow": "color3",
                  "selection": "selection_background"}


def app_id() -> str:
    return APP_ID


def popup() -> bool:
    return True


def _is_color(value) -> bool:
    return isinstance(value, str) and bool(_COLOR_RE.fullmatch(value.strip()))


def theme_colors(path=None) -> dict:
    from palette import DEFAULT_COLORS
    try:
        theme = tomllib.loads(Path(path or COLORS_FILE).read_text())
    except (OSError, ValueError):
        return DEFAULT_COLORS.copy()
    # Older themes name the terminal colours only: color1 red, color2 green, color3 yellow.
    for key, old in OLD_COLOR_KEYS.items():
        if not _is_color(theme.get(key)) and _is_color(theme.get(old)):
            theme[key] = theme[old]
    colors = {key: theme[key] if _is_color(theme.get(key)) else fallback
              for key, fallback in DEFAULT_COLORS.items()}
    if "muted" not in theme:
        colors["muted"] = next((theme[k] for k in ("dark_foreground", "color8") if _is_color(theme.get(k))),
                               DEFAULT_COLORS["muted"])
    return colors


def window_rounding():
    from launcher_core import read_bounded_output
    import corners
    out = read_bounded_output(["hyprctl", "getoption", "decoration:rounding", "-j"], max_bytes=4096, timeout=2)
    return corners.parse_rounding(out or "")


def rounding_source() -> str:
    import corners
    return corners.rounding_source()


def clients():
    try:
        result = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def knows_windows() -> bool:
    return True


def close_window(client):
    address = client.get("address", "")
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", address):
        return
    command = "hl.dsp.window.close({window = " + json.dumps("address:" + address) + "})"
    subprocess.Popen(["hyprctl", "dispatch", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def focus_window(client):
    """Show the window: its workspace comes up and it gets the focus."""
    address = client.get("address", "")
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", address):
        return
    command = "hl.dsp.focus({window = " + json.dumps("address:" + address) + "})"

    def run():
        # The Lua dispatcher of every Omarchy 4.x; the classic one if it is refused (as
        # omarchy-launch-or-focus does).
        try:
            result = subprocess.run(["hyprctl", "dispatch", command], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return
            subprocess.run(["hyprctl", "dispatch", "focuswindow", "address:" + address],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    import threading
    threading.Thread(target=run, daemon=True).start()


def can_float() -> bool:
    return True


def lua_string(text: str) -> str:
    """text as a Lua string literal: every byte escaped (\\ddd), so nothing in
    it can end the string or reach the Lua code around it."""
    return '"' + "".join(f"\\{byte:03d}" for byte in text.encode()) + '"'


MAX_WORKSPACE = 100     # Hyprland's normal workspaces offered by "Open on workspace"


def exec_floating(argv) -> bool:
    """Start argv through Hyprland with the float rule: its first window
    opens floating, whatever the window rules say (Hyprland's exec runs it
    in a shell, so the words are quoted)."""
    return _exec_with_rule(argv, "float = true")


def exec_on_workspace(argv, workspace) -> bool:
    """Start argv through Hyprland with the workspace rule: its first window
    opens on that workspace (1 … MAX_WORKSPACE), which then shows."""
    if isinstance(workspace, bool) or not isinstance(workspace, int) or not 1 <= workspace <= MAX_WORKSPACE:
        return False
    return _exec_with_rule(argv, f"workspace = {workspace}")


def workspaces():
    """([(id, [window classes])] of the normal workspaces in use, the first free id),
    or None when Hyprland does not answer."""
    try:
        result = subprocess.run(["hyprctl", "workspaces", "-j"], capture_output=True, text=True, timeout=2)
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        data = None
    if not isinstance(data, list):
        return None
    ids = sorted({w["id"] for w in data if isinstance(w, dict) and type(w.get("id")) is int
                  and 1 <= w["id"] <= MAX_WORKSPACE})
    apps = {}
    for client in clients() or []:
        ws = client.get("workspace", {}).get("id") if isinstance(client, dict) else None
        name = str(client.get("class") or client.get("title") or "")[:40] if ws in ids else ""
        if name and name not in apps.setdefault(ws, []):
            apps[ws].append(name)
    free = next((n for n in range(1, MAX_WORKSPACE + 1) if n not in ids), None)
    return [(i, apps.get(i, [])) for i in ids], free


def _exec_with_rule(argv, rule: str) -> bool:
    import shlex
    code = f"hl.exec_cmd({lua_string(shlex.join(argv))}, {{{rule}}})"
    try:
        result = subprocess.run(["hyprctl", "eval", code], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "ok"


def has_plugins() -> bool:
    return True


def has_dock() -> bool:
    return True


def has_shortcuts() -> bool:
    return True


def watches_menus() -> bool:
    return True


def system_update():
    """Omarchy's own update window (password, snapshot, all packages)."""
    launcher = shutil.which("omarchy-launch-floating-terminal-with-presentation")
    if launcher is None or shutil.which("omarchy-update") is None:
        return None
    return [launcher, "omarchy-update"]
