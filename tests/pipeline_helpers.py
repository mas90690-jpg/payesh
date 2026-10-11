# tests/pipeline_helpers.py
"""
Pipeline end-to-end برای تست‌های پلتفرم.

نسخهٔ ۵ (این نسخه):
  - 🐛 FIX: Connection leak در ۴ متد (find_in_db, find_leads_in_db,
    count_messages, count_leads) — حالا try/finally + _safe_close.
  - 🐛 FIX: _FakeEvent حالا module-level است (نه داخل تابع).
    قبلاً برای هر پیام یک کلاس جدید ساخته می‌شد.
  - 🐛 FIX: _FakeEvent حالا __slots__ دارد (کارایی بهتر).
  - 🎯 FIX: import sqlite3 به بالای فایل منتقل شد (نه ۴ بار تکرار).
  - 🐛 FIX: await asyncio.sleep(0.3) حذف شد — غیرضروری بود
    (چون handle() همهٔ DB writes را await می‌کند).
  - 🎯 FIX: docstring feed() حالا با کد هماهنگ است.
  - 🎯 CLEAN: ۲ خط خالی بین top-level تعاریف (PEP 8).
  - ✅ حفظ همهٔ API عمومی (feed, find_in_db, count_messages, ...).

API عمومی:
    async with TestPipeline() as p:
        msgs = await collector.fetch_messages()
        await p.feed(msgs)
        found = await p.find_in_db(msgs)
        total = await p.count_messages(platform="bale")
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ═══════════════════════════════════════════════════════
# helpers داخلی
# ═══════════════════════════════════════════════════════
class _FakeEvent:
    """
    شیء سبک با attribute .data برای StorageHandler.

    StorageHandler.handle() انتظار یک event با attribute .data دارد.
    این کلاس معادل سبک Event واقعی است.
    """

    __slots__ = ("data", "event_type")

    def __init__(self, data: dict) -> None:
        self.data = data
        self.event_type = "message.received"


def _make_event(msg: dict) -> _FakeEvent:
    """ساخت یک _FakeEvent جدید برای پیام داده‌شده."""
    return _FakeEvent(msg)


def _safe_close(conn: sqlite3.Connection | None) -> None:
    """بستن امن یک connection — بدون raise."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# TestPipeline
# ═══════════════════════════════════════════════════════
class TestPipeline:
    """
    Pipeline تست: Collector → StorageHandler → DB (isolated).

    نکته: در این نسخه، StorageHandler مستقیم صدا زده می‌شود (نه از
    طریق EventBus) — چون EventBus ممکن است handler را در task
    پس‌زمینه اجرا کند و await نکند.

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
        allowed_tiers: tuple[str, ...] = ("T1", "T2", "T3"),
    ) -> None:
        self._tmp_dir: str | None = None
        self._db_path: str | None = None
        self._db: Any | None = None
        self._bus: Any | None = None
        self._storage: Any | None = None

        self._keywords_only = keywords_only
        self._require_t1 = require_t1
        self._allowed_tiers = tuple(allowed_tiers)

        self.stats: dict[str, int] = {
            "fed": 0,
            "stored": 0,
            "leads": 0,
            "errors": 0,
        }

    # ═══════════════════════════════════════════════════
    # Context manager
    # ═══════════════════════════════════════════════════
    async def __aenter__(self) -> "TestPipeline":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        await self.stop()

    # ═══════════════════════════════════════════════════
    # start / stop
    # ═══════════════════════════════════════════════════
    async def start(self) -> "TestPipeline":
        """ساخت DB موقت + EventBus + StorageHandler."""
        # ─── ۱) پوشهٔ temp ───
        storage_dir = os.path.join(ROOT, "storage")
        os.makedirs(storage_dir, exist_ok=True)
        self._tmp_dir = tempfile.mkdtemp(
            prefix="test_pipeline_",
            dir=storage_dir,
        )
        self._db_path = os.path.join(self._tmp_dir, "test.db")

        # ─── ۲) ساخت DB موقت ───
        from core.database.sqlite import SQLiteDatabase

        self._db = SQLiteDatabase(db_path=self._db_path)
        await self._db.init()

        # ─── ۳) EventBus (برای API خارجی — feed از آن استفاده نمی‌کند) ───
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

        # subscribe (برای API خارجی — feed مستقیم handle صدا می‌زند)
        self._bus.subscribe(
            "message.received",
            self._storage.handle,
        )

        return self

    async def stop(self) -> None:
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
    # feed
    # ═══════════════════════════════════════════════════
    async def feed(self, msgs: list[dict]) -> int:
        """
        پیام‌ها را مستقیم به StorageHandler می‌فرستد.

        چرا مستقیم (نه EventBus)؟
          چون EventBus ممکن است handler را در task پس‌زمینه صدا بزند
          و await نکند → ممکن است stored=0 اشتباه برگردد.

        Returns:
            تعداد پیام‌های ذخیره‌شده در این فراخوانی (delta).
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
                print(
                    "  ⚠️ pipeline.feed error: "
                    f"{type(e).__name__}: {e}"
                )

        # ─── نیازی به sleep نیست — handle() همهٔ DB writes را await می‌کند ───
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

        Raises:
            ValueError: اگر platform مشخص نباشد و msgs چند پلتفرم داشته باشند.
        """
        if not msgs or not self._db:
            return {}

        # ─── استخراج platform ───
        if not platform:
            platforms = {
                m.get("platform") for m in msgs if m.get("platform")
            }
            if len(platforms) != 1:
                raise ValueError(
                    f"platform باید یکتا باشد: {platforms}"
                )
            platform = platforms.pop()

        # ─── جمع‌آوری idها (خام + با prefix) ───
        prefix = f"{platform}_"
        all_candidates: dict[str, str] = {}
        for m in msgs:
            rid = str(m.get("id", ""))
            if not rid:
                continue
            all_candidates[rid] = rid
            if not rid.startswith(prefix):
                all_candidates[f"{prefix}{rid}"] = rid

        # ─── جستجو در DB ───
        conn = None
        raw_found: dict[str, dict] = {}
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            chunk_size = 500
            candidates = list(all_candidates.keys())
            for i in range(0, len(candidates), chunk_size):
                chunk = candidates[i:i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cur.execute(
                    "SELECT * FROM messages "
                    f"WHERE platform=? AND id IN ({placeholders})",
                    [platform] + chunk,
                )
                for row in cur.fetchall():
                    db_id = str(row["id"])
                    raw_id = all_candidates.get(db_id)
                    if raw_id and raw_id not in raw_found:
                        raw_found[raw_id] = dict(row)
        except Exception as e:
            print(
                "  ⚠️ pipeline.find_in_db error: "
                f"{type(e).__name__}: {e}"
            )
        finally:
            _safe_close(conn)

        return raw_found

    async def find_leads_in_db(
        self, platform: str
    ) -> list[dict]:
        """همهٔ سرنخ‌های یک پلتفرم در DB تست."""
        if not self._db:
            return []

        conn = None
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM messages "
                "WHERE platform=? AND keywords IS NOT NULL "
                "AND keywords != ''",
                (platform,),
            )
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(
                "  ⚠️ pipeline.find_leads_in_db error: "
                f"{type(e).__name__}: {e}"
            )
            return []
        finally:
            _safe_close(conn)

    async def count_messages(
        self, platform: str | None = None
    ) -> int:
        """تعداد پیام‌ها در DB تست."""
        if not self._db:
            return 0

        conn = None
        try:
            conn = sqlite3.connect(self._db_path)
            cur = conn.cursor()
            if platform:
                cur.execute(
                    "SELECT COUNT(*) FROM messages WHERE platform=?",
                    (platform,),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM messages")
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            _safe_close(conn)

    async def count_leads(
        self, platform: str | None = None
    ) -> int:
        """تعداد سرنخ‌ها در DB تست."""
        if not self._db:
            return 0

        conn = None
        try:
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
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            _safe_close(conn)