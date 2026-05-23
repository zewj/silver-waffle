"""Entrypoint for the multi Roblox instance manager (Windows only)."""
import platform
import sys


def main():
    if platform.system() != "Windows":
        print("This tool only runs on Windows; Roblox client is Windows-only.", file=sys.stderr)
        sys.exit(1)
    from multi_roblox.gui import main as run
    run()


if __name__ == "__main__":
    main()
