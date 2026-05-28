"""Remove everything the app has put on disk.

`wipe()` removes `%APPDATA%\\MultiRobloxManager\\` and its contents —
accounts, config, logs, per-account profile dirs. Anything outside
that folder (Roblox install, registry, exe) is left alone.

`reset_roblox_login_state()` additionally clears the Roblox client's
own persisted sign-in state on this machine:

  * `%LOCALAPPDATA%\\Roblox\\LocalStorage\\` — cookies + session state
  * `HKCU\\Software\\Roblox\\RobloxStudioBrowser` — auth/account hints
    the launcher reads when the browser fires a `roblox-player:` URL

This is the "browser launches the wrong account" fix: when our
managed launches authenticate via tickets, the Roblox client writes
who-is-currently-signed-in markers into both of those locations. Even
after our app folder is gone, those markers stick and the browser's
Play button keeps using them. Clearing them puts the system back in
the "not signed in to Roblox yet on this device" state, so the
browser launcher will use whoever you're signed in as in your browser
again.

This DOES NOT touch:
  * `%LOCALAPPDATA%\\Roblox\\Versions\\` — the actual game install
  * `GlobalBasicSettings_*.xml` — your graphics / keybinds
  * Anything outside the keys / paths listed above

Safety: each per-account profile contains a directory junction
(`<acc>\\Roblox\\Versions` → real `%LOCALAPPDATA%\\Roblox\\Versions`).
A naive `shutil.rmtree` *can* traverse junctions on some Python /
Windows combinations, which would delete the real Roblox install —
catastrophic. We sweep junctions first via `cmd /c rmdir` (Windows-
native, removes the junction without touching its target) and only
then `rmtree` the parent.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def app_root() -> Path:
    """`%APPDATA%\\MultiRobloxManager\\`. Returns a path even if missing."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "MultiRobloxManager"


def _iter_junctions(root: Path) -> Iterable[Path]:
    """Yield every directory junction under `root` (recursive).

    Uses `is_symlink()` which returns True for Windows directory
    junctions in Python 3.8+.
    """
    if not root.exists():
        return
    for path in root.rglob("*"):
        try:
            if path.is_symlink() and path.is_dir():
                yield path
        except OSError:
            continue


def _rmdir_junction(path: Path) -> bool:
    """Remove a directory junction without touching its target.

    On Windows `cmd /c rmdir /Q` treats junctions as links (deletes the
    link only); on non-Windows (tests, future cross-platform) we just
    unlink the symlink. Returns True on success.
    """
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "rmdir", "/Q", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW, check=False,
            )
            return result.returncode == 0
        # POSIX equivalent for the smoke test: symlinks unlink cleanly.
        os.unlink(str(path)) if path.is_symlink() else os.rmdir(str(path))
        return True
    except OSError:
        return False


def wipe() -> dict:
    """Delete the entire app root. Returns a summary dict.

    {
        "root": Path,
        "existed": bool,           # was anything there to begin with?
        "junctions_removed": int,
        "removed": [str, ...],     # paths that were deleted
        "errors": [(str, str)],    # (path, reason) for anything we failed on
    }
    """
    root = app_root()
    summary: dict = {
        "root": root,
        "existed": root.exists(),
        "junctions_removed": 0,
        "removed": [],
        "errors": [],
    }
    if not summary["existed"]:
        log.info("wipe: nothing to do (%s does not exist)", root)
        return summary

    # First pass: clear junctions so rmtree never follows one.
    for junction in _iter_junctions(root):
        if _rmdir_junction(junction):
            summary["junctions_removed"] += 1
            summary["removed"].append(str(junction))
        else:
            summary["errors"].append((str(junction), "rmdir junction failed"))

    # Second pass: nuke the directory itself. Try shutil first (gives
    # readable Python tracebacks on failure); fall back to cmd's rmdir
    # which is more forgiving for files that were briefly in use.
    try:
        shutil.rmtree(root)
        summary["removed"].append(str(root))
    except Exception as e:
        log.warning("shutil.rmtree failed for %s: %s; falling back to cmd", root, e)
        try:
            subprocess.run(
                ["cmd", "/c", "rmdir", "/S", "/Q", str(root)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW, check=True,
            )
            summary["removed"].append(str(root))
        except Exception as e2:
            summary["errors"].append((str(root), f"{e} / fallback: {e2}"))

    log.info("wipe complete: removed=%d junctions=%d errors=%d",
             len(summary["removed"]), summary["junctions_removed"],
             len(summary["errors"]))
    return summary


# ---------------------------------------------------------------------------
# Roblox-side login state reset


def _roblox_localstorage_dir() -> Path:
    """`%LOCALAPPDATA%\\Roblox\\LocalStorage\\`."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Roblox" / "LocalStorage"


def _delete_localstorage() -> tuple[bool, str]:
    """Remove the LocalStorage folder. Returns (succeeded, note)."""
    target = _roblox_localstorage_dir()
    if not target.exists():
        return True, f"{target} (already gone)"
    try:
        shutil.rmtree(target)
        return True, str(target)
    except Exception as e:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["cmd", "/c", "rmdir", "/S", "/Q", str(target)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW, check=True,
                )
                return True, str(target)
            except Exception as e2:
                return False, f"{target}: {e} / fallback: {e2}"
        return False, f"{target}: {e}"


def _delete_registry_key(subkey: str) -> tuple[bool, str]:
    """Recursively delete an HKCU subkey. Returns (succeeded, note)."""
    if os.name != "nt":
        return True, f"HKCU\\{subkey} (not Windows; skipped)"
    try:
        import winreg  # type: ignore
    except ImportError:
        return False, f"HKCU\\{subkey}: winreg unavailable"
    try:
        _recursive_reg_delete(winreg.HKEY_CURRENT_USER, subkey, winreg)
        return True, f"HKCU\\{subkey}"
    except FileNotFoundError:
        return True, f"HKCU\\{subkey} (already gone)"
    except OSError as e:
        return False, f"HKCU\\{subkey}: {e}"


def _recursive_reg_delete(hive, subkey: str, winreg) -> None:
    """winreg has no built-in 'delete tree' — peel subkeys off until empty."""
    # Open with KEY_ALL_ACCESS so we can enumerate + delete subkeys.
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break  # no more subkeys
                _recursive_reg_delete(hive, f"{subkey}\\{child}", winreg)
    except FileNotFoundError:
        return
    # All children gone — now drop the key itself.
    winreg.DeleteKey(hive, subkey)


# Keys to nuke. Keep this list narrow — we only want to clear sign-in
# state, not preferences / graphics settings (which live in different
# keys and the user probably wants to keep).
_LOGIN_REG_KEYS = (
    r"Software\Roblox\RobloxStudioBrowser",
)


def reset_roblox_login_state() -> dict:
    """Clear the Roblox client's persisted sign-in state on this machine.

    Returns a summary dict with the same shape as `wipe()`.
    """
    summary: dict = {"removed": [], "errors": []}

    ok, note = _delete_localstorage()
    (summary["removed"] if ok else summary["errors"]).append(note if ok else (note, ""))

    for key in _LOGIN_REG_KEYS:
        ok, note = _delete_registry_key(key)
        if ok:
            summary["removed"].append(note)
        else:
            summary["errors"].append((note, ""))

    log.info("roblox login reset: removed=%d errors=%d",
             len(summary["removed"]), len(summary["errors"]))
    return summary
