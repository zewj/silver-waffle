"""Entrypoint for the multi Roblox instance manager (Windows only)."""
import logging
import platform
import sys


def main():
    if platform.system() != "Windows":
        print("This tool only runs on Windows; Roblox client is Windows-only.", file=sys.stderr)
        sys.exit(1)
    from multi_roblox import logging_setup
    log_path = logging_setup.setup()
    log = logging.getLogger("main")
    try:
        from multi_roblox.gui import main as run
        run()
    except Exception:
        log.exception("fatal error; see %s for details", log_path)
        raise


if __name__ == "__main__":
    main()
