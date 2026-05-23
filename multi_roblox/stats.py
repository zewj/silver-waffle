"""Process stats + liveness via psutil."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False


@dataclass
class Sample:
    alive: bool
    cpu_percent: float = 0.0
    rss_mb: float = 0.0


_handles: dict[int, "psutil.Process"] = {}


def sample(pid: Optional[int]) -> Sample:
    """Return a one-shot stats sample for `pid`."""
    if not pid or not HAVE_PSUTIL:
        return Sample(alive=bool(pid))
    proc = _handles.get(pid)
    if proc is None:
        try:
            proc = psutil.Process(pid)
            # Prime CPU sampling; psutil's first reading is always 0.
            proc.cpu_percent(interval=None)
            _handles[pid] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return Sample(alive=False)
    try:
        with proc.oneshot():
            return Sample(
                alive=proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE,
                cpu_percent=proc.cpu_percent(interval=None),
                rss_mb=proc.memory_info().rss / (1024 * 1024),
            )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        _handles.pop(pid, None)
        return Sample(alive=False)


def forget(pid: Optional[int]):
    if pid:
        _handles.pop(pid, None)
