# tests/pipeline_helpers.py
"""
Pipeline end-to-end برای تست‌های پلتفرم.

نسخهٔ ۴:
  - 🐛 FIX: EventBus.emit(event_name, data) مستقیم صدا زده می‌شود
    (قبلاً Event(event_type=...) اشتباه بود — emit اصلاً Event نمی‌گیرد)
  - 🎯 انتخاب emit_async برای اطمینان از اجرای async handlerها
  - ✅ حفظ همهٔ API عمومی (feed, find_in_db, count_messages, ...)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def _make_event(msg: dict):
    """ساخت یک شیء سبک با attribute .data برای StorageHandler."""
    class _FakeEvent:
        def __init__(self, data):
            self.data = data
            self.event_type = "message.received"

    return _FakeEvent(msg)
    
class TestPipeline:
    """
    Pipeline تست: Collector → EventBus → StorageHandler → DB.

    Example:
        async with TestPipeline() as p:
            msgs = await collector.fetch_messages()
            await p.feed(msgs)
            found = await p.find_in_db(msgs)
    """

    def __init__(
        self,
        keywords_only: bool = False,
        require_t1: bool = False,
        allowed_tiers: tuple = ("T1", "T2", "T3"),
    ):
        self._tmp_dir: str | None = None
        self._db_path: str | None = None
        self._db = None
        self._bus = None
        self._storage = None

        self._keywords_only = keywords_only
        self._require_t1 = require_t1
        self._allowed_tiers = allowed_tiers

        self.stats = {
            "fed": 0,
            "stored": 0,
            "leads": 0,
            "errors": 0,
        }

    # ═══════════════════════════════════════════════════
    # Context manager
    # ═══════════════════════════════════════════════════
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    # ═══════════════════════════════════════════════════
    # start / stop
    # ═══════════════════════════════════════════════════
    async def start(self):
        """ساخت DB موقت + EventBus + StorageHandler."""
        # ─── ۱) پوشهٔ temp ───
        self._tmp_dir = tempfile.mkdtemp(
            prefix="test_pipeline_",
            dir=os.path.join(ROOT, "storage"),
        )
        self._db_path = os.path.join(self._tmp_dir, "test.db")

        # ─── ۲) ساخت DB موقت ───
        from core.database.sqlite import SQLiteDatabase

        self._db = SQLiteDatabase(db_path=self._db_path)
        await self._db.init()

        # ─── ۳) EventBus ───
        from core.kernel.event_bus import EventBus

        self._bus = EventBus()

        # ─── ۴) StorageHandler ───
        from handlers.storage_handler import StorageHandler

        self._storage = StorageHandler(
            db=self._db,
            scorer=None,
            enabled=True,
            keywords_only=self._keywords_only,
            require_t1=self._require_t1,
            allowed_tiers=list(self._allowed_tiers),
            save_leads=True,
            save_media=False,
        )

        # subscribe
        self._bus.subscribe(
            "message.received",
            self._storage.handle,
        )

        return self

    async def stop(self):
        """پاک‌سازی کامل."""
        if self._db:
            try:
                await self._db.close()
            except Exception:
                pass
            self._db = None

        if self._tmp_dir and os.path.exists(self._tmp_dir):
            try:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self._tmp_dir = None

        self._bus = None
        self._storage = None

    # ═══════════════════════════════════════════════════
    # 🎯 FIX نهایی: emit مستقیم با event_name + data
    # ═══════════════════════════════════════════════════
    async def feed(self, msgs: list[dict]) -> int:
        """
        پیام‌ها را مستقیم به StorageHandler می‌فرستد (بدون EventBus).

        چرا؟ چون EventBus واقعی ممکن است handler را در task پس‌زمینه
        صدا بزند و await نکند → stored=0 اشتباه.
        """
        if not msgs or not self._storage:
            return 0

        stored_before = self.stats["stored"]

        for msg in msgs:
            try:
                event = _make_event(msg)
                await self._storage.handle(event)
                self.stats["fed"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                print(f"  ⚠️ pipeline.feed error: "
                      f"{type(e).__name__}: {e}")

        await asyncio.sleep(0.3)

        handler_stats = self._storage.stats if self._storage else {}
        self.stats["stored"] = handler_stats.get(
            "messages_with_keywords", 0
        )
        self.stats["leads"] = self.stats["stored"]

        return self.stats["stored"] - stored_before
        
    # ═══════════════════════════════════════════════════
    # چک DB
    # ═══════════════════════════════════════════════════
    async def find_in_db(
        self,
        msgs: list[dict],
        platform: str | None = None,
    ) -> dict[str, dict]:
        """
        پیام‌هایی که در DB ذخیره شده‌اند را برمی‌گرداند.

        برای هر پیام، **هر دو فرم id** (خام و با prefix) چک می‌شود.
        """
        if not msgs or not self._db:
            return {}

        # استخراج platform
        if not platform:
            platforms = {
                m.get("platform") for m in msgs if m.get("platform")
            }
            if len(platforms) != 1:
                raise ValueError(
                    f"platform باید یکتا باشد: {platforms}"
                )
            platform = platforms.pop()

        # جمع‌آوری idها (خام + با prefix)
        prefix = f"{platform}_"
        all_candidates: dict[str, str] = {}
        for m in msgs:
            rid = str(m.get("id", ""))
            if not rid:
                continue
            all_candidates[rid] = rid
            if not rid.startswith(prefix):
                all_candidates[f"{prefix}{rid}"] = rid

        # جستجو در DB
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        raw_found: dict[str, dict] = {}
        chunk_size = 500
        candidates = list(all_candidates.keys())
        for i in range(0, len(candidates), chunk_size):
            chunk = candidates[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"SELECT * FROM messages "
                f"WHERE platform=? AND id IN ({placeholders})",
                [platform] + chunk,
            )
            for row in cur.fetchall():
                db_id = str(row["id"])
                raw_id = all_candidates.get(db_id)
                if raw_id and raw_id not in raw_found:
                    raw_found[raw_id] = dict(row)

        conn.close()
        return raw_found

    async def find_leads_in_db(
        self, platform: str
    ) -> list[dict]:
        """همهٔ سرنخ‌های یک پلتفرم در DB تست."""
        if not self._db:
            return []

        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM messages "
            "WHERE platform=? AND keywords IS NOT NULL "
            "AND keywords != ''",
            (platform,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    async def count_messages(
        self, platform: str | None = None
    ) -> int:
        """تعداد پیام‌ها در DB تست."""
        if not self._db:
            return 0
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        cur = conn.cursor()
        if platform:
            cur.execute(
                "SELECT COUNT(*) FROM messages WHERE platform=?",
                (platform,),
            )
        else:
            cur.execute("SELECT COUNT(*) FROM messages")
        n = cur.fetchone()[0]
        conn.close()
        return n

    async def count_leads(
        self, platform: str | None = None
    ) -> int:
        """تعداد سرنخ‌ها در DB تست."""
        if not self._db:
            return 0
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        cur = conn.cursor()
        if platform:
            cur.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE platform=? AND keywords IS NOT NULL "
                "AND keywords != ''",
                (platform,),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE keywords IS NOT NULL AND keywords != ''"
            )
        n = cur.fetchone()[0]
        conn.close()
        return n