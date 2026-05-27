"""Roblox web-auth flows: CSRF, authentication tickets, identity lookup."""
import logging
import random
import time
import urllib.parse
from typing import Optional

import requests

log = logging.getLogger(__name__)

_BASE_HEADERS = {
    "User-Agent": "Roblox/WinInet",
    "Referer": "https://www.roblox.com/",
    "Origin": "https://www.roblox.com",
}

# Proxy URL schemes we accept. SOCKS variants are routed by `requests`
# via PySocks, which `requests` imports lazily the first time a
# socks*:// URL is used.
_SUPPORTED_PROXY_SCHEMES = {
    "http", "https",
    "socks5", "socks5h",  # h = hostname resolution through the proxy
    "socks4", "socks4a",
}

# How aggressively we retry on 429. Capped low because Roblox's rate
# windows are minutes-long — pounding on it makes the lockout worse.
_RATE_RETRIES = 2
_RATE_FALLBACK_WAIT_SEC = 12.0


class AuthError(RuntimeError):
    """Generic auth failure (bad cookie, ticket refused, server error)."""


class RateLimitError(AuthError):
    """Roblox returned 429. Includes the suggested wait so the caller can
    back off intelligently instead of treating it like a generic failure."""
    def __init__(self, message: str, retry_after_sec: float):
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


def validate_proxy_url(url: Optional[str]) -> str:
    """Strip + validate a proxy URL. Returns the URL or raises ValueError.

    Empty / None is treated as "no proxy" and returns ''. SOCKS schemes
    additionally require PySocks; we surface a friendlier message if it's
    missing instead of letting `requests` raise its generic error mid-call.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _SUPPORTED_PROXY_SCHEMES:
        raise ValueError(
            f"Unsupported proxy scheme {scheme!r}. "
            "Use http://, https://, socks5://, socks5h://, or socks4://."
        )
    if not parsed.hostname:
        raise ValueError("Proxy URL is missing a host")
    if scheme.startswith("socks"):
        try:
            import socks  # noqa: F401  (PySocks)
        except ImportError as e:
            raise ValueError(
                "SOCKS proxy support needs PySocks. "
                "Install with: pip install PySocks"
            ) from e
    return url


def _session(cookie: str, proxy: Optional[str] = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(_BASE_HEADERS)
    s.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _retry_after_seconds(resp: requests.Response) -> float:
    """Parse the Retry-After header (integer seconds; HTTP-date variant is
    rare from Roblox in practice). Falls back to a fixed wait so we always
    return *something* the caller can sleep on."""
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if raw:
        try:
            return max(1.0, float(raw.strip()))
        except ValueError:
            pass
    return _RATE_FALLBACK_WAIT_SEC


def _raise_for_rate_limit(resp: requests.Response, what: str) -> None:
    """Convert a 429 into a typed RateLimitError. No-op for other statuses."""
    if resp.status_code != 429:
        return
    wait = _retry_after_seconds(resp)
    log.warning("%s rate-limited (429); Retry-After=%.1fs", what, wait)
    raise RateLimitError(
        f"Roblox rate-limited the {what} request (HTTP 429). "
        f"Try again in ~{int(wait)}s, or add a proxy / slow the launch cadence.",
        retry_after_sec=wait,
    )


def _do_with_retry(label: str, call) -> requests.Response:
    """Run `call()` (returns a Response) with limited 429 retries + jitter.

    We retry at most _RATE_RETRIES times, sleeping for the server-suggested
    Retry-After plus a small randomized jitter. The final 429 turns into a
    RateLimitError so the caller can show it cleanly in the UI.
    """
    last: Optional[requests.Response] = None
    for attempt in range(_RATE_RETRIES + 1):
        resp = call()
        last = resp
        if resp.status_code != 429:
            return resp
        if attempt == _RATE_RETRIES:
            break
        wait = _retry_after_seconds(resp) + random.uniform(0.5, 2.0)
        log.info("%s: 429 attempt %d/%d, sleeping %.1fs",
                 label, attempt + 1, _RATE_RETRIES + 1, wait)
        time.sleep(wait)
    assert last is not None
    _raise_for_rate_limit(last, label)
    return last  # unreachable; _raise_for_rate_limit raised


def fetch_csrf_token(session: requests.Session) -> str:
    """An empty POST to /v2/logout returns 403 with the X-CSRF-TOKEN header."""
    resp = _do_with_retry(
        "CSRF probe",
        lambda: session.post("https://auth.roblox.com/v2/logout", timeout=10),
    )
    token = resp.headers.get("x-csrf-token")
    if not token:
        raise AuthError("Could not retrieve CSRF token (cookie may be invalid)")
    return token


def whoami(cookie: str, proxy: Optional[str] = None) -> dict:
    """Validate a cookie and return {id, name, displayName}."""
    s = _session(cookie, proxy=proxy)
    resp = _do_with_retry(
        "cookie validation",
        lambda: s.get("https://users.roblox.com/v1/users/authenticated", timeout=10),
    )
    if resp.status_code == 401:
        raise AuthError("Cookie rejected (401). Re-export .ROBLOSECURITY from the browser.")
    resp.raise_for_status()
    return resp.json()


def fetch_auth_ticket(cookie: str, proxy: Optional[str] = None) -> str:
    """Exchange a .ROBLOSECURITY cookie for a one-shot launch ticket."""
    s = _session(cookie, proxy=proxy)
    csrf = fetch_csrf_token(s)
    headers = {
        "X-CSRF-TOKEN": csrf,
        "RBXAuthenticationNegotiation": "1",
        "Content-Type": "application/json",
    }
    resp = _do_with_retry(
        "auth ticket",
        lambda: s.post(
            "https://auth.roblox.com/v1/authentication-ticket/",
            headers=headers, json={}, timeout=10,
        ),
    )
    if resp.status_code in (401, 403):
        raise AuthError(f"Auth ticket refused ({resp.status_code}); cookie expired?")
    resp.raise_for_status()
    ticket = resp.headers.get("rbx-authentication-ticket")
    if not ticket:
        raise AuthError("No rbx-authentication-ticket header in response")
    log.debug("minted auth ticket (proxy=%s)", bool(proxy))
    return ticket


def place_launcher_url(place_id: int, job_id: Optional[str] = None,
                       browser_tracker_id: Optional[int] = None) -> str:
    """Build the URL the Roblox client expects in its -j argument."""
    params = [
        ("placeId", str(place_id)),
        ("isPlayTogetherGame", "false"),
        ("isPartyLeader", "false"),
    ]
    if job_id:
        params.insert(0, ("request", "RequestGameJob"))
        params.append(("gameId", job_id))
    else:
        params.insert(0, ("request", "RequestGame"))
    if browser_tracker_id is not None:
        params.append(("browserTrackerId", str(browser_tracker_id)))
    query = "&".join(f"{k}={v}" for k, v in params)
    return f"https://assetgame.roblox.com/game/PlaceLauncher.ashx?{query}"


def launchtime_ms() -> int:
    return int(time.time() * 1000)
