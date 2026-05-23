"""Centralized logging: rotating file + console handlers.

Every module uses `log = logging.getLogger(__name__)`. The user-facing log
file lives next to the config and rolls at 2 MB, keeping 5 backups.
"""
import logging
import logging.handlers
import os
from pathlib import Path


def logs_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "MultiRobloxManager" / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def setup(level: int = logging.INFO) -> Path:
    log_path = logs_dir() / "manager.log"
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler)
               and not isinstance(h, logging.handlers.RotatingFileHandler)
               for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return log_path
