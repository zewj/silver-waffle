"""Tracks launched Roblox instances and orchestrates hops."""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import antiafk, auth, launcher, profiles, servers, stats, windows
from .accounts import Account
from .mutex import SingletonMutex

log = logging.getLogger(__name__)


@dataclass
class Instance:
    label: str
    place_id: int
    account: Optional[Account] = None
    pid: Optional[int] = None
    hwnd: Optional[int] = None
    job_id: Optional[str] = None
    recent_jobs: list = field(default_factory=list)
    status: str = "starting"     # starting | running | crashed | closed
    last_sample: stats.Sample = field(default_factory=lambda: stats.Sample(alive=False))
    antiafk_worker: Optional["antiafk.AntiAFK"] = None

    @property
    def antiafk_on(self) -> bool:
        return self.antiafk_worker is not None and self.antiafk_worker.running

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
        log.info("singleton mutex acquired; manager started")

    def shutdown(self):
        with self._lock:
            for inst in self.instances:
                if inst.antiafk_worker:
                    inst.antiafk_worker.stop()
                if inst.pid:
                    windows.kill_pid(inst.pid)
                    stats.forget(inst.pid)
            self.instances.clear()
        self._mutex.release()
        log.info("manager shut down; mutex released")

    def _respect_cooldown(self):
        wait = self.LAUNCH_COOLDOWN - (time.time() - self._last_launch)
        if wait > 0:
            log.debug("cooldown: sleeping %.2fs before launch", wait)
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

        When `account` is provided we also override LOCALAPPDATA for the
        spawned process so it writes cookies/cache/logs into a per-account
        directory — isolation without disturbing the binaries.
        """
        self._respect_cooldown()
        env = profiles.env_for_account(account.user_id) if account else None
        proxy = account.proxy_or_none() if account else None
        log.info("launch start place=%s job=%s account=%s mode=%s",
                 place_id, job_id, account.label() if account else "(none)",
                 self.launch_mode)

        if not account:
            before = windows.list_roblox_pids()
            uri = servers.join_uri(place_id, job_id) if job_id else servers.launch_uri(place_id)
            windows.launch_uri(uri)
            return self._wait_for_new_pid(before, self.PROTOCOL_PID_TIMEOUT)

        if self.launch_mode == "direct":
            before = windows.list_roblox_pids()
            ticket = auth.fetch_auth_ticket(account.cookie(), proxy=proxy)
            launcher.launch_with_ticket(ticket, place_id, job_id=job_id, env=env)
            return self._wait_for_new_pid(before, self.DIRECT_PID_TIMEOUT)

        # protocol-first with direct fallback
        before = windows.list_roblox_pids()
        ticket = auth.fetch_auth_ticket(account.cookie(), proxy=proxy)
        launcher.launch_protocol_with_ticket(ticket, place_id, job_id=job_id, env=env)
        pid = self._wait_for_new_pid(before, self.PROTOCOL_PID_TIMEOUT)
        if pid:
            return pid
        log.warning("protocol launch stalled for %s; falling back to direct exec",
                    account.label())
        before = windows.list_roblox_pids()
        ticket = auth.fetch_auth_ticket(account.cookie(), proxy=proxy)
        launcher.launch_with_ticket(ticket, place_id, job_id=job_id, env=env)
        return self._wait_for_new_pid(before, self.DIRECT_PID_TIMEOUT)

    def add_instance(self, label: str, place_id: int,
                     account: Optional[Account] = None,
                     job_id: Optional[str] = None) -> Instance:
        inst = Instance(label=label, place_id=place_id, account=account)
        with self._lock:
            self.instances.append(inst)
        try:
            pid = self._launch_process(account, place_id, job_id)
        except Exception:
            log.exception("launch failed for %s", label)
            inst.status = "crashed"
            raise
        inst.pid = pid
        inst.status = "running" if pid else "crashed"
        if pid:
            inst.hwnd = windows.find_window_for_pid(pid, timeout=30.0)
        if job_id:
            inst.remember_job(job_id)
        log.info("instance %s ready pid=%s hwnd=%s", label, pid, inst.hwnd)
        return inst

    def focus(self, inst: Instance) -> bool:
        if not inst.hwnd and inst.pid:
            inst.hwnd = windows.find_window_for_pid(inst.pid, timeout=2.0)
        return windows.focus_window(inst.hwnd) if inst.hwnd else False

    def close(self, inst: Instance):
        if inst.antiafk_worker:
            inst.antiafk_worker.stop()
            inst.antiafk_worker = None
        if inst.pid:
            windows.kill_pid(inst.pid)
            stats.forget(inst.pid)
        with self._lock:
            if inst in self.instances:
                self.instances.remove(inst)
        log.info("closed instance %s (pid=%s)", inst.label, inst.pid)

    def set_antiafk(self, inst: Instance, enabled: bool) -> bool:
        """Toggle a single instance's anti-AFK worker. Returns the new state."""
        if enabled:
            if inst.antiafk_worker is None:
                inst.antiafk_worker = antiafk.AntiAFK(
                    label=inst.label,
                    hwnd_lookup=lambda i=inst: i.hwnd,
                )
            inst.antiafk_worker.start()
        else:
            if inst.antiafk_worker:
                inst.antiafk_worker.stop()
        return inst.antiafk_on

    def set_antiafk_all(self, enabled: bool) -> int:
        """Toggle anti-AFK for every running instance. Returns the count affected."""
        affected = 0
        for inst in self.instances:
            if inst.status == "crashed":
                continue
            self.set_antiafk(inst, enabled)
            affected += 1
        log.info("anti-AFK %s for %d instance(s)", "enabled" if enabled else "disabled", affected)
        return affected

    def server_hop(self, inst: Instance) -> Optional[str]:
        # Route the server-list lookup through the account's proxy too, so
        # the IP discovering the candidate list matches the IP that'll
        # ultimately join. Otherwise a SOCKS5'd account would fetch from
        # the local egress and then connect from the proxy egress.
        proxy = inst.account.proxy_or_none() if inst.account else None
        srv = servers.pick_server(
            inst.place_id, exclude_job_ids=inst.recent_jobs, proxy=proxy,
        )
        if not srv:
            log.info("hop: no eligible server for place %s", inst.place_id)
            return None
        new_job = srv["id"]
        old_pid = inst.pid
        try:
            new_pid = self._launch_process(inst.account, inst.place_id, new_job)
        except Exception:
            log.exception("hop launch failed for %s", inst.label)
            return None
        new_hwnd = windows.find_window_for_pid(new_pid, timeout=self.HOP_OVERLAP_TIMEOUT) if new_pid else None
        if old_pid:
            windows.kill_pid(old_pid)
            stats.forget(old_pid)
        inst.pid = new_pid
        inst.hwnd = new_hwnd or (windows.find_window_for_pid(new_pid, timeout=5.0) if new_pid else None)
        inst.remember_job(new_job)
        inst.status = "running" if new_pid else "crashed"
        log.info("hop done %s -> %s (pid=%s)", inst.label, new_job, new_pid)
        return new_job

    def cycle_focus(self) -> Optional[Instance]:
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

    def refresh_stats(self) -> None:
        """Update CPU/RAM samples and mark dead PIDs as crashed."""
        for inst in list(self.instances):
            if not inst.pid:
                continue
            inst.last_sample = stats.sample(inst.pid)
            if not inst.last_sample.alive and inst.status == "running":
                log.warning("instance %s (pid=%s) is no longer alive", inst.label, inst.pid)
                inst.status = "crashed"
                stats.forget(inst.pid)
