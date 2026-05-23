"""Per-account LOCALAPPDATA isolation for Roblox clients.

Each account gets its own `%APPDATA%/MultiRobloxManager/data/<user_id>/`
directory. When we spawn a client, `LOCALAPPDATA` is overridden to point
at `<account_dir>` so Roblox writes its cookies/cache/logs into that
folder instead of stomping over another account's state.

The Roblox binaries live in `<real LOCALAPPDATA>/Roblox/Versions/`; we
expose them inside each account dir via a NTFS directory junction
(`mklink /J`, no admin required) so the launcher and player still find
the right exes.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def data_root() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "MultiRobloxManager" / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def account_data_dir(user_id: int) -> Path:
    p = data_root() / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_account_dir(user_id: int) -> Path:
    """Create the per-account dir and the Versions junction inside it."""
    base = account_data_dir(user_id)
    roblox = base / "Roblox"
    roblox.mkdir(parents=True, exist_ok=True)

    real_local = os.environ.get("LOCALAPPDATA")
    if real_local:
        real_versions = Path(real_local) / "Roblox" / "Versions"
        junction = roblox / "Versions"
        if real_versions.exists() and not junction.exists():
            try:
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(junction), str(real_versions)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW, check=True,
                )
                log.info("created junction %s -> %s", junction, real_versions)
            except subprocess.CalledProcessError as e:
                log.warning("mklink failed for %s (%s); isolation may be partial",
                            junction, e)
    return base


def env_for_account(user_id: Optional[int]) -> Optional[dict]:
    """Return a child-process env with LOCALAPPDATA pointing at the account dir.

    Returns None when no isolation is requested (i.e. no account selected),
    so callers can keep the inherited environment.
    """
    if user_id is None:
        return None
    base = ensure_account_dir(user_id)
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(base)
    return env
