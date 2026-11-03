"""MemoryStore — حافظهٔ SQLite با Bitemporal + FTS + Tags.

نسخهٔ ۳ (رفع ۱۰ باگ):
  - Bitemporal: valid_from/valid_to + recorded_at
  - Full-text search (FTS5)
  - Tags (برای دسته‌بندی)
  - Importance (0-1) برای Forget
  - Immutable archive (facts_archive)
  - Graph relations (memory_relations)
  - FIX 2: db_path در __new__ → ValueError اگر متفاوت
  - FIX 3: IN () خالی → guard
  - FIX 6: UPDATE access_count → فقط اگر query غیرخالی
  - FIX 7: add → استخراج _archive_and_insert (کوتاه‌تر)
  - FIX 8: db_path در get_stats → حذف
  - FIX 9: ۳ round-trip → ۲ کوئری
  - FIX 10: مستندسازی دو lock
  - FIX 12: newline انتهای فایل
  - FIX 13: forget → row_factory + row["id"] (به‌جای row[0])
  - FIX 14: query → except sqlite3.Error (به‌جای OperationalError)
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from core.kernel.logger import log_error, log_info, log_warning
from data import paths as data_paths


class MemoryStore:
    """حافظهٔ SQLite با Bitemporal + FTS + Relations.

    Lockها:
      - `_lock` (class-level, RLock): فقط برای ساخت singleton در __new__.
      - `_write_lock` (instance-level, Lock): برای عملیات نوشتن (add/forget).
    """

    _instance: MemoryStore | None = None
    _lock = threading.RLock()

    def __new__(cls, db_path: str | None = None):
        # 🆕 FIX 2: اگر db_path داده شد و با instance قبلی متفاوت بود → ValueError
        if cls._instance is not None and db_path is not None:
            if cls._instance.db_path != db_path:
                raise ValueError(
                    f"MemoryStore singleton already initialized with "
                    f"db_path={cls._instance.db_path!r}, "
                    f"cannot use {db_path!r}"
                )
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialized = False
                    cls._instance = obj
        return cls._instance

    def __init__(self, db_path: str | None = None):
        if getattr(self, "_initialized", False):
            return

        # ─── مسیر DB ───
        if db_path is None:
            db_path = str(data_paths.STORAGE_DIR / "memory" / "memory.db")
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # ─── lock برای نوشتن (توضیح در docstring کلاس) ───
        self._write_lock = threading.Lock()

        # 🆕 FIX 1: _init_schema اول، بعد _initialized
        self._init_schema()
        self._initialized = True
        log_info(f"🧠 [MEMORY:init] آماده | db={db_path}")

    # ═══════════════════════════════════════════
    # Schema
    # ═══════════════════════════════════════════
    def _init_schema(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executescript("""
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = NORMAL;

                    CREATE TABLE IF NOT EXISTS memory_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        importance REAL DEFAULT 0.5,
                        valid_from TEXT DEFAULT CURRENT_TIMESTAMP,
                        valid_to TEXT,
                        recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        access_count INTEGER DEFAULT 0,
                        last_accessed TEXT,
                        tags TEXT,
                        source TEXT,
                        rationale TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_items_category
                        ON memory_items(category);
                    CREATE INDEX IF NOT EXISTS idx_items_key
                        ON memory_items(key);
                    CREATE INDEX IF NOT EXISTS idx_items_valid
                        ON memory_items(valid_from, valid_to);

                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                    USING fts5(
                        key, value, tags,
                        content='memory_items',
                        content_rowid='id'
                    );

                    CREATE TRIGGER IF NOT EXISTS memory_ai
                    AFTER INSERT ON memory_items BEGIN
                        INSERT INTO memory_fts(rowid, key, value, tags)
                        VALUES (new.id, new.key, new.value, new.tags);
                    END;

                    CREATE TRIGGER IF NOT EXISTS memory_ad
                    AFTER DELETE ON memory_items BEGIN
                        INSERT INTO memory_fts(memory_fts, rowid, key, value, tags)
                        VALUES ('delete', old.id, old.key, old.value, old.tags);
                    END;

                    CREATE TABLE IF NOT EXISTS memory_relations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        from_id INTEGER,
                        to_id INTEGER,
                        relation_type TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (from_id) REFERENCES memory_items(id),
                        FOREIGN KEY (to_id) REFERENCES memory_items(id)
                    );

                    CREATE TABLE IF NOT EXISTS facts_archive (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        original_id INTEGER,
                        category TEXT,
                        key TEXT,
                        value TEXT,
                        archived_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        reason TEXT
                    );
                """)
                conn.commit()
        except sqlite3.Error as e:
            log_warning(
                f"⚠️ [MEMORY:schema] {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise

    # ═══════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════
    def _find_similar(self, category: str, key: str) -> list[dict]:
        """پیدا کردن facts مشابه که هنوز معتبرن."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM memory_items
                WHERE category = ?
                  AND key = ?
                  AND valid_to IS NULL""",
                (category, key),
            ).fetchall()
            return [dict(r) for r in rows]

    def _archive_and_insert(
        self,
        conn,
        category: str,
        key: str,
        value: str,
        importance: float,
        tags_str: str,
        source: str,
        rationale: str,
        existing: list[dict],
    ) -> int:
        """🆕 FIX 7: آرشیو قدیمی‌ها + درج جدید + relation (یک تابع)."""
        # ─── آرشیو قدیمی‌ها ───
        if existing:
            old_ids = [old["id"] for old in existing]

            conn.executemany(
                "UPDATE memory_items SET valid_to = CURRENT_TIMESTAMP WHERE id = ?", [
                    (oid,) for oid in old_ids], )

            conn.executemany(
                "INSERT INTO facts_archive "
                "(original_id, category, key, value, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        old["id"],
                        old["category"],
                        old["key"],
                        old["value"],
                        "superseded_by_new",
                    )
                    for old in existing
                ],
            )

        # ─── درج جدید ───
        cur = conn.execute(
            """INSERT INTO memory_items
            (category, key, value, importance, tags, source, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (category, key, value, importance, tags_str, source, rationale),
        )
        new_id = cur.lastrowid

        # ─── relation با قدیمی‌ها ───
        if existing:
            conn.executemany(
                "INSERT INTO memory_relations "
                "(from_id, to_id, relation_type) "
                "VALUES (?, ?, 'supersedes')",
                [(new_id, old["id"]) for old in existing],
            )

        return new_id

    # ═══════════════════════════════════════════
    # Add (با contradiction check)
    # ═══════════════════════════════════════════
    def add(
        self,
        category: str,
        key: str,
        value: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        source: str = "system",
        rationale: str = "",
    ) -> int:
        """افزودن یه fact جدید — با contradiction check.

        Returns:
            id رکورد جدید یا -1.
        """
        if not key or not value:
            return -1

        importance = max(0.0, min(1.0, float(importance)))
        tags_str = ",".join(tags or [])

        with self._write_lock:
            existing = self._find_similar(category, key)

            # ─── اگر value یکسان بود: id قدیمی را برگردان ───
            for old in existing:
                if old["value"] == value:
                    log_info(f"💾 [MEMORY:add] value یکسان | id={old['id']}")
                    return old["id"]

            # ─── درج + آرشیو ───
            with sqlite3.connect(self.db_path) as conn:
                new_id = self._archive_and_insert(
                    conn,
                    category=category,
                    key=key,
                    value=value,
                    importance=importance,
                    tags_str=tags_str,
                    source=source,
                    rationale=rationale,
                    existing=existing,
                )
                conn.commit()

            log_info(
                f"💾 [MEMORY:add] {category} | id={new_id} | "
                f"key={key[:50]!r} | superseded={len(existing)}"
            )
            return new_id

    # ═══════════════════════════════════════════
    # Query (FTS + fallback)
    # ═══════════════════════════════════════════
    def query(
        self,
        category: str = "",
        query: str = "",
        limit: int = 5,
    ) -> list[dict]:
        """جستجو — اول FTS، بعد LIKE."""
        limit = max(1, min(limit, 100))
        results: list[dict] = []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # ─── FTS اول ───
            if query and query.strip():
                try:
                    sql = """
                        SELECT m.* FROM memory_items m
                        JOIN memory_fts f ON m.id = f.rowid
                        WHERE f.memory_fts MATCH ?
                          AND m.valid_to IS NULL
                    """
                    params: list[Any] = [query]
                    if category:
                        sql += " AND m.category = ?"
                        params.append(category)
                    sql += " ORDER BY m.importance DESC LIMIT ?"
                    params.append(limit)

                    rows = conn.execute(sql, params).fetchall()
                    results = [dict(r) for r in rows]
                except sqlite3.Error as e:
                    # 🆕 FIX 14: sqlite3.Error (به‌جای OperationalError)
                    log_warning(
                        f"⚠️ [MEMORY:fts] {type(e).__name__}: {e}",
                        exc_info=True,
                    )

            # ─── fallback: LIKE ───
            if not results:
                sql = "SELECT * FROM memory_items WHERE valid_to IS NULL"
                params = []
                if category:
                    sql += " AND category = ?"
                    params.append(category)
                if query:
                    sql += " AND (key LIKE ? OR value LIKE ?)"
                    like = f"%{query}%"
                    params.extend([like, like])
                sql += " ORDER BY importance DESC, recorded_at DESC LIMIT ?"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()
                results = [dict(r) for r in rows]

            # 🆕 FIX 3: آپدیت access_count — فقط اگر results غیرخالی و ids معتبر
            if results:
                ids = [r["id"] for r in results]
                if ids:
                    ph = ",".join("?" * len(ids))
                    conn.execute(
                        f"""UPDATE memory_items
                        SET access_count = access_count + 1,
                            last_accessed = CURRENT_TIMESTAMP
                        WHERE id IN ({ph})""",
                        ids,
                    )
                    conn.commit()

        return results

    # ═══════════════════════════════════════════
    # Forget (importance decay)
    # ═══════════════════════════════════════════
    def forget(self, days: int = 30, min_importance: float = 0.2) -> int:
        """فراموشی — رکوردهای کم‌اهمیت که مدتی استفاده نشدن."""
        with self._write_lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # ─── پیدا کردن رکوردهای قابل فراموشی ───
            rows = conn.execute(
                """SELECT * FROM memory_items
                    WHERE valid_to IS NULL
                      AND importance < ?
                      AND (
                          last_accessed IS NULL
                          OR julianday('now') - julianday(last_accessed) > ?
                      )""",
                (min_importance, days),
            ).fetchall()

            if not rows:
                return 0

            # 🆕 FIX 13: به archive منتقل کن (با row["id"] به‌جای row[0])
            conn.executemany(
                "INSERT INTO facts_archive "
                "(original_id, category, key, value, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        row["id"],
                        row["category"],
                        row["key"],
                        row["value"],
                        "forgotten_by_decay",
                    )
                    for row in rows
                ],
            )

            # ─── valid_to بزن ───
            # 🆕 FIX 13: row["id"] به‌جای row[0]
            ids = [row["id"] for row in rows]
            if ids:
                ph = ",".join("?" * len(ids))
                conn.execute(
                    f"""UPDATE memory_items
                        SET valid_to = CURRENT_TIMESTAMP
                        WHERE id IN ({ph})""",
                    ids,
                )

            conn.commit()
            n = len(ids)
            if n:
                log_info(f"🧹 [MEMORY:forget] {n} رکورد فراموش شد (archive شد)")
            return n

    # ═══════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🆕 FIX 8+9: db_path حذف شد + ۲ کوئری (به‌جای ۳)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # ۱ کوئری برای total_active + archived
            row = conn.execute(
                """SELECT
                    (SELECT COUNT(*) FROM memory_items WHERE valid_to IS NULL) as total_active,
                    (SELECT COUNT(*) FROM facts_archive) as archived"""
            ).fetchone()

            total_active = row["total_active"] if row else 0
            archived = row["archived"] if row else 0

            # ۱ کوئری برای by_category
            by_cat = conn.execute(
                """SELECT category, COUNT(*) as n
                FROM memory_items WHERE valid_to IS NULL
                GROUP BY category"""
            ).fetchall()

            return {
                "total_active": total_active,
                "archived": archived,
                "by_category": {r["category"]: r["n"] for r in by_cat},
            }
