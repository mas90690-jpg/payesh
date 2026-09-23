"""حافظه و کش Brain — SmartCache, APIMemory, BrainMemory, Semantic."""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

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
# SmartCache
# ═══════════════════════════════════════════
class SmartCache:
    """کش با TTL، throttle save، و atomic write."""

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
        return hashlib.sha256(f"{text}|{model}".encode()).hexdigest()

    def get(self, text: str, model: str = "") -> str | None:
        key = self._key(text, model)
        with self._lock:
            entry = self.cache.get(key)
        if (
            entry
            and (datetime.now() - datetime.fromtimestamp(entry["timestamp"]))
            < self.ttl
        ):
            self._hits += 1
            return entry["answer"]
        self._misses += 1
        return None

    def set(self, text: str, answer: str, model: str = ""):
        key = self._key(text, model)
        with self._lock:
            self.cache[key] = {
                "answer": answer,
                "timestamp": time.time(),
            }
            if len(self.cache) > self.max_size:
                sorted_items = sorted(
                    self.cache.items(),
                    key=lambda x: x[1]["timestamp"],
                )
                self.cache = dict(
                    sorted_items[-int(self.max_size * 0.8):]
                )
            self._dirty = True

        now = time.time()
        if now - self._last_save >= CACHE_SAVE_INTERVAL_S:
            self.save()

    def flush(self):
        with self._lock:
            dirty = self._dirty
        if dirty:
            self.save()

    def load(self):
        if not self.filepath.exists():
            return
        try:
            with self._lock:
                self.cache = json.loads(
                    self.filepath.read_text(encoding="utf-8")
                )
            log_info(
                f"💾 [BRAIN:cache:{self.filepath.stem}] "
                f"{len(self.cache)} مدخل بارگذاری شد"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                f"JSON نامعتبر | line={e.lineno} — کش خالی شد"
            )
            self.cache = {}
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:cache:{self.filepath.stem}] "
                f"{type(e).__name__}: {e}"
            )
            self.cache = {}

    def save(self):
        try:
            with self._lock:
                data = json.dumps(
                    self.cache, ensure_ascii=False, indent=2
                )
                self._last_save = time.time()
                self._dirty = False
            tmp = self.filepath.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.filepath)
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:cache:{self.filepath.stem}] ذخیره ناموفق | "
                f"{type(e).__name__}: {e}"
            )

    def clear(self):
        with self._lock:
            self.cache = {}
            self._hits = 0
            self._misses = 0
            self._dirty = True
        self.save()
        log_info(f"🗑️ [BRAIN:cache:{self.filepath.stem}] پاک شد")

    def stats(self) -> dict:
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
            }


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
        log_info(
            f"🧠 [BRAIN:apimem] آماده | url={self.api_url} | "
            f"key={'✅' if api_key else '❌'}"
        )

    async def _ensure_session(self):
        if self._session is None:
            import aiohttp
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )

    async def _post(self, endpoint: str, data: dict) -> dict:
        await self._ensure_session()
        t0 = time.monotonic()
        try:
            async with self._session.post(
                f"{self.api_url}/{endpoint}", json=data
            ) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                if resp.status >= 400:
                    body = (await resp.text())[:LOG_PREVIEW_LEN]
                    log_warning(
                        f"⚠️ [BRAIN:apimem] {endpoint} | "
                        f"HTTP {resp.status} | {elapsed_ms:.0f}ms | "
                        f"body={body!r}"
                    )
                    return {"error": f"HTTP {resp.status}"}
                return await resp.json()
        except TimeoutError:
            log_error(
                f"⏰ [BRAIN:apimem] {endpoint} | "
                f"Timeout {self.timeout}s"
            )
            return {"error": "timeout"}
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:apimem] {endpoint} | "
                f"{type(e).__name__}: {e}"
            )
            return {"error": str(e)}

    async def add(
        self, role: str, content: str, metadata: dict = None
    ):
        return await self._post(
            "add",
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
            },
        )

    async def search(self, query: str, n: int = 5) -> list[dict]:
        result = await self._post("search", {"query": query, "n": n})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def get_context(
        self, query: str = "", n: int = 5
    ) -> list[dict]:
        result = await self._post("context", {"query": query, "n": n})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "context" in result:
            return result["context"]
        return []

    async def clear(self):
        await self._post("clear", {})

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None


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
        self.api_memory = None
        self.chroma_client = None
        self.collection = None
        self._pending_tasks: list[asyncio.Task] = []

        if use_api and api_url:
            self.api_memory = APIMemory(api_url, api_key)
            log_success("✅ [BRAIN:mem] حالت API فعال")
        else:
            self._init_chromadb()

    def _init_chromadb(self):
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
                f"✅ [BRAIN:mem] ChromaDB آماده | items={items}"
            )
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:mem] ChromaDB نصب نیست | {e}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:mem] ChromaDB خطا | "
                f"{type(e).__name__}: {e}"
            )

    def _spawn_task(self, coro) -> None:
        try:
            task = asyncio.create_task(coro)
            self._pending_tasks.append(task)
            task.add_done_callback(self._on_task_done)
        except RuntimeError:
            log_warning("⚠️ [BRAIN:mem] task رها شد")

    def _on_task_done(self, task: asyncio.Task) -> None:
        try:
            self._pending_tasks.remove(task)
        except ValueError:
            pass
        try:
            exc = task.exception()
            if exc:
                log_warning(
                    f"⚠️ [BRAIN:mem:task] "
                    f"{type(exc).__name__}: {exc}"
                )
        except (asyncio.CancelledError, Exception):
            pass

    def add(self, role: str, content: str):
        self.short_term.append(
            {
                "role": role,
                "content": content,
                "timestamp": time.time(),
            }
        )
        if self.api_memory:
            self._spawn_task(self.api_memory.add(role, content))
        if self.collection:
            try:
                doc_id = hashlib.md5(
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
                    f"⚠️ [BRAIN:mem:chroma] add ناموفق | {e}"
                )
        if len(self.short_term) > 20:
            self._compress()

    def _compress(self):
        if len(self.short_term) < 10:
            return
        to_summarize = self.short_term[:15]
        text = "\n".join(
            [
                f"{m['role']}: {m['content'][:300]}"
                for m in to_summarize
            ]
        )
        summary = (
            f"[خلاصه‌ی {len(to_summarize)} پیام قبلی] "
            f"{text[:600]}..."
        )
        self.summaries.append(summary)
        self.short_term = self.short_term[15:]
        log_info(
            f"📦 [BRAIN:mem] خلاصه‌سازی | "
            f"{len(to_summarize)} پیام → ۱ خلاصه"
        )

    async def get_context(self, query: str = "") -> list[dict]:
        context: list[dict] = []
        tokens = 0
        for msg in self.short_term[-5:]:
            context.append(msg)
            tokens += len(msg.get("content", "")) // 4
        for summary in reversed(self.summaries):
            if tokens + len(summary) // 4 > self.max_tokens:
                break
            context.append(
                {"role": "system", "content": summary}
            )
            tokens += len(summary) // 4
        if self.api_memory and query:
            try:
                results = await self.api_memory.get_context(
                    query, n=3
                )
                for item in results:
                    if isinstance(item, dict):
                        doc = item.get("content", str(item))
                        if tokens + len(doc) // 4 > self.max_tokens:
                            break
                        context.append(
                            {
                                "role": "system",
                                "content": f"[حافظه API] {doc}",
                            }
                        )
                        tokens += len(doc) // 4
            except Exception as e:
                log_warning(f"⚠️ [BRAIN:mem:api] ناموفق | {e}")
        if query and self.collection:
            try:
                results = self.collection.query(
                    query_texts=[query], n_results=3
                )
                if results.get("documents"):
                    for doc in results["documents"][0]:
                        if tokens + len(doc) // 4 > self.max_tokens:
                            break
                        context.append(
                            {
                                "role": "system",
                                "content": f"[حافظه مرتبط] {doc}",
                            }
                        )
                        tokens += len(doc) // 4
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:mem:chroma] ناموفق | {e}"
                )
        return context

    def clear(self):
        self.short_term = []
        self.summaries = []
        if self.api_memory:
            self._spawn_task(self.api_memory.clear())
        if self.collection:
            try:
                self.collection.delete(
                    ids=self.collection.get()["ids"]
                )
            except Exception:
                pass
        log_info("🗑️ [BRAIN:mem] حافظه پاک شد")

    async def search(self, query: str, n: int = 5) -> list[dict]:
        if self.api_memory:
            try:
                results = await self.api_memory.search(query, n)
                if results:
                    return results
            except Exception as e:
                log_warning(f"⚠️ [BRAIN:mem:api] ناموفق | {e}")
        if not self.collection:
            return []
        try:
            results = self.collection.query(
                query_texts=[query], n_results=n
            )
            if not results or not results.get("documents"):
                return []
            return [
                {
                    "role": m.get("role", "unknown"),
                    "content": d[:300],
                }
                for d, m in zip(
                    results["documents"][0],
                    results["metadatas"][0], strict=False,
                )
            ]
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:mem:chroma] ناموفق | {e}"
            )
            return []

    async def close(self):
        if self.api_memory:
            await self.api_memory.close()


# ═══════════════════════════════════════════
# ProjectSemanticSearch
# ═══════════════════════════════════════════
class ProjectSemanticSearch:
    """جستجوی معنایی در کدبیس با ChromaDB."""

    def __init__(self):
        self.client = None
        self.collection = None
        self._init()

    def _init(self):
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
            log_success("✅ [BRAIN:sem] Semantic Search فعال شد")
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:sem] chromadb نصب نیست | {e}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:sem] غیرفعال | "
                f"{type(e).__name__}: {e}"
            )

    def index_files(
        self, patterns: list[str] | None = None
    ) -> int:
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
            for fpath in glob.glob(
                pattern, root_dir=str(project), recursive=True
            ):
                try:
                    full = project / fpath
                    if (
                        full.is_file()
                        and full.stat().st_size
                        < MAX_FILE_SIZE_BYTES
                    ):
                        content = full.read_text(
                            encoding="utf-8", errors="ignore"
                        )
                        self.collection.add(
                            documents=[content],
                            metadatas=[{"path": str(fpath)}],
                            ids=[
                                f"file_{hashlib.md5(fpath.encode()).hexdigest()}"
                            ],
                        )
                        total += 1
                except Exception as e:
                    failed += 1
                    if failed <= 3:
                        log_warning(
                            f"⚠️ [BRAIN:sem] index fail | "
                            f"{fpath} | {type(e).__name__}: {e}"
                        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [BRAIN:sem] {total} فایل ایندکس شد | "
            f"failed={failed} | {elapsed_ms:.0f}ms"
        )
        return total

    def search(self, query: str, n: int = 5) -> list[dict]:
        if not self.collection:
            return []
        try:
            results = self.collection.query(
                query_texts=[query], n_results=n
            )
            if not results or not results.get("documents"):
                return []
            return [
                {
                    "path": m.get("path", "?"),
                    "content": d[:500]
                    + ("..." if len(d) > 500 else ""),
                }
                for d, m in zip(
                    results["documents"][0],
                    results["metadatas"][0], strict=False,
                )
            ]
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:sem] search ناموفق | "
                f"{type(e).__name__}: {e}"
            )
            return []
