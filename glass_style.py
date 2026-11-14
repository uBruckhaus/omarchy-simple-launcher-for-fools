"""See-through popups for the ubruckhaus plugins (reader; shared kit file).

Theme Manager → Settings → "Plugin popups follow window style" creates the
empty marker file ~/.config/omarchy/theme-manager/popup-glass; without it
popups stay solid. Only file names are checked here, no file is opened.
With it a popup's card follows Omarchy's window style (opaque / transparent /
glassy, the toggle files of omarchy-hyprland-window-transparency).
"""
from __future__ import annotations

import os
from pathlib import Path

GLASSY_ALPHA = 0.6
TRANSPARENT_ALPHA = 0.9


def marker_file() -> Path:
    return Path.home() / ".config/omarchy/theme-manager/popup-glass"


def toggles_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omarchy/toggles/hypr"


def enabled() -> bool:
    return marker_file().is_file()


def window_mode() -> str:
    """Omarchy's window style, read the way its own command reads it."""
    if (toggles_dir() / "window-opaque.lua").exists():
        return "opaque"
    if (toggles_dir() / "window-glassy.lua").exists():
        return "glassy"
    return "transparent"


def popup_alpha() -> float:
    """The card's alpha: 1.0 unless switched on in Theme Manager."""
    if not enabled():
        return 1.0
    return {"glassy": GLASSY_ALPHA, "transparent": TRANSPARENT_ALPHA}.get(window_mode(), 1.0)
