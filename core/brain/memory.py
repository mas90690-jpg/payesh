"""حافظه و کش Brain — SmartCache, APIMemory, BrainMemory,
PersistentMemory, Semantic.

نسخهٔ ۴ (این نسخه):
  - 🎯 NEW: `PersistentMemory` — حافظهٔ پایدار SQLite + FTS5:
      * `remember(category, key, value)` — ذخیره
      * `recall(query, category, limit)` — جستجو با FTS
      * `get_context(query, n)` — ساخت context برای AI
      * `count(category)` — تعداد
      * `forget(days, min_importance)` — فراموشی انتخابی
      * `get_stats()` — آمار کامل
      * `close()` — بستن تمیز
      * auto-cleanup وقتی از سقف گذشت
  - 🎯 NEW: `get_persistent_memory()` — singleton
  - 🎯 NEW: `reset_persistent_memory()` — برای تست
  - 🎯 همهٔ مقادیر از `constants.py` (env-based)
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.brain.constants import (
    CACHE_DIR,
    CACHE_SAVE_INTERVAL_S,
    CACHE_TTL_DAYS,
    COMPRESS_THRESHOLD,
    CONTEXT_API_N,
    CONTEXT_CHROMA_N,
    CONTEXT_RECENT,
    LOG_PREVIEW_LEN,
    MAX_CONTEXT_TOKENS,
    MAX_FILE_SIZE_BYTES,
    MAX_PENDING_TASKS,
    MAX_SHORT_TERM,
    MEMORY_DIR,
    PERSISTENT_DB_PATH,
    PERSISTENT_MAX_ROWS,
    PERSISTENT_MEMORY_ENABLED,
    PROJECT_BASE,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)

# ═══════════════════════════════════════════════════════════
# 🆕 Alias — brain_memory به‌عنوان منبع اصلی
# ═══════════════════════════════════════════════════════════
from core.brain.brain_memory import (
    BrainMemory as _BrainMemoryNew,
    PersistentMemory as _PersistentMemoryNew,
    get_persistent_memory as _get_persistent_new,
    reset_persistent_memory as _reset_persistent_new,
)

# Alias عمومی
BrainMemory = _BrainMemoryNew
PersistentMemory = _PersistentMemoryNew
get_persistent_memory = _get_persistent_new
reset_persistent_memory = _reset_persistent_new

# ═══════════════════════════════════════════════════════════
# 🆕 Alias — brain_memory به‌عنوان منبع اصلی
# ═══════════════════════════════════════════════════════════
from core.brain.brain_memory import (
    BrainMemory as _BrainMemoryNew,
    PersistentMemory as _PersistentMemoryNew,
    get_persistent_memory as _get_persistent_new,
    reset_persistent_memory as _reset_persistent_new,
)

# Alias عمومی — هر کسی `from core.brain.memory import BrainMemory` بزنه،
# همون brain_memory.BrainMemory رو می‌گیره
BrainMemory = _BrainMemoryNew
PersistentMemory = _PersistentMemoryNew
get_persistent_memory = _get_persistent_new
reset_persistent_memory = _reset_persistent_new

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


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# 🎯 سقف‌های env-based
_MAX_PENDING_TASKS = MAX_PENDING_TASKS
_MAX_SHORT_TERM = MAX_SHORT_TERM
_COMPRESS_THRESHOLD = COMPRESS_THRESHOLD
_CONTEXT_RECENT = CONTEXT_RECENT
_CONTEXT_API_N = CONTEXT_API_N
_CONTEXT_CHROMA_N = CONTEXT_CHROMA_N

# 🎯 Persistent Memory thresholds
_PERSISTENT_MEMORY_ENABLED = PERSISTENT_MEMORY_ENABLED
_PERSISTENT_DB_PATH = PERSISTENT_DB_PATH
_PERSISTENT_MAX_ROWS = PERSISTENT_MAX_ROWS


# ═══════════════════════════════════════════
# SmartCache
# ═══════════════════════════════════════════
class SmartCache:
    """کش با TTL، throttle save، و atomic write.

    thread-safe: با `threading.Lock` محافظت می‌شود.
    """

    def __init__(
        self,
        name: str,
        ttl_days: int = CACHE_TTL_DAYS,
        max_size: int = 5000,
    ):
        self.filepath = CACHE_DIR / f"{name}.json"
        self.ttl = timedelta(days=ttl_days)
        self.max_size = max_size
        self.cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._last_save = 0.0
        self._dirty = False
        self.load()

    def _key(self, text: str, model: str = "") -> str:
        return hashlib.sha256(
            f"{text}|{model}".encode()
        ).hexdigest()

    def get(self, text: str, model: str = "") -> str | None:
        """خواندن از کش — با چک TTL."""
        key = self._key(text, model)
        with self._lock:
            entry = self.cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            try:
                age = (
                    datetime.now()
                    - datetime.fromtimestamp(entry["timestamp"])
                )
                if age >= self.ttl:
                    # expired — حذف
                    del self.cache[key]
                    self._misses += 1
                    return None
                self._hits += 1
                return entry["answer"]
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                    f"get entry | {type(e).__name__}: {e}"
                )
                self._misses += 1
                return None

    def set(
        self, text: str, answer: str, model: str = ""
    ) -> None:
        """ذخیره در کش — با LRU eviction."""
        if not text or not answer:
            return
        key = self._key(text, model)
        with self._lock:
            self.cache[key] = {
                "answer": answer,
                "timestamp": time.time(),
            }
            if len(self.cache) > self.max_size:
                # LRU eviction: نگه‌داشتن 80٪ جدیدترین
                try:
                    sorted_items = sorted(
                        self.cache.items(),
                        key=lambda x: x[1].get("timestamp", 0),
                    )
                    keep_n = int(self.max_size * 0.8)
                    self.cache = dict(sorted_items[-keep_n:])
                except Exception as e:
                    log_warning(
                        f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                        f"eviction | {type(e).__name__}: {e}"
                    )
            self._dirty = True

        # throttle save
        now = time.time()
        if now - self._last_save >= CACHE_SAVE_INTERVAL_S:
            self.save()

    def flush(self) -> None:
        """ذخیره اجباری در دیسک."""
        with self._lock:
            dirty = self._dirty
        if dirty:
            self.save()

    def load(self) -> None:
        """بارگذاری از دیسک."""
        if not self.filepath.exists():
            return
        try:
            with self._lock:
                data = json.loads(
                    self.filepath.read_text(encoding="utf-8")
                )
                self.cache = data if isinstance(data, dict) else {}
            log_info(
                f"💾 [BRAIN:cache:{self.filepath.stem}] "
                f"{len(self.cache)} مدخل بارگذاری شد"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                f"JSON نامعتبر | line={e.lineno} col={e.colno} | "
                f"{e.msg} — کش خالی شد"
            )
            self.cache = {}
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                f"بارگذاری | {type(e).__name__}: {e}"
            )
            self.cache = {}

    def save(self) -> None:
        """ذخیره atomic + fsync + mkdir."""
        try:
            with self._lock:
                data = json.dumps(
                    self.cache, ensure_ascii=False, indent=2
                )
                self._last_save = time.time()
                self._dirty = False

            # mkdir قبل از write
            self.filepath.parent.mkdir(parents=True, exist_ok=True)

            tmp = self.filepath.with_suffix(".tmp")
            with open(str(tmp), "w", encoding="utf-8") as f:
                f.write(data)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass
            tmp.replace(self.filepath)

            # fsync دایرکتوری (POSIX)
            try:
                dir_fd = os.open(
                    str(self.filepath.parent), os.O_DIRECTORY
                )
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                f"ذخیره ناموفق | {type(e).__name__}: {e}"
            )

    def clear(self) -> None:
        """پاک کردن کش."""
        with self._lock:
            self.cache = {}
            self._hits = 0
            self._misses = 0
            self._dirty = True
        self.save()
        log_info(
            f"🗑️ [BRAIN:cache:{self.filepath.stem}] پاک شد"
        )

    def stats(self) -> dict[str, Any]:
        """آمار کش — JSON-safe."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self.cache),
                "file": str(self.filepath),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (
                    round(self._hits / total, 3) if total else 0.0
                ),
                "dirty": self._dirty,
            }

    def get_stats(self) -> dict[str, Any]:
        return self.stats()


# ═══════════════════════════════════════════
# APIMemory
# ═══════════════════════════════════════════
class APIMemory:
    """حافظه از طریق API (جایگزین ChromaDB)."""

    def __init__(
        self, api_url: str, api_key: str = "", timeout: int = 30
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = None
        self._session_lock = asyncio.Lock()
        log_info(
            f"🧠 [BRAIN:apimem:init] url={self.api_url} | "
            f"key={'✅' if api_key else '❌'} | "
            f"timeout={timeout}s"
        )

    async def _ensure_session(self):
        """ساخت session با lock (بدون race)."""
        if self._session is not None:
            return
        async with self._session_lock:
            if self._session is not None:
                return
            try:
                import aiohttp
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = (
                        f"Bearer {self.api_key}"
                    )
                self._session = aiohttp.ClientSession(
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(
                        total=self.timeout
                    ),
                )
                log_info(
                    "🔗 [BRAIN:apimem:session] session ساخته شد"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:apimem:session] "
                    f"{type(e).__name__}: {e}"
                )
                self._session = None

    async def _post(self, endpoint: str, data: dict) -> dict:
        """POST امن — با چک session."""
        try:
            await self._ensure_session()
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:apimem:_post] ensure_session | "
                f"{type(e).__name__}: {e}"
            )
            return {"error": "session_unavailable"}

        if self._session is None:
            return {"error": "session_unavailable"}

        t0 = time.monotonic()
        try:
            async with self._session.post(
                f"{self.api_url}/{endpoint}", json=data
            ) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                if resp.status >= 400:
                    try:
                        body = (await resp.text())[
                            :LOG_PREVIEW_LEN
                        ]
                    except Exception:
                        body = ""
                    log_warning(
                        f"⚠️ [BRAIN:apimem:{endpoint}] "
                        f"HTTP {resp.status} | {elapsed_ms:.0f}ms | "
                        f"body={body!r}"
                    )
                    return {"error": f"HTTP {resp.status}"}
                return await resp.json()
        except TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"⏰ [BRAIN:apimem:{endpoint}] "
                f"Timeout {self.timeout}s | {elapsed_ms:.0f}ms"
            )
            return {"error": "timeout"}
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_warning(
                f"⚠️ [BRAIN:apimem:{endpoint}] "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )
            return {"error": str(e)}

    async def add(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        return await self._post(
            "add",
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
            },
        )

    async def search(
        self, query: str, n: int = 5
    ) -> list[dict]:
        result = await self._post(
            "search", {"query": query, "n": n}
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def get_context(
        self, query: str = "", n: int = 5
    ) -> list[dict]:
        result = await self._post(
            "context", {"query": query, "n": n}
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "context" in result:
            return result["context"]
        return []

    async def clear(self) -> dict:
        return await self._post("clear", {})

    async def close(self) -> None:
        """idempotent close."""
        session = self._session
        self._session = None
        if session is not None:
            try:
                await session.close()
                log_info(
                    "🔌 [BRAIN:apimem:close] session بسته شد"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:apimem:close] "
                    f"{type(e).__name__}: {e}"
                )


# ═══════════════════════════════════════════
# 🆕 PersistentMemory — حافظهٔ پایدار SQLite + FTS5
# ═══════════════════════════════════════════
class ProjectSemanticSearch:
    """جستجوی معنایی در کدبیس با ChromaDB."""

    def __init__(self):
        self.client = None
        self.collection = None
        self.embedding_fn = None
        self._init()

    def _init(self) -> None:
        """ساخت ChromaDB client با embedding — یا غیرفعال اگه ممکن نبود."""
        import os
        if os.getenv(
            "BRAIN_DISABLE_SEMANTIC", "true"
        ).lower() == "true":
            log_info(
                "ℹ️ [BRAIN:sem:init] semantic search غیرفعال "
                "(BRAIN_DISABLE_SEMANTIC=true)"
            )
            self.client = None
            self.collection = None
            self.embedding_fn = None
            return

        try:
            import chromadb
            from chromadb.utils import embedding_functions

            self.client = chromadb.PersistentClient(
                path=str(MEMORY_DIR / "codebase")
            )
            self.embedding_fn = (
                embedding_functions
                .SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
            )
            self.collection = (
                self.client.get_or_create_collection(
                    name="code_embeddings",
                    embedding_function=self.embedding_fn,
                )
            )
            log_success(
                "✅ [BRAIN:sem:init] Semantic Search فعال شد"
            )
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:sem:init] chromadb نصب نیست | {e}"
            )
            self.client = None
            self.collection = None
            self.embedding_fn = None
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:sem:init] غیرفعال | "
                f"{type(e).__name__}: {e}"
            )
            self.client = None
            self.collection = None
            self.embedding_fn = None

    def index_files(
        self, patterns: list[str] | None = None
    ) -> int:
        """ایندکس فایل‌های پروژه."""
        if not self.collection:
            return 0
        if patterns is None:
            patterns = [
                "**/*.py", "**/*.txt", "**/*.md",
                "**/*.json", "**/*.yaml", "**/*.yml",
            ]
        total = 0
        failed = 0
        t0 = time.monotonic()
        project = Path(PROJECT_BASE)
        for pattern in patterns:
            try:
                files = glob.glob(
                    pattern,
                    root_dir=str(project),
                    recursive=True,
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:sem] glob {pattern} | "
                    f"{type(e).__name__}: {e}"
                )
                continue

            for fpath in files:
                try:
                    full = project / fpath
                    if not full.is_file():
                        continue
                    if full.stat().st_size >= MAX_FILE_SIZE_BYTES:
                        continue
                    content = full.read_text(
                        encoding="utf-8", errors="ignore"
                    )
                    self.collection.add(
                        documents=[content],
                        metadatas=[{"path": str(fpath)}],
                        ids=[
                            f"file_{hashlib.sha256(fpath.encode()).hexdigest()}"
                        ],
                    )
                    total += 1
                except Exception as e:
                    failed += 1
                    if failed <= 3:
                        log_warning(
                            f"⚠️ [BRAIN:sem] index | "
                            f"{fpath} | {type(e).__name__}: {e}"
                        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [BRAIN:sem:index] {total} فایل ایندکس شد | "
            f"failed={failed} | {elapsed_ms:.0f}ms"
        )
        return total

    def search(
        self, query: str, n: int = 5
    ) -> list[dict]:
        """جستجو در کدبیس."""
        if not self.collection:
            return []
        try:
            results = self.collection.query(
                query_texts=[query], n_results=n
            )
            docs = results.get("documents") or [[]]
            metas = results.get("metadatas") or [[]]
            if not docs or not docs[0]:
                return []
            return [
                {
                    "path": (
                        m.get("path", "?")
                        if isinstance(m, dict) else "?"
                    ),
                    "content": str(d)[:500]
                    + ("..." if len(str(d)) > 500 else ""),
                }
                for d, m in zip(
                    docs[0], metas[0], strict=False
                )
            ]
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:sem:search] "
                f"{type(e).__name__}: {e}"
            )
            return []


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "SmartCache",
    "APIMemory",
    "BrainMemory",           # alias به brain_memory.BrainMemory
    "ProjectSemanticSearch",
    "PersistentMemory",      # حفظ برای سازگاری
    "get_persistent_memory",
    "reset_persistent_memory",
]