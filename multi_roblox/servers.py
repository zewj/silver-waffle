"""Roblox public game/server API helpers for server hopping."""
import re
from typing import Optional

import requests

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Roblox/WinInet", "Accept": "application/json"})

_PLACE_URL_RE = re.compile(r"/games/(\d+)")


def parse_place_id(value: str) -> Optional[int]:
    """Accept a numeric placeId or a roblox.com /games/<id>/ URL."""
    value = value.strip()
    if value.isdigit():
        return int(value)
    m = _PLACE_URL_RE.search(value)
    return int(m.group(1)) if m else None


def fetch_servers(place_id: int, limit: int = 100, sort: str = "Asc"):
    """Return the public servers list for a place. Raises on network failure."""
    url = f"https://games.roblox.com/v1/games/{place_id}/servers/Public"
    resp = _SESSION.get(url, params={"sortOrder": sort, "limit": limit}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", [])


def pick_server(place_id: int, exclude_job_ids=()) -> Optional[dict]:
    """Choose a non-full public server that isn't in the exclude set.

    Prefers servers with the most headroom but at least one player, which gives
    a smoother hop than dropping into a brand-new empty server.
    """
    exclude = set(exclude_job_ids or ())
    candidates = []
    for srv in fetch_servers(place_id):
        job_id = srv.get("id")
        if not job_id or job_id in exclude:
            continue
        playing = srv.get("playing", 0)
        max_players = srv.get("maxPlayers", 0)
        if max_players and playing < max_players:
            candidates.append((max_players - playing, playing, srv))
    if not candidates:
        return None
    # prefer non-empty servers with the most slots free
    candidates.sort(key=lambda c: (c[1] == 0, -c[0]))
    return candidates[0][2]


def join_uri(place_id: int, job_id: str) -> str:
    return f"roblox://placeId={place_id}&gameInstanceId={job_id}"


def launch_uri(place_id: int) -> str:
    return f"roblox://placeId={place_id}"
