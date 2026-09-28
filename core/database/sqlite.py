"""پیاده‌سازی SQLite با aiosqlite."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from core.database.base import BaseDatabase, _QueryResult
from core.kernel.logger import log_error, log_info, log_success, log_warning

# نسخهٔ اسکیمای فعلی — هر بار ستون جدید اضافه شد، +۱ کن
_SCHEMA_VERSION = 6


class SQLiteDatabase(BaseDatabase):
    """پیاده‌سازی SQLite با aiosqlite — سبک، فایلی، بدون سرور."""

    _MESSAGES_COLUMNS = """
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        platform_message_id TEXT,
        text TEXT,
        text_translated TEXT,
        chat_id TEXT,
        chat_name TEXT,
        chat_username TEXT,
        chat_link TEXT,
        chat_type TEXT,
        user_id TEXT,
        sender_name TEXT,
        timestamp TIMESTAMP,
        keywords TEXT,
        media_type TEXT DEFAULT 'text',
        file_id TEXT,
        local_file_path TEXT,
        caption TEXT,
        reply_to TEXT,
        opportunity_score INTEGER DEFAULT 0,
        opportunity_type TEXT,
        priority_tier TEXT,
        matched_products TEXT,
        brands TEXT,
        is_important INTEGER DEFAULT 0,
        my_note TEXT,
        raw_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """

    _LEADS_COLUMNS = """
        id TEXT PRIMARY KEY,
        message_id TEXT,
        platform TEXT NOT NULL,
        text TEXT,
        chat_id TEXT,
        chat_name TEXT,
        user_id TEXT,
        timestamp TIMESTAMP,
        keywords TEXT,
        media_type TEXT DEFAULT 'text',
        file_id TEXT,
        opportunity_score INTEGER DEFAULT 0,
        opportunity_type TEXT,
        priority_tier TEXT,
        matched_products TEXT,
        brands TEXT,
        is_important INTEGER DEFAULT 0,
        follow_up_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection = None

    @asynccontextmanager
    async def _get_conn(self):
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA synchronous = NORMAL")
            yield conn

    # ─── init + migrate ───
    async def init(self):
        t0 = time.monotonic()
        log_info(f"🗄️ [DB:sqlite] init شروع | path={self.db_path}")

        async with self._get_conn() as conn:
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS messages "
                f"({self._MESSAGES_COLUMNS})"
            )
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS leads "
                f"({self._LEADS_COLUMNS})"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS last_poll (
                    platform TEXT PRIMARY KEY,
                    last_poll TIMESTAMP,
                    last_message_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_offsets (
                    platform TEXT,
                    chat_id TEXT,
                    last_message_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, chat_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE UNIQUE,
                    total_messages INTEGER DEFAULT 0,
                    total_leads INTEGER DEFAULT 0,
                    unique_groups INTEGER DEFAULT 0,
                    unique_keywords INTEGER DEFAULT 0,
                    avg_score REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version INTEGER UNIQUE,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for idx in (
                "CREATE INDEX IF NOT EXISTS idx_messages_timestamp "
                "ON messages(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_keywords "
                "ON messages(keywords)",
                "CREATE INDEX IF NOT EXISTS idx_messages_platform "
                "ON messages(platform)",
                "CREATE INDEX IF NOT EXISTS idx_messages_chat "
                "ON messages(chat_name, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_leads_score "
                "ON leads(opportunity_score DESC)",
                "CREATE INDEX IF NOT EXISTS idx_leads_timestamp "
                "ON leads(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_daily_stats_date "
                "ON daily_stats(date)",
            ):
                await conn.execute(idx)
            await conn.commit()

        await self.migrate()

        elapsed_ms = (time.monotonic() - t0) * 1000
        size_kb = (
            round(os.path.getsize(self.db_path) / 1024, 1)
            if os.path.exists(self.db_path) else 0
        )
        log_success(
            f"✅ [DB:sqlite] init کامل شد | "
            f"schema=v{_SCHEMA_VERSION} | {elapsed_ms:.0f}ms | "
            f"size={size_kb}KB"
        )

    async def migrate(self):
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "SELECT version FROM migrations "
                "ORDER BY version DESC LIMIT 1"
            )
            row = await cur.fetchone()
            current = row[0] if row else 0

            log_info(
                f"🔧 [DB:sqlite] migrate | "
                f"current=v{current} target=v{_SCHEMA_VERSION}"
            )

            # 🆕 sync PRAGMA user_version با جدول migrations
            await self._sync_user_version(conn, current)

            if current >= _SCHEMA_VERSION:
                log_info(
                    f"ℹ️ [DB:sqlite] migrate نیازی نیست "
                    f"(از قبل v{current})"
                )
                return

            migrations = {
                1: [("messages", "raw_data")],
                2: [("last_poll", "updated_at")],
                3: [
                    ("messages", "chat_username"),
                    ("messages", "chat_link"),
                    ("messages", "chat_type"),
                ],
                4: [("messages", "local_file_path")],
                5: [("messages", "text_translated")],
                6: [
                    ("messages", "priority_tier"),
                    ("messages", "matched_products"),
                    ("messages", "brands"),
                    ("leads", "priority_tier"),
                    ("leads", "matched_products"),
                    ("leads", "brands"),
                ],
            }

            applied = 0
            for version in sorted(migrations.keys()):
                if current >= version:
                    continue
                alters = migrations[version]
                ok_cols = 0
                for table, column in alters:
                    try:
                        await conn.execute(
                            f"ALTER TABLE {table} "
                            f"ADD COLUMN {column} TEXT"
                        )
                        ok_cols += 1
                    except Exception as e:
                        log_warning(
                            f"⚠️ [DB:sqlite] migrate: "
                            f"{table}.{column} از قبل هست "
                            f"({type(e).__name__})"
                        )
                await conn.execute(
                    "INSERT OR IGNORE INTO migrations (version) "
                    "VALUES (?)",
                    (version,),
                )
                applied += 1
                log_success(
                    f"✅ [DB:sqlite] migration v{version} اعمال شد | "
                    f"ستون‌ها: {ok_cols}/{len(alters)}"
                )
            await conn.commit()

            if applied:
                log_success(
                    f"✅ [DB:sqlite] migrate کامل | "
                    f"{applied} migration"
                )
                # 🆕 بعد از اعمال migrationها، PRAGMA رو هم set کن
                await self._sync_user_version(
                    conn, _SCHEMA_VERSION
                )
            else:
                log_info("ℹ️ [DB:sqlite] migrate: لازم نبود")

    # ═══════════════════════════════════════════════════════════
    # 🆕 sync PRAGMA user_version — استاندارد SQLite
    # ═══════════════════════════════════════════════════════════
    async def _sync_user_version(self, conn, version: int):
        """PRAGMA user_version رو با جدول migrations sync میکنه.

        چرا مهمه؟
          - PRAGMA user_version استاندارد SQLite هست
          - ابزارهای خارجی (DBeaver, DB Browser) ازش میفهمن schema چیه
          - جدول migrations منبع حقیقت (source of truth) میمونه
          - PRAGMA فقط یه آینهست که همیشه باید هماهنگ باشه

        Args:
            conn: اتصال aiosqlite فعال
            version: نسخهٔ فعلی از جدول migrations
        """
        if version <= 0:
            return
        try:
            # چک کن از قبل set شده؟
            cur = await conn.execute("PRAGMA user_version")
            row = await cur.fetchone()
            current_pragma = row[0] if row else 0

            if current_pragma == version:
                # از قبل هماهنگه — کاری نکن
                return

            # PRAGMA نمیتونه پارامتر ? بگیره → f-string امنه چون int هست
            await conn.execute(
                f"PRAGMA user_version = {int(version)}"
            )
            await conn.commit()
            log_info(
                f"🔧 [DB:sqlite] PRAGMA user_version sync | "
                f"v{current_pragma} → v{version}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [DB:sqlite] sync user_version | "
                f"{type(e).__name__}: {e}"
            )
            
    # ─── write ───
    async def save_message(self, message: dict[str, Any]) -> int:
        msg_id = message.get("id")
        platform = message.get("platform", "?")
        try:
            async with self._get_conn() as conn:
                cursor = await conn.execute(
                    """
                    INSERT OR IGNORE INTO messages (
                        id, platform, platform_message_id, text,
                        text_translated, chat_id, chat_name,
                        chat_username, chat_link, chat_type,
                        user_id, sender_name, timestamp, keywords,
                        media_type, file_id, local_file_path,
                        caption, reply_to, opportunity_score,
                        opportunity_type, priority_tier,
                        matched_products, brands, raw_data
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                              ?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        msg_id,
                        platform,
                        message.get("platform_message_id"),
                        (message.get("text") or "")[:5000],
                        message.get("text_translated"),
                        message.get("chat_id"),
                        message.get("chat_name", "Unknown"),
                        message.get("chat_username"),
                        message.get("chat_link"),
                        message.get("chat_type"),
                        message.get("user_id"),
                        message.get("sender_name"),
                        message.get("timestamp"),
                        message.get("keywords", ""),
                        message.get("media_type", "text"),
                        message.get("file_id"),
                        message.get("local_file_path"),
                        message.get("caption"),
                        message.get("reply_to"),
                        message.get("opportunity_score", 0),
                        message.get("opportunity_type"),
                        message.get("priority_tier", "T0"),
                        message.get("matched_products", ""),
                        message.get("brands", ""),
                        json.dumps(
                            message.get("raw_data", {}),
                            ensure_ascii=False,
                        )[:10000],
                    ),
                )
                await conn.commit()
                rowid = cursor.lastrowid or 1
                log_info(
                    f"💾 [DB:sqlite:save_message] ✅ id={msg_id} "
                    f"platform={platform} rowid={rowid}"
                )
                return rowid
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:save_message] id={msg_id} "
                f"platform={platform} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def save_lead(self, lead: dict[str, Any]) -> int:
        lead_id = lead.get("id")
        platform = lead.get("platform", "?")
        try:
            async with self._get_conn() as conn:
                cursor = await conn.execute(
                    """
                    INSERT OR IGNORE INTO leads (
                        id, message_id, platform, text, chat_id,
                        chat_name, user_id, timestamp, keywords,
                        media_type, file_id, opportunity_score,
                        opportunity_type, priority_tier,
                        matched_products, brands
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        lead_id,
                        lead.get("message_id"),
                        platform,
                        (lead.get("text") or "")[:5000],
                        lead.get("chat_id"),
                        lead.get("chat_name", "Unknown"),
                        lead.get("user_id"),
                        lead.get("timestamp"),
                        lead.get("keywords", ""),
                        lead.get("media_type", "text"),
                        lead.get("file_id"),
                        lead.get("opportunity_score", 0),
                        lead.get("opportunity_type"),
                        lead.get("priority_tier", "T0"),
                        lead.get("matched_products", ""),
                        lead.get("brands", ""),
                    ),
                )
                await conn.commit()
                rowid = cursor.lastrowid or 1

            log_success(
                f"🎯 [DB:sqlite:save_lead] ✅ id={lead_id} "
                f"platform={platform} "
                f"kw={lead.get('keywords', '')!r} "
                f"tier={lead.get('priority_tier', 'T0')} "
                f"score={lead.get('opportunity_score', 0)}"
            )

            try:
                asyncio.create_task(self.update_daily_stats())
            except RuntimeError:
                log_warning(
                    "⚠️ [DB:sqlite:save_lead] update_daily_stats "
                    "رها شد (loop بسته)"
                )
            return rowid
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:save_lead] id={lead_id} "
                f"platform={platform} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def mark_important(self, message_id: str, note: str = None):
        async with self._get_conn() as conn:
            if note:
                await conn.execute(
                    "UPDATE messages SET is_important = 1, "
                    "my_note = ? WHERE id = ?",
                    (note, message_id),
                )
            else:
                await conn.execute(
                    "UPDATE messages SET is_important = 1 "
                    "WHERE id = ?",
                    (message_id,),
                )
            await conn.commit()
        log_info(
            f"⭐ [DB:sqlite:mark_important] id={message_id} "
            f"note={'✅' if note else '—'}"
        )

    async def update_lead_score(self, lead_id: str, score: int):
        async with self._get_conn() as conn:
            await conn.execute(
                "UPDATE leads SET opportunity_score = ? "
                "WHERE id = ?",
                (score, lead_id),
            )
            await conn.commit()
        log_info(
            f"📊 [DB:sqlite:update_lead_score] "
            f"id={lead_id} score={score}"
        )

    async def update_last_poll(
        self, platform: str, timestamp: datetime
    ):
        async with self._get_conn() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO last_poll
                    (platform, last_poll, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (platform, timestamp.isoformat()),
            )
            await conn.commit()

    async def set_chat_offset(
        self, platform: str, chat_id: str, last_message_id
    ):
        async with self._get_conn() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO chat_offsets
                    (platform, chat_id, last_message_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (platform, str(chat_id), str(last_message_id)),
            )
            await conn.commit()

    async def update_daily_stats(self):
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            async with self._get_conn() as conn:
                cur = await conn.execute(
                    "SELECT id FROM daily_stats WHERE date = ?",
                    (today,),
                )
                if await cur.fetchone():
                    return

                cur = await conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_messages,
                        SUM(CASE WHEN keywords != '' THEN 1 ELSE 0 END)
                            AS total_leads,
                        COUNT(DISTINCT chat_name) AS unique_groups
                    FROM messages WHERE date(timestamp) = ?
                    """,
                    (today,),
                )
                stats = await cur.fetchone()

                cur = await conn.execute(
                    "SELECT keywords FROM messages "
                    "WHERE date(timestamp) = ? AND keywords != ''",
                    (today,),
                )
                rows = await cur.fetchall()
                kws = set()
                for r in rows:
                    for kw in (r[0] or "").split(","):
                        if kw.strip():
                            kws.add(kw.strip())

                await conn.execute(
                    """
                    INSERT INTO daily_stats
                        (date, total_messages, total_leads,
                         unique_groups, unique_keywords)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (today, stats[0], stats[1], stats[2], len(kws)),
                )
                await conn.commit()

            log_success(
                f"📅 [DB:sqlite:daily_stats] ✅ {today} | "
                f"messages={stats[0]} leads={stats[1]} "
                f"groups={stats[2]} kw={len(kws)}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [DB:sqlite:daily_stats] "
                f"{type(e).__name__}: {e}"
            )

    async def delete_old_messages(self, days: int = 30):
        cutoff = (
            datetime.now() - timedelta(days=days)
        ).isoformat()
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "DELETE FROM messages WHERE timestamp < ?",
                (cutoff,),
            )
            await conn.commit()
            removed = cur.rowcount or 0
        log_success(
            f"🧹 [DB:sqlite:cleanup] حذف {removed} پیام "
            f"قدیمی‌تر از {days} روز"
        )

    async def backup(self, backup_path: str) -> bool:
        import shutil
        try:
            shutil.copy2(self.db_path, backup_path)
            size_kb = round(os.path.getsize(backup_path) / 1024, 1)
            log_success(
                f"💾 [DB:sqlite:backup] ✅ {backup_path} "
                f"({size_kb}KB)"
            )
            return True
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:backup] "
                f"{type(e).__name__}: {e}"
            )
            return False

    # ─── read ───
    async def get_last_poll(
        self, platform: str
    ) -> datetime | None:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "SELECT last_poll FROM last_poll WHERE platform = ?",
                (platform,),
            )
            row = await cur.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
        return None

    async def get_chat_offset(
        self, platform: str, chat_id: str
    ) -> str | None:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "SELECT last_message_id FROM chat_offsets "
                "WHERE platform=? AND chat_id=?",
                (platform, str(chat_id)),
            )
            row = await cur.fetchone()
            return row[0] if row and row[0] else None

    async def get_high_score_leads(
        self, min_score: int = 0, limit: int = 50
    ) -> list[dict]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM leads
                WHERE opportunity_score >= ?
                ORDER BY opportunity_score DESC, timestamp DESC
                LIMIT ?
                """,
                (min_score, limit),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_leads_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[dict]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM messages
                WHERE date(timestamp) BETWEEN ? AND ?
                  AND keywords != ''
                ORDER BY timestamp DESC
                """,
                (start_date, end_date),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_keyword_stats(
        self, limit: int = 20
    ) -> list[tuple[str, int]]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "SELECT keywords FROM messages "
                "WHERE keywords != ''"
            )
            rows = await cur.fetchall()
            counts: dict[str, int] = {}
            for row in rows:
                for kw in (row[0] or "").split(","):
                    kw = kw.strip()
                    if kw:
                        counts[kw] = counts.get(kw, 0) + 1
            return sorted(
                counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:limit]

    async def get_group_stats(
        self, limit: int = 20
    ) -> list[tuple[str, int]]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT chat_name, COUNT(*) AS cnt FROM messages
                WHERE keywords != '' GROUP BY chat_name
                ORDER BY cnt DESC LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [(r[0], r[1]) for r in rows]

    async def get_daily_stats(
        self, days: int = 7
    ) -> list[dict]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT date(timestamp) AS date, COUNT(*) AS count
                FROM messages
                WHERE keywords != ''
                  AND date(timestamp) >= date('now', ?)
                GROUP BY date(timestamp)
                ORDER BY date DESC
                """,
                (f"-{days} days",),
            )
            rows = await cur.fetchall()
            return [
                {"date": r[0], "count": r[1]} for r in rows
            ]

    async def search(
        self,
        query: str,
        search_type: str = "all",
        limit: int = 50,
    ) -> list[dict]:
        like = f"%{query}%"
        async with self._get_conn() as conn:
            if search_type == "keyword":
                cur = await conn.execute(
                    """
                    SELECT id, platform, chat_name, text,
                           keywords, timestamp
                    FROM messages WHERE keywords LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (like, limit),
                )
            elif search_type == "text":
                cur = await conn.execute(
                    """
                    SELECT id, platform, chat_name, text,
                           keywords, timestamp
                    FROM messages WHERE text LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (like, limit),
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT id, platform, chat_name, text,
                           keywords, timestamp
                    FROM messages
                    WHERE text LIKE ? OR keywords LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (like, like, limit),
                )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_important_messages(
        self, limit: int = 50
    ) -> list[dict]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM messages WHERE is_important = 1
                ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_total_count(self) -> dict[str, int]:
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM messages"
            )
            messages = (await cur.fetchone())[0]
            cur = await conn.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE keywords != ''"
            )
            leads = (await cur.fetchone())[0]
            cur = await conn.execute(
                "SELECT COUNT(DISTINCT chat_name) FROM messages"
            )
            groups = (await cur.fetchone())[0]
            return {
                "messages": messages,
                "leads": leads,
                "groups": groups,
            }

    # ─── generic ───
    async def execute(
        self, query: str, params=()
    ) -> _QueryResult:
        t0 = time.monotonic()
        async with self._get_conn() as conn:
            cur = await conn.execute(query, params)
            try:
                rows = await cur.fetchall()
            except Exception:
                rows = []
            await conn.commit()
        elapsed_ms = (time.monotonic() - t0) * 1000

        if elapsed_ms > 500:
            log_warning(
                f"🐌 [DB:sqlite:execute] کوئری کند | "
                f"{elapsed_ms:.0f}ms | {query[:80]!r}"
            )
        return _QueryResult(rows)

    async def close(self):
        self._connection = None
        log_info("🔌 [DB:sqlite] بسته شد")
