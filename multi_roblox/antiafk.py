"""Per-instance anti-AFK background ticker.

Posts tiny synthetic input directly to the target Roblox HWND via
`PostMessageW`. Because we never call `SetForegroundWindow`, the action
does *not* steal focus from your main game — the background Roblox
window receives the input message off its own thread's message queue.

When we do send, we send a paired action:
  * `WM_MOUSEMOVE` with a 5–15 px jitter around the client-area
    center — Roblox treats it as input and resets the 20-min idle timer.
  * `WM_KEYDOWN`/`WM_KEYUP` for the `F15` key — a "dead" function key
    almost no game binds, sent as a second signal in case a particular
    game build only watches keyboard input.

We send **at most one paired action every `TICK_INTERVAL_SEC`** (default
15 min). That's well under Roblox's ~20-min kick but the minimum
possible input footprint — no point spamming input every few seconds
when one event every 15 min keeps Roblox alive just as reliably.

The loop still wakes more often (every 12–35 s) but only to *check
state*; the actual `PostMessage` calls are gated by the throttle and
by these skip rules:

  * **Foreground + user active** (target HWND is foregrounded and the
    user produced real input in the last 60 s, per `GetLastInputInfo`)
    → skip. PvP / 1v1 safety: your real input is already resetting
    Roblox's timer, synthetic input would only risk a misinput.
  * **Background**, or **foreground + user idle for >60 s** → fire the
    paired action if 15 min has passed since the last successful send.

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

# Virtual-key for F15 — a function key practically no Roblox game binds.
# Chosen over digits (0-9) because PvP games routinely bind number keys to
# weapon slots / abilities and a stray press could cause a real misinput.
VK_F15 = 0x7E
# Scan code for F15.
SCAN_F15 = 0x68

# How often the worker thread wakes to check state (foreground window,
# user idleness, throttle clock). Each wake is a few Win32 calls in
# the microseconds range — keeping the polling cheap lets us react to
# state changes (user goes AFK, Alt-Tab) within seconds. The actual
# synthetic input is throttled separately by TICK_INTERVAL_SEC.
WAKE_INTERVAL_MIN = 12.0
WAKE_INTERVAL_MAX = 35.0

# Movement bounds in pixels.
MOVE_MIN = 5
MOVE_MAX = 15

# When the target window is foregrounded, treat the user as "really AFK"
# (and therefore safe to tick) only after this many seconds of no system
# keyboard / mouse activity.
USER_IDLE_THRESHOLD_SEC = 60.0

# Minimum gap between actual PostMessage sends, in any state. Roblox's
# idle kick fires around 20 min, so 15 leaves a 5-min margin to ride
# out a missed tick. Background and foreground-AFK use the same cap.
TICK_INTERVAL_SEC = 15 * 60


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
                 wake_min: float = WAKE_INTERVAL_MIN,
                 wake_max: float = WAKE_INTERVAL_MAX):
        self._label = label
        self._lookup = hwnd_lookup
        self._wake_min = max(1.0, float(wake_min))
        self._wake_max = max(self._wake_min, float(wake_max))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Monotonic timestamp of the last synthetic input we sent.
        # Throttle clock; one TICK_INTERVAL_SEC cap covers all branches.
        # Starts at 0.0 so the first eligible cycle ticks immediately
        # (initial AFK protection is in place from launch).
        self._last_sent_monotonic = 0.0

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

    def _decide(self) -> Optional[int]:
        """Return the HWND to send to this cycle, or None to skip."""
        hwnd = self._lookup()
        if not hwnd or not windows._user32.IsWindow(hwnd):
            return None
        # PvP / 1v1 safety: never fire into a window the user is
        # actively playing.
        if windows._user32.GetForegroundWindow() == hwnd:
            if windows.system_idle_seconds() < USER_IDLE_THRESHOLD_SEC:
                return None
        # Single throttle for every other case (background, or
        # foreground-AFK): at most one send per TICK_INTERVAL_SEC.
        if time.monotonic() - self._last_sent_monotonic < TICK_INTERVAL_SEC:
            return None
        return hwnd

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
        #   bits 16-23: scan code (F15)
        #   bit 24: extended key (0)
        #   bit 30: previous state (0 down / 1 up)
        #   bit 31: transition (0 down / 1 up)
        down_lparam = (SCAN_F15 << 16) | 1
        up_lparam = down_lparam | (1 << 30) | (1 << 31)
        windows._user32.PostMessageW(hwnd, WM_KEYDOWN, VK_F15, down_lparam)
        # A tiny wait between down/up makes the event look real to handlers
        # that filter zero-duration presses.
        time.sleep(0.04 + random.random() * 0.05)
        windows._user32.PostMessageW(hwnd, WM_KEYUP, VK_F15, up_lparam)

    def _loop(self) -> None:
        # Stagger first wake so a batch of "Enable on All" doesn't fire in lockstep.
        self._stop.wait(random.uniform(0.5, 3.0))
        while not self._stop.is_set():
            hwnd = self._decide()
            if hwnd:
                try:
                    self._send_jitter(hwnd)
                    self._send_benign_keystroke(hwnd)
                    self._last_sent_monotonic = time.monotonic()
                    log.debug("anti-AFK tick sent for %s", self._label)
                except Exception:
                    log.exception("anti-AFK tick failed for %s", self._label)
            # Event-based sleep so stop() returns promptly.
            self._stop.wait(random.uniform(self._wake_min, self._wake_max))
        self._thread = None
        log.info("anti-AFK loop exited for %s", self._label)
