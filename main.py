"""Entrypoint for the multi Roblox instance manager (Windows only)."""
import logging
import platform
import sys


def main():
    # Sub-mode: when frozen by PyInstaller, the GUI re-invokes the same
    # exe with this flag instead of `python -m multi_roblox.browser_login`,
    # which doesn't work in a bundled app. Dispatch here before the
    # platform check so the webview can run independently.
    if "--browser-login-harvest" in sys.argv:
        from multi_roblox import browser_login
        browser_login._run_webview()
        return

    if platform.system() != "Windows":
        print("This tool only runs on Windows; Roblox client is Windows-only.", file=sys.stderr)
        sys.exit(1)
    from multi_roblox import dpi, logging_setup
    # Per-Monitor-V2 awareness must be enabled before any Tk window is
    # constructed; otherwise Windows bitmap-stretches the first window
    # and resize feels laggy thereafter.
    scale = dpi.enable()
    log_path = logging_setup.setup()
    log = logging.getLogger("main")
    try:
        from multi_roblox.gui import run
        run(scale=scale)
    except Exception:
        log.exception("fatal error; see %s for details", log_path)
        raise


if __name__ == "__main__":
    main()
