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
import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from . import auth

_CREATE_NO_WINDOW = 0x08000000


def find_player_exe() -> Optional[Path]:
    """Return the newest RobloxPlayerBeta.exe under %LOCALAPPDATA%\\Roblox\\Versions."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    versions_dir = Path(local) / "Roblox" / "Versions"
    if not versions_dir.is_dir():
        return None
    candidates = []
    for child in versions_dir.iterdir():
        exe = child / "RobloxPlayerBeta.exe"
        if exe.is_file():
            candidates.append((exe.stat().st_mtime, exe))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


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


def spawn(args: list[str]) -> subprocess.Popen:
    """Spawn the Roblox client detached so the manager outlives it cleanly."""
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
        close_fds=True,
    )


def launch_with_ticket(ticket: str, place_id: int,
                       job_id: Optional[str] = None) -> subprocess.Popen:
    exe = find_player_exe()
    if not exe:
        raise FileNotFoundError(
            "RobloxPlayerBeta.exe not found. Install Roblox and run it once."
        )
    # A loose browser tracker id, unique-ish per launch — not security sensitive.
    bti = int(time.time() * 1000) & 0x7FFFFFFF
    return spawn(build_args(exe, ticket, place_id, job_id=job_id, browser_tracker_id=bti))


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
                                job_id: Optional[str] = None) -> None:
    """Open the protocol URL so RobloxPlayerLauncher.exe handles the launch."""
    bti = int(time.time() * 1000) & 0x7FFFFFFF
    os.startfile(protocol_url_with_ticket(ticket, place_id, job_id=job_id,
                                          browser_tracker_id=bti))
