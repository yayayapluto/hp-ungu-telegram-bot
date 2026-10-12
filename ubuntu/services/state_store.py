"""Small persistent key/value store plus volatile per-admin caches."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ubuntu.utils.logging_config import get_logger

log = get_logger(__name__)

PENDING_TTL_SECONDS = 300
LISTING_TTL_SECONDS = 900


class StateStore:
    """Persists per-admin preferences and caches transient UI state."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._pending: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self._listings: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------ disk
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, ValueError) as exc:
            log.warning("Could not read state file %s: %s", self._path, exc)

    def _flush(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("Could not persist state file %s: %s", self._path, exc)

    # ------------------------------------------------------------- key/value
    def _bucket(self, admin_id: int) -> Dict[str, Any]:
        return self._data.setdefault(str(admin_id), {})

    def get(self, admin_id: int, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._bucket(admin_id).get(key, default)

    def set(self, admin_id: int, key: str, value: Any) -> None:
        with self._lock:
            self._bucket(admin_id)[key] = value
            self._flush()

    def delete(self, admin_id: int, key: str) -> None:
        with self._lock:
            self._bucket(admin_id).pop(key, None)
            self._flush()

    def toggle(self, admin_id: int, key: str, default: bool = False) -> bool:
        with self._lock:
            current = bool(self._bucket(admin_id).get(key, default))
            self._bucket(admin_id)[key] = not current
            self._flush()
            return not current

    # ------------------------------------------------------------ confirmation
    def add_pending(self, admin_id: int, payload: Dict[str, Any]) -> str:
        token = secrets.token_urlsafe(9)
        with self._lock:
            bucket = self._pending.setdefault(admin_id, {})
            bucket[token] = {"payload": payload, "created_at": time.time()}
            self._expire_pending(admin_id)
        return token

    def pop_pending(self, admin_id: int, token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._expire_pending(admin_id)
            bucket = self._pending.get(admin_id, {})
            entry = bucket.pop(token, None)
            return entry["payload"] if entry else None

    def _expire_pending(self, admin_id: int) -> None:
        bucket = self._pending.get(admin_id)
        if not bucket:
            return
        cutoff = time.time() - PENDING_TTL_SECONDS
        for token in [t for t, e in bucket.items() if e["created_at"] < cutoff]:
            bucket.pop(token, None)

    # --------------------------------------------------------------- listings
    def set_listing(self, admin_id: int, path: str, entries: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._listings[admin_id] = {
                "path": path,
                "entries": entries,
                "created_at": time.time(),
            }

    def get_listing(self, admin_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._listings.get(admin_id)
            if not entry:
                return None
            if time.time() - entry["created_at"] > LISTING_TTL_SECONDS:
                self._listings.pop(admin_id, None)
                return None
            return entry
