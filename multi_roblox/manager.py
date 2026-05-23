"""Tracks launched Roblox instances and orchestrates hops."""
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import auth, launcher, servers, windows
from .accounts import Account
from .mutex import SingletonMutex


@dataclass
class Instance:
    label: str
    place_id: int
    account: Optional[Account] = None
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

    # Roblox throttles join attempts. Spacing launches avoids the
    # "joining too quickly" / "already running" errors.
    LAUNCH_COOLDOWN = 2.5
    # How long to wait for a new RobloxPlayerBeta.exe to appear after a
    # launch. Protocol mode goes through RobloxPlayerLauncher.exe which may
    # run an update first, so we give it more headroom than direct exec.
    PROTOCOL_PID_TIMEOUT = 90.0
    DIRECT_PID_TIMEOUT = 30.0
    # Wait this long for the new client's window before killing the old one
    # during a server hop. Keeps the visible gap minimal.
    HOP_OVERLAP_TIMEOUT = 35.0

    def __init__(self):
        self._mutex = SingletonMutex()
        self._lock = threading.Lock()
        self._last_launch = 0.0
        # Default. The GUI overrides this from the persisted config.
        self.launch_mode = "protocol"
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

    def _wait_for_new_pid(self, before: set, timeout: float) -> Optional[int]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            diff = windows.list_roblox_pids() - before
            if diff:
                return sorted(diff)[-1]
            time.sleep(0.4)
        return None

    def _launch_process(self, account: Optional[Account], place_id: int,
                        job_id: Optional[str]) -> Optional[int]:
        """Spawn one client, returning the resolved RobloxPlayerBeta PID.

        Protocol launches are preferred because RobloxPlayerLauncher handles
        version updates and Hyperion's parent-process expectations. If the
        protocol launch doesn't produce a new PID we fall back to direct
        exec with a freshly minted ticket (auth tickets are single-use).
        """
        self._respect_cooldown()

        if not account:
            # No saved account → protocol handoff using whoever is signed in.
            before = windows.list_roblox_pids()
            uri = servers.join_uri(place_id, job_id) if job_id else servers.launch_uri(place_id)
            windows.launch_uri(uri)
            return self._wait_for_new_pid(before, self.PROTOCOL_PID_TIMEOUT)

        if self.launch_mode == "direct":
            before = windows.list_roblox_pids()
            ticket = auth.fetch_auth_ticket(account.cookie())
            launcher.launch_with_ticket(ticket, place_id, job_id=job_id)
            return self._wait_for_new_pid(before, self.DIRECT_PID_TIMEOUT)

        # protocol-first with direct fallback
        before = windows.list_roblox_pids()
        ticket = auth.fetch_auth_ticket(account.cookie())
        launcher.launch_protocol_with_ticket(ticket, place_id, job_id=job_id)
        pid = self._wait_for_new_pid(before, self.PROTOCOL_PID_TIMEOUT)
        if pid:
            return pid
        # Protocol launch produced nothing; the first ticket is now consumed
        # or expired, so mint a fresh one and try direct exec.
        before = windows.list_roblox_pids()
        ticket = auth.fetch_auth_ticket(account.cookie())
        launcher.launch_with_ticket(ticket, place_id, job_id=job_id)
        return self._wait_for_new_pid(before, self.DIRECT_PID_TIMEOUT)

    def add_instance(self, label: str, place_id: int,
                     account: Optional[Account] = None,
                     job_id: Optional[str] = None) -> Instance:
        inst = Instance(label=label, place_id=place_id, account=account)
        with self._lock:
            self.instances.append(inst)
        pid = self._launch_process(account, place_id, job_id)
        inst.pid = pid
        if pid:
            inst.hwnd = windows.find_window_for_pid(pid, timeout=30.0)
        if job_id:
            inst.remember_job(job_id)
        return inst

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
        """Overlap-style hop: bring the new client up before killing the old.

        The new RobloxPlayerBeta gets its own auth ticket and joins the chosen
        public server directly. We only terminate the previous PID once the
        new window appears (or after a timeout), which avoids the
        "Roblox closed and reopened" flash of a kill-then-relaunch hop.
        Returns the new jobId on success.
        """
        srv = servers.pick_server(inst.place_id, exclude_job_ids=inst.recent_jobs)
        if not srv:
            return None
        new_job = srv["id"]
        old_pid = inst.pid
        new_pid = self._launch_process(inst.account, inst.place_id, new_job)
        new_hwnd = windows.find_window_for_pid(new_pid, timeout=self.HOP_OVERLAP_TIMEOUT) if new_pid else None
        if old_pid:
            windows.kill_pid(old_pid)
        inst.pid = new_pid
        inst.hwnd = new_hwnd or (windows.find_window_for_pid(new_pid, timeout=5.0) if new_pid else None)
        inst.remember_job(new_job)
        return new_job

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
