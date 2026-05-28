"""Persistent app config: saved instance presets and recent place IDs."""
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "MultiRobloxManager"
    p.mkdir(parents=True, exist_ok=True)
    return p / "config.json"


@dataclass
class Preset:
    label: str
    place_id: int
    account_user_id: Optional[int] = None  # None == launcher-signed-in fallback


@dataclass
class Config:
    presets: list[Preset] = field(default_factory=list)
    recent_places: list[int] = field(default_factory=list)
    launch_cooldown: float = 6.0  # seconds; conservative to avoid auth.roblox.com 429
    # "protocol": go through RobloxPlayerLauncher.exe (stable, default).
    # "direct":   spawn RobloxPlayerBeta.exe directly (faster, skips launcher).
    launch_mode: str = "protocol"
    # Per-account LOCALAPPDATA isolation. When True (default), each launched
    # client writes cookies / cache / logs into
    # %APPDATA%\MultiRobloxManager\data\<user_id>\Roblox\, so alts don't see
    # each other's state — reduces fingerprint linkage. Side effect: launches
    # from outside the manager (e.g. clicking Play in your browser) can
    # behave oddly because the system's "current account" state is now
    # split across directories. Turn this off if you want browser launches
    # to work the same as before any manager run.
    account_isolation: bool = True
    # UI theme: "dark" or "light".
    theme: str = "dark"
    # Discord webhook URL fired when an instance crashes. Empty = disabled.
    webhook_url: str = ""
    # Master switch; even with a URL set, leaving this off mutes notifications.
    webhook_on_crash: bool = True
    # Discord bot for remote control (!screenshot, !instances, etc).
    bot_token: str = ""
    bot_enabled: bool = False
    # Discord user IDs allowed to invoke bot commands. Stored as strings
    # because Discord snowflake IDs are 18-19 digits and JSON's number
    # type is float64 — safe as strings, lossy as numbers.
    bot_user_ids: list[str] = field(default_factory=list)

    def _key(self, p: Preset):
        return (p.label, p.place_id, p.account_user_id)

    def upsert_preset(self, preset: Preset):
        self.presets = [p for p in self.presets if self._key(p) != self._key(preset)]
        self.presets.append(preset)

    def remove_preset(self, index: int):
        if 0 <= index < len(self.presets):
            del self.presets[index]

    def remember_place(self, place_id: int, cap: int = 10):
        if place_id in self.recent_places:
            self.recent_places.remove(place_id)
        self.recent_places.insert(0, place_id)
        del self.recent_places[cap:]


class ConfigStore:
    """Thread-safe wrapper that writes to disk on every mutation."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or config_path()
        self._lock = threading.Lock()
        self.cfg = self._load()

    def _load(self) -> Config:
        if not self.path.exists():
            return Config()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Config()
        mode = data.get("launch_mode", "protocol")
        if mode not in ("protocol", "direct"):
            mode = "protocol"
        theme = data.get("theme", "dark")
        if theme not in ("dark", "light"):
            theme = "dark"
        return Config(
            presets=[Preset(**p) for p in data.get("presets", [])],
            recent_places=data.get("recent_places", []),
            launch_cooldown=float(data.get("launch_cooldown", 6.0)),
            launch_mode=mode,
            account_isolation=bool(data.get("account_isolation", True)),
            theme=theme,
            webhook_url=str(data.get("webhook_url", "")),
            webhook_on_crash=bool(data.get("webhook_on_crash", True)),
            bot_token=str(data.get("bot_token", "")),
            bot_enabled=bool(data.get("bot_enabled", False)),
            bot_user_ids=[str(x) for x in data.get("bot_user_ids", [])],
        )

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "presets": [asdict(p) for p in self.cfg.presets],
                "recent_places": self.cfg.recent_places,
                "launch_cooldown": self.cfg.launch_cooldown,
                "launch_mode": self.cfg.launch_mode,
                "account_isolation": self.cfg.account_isolation,
                "theme": self.cfg.theme,
                "webhook_url": self.cfg.webhook_url,
                "webhook_on_crash": self.cfg.webhook_on_crash,
                "bot_token": self.cfg.bot_token,
                "bot_enabled": self.cfg.bot_enabled,
                "bot_user_ids": self.cfg.bot_user_ids,
            }, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def upsert(self, preset: Preset):
        self.cfg.upsert_preset(preset)
        self.save()

    def remove(self, index: int):
        self.cfg.remove_preset(index)
        self.save()

    def remember(self, place_id: int):
        self.cfg.remember_place(place_id)
        self.save()
