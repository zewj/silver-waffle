"""Capture an HWND's contents as PNG bytes.

Uses PrintWindow(PW_RENDERFULLCONTENT) which renders the window's actual
content into a memory DC even when the window is minimized or fully
occluded. That's exactly what we want for "what is the alt's game
showing right now?" — you can stay in Forza on top and still see the
Roblox window via Discord.

Not a cheat: this only reads pixels that the window itself drew. We
don't touch the Roblox process's memory or input.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from io import BytesIO
from typing import Optional

log = logging.getLogger(__name__)

# Win10+: capture renders the window even when it's occluded / offscreen.
PW_RENDERFULLCONTENT = 0x00000002
# BI_RGB = uncompressed 32-bpp BGRA.
BI_RGB = 0

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


# Function prototypes — declaring restype/argtypes keeps ctypes from
# truncating handle values on 64-bit Windows.
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
_user32.PrintWindow.restype = wintypes.BOOL

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint,
    ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint,
]
_gdi32.GetDIBits.restype = ctypes.c_int


def capture_window_png(hwnd: int) -> Optional[bytes]:
    """Return a PNG of the window's contents, or None on failure.

    Best-effort: tries PrintWindow with PW_RENDERFULLCONTENT first (handles
    occluded / minimized windows on Win10+), falls back to the default
    behavior. Returns None for invalid HWNDs or zero-area windows.
    """
    if not hwnd:
        return None
    try:
        from PIL import Image
    except ImportError:
        log.error("Pillow not installed; screenshot disabled")
        return None

    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    window_dc = _user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    mem_dc = None
    bitmap = None
    try:
        mem_dc = _gdi32.CreateCompatibleDC(window_dc)
        if not mem_dc:
            return None
        bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not bitmap:
            return None
        old = _gdi32.SelectObject(mem_dc, bitmap)
        try:
            ok = _user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
            if not ok:
                # Older windows / quirky drivers: try without the flag.
                ok = _user32.PrintWindow(hwnd, mem_dc, 0)
                if not ok:
                    log.warning("PrintWindow failed for hwnd=%s", hwnd)
                    return None

            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height  # negative = top-down rows
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = (ctypes.c_ubyte * (width * height * 4))()
            scanned = _gdi32.GetDIBits(
                mem_dc, bitmap, 0, height, buf, ctypes.byref(bmi), 0,
            )
            if scanned == 0:
                log.warning("GetDIBits returned 0 rows for hwnd=%s", hwnd)
                return None

            img = Image.frombuffer(
                "RGBA", (width, height), bytes(buf), "raw", "BGRA", 0, 1,
            )
            out = BytesIO()
            img.save(out, format="PNG", optimize=False, compress_level=6)
            return out.getvalue()
        finally:
            _gdi32.SelectObject(mem_dc, old)
    except Exception:
        log.exception("capture_window_png failed for hwnd=%s", hwnd)
        return None
    finally:
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if mem_dc:
            _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(hwnd, window_dc)
