"""Holds the Roblox singleton mutex so multiple clients can run."""
import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

# Roblox's singleton check opens this named mutex; if any process already holds
# it, the new client skips the "already running" exit path.
_MUTEX_NAME = "ROBLOX_singletonEvent"


class SingletonMutex:
    def __init__(self):
        self._handle = None

    def acquire(self):
        if self._handle:
            return
        handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle

    def release(self):
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()
