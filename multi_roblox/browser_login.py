"""Embedded-browser sign-in that harvests .ROBLOSECURITY after login.

The user signs in to Roblox's own page however they want (password+MFA,
QR code via Roblox mobile, passkey). When the post-login redirect lands,
we read the cookie jar from the WebView2 backend and hand the
.ROBLOSECURITY cookie back to the caller. pywebview's event loop wants
the main thread, so we run the window in a subprocess and print the
cookie on stdout — that keeps it isolated from the Tk main loop.
"""
from __future__ import annotations

import http.cookies
import logging
import subprocess
import sys
from typing import Optional

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.roblox.com/login"
# Roblox redirects to /home (and sometimes the discover/dashboard pages)
# once authentication completes.
POST_LOGIN_HINTS = ("/home", "/discover", "/dashboard")


def is_available() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


def install_hint() -> str:
    return (
        "Browser sign-in needs pywebview.\n"
        "Install with: pip install pywebview\n"
        "(WebView2 runtime is preinstalled on Windows 10 and 11.)"
    )


def harvest_via_subprocess(timeout: float = 300.0) -> Optional[str]:
    """Open the login window in a child process; return the cookie or None."""
    # When frozen by PyInstaller, sys.executable is the bundled .exe, not
    # Python — `-m` won't work. Re-invoke our own entry point with the
    # browser-login flag instead. main.py dispatches it to _run_webview.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--browser-login-harvest"]
    else:
        cmd = [sys.executable, "-m", "multi_roblox.browser_login", "--harvest"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    cookie = proc.stdout.strip()
    return cookie or None


def _extract_cookie(cookies) -> Optional[str]:
    """Pull .ROBLOSECURITY from whatever shape pywebview hands us."""
    for entry in cookies or []:
        if isinstance(entry, http.cookies.SimpleCookie) and ".ROBLOSECURITY" in entry:
            return entry[".ROBLOSECURITY"].value
        # Defensive: some pywebview backends return raw dict / object cookies.
        name = getattr(entry, "name", None)
        if name == ".ROBLOSECURITY":
            return getattr(entry, "value", None)
        if isinstance(entry, dict) and entry.get("name") == ".ROBLOSECURITY":
            return entry.get("value")
    return None


def _run_webview():
    import webview

    captured: list[str] = []

    def on_loaded():
        try:
            url = window.get_current_url() or ""
        except Exception:
            log.exception("could not read current url from webview")
            return
        if not any(hint in url for hint in POST_LOGIN_HINTS):
            return
        try:
            cookie = _extract_cookie(window.get_cookies())
        except Exception:
            log.exception("could not read cookies from webview")
            return
        if cookie:
            captured.append(cookie)
            try:
                window.destroy()
            except Exception:
                log.exception("could not destroy webview window cleanly")

    window = webview.create_window(
        "Sign in to Roblox",
        LOGIN_URL,
        width=520, height=760, resizable=True,
    )
    window.events.loaded += on_loaded
    webview.start()

    if captured:
        sys.stdout.write(captured[0])
        sys.stdout.flush()
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    if "--harvest" in sys.argv:
        _run_webview()
    else:
        print("usage: python -m multi_roblox.browser_login --harvest", file=sys.stderr)
        sys.exit(2)
