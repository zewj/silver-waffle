"""Discord webhook notifications.

Fires off a POST in a background thread so the manager's stats poll never
blocks on network. Errors are logged but never propagate; a webhook
delivery failure should not crash the app.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Accept both discord.com and the legacy discordapp.com host, plus the
# ptb / canary subdomains people sometimes end up with after copying
# from the developer portal.
_WEBHOOK_RE = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+/?$"
)

USERNAME = "Multi Roblox Manager"
COLOR_RED = 0x_DE_4F_33    # crashed
COLOR_GREEN = 0x_2E_A0_43  # test / success
COLOR_BLUE = 0x_00_78_D4   # info


def validate_url(url: Optional[str]) -> str:
    """Strip + sanity-check a webhook URL. Returns it or raises ValueError."""
    url = (url or "").strip()
    if not url:
        return ""
    if not _WEBHOOK_RE.match(url):
        raise ValueError(
            "Doesn't look like a Discord webhook URL.\n"
            "Expected: https://discord.com/api/webhooks/<id>/<token>"
        )
    return url


def _post_sync(url: str, payload: dict, timeout: float = 6.0) -> tuple[bool, str]:
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        log.warning("webhook POST raised: %s", e)
        return False, str(e)
    if resp.status_code >= 400:
        log.warning("webhook POST %s: %s", resp.status_code, resp.text[:200])
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    log.debug("webhook delivered (%s)", resp.status_code)
    return True, "OK"


def _post_async(url: str, payload: dict) -> None:
    threading.Thread(
        target=_post_sync, args=(url, payload), daemon=True,
    ).start()


def _embed_crash(instance) -> dict:
    account_label = instance.account.label() if instance.account else "(signed-in)"
    return {
        "title": "Roblox instance crashed",
        "description": (
            f"**{instance.label}** ({account_label}) is no longer running."
        ),
        "color": COLOR_RED,
        "fields": [
            {"name": "Account", "value": account_label, "inline": True},
            {"name": "Place ID", "value": str(instance.place_id), "inline": True},
            {"name": "PID", "value": str(instance.pid) if instance.pid else "—",
             "inline": True},
            {"name": "Server (jobId)", "value": instance.job_id or "—",
             "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def post_crash(url: str, instance) -> None:
    """Fire a crash notification for `instance`. No-op if url is empty."""
    if not url:
        return
    payload = {"username": USERNAME, "embeds": [_embed_crash(instance)]}
    _post_async(url, payload)


def post_test_sync(url: str) -> tuple[bool, str]:
    """Synchronous test post so the settings dialog can show the result."""
    payload = {
        "username": USERNAME,
        "embeds": [{
            "title": "Test notification",
            "description": "If you see this, your webhook is working.",
            "color": COLOR_GREEN,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
    return _post_sync(url, payload)
