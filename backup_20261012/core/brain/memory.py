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
class PersistentMemory:
    """حافظهٔ پایدار — هیچ‌وقت پاک نمی‌شود.

    SQLite + FTS5 برای جستجوی سریع متن کامل.

    Features:
        - `remember(category, key, value, importance)` → ذخیره
        - `recall(query, category, limit)` → جستجو با FTS
        - `get_context(query, n)` → context برای AI
        - `count(category)` → تعداد
        - `forget(days, min_importance)` → فراموشی انتخابی
        - `get_stats()` → آمار
        - `close()` → بستن تمیز

    Categories پیشنهادی:
        - "conversation"     → گفتگوهای عادی
        - "code_review"      → نتایج بازبینی کد
        - "knowledge"        → دانش‌های یادگرفته
        - "reflexion"        → درس‌های اشتباهات
        - "learned_rule"     → قوانین یادگرفته‌شده
    """

    def __init__(
        self,
        db_path: str | None = None,
        max_rows: int | None = None,
    ):
        # ─── مسیر absolute ───
        raw_path = (
            db_path
            or _PERSISTENT_DB_PATH
            or "storage/brain/persistent.db"
        )
        p = Path(raw_path)
        if not p.is_absolute():
            try:
                p = Path(PROJECT_BASE) / raw_path
            except Exception:
                p = Path.cwd() / raw_path

        self.db_path = str(p)
        self.max_rows = max(
            100,
            int(max_rows or _PERSISTENT_MAX_ROWS or 100_000),
        )

        # ─── ساخت پوشه ───
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:mkdir] {type(e).__name__}: {e}"
            )

        # ─── lock ───
        self._lock = threading.RLock()

        # ─── schema ───
        self._init_db()

        log_success(
            f"🧠 [MEM:init] آماده | db={self.db_path} | "
            f"max_rows={self.max_rows} | "
            f"count={self.count()}"
        )

    # ═══════════════════════════════════════════
    # schema
    # ═══════════════════════════════════════════
    def _init_db(self) -> None:
        """ساخت جدول‌ها + FTS + indexes."""
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    # ─── جدول اصلی ───
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS memory (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            category TEXT NOT NULL,
                            key TEXT NOT NULL,
                            value TEXT NOT NULL,
                            importance REAL DEFAULT 0.5,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            accessed_at TIMESTAMP,
                            access_count INTEGER DEFAULT 0
                        )
                    """)

                    # ─── indexes ───
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_memory_category
                        ON memory(category)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_memory_key
                        ON memory(key)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_memory_created
                        ON memory(created_at DESC)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_memory_importance
                        ON memory(importance DESC)
                    """)

                    # ─── FTS5 ───
                    try:
                        conn.execute("""
                            CREATE VIRTUAL TABLE IF NOT EXISTS
                            memory_fts
                            USING fts5(
                                key, value, category,
                                content='memory',
                                content_rowid='id'
                            )
                        """)

                        # trigger INSERT
                        conn.execute("""
                            CREATE TRIGGER IF NOT EXISTS memory_ai
                            AFTER INSERT ON memory BEGIN
                                INSERT INTO memory_fts(
                                    rowid, key, value, category
                                )
                                VALUES (
                                    new.id, new.key,
                                    new.value, new.category
                                );
                            END
                        """)

                        # trigger DELETE
                        conn.execute("""
                            CREATE TRIGGER IF NOT EXISTS memory_ad
                            AFTER DELETE ON memory BEGIN
                                INSERT INTO memory_fts(
                                    memory_fts, rowid, key,
                                    value, category
                                )
                                VALUES (
                                    'delete', old.id, old.key,
                                    old.value, old.category
                                );
                            END
                        """)
                    except sqlite3.OperationalError as e:
                        log_warning(
                            f"⚠️ [MEM:fts] FTS5 در دسترس نیست | "
                            f"{type(e).__name__}: {e}"
                        )

                    conn.commit()

                log_info(
                    f"🗄️ [MEM:schema] آماده | {self.db_path}"
                )
            except Exception as e:
                log_error(
                    f"❌ [MEM:schema] {type(e).__name__}: {e}"
                )
                raise

    # ═══════════════════════════════════════════
    # remember
    # ═══════════════════════════════════════════
    def remember(
        self,
        category: str,
        key: str,
        value: str,
        importance: float = 0.5,
    ) -> int:
        """ذخیرهٔ یک خاطره.

        Args:
            category: دسته (conversation, code_review, knowledge, ...).
            key: کلید (سؤال کاربر، مسیر فایل، ...).
            value: مقدار (پاسخ، محتوا، ...).
            importance: اهمیت (0-1).

        Returns:
            id رکورد جدید یا -1 در صورت خطا.
        """
        if not key or not value:
            log_warning(
                "⚠️ [MEM:remember] key/value خالی — رد شد"
            )
            return -1

        # sanitize
        cat = str(category or "misc")[:100]
        k = str(key)[:500]
        v = str(value)[:10000]

        try:
            imp = float(importance)
        except (TypeError, ValueError):
            imp = 0.5
        imp = max(0.0, min(1.0, imp))

        try:
            with self._lock:
                with sqlite3.connect(self.db_path) as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO memory
                            (category, key, value, importance)
                        VALUES (?, ?, ?, ?)
                        """,
                        (cat, k, v, imp),
                    )
                    conn.commit()
                    new_id = cur.lastrowid or -1

                    # ─── cleanup اگر از سقف گذشت ───
                    self._maybe_cleanup(conn)

            log_info(
                f"💾 [MEM:remember] {cat} | id={new_id} | "
                f"key={k[:60]!r} | imp={imp}"
            )
            return new_id

        except Exception as e:
            log_error(
                f"❌ [MEM:remember] {type(e).__name__}: {e}"
            )
            return -1

    def _maybe_cleanup(self, conn) -> None:
        """اگر از سقف گذشت، قدیمی‌ترین‌ها را حذف کن."""
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM memory"
            ).fetchone()
            total = row[0] if row else 0

            if total <= self.max_rows:
                return

            # حذف ۱۰٪ قدیمی‌ترین + کم‌اهمیت
            excess = total - self.max_rows
            remove_n = max(excess, self.max_rows // 10)

            conn.execute(
                """
                DELETE FROM memory
                WHERE id IN (
                    SELECT id FROM memory
                    ORDER BY importance ASC, created_at ASC
                    LIMIT ?
                )
                """,
                (remove_n,),
            )
            conn.commit()

            log_warning(
                f"🧹 [MEM:cleanup] {remove_n} رکورد قدیمی حذف شد | "
                f"total_before={total}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:cleanup] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # recall
    # ═══════════════════════════════════════════
    def recall(
        self,
        query: str = "",
        category: str = "",
        limit: int = 10,
    ) -> list[dict]:
        """به‌یاد آوردن — با FTS یا LIKE.

        Args:
            query: متن جستجو.
            category: فیلتر دسته (اختیاری).
            limit: حداکثر نتایج.

        Returns:
            لیست dict با id, category, key, value,
            importance, created_at, access_count.
        """
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 10

        results: list[dict] = []

        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row

                    # ─── FTS اول ───
                    if query and query.strip():
                        try:
                            sql = """
                                SELECT m.* FROM memory m
                                JOIN memory_fts f ON m.id = f.rowid
                                WHERE memory_fts MATCH ?
                            """
                            params: list[Any] = [query]
                            if category:
                                sql += " AND m.category = ?"
                                params.append(category)
                            sql += (
                                " ORDER BY m.importance DESC, "
                                "m.created_at DESC LIMIT ?"
                            )
                            params.append(limit)

                            rows = conn.execute(
                                sql, params
                            ).fetchall()
                            results = [dict(r) for r in rows]
                        except Exception as e:
                            log_warning(
                                f"⚠️ [MEM:fts] {e} — fallback LIKE"
                            )

                    # ─── fallback: LIKE ───
                    if not results:
                        sql = (
                            "SELECT * FROM memory WHERE 1=1"
                        )
                        params = []
                        if category:
                            sql += " AND category = ?"
                            params.append(category)
                        if query:
                            sql += (
                                " AND (key LIKE ? "
                                "OR value LIKE ?)"
                            )
                            like = f"%{query}%"
                            params.extend([like, like])
                        sql += (
                            " ORDER BY importance DESC, "
                            "created_at DESC LIMIT ?"
                        )
                        params.append(limit)

                        rows = conn.execute(
                            sql, params
                        ).fetchall()
                        results = [dict(r) for r in rows]

                    # ─── آپدیت access_count ───
                    if results:
                        ids = [r["id"] for r in results]
                        placeholders = ",".join(
                            "?" * len(ids)
                        )
                        conn.execute(
                            f"""
                            UPDATE memory
                            SET access_count =
                                access_count + 1,
                                accessed_at = CURRENT_TIMESTAMP
                            WHERE id IN ({placeholders})
                            """,
                            ids,
                        )
                        conn.commit()

            except Exception as e:
                log_error(
                    f"❌ [MEM:recall] {type(e).__name__}: {e}"
                )

        log_info(
            f"🔍 [MEM:recall] query={query[:40]!r} | "
            f"category={category or 'all'} | "
            f"results={len(results)}"
        )
        return results

    # ═══════════════════════════════════════════
    # get_context
    # ═══════════════════════════════════════════
    def get_context(
        self, query: str = "", n: int = 5
    ) -> str:
        """ساخت context از حافظه — برای AI.

        Args:
            query: متن جستجو.
            n: تعداد آیتم.

        Returns:
            متن آماده برای درج در prompt (یا "").
        """
        results = self.recall(query=query, limit=n)
        if not results:
            return ""

        lines = ["# حافظهٔ مرتبط:"]
        for r in results:
            cat = r.get("category", "?")
            key = str(r.get("key", ""))[:100]
            val = str(r.get("value", ""))[:300]
            lines.append(
                f"\n[{cat}] {key}\n→ {val}"
            )
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # count
    # ═══════════════════════════════════════════
    def count(self, category: str = "") -> int:
        """تعداد رکوردها (کل یا یک دسته)."""
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    if category:
                        cur = conn.execute(
                            "SELECT COUNT(*) FROM memory "
                            "WHERE category = ?",
                            (category,),
                        )
                    else:
                        cur = conn.execute(
                            "SELECT COUNT(*) FROM memory"
                        )
                    row = cur.fetchone()
                    return row[0] if row else 0
            except Exception as e:
                log_warning(
                    f"⚠️ [MEM:count] {type(e).__name__}: {e}"
                )
                return 0

    # ═══════════════════════════════════════════
    # forget
    # ═══════════════════════════════════════════
    def forget(
        self,
        days: int = 30,
        min_importance: float = 0.2,
    ) -> int:
        """فراموشی — حذف رکوردهای کم‌اهمیت و قدیمی.

        Args:
            days: چند روز پیش.
            min_importance: اهمیت کمتر از این حذف شود.

        Returns:
            تعداد حذف‌شده.
        """
        try:
            days = max(1, int(days))
        except (TypeError, ValueError):
            days = 30

        try:
            imp = float(min_importance)
        except (TypeError, ValueError):
            imp = 0.2

        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cur = conn.execute(
                        """
                        DELETE FROM memory
                        WHERE importance < ?
                          AND julianday('now')
                              - julianday(created_at) > ?
                          AND (
                              access_count = 0
                              OR access_count IS NULL
                          )
                        """,
                        (imp, days),
                    )
                    conn.commit()
                    n = cur.rowcount or 0

                if n > 0:
                    log_success(
                        f"🧹 [MEM:forget] {n} رکورد "
                        f"(imp<{imp}, age>{days}d)"
                    )
                return n
            except Exception as e:
                log_warning(
                    f"⚠️ [MEM:forget] {type(e).__name__}: {e}"
                )
                return 0

    # ═══════════════════════════════════════════
    # clear (دستی — با احتیاط)
    # ═══════════════════════════════════════════
    def clear(self, category: str = "") -> int:
        """پاک کردن همه یا یک دسته — دستی.

        ⚠️ با احتیاط استفاده کن.
        """
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    if category:
                        cur = conn.execute(
                            "DELETE FROM memory "
                            "WHERE category = ?",
                            (category,),
                        )
                    else:
                        cur = conn.execute(
                            "DELETE FROM memory"
                        )
                    conn.commit()
                    n = cur.rowcount or 0
                    conn.execute("VACUUM")
                    conn.commit()

                log_warning(
                    f"🗑️ [MEM:clear] {n} رکورد "
                    f"(category={category or 'all'})"
                )
                return n
            except Exception as e:
                log_error(
                    f"❌ [MEM:clear] {type(e).__name__}: {e}"
                )
                return 0

    # ═══════════════════════════════════════════
    # get_stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """آمار کامل — برای debug."""
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row

                    total = conn.execute(
                        "SELECT COUNT(*) as n FROM memory"
                    ).fetchone()["n"]

                    by_cat = conn.execute("""
                        SELECT category, COUNT(*) as n
                        FROM memory
                        GROUP BY category
                        ORDER BY n DESC
                        LIMIT 20
                    """).fetchall()

                    try:
                        size_bytes = os.path.getsize(
                            self.db_path
                        )
                        size_kb = round(size_bytes / 1024, 1)
                    except Exception:
                        size_kb = 0

                    return {
                        "total": total,
                        "by_category": {
                            r["category"]: r["n"]
                            for r in by_cat
                        },
                        "db_path": self.db_path,
                        "size_kb": size_kb,
                        "max_rows": self.max_rows,
                    }
            except Exception as e:
                log_warning(
                    f"⚠️ [MEM:stats] {type(e).__name__}: {e}"
                )
                return {
                    "total": 0,
                    "by_category": {},
                    "db_path": self.db_path,
                    "size_kb": 0,
                    "max_rows": self.max_rows,
                }

    # ═══════════════════════════════════════════
    # close
    # ═══════════════════════════════════════════
    def close(self) -> None:
        """بستن تمیز — no-op فعلاً (connection per-call)."""
        log_info("🔌 [MEM:close] بسته شد")

    # ═══════════════════════════════════════════
    # __repr__
    # ═══════════════════════════════════════════
    def __repr__(self) -> str:
        return (
            f"<PersistentMemory db={os.path.basename(self.db_path)} "
            f"count={self.count()}>"
        )


# ═══════════════════════════════════════════
# PersistentMemory Singleton
# ═══════════════════════════════════════════
_shared_persistent: PersistentMemory | None = None
_shared_persistent_lock = threading.Lock()


def get_persistent_memory() -> PersistentMemory | None:
    """دریافت PersistentMemory singleton.

    اگر `PERSISTENT_MEMORY_ENABLED=false` باشد، None برمی‌گرداند.
    """
    global _shared_persistent

    if not _PERSISTENT_MEMORY_ENABLED:
        return None

    if _shared_persistent is None:
        with _shared_persistent_lock:
            if _shared_persistent is None:
                try:
                    _shared_persistent = PersistentMemory()
                except Exception as e:
                    log_error(
                        f"❌ [MEM:singleton] "
                        f"{type(e).__name__}: {e}"
                    )
                    return None
    return _shared_persistent


def reset_persistent_memory() -> None:
    """پاک کردن singleton — فقط برای تست."""
    global _shared_persistent
    with _shared_persistent_lock:
        if _shared_persistent is not None:
            try:
                _shared_persistent.close()
            except Exception:
                pass
            log_info("🗑️ [MEM:singleton] reset شد")
        _shared_persistent = None


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
        self._pending_tasks: list[asyncio.Task] = []
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
        """ساخت task با reference نگه‌داری + سقف."""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError as e:
            log_warning(
                f"⚠️ [BRAIN:mem:task] create_task | "
                f"{type(e).__name__}: {e}"
            )
            return

        with self._tasks_lock:
            if len(self._pending_tasks) >= _MAX_PENDING_TASKS:
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
        """exception دقیق‌تر."""
        with self._tasks_lock:
            try:
                self._pending_tasks.remove(task)
            except ValueError:
                pass

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
        """خلاصه‌سازی با threshold env-based."""
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
        """منتظر pending taskها + بستن API session."""
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

        if self.api_memory:
            await self.api_memory.close()

    def get_stats(self) -> dict[str, Any]:
        """خلاصهٔ وضعیت — برای debug."""
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
    "BrainMemory",
    "ProjectSemanticSearch",
    "PersistentMemory",
    "get_persistent_memory",
    "reset_persistent_memory",
]