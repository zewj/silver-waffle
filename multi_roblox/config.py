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
    launch_cooldown: float = 2.5

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
        return Config(
            presets=[Preset(**p) for p in data.get("presets", [])],
            recent_places=data.get("recent_places", []),
            launch_cooldown=float(data.get("launch_cooldown", 2.5)),
        )

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "presets": [asdict(p) for p in self.cfg.presets],
                "recent_places": self.cfg.recent_places,
                "launch_cooldown": self.cfg.launch_cooldown,
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
