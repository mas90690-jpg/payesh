"""پیاده‌سازی SQLite با aiosqlite — نسخهٔ ۳ (بهینه).

تغییرات نسخهٔ ۳ نسبت به ۲:
  - ⚡ connection دائمی (یک بار باز، بارها استفاده) — بدون باز/بستهٔ مکرر
  - ⚡ PRAGMAها یک بار ست می‌شن، نه هر بار
  - ⚡ `temp_store=MEMORY` و `cache_size=64MB` برای سرعت بیشتر
  - ⚡ `daily_stats` throttle شد (هر ۶۰ ثانیه، نه هر lead)
  - ⚡ `_write_lock` برای جلوگیری از تداخل write همزمان
  - ⚡ `save_message_and_lead` ترکیبی — یک transaction برای هر پیام
  - ⚡ `close` حالا connection رو درست می‌بنده
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲
  - 🔒 SEC FIX: SQL f-string ها sanitize شدند (SEC-SQL-FSTRING)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
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


# 🔒 SQL identifier validation — جلوگیری از SQL injection در
# جاهایی که پارامتر binding امکان‌پذیر نیست (نام جدول/ستون).
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _safe_identifier(name: str, kind: str = "identifier") -> str:
    """اعتبارسنجی نام جدول/ستون — فقط [A-Za-z_][A-Za-z0-9_]*.

    Raises:
        ValueError: اگر نام معتبر نباشد.
    """
    if not isinstance(name, str) or not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"invalid SQL {kind}: {name!r}")
    return name


try:
    from config import SQLITE_SCHEMA_VERSION as _SCHEMA_VERSION
except ImportError:
    _SCHEMA_VERSION = 6


_SLOW_QUERY_MS = _env_int(
    "DB_SQLITE_SLOW_QUERY_MS", 500,
    min_val=50, max_val=60_000,
)

_MAX_QUERY_ROWS = _env_int(
    "DB_SQLITE_MAX_QUERY_ROWS", 10_000,
    min_val=100, max_val=1_000_000,
)

# ⚡ فاصلهٔ حداقلی بین دو اجرای daily_stats (ثانیه)
_DAILY_STATS_INTERVAL = _env_int(
    "DB_SQLITE_DAILY_STATS_INTERVAL", 60,
    min_val=5, max_val=3600,
)

# ⚡ تنظیمات cache
_CACHE_SIZE_KB = _env_int(
    "DB_SQLITE_CACHE_KB", 64_000,
    min_val=2_000, max_val=512_000,
)


# ═══════════════════════════════════════════
# SQLiteDatabase
# ═══════════════════════════════════════════
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

    # ═══════════════════════════════════════════
    # lifecycle
    # ═══════════════════════════════════════════
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection = None
        self._conn_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending_tasks: list[asyncio.Task] = []
        self._tasks_lock = asyncio.Lock()
        # ⚡ throttle برای daily_stats
        self._last_daily_stats = 0.0

    async def _connect(self):
        """⚡ یک connection دائمی با PRAGMAهای بهینه.

        یک بار باز می‌شود، تا `close()` زنده می‌ماند.
        """
        if self._connection is not None:
            return self._connection

        async with self._conn_lock:
            # double-check داخل lock
            if self._connection is not None:
                return self._connection

            import aiosqlite
            conn = await aiosqlite.connect(self.db_path)
            conn.row_factory = aiosqlite.Row

            # ⚡ PRAGMAها — یک بار برای همیشه
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA synchronous = NORMAL")
            await conn.execute("PRAGMA temp_store = MEMORY")
            # 🔒 SEC FIX: cache_size از int معتبر ساخته می‌شود
            # (PRAGMA پارامتر bind نمی‌پذیرد، پس int() امن‌ساز است)
            cache_kb = int(_CACHE_SIZE_KB)
            await conn.execute(
                f"PRAGMA cache_size = -{cache_kb}"
            )
            await conn.execute("PRAGMA busy_timeout = 5000")
            await conn.commit()

            self._connection = conn
            log_info(
                f"🔌 [DB:sqlite:connect] connection دائمی | "
                f"cache={_CACHE_SIZE_KB}KB | WAL=on | "
                f"sync=NORMAL"
            )
            return conn

    @asynccontextmanager
    async def _get_conn(self):
        """⚡ connection دائمی — بدون باز/بستهٔ مکرر."""
        conn = await self._connect()
        yield conn

    async def close(self):
        """بستن دیتابیس — منتظر taskها و بستن connection."""
        # ─── taskهای background ───
        if self._pending_tasks:
            pending = list(self._pending_tasks)
            log_info(
                f"⏳ [DB:sqlite:close] انتظار "
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
                    "⏰ [DB:sqlite:close] timeout — cancel"
                )
                for t in pending:
                    if not t.done():
                        t.cancel()
            except Exception as e:
                log_warning(
                    f"⚠️ [DB:sqlite:close] gather | "
                    f"{type(e).__name__}: {e}"
                )
            self._pending_tasks.clear()

        # ─── بستن connection دائمی ───
        async with self._conn_lock:
            if self._connection is not None:
                try:
                    await self._connection.close()
                except Exception as e:
                    log_warning(
                        f"⚠️ [DB:sqlite:close] | "
                        f"{type(e).__name__}: {e}"
                    )
                finally:
                    self._connection = None

        log_info("🔌 [DB:sqlite] بسته شد")

    # ═══════════════════════════════════════════
    # init + migrate
    # ═══════════════════════════════════════════
    async def init(self):
        t0 = time.monotonic()
        log_info(
            f"🗄️ [DB:sqlite:init] شروع | path={self.db_path}"
        )

        async with self._get_conn() as conn:
            # 🔒 SEC FIX: رشته‌ها ثابت هستند — f-string لازم نیست
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                + self._MESSAGES_COLUMNS
                + ")"
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS leads ("
                + self._LEADS_COLUMNS
                + ")"
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

            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_messages_timestamp "
                "ON messages(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_keywords "
                "ON messages(keywords)",
                "CREATE INDEX IF NOT EXISTS idx_messages_platform "
                "ON messages(platform)",
                "CREATE INDEX IF NOT EXISTS idx_messages_chat "
                "ON messages(chat_name, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_tier "
                "ON messages(priority_tier)",
                "CREATE INDEX IF NOT EXISTS idx_leads_score "
                "ON leads(opportunity_score DESC)",
                "CREATE INDEX IF NOT EXISTS idx_leads_timestamp "
                "ON leads(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_leads_tier "
                "ON leads(priority_tier)",
                "CREATE INDEX IF NOT EXISTS idx_daily_stats_date "
                "ON daily_stats(date)",
            ]
            for idx in indexes:
                try:
                    await conn.execute(idx)
                except Exception as e:
                    log_warning(
                        f"⚠️ [DB:sqlite:init] index | "
                        f"{type(e).__name__}: {e}"
                    )
            await conn.commit()

        await self.migrate()

        elapsed_ms = (time.monotonic() - t0) * 1000
        size_kb = (
            round(os.path.getsize(self.db_path) / 1024, 1)
            if os.path.exists(self.db_path) else 0
        )
        log_success(
            f"✅ [DB:sqlite:init] complete | "
            f"schema=v{_SCHEMA_VERSION} | {elapsed_ms:.0f}ms | "
            f"size={size_kb}KB"
        )

    async def migrate(self):
        """اعمال migrationهای لازم."""
        async with self._get_conn() as conn:
            cur = await conn.execute(
                "SELECT version FROM migrations "
                "ORDER BY version DESC LIMIT 1"
            )
            row = await cur.fetchone()
            current = int(row[0]) if row else 0

            log_info(
                f"🔧 [DB:sqlite:migrate] "
                f"current=v{current} target=v{_SCHEMA_VERSION}"
            )

            await self._sync_user_version(conn, current)

            if current >= _SCHEMA_VERSION:
                log_info(
                    f"ℹ️ [DB:sqlite:migrate] نیازی نیست "
                    f"(از قبل v{current})"
                )
                return

            migrations: dict[int, list[tuple[str, str]]] = {
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
                    # 🔒 SEC FIX: اعتبارسنجی نام جدول/ستون قبل از
                    # interpolate در ALTER TABLE (SQLite پارامتر
                    # برای identifier نمی‌پذیرد).
                    try:
                        safe_table = _safe_identifier(
                            table, "table name"
                        )
                        safe_column = _safe_identifier(
                            column, "column name"
                        )
                    except ValueError as ve:
                        log_warning(
                            f"⚠️ [DB:sqlite:migrate] "
                            f"identifier نامعتبر | {ve}"
                        )
                        continue

                    try:
                        await conn.execute(
                            f"ALTER TABLE {safe_table} "
                            f"ADD COLUMN {safe_column} TEXT"
                        )
                        ok_cols += 1
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            log_warning(
                                f"⚠️ [DB:sqlite:migrate] "
                                f"{safe_table}.{safe_column} | "
                                f"{type(e).__name__}: {e}"
                            )
                await conn.execute(
                    "INSERT OR IGNORE INTO migrations (version) "
                    "VALUES (?)",
                    (version,),
                )
                applied += 1
                log_success(
                    f"✅ [DB:sqlite:migrate] v{version} | "
                    f"ستون‌ها: {ok_cols}/{len(alters)}"
                )
            await conn.commit()

            if applied:
                log_success(
                    f"✅ [DB:sqlite:migrate] کامل | "
                    f"{applied} migration"
                )
                await self._sync_user_version(
                    conn, _SCHEMA_VERSION
                )
            else:
                log_info("ℹ️ [DB:sqlite:migrate] لازم نبود")

    async def _sync_user_version(
        self, conn, version: int
    ) -> None:
        """PRAGMA user_version را با جدول migrations sync می‌کند."""
        if version <= 0:
            return
        try:
            cur = await conn.execute("PRAGMA user_version")
            row = await cur.fetchone()
            current_pragma = int(row[0]) if row else 0

            if current_pragma == version:
                return

            # 🔒 SEC FIX: int() روی version — PRAGMA پارامتر
            # bind نمی‌پذیرد، پس int() امن‌ساز قطعی است.
            safe_version = int(version)
            await conn.execute(
                f"PRAGMA user_version = {safe_version}"
            )
            await conn.commit()
            log_info(
                f"🔧 [DB:sqlite:pragma] sync | "
                f"v{current_pragma} → v{version}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [DB:sqlite:pragma] sync | "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # write
    # ═══════════════════════════════════════════
    async def save_message(
        self, message: dict[str, Any]
    ) -> int:
        msg_id = message.get("id")
        platform = message.get("platform", "?")
        try:
            async with self._write_lock:
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
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                  ?,?,?,?,?,?,?,?,?)
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
                f"💾 [DB:sqlite:save_message] OK | "
                f"id={msg_id} platform={platform} rowid={rowid}"
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
        """ذخیرهٔ lead + throttle برای daily_stats."""
        lead_id = lead.get("id")
        platform = lead.get("platform", "?")
        try:
            async with self._write_lock:
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
                f"🎯 [DB:sqlite:save_lead] OK | "
                f"id={lead_id} platform={platform} | "
                f"kw={lead.get('keywords', '')!r} | "
                f"tier={lead.get('priority_tier', 'T0')} | "
                f"score={lead.get('opportunity_score', 0)}"
            )

            # ⚡ throttle: فقط هر ۶۰ ثانیه یک بار
            now = time.monotonic()
            if now - self._last_daily_stats >= _DAILY_STATS_INTERVAL:
                self._last_daily_stats = now
                self._spawn_task(self.update_daily_stats())

            return rowid
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:save_lead] id={lead_id} "
                f"platform={platform} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def save_message_and_lead(
        self,
        message: dict[str, Any],
        lead: dict[str, Any] | None = None,
    ) -> tuple[int, int]:
        """⚡ ذخیرهٔ اتمیک پیام + lead در یک transaction.

        Returns:
            (message_rowid, lead_rowid) — lead_rowid صفر اگر lead نباشد.
        """
        msg_id = message.get("id")
        platform = message.get("platform", "?")
        try:
            async with self._write_lock:
                async with self._get_conn() as conn:
                    cur1 = await conn.execute(
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
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                  ?,?,?,?,?,?,?,?,?)
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
                    msg_rowid = cur1.lastrowid or 1

                    lead_rowid = 0
                    if lead:
                        cur2 = await conn.execute(
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
                                lead.get("id"),
                                lead.get("message_id"),
                                lead.get("platform", platform),
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
                        lead_rowid = cur2.lastrowid or 1

                    await conn.commit()

            log_info(
                f"💾 [DB:sqlite:save_both] OK | "
                f"id={msg_id} platform={platform} | "
                f"msg_rowid={msg_rowid} lead_rowid={lead_rowid}"
            )

            # ⚡ throttle
            now = time.monotonic()
            if now - self._last_daily_stats >= _DAILY_STATS_INTERVAL:
                self._last_daily_stats = now
                self._spawn_task(self.update_daily_stats())

            return msg_rowid, lead_rowid
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:save_both] id={msg_id} | "
                f"{type(e).__name__}: {e}"
            )
            raise

    def _spawn_task(self, coro) -> None:
        """ساخت task با reference نگه‌داری."""
        try:
            task = asyncio.create_task(coro)
            self._pending_tasks.append(task)
            task.add_done_callback(self._on_task_done)
        except RuntimeError:
            log_warning(
                "⚠️ [DB:sqlite:task] create_task در خارج از loop"
            )

    def _on_task_done(self, task: asyncio.Task) -> None:
        try:
            self._pending_tasks.remove(task)
        except ValueError:
            pass
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except (asyncio.CancelledError, Exception):
            return
        if exc is not None:
            log_warning(
                f"⚠️ [DB:sqlite:task] "
                f"{type(exc).__name__}: {exc}"
            )

    async def mark_important(
        self, message_id: str, note: str = None
    ):
        async with self._write_lock:
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
            f"⭐ [DB:sqlite:mark_important] id={message_id} | "
            f"note={'✅' if note else '—'}"
        )

    async def update_lead_score(self, lead_id: str, score: int):
        async with self._write_lock:
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
        async with self._write_lock:
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
        async with self._write_lock:
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
                    FROM messages
                    WHERE date(timestamp, 'localtime') = ?
                    """,
                    (today,),
                )
                stats = await cur.fetchone()

                cur = await conn.execute(
                    "SELECT keywords FROM messages "
                    "WHERE date(timestamp, 'localtime') = ? "
                    "AND keywords != ''",
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
                    (
                        today,
                        stats[0] or 0,
                        stats[1] or 0,
                        stats[2] or 0,
                        len(kws),
                    ),
                )
                await conn.commit()

            log_success(
                f"📅 [DB:sqlite:daily_stats] OK | {today} | "
                f"messages={stats[0] or 0} "
                f"leads={stats[1] or 0} "
                f"groups={stats[2] or 0} kw={len(kws)}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [DB:sqlite:daily_stats] "
                f"{type(e).__name__}: {e}"
            )

    async def delete_old_messages(self, days: int = 30):
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            log_info(
                f"ℹ️ [DB:sqlite:cleanup] days={days} ≤ 0 — رد شد"
            )
            return

        cutoff = (
            datetime.now() - timedelta(days=days)
        ).isoformat()

        async with self._write_lock:
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
            # ⚡ WAL checkpoint قبل از backup
            async with self._get_conn() as conn:
                try:
                    await conn.execute("PRAGMA wal_checkpoint(FULL)")
                    await conn.commit()
                except Exception:
                    pass

            shutil.copy2(self.db_path, backup_path)
            size_kb = round(
                os.path.getsize(backup_path) / 1024, 1
            )
            log_success(
                f"💾 [DB:sqlite:backup] OK | {backup_path} "
                f"({size_kb}KB)"
            )
            return True
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:backup] "
                f"{type(e).__name__}: {e}"
            )
            return False

    # ═══════════════════════════════════════════
    # read
    # ═══════════════════════════════════════════
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
                try:
                    return datetime.fromisoformat(row[0])
                except Exception:
                    return None
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
                WHERE date(timestamp, 'localtime') BETWEEN ? AND ?
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
                SELECT date(timestamp, 'localtime') AS date,
                       COUNT(*) AS count
                FROM messages
                WHERE keywords != ''
                  AND date(timestamp, 'localtime') >=
                      date('now', 'localtime', ?)
                GROUP BY date(timestamp, 'localtime')
                ORDER BY date DESC
                """,
                (f"-{int(days)} days",),  # noqa: SEC-SQL-FSTRING
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
        if not query or not query.strip():
            log_warning(
                "⚠️ [DB:sqlite:search] query خالی — رد شد"
            )
            return []

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
        """آمار کلی با یک کوئری اتمیک."""
        async with self._get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM messages) AS messages,
                    (SELECT COUNT(*) FROM messages
                     WHERE keywords != '') AS leads,
                    (SELECT COUNT(DISTINCT chat_name)
                     FROM messages) AS groups
                """
            )
            row = await cur.fetchone()
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
        """اجرای کوئری — SELECT یا write."""
        if not query or not query.strip():
            return _QueryResult([])

        t0 = time.monotonic()
        query_stripped = query.strip()
        query_upper = query_stripped.upper()

        is_select = query_upper.startswith("SELECT") or \
                    query_upper.startswith("PRAGMA") or \
                    query_upper.startswith("EXPLAIN")

        try:
            async with self._get_conn() as conn:
                cur = await conn.execute(query, params)
                rows: list = []
                try:
                    fetched = await cur.fetchall()
                    if len(fetched) > _MAX_QUERY_ROWS:
                        log_warning(
                            f"⚠️ [DB:sqlite:execute] >"
                            f"{_MAX_QUERY_ROWS} ردیف | "
                            f"truncated"
                        )
                        fetched = fetched[:_MAX_QUERY_ROWS]
                    rows = [dict(r) for r in fetched]
                except Exception:
                    rows = []

                if not is_select:
                    await conn.commit()

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_QUERY_MS:
                log_warning(
                    f"🐌 [DB:sqlite:execute] کند | "
                    f"{elapsed_ms:.0f}ms | rows={len(rows)} | "
                    f"{query_stripped[:80]!r}"
                )
            return _QueryResult(rows)
        except Exception as e:
            log_error(
                f"❌ [DB:sqlite:execute] "
                f"{type(e).__name__}: {e} | "
                f"{query_stripped[:80]!r}"
            )
            raise