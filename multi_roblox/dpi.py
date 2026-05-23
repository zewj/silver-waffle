"""Process DPI awareness so the window isn't bitmap-stretched on high-DPI displays.

Call `enable()` before constructing the Tk root. Returns the system scale
factor (1.0 at 96 DPI, 1.5 at 144 DPI / 150%, etc.) which the GUI then
passes to `tk scaling` so Tk lays out widgets at the right pixel size
instead of letting Windows stretch a 96-DPI bitmap to fit the monitor.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def enable() -> float:
    """Mark the process as Per-Monitor-V2 DPI aware. Returns the scale factor."""
    if sys.platform != "win32":
        return 1.0

    import ctypes

    user32 = ctypes.windll.user32
    shcore = getattr(ctypes.windll, "shcore", None)

    # Per-Monitor V2 (Win10 1703+) is the modern API. Fall back to V1, then
    # the legacy system-wide SetProcessDPIAware as a last resort.
    PROCESS_PER_MONITOR_AWARE_V2 = -4
    PROCESS_PER_MONITOR_AWARE_V1 = -3
    set_ctx = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_ctx is not None:
        for handle in (PROCESS_PER_MONITOR_AWARE_V2, PROCESS_PER_MONITOR_AWARE_V1):
            try:
                if set_ctx(ctypes.c_void_p(handle)):
                    break
            except OSError:
                continue
    elif shcore is not None:
        try:
            # 2 = PROCESS_PER_MONITOR_DPI_AWARE
            shcore.SetProcessDpiAwareness(2)
        except OSError:
            try:
                user32.SetProcessDPIAware()
            except OSError:
                pass
    else:
        try:
            user32.SetProcessDPIAware()
        except OSError:
            pass

    # Resolve the actual DPI to compute the scale factor.
    dpi = 96
    get_dpi_for_system = getattr(user32, "GetDpiForSystem", None)
    if get_dpi_for_system is not None:
        try:
            dpi = int(get_dpi_for_system())
        except OSError:
            pass
    if dpi == 96:
        # Fallback for older builds.
        try:
            hdc = user32.GetDC(0)
            if hdc:
                LOGPIXELSX = 88
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX) or 96
                user32.ReleaseDC(0, hdc)
        except OSError:
            pass

    scale = max(0.75, dpi / 96.0)
    log.info("DPI=%s, scale=%.2f", dpi, scale)
    return scale


def apply_tk_scaling(root, scale: float) -> None:
    """Tell Tk about the scale so points/pixels map correctly."""
    try:
        # Tk's `scaling` is points-per-pixel; default is 1.333 (96 DPI / 72 pt).
        # Pass the actual ratio so font sizes stay readable at high DPI.
        root.tk.call("tk", "scaling", scale * 1.3333333)
    except Exception:
        log.exception("could not set tk scaling")
