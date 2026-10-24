"""
🧠 BrainMemory — مغز انسان برای پروژه

۵ حافظه:
- Working Memory (کوتاه‌مدت): ۱۰ سؤال آخر
- Long-Term Memory (بلندمدت): دانش پروژه
- Procedural Memory (رویه‌ای): کارهای انجام‌شده
- Semantic Memory (معنایی): ChromaDB
- Persistent Memory (پایدار): SQLite + FTS5

همه در storage/project_knowledge/ (یا storage/brain/)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path


from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)

# ══════════════════════════════════════════════════════════
# مسیرها
# ══════════════════════════════════════════════════════════
try:
    from data import paths as data_paths
    BASE_DIR = Path(data_paths.PROJECT_ROOT) / "storage" / "project_knowledge"
except Exception:
    BASE_DIR = Path("storage") / "project_knowledge"

WORKING_FILE = BASE_DIR / "working_memory.json"
LONG_TERM_DB = BASE_DIR / "long_term.db"
PROCEDURAL_FILE = BASE_DIR / "procedural.json"
PERSISTENT_DB = BASE_DIR / "persistent.db"


# ══════════════════════════════════════════════════════════
# ۱. حافظه کوتاه‌مدت (Working Memory)
# ══════════════════════════════════════════════════════════
class WorkingMemory:
    """حافظه کوتاه‌مدت — ۱۰ سؤال آخر."""

    MAX_ITEMS = 10

    def __init__(self):
        self.items: list[dict] = []
        self._load()

    def _load(self):
        if WORKING_FILE.exists():
            try:
                data = json.loads(WORKING_FILE.read_text(encoding="utf-8"))
                self.items = data.get("items", [])
            except Exception as e:
                log_warning(f"⚠️ [WM:load] {e}")
                self.items = []

    def _save(self):
        try:
            WORKING_FILE.parent.mkdir(parents=True, exist_ok=True)
            WORKING_FILE.write_text(
                json.dumps({
                    "items": self.items,
                    "updated_at": datetime.now().isoformat(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log_warning(f"⚠️ [WM:save] {e}")

    def add(self, question: str, answer: str, importance: float = 0.5):
        """افزودن به حافظه کوتاه."""
        self.items.append({
            "q": question[:500],
            "a": answer[:1000],
            "t": datetime.now().isoformat(),
            "imp": importance,
        })
        self.items = self.items[-self.MAX_ITEMS:]
        self._save()

    def get_recent(self, n: int = 5) -> list[dict]:
        """آخرین n مورد."""
        return self.items[-n:]

    def get_by_key(self, key: str) -> dict | None:
        """جستجو در حافظه کوتاه."""
        key_low = key.lower().strip()
        for item in reversed(self.items):
            if key_low == item["q"].lower().strip():
                return item
        return None

    def clear(self):
        """پاکسازی."""
        self.items = []
        self._save()


# ══════════════════════════════════════════════════════════
# ۲. حافظه بلندمدت (Long-Term Memory)
# ══════════════════════════════════════════════════════════
class LongTermMemory:
    """حافظه بلندمدت — SQLite."""

    def __init__(self):
        self.db_path = str(LONG_TERM_DB)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    importance REAL DEFAULT 0.5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_category ON knowledge(category);
                CREATE INDEX IF NOT EXISTS idx_key ON knowledge(key);

                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wrong TEXT NOT NULL,
                    correct TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def add(self, category: str, key: str, value: str, importance: float = 0.5):
        """افزودن دانش."""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM knowledge WHERE category=? AND key=?",
                (category, key),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE knowledge SET value=?, importance=?, updated_at=? WHERE id=?",
                    (value, importance, datetime.now(), existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO knowledge (category, key, value, importance) VALUES (?, ?, ?, ?)",
                    (category, key, value, importance),
                )
            conn.commit()

    def get(self, category: str, key: str) -> str | None:
        """گرفتن دانش."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM knowledge WHERE category=? AND key=?",
                (category, key),
            ).fetchone()
            return row[0] if row else None

    def get_category(self, category: str) -> list[dict]:
        """همه دانش یک دسته."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT key, value, importance FROM knowledge WHERE category=? ORDER BY importance DESC",
                (category,),
            ).fetchall()
            return [{"key": r[0], "value": r[1], "importance": r[2]} for r in rows]

    def add_lesson(self, wrong: str, correct: str):
        """افزودن درس."""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO lessons (wrong, correct) VALUES (?, ?)",
                (wrong[:500], correct[:1000]),
            )
            conn.commit()

    def search_lessons(self, query: str) -> list[dict]:
        """جستجوی درس."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT wrong, correct FROM lessons WHERE wrong LIKE ? OR correct LIKE ? LIMIT 5",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
            return [{"wrong": r[0], "correct": r[1]} for r in rows]

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            knowledge_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge"
            ).fetchone()[0]
            lessons_count = conn.execute(
                "SELECT COUNT(*) FROM lessons"
            ).fetchone()[0]
            return {
                "knowledge": knowledge_count,
                "lessons": lessons_count,
            }


# ══════════════════════════════════════════════════════════
# ۳. حافظه رویه‌ای (Procedural Memory)
# ══════════════════════════════════════════════════════════
class ProceduralMemory:
    """حافظه رویه‌ای — کارهای انجام‌شده."""

    def __init__(self):
        self.tasks_done: dict[str, str] = {}
        self.patterns: dict[str, int] = {}
        self._load()

    def _load(self):
        if PROCEDURAL_FILE.exists():
            try:
                data = json.loads(PROCEDURAL_FILE.read_text(encoding="utf-8"))
                self.tasks_done = data.get("tasks_done", {})
                self.patterns = data.get("patterns", {})
            except Exception as e:
                log_warning(f"⚠️ [PM:load] {e}")

    def _save(self):
        try:
            PROCEDURAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROCEDURAL_FILE.write_text(
                json.dumps({
                    "tasks_done": self.tasks_done,
                    "patterns": self.patterns,
                    "updated_at": datetime.now().isoformat(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log_warning(f"⚠️ [PM:save] {e}")

    def mark_done(self, task: str):
        """ثبت کار انجام‌شده."""
        self.tasks_done[task.lower().strip()] = datetime.now().isoformat()
        self._save()

    def is_done(self, task: str, within_hours: int = 24) -> bool:
        """آیا کار قبلاً انجام شده؟"""
        key = task.lower().strip()
        if key not in self.tasks_done:
            return False
        try:
            done_at = datetime.fromisoformat(self.tasks_done[key])
            return (datetime.now() - done_at) < timedelta(hours=within_hours)
        except Exception:
            return False

    def add_pattern(self, pattern: str):
        """افزودن الگو."""
        self.patterns[pattern] = self.patterns.get(pattern, 0) + 1
        self._save()

    def get_top_patterns(self, n: int = 5) -> list[tuple[str, int]]:
        """پرکاربردترین الگوها."""
        return sorted(self.patterns.items(), key=lambda x: -x[1])[:n]


# ══════════════════════════════════════════════════════════
# ۴. حافظه معنایی (Semantic Memory)
# ══════════════════════════════════════════════════════════
class SemanticMemory:
    """حافظه معنایی — ChromaDB (اختیاری)."""

    def __init__(self):
        self.enabled = False
        self.collection = None

        # ⚡ غیرفعال کردن ChromaDB برای سرعت (پیش‌فرض: خاموش)
        if os.environ.get("DISABLE_CHROMA", "0") == "1":
            log_info("ℹ️ [SEM:init] ChromaDB غیرفعال (DISABLE_CHROMA=1)")
            return

        try:
            import chromadb
            client = chromadb.PersistentClient(
                path=str(BASE_DIR / "chromadb")
            )
            self.collection = client.get_or_create_collection("project_knowledge")
            self.enabled = True
            log_success(f"✅ [SEM:init] ChromaDB فعال | items={self.collection.count()}")
        except ImportError:
            log_info("ℹ️ [SEM:init] chromadb نصب نیست — خاموش")
        except Exception as e:
            log_warning(f"⚠️ [SEM:init] {e}")

    def add(self, text: str, metadata: dict | None = None, doc_id: str | None = None):
        if not self.enabled:
            return
        try:
            doc_id = doc_id or f"doc_{datetime.now().timestamp()}"
            self.collection.add(
                documents=[text],
                metadatas=[metadata if metadata else {"_type": "general"}],
                ids=[doc_id],
            )
        except Exception as e:
            log_warning(f"⚠️ [SEM:add] {e}")

    def search(self, query: str, n: int = 5) -> list[str]:
        if not self.enabled or self.collection.count() == 0:
            return []
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(n, self.collection.count()),
            )
            docs = results.get("documents", [[]])[0]
            return docs
        except Exception as e:
            log_warning(f"⚠️ [SEM:search] {e}")
            return []

    def clear(self) -> None:
        """پاک کردن همهٔ آیتم‌های ChromaDB."""
        if not self.enabled or not self.collection:
            return
        try:
            ids = self.collection.get().get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
                log_info(f"🗑️ [SEM:clear] {len(ids)} آیتم پاک شد")
            else:
                log_info("ℹ️ [SEM:clear] چیزی برای پاک کردن نبود")
        except Exception as e:
            log_warning(f"⚠️ [SEM:clear] {e}")

    def count(self) -> int:
        """تعداد آیتم‌های ChromaDB."""
        if not self.enabled or not self.collection:
            return 0
        try:
            return self.collection.count()
        except Exception:
            return 0


# ══════════════════════════════════════════════════════════
# ۵. حافظهٔ پایدار (Persistent Memory) — SQLite + FTS5
# ══════════════════════════════════════════════════════════
class PersistentMemory:
    """حافظهٔ پایدار SQLite + FTS5 — هیچ‌وقت پاک نمی‌شود."""

    def __init__(self, db_path: str | None = None):
        # ─── lazy import (circular-safe) ───
        try:
            from core.brain.constants import (
                PERSISTENT_MEMORY_ENABLED,
                PERSISTENT_DB_PATH,
                PERSISTENT_MAX_ROWS,
            )
        except Exception:
            PERSISTENT_MEMORY_ENABLED = True
            PERSISTENT_DB_PATH = str(PERSISTENT_DB)
            PERSISTENT_MAX_ROWS = 100_000

        self.enabled = PERSISTENT_MEMORY_ENABLED
        self._lock = threading.RLock()

        if not self.enabled:
            self.db_path = ""
            self.max_rows = 0
            log_info("ℹ️ [PM:init] PersistentMemory غیرفعال")
            return

        # ─── مسیر DB ───
        raw = db_path or PERSISTENT_DB_PATH
        p = Path(raw) if raw else PERSISTENT_DB
        if not p.is_absolute():
            try:
                from data import paths as _dp
                p = Path(_dp.PROJECT_ROOT) / raw
            except Exception:
                p = Path.cwd() / raw

        self.db_path = str(p)
        self.max_rows = max(100, int(PERSISTENT_MAX_ROWS or 100_000))

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_warning(f"⚠️ [PM:mkdir] {e}")

        self._init_db()
        log_success(
            f"🧠 [PM:init] آماده | db={self.db_path} | "
            f"count={self.count()}"
        )

    def _init_db(self) -> None:
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
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

                    # FTS5 (اختیاری)
                    try:
                        conn.execute("""
                            CREATE VIRTUAL TABLE IF NOT EXISTS
                            memory_fts USING fts5(
                                key, value, category,
                                content='memory',
                                content_rowid='id'
                            )
                        """)
                        conn.execute("""
                            CREATE TRIGGER IF NOT EXISTS memory_ai
                            AFTER INSERT ON memory BEGIN
                                INSERT INTO memory_fts(
                                    rowid, key, value, category
                                ) VALUES (
                                    new.id, new.key,
                                    new.value, new.category
                                );
                            END
                        """)
                        conn.execute("""
                            CREATE TRIGGER IF NOT EXISTS memory_ad
                            AFTER DELETE ON memory BEGIN
                                INSERT INTO memory_fts(
                                    memory_fts, rowid, key,
                                    value, category
                                ) VALUES (
                                    'delete', old.id, old.key,
                                    old.value, old.category
                                );
                            END
                        """)
                    except sqlite3.OperationalError as e:
                        log_warning(f"⚠️ [PM:fts] FTS5 غیرفعال | {e}")

                    conn.commit()
            except Exception as e:
                log_error(f"❌ [PM:schema] {e}")
                raise

    def remember(
        self,
        category: str,
        key: str,
        value: str,
        importance: float = 0.5,
    ) -> int:
        if not self.enabled:
            return -1
        if not key or not value:
            return -1

        cat = str(category or "misc")[:100]
        k = str(key)[:500]
        v = str(value)[:10000]
        try:
            imp = max(0.0, min(1.0, float(importance)))
        except (TypeError, ValueError):
            imp = 0.5

        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
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
                self._maybe_cleanup(conn)
            log_info(
                f"💾 [PM:remember] {cat} | id={new_id} | "
                f"key={k[:60]!r}"
            )
            return new_id
        except Exception as e:
            log_error(f"❌ [PM:remember] {e}")
            return -1

    def _maybe_cleanup(self, conn) -> None:
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM memory"
            ).fetchone()[0]
            if total <= self.max_rows:
                return
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
            log_warning(f"🧹 [PM:cleanup] {remove_n} رکورد حذف شد")
        except Exception as e:
            log_warning(f"⚠️ [PM:cleanup] {e}")

    def recall(
        self,
        query: str = "",
        category: str = "",
        limit: int = 10,
    ) -> list[dict]:
        if not self.enabled:
            return []
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 10

        results: list[dict] = []

        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row

                    # FTS اول
                    if query and query.strip():
                        try:
                            sql = """
                                SELECT m.* FROM memory m
                                JOIN memory_fts f ON m.id = f.rowid
                                WHERE memory_fts MATCH ?
                            """
                            params: list = [query]
                            if category:
                                sql += " AND m.category = ?"
                                params.append(category)
                            sql += (
                                " ORDER BY m.importance DESC, "
                                "m.created_at DESC LIMIT ?"
                            )
                            params.append(limit)
                            rows = conn.execute(sql, params).fetchall()
                            results = [dict(r) for r in rows]
                        except Exception:
                            pass

                    # fallback LIKE
                    if not results:
                        sql = "SELECT * FROM memory WHERE 1=1"
                        params = []
                        if category:
                            sql += " AND category = ?"
                            params.append(category)
                        if query:
                            sql += " AND (key LIKE ? OR value LIKE ?)"
                            like = f"%{query}%"
                            params.extend([like, like])
                        sql += (
                            " ORDER BY importance DESC, "
                            "created_at DESC LIMIT ?"
                        )
                        params.append(limit)
                        rows = conn.execute(sql, params).fetchall()
                        results = [dict(r) for r in rows]

                    # آپدیت access
                    if results:
                        ids = [r["id"] for r in results]
                        ph = ",".join("?" * len(ids))
                        conn.execute(
                            f"""
                            UPDATE memory
                            SET access_count = access_count + 1,
                                accessed_at = CURRENT_TIMESTAMP
                            WHERE id IN ({ph})
                            """,
                            ids,
                        )
                        conn.commit()
            except Exception as e:
                log_error(f"❌ [PM:recall] {e}")

        return results

    def get_context(self, query: str = "", n: int = 5) -> str:
        results = self.recall(query=query, limit=n)
        if not results:
            return ""
        lines = ["# حافظهٔ پایدار مرتبط:"]
        for r in results:
            cat = r.get("category", "?")
            key = str(r.get("key", ""))[:100]
            val = str(r.get("value", ""))[:300]
            lines.append(f"\n[{cat}] {key}\n→ {val}")
        return "\n".join(lines)

    def count(self, category: str = "") -> int:
        if not self.enabled:
            return 0
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    if category:
                        return conn.execute(
                            "SELECT COUNT(*) FROM memory WHERE category = ?",
                            (category,),
                        ).fetchone()[0]
                    return conn.execute(
                        "SELECT COUNT(*) FROM memory"
                    ).fetchone()[0]
            except Exception:
                return 0

    # ═══════════════════════════════════════════
    # 🆕 Memory Decay (فراموشی تدریجی)
    # ═══════════════════════════════════════════
    def decay(
        self,
        factor: float = 0.95,
        days_per_cycle: int = 7,
        min_importance: float = 0.2,
        delete_after_days: int = 30,
    ) -> dict:
        """فراموشی تدریجی — importance رو کم کن.

        Args:
            factor: ضریب کاهش هر سیکل (0.95 = ۵٪).
            days_per_cycle: هر چند روز یک بار اعمال بشه.
            min_importance: اگه کم‌تر شد، حذف کن.
            delete_after_days: چند روز بعد حذف کن.

        Returns:
            dict با updated و deleted.
        """
        if not self.enabled:
            return {"updated": 0, "deleted": 0}

        try:
            with self._lock:
                with sqlite3.connect(self.db_path) as conn:
                    # ─── ۱) کاهش importance ───
                    cur1 = conn.execute(
                        """
                        UPDATE memory
                        SET importance = importance * ?
                        WHERE julianday('now')
                              - julianday(created_at) > ?
                          AND (access_count = 0
                               OR access_count IS NULL)
                        """,
                        (factor, days_per_cycle),
                    )
                    updated = cur1.rowcount or 0

                    # ─── ۲) حذف موارد کم‌اهمیت ───
                    cur2 = conn.execute(
                        """
                        DELETE FROM memory
                        WHERE importance < ?
                          AND julianday('now')
                              - julianday(created_at) > ?
                          AND (access_count = 0
                               OR access_count IS NULL)
                        """,
                        (min_importance, delete_after_days),
                    )
                    deleted = cur2.rowcount or 0

                    conn.commit()

            if updated > 0 or deleted > 0:
                log_success(
                    f"🧹 [PM:decay] updated={updated} | "
                    f"deleted={deleted}"
                )
            else:
                log_info(
                    "🧹 [PM:decay] چیزی تغییر نکرد | "
                    "updated=0 | deleted=0"
                )

            return {"updated": updated, "deleted": deleted}
        except Exception as e:
            log_error(
                f"❌ [PM:decay] {type(e).__name__}: {e}"
            )
            return {"updated": 0, "deleted": 0}

    def get_decay_preview(
        self,
        factor: float = 0.95,
        days_per_cycle: int = 7,
        min_importance: float = 0.2,
        delete_after_days: int = 30,
    ) -> dict:
        """پیش‌نمایش Memory Decay — بدون تغییر واقعی.

        Returns:
            dict با count_updated و count_deleted.
        """
        if not self.enabled:
            return {"count_updated": 0, "count_deleted": 0}

        try:
            with self._lock:
                with sqlite3.connect(self.db_path) as conn:
                    # ─── چک updated ───
                    row1 = conn.execute(
                        """
                        SELECT COUNT(*) FROM memory
                        WHERE julianday('now')
                              - julianday(created_at) > ?
                          AND (access_count = 0
                               OR access_count IS NULL)
                        """,
                        (days_per_cycle,),
                    ).fetchone()
                    count_updated = row1[0] if row1 else 0

                    # ─── چک deleted ───
                    row2 = conn.execute(
                        """
                        SELECT COUNT(*) FROM memory
                        WHERE importance < ?
                          AND julianday('now')
                              - julianday(created_at) > ?
                          AND (access_count = 0
                               OR access_count IS NULL)
                        """,
                        (min_importance, delete_after_days),
                    ).fetchone()
                    count_deleted = row2[0] if row2 else 0

            return {
                "count_updated": count_updated,
                "count_deleted": count_deleted,
            }
        except Exception as e:
            log_warning(
                f"⚠️ [PM:decay_preview] "
                f"{type(e).__name__}: {e}"
            )
            return {"count_updated": 0, "count_deleted": 0}

    def forget(
        self, days: int = 30, min_importance: float = 0.2
    ) -> int:
        if not self.enabled:
            return 0
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
                          AND (access_count = 0 OR access_count IS NULL)
                        """,
                        (imp, days),
                    )
                    conn.commit()
                    n = cur.rowcount or 0
                if n > 0:
                    log_success(f"🧹 [PM:forget] {n} رکورد")
                return n
            except Exception as e:
                log_warning(f"⚠️ [PM:forget] {e}")
                return 0

    def clear(self, category: str = "") -> int:
        if not self.enabled:
            return 0
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    if category:
                        cur = conn.execute(
                            "DELETE FROM memory WHERE category = ?",
                            (category,),
                        )
                    else:
                        cur = conn.execute("DELETE FROM memory")
                    conn.commit()
                    n = cur.rowcount or 0
                log_warning(f"🗑️ [PM:clear] {n} رکورد")
                return n
            except Exception as e:
                log_error(f"❌ [PM:clear] {e}")
                return 0

    def get_stats(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "total": 0}
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    total = conn.execute(
                        "SELECT COUNT(*) as n FROM memory"
                    ).fetchone()["n"]
                    by_cat = conn.execute("""
                        SELECT category, COUNT(*) as n
                        FROM memory GROUP BY category
                        ORDER BY n DESC LIMIT 20
                    """).fetchall()
                    try:
                        size = os.path.getsize(self.db_path)
                    except Exception:
                        size = 0
                    return {
                        "enabled": True,
                        "total": total,
                        "by_category": {
                            r["category"]: r["n"] for r in by_cat
                        },
                        "db_path": self.db_path,
                        "size_kb": round(size / 1024, 1),
                        "max_rows": self.max_rows,
                    }
            except Exception as e:
                return {"enabled": True, "error": str(e), "total": 0}

    def close(self) -> None:
        pass


# ══════════════════════════════════════════════════════════
# Singleton PersistentMemory
# ══════════════════════════════════════════════════════════
_shared_persistent: PersistentMemory | None = None
_persistent_lock = threading.Lock()


def get_persistent_memory() -> PersistentMemory | None:
    """Singleton PersistentMemory."""
    global _shared_persistent
    try:
        from core.brain.constants import PERSISTENT_MEMORY_ENABLED
    except Exception:
        PERSISTENT_MEMORY_ENABLED = True

    if not PERSISTENT_MEMORY_ENABLED:
        return None
    if _shared_persistent is None:
        with _persistent_lock:
            if _shared_persistent is None:
                try:
                    _shared_persistent = PersistentMemory()
                except Exception as e:
                    log_error(f"❌ [PM:singleton] {e}")
                    return None
    return _shared_persistent


def reset_persistent_memory() -> None:
    global _shared_persistent
    with _persistent_lock:
        _shared_persistent = None


# ══════════════════════════════════════════════════════════
# ۶. مغز کامل (BrainMemory)
# ══════════════════════════════════════════════════════════
class BrainMemory:
    """مغز کامل — ۵ حافظه.

    API دوگانه:
        ─── اصلی (brain_memory) ───
        - remember(question, answer, importance)
        - recall(query) -> str | None
        - build_context(query) -> str
        - mark_task_done / is_task_done / add_lesson

        ─── سازگاری (memory.py) ───
        - add(role, content)
        - get_context(query) -> list[dict]
        - search(query, n) -> list[dict]
        - clear() / get_stats()
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        use_api: bool = False,
        api_url: str = "",
        api_key: str = "",
    ):
        # lazy import
        if max_tokens is None:
            try:
                from core.brain.constants import MAX_CONTEXT_TOKENS
                max_tokens = MAX_CONTEXT_TOKENS
            except Exception:
                max_tokens = 128_000

        log_info("🧠 [BM:init] شروع...")
        BASE_DIR.mkdir(parents=True, exist_ok=True)

        # ─── ۵ حافظه ───
        self.working = WorkingMemory()
        self.long_term = LongTermMemory()
        self.procedural = ProceduralMemory()
        self.semantic = SemanticMemory()
        self.persistent: PersistentMemory | None = get_persistent_memory()

        # ─── تنظیمات ───
        self.max_tokens = max_tokens
        self.use_api = use_api
        self.api_url = api_url
        self.api_key = api_key
        self._api_memory = None
        # 🆕 Hybrid Search (lazy)
        self.hybrid_search = None

        # ─── stats ───
        stats = self.long_term.get_stats()
        p_count = (
            self.persistent.count()
            if self.persistent and self.persistent.enabled
            else 0
        )
        log_success(
            f"✅ [BM:init] آماده | "
            f"working={len(self.working.items)} | "
            f"knowledge={stats['knowledge']} | "
            f"lessons={stats['lessons']} | "
            f"tasks={len(self.procedural.tasks_done)} | "
            f"semantic={'✅' if self.semantic.enabled else '❌'} | "
            f"persistent={p_count}"
        )

    # ═══════════════════════════════════════════
    # Properties سازگاری
    # ═══════════════════════════════════════════
    @property
    def short_term(self) -> list[dict]:
        return [
            {
                "role": "user",
                "content": item.get("q", ""),
                "timestamp": item.get("t", 0),
            }
            for item in self.working.items
        ]

    @property
    def summaries(self) -> list[str]:
        try:
            rows = self.long_term.get_category("conversation")
            return [
                f"[{r['key'][:80]}] {r['value'][:200]}"
                for r in rows[:5]
            ]
        except Exception:
            return []

    @property
    def collection(self):
        if self.semantic.enabled and self.semantic.collection:
            return self.semantic.collection
        return None

    @property
    def api_memory(self):
        return self._api_memory

    # ═══════════════════════════════════════════
    # API اصلی
    # ═══════════════════════════════════════════
    def remember(
        self, question: str, answer: str, importance: float = 0.5
    ) -> None:
        """ذخیره در همه حافظه‌ها."""
        # ۱. کوتاه‌مدت
        self.working.add(question, answer, importance)

        # ۲. بلندمدت (اگر مهم)
        if importance > 0.7:
            self.long_term.add(
                category="conversation",
                key=question[:200],
                value=answer[:1000],
                importance=importance,
            )

        # ۳. معنایی
        self.semantic.add(
            text=f"Q: {question}\nA: {answer[:500]}",
            metadata={
                "type": "conversation",
                "importance": importance,
            },
        )

        # ۴. پایدار
        if self.persistent and self.persistent.enabled:
            try:
                self.persistent.remember(
                    category="conversation",
                    key=question[:200],
                    value=answer[:1000],
                    importance=importance,
                )
            except Exception as e:
                log_warning(f"⚠️ [BM:remember:persistent] {e}")

    def recall(self, query: str) -> str | None:
        """بازیابی — short → persistent → semantic."""
        item = self.working.get_by_key(query)
        if item:
            return item["a"]

        if self.persistent and self.persistent.enabled:
            try:
                results = self.persistent.recall(query=query, limit=1)
                if results:
                    return results[0].get("value", "")[:1000]
            except Exception:
                pass

        results = self.semantic.search(query, n=1)
        if results:
            return results[0]

        return None

    def is_task_done(
        self, task: str, within_hours: int = 24
    ) -> bool:
        return self.procedural.is_done(task, within_hours)

    def mark_task_done(self, task: str) -> None:
        self.procedural.mark_done(task)

    def add_lesson(self, wrong: str, correct: str) -> None:
        self.long_term.add_lesson(wrong, correct)

    def build_context(self, query: str) -> str:
        """ساخت context برای AI."""
        parts = []

        # ۱. گفتگوی اخیر
        recent = self.working.get_recent(5)
        if recent:
            parts.append("## گفتگوی اخیر:")
            for item in recent:
                parts.append(f"- س: {item['q'][:100]}")
                parts.append(f"  ج: {item['a'][:150]}")

        # ۲. معنایی
        semantic = self.semantic.search(query, n=3)
        if semantic:
            parts.append("\n## دانش پروژه:")
            for s in semantic:
                parts.append(f"- {s[:200]}")

        # ۳. پایدار
        if self.persistent and self.persistent.enabled:
            try:
                p_ctx = self.persistent.get_context(query, n=3)
                if p_ctx:
                    parts.append("\n" + p_ctx)
            except Exception:
                pass

        # ۴. دروس
        lessons = self.long_term.search_lessons(query)
        if lessons:
            parts.append("\n## یادگیری‌ها:")
            for item in lessons:
                parts.append(
                    f"- قبلاً: {item['wrong'][:80]} → "
                    f"درست: {item['correct'][:80]}"
                )

        # ۵. الگوها
        patterns = self.procedural.get_top_patterns(3)
        if patterns:
            parts.append("\n## الگوهای کاربر:")
            for p, c in patterns:
                parts.append(f"- {p} (×{c})")

        # ۶. تجربه‌های پلتفرم
        try:
            from core.brain.experience_learner import get_experience
            exp = get_experience()
            exp_ctx = exp.build_context(limit=3)
            if exp_ctx:
                parts.append("\n" + exp_ctx)
        except Exception:
            pass

        return "\n".join(parts) if parts else ""

    # ═══════════════════════════════════════════
    # API سازگاری (memory.py)
    # ═══════════════════════════════════════════
    def add(self, role: str, content: str) -> None:
        """سازگاری با memory.py.add()."""
        if not content:
            return
        try:
            self.working.add(
                question=f"[{role}]",
                answer=str(content)[:1000],
                importance=0.5,
            )
        except Exception as e:
            log_warning(f"⚠️ [BM:add] {e}")

    async def get_context(self, query: str = "") -> list[dict]:
        """سازگاری با memory.py.get_context()."""
        context: list[dict] = []
        tokens = 0

        try:
            from core.brain.constants import CONTEXT_RECENT
        except Exception:
            CONTEXT_RECENT = 10

        for item in self.working.items[-CONTEXT_RECENT:]:
            context.append({
                "role": "user",
                "content": item.get("q", ""),
            })
            tokens += len(item.get("q", "")) // 4

        # semantic
        if query:
            results = self.semantic.search(
                query, n=CONTEXT_RECENT // 2
            )
            for doc in results:
                if tokens + len(doc) // 4 > self.max_tokens:
                    break
                context.append({
                    "role": "system",
                    "content": f"[حافظه مرتبط] {doc}",
                })
                tokens += len(doc) // 4

        # persistent
        if query and self.persistent and self.persistent.enabled:
            try:
                p_ctx = self.persistent.get_context(query, n=3)
                if p_ctx:
                    context.append({
                        "role": "system",
                        "content": p_ctx,
                    })
            except Exception:
                pass

        return context

    async def search(
        self, query: str, n: int = 5
    ) -> list[dict]:
        """سازگاری — جستجوی ترکیبی."""
        results: list[dict] = []

        docs = self.semantic.search(query, n=n)
        for doc in docs:
            results.append({
                "role": "memory",
                "content": str(doc)[:300],
            })

        if self.persistent and self.persistent.enabled:
            try:
                p = self.persistent.recall(query=query, limit=n)
                for r in p:
                    results.append({
                        "role": r.get("category", "memory"),
                        "content": f"{r.get('key', '')}: "
                                   f"{r.get('value', '')[:200]}",
                    })
            except Exception:
                pass

        return results[:n]

    def clear(self) -> None:
        """پاک کردن همه حافظه‌ها."""
        self.working.clear()
        # ✅ FIX: استفاده از if به جای ternary به‌عنوان statement
        if hasattr(self.semantic, "clear"):
            self.semantic.clear()
        log_info("🗑️ [BM:clear] حافظه پاک شد")

    def get_stats(self) -> dict:
        """آمار کامل."""
        result = {
            "working": len(self.working.items),
            "long_term": self.long_term.get_stats(),
            "procedural": {
                "tasks": len(self.procedural.tasks_done),
                "patterns": len(self.procedural.patterns),
            },
            "semantic": {
                "enabled": self.semantic.enabled,
                "count": (
                    self.semantic.collection.count()
                    if self.semantic.enabled
                    and self.semantic.collection
                    else 0
                ),
            },
        }

        if self.persistent and self.persistent.enabled:
            result["persistent"] = self.persistent.get_stats()
        else:
            result["persistent"] = {"enabled": False, "total": 0}

        return result

    # ═══════════════════════════════════════════
    # 🆕 Hybrid Search (FTS + Semantic)
    # ═══════════════════════════════════════════
    def hybrid_recall(
        self, query: str, n: int = 10
    ) -> list[dict]:
        """جستجوی ترکیبی (FTS + Semantic با RRF).

        Args:
            query: متن جستجو.
            n: تعداد نتایج.

        Returns:
            list[dict] با id، content، score، source.
        """
        if self.hybrid_search is None:
            self.hybrid_search = HybridSearch(self)
        return self.hybrid_search.search(query, n=n)


# ══════════════════════════════════════════════════════════
# Singleton BrainMemory
# ══════════════════════════════════════════════════════════
_shared_bm: BrainMemory | None = None
_shared_lock = threading.Lock()


def get_brain_memory() -> BrainMemory:
    """دریافت Singleton."""
    global _shared_bm
    if _shared_bm is None:
        with _shared_lock:
            if _shared_bm is None:
                _shared_bm = BrainMemory()
    return _shared_bm


def reset_brain_memory() -> None:
    """پاک کردن Singleton."""
    global _shared_bm
    with _shared_lock:
        _shared_bm = None
        log_info("🗑️ [BM:singleton] reset")

# ══════════════════════════════════════════════════════════
# 🆕 Hybrid Search (FTS + Semantic با RRF)
# ══════════════════════════════════════════════════════════


class HybridSearch:
    """جستجوی ترکیبی: FTS + Semantic.

    با Reciprocal Rank Fusion (RRF) نتایج ترکیب می‌شن.
    RRF = 1 / (k + rank)  که k یه ثابت (پیش‌فرض 60) هست.

    Features:
        - search(query, n) → list[dict]
        - هر نتیجه شامل source (fts/semantic/both)

    Usage:
        hs = HybridSearch(brain_memory_instance)
        results = hs.search("سیستم", n=10)
    """

    def __init__(self, brain_memory=None):
        """سازنده.

        Args:
            brain_memory: نمونهٔ BrainMemory (اختیاری).
        """
        self._bm = brain_memory
        log_info("🔍 [HYBRID:init] آماده")

    def _get_bm(self):
        """دریافت BrainMemory (lazy)."""
        if self._bm is None:
            try:
                self._bm = get_brain_memory()
            except Exception as e:
                log_warning(f"⚠️ [HYBRID:get_bm] {e}")
        return self._bm

    def search(
        self,
        query: str,
        n: int = 10,
        k_rrf: int = 60,
    ) -> list[dict]:
        """جستجوی ترکیبی.

        Args:
            query: متن جستجو.
            n: تعداد نتایج نهایی.
            k_rrf: ثابت RRF (پیش‌فرض 60).

        Returns:
            list[dict] با rank، source، content، score.
        """
        if not query or not query.strip():
            return []

        bm = self._get_bm()
        if bm is None:
            return []

        # ─── ۱) FTS results (PersistentMemory) ───
        fts_results: list[dict] = []
        pm = getattr(bm, "persistent", None)
        if pm and pm.enabled:
            try:
                fts_raw = pm.recall(query=query, limit=n * 2)
                for i, r in enumerate(fts_raw):
                    fts_results.append({
                        "id": f"fts_{r.get('id', i)}",
                        "content": (
                            f"[{r.get('category', '?')}] "
                            f"{r.get('key', '')[:100]}: "
                            f"{r.get('value', '')[:200]}"
                        ),
                        "rank": i + 1,
                    })
            except Exception as e:
                log_warning(f"⚠️ [HYBRID:fts] {e}")

        # ─── ۲) Semantic results (ChromaDB) ───
        sem_results: list[dict] = []
        sem = getattr(bm, "semantic", None)
        if sem and sem.enabled:
            try:
                sem_raw = sem.search(query, n=n * 2)
                for i, doc in enumerate(sem_raw):
                    sem_results.append({
                        "id": f"sem_{i}",
                        "content": str(doc)[:300],
                        "rank": i + 1,
                    })
            except Exception as e:
                log_warning(f"⚠️ [HYBRID:sem] {e}")

        # ─── ۳) ترکیب با RRF ───
        scores: dict[str, dict] = {}

        # FTS
        for r in fts_results:
            rid = r["id"]
            if rid not in scores:
                scores[rid] = {
                    "content": r["content"],
                    "rrf": 0.0,
                    "sources": set(),
                }
            scores[rid]["rrf"] += 1.0 / (k_rrf + r["rank"])
            scores[rid]["sources"].add("fts")

        # Semantic
        for r in sem_results:
            rid = r["id"]
            if rid not in scores:
                scores[rid] = {
                    "content": r["content"],
                    "rrf": 0.0,
                    "sources": set(),
                }
            scores[rid]["rrf"] += 1.0 / (k_rrf + r["rank"])
            scores[rid]["sources"].add("semantic")

        # ─── ۴) مرتب‌سازی ───
        sorted_items = sorted(
            scores.items(),
            key=lambda x: -x[1]["rrf"],
        )

        # ─── ۵) خروجی ───
        results: list[dict] = []
        for rid, data in sorted_items[:n]:
            sources = data["sources"]
            if len(sources) == 2:
                source = "both"
            else:
                source = list(sources)[0]

            results.append({
                "id": rid,
                "content": data["content"],
                "score": round(data["rrf"], 4),
                "source": source,
            })

        log_info(
            f"🔍 [HYBRID:search] q={query[:40]!r} | "
            f"fts={len(fts_results)} | "
            f"sem={len(sem_results)} | "
            f"final={len(results)}"
        )
        return results


__all__ = [
    "BrainMemory",
    "WorkingMemory",
    "LongTermMemory",
    "ProceduralMemory",
    "SemanticMemory",
    "PersistentMemory",
    "get_brain_memory",
    "reset_brain_memory",
    "get_persistent_memory",
    "reset_persistent_memory",
    "HybridSearch",
]
