"""DPAPI wrappers for per-user encryption of stored credentials."""
import ctypes
from ctypes import wintypes


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = _crypt32.CryptProtectData.argtypes
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
_kernel32.LocalFree.restype = wintypes.HLOCAL

_FLAG_UI_FORBIDDEN = 0x01


def _blob(data: bytes) -> _DATA_BLOB:
    buf = (ctypes.c_byte * len(data))(*data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _read_blob(blob: _DATA_BLOB) -> bytes:
    try:
        return bytes(ctypes.string_at(blob.pbData, blob.cbData))
    finally:
        _kernel32.LocalFree(blob.pbData)


def protect(plaintext: bytes) -> bytes:
    out = _DATA_BLOB()
    if not _crypt32.CryptProtectData(
        ctypes.byref(_blob(plaintext)), "multi_roblox", None, None, None,
        _FLAG_UI_FORBIDDEN, ctypes.byref(out),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return _read_blob(out)


def unprotect(ciphertext: bytes) -> bytes:
    out = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(_blob(ciphertext)), None, None, None, None,
        _FLAG_UI_FORBIDDEN, ctypes.byref(out),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return _read_blob(out)
