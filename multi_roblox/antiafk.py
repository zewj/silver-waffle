"""Per-instance anti-AFK background ticker.

Posts tiny synthetic input directly to the target Roblox HWND via
`PostMessageW`. Because we never call `SetForegroundWindow`, the action
does *not* steal focus from your main game — the background Roblox
window receives the input message off its own thread's message queue.

Per cycle (random 12–35 s) we:
  * Post a `WM_MOUSEMOVE` with a 5–15 px jitter around the client-area
    center. Roblox's input layer treats this as movement, which resets
    its 20-minute idle timer.
  * Every few cycles, post a benign `WM_KEYDOWN`/`WM_KEYUP` for the `0`
    key (no default Roblox binding) as a second signal — some game
    builds dismiss the idle prompt via keyboard input only.

Each instance has its own `AntiAFK` thread; the thread sleeps almost
all the time so dozens of them are still effectively zero-CPU.
"""
from __future__ import annotations

import ctypes
import logging
import random
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

from . import windows

log = logging.getLogger(__name__)

# Window message constants.
WM_MOUSEMOVE = 0x0200
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

# Virtual-key for the digit 0; not bound to any default Roblox action.
VK_0 = 0x30

# Default cycle bounds (seconds).
DEFAULT_INTERVAL_MIN = 12.0
DEFAULT_INTERVAL_MAX = 35.0

# Movement bounds in pixels.
MOVE_MIN = 5
MOVE_MAX = 15

# Keystroke fires every Nth cycle.
KEYSTROKE_EVERY = 3


def _make_lparam_coord(x: int, y: int) -> int:
    """Pack (x, y) into the lParam format expected by WM_MOUSEMOVE."""
    return (int(y) << 16) | (int(x) & 0xFFFF)


def _client_center(hwnd: int) -> Optional[tuple[int, int]]:
    rect = wintypes.RECT()
    if not windows._user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.right - rect.left) // 2, (rect.bottom - rect.top) // 2


def _pick_delta() -> int:
    """Return a signed integer with magnitude in [MOVE_MIN, MOVE_MAX]."""
    magnitude = random.randint(MOVE_MIN, MOVE_MAX)
    return magnitude if random.random() < 0.5 else -magnitude


class AntiAFK:
    """One background ticker. Safe to start/stop repeatedly."""

    def __init__(self, label: str,
                 hwnd_lookup: Callable[[], Optional[int]],
                 interval_min: float = DEFAULT_INTERVAL_MIN,
                 interval_max: float = DEFAULT_INTERVAL_MAX):
        self._label = label
        self._lookup = hwnd_lookup
        self._interval_min = max(1.0, float(interval_min))
        self._interval_max = max(self._interval_min, float(interval_max))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"antiafk-{self._label}",
            daemon=True,
        )
        self._thread.start()
        log.info("anti-AFK started for %s", self._label)

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        log.info("anti-AFK stop requested for %s", self._label)

    def _resolve_hwnd(self) -> Optional[int]:
        hwnd = self._lookup()
        if hwnd and windows._user32.IsWindow(hwnd):
            return hwnd
        return None

    def _send_jitter(self, hwnd: int) -> None:
        center = _client_center(hwnd)
        if center is None:
            return
        cx, cy = center
        x = max(0, cx + _pick_delta())
        y = max(0, cy + _pick_delta())
        windows._user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, _make_lparam_coord(x, y))

    def _send_benign_keystroke(self, hwnd: int) -> None:
        # lParam fields encoded per the WM_KEYDOWN docs:
        #   bits 0-15: repeat count (1)
        #   bits 16-23: scan code for '0' (0x0B)
        #   bit 24: extended key (0)
        #   bit 30: previous state (0 down / 1 up)
        #   bit 31: transition (0 down / 1 up)
        down_lparam = (0x0B << 16) | 1
        up_lparam = down_lparam | (1 << 30) | (1 << 31)
        windows._user32.PostMessageW(hwnd, WM_KEYDOWN, VK_0, down_lparam)
        # A tiny wait between down/up makes the event look real to handlers
        # that filter zero-duration presses.
        time.sleep(0.04 + random.random() * 0.05)
        windows._user32.PostMessageW(hwnd, WM_KEYUP, VK_0, up_lparam)

    def _loop(self) -> None:
        # Stagger first tick so a batch of "Enable on All" doesn't fire in lockstep.
        self._stop.wait(random.uniform(0.5, 3.0))
        cycle = 0
        while not self._stop.is_set():
            hwnd = self._resolve_hwnd()
            if hwnd:
                try:
                    self._send_jitter(hwnd)
                    if cycle % KEYSTROKE_EVERY == 0:
                        self._send_benign_keystroke(hwnd)
                except Exception:
                    log.exception("anti-AFK tick failed for %s", self._label)
            else:
                log.debug("anti-AFK %s: no live window this cycle", self._label)
            cycle += 1
            # Event-based sleep so stop() returns promptly.
            self._stop.wait(random.uniform(self._interval_min, self._interval_max))
        self._thread = None
        log.info("anti-AFK loop exited for %s", self._label)
