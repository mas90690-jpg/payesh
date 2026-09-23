"""پیاده‌سازی PostgreSQL با asyncpg."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any

from core.database.base import BaseDatabase, _QueryResult
from core.kernel.logger import log_error, log_info, log_success, log_warning

_SCHEMA_VERSION = 6


class PostgresDatabase(BaseDatabase):
    """پیاده‌سازی PostgreSQL با asyncpg."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._pool = None

    @staticmethod
    def _ts(value) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return datetime.now()
        return datetime.now()

    # ─── init + migrate ───
    async def init(self):
        import asyncpg

        t0 = time.monotonic()
        log_info(
            f"🐘 [DB:pg] init شروع | "
            f"host={self.host}:{self.port} "
            f"db={self.database} user={self.user}"
        )

        self._pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            min_size=1,
            max_size=10,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
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
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
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
                    id SERIAL PRIMARY KEY,
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

        await self.migrate()

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [DB:pg] init کامل شد | "
            f"schema=v{_SCHEMA_VERSION} | {elapsed_ms:.0f}ms | "
            f"pool=1..10"
        )

    async def migrate(self):
        log_info("🔧 [DB:pg] migrate شروع")
        async with self._pool.acquire() as conn:
            migrations = [
                ("messages", "text_translated", "TEXT"),
                ("messages", "chat_username", "TEXT"),
                ("messages", "chat_link", "TEXT"),
                ("messages", "chat_type", "TEXT"),
                ("messages", "local_file_path", "TEXT"),
                ("messages", "priority_tier", "TEXT"),
                ("messages", "matched_products", "TEXT"),
                ("messages", "brands", "TEXT"),
                ("messages", "raw_data", "TEXT"),
                ("leads", "priority_tier", "TEXT"),
                ("leads", "matched_products", "TEXT"),
                ("leads", "brands", "TEXT"),
            ]
            ok = 0
            for table, column, col_type in migrations:
                try:
                    await conn.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN IF NOT EXISTS "
                        f"{column} {col_type}"
                    )
                    ok += 1
                except Exception as e:
                    log_warning(
                        f"⚠️ [DB:pg] migrate {table}.{column} | "
                        f"{type(e).__name__}: {e}"
                    )
            log_success(
                f"✅ [DB:pg] migrate کامل | "
                f"{ok}/{len(migrations)} ستون"
            )

    # ─── write ───
    async def save_message(self, message: dict[str, Any]) -> int:
        msg_id = message.get("id")
        platform = message.get("platform", "?")
        raw = json.dumps(
            message.get("raw_data", {}), ensure_ascii=False
        )[:10000]
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO messages (
                        id, platform, platform_message_id, text,
                        text_translated, chat_id, chat_name,
                        chat_username, chat_link, chat_type,
                        user_id, sender_name, timestamp, keywords,
                        media_type, file_id, local_file_path,
                        caption, reply_to, opportunity_score,
                        opportunity_type, priority_tier,
                        matched_products, brands, raw_data
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                        $11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                        $21,$22,$23,$24,$25
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
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
                    self._ts(message.get("timestamp")),
                    message.get("keywords", ""),
                    message.get("media_type", "text"),
                    message.get("file_id"),
                    message.get("local_file_path"),
                    message.get("caption"),
                    message.get("reply_to"),
                    int(message.get("opportunity_score", 0) or 0),
                    message.get("opportunity_type"),
                    message.get("priority_tier", "T0"),
                    message.get("matched_products", ""),
                    message.get("brands", ""),
                    raw,
                )
            log_info(
                f"💾 [DB:pg:save_message] ✅ id={msg_id} "
                f"platform={platform}"
            )
            return 1
        except Exception as e:
            log_error(
                f"❌ [DB:pg:save_message] id={msg_id} "
                f"platform={platform} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def save_lead(self, lead: dict[str, Any]) -> int:
        lead_id = lead.get("id")
        platform = lead.get("platform", "?")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO leads (
                        id, message_id, platform, text, chat_id,
                        chat_name, user_id, timestamp, keywords,
                        media_type, file_id, opportunity_score,
                        opportunity_type, priority_tier,
                        matched_products, brands
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,
                        $9,$10,$11,$12,$13,$14,$15,$16
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    lead_id,
                    lead.get("message_id"),
                    platform,
                    (lead.get("text") or "")[:5000],
                    lead.get("chat_id"),
                    lead.get("chat_name", "Unknown"),
                    lead.get("user_id"),
                    self._ts(lead.get("timestamp")),
                    lead.get("keywords", ""),
                    lead.get("media_type", "text"),
                    lead.get("file_id"),
                    int(lead.get("opportunity_score", 0) or 0),
                    lead.get("opportunity_type"),
                    lead.get("priority_tier", "T0"),
                    lead.get("matched_products", ""),
                    lead.get("brands", ""),
                )
            log_success(
                f"🎯 [DB:pg:save_lead] ✅ id={lead_id} "
                f"platform={platform} "
                f"kw={lead.get('keywords', '')!r} "
                f"tier={lead.get('priority_tier', 'T0')}"
            )
            try:
                asyncio.create_task(self.update_daily_stats())
            except RuntimeError:
                log_warning(
                    "⚠️ [DB:pg:save_lead] update_daily_stats "
                    "رها شد (loop بسته)"
                )
            return 1
        except Exception as e:
            log_error(
                f"❌ [DB:pg:save_lead] id={lead_id} "
                f"platform={platform} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def mark_important(self, message_id: str, note: str = None):
        async with self._pool.acquire() as conn:
            if note:
                await conn.execute(
                    "UPDATE messages SET is_important=1, "
                    "my_note=$1 WHERE id=$2",
                    note,
                    message_id,
                )
            else:
                await conn.execute(
                    "UPDATE messages SET is_important=1 "
                    "WHERE id=$1",
                    message_id,
                )
        log_info(
            f"⭐ [DB:pg:mark_important] id={message_id} "
            f"note={'✅' if note else '—'}"
        )

    async def update_lead_score(self, lead_id: str, score: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE leads SET opportunity_score=$1 "
                "WHERE id=$2",
                score,
                lead_id,
            )
        log_info(
            f"📊 [DB:pg:update_lead_score] "
            f"id={lead_id} score={score}"
        )

    async def update_last_poll(
        self, platform: str, timestamp: datetime
    ):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO last_poll
                    (platform, last_poll, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (platform) DO UPDATE
                  SET last_poll=EXCLUDED.last_poll,
                      updated_at=NOW()
                """,
                platform,
                self._ts(timestamp),
            )

    async def set_chat_offset(
        self, platform: str, chat_id: str, last_message_id
    ):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_offsets
                    (platform, chat_id, last_message_id, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (platform, chat_id) DO UPDATE
                  SET last_message_id=EXCLUDED.last_message_id,
                      updated_at=NOW()
                """,
                platform,
                str(chat_id),
                str(last_message_id),
            )

    async def update_daily_stats(self):
        today = datetime.now().date()
        try:
            async with self._pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT 1 FROM daily_stats WHERE date=$1",
                    today,
                )
                if exists:
                    return
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS total_messages,
                           SUM(CASE WHEN keywords <> '' THEN 1 ELSE 0 END)
                               AS total_leads,
                           COUNT(DISTINCT chat_name) AS unique_groups
                    FROM messages WHERE timestamp::date = $1
                    """,
                    today,
                )
                krows = await conn.fetch(
                    "SELECT keywords FROM messages "
                    "WHERE timestamp::date=$1 AND keywords <> ''",
                    today,
                )
                kset = set()
                for r in krows:
                    for kw in (r[0] or "").split(","):
                        if kw.strip():
                            kset.add(kw.strip())
                await conn.execute(
                    """
                    INSERT INTO daily_stats
                        (date, total_messages, total_leads,
                         unique_groups, unique_keywords)
                    VALUES ($1,$2,$3,$4,$5)
                    ON CONFLICT (date) DO NOTHING
                    """,
                    today,
                    row[0] or 0,
                    row[1] or 0,
                    row[2] or 0,
                    len(kset),
                )
            log_success(
                f"📅 [DB:pg:daily_stats] ✅ {today} | "
                f"messages={row[0] or 0} leads={row[1] or 0} "
                f"groups={row[2] or 0} kw={len(kset)}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [DB:pg:daily_stats] "
                f"{type(e).__name__}: {e}"
            )

    async def delete_old_messages(self, days: int = 30):
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM messages WHERE timestamp < "
                "NOW() - ($1 || ' days')::interval",
                str(days),
            )
        log_success(
            f"🧹 [DB:pg:cleanup] {result} — "
            f"حذف قدیمی‌تر از {days} روز"
        )

    async def backup(self, backup_path: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pg_dump",
                "-h", str(self.host),
                "-p", str(self.port),
                "-U", self.user,
                "-d", self.database,
                "-f", backup_path,
                env={**os.environ, "PGPASSWORD": self.password},
            )
            await proc.wait()
            ok = proc.returncode == 0
            if ok:
                size_kb = (
                    round(os.path.getsize(backup_path) / 1024, 1)
                    if os.path.exists(backup_path) else 0
                )
                log_success(
                    f"💾 [DB:pg:backup] ✅ {backup_path} "
                    f"({size_kb}KB)"
                )
            else:
                log_error(
                    f"❌ [DB:pg:backup] pg_dump با کد "
                    f"{proc.returncode} خارج شد"
                )
            return ok
        except Exception as e:
            log_error(
                f"❌ [DB:pg:backup] pg_dump در دسترس نیست | "
                f"{type(e).__name__}: {e}"
            )
            return False

    # ─── read ───
    async def get_last_poll(
        self, platform: str
    ) -> datetime | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT last_poll FROM last_poll "
                "WHERE platform=$1",
                platform,
            )

    async def get_chat_offset(
        self, platform: str, chat_id: str
    ) -> str | None:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT last_message_id FROM chat_offsets "
                "WHERE platform=$1 AND chat_id=$2",
                platform,
                str(chat_id),
            )
            return val or None

    async def get_high_score_leads(
        self, min_score: int = 0, limit: int = 50
    ) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM leads
                WHERE opportunity_score >= $1
                ORDER BY opportunity_score DESC, timestamp DESC
                LIMIT $2
                """,
                min_score,
                limit,
            )
            return [dict(r) for r in rows]

    async def get_leads_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM messages
                WHERE timestamp::date BETWEEN $1::date AND $2::date
                  AND keywords <> ''
                ORDER BY timestamp DESC
                """,
                start_date,
                end_date,
            )
            return [dict(r) for r in rows]

    async def get_keyword_stats(
        self, limit: int = 20
    ) -> list[tuple[str, int]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT keywords FROM messages "
                "WHERE keywords <> ''"
            )
        counts: dict[str, int] = {}
        for r in rows:
            for kw in (r[0] or "").split(","):
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
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT chat_name, COUNT(*) AS cnt FROM messages
                WHERE keywords <> '' GROUP BY chat_name
                ORDER BY cnt DESC LIMIT $1
                """,
                limit,
            )
            return [(r[0], r[1]) for r in rows]

    async def get_daily_stats(
        self, days: int = 7
    ) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT timestamp::date AS date,
                       COUNT(*) AS count
                FROM messages
                WHERE keywords <> ''
                  AND timestamp >= NOW() - ($1 || ' days')::interval
                GROUP BY timestamp::date
                ORDER BY date DESC
                """,
                str(days),
            )
            return [
                {"date": str(r[0]), "count": r[1]} for r in rows
            ]

    async def search(
        self,
        query: str,
        search_type: str = "all",
        limit: int = 50,
    ) -> list[dict]:
        like = f"%{query}%"
        async with self._pool.acquire() as conn:
            if search_type == "keyword":
                rows = await conn.fetch(
                    """
                    SELECT id, platform, chat_name, text,
                           keywords, timestamp
                    FROM messages WHERE keywords ILIKE $1
                    ORDER BY timestamp DESC LIMIT $2
                    """,
                    like,
                    limit,
                )
            elif search_type == "text":
                rows = await conn.fetch(
                    """
                    SELECT id, platform, chat_name, text,
                           keywords, timestamp
                    FROM messages WHERE text ILIKE $1
                    ORDER BY timestamp DESC LIMIT $2
                    """,
                    like,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, platform, chat_name, text,
                           keywords, timestamp
                    FROM messages
                    WHERE text ILIKE $1 OR keywords ILIKE $1
                    ORDER BY timestamp DESC LIMIT $2
                    """,
                    like,
                    limit,
                )
            return [dict(r) for r in rows]

    async def get_important_messages(
        self, limit: int = 50
    ) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM messages "
                "WHERE is_important=1 "
                "ORDER BY timestamp DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in rows]

    async def get_total_count(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM messages"
            )
            leads = await conn.fetchval(
                "SELECT COUNT(*) FROM messages "
                "WHERE keywords <> ''"
            )
            groups = await conn.fetchval(
                "SELECT COUNT(DISTINCT chat_name) FROM messages"
            )
            return {
                "messages": total or 0,
                "leads": leads or 0,
                "groups": groups or 0,
            }

    # ─── generic ───
    async def execute(
        self, query: str, params=()
    ) -> _QueryResult:
        t0 = time.monotonic()
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, *params)
                result = _QueryResult([dict(r) for r in rows])
            except Exception:
                await conn.execute(query, *params)
                result = _QueryResult([])
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > 500:
            log_warning(
                f"🐌 [DB:pg:execute] کوئری کند | "
                f"{elapsed_ms:.0f}ms | {query[:80]!r}"
            )
        return result

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
        log_info("🔌 [DB:pg] بسته شد")
