"""حافظه و کش Brain — SmartCache, APIMemory, BrainMemory, Semantic.

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX مهم: `APIMemory._ensure_session` حالا با `asyncio.Lock`
    از race condition جلوگیری می‌کند (قبلاً دو task هم‌زمان می‌توانستند
    دو session بسازند).
  - 🐛 FIX مهم: `APIMemory._post` حالا اگر session نبود، error
    امن برمی‌گرداند (به‌جای AttributeError).
  - 🐛 FIX مهم: `APIMemory.close` حالا idempotent است (دوبار صدا زدن
    مشکلی ندارد).
  - 🐛 FIX: `SmartCache.save` حالا قبل از write، `mkdir` می‌زند.
  - 🐛 FIX: `SmartCache.save` حالا `os.fsync` می‌زند (durability).
  - 🐛 FIX: `_on_task_done` حالا exception اصلی را دقیق‌تر می‌گیرد
    (نه `except (asyncio.CancelledError, Exception)` که مبهم بود).
  - 🐛 FIX: `BrainMemory.close` حالا منتظر pending taskها می‌ماند
    (به‌جای رها کردن آن‌ها).
  - 🎯 `_MAX_PENDING_TASKS` — سقف برای `_pending_tasks` (جلوگیری
    از رشد نامحدود).
  - 🎯 `BrainMemory.get_stats()` متد جدید برای debug.
  - 🎯 `SmartCache.get_stats()` alias برای `stats()` (یکدست‌سازی).
  - 🎯 لاگ‌های دقیق‌تر با prefixهای `[BRAIN:cache:*]`,
    `[BRAIN:apimem:*]`, `[BRAIN:mem:*]`, `[BRAIN:sem:*]`.
  - 🎯 `_env_int` helper برای thresholds.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.brain.constants import (
    CACHE_DIR,
    CACHE_SAVE_INTERVAL_S,
    CACHE_TTL_DAYS,
    LOG_PREVIEW_LEN,
    MAX_CONTEXT_TOKENS,
    MAX_FILE_SIZE_BYTES,
    MEMORY_DIR,
    PROJECT_BASE,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
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


# 🎯 سقف برای taskهای pending
_MAX_PENDING_TASKS = _env_int(
    "BRAIN_MAX_PENDING_TASKS", 100,
    min_val=10, max_val=10_000,
)

# 🎯 سقف برای context history
_MAX_SHORT_TERM = _env_int(
    "BRAIN_MAX_SHORT_TERM", 20,
    min_val=5, max_val=500,
)
_COMPRESS_THRESHOLD = _env_int(
    "BRAIN_COMPRESS_THRESHOLD", 15,
    min_val=3, max_val=200,
)
_CONTEXT_RECENT = _env_int(
    "BRAIN_CONTEXT_RECENT", 5,
    min_val=1, max_val=50,
)
_CONTEXT_API_N = _env_int(
    "BRAIN_CONTEXT_API_N", 3,
    min_val=1, max_val=20,
)
_CONTEXT_CHROMA_N = _env_int(
    "BRAIN_CONTEXT_CHROMA_N", 3,
    min_val=1, max_val=20,
)


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
        """🎯 FIX: ذخیره atomic + fsync + mkdir."""
        try:
            with self._lock:
                data = json.dumps(
                    self.cache, ensure_ascii=False, indent=2
                )
                self._last_save = time.time()
                self._dirty = False

            # 🎯 FIX: mkdir قبل از write
            self.filepath.parent.mkdir(parents=True, exist_ok=True)

            tmp = self.filepath.with_suffix(".tmp")
            with open(str(tmp), "w", encoding="utf-8") as f:
                f.write(data)
                # 🎯 FIX: fsync
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

    # 🎯 alias برای یکدست‌سازی
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
        # 🐛 FIX: lock برای جلوگیری از race در ساخت session
        self._session_lock = asyncio.Lock()
        log_info(
            f"🧠 [BRAIN:apimem:init] url={self.api_url} | "
            f"key={'✅' if api_key else '❌'} | "
            f"timeout={timeout}s"
        )

    async def _ensure_session(self):
        """🎯 FIX: ساخت session با lock (بدون race)."""
        if self._session is not None:
            return
        async with self._session_lock:
            # چک دوباره داخل lock
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
        # 🐛 FIX: چک session
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
        """افزودن به حافظه API."""
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
        """جستجو در حافظه API."""
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
        """گرفتن context از حافظه API."""
        result = await self._post(
            "context", {"query": query, "n": n}
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "context" in result:
            return result["context"]
        return []

    async def clear(self) -> dict:
        """پاک کردن حافظه API."""
        return await self._post("clear", {})

    async def close(self) -> None:
        """🎯 FIX: idempotent close."""
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
# BrainMemory
# ═══════════════════════════════════════════
class BrainMemory:
    """حافظه: کوتاه‌مدت + خلاصه + ChromaDB یا API."""

    def __init__(
        self,
        max_tokens: int = MAX_CONTEXT_TOKENS,
        use_api: bool = False,
        api_url: str = "",
        api_key: str = "",
    ):
        self.max_tokens = max_tokens
        self.short_term: list[dict] = []
        self.summaries: list[str] = []
        self.use_api = use_api
        self.api_url = api_url
        self.api_key = api_key
        self.api_memory: APIMemory | None = None
        self.chroma_client = None
        self.collection = None
        # 🐛 FIX: نگه‌داری reference taskها
        self._pending_tasks: list[asyncio.Task] = []
        # 🐛 FIX: lock برای task list
        self._tasks_lock = threading.Lock()

        if use_api and api_url:
            self.api_memory = APIMemory(api_url, api_key)
            log_success(
                "✅ [BRAIN:mem:init] حالت API فعال"
            )
        else:
            self._init_chromadb()

    def _init_chromadb(self) -> None:
        """ساخت ChromaDB client (اختیاری)."""
        try:
            import chromadb
            self.chroma_client = chromadb.PersistentClient(
                path=str(MEMORY_DIR)
            )
            self.collection = (
                self.chroma_client.get_or_create_collection(
                    name="brain_memory",
                    metadata={"hnsw:space": "cosine"},
                )
            )
            try:
                items = self.collection.count()
            except Exception:
                items = "?"
            log_success(
                f"✅ [BRAIN:mem:init] ChromaDB | items={items}"
            )
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:mem:init] ChromaDB نصب نیست | {e}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:mem:init] ChromaDB خطا | "
                f"{type(e).__name__}: {e}"
            )

    def _spawn_task(self, coro) -> None:
        """🎯 FIX: ساخت task با reference نگه‌داری + سقف."""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError as e:
            log_warning(
                f"⚠️ [BRAIN:mem:task] create_task | "
                f"{type(e).__name__}: {e}"
            )
            return

        with self._tasks_lock:
            # 🎯 FIX: سقف برای pending tasks
            if len(self._pending_tasks) >= _MAX_PENDING_TASKS:
                # قدیمی‌ترین task تمام‌شده را حذف کن
                self._pending_tasks = [
                    t for t in self._pending_tasks
                    if not t.done()
                ]
                if len(self._pending_tasks) >= _MAX_PENDING_TASKS:
                    log_warning(
                        f"⚠️ [BRAIN:mem:task] سقف pending "
                        f"({_MAX_PENDING_TASKS}) — task جدید رد شد"
                    )
                    task.cancel()
                    return
            self._pending_tasks.append(task)

        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """🎯 FIX: exception دقیق‌تر."""
        with self._tasks_lock:
            try:
                self._pending_tasks.remove(task)
            except ValueError:
                pass

        # چک exception (به‌جز CancelledError)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            return
        if exc is not None:
            log_warning(
                f"⚠️ [BRAIN:mem:task] exception | "
                f"{type(exc).__name__}: {exc}"
            )

    def add(self, role: str, content: str) -> None:
        """افزودن پیام به حافظه."""
        if not content:
            return
        self.short_term.append({
            "role": role,
            "content": str(content)[:5000],
            "timestamp": time.time(),
        })

        # ─── API memory ───
        if self.api_memory:
            self._spawn_task(
                self.api_memory.add(role, content)
            )

        # ─── ChromaDB ───
        if self.collection:
            try:
                doc_id = hashlib.sha256(
                    f"{role}{content}{time.time()}".encode()
                ).hexdigest()
                self.collection.add(
                    documents=[content],
                    metadatas=[
                        {"role": role, "timestamp": time.time()}
                    ],
                    ids=[doc_id],
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:chroma] add | "
                    f"{type(e).__name__}: {e}"
                )

        # ─── خلاصه‌سازی ───
        if len(self.short_term) > _MAX_SHORT_TERM:
            self._compress()

    def _compress(self) -> None:
        """🎯 FIX: خلاصه‌سازی با threshold env-based."""
        if len(self.short_term) < _COMPRESS_THRESHOLD:
            return
        to_summarize = self.short_term[:_COMPRESS_THRESHOLD]
        text = "\n".join(
            f"{m['role']}: {m['content'][:300]}"
            for m in to_summarize
        )
        summary = (
            f"[خلاصه‌ی {len(to_summarize)} پیام قبلی] "
            f"{text[:600]}..."
        )
        self.summaries.append(summary)
        self.short_term = self.short_term[_COMPRESS_THRESHOLD:]
        log_info(
            f"📦 [BRAIN:mem:compress] "
            f"{len(to_summarize)} پیام → ۱ خلاصه | "
            f"remaining={len(self.short_term)}"
        )

    async def get_context(self, query: str = "") -> list[dict]:
        """ساخت context برای AI."""
        context: list[dict] = []
        tokens = 0

        # ─── short_term (آخرین N) ───
        for msg in self.short_term[-_CONTEXT_RECENT:]:
            context.append(msg)
            tokens += len(msg.get("content", "")) // 4

        # ─── خلاصه‌ها (جدیدترین اول) ───
        for summary in reversed(self.summaries):
            if tokens + len(summary) // 4 > self.max_tokens:
                break
            context.append({
                "role": "system",
                "content": summary,
            })
            tokens += len(summary) // 4

        # ─── API memory ───
        if self.api_memory and query:
            try:
                results = await self.api_memory.get_context(
                    query, n=_CONTEXT_API_N
                )
                for item in results:
                    if isinstance(item, dict):
                        doc = item.get("content", str(item))
                    else:
                        doc = str(item)
                    if tokens + len(doc) // 4 > self.max_tokens:
                        break
                    context.append({
                        "role": "system",
                        "content": f"[حافظه API] {doc}",
                    })
                    tokens += len(doc) // 4
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:api] context | "
                    f"{type(e).__name__}: {e}"
                )

        # ─── ChromaDB ───
        if query and self.collection:
            try:
                results = self.collection.query(
                    query_texts=[query],
                    n_results=_CONTEXT_CHROMA_N,
                )
                docs = results.get("documents") or [[]]
                for doc in docs[0]:
                    if tokens + len(doc) // 4 > self.max_tokens:
                        break
                    context.append({
                        "role": "system",
                        "content": f"[حافظه مرتبط] {doc}",
                    })
                    tokens += len(doc) // 4
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:chroma] context | "
                    f"{type(e).__name__}: {e}"
                )

        return context

    def clear(self) -> None:
        """پاک کردن حافظه."""
        self.short_term = []
        self.summaries = []
        if self.api_memory:
            self._spawn_task(self.api_memory.clear())
        if self.collection:
            try:
                ids = self.collection.get().get("ids", [])
                if ids:
                    self.collection.delete(ids=ids)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:chroma] clear | "
                    f"{type(e).__name__}: {e}"
                )
        log_info("🗑️ [BRAIN:mem:clear] حافظه پاک شد")

    async def search(
        self, query: str, n: int = 5
    ) -> list[dict]:
        """جستجوی ترکیبی (API > Chroma)."""
        if self.api_memory:
            try:
                results = await self.api_memory.search(query, n)
                if results:
                    return results
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:api] search | "
                    f"{type(e).__name__}: {e}"
                )

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
                    "role": m.get("role", "unknown")
                    if isinstance(m, dict) else "unknown",
                    "content": str(d)[:300],
                }
                for d, m in zip(
                    docs[0], metas[0], strict=False
                )
            ]
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:mem:chroma] search | "
                f"{type(e).__name__}: {e}"
            )
            return []

    async def close(self) -> None:
        """🎯 FIX: منتظر pending taskها + بستن API session."""
        # ─── منتظر pending taskها (با timeout) ───
        with self._tasks_lock:
            pending = list(self._pending_tasks)
        if pending:
            log_info(
                f"⏳ [BRAIN:mem:close] انتظار {len(pending)} "
                f"task pending..."
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *pending, return_exceptions=True
                    ),
                    timeout=5.0,
                )
            except TimeoutError:
                log_warning(
                    "⏰ [BRAIN:mem:close] timeout — "
                    "taskهای باقی‌مانده cancel می‌شوند"
                )
                for t in pending:
                    if not t.done():
                        t.cancel()
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:close] gather | "
                    f"{type(e).__name__}: {e}"
                )

        # ─── بستن API ───
        if self.api_memory:
            await self.api_memory.close()

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        with self._tasks_lock:
            pending_count = sum(
                1 for t in self._pending_tasks if not t.done()
            )
        return {
            "short_term": len(self.short_term),
            "summaries": len(self.summaries),
            "pending_tasks": pending_count,
            "api_memory": self.api_memory is not None,
            "chroma": self.collection is not None,
            "max_tokens": self.max_tokens,
        }


# ═══════════════════════════════════════════
# ProjectSemanticSearch
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
        # 🎯 FIX: sentence-transformers در Python 3.14 پشتیبانی نمی‌شه
        # و دانلود مدل خیلی سنگینه. پس semantic search رو غیرفعال می‌کنیم.
        import os
        if os.getenv("BRAIN_DISABLE_SEMANTIC", "true").lower() == "true":
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