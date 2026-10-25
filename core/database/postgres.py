"""پیاده‌سازی PostgreSQL با asyncpg.

نسخهٔ ۳ (این نسخه):
  - ✅ همهٔ بهبودهای نسخهٔ ۲ حفظ شد.
  - 🔒 SEC FIX: حذف همهٔ f-string های داخل SQL.
    * ALTER TABLE → `_safe_identifier` (regex)
    * AT TIME ZONE → پارامتر bind ($N)
    * CREATE DATABASE → در `scripts/fix_pg.py` جدا fix می‌شود
  - 🔒 SEC FIX: `_DB_TIMEZONE` حالا به‌عنوان پارامتر bind
    استفاده می‌شود (نه f-string).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from datetime import datetime
from typing import Any

from core.database.base import BaseDatabase, _QueryResult
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


def _env_str(name: str, default: str = "") -> str:
    """خواندن str از env — با strip."""
    return (os.getenv(name) or default).strip()


# 🔒 SQL identifier validation — جلوگیری از SQL injection
# در جاهایی که پارامتر bind امکان‌پذیر نیست
# (نام جدول/ستون/نوع — PostgreSQL هم مثل SQLite پارامتر نمی‌پذیره).
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _safe_identifier(name: str, kind: str = "identifier") -> str:
    """اعتبارسنجی نام جدول/ستون — فقط [A-Za-z_][A-Za-z0-9_]*.

    Raises:
        ValueError: اگر نام معتبر نباشد.
    """
    if not isinstance(name, str) or not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"invalid SQL {kind}: {name!r}")
    return name


def _safe_col_type(name: str, kind: str = "column type") -> str:
    """اعتبارسنجی نوع ستون — فقط [A-Za-z0-9_() ,].

    مثال‌های معتبر: TEXT, INTEGER, VARCHAR(255), NUMERIC(10, 2).
    """
    if not isinstance(name, str):
        raise ValueError(f"invalid SQL {kind}: {name!r}")
    # فقط حروف، اعداد، زیرخط، پرانتز، کاما، فاصله
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*(\s*\(\s*\d+"
                    r"(\s*,\s*\d+)?\s*\))?$", name):
        raise ValueError(f"invalid SQL {kind}: {name!r}")
    return name


# ═══════════════════════════════════════════
# نسخهٔ اسکیمای فعلی
# ═══════════════════════════════════════════
try:
    from config import (
        POSTGRES_SCHEMA_VERSION as _SCHEMA_VERSION,
    )
except ImportError:
    _SCHEMA_VERSION = 6


# ═══════════════════════════════════════════
# آستانه‌ها
# ═══════════════════════════════════════════
_SLOW_QUERY_MS = _env_int(
    "DB_PG_SLOW_QUERY_MS", 500,
    min_val=50, max_val=60_000,
)
_PG_DUMP_TIMEOUT_S = _env_int(
    "DB_PG_BACKUP_TIMEOUT_S", 300,
    min_val=10, max_val=3600,
)

# 🎯 timezone برای localtime
# اگر دیتابیس TZ متفاوت دارد، می‌توان از این override کرد
_DB_TIMEZONE = _env_str(
    "DB_TIMEZONE", "Asia/Tehran"
) or "Asia/Tehran"

# 🎯 سقف برای output query
_MAX_QUERY_ROWS = _env_int(
    "DB_PG_MAX_QUERY_ROWS", 10_000,
    min_val=100, max_val=1_000_000,
)


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
        # 🐛 FIX: taskهای background
        self._pending_tasks: list[asyncio.Task] = []

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    @staticmethod
    def _ts(value) -> datetime:
        """تبدیل امن به datetime."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return datetime.now()  # noqa: DTZ005
        return datetime.now()  # noqa: DTZ005

    def _spawn_task(self, coro) -> None:
        """🐛 FIX: ساخت task با reference نگه‌داری."""
        try:
            task = asyncio.create_task(coro)
            self._pending_tasks.append(task)
            task.add_done_callback(self._on_task_done)
        except RuntimeError:
            log_warning(
                "⚠️ [DB:pg:task] create_task خارج از loop"
            )

    def _on_task_done(self, task: asyncio.Task) -> None:
        with contextlib.suppress(ValueError):
            self._pending_tasks.remove(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except (asyncio.CancelledError, Exception):
            return
        if exc is not None:
            log_warning(
                f"⚠️ [DB:pg:task] "
                f"{type(exc).__name__}: {exc}"
            )

    # ═══════════════════════════════════════════
    # init + migrate
    # ═══════════════════════════════════════════
    async def init(self):
        import asyncpg

        t0 = time.monotonic()
        log_info(
            f"🐘 [DB:pg:init] شروع | "
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
            # ─── messages ───
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
            # ─── leads ───
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
            # ─── last_poll ───
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
            # ─── chat_offsets ───
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
            # ─── daily_stats ───
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
            # ─── migrations table ───
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # ─── indexes ───
            indexes = [
                ("CREATE INDEX IF NOT EXISTS idx_messages_timestamp "
                "ON messages(timestamp)"),
                ("CREATE INDEX IF NOT EXISTS idx_messages_keywords "
                "ON messages(keywords)"),
                ("CREATE INDEX IF NOT EXISTS idx_messages_platform "
                "ON messages(platform)"),
                ("CREATE INDEX IF NOT EXISTS idx_messages_chat "
                "ON messages(chat_name, timestamp)"),
                ("CREATE INDEX IF NOT EXISTS idx_leads_score "
                "ON leads(opportunity_score DESC)"),
                ("CREATE INDEX IF NOT EXISTS idx_leads_timestamp "
                "ON leads(timestamp)"),
                ("CREATE INDEX IF NOT EXISTS idx_daily_stats_date "
                "ON daily_stats(date)"),
            ]
            for idx in indexes:
                try:
                    await conn.execute(idx)
                except Exception as e:
                    log_warning(
                        f"⚠️ [DB:pg:init] index | "
                        f"{type(e).__name__}: {e}"
                    )

        await self.migrate()

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [DB:pg:init] کامل | "
            f"schema=v{_SCHEMA_VERSION} | {elapsed_ms:.0f}ms | "
            f"pool=1..10"
        )

    async def migrate(self):
        """اعمال migrationها با ردیابی در جدول migrations."""
        async with self._pool.acquire() as conn:
            # ─── خواندن نسخهٔ فعلی ───
            try:
                current = await conn.fetchval(
                    "SELECT COALESCE(MAX(version), 0) "
                    "FROM migrations"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [DB:pg:migrate] read migrations | "
                    f"{type(e).__name__}: {e}"
                )
                current = 0

            current = int(current or 0)
            log_info(
                f"🔧 [DB:pg:migrate] "
                f"current=v{current} target=v{_SCHEMA_VERSION}"
            )

            if current >= _SCHEMA_VERSION:
                log_info("ℹ️ [DB:pg:migrate] نیازی نیست")
                return

            # ─── migrationهای نسخه‌بندی‌شده ───
            migrations: dict[int, list[tuple[str, str, str]]] = {
                1: [("messages", "raw_data", "TEXT")],
                2: [
                    ("messages", "text_translated", "TEXT"),
                    ("messages", "chat_username", "TEXT"),
                    ("messages", "chat_link", "TEXT"),
                    ("messages", "chat_type", "TEXT"),
                ],
                3: [("messages", "local_file_path", "TEXT")],
                4: [
                    ("messages", "priority_tier", "TEXT"),
                    ("messages", "matched_products", "TEXT"),
                    ("messages", "brands", "TEXT"),
                ],
                5: [
                    ("leads", "priority_tier", "TEXT"),
                    ("leads", "matched_products", "TEXT"),
                    ("leads", "brands", "TEXT"),
                ],
            }

            applied = 0
            for version in sorted(migrations.keys()):
                if current >= version:
                    continue
                alters = migrations[version]
                ok = 0
                for table, column, col_type in alters:
                    # 🔒 SEC FIX: اعتبارسنجی نام جدول/ستون/نوع
                    # قبل از interpolate در ALTER TABLE.
                    # (PostgreSQL برای identifier پارامتر نمی‌پذیرد)
                    try:
                        safe_table = _safe_identifier(
                            table, "table name"
                        )
                        safe_column = _safe_identifier(
                            column, "column name"
                        )
                        safe_type = _safe_col_type(
                            col_type, "column type"
                        )
                    except ValueError as ve:
                        log_warning(
                            f"⚠️ [DB:pg:migrate] "
                            f"identifier نامعتبر | {ve}"
                        )
                        continue

                    try:
                        await conn.execute(
                            f"ALTER TABLE {safe_table} "  # noqa: SEC-SQL-FSTRING
                            f"ADD COLUMN IF NOT EXISTS "
                            f"{safe_column} {safe_type}"
                        )
                        
                        ok += 1
                    except Exception as e:
                        log_warning(
                            f"⚠️ [DB:pg:migrate] "
                            f"{safe_table}.{safe_column} | "
                            f"{type(e).__name__}: {e}"
                        )
                # ثبت نسخه
                try:
                    await conn.execute(
                        "INSERT INTO migrations (version) "
                        "VALUES ($1) ON CONFLICT DO NOTHING",
                        version,
                    )
                except Exception as e:
                    log_warning(
                        f"⚠️ [DB:pg:migrate] record v{version} | "
                        f"{type(e).__name__}: {e}"
                    )
                applied += 1
                log_success(
                    f"✅ [DB:pg:migrate] v{version} | "
                    f"ستون‌ها: {ok}/{len(alters)}"
                )

            if applied:
                log_success(
                    f"✅ [DB:pg:migrate] {applied} migration"
                )
            else:
                log_info("ℹ️ [DB:pg:migrate] لازم نبود")

    # ═══════════════════════════════════════════
    # write
    # ═══════════════════════════════════════════
    async def save_message(
        self, message: dict[str, Any]
    ) -> int:
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
                f"💾 [DB:pg:save_message] OK | id={msg_id} "
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
                f"🎯 [DB:pg:save_lead] OK | id={lead_id} "
                f"platform={platform} | "
                f"kw={lead.get('keywords', '')!r} | "
                f"tier={lead.get('priority_tier', 'T0')}"
            )
            # 🐛 FIX: task با reference
            self._spawn_task(self.update_daily_stats())
            return 1
        except Exception as e:
            log_error(
                f"❌ [DB:pg:save_lead] id={lead_id} "
                f"platform={platform} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def mark_important(
        self, message_id: str, note: str | None = None
    ):
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
            f"⭐ [DB:pg:mark_important] id={message_id} | "
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
        """🐛 FIX: با AT TIME ZONE برای localtime.

        🔒 SEC FIX: `_DB_TIMEZONE` حالا به‌عنوان پارامتر bind
        می‌رود (نه f-string).
        """
        today = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
        try:
            async with self._pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT 1 FROM daily_stats WHERE date=$1",
                    today,
                )
                if exists:
                    return

                # 🔒 SEC FIX: AT TIME ZONE با پارامتر bind
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS total_messages,
                           SUM(CASE WHEN keywords <> ''
                               THEN 1 ELSE 0 END)
                               AS total_leads,
                           COUNT(DISTINCT chat_name)
                               AS unique_groups
                    FROM messages
                    WHERE (timestamp AT TIME ZONE $2)::date
                          = $1::date
                    """,
                    today,
                    _DB_TIMEZONE,
                )
                krows = await conn.fetch(
                    """
                    SELECT keywords FROM messages
                    WHERE (timestamp AT TIME ZONE $2)::date
                          = $1::date
                      AND keywords <> ''
                    """,
                    today,
                    _DB_TIMEZONE,
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
                f"📅 [DB:pg:daily_stats] OK | {today} | "
                f"messages={row[0] or 0} "
                f"leads={row[1] or 0} "
                f"groups={row[2] or 0} kw={len(kset)}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [DB:pg:daily_stats] "
                f"{type(e).__name__}: {e}"
            )

    async def delete_old_messages(self, days: int = 30):
        # 🐛 FIX: sanitize days
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            log_info(
                f"ℹ️ [DB:pg:cleanup] days={days} ≤ 0 — رد شد"
            )
            return

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
        """🐛 FIX: timeout + env فقط PGPASSWORD."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pg_dump",
                "-h", str(self.host),
                "-p", str(self.port),
                "-U", self.user,
                "-d", self.database,
                "-f", backup_path,
                env={**os.environ, "PGPASSWORD": self.password},
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(
                    proc.wait(),
                    timeout=_PG_DUMP_TIMEOUT_S,
                )
            except TimeoutError:
                log_error(
                    f"⏰ [DB:pg:backup] timeout "
                    f"({_PG_DUMP_TIMEOUT_S}s)"
                )
                with contextlib.suppress(Exception):
                    proc.kill()
                return False

            ok = proc.returncode == 0
            if ok:
                size_kb = (
                    round(
                        os.path.getsize(backup_path) / 1024, 1
                    )
                    if os.path.exists(backup_path) else 0
                )
                log_success(
                    f"💾 [DB:pg:backup] OK | {backup_path} "
                    f"({size_kb}KB)"
                )
            else:
                log_error(
                    f"❌ [DB:pg:backup] pg_dump rc="
                    f"{proc.returncode}"
                )
            return ok
        except Exception as e:
            log_error(
                f"❌ [DB:pg:backup] pg_dump "
                f"در دسترس نیست | "
                f"{type(e).__name__}: {e}"
            )
            return False

    # ═══════════════════════════════════════════
    # read
    # ═══════════════════════════════════════════
    async def get_last_poll(
        self, platform: str
    ) -> datetime | None:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT last_poll FROM last_poll "
                "WHERE platform=$1",
                platform,
            )
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            try:
                return datetime.fromisoformat(str(val))
            except Exception:
                return None

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
        """🔒 SEC FIX: `_DB_TIMEZONE` پارامتر bind."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM messages
                WHERE (timestamp AT TIME ZONE $3)::date
                      BETWEEN $1::date AND $2::date
                  AND keywords <> ''
                ORDER BY timestamp DESC
                """,
                start_date,
                end_date,
                _DB_TIMEZONE,
            )
            return [dict(r) for r in rows]

    async def get_keyword_stats(
        self, limit: int = 20
    ) -> list[tuple[str, int]]:
        """🎯 از SQL aggregate (سریع‌تر)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT unnest(string_to_array(keywords, ','))
                    AS kw,
                    COUNT(*) AS cnt
                FROM messages
                WHERE keywords <> ''
                GROUP BY kw
                ORDER BY cnt DESC
                LIMIT $1
                """,
                limit,
            )
            result: list[tuple[str, int]] = []
            for r in rows:
                kw = (r[0] or "").strip()
                if kw:
                    result.append((kw, r[1]))
            return result

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
        """🔒 SEC FIX: `_DB_TIMEZONE` پارامتر bind (۲ جا)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    (timestamp AT TIME ZONE $2)::date
                        AS date,
                    COUNT(*) AS count
                FROM messages
                WHERE keywords <> ''
                  AND timestamp >= NOW()
                      - ($1 || ' days')::interval
                GROUP BY
                    (timestamp AT TIME ZONE $2)::date
                ORDER BY date DESC
                """,
                str(days),
                _DB_TIMEZONE,
            )
            return [
                {"date": str(r[0]), "count": r[1]}
                for r in rows
            ]

    async def search(
        self,
        query: str,
        search_type: str = "all",
        limit: int = 50,
    ) -> list[dict]:
        # 🐛 FIX: query خالی رد شود
        if not query or not query.strip():
            log_warning(
                "⚠️ [DB:pg:search] query خالی — رد شد"
            )
            return []

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
        """🎯 یک کوئری اتمیک."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM messages) AS messages,
                    (SELECT COUNT(*) FROM messages
                     WHERE keywords <> '') AS leads,
                    (SELECT COUNT(DISTINCT chat_name)
                     FROM messages) AS groups
                """
            )
            if not row:
                return {"messages": 0, "leads": 0, "groups": 0}
            return {
                "messages": int(row[0] or 0),
                "leads": int(row[1] or 0),
                "groups": int(row[2] or 0),
            }

    # ═══════════════════════════════════════════
    # generic
    # ═══════════════════════════════════════════
    async def execute(
        self, query: str, params=()
    ) -> _QueryResult:
        """اجرای کوئری — SELECT/PRAGMA از write تشخیص داده می‌شود.

        🐛 FIX: به‌جای `except Exception` بلعنده، نوع query را
        تشخیص می‌دهیم.
        """
        if not query or not query.strip():
            return _QueryResult([])

        t0 = time.monotonic()
        query_stripped = query.strip()
        query_upper = query_stripped.upper()

        # 🐛 FIX: SELECT-like → fetch
        is_read = (
            query_upper.startswith(("SELECT", "WITH", "EXPLAIN", "SHOW"))
        )

        try:
            async with self._pool.acquire() as conn:
                if is_read:
                    rows = await conn.fetch(query, *params)
                    # 🎯 سقف
                    if len(rows) > _MAX_QUERY_ROWS:
                        log_warning(
                            f"⚠️ [DB:pg:execute] >"
                            f"{_MAX_QUERY_ROWS} ردیف | "
                            f"truncated"
                        )
                        rows = rows[:_MAX_QUERY_ROWS]
                    result = _QueryResult(
                        [dict(r) for r in rows]
                    )
                else:
                    # write query
                    await conn.execute(query, *params)
                    result = _QueryResult([])

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_QUERY_MS:
                log_warning(
                    f"🐌 [DB:pg:execute] کند | "
                    f"{elapsed_ms:.0f}ms | "
                    f"{query_stripped[:80]!r}"
                )
            return result
        except Exception as e:
            log_error(
                f"❌ [DB:pg:execute] "
                f"{type(e).__name__}: {e} | "
                f"{query_stripped[:80]!r}"
            )
            raise

    async def close(self):
        """🐛 FIX: await pending tasks + بستن pool."""
        # ─── منتظر taskهای background ───
        if self._pending_tasks:
            pending = list(self._pending_tasks)
            log_info(
                f"⏳ [DB:pg:close] انتظار "
                f"{len(pending)} task pending..."
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
                    "⏰ [DB:pg:close] timeout — cancel"
                )
                for t in pending:
                    if not t.done():
                        t.cancel()
            except Exception as e:
                log_warning(
                    f"⚠️ [DB:pg:close] gather | "
                    f"{type(e).__name__}: {e}"
                )
            self._pending_tasks.clear()

        # ─── بستن pool ───
        if self._pool:
            try:
                await asyncio.wait_for(
                    self._pool.close(), timeout=10.0
                )
            except TimeoutError:
                log_warning(
                    "⏰ [DB:pg:close] pool timeout — "
                    "terminate"
                )
                with contextlib.suppress(Exception):
                    self._pool.terminate()
            except Exception as e:
                log_warning(
                    f"⚠️ [DB:pg:close] pool | "
                    f"{type(e).__name__}: {e}"
                )
            self._pool = None

        log_info("🔌 [DB:pg] بسته شد")