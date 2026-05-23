"""Per-instance anti-AFK background ticker.

Posts tiny synthetic input directly to the target Roblox HWND via
`PostMessageW`. Because we never call `SetForegroundWindow`, the action
does *not* steal focus from your main game — the background Roblox
window receives the input message off its own thread's message queue.

Per cycle (random 12–35 s) we:
  * Post a `WM_MOUSEMOVE` with a 5–15 px jitter around the client-area
    center. Roblox's input layer treats this as movement, which resets
    its 20-minute idle timer.
  * Every few cycles, post a benign `WM_KEYDOWN`/`WM_KEYUP` for the
    `F15` key — a "dead" function key almost no game binds — as a
    second signal for game builds that only watch keyboard input.

Behavior depends on which "mode" the cycle falls into:

  * **Background** (target HWND isn't the foreground window) → tick
    every cycle, normal 12–35 s cadence. This is the headline case:
    Forza on top, Roblox behind, keep it alive.
  * **Foreground + user active** (target HWND is foregrounded and the
    user produced real input in the last 60 s) → skip. Real input is
    already resetting Roblox's idle timer; synthetic input would only
    risk a PvP / 1v1 misinput.
  * **Foreground + user truly AFK** (target HWND is foregrounded but
    no system input for >60 s) → slow-mode tick: send a full move +
    keystroke at most once every `SLOW_TICK_INTERVAL_SEC` (default
    15 min, well under Roblox's 20-min kick). The synthetic input
    can't clash with input that isn't happening, but we also don't
    spam — we send the bare minimum to stay alive.

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

# Default cycle bounds (seconds).
DEFAULT_INTERVAL_MIN = 12.0
DEFAULT_INTERVAL_MAX = 35.0

# Movement bounds in pixels.
MOVE_MIN = 5
MOVE_MAX = 15

# Keystroke fires every Nth cycle. Mouse jitter alone resets Roblox's
# engine-level idle timer, so the keystroke is belt-and-suspenders and
# can fire less often to further reduce any clash risk.
KEYSTROKE_EVERY = 5

# When the target window is foregrounded, treat the user as "really AFK"
# (and therefore safe to tick) only after this many seconds of no system
# keyboard / mouse activity.
USER_IDLE_THRESHOLD_SEC = 60.0

# In the foreground-AFK case, cap the synthetic input rate. Roblox's
# idle kick fires at ~20 min, so 15 min leaves a 5-min margin to ride
# out a missed tick without risking the kick.
SLOW_TICK_INTERVAL_SEC = 15 * 60


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
        # Monotonic timestamp of the last synthetic input we sent. Used
        # to throttle the slow-mode (foreground + user AFK) branch to
        # one tick per SLOW_TICK_INTERVAL_SEC.
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

    def _decide(self) -> tuple[Optional[int], bool]:
        """Return (hwnd_to_tick_or_None, is_slow_mode_tick).

        See the module docstring for the three states. `is_slow_mode_tick`
        means we're in the foreground-AFK branch and should send a full
        move + keystroke (since the next opportunity is 15 min away).
        """
        hwnd = self._lookup()
        if not hwnd or not windows._user32.IsWindow(hwnd):
            return None, False
        if windows._user32.GetForegroundWindow() == hwnd:
            if windows.system_idle_seconds() < USER_IDLE_THRESHOLD_SEC:
                return None, False
            if time.monotonic() - self._last_sent_monotonic < SLOW_TICK_INTERVAL_SEC:
                return None, False
            return hwnd, True
        return hwnd, False

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
        # Stagger first tick so a batch of "Enable on All" doesn't fire in lockstep.
        self._stop.wait(random.uniform(0.5, 3.0))
        cycle = 0
        while not self._stop.is_set():
            hwnd, slow = self._decide()
            if hwnd:
                try:
                    self._send_jitter(hwnd)
                    # Slow-mode ticks are sparse (~15 min apart) so always
                    # pair the move with a keystroke. Background ticks
                    # only fire the keystroke every Nth cycle.
                    if slow or cycle % KEYSTROKE_EVERY == 0:
                        self._send_benign_keystroke(hwnd)
                    self._last_sent_monotonic = time.monotonic()
                except Exception:
                    log.exception("anti-AFK tick failed for %s", self._label)
            cycle += 1
            # Event-based sleep so stop() returns promptly.
            self._stop.wait(random.uniform(self._interval_min, self._interval_max))
        self._thread = None
        log.info("anti-AFK loop exited for %s", self._label)
