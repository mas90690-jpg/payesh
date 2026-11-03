# tests/pipeline_helpers.py
"""
TestPipeline — Pipeline ایزوله برای تست‌های end-to-end.

این ماژول یک pipeline کامل اما موقت (DB جدا) می‌سازه تا تست‌ها
بتونن بدون دست‌زدن به DB اصلی، جریان واقعی رو شبیه‌سازی کنن:

    Collector → EventBus → StorageHandler → DB (موقت)

API مورد انتظار تست‌ها:
    pipeline = TestPipeline(...)
    await pipeline.start()
    stored = await pipeline.feed(msgs)
    total = await pipeline.count_messages(platform="bale")
    leads = await pipeline.count_leads(platform="bale")
    found = await pipeline.find_in_db(msgs, platform="bale")
    sample = await pipeline.find_leads_in_db("bale")
    await pipeline.stop()

نکته‌های طراحی:
  - DB در storage/tests/ ساخته می‌شه (نه storage/main.db)
  - از همون schema اصلی استفاده می‌کنه (messages, leads)
  - schema اگر نبود، خودش می‌سازه (idempotent)
  - در stop() فایل DB حذف می‌شه
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime
from typing import Any

import aiosqlite

from analyzers.keyword_finder import get_keyword_finder
from analyzers.lead_scorer import get_lead_scorer
from core.kernel.logger import log_info, log_warning


# ═══════════════════════════════════════════════════════════════
# Schema — دقیقاً مطابق DB اصلی (storage/main.db)
# ═══════════════════════════════════════════════════════════════
_SCHEMA_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    text TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    chat_type TEXT,
    chat_username TEXT,
    chat_link TEXT,
    user_id TEXT,
    sender_name TEXT,
    timestamp TEXT,
    media_type TEXT,
    file_id TEXT,
    local_file_path TEXT,
    reply_to TEXT,
    keywords TEXT,
    text_translated TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_platform
    ON messages(platform);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_chat
    ON messages(platform, chat_id);
"""

_SCHEMA_LEADS = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    text TEXT,
    keywords TEXT,
    opportunity_type TEXT,
    score INTEGER DEFAULT 0,
    created_at TEXT,
    FOREIGN KEY(message_id) REFERENCES messages(id)
);
CREATE INDEX IF NOT EXISTS idx_leads_platform
    ON leads(platform);
CREATE INDEX IF NOT EXISTS idx_leads_message
    ON leads(message_id);
"""


class TestPipeline:
    """Pipeline ایزوله با DB موقت — برای تست‌های end-to-end."""

    def __init__(
        self,
        keywords_only: bool = False,
        require_t1: bool = False,
        allowed_tiers: tuple[str, ...] = ("T1", "T2", "T3"),
    ):
        # flagها نگه داشته می‌شن (تست‌ها این‌ها رو پاس می‌دن)، اما
        # منطق ذخیره‌سازی همیشه "همه چیز ذخیره" است — تصمیم نهایی
        # روی DB تستی گرفته می‌شه، نه روی DB واقعی.
        self.keywords_only = bool(keywords_only)
        self.require_t1 = bool(require_t1)
        self.allowed_tiers = tuple(allowed_tiers)

        self._kw = get_keyword_finder()
        try:
            self._scorer = get_lead_scorer()
        except Exception:
            self._scorer = None

        self._db_path: str | None = None
        self._db: aiosqlite.Connection | None = None
        self._started = False
        self._lock = asyncio.Lock()

    # ───────────────────────────────────────────────────────────
    # start / stop
    # ───────────────────────────────────────────────────────────
    async def start(self) -> None:
        """ساخت DB موقت + schema."""
        if self._started:
            return

        # مسیر موقت (پایدار در طول تست، پاک در stop)
        try:
            from config import STORAGE_DIR
            base = os.path.join(STORAGE_DIR, "tests")
        except Exception:
            base = os.path.join(tempfile.gettempdir(), "payesh_tests")

        os.makedirs(base, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._db_path = os.path.join(base, f"test_pipeline_{ts}.db")

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        # schema
        await self._db.executescript(_SCHEMA_MESSAGES)
        await self._db.executescript(_SCHEMA_LEADS)
        await self._db.commit()

        self._started = True
        log_info(f"🧪 [TestPipeline] DB موقت: {os.path.basename(self._db_path)}")

    async def stop(self) -> None:
        """بستن DB + حذف فایل."""
        if not self._started:
            return
        try:
            if self._db:
                await self._db.close()
        except Exception as e:
            log_warning(f"بستن DB تست: {e}")
        finally:
            self._db = None
            # حذف فایل
            if self._db_path and os.path.exists(self._db_path):
                try:
                    os.remove(self._db_path)
                except Exception:
                    pass
            self._db_path = None
            self._started = False
            log_info("🧪 [TestPipeline] DB موقت پاک شد")

    # ───────────────────────────────────────────────────────────
    # feed — ذخیره پیام‌ها در DB تست
    # ───────────────────────────────────────────────────────────
    async def feed(self, msgs: list[dict[str, Any]]) -> int:
        """
        ذخیره پیام‌ها در DB. برای هر پیام:
          1. INSERT OR IGNORE در messages
          2. اگر کلیدواژه داشت → lead ساخته می‌شه
        برمی‌گردونه: تعداد پیام‌هایی که *واقعاً* جدید ذخیره شدن.
        """
        if not self._started or not self._db:
            raise RuntimeError("TestPipeline.start() صدا زده نشده")

        if not msgs:
            return 0

        stored = 0
        async with self._lock:
            for m in msgs:
                try:
                    if await self._insert_message(m):
                        stored += 1
                        # lead (اگر کلیدواژه داشت)
                        await self._maybe_insert_lead(m)
                except Exception as e:
                    log_warning(
                        f"⚠️ [TestPipeline.feed] id={m.get('id','?')[:20]} | "
                        f"{type(e).__name__}: {e}"
                    )
                    continue

            await self._db.commit()

        return stored

    async def _insert_message(self, m: dict[str, Any]) -> bool:
        """INSERT OR IGNORE — True اگر جدید ذخیره شد."""
        mid = str(m.get("id", "")).strip()
        if not mid:
            return False

        ts = m.get("timestamp")
        if isinstance(ts, datetime):
            ts_str = ts.isoformat()
        elif ts:
            ts_str = str(ts)
        else:
            ts_str = datetime.now().isoformat()

        try:
            cur = await self._db.execute(
                """
                INSERT OR IGNORE INTO messages (
                    id, platform, text, chat_id, chat_name,
                    chat_type, chat_username, chat_link,
                    user_id, sender_name, timestamp,
                    media_type, file_id, local_file_path,
                    reply_to, keywords, text_translated,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    str(m.get("platform", "")),
                    str(m.get("text", "")),
                    str(m.get("chat_id", "")),
                    m.get("chat_name"),
                    m.get("chat_type"),
                    m.get("chat_username"),
                    m.get("chat_link"),
                    m.get("user_id"),
                    m.get("sender_name"),
                    ts_str,
                    m.get("media_type"),
                    m.get("file_id"),
                    m.get("local_file_path"),
                    m.get("reply_to"),
                    m.get("keywords"),
                    m.get("text_translated"),
                    datetime.now().isoformat(),
                ),
            )
            return cur.rowcount > 0
        except Exception as e:
            log_warning(f"⚠️ insert message: {e}")
            return False

    async def _maybe_insert_lead(self, m: dict[str, Any]) -> None:
        """اگر پیام کلیدواژه داره، lead بساز (اگر قبلاً نبود)."""
        text = (m.get("text") or "").strip()
        if not text:
            return
        try:
            if not self._kw.has_match(text):
                return
        except Exception:
            return

        mid = str(m.get("id", ""))
        if not mid:
            return

        # opportunity_type
        opp = "unknown"
        score = 0
        if self._scorer is not None:
            try:
                result = self._scorer.score(text)
                if isinstance(result, dict):
                    opp = str(result.get("tier") or result.get("type") or "unknown")
                    score = int(result.get("score") or 0)
            except Exception:
                pass

        # respect allowed_tiers
        if opp not in self.allowed_tiers and opp != "unknown":
            # اگر کاربر فقط T1 می‌خواد و opp T3 است، رد
            if self.require_t1 and opp != "T1":
                return

        lead_id = f"lead_{mid}"
        try:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO leads (
                    id, message_id, platform, chat_id, chat_name,
                    text, keywords, opportunity_type, score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    mid,
                    str(m.get("platform", "")),
                    str(m.get("chat_id", "")),
                    m.get("chat_name"),
                    text,
                    m.get("keywords"),
                    opp,
                    score,
                    datetime.now().isoformat(),
                ),
            )
        except Exception as e:
            log_warning(f"⚠️ insert lead: {e}")

    # ───────────────────────────────────────────────────────────
    # شمارش
    # ───────────────────────────────────────────────────────────
    async def count_messages(self, platform: str) -> int:
        if not self._started or not self._db:
            return 0
        try:
            cur = await self._db.execute(
                "SELECT COUNT(*) FROM messages WHERE platform=?",
                (platform,),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            log_warning(f"⚠️ count_messages: {e}")
            return 0

    async def count_leads(self, platform: str) -> int:
        if not self._started or not self._db:
            return 0
        try:
            cur = await self._db.execute(
                "SELECT COUNT(*) FROM leads WHERE platform=?",
                (platform,),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            log_warning(f"⚠️ count_leads: {e}")
            return 0

    # ───────────────────────────────────────────────────────────
    # find_in_db — آیا این پیام‌ها در DB هستن؟
    # ───────────────────────────────────────────────────────────
    async def find_in_db(
        self,
        msgs: list[dict[str, Any]],
        platform: str,
    ) -> dict[str, dict[str, Any]]:
        """
        برمی‌گردونه: {raw_id: row_dict} برای پیام‌هایی که در DB
        پیدا شدن. id کلید، مقدار، خود رکورد به‌صورت dict.
        """
        if not self._started or not self._db:
            return {}

        out: dict[str, dict[str, Any]] = {}
        for m in msgs:
            mid = str(m.get("id", "")).strip()
            if not mid:
                continue
            try:
                cur = await self._db.execute(
                    "SELECT * FROM messages WHERE id=? AND platform=?",
                    (mid, platform),
                )
                row = await cur.fetchone()
                if row:
                    out[mid] = dict(row)
            except Exception:
                continue
        return out

    async def find_leads_in_db(
        self,
        platform: str,
    ) -> list[dict[str, Any]]:
        """همه leadهای یک پلتفرم در DB تست."""
        if not self._started or not self._db:
            return []
        try:
            cur = await self._db.execute(
                "SELECT * FROM leads WHERE platform=? ORDER BY created_at DESC",
                (platform,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log_warning(f"⚠️ find_leads_in_db: {e}")
            return []

    # ───────────────────────────────────────────────────────────
    # reset — پاک کردن DB تست (برای اجرای چندباره)
    # ───────────────────────────────────────────────────────────
    async def reset(self) -> None:
        if not self._started or not self._db:
            return
        async with self._lock:
            try:
                await self._db.execute("DELETE FROM leads")
                await self._db.execute("DELETE FROM messages")
                await self._db.commit()
            except Exception as e:
                log_warning(f"⚠️ reset: {e}")


# ═══════════════════════════════════════════════════════════════
# alias برای سازگاری (اگر جایی get_pipeline صدا زده شد)
# ═══════════════════════════════════════════════════════════════
def get_test_pipeline(**kwargs) -> TestPipeline:
    return TestPipeline(**kwargs)


__all__ = ["TestPipeline", "get_test_pipeline"]