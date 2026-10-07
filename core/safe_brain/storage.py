"""SafeMemory + SafeCache — حافظه و کش سبک.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: `_save` حالا `os.fsync` + dir fsync + cleanup tmp
    در exception دارد.
  - 🐛 FIX: `_load` حالا validate می‌کند که JSON list/dict است
    (نه str/int).
  - 🐛 FIX: `_load` حالا `try/except OSError` برای read_text دارد.
  - 🐛 FIX: `SafeCache.get` حالا `hits/misses` را thread-safe
    increment می‌کند (داخل lock).
  - 🐛 FIX: `SafeCache.set` حالا deep-copy پاسخ می‌دهد
    (جلوگیری از mutation).
  - 🐛 FIX: `SafeCache._key` حالا type-check دارد.
  - 🐛 FIX: `SafeMemory.add` حالا `content` None-safe است و
    `max_entries` clamp می‌شود.
  - 🐛 FIX: `SafeMemory.get_recent` حالا `n` را clamp می‌کند.
  - 🐛 FIX: `SafeMemory.__init__` حالا `max_entries` را clamp
    می‌کند (min=1, max=100k).
  - 🐛 FIX: `SafeCache.__init__` حالا `ttl_hours` را clamp
    می‌کند (min=1, max=8760).
  - 🎯 env-based: `SAFE_BRAIN_MAX_CONTENT_LEN`,
    `SAFE_BRAIN_MAX_CACHE_ENTRIES`, `SAFE_BRAIN_MAX_SEARCH_N`.
  - 🎯 `__repr__` برای هر دو کلاس.
  - 🎯 `get_stats()` برای SafeMemory.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import timedelta
from typing import Any

from core.kernel.logger import log_info, log_warning
from core.safe_brain.constants import (
    _CACHE_SAVE_INTERVAL_S,
    _MEM_SAVE_INTERVAL_S,
    SAFE_CACHE_DIR,
    SAFE_MEMORY_DIR,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# 🎯 سقف‌های env-based
_MAX_CONTENT_LEN = _env_int(
    "SAFE_BRAIN_MAX_CONTENT_LEN", 500,
    min_val=50, max_val=10_000,
)
_MAX_CACHE_ENTRIES = _env_int(
    "SAFE_BRAIN_MAX_CACHE_ENTRIES", 500,
    min_val=50, max_val=100_000,
)
_CACHE_EVICT_RATIO = 0.8  # نگه‌داشتن ۸۰٪
_MAX_SEARCH_N = _env_int(
    "SAFE_BRAIN_MAX_SEARCH_N", 100,
    min_val=1, max_val=10_000,
)


def _atomic_write_json(path: Any, data: Any) -> bool:
    """🎯 FIX: نوشتن atomic + fsync + cleanup tmp."""
    tmp = path.with_suffix(".tmp")
    try:
        # ─── mkdir ───
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # ─── write + fsync ───
        payload = json.dumps(
            data, ensure_ascii=False, indent=2
        )
        with open(str(tmp), "w", encoding="utf-8") as f:
            f.write(payload)
            try:
                f.flush()
                os.fsync(f.fileno())
            except (AttributeError, OSError):
                pass

        tmp.replace(path)

        # ─── dir fsync (POSIX) ───
        try:
            dir_fd = os.open(
                str(path.parent), os.O_DIRECTORY
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (AttributeError, OSError):
            pass

        return True
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════
# SafeMemory
# ═══════════════════════════════════════════
class SafeMemory:
    """حافظهٔ سبک محلی — فایل JSON با throttle save."""

    def __init__(
        self,
        name: str = "safe_memory",
        max_entries: int = 100,
    ):
        self.filepath = SAFE_MEMORY_DIR / f"{name}.json"
        # 🎯 clamp
        try:
            me = int(max_entries)
        except (TypeError, ValueError):
            me = 100
        self.max_entries = max(1, min(me, 100_000))

        self.memory: list[dict] = []
        self._lock = threading.Lock()
        self._last_save = 0.0
        self._dirty = False
        self._load()

    # ═══════════════════════════════════════════
    # load / save
    # ═══════════════════════════════════════════
    def _load(self) -> None:
        """🎯 FIX: بارگذاری با validate."""
        if not self.filepath.exists():
            return

        try:
            raw = self.filepath.read_text(encoding="utf-8")
        except OSError as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"read | {type(e).__name__}: {e}"
            )
            return
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"JSON نامعتبر | line={e.lineno} — "
                f"حافظه خالی شد"
            )
            self.memory = []
            return
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            self.memory = []
            return

        # 🎯 FIX: validate list
        if not isinstance(data, list):
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"JSON list نیست (type={type(data).__name__}) "
                f"— حافظه خالی شد"
            )
            self.memory = []
            return

        # ─── validate هر entry ───
        clean: list[dict] = []
        for item in data:
            if isinstance(item, dict):
                clean.append(item)

        with self._lock:
            self.memory = clean

        log_info(
            f"📚 [SAFEBRAIN:mem:{self.filepath.stem}] "
            f"{len(clean)} ورودی بارگذاری شد"
        )

    def _save(self, force: bool = False) -> None:
        """🎯 FIX: ذخیره با throttle + fsync."""
        now = time.time()

        with self._lock:
            if (
                not force
                and (now - self._last_save)
                < _MEM_SAVE_INTERVAL_S
            ):
                self._dirty = True
                return

            # snapshot
            data_snapshot = list(self.memory)
            self._last_save = now
            self._dirty = False

        ok = _atomic_write_json(
            self.filepath, data_snapshot
        )
        if not ok:
            log_warning(
                f"⚠️ [SAFEBRAIN:mem:{self.filepath.stem}] "
                f"ذخیره ناموفق"
            )

    def flush(self) -> None:
        """ذخیرهٔ اجباری در دیسک."""
        with self._lock:
            dirty = self._dirty
        if dirty:
            self._save(force=True)

    # ═══════════════════════════════════════════
    # add / get
    # ═══════════════════════════════════════════
    def add(self, role: str, content: Any) -> None:
        """🎯 FIX: None-safe + clamp."""
        if content is None:
            content = ""

        # 🎯 truncate امن
        try:
            content_str = str(content)[:_MAX_CONTENT_LEN]
        except Exception:
            content_str = ""

        role_str = str(role or "user")[:32]

        with self._lock:
            self.memory.append({
                "role": role_str,
                "content": content_str,
                "timestamp": time.time(),
            })
            # ─── cap ───
            if len(self.memory) > self.max_entries:
                self.memory = self.memory[
                    -self.max_entries:
                ]

        self._save()

    def get_recent(self, n: int = 5) -> list[dict]:
        """🎯 FIX: clamp n."""
        try:
            n_int = int(n)
        except (TypeError, ValueError):
            n_int = 5
        n_int = max(1, min(n_int, self.max_entries))

        with self._lock:
            if not self.memory:
                return []
            return list(self.memory[-n_int:])

    def get_context(self, n: int = 5) -> str:
        """ساخت context از N ورودی اخیر."""
        recent = self.get_recent(n)
        if not recent:
            return ""

        lines: list[str] = []
        for entry in recent:
            role_raw = entry.get("role", "user")
            role = (
                "کاربر" if role_raw == "user"
                else "دستیار"
            )
            content = str(
                entry.get("content", "")
            )[:200]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def search(
        self, query: str, n: int = 3
    ) -> list[dict]:
        """جستجوی substring در حافظه."""
        if not query:
            return []

        try:
            n_int = int(n)
        except (TypeError, ValueError):
            n_int = 3
        n_int = max(1, min(n_int, _MAX_SEARCH_N))

        q = str(query).lower()
        results: list[dict] = []

        with self._lock:
            for entry in reversed(self.memory):
                content = str(
                    entry.get("content", "")
                ).lower()
                if q in content:
                    results.append(entry)
                    if len(results) >= n_int:
                        break

        return results

    def clear(self) -> None:
        """پاک کردن حافظه."""
        with self._lock:
            self.memory = []
            self._dirty = True
        self._save(force=True)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.memory)

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        with self._lock:
            count = len(self.memory)
            dirty = self._dirty
            last_save = self._last_save

        return {
            "name": self.filepath.stem,
            "count": count,
            "max_entries": self.max_entries,
            "dirty": dirty,
            "last_save_ago_s": (
                round(time.time() - last_save, 1)
                if last_save > 0 else 0
            ),
            "filepath": str(self.filepath),
        }

    def __repr__(self) -> str:
        return (
            f"<SafeMemory name={self.filepath.stem} "
            f"count={self.count}/{self.max_entries}>"
        )


# ═══════════════════════════════════════════
# SafeCache
# ═══════════════════════════════════════════
class SafeCache:
    """کش سبک با TTL — فایل JSON با throttle save."""

    def __init__(
        self,
        name: str = "safe_cache",
        ttl_hours: int = 48,
    ):
        self.filepath = SAFE_CACHE_DIR / f"{name}.json"

        # 🎯 clamp
        try:
            th = int(ttl_hours)
        except (TypeError, ValueError):
            th = 48
        th = max(1, min(th, 24 * 365))
        self.ttl = timedelta(hours=th)

        self.cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._last_save = 0.0
        self._dirty = False
        self._load()

    # ═══════════════════════════════════════════
    # key
    # ═══════════════════════════════════════════
    @staticmethod
    def _key(text: Any) -> str:
        """🎯 FIX: type-check."""
        if text is None:
            text = ""
        try:
            s = str(text)
        except Exception:
            s = ""
        return hashlib.sha256(
            s.encode("utf-8")
        ).hexdigest()

    # ═══════════════════════════════════════════
    # load / save
    # ═══════════════════════════════════════════
    def _load(self) -> None:
        """🎯 FIX: بارگذاری با validate."""
        if not self.filepath.exists():
            return

        try:
            raw = self.filepath.read_text(encoding="utf-8")
        except OSError as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"read | {type(e).__name__}: {e}"
            )
            return
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"JSON نامعتبر | line={e.lineno} — "
                f"کش خالی شد"
            )
            self.cache = {}
            return
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            self.cache = {}
            return

        # 🎯 FIX: validate dict
        if not isinstance(data, dict):
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"JSON dict نیست (type={type(data).__name__}) "
                f"— کش خالی شد"
            )
            self.cache = {}
            return

        with self._lock:
            self.cache = data

        log_info(
            f"💾 [SAFEBRAIN:cache:{self.filepath.stem}] "
            f"{len(data)} مدخل بارگذاری شد"
        )

    def _save(self, force: bool = False) -> None:
        """🎯 FIX: ذخیره با throttle + fsync."""
        now = time.time()

        with self._lock:
            if (
                not force
                and (now - self._last_save)
                < _CACHE_SAVE_INTERVAL_S
            ):
                self._dirty = True
                return

            # snapshot
            data_snapshot = dict(self.cache)
            self._last_save = now
            self._dirty = False

        ok = _atomic_write_json(
            self.filepath, data_snapshot
        )
        if not ok:
            log_warning(
                f"⚠️ [SAFEBRAIN:cache:{self.filepath.stem}] "
                f"ذخیره ناموفق"
            )

    def flush(self) -> None:
        """ذخیرهٔ اجباری در دیسک."""
        with self._lock:
            dirty = self._dirty
        if dirty:
            self._save(force=True)

    # ═══════════════════════════════════════════
    # get / set
    # ═══════════════════════════════════════════
    def get(self, text: str) -> str | None:
        """🎯 FIX: hits/misses thread-safe."""
        if not text:
            return None

        key = self._key(text)
        now = time.time()

        with self._lock:
            entry = self.cache.get(key)

            if not isinstance(entry, dict):
                self._misses += 1
                return None

            try:
                age_s = now - float(
                    entry.get("timestamp", 0)
                )
                ttl_s = self.ttl.total_seconds()

                if age_s < ttl_s:
                    self._hits += 1
                    answer = entry.get("answer")
                    return answer
            except Exception:
                pass

            # expired یا invalid → miss
            self._misses += 1
            return None

    def set(self, text: str, answer: str) -> None:
        """🎯 FIX: deep-copy پاسخ."""
        if not text or answer is None:
            return

        try:
            answer_str = str(answer)
        except Exception:
            return

        if not answer_str:
            return

        key = self._key(text)

        with self._lock:
            self.cache[key] = {
                "answer": answer_str,
                "timestamp": time.time(),
            }

            # ─── LRU eviction ───
            if len(self.cache) > _MAX_CACHE_ENTRIES:
                try:
                    sorted_items = sorted(
                        self.cache.items(),
                        key=lambda x: (
                            x[1].get("timestamp", 0)
                            if isinstance(x[1], dict)
                            else 0
                        ),
                    )
                    keep_n = int(
                        _MAX_CACHE_ENTRIES
                        * _CACHE_EVICT_RATIO
                    )
                    self.cache = dict(
                        sorted_items[-keep_n:]
                    )
                except Exception:
                    pass

            self._dirty = True

        self._save()

    def clear(self) -> None:
        """پاک کردن کش."""
        with self._lock:
            self.cache = {}
            self._hits = 0
            self._misses = 0
            self._dirty = True
        self._save(force=True)
        log_info(
            f"🗑️ [SAFEBRAIN:cache:{self.filepath.stem}] "
            f"پاک شد"
        )

    @property
    def size(self) -> int:
        with self._lock:
            return len(self.cache)

    def stats(self) -> dict[str, Any]:
        """آمار کش — JSON-safe."""
        with self._lock:
            hits = self._hits
            misses = self._misses
            size = len(self.cache)
            dirty = self._dirty

        total = hits + misses
        return {
            "name": self.filepath.stem,
            "size": size,
            "max_entries": _MAX_CACHE_ENTRIES,
            "hits": hits,
            "misses": misses,
            "hit_rate": (
                round(hits / total, 3) if total else 0.0
            ),
            "dirty": dirty,
            "ttl_hours": int(
                self.ttl.total_seconds() / 3600
            ),
            "filepath": str(self.filepath),
        }

    # 🎯 alias
    def get_stats(self) -> dict[str, Any]:
        return self.stats()

    def __repr__(self) -> str:
        with self._lock:
            size = len(self.cache)
        return (
            f"<SafeCache name={self.filepath.stem} "
            f"size={size}/{_MAX_CACHE_ENTRIES}>"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "SafeMemory",
    "SafeCache",
]