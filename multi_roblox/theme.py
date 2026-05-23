"""Dark / light theming for the Tk GUI.

sv-ttk handles all ttk widgets; this module covers what it doesn't:
  * Native (non-ttk) widgets: `tk.Listbox`, `tk.Text`, `tk.Tk`'s background.
  * The Windows title bar via `DwmSetWindowAttribute` so it doesn't
    stay light grey when the rest of the window is dark.
  * Treeview tag colors that need different shades per theme (the
    'crashed' row should be a brighter red against a dark background).
"""
from __future__ import annotations

import ctypes
import logging
import tkinter as tk
from typing import Optional

log = logging.getLogger(__name__)

# Palette per theme — kept in this file so future tweaks live in one place.
_PALETTE = {
    "dark": {
        "bg": "#1c1c1c",
        "fg": "#ffffff",
        "select_bg": "#3a5a8a",
        "select_fg": "#ffffff",
        "crashed_fg": "#ff5e5e",
    },
    "light": {
        "bg": "#ffffff",
        "fg": "#000000",
        "select_bg": "#cce4f7",
        "select_fg": "#000000",
        "crashed_fg": "#aa0000",
    },
}

# DwmSetWindowAttribute key for dark title bars.
# 20 is the Win11 / Win10 20H1+ key, 19 is the older Win10 1809 key.
_DWMWA_USE_IMMERSIVE_DARK_MODE_NEW = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19


def is_available() -> bool:
    try:
        import sv_ttk  # noqa: F401
        return True
    except ImportError:
        return False


def _apply_title_bar(window: tk.Misc, dark: bool) -> None:
    """Flip the OS-drawn title bar between dark and light on Windows."""
    try:
        # Need the actual top-level HWND, not the child widget winfo_id() points at.
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return
        value = ctypes.c_int(1 if dark else 0)
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE_NEW,
                     _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value),
            )
    except Exception:
        log.exception("could not apply dark title bar")


def apply(window: tk.Misc, theme: str,
          native_widgets: Optional[list[tk.Widget]] = None) -> str:
    """Apply `theme` ("dark" / "light") to `window` and any non-ttk widgets.

    Returns the theme name actually applied (falls back to "light" if
    sv-ttk isn't installed, so the caller can update persisted state).
    """
    if theme not in _PALETTE:
        theme = "dark"
    if not is_available():
        log.warning("sv-ttk not installed; dark mode unavailable")
        return "light"

    import sv_ttk
    sv_ttk.set_theme(theme)

    palette = _PALETTE[theme]
    is_dark = theme == "dark"

    # Window root bg — sv-ttk styles ttk but not the bare tk.Tk background.
    try:
        window.configure(bg=palette["bg"])
    except tk.TclError:
        pass

    for widget in native_widgets or []:
        try:
            widget.configure(
                bg=palette["bg"],
                fg=palette["fg"],
                selectbackground=palette["select_bg"],
                selectforeground=palette["select_fg"],
                highlightthickness=0,
                insertbackground=palette["fg"],
            )
        except tk.TclError:
            # Some widgets don't accept every option (e.g. insertbackground
            # on Listbox); ignore — the rest still apply.
            pass

    _apply_title_bar(window, dark=is_dark)
    return theme


def crashed_fg(theme: str) -> str:
    return _PALETTE.get(theme, _PALETTE["dark"])["crashed_fg"]
