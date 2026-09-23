"""SafeMemory + SafeCache — حافظه و کش سبک."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from core.kernel.logger import log_info, log_warning
from core.safe_brain.constants import (
    _CACHE_SAVE_INTERVAL_S,
    _MEM_SAVE_INTERVAL_S,
    SAFE_CACHE_DIR,
    SAFE_MEMORY_DIR,
)


class SafeMemory:
    """حافظهٔ سبک محلی — فایل JSON با throttle save."""

    def __init__(
        self, name: str = "safe_memory", max_entries: int = 100
    ):
        self.filepath = SAFE_MEMORY_DIR / f"{name}.json"
        self.max_entries = max_entries
        self.memory: list[dict] = []
        self._lock = threading.Lock()
        self._last_save = 0.0
        self._dirty = False
        self._load()

    def _load(self):
        if not self.filepath.exists():
            return
        try:
            with self._lock:
                self.memory = json.loads(
                    self.filepath.read_text(encoding="utf-8")
                )
            log_info(
                f"📚 [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"{len(self.memory)} ورودی بارگذاری شد"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"JSON نامعتبر | line={e.lineno} — حافظه خالی شد"
            )
            self.memory = []
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            self.memory = []

    def _save(self, force: bool = False):
        now = time.time()
        with self._lock:
            if (
                not force
                and (now - self._last_save) < _MEM_SAVE_INTERVAL_S
            ):
                self._dirty = True
                return
            data = json.dumps(
                self.memory, ensure_ascii=False, indent=2
            )
            self._last_save = now
            self._dirty = False

        try:
            tmp = self.filepath.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.filepath)
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"ذخیره | {type(e).__name__}: {e}"
            )

    def flush(self):
        with self._lock:
            dirty = self._dirty
        if dirty:
            self._save(force=True)

    def add(self, role: str, content: str):
        with self._lock:
            self.memory.append({
                "role": role,
                "content": str(content)[:500],
                "timestamp": time.time(),
            })
            if len(self.memory) > self.max_entries:
                self.memory = self.memory[-self.max_entries:]
        self._save()

    def get_recent(self, n: int = 5) -> list[dict]:
        with self._lock:
            return self.memory[-n:] if self.memory else []

    def get_context(self, n: int = 5) -> str:
        recent = self.get_recent(n)
        if not recent:
            return ""
        lines = []
        for entry in recent:
            role = (
                "کاربر"
                if entry.get("role") == "user"
                else "دستیار"
            )
            lines.append(
                f"{role}: {entry.get('content', '')[:200]}"
            )
        return "\n".join(lines)

    def search(self, query: str, n: int = 3) -> list[dict]:
        if not query:
            return []
        results: list[dict] = []
        q = query.lower()
        with self._lock:
            for entry in reversed(self.memory):
                if q in (entry.get("content") or "").lower():
                    results.append(entry)
                    if len(results) >= n:
                        break
        return results

    def clear(self):
        with self._lock:
            self.memory = []
            self._dirty = True
        self._save(force=True)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.memory)


class SafeCache:
    """کش سبک با TTL — فایل JSON با throttle save."""

    def __init__(
        self, name: str = "safe_cache", ttl_hours: int = 48
    ):
        self.filepath = SAFE_CACHE_DIR / f"{name}.json"
        self.ttl = timedelta(hours=ttl_hours)
        self.cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._last_save = 0.0
        self._dirty = False
        self._load()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _load(self):
        if not self.filepath.exists():
            return
        try:
            with self._lock:
                self.cache = json.loads(
                    self.filepath.read_text(encoding="utf-8")
                )
            log_info(
                f"💾 [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"{len(self.cache)} مدخل بارگذاری شد"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"JSON نامعتبر | line={e.lineno} — کش خالی شد"
            )
            self.cache = {}
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            self.cache = {}

    def _save(self, force: bool = False):
        now = time.time()
        with self._lock:
            if (
                not force
                and (now - self._last_save) < _CACHE_SAVE_INTERVAL_S
            ):
                self._dirty = True
                return
            data = json.dumps(self.cache, ensure_ascii=False)
            self._last_save = now
            self._dirty = False

        try:
            tmp = self.filepath.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.filepath)
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"ذخیره | {type(e).__name__}: {e}"
            )

    def flush(self):
        with self._lock:
            dirty = self._dirty
        if dirty:
            self._save(force=True)

    def get(self, text: str) -> str | None:
        if not text:
            return None
        key = self._key(text)
        with self._lock:
            entry = self.cache.get(key)
        if entry:
            try:
                age = datetime.now() - datetime.fromtimestamp(
                    entry.get("timestamp", 0)
                )
                if age < self.ttl:
                    self._hits += 1
                    return entry.get("answer")
            except Exception:
                pass
        self._misses += 1
        return None

    def set(self, text: str, answer: str):
        if not text or not answer:
            return
        key = self._key(text)
        with self._lock:
            self.cache[key] = {
                "answer": answer,
                "timestamp": time.time(),
            }
            if len(self.cache) > 500:
                sorted_items = sorted(
                    self.cache.items(),
                    key=lambda x: x[1].get("timestamp", 0),
                )
                self.cache = dict(sorted_items[-400:])
            self._dirty = True
        self._save()

    def clear(self):
        with self._lock:
            self.cache = {}
            self._hits = 0
            self._misses = 0
            self._dirty = True
        self._save(force=True)
        log_info(
            f"🗑️ [SAFEBRAIN:cache:{self.filepath.stem}] پاک شد"
        )

    @property
    def size(self) -> int:
        with self._lock:
            return len(self.cache)

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": self.size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (
                round(self._hits / total, 3) if total else 0.0
            ),
        }
