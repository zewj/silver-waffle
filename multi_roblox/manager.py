"""Tracks launched Roblox instances and orchestrates hops."""
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import servers, windows
from .mutex import SingletonMutex


@dataclass
class Instance:
    label: str
    place_id: int
    pid: Optional[int] = None
    hwnd: Optional[int] = None
    job_id: Optional[str] = None
    recent_jobs: list = field(default_factory=list)

    def remember_job(self, job_id: str, cap: int = 10):
        if not job_id:
            return
        self.job_id = job_id
        if job_id in self.recent_jobs:
            self.recent_jobs.remove(job_id)
        self.recent_jobs.append(job_id)
        del self.recent_jobs[:-cap]


class InstanceManager:
    """Owns the singleton mutex and the set of launched instances."""

    # Roblox throttles join attempts; spacing launches avoids 'too many attempts'
    # style errors when stacking instances or hopping quickly.
    LAUNCH_COOLDOWN = 2.5

    def __init__(self):
        self._mutex = SingletonMutex()
        self._lock = threading.Lock()
        self._last_launch = 0.0
        self.instances: list[Instance] = []

    def start(self):
        self._mutex.acquire()

    def shutdown(self):
        with self._lock:
            for inst in self.instances:
                if inst.pid:
                    windows.kill_pid(inst.pid)
            self.instances.clear()
        self._mutex.release()

    def _respect_cooldown(self):
        wait = self.LAUNCH_COOLDOWN - (time.time() - self._last_launch)
        if wait > 0:
            time.sleep(wait)
        self._last_launch = time.time()

    def add_instance(self, label: str, place_id: int, job_id: Optional[str] = None) -> Instance:
        inst = Instance(label=label, place_id=place_id)
        with self._lock:
            self.instances.append(inst)
        self._launch_into(inst, job_id)
        return inst

    def _launch_into(self, inst: Instance, job_id: Optional[str]):
        self._respect_cooldown()
        before = windows.list_roblox_pids()
        uri = servers.join_uri(inst.place_id, job_id) if job_id else servers.launch_uri(inst.place_id)
        windows.launch_uri(uri)
        inst.remember_job(job_id) if job_id else None

        # Wait for the new RobloxPlayerBeta.exe to appear (launcher spawns it).
        deadline = time.time() + 45.0
        new_pid = None
        while time.time() < deadline:
            after = windows.list_roblox_pids()
            diff = after - before
            if diff:
                new_pid = sorted(diff)[-1]
                break
            time.sleep(0.5)
        inst.pid = new_pid
        if new_pid:
            inst.hwnd = windows.find_window_for_pid(new_pid, timeout=30.0)

    def focus(self, inst: Instance) -> bool:
        if not inst.hwnd and inst.pid:
            inst.hwnd = windows.find_window_for_pid(inst.pid, timeout=2.0)
        return windows.focus_window(inst.hwnd) if inst.hwnd else False

    def close(self, inst: Instance):
        if inst.pid:
            windows.kill_pid(inst.pid)
        with self._lock:
            if inst in self.instances:
                self.instances.remove(inst)

    def server_hop(self, inst: Instance) -> Optional[str]:
        """Kill the instance and relaunch into a fresh public server.

        Returns the new jobId on success, None if no server was available.
        """
        srv = servers.pick_server(inst.place_id, exclude_job_ids=inst.recent_jobs)
        if not srv:
            return None
        if inst.pid:
            windows.kill_pid(inst.pid)
            inst.pid = None
            inst.hwnd = None
            # Give the OS a moment to tear down the process before relaunch.
            time.sleep(1.0)
        self._launch_into(inst, srv["id"])
        return srv["id"]

    def cycle_focus(self) -> Optional[Instance]:
        """Rotate keyboard focus to the next live instance."""
        live = [i for i in self.instances if i.hwnd]
        if not live:
            return None
        current = windows._user32.GetForegroundWindow()
        try:
            idx = next(i for i, inst in enumerate(live) if inst.hwnd == current)
            target = live[(idx + 1) % len(live)]
        except StopIteration:
            target = live[0]
        self.focus(target)
        return target
