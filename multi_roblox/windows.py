"""Process and window helpers for Roblox clients on Windows."""
import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import time

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

SW_RESTORE = 9
ROBLOX_EXE = "RobloxPlayerBeta.exe"


def list_roblox_pids():
    """Return PIDs of all running RobloxPlayerBeta.exe processes."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {ROBLOX_EXE}", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).decode(errors="ignore")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    pids = set()
    for line in out.splitlines():
        m = re.match(r'"[^"]+","(\d+)"', line.strip())
        if m:
            pids.add(int(m.group(1)))
    return pids


def find_window_for_pid(pid, timeout=30.0):
    """Wait up to `timeout` seconds for a visible top-level window owned by pid."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_hwnd(pid)
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return None


def _find_hwnd(target_pid):
    found = [None]

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != target_pid:
            return True
        length = _user32.GetWindowTextW(hwnd, None, 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title and "Roblox" in title:
            found[0] = hwnd
            return False
        return True

    _user32.EnumWindows(_EnumWindowsProc(cb), 0)
    return found[0]


def focus_window(hwnd):
    """Bring a window to the foreground reliably across focus-stealing checks."""
    if not hwnd:
        return False
    _user32.ShowWindow(hwnd, SW_RESTORE)
    fg = _user32.GetForegroundWindow()
    fg_tid = _user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD()))
    cur_tid = _kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(_user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        _user32.BringWindowToTop(hwnd)
        return bool(_user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            _user32.AttachThreadInput(cur_tid, fg_tid, False)


def kill_pid(pid):
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )


def launch_uri(uri):
    """Open a roblox:// URI via the registered protocol handler."""
    os.startfile(uri)
