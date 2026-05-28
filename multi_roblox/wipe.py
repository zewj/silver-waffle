"""Remove everything the app has put on disk.

Wipes `%APPDATA%\\MultiRobloxManager\\` and its contents — accounts,
config, logs, per-account profile directories. Anything outside that
folder (the real Roblox install, the user's `%LOCALAPPDATA%\\Roblox`,
the registry, the exe itself) is intentionally left alone.

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
