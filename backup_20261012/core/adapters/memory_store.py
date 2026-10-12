"""MemoryStore — حافظهٔ SQLite با Bitemporal + FTS + Tags.

نسخهٔ ۱ (جدید) — استاندارد ۲۰۲۶:
  - Bitemporal: valid_from/valid_to + recorded_at
  - Full-text search (FTS5)
  - Tags (برای دسته‌بندی)
  - Importance (0-1) برای Forget
  - Immutable archive (facts_archive)
  - Graph relations (memory_relations)
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from core.kernel.logger import log_info, log_warning
from data import paths as data_paths


class MemoryStore:
    """حافظهٔ SQLite با Bitemporal + FTS + Relations."""

    _instance: "MemoryStore | None" = None
    _lock = threading.RLock()

    def __new__(cls, db_path: str | None = None):
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
        self._initialized = True

        # ─── مسیر DB ───
        if db_path is None:
            db_path = str(
                data_paths.STORAGE_DIR / "memory" / "memory.db"
            )
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # ─── lock برای نوشتن ───
        self._write_lock = threading.Lock()

        self._init_schema()
        log_info(f"🧠 [MEMORY:init] آماده | db={db_path}")

    # ═══════════════════════════════════════════
    # Schema
    # ═══════════════════════════════════════════
    def _init_schema(self):
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
            # ─── چک contradiction ───
            existing = self._find_similar(category, key)

            with sqlite3.connect(self.db_path) as conn:
                # ─── اگه مشابه بود: آرشیو کن قدیمی رو ───
                for old in existing:
                    conn.execute(
                        "UPDATE memory_items "
                        "SET valid_to = CURRENT_TIMESTAMP "
                        "WHERE id = ?",
                        (old["id"],),
                    )
                    conn.execute(
                        "INSERT INTO facts_archive "
                        "(original_id, category, key, value, reason) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            old["id"],
                            old["category"],
                            old["key"],
                            old["value"],
                            "superseded_by_new",
                        ),
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
                for old in existing:
                    conn.execute(
                        "INSERT INTO memory_relations "
                        "(from_id, to_id, relation_type) "
                        "VALUES (?, ?, 'supersedes')",
                        (new_id, old["id"]),
                    )

                conn.commit()

            log_info(
                f"💾 [MEMORY:add] {category} | id={new_id} | "
                f"key={key[:50]!r} | superseded={len(existing)}"
            )
            return new_id

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
                except Exception as e:
                    log_warning(f"⚠️ [MEMORY:fts] {e}")

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

            # ─── آپدیت access_count ───
            if results:
                ids = [r["id"] for r in results]
                conn.execute(
                    f"""UPDATE memory_items
                    SET access_count = access_count + 1,
                        last_accessed = CURRENT_TIMESTAMP
                    WHERE id IN ({','.join('?' * len(ids))})""",
                    ids,
                )
                conn.commit()

        return results

    # ═══════════════════════════════════════════
    # Forget (importance decay)
    # ═══════════════════════════════════════════
    def forget(self, days: int = 30, min_importance: float = 0.2):
        """فراموشی — رکوردهای کم‌اهمیت که مدتی استفاده نشدن."""
        with self._write_lock:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    """UPDATE memory_items
                    SET valid_to = CURRENT_TIMESTAMP
                    WHERE valid_to IS NULL
                      AND importance < ?
                      AND (
                          last_accessed IS NULL
                          OR julianday('now') - julianday(last_accessed) > ?
                      )""",
                    (min_importance, days),
                )
                conn.commit()
                n = cur.rowcount
                if n:
                    log_info(f"🧹 [MEMORY:forget] {n} رکورد فراموش شد")
                return n

    # ═══════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                "SELECT COUNT(*) as n FROM memory_items WHERE valid_to IS NULL"
            ).fetchone()["n"]
            archived = conn.execute(
                "SELECT COUNT(*) as n FROM facts_archive"
            ).fetchone()["n"]
            by_cat = conn.execute(
                """SELECT category, COUNT(*) as n
                FROM memory_items WHERE valid_to IS NULL
                GROUP BY category"""
            ).fetchall()

            return {
                "total_active": total,
                "archived": archived,
                "by_category": {r["category"]: r["n"] for r in by_cat},
                "db_path": self.db_path,
            }