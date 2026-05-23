"""Launch Roblox clients with a per-account auth ticket.

Two launch paths:
  * `launch_protocol_with_ticket` — opens a `roblox-player:` URL through the
    registered protocol handler. RobloxPlayerLauncher.exe handles updates,
    Hyperion parent-process expectations, and registry plumbing, then spawns
    RobloxPlayerBeta.exe with the ticket. This is the stable default.
  * `launch_with_ticket` — runs RobloxPlayerBeta.exe directly. Faster start
    and lighter, but skips the launcher's setup; kept as a fallback.

Both paths authenticate the session via the ticket, so multi-account
behavior is identical between them.
"""
import logging
import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from . import auth

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def _newest_under_versions(filename: str) -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    versions_dir = Path(local) / "Roblox" / "Versions"
    if not versions_dir.is_dir():
        return None
    candidates = []
    for child in versions_dir.iterdir():
        exe = child / filename
        if exe.is_file():
            candidates.append((exe.stat().st_mtime, exe))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_player_exe() -> Optional[Path]:
    """Newest RobloxPlayerBeta.exe under %LOCALAPPDATA%\\Roblox\\Versions."""
    return _newest_under_versions("RobloxPlayerBeta.exe")


def find_launcher_exe() -> Optional[Path]:
    """Newest RobloxPlayerLauncher.exe under %LOCALAPPDATA%\\Roblox\\Versions."""
    return _newest_under_versions("RobloxPlayerLauncher.exe")


def detect_version() -> Optional[str]:
    """Return the Roblox version folder (e.g. 'version-abc123') if discoverable."""
    exe = find_player_exe()
    if not exe:
        return None
    parent = exe.parent.name
    return parent[len("version-"):] if parent.startswith("version-") else parent


def build_args(exe: Path, ticket: str, place_id: int,
               job_id: Optional[str] = None,
               browser_tracker_id: int = 0) -> list[str]:
    place_url = auth.place_launcher_url(place_id, job_id=job_id,
                                        browser_tracker_id=browser_tracker_id or None)
    return [
        str(exe),
        "--play",
        "-a", "https://www.roblox.com/Login/Negotiate.ashx",
        "-t", ticket,
        "-j", place_url,
        "-b", str(browser_tracker_id),
        f"--launchtime={auth.launchtime_ms()}",
        "--rloc", "en_us",
        "--gloc", "en_us",
    ]


def spawn(args: list[str], env: Optional[dict] = None) -> subprocess.Popen:
    """Spawn the Roblox client detached so the manager outlives it cleanly."""
    log.info("spawn: %s", args[0])
    return subprocess.Popen(
        args,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
        close_fds=True,
    )


def launch_with_ticket(ticket: str, place_id: int,
                       job_id: Optional[str] = None,
                       env: Optional[dict] = None) -> subprocess.Popen:
    exe = find_player_exe()
    if not exe:
        raise FileNotFoundError(
            "RobloxPlayerBeta.exe not found. Install Roblox and run it once."
        )
    bti = int(time.time() * 1000) & 0x7FFFFFFF
    return spawn(build_args(exe, ticket, place_id, job_id=job_id, browser_tracker_id=bti),
                 env=env)


def protocol_url_with_ticket(ticket: str, place_id: int,
                             job_id: Optional[str] = None,
                             browser_tracker_id: int = 0) -> str:
    """Build the `roblox-player:` URL the registered protocol handler expects."""
    place_url = auth.place_launcher_url(
        place_id, job_id=job_id,
        browser_tracker_id=browser_tracker_id or None,
    )
    return "+".join([
        "roblox-player:1",
        "launchmode:play",
        f"gameinfo:{ticket}",
        f"launchtime:{auth.launchtime_ms()}",
        f"placelauncherurl:{urllib.parse.quote(place_url, safe='')}",
        f"browsertrackerid:{browser_tracker_id}",
        "robloxLocale:en_us",
        "gameLocale:en_us",
    ])


def launch_protocol_with_ticket(ticket: str, place_id: int,
                                job_id: Optional[str] = None,
                                env: Optional[dict] = None) -> None:
    """Open the protocol URL so RobloxPlayerLauncher.exe handles the launch.

    When `env` is provided we invoke RobloxPlayerLauncher.exe directly with
    that environment (so LOCALAPPDATA can point at a per-account dir).
    With no env we hand off via the shell, which is fine for the shared
    launcher's-signed-in-account flow.
    """
    bti = int(time.time() * 1000) & 0x7FFFFFFF
    url = protocol_url_with_ticket(ticket, place_id, job_id=job_id, browser_tracker_id=bti)
    launcher_exe = find_launcher_exe()
    if env is not None and launcher_exe is not None:
        log.info("protocol launch via %s (env override applied)", launcher_exe)
        spawn([str(launcher_exe), url], env=env)
        return
    log.info("protocol launch via shell handoff (no env override)")
    os.startfile(url)
