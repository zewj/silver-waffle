"""Persistent encrypted store of Roblox accounts (cookie + display name)."""
import base64
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from . import auth, dpapi


def store_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "MultiRobloxManager"
    p.mkdir(parents=True, exist_ok=True)
    return p / "accounts.json"


@dataclass
class Account:
    user_id: int
    username: str
    display_name: str
    nickname: str = ""          # user-assigned label
    cookie_blob: str = ""       # DPAPI ciphertext, base64

    def label(self) -> str:
        return self.nickname or self.display_name or self.username

    def cookie(self) -> str:
        if not self.cookie_blob:
            raise ValueError(f"No cookie stored for {self.label()}")
        return dpapi.unprotect(base64.b64decode(self.cookie_blob)).decode("utf-8")


class AccountStore:
    """Thread-safe JSON-backed account list."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or store_path()
        self._lock = threading.Lock()
        self.accounts: list[Account] = []
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.accounts = [Account(**row) for row in raw.get("accounts", [])]

    def _save(self):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"accounts": [asdict(a) for a in self.accounts]}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def add_or_update(self, cookie: str, nickname: str = "") -> Account:
        """Validate the cookie, then persist (replacing any existing entry)."""
        info = auth.whoami(cookie)
        encrypted = base64.b64encode(dpapi.protect(cookie.encode("utf-8"))).decode("ascii")
        acc = Account(
            user_id=info["id"],
            username=info.get("name", ""),
            display_name=info.get("displayName", info.get("name", "")),
            nickname=nickname,
            cookie_blob=encrypted,
        )
        with self._lock:
            self.accounts = [a for a in self.accounts if a.user_id != acc.user_id]
            self.accounts.append(acc)
            self._save()
        return acc

    def remove(self, user_id: int):
        with self._lock:
            self.accounts = [a for a in self.accounts if a.user_id != user_id]
            self._save()

    def find(self, user_id: int) -> Optional[Account]:
        return next((a for a in self.accounts if a.user_id == user_id), None)

    def __iter__(self):
        return iter(list(self.accounts))
