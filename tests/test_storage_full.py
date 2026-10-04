# tests/test_storage_full.py
"""
تست مستقل StorageHandler — بدون نیاز به scrape.

این تست:
  - یک پیام فیک می‌سازد
  - آن را از طریق StorageHandler.handle() به DB می‌فرستد
  - چک می‌کند که در DB ذخیره شده است

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX بحرانی: متن پیام حالا از یک کلیدواژهٔ واقعی
    (kw.keywords[0]) استفاده می‌کند. قبلاً «کود مایع سپاهان رویش»
    با هیچ‌کدام از ۲۹۳ کلیدواژه match نمی‌شد → تست 0/N fail می‌شد.
  - 🐛 FIX: fallback امن اگه keyword_finder خالی بود.
  - 🎯 گزارش کلیدواژه انتخاب‌شده (برای دیباگ).
  - ✅ حفظ همهٔ مراحل نسخهٔ ۱.

اجرا:
  python tests/test_storage_full.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, section, C,
)
from tests.db_helpers import (
    db_available, db_size_kb,
)


# ═════════════════════════════════════════════════════════
# انتخاب کلیدواژهٔ واقعی از finder
# ═════════════════════════════════════════════════════════
def _pick_real_keyword() -> str:
    """
    یک کلیدواژهٔ واقعی از لیست keyword_finder برمی‌گرداند.

    چرا؟ چون StorageHandler فقط پیام‌هایی را ذخیره می‌کند که
    حداقل یک کلیدواژه از finder داشته باشند. اگه متن تست
    با هیچ کلیدواژه‌ای match نشود، گارد ۳ پیام را رد می‌کند
    و تست شکست می‌خورد.
    """
    try:
        from analyzers.keyword_finder import get_keyword_finder
        kw = get_keyword_finder()
        if kw.keywords:
            return kw.keywords[0]
    except Exception as e:
        print(f"  ⚠️ keyword_finder error: {type(e).__name__}: {e}")

    # fallback امن — ولی احتمالاً با data واقعی match نمی‌شود
    return "کود"


async def main():
    banner("تست مستقل StorageHandler")
    report = TestReport("storage")

    # ═══ ۱) DB اصلی (فقط برای لاگ) ═══
    section("مرحله ۱: چک دسترسی به DB اصلی")
    if db_available():
        report.add("db.main.available", "ok",
                   f"size={db_size_kb():.1f}KB")
    else:
        report.add("db.main.available", "warn",
                   "DB اصلی پیدا نشد")

    # ═══ ۲) ساخت StorageHandler (با DB ایزوله) ═══
    section("مرحله ۲: ساخت StorageHandler با DB ایزوله")
    try:
        from tests.pipeline_helpers import TestPipeline

        pipeline = TestPipeline(
            keywords_only=False,
            require_t1=False,
            allowed_tiers=("T1", "T2"),
        )
        await pipeline.start()
        handler = pipeline._storage
        db = pipeline._db

        report.add("handler.init", "ok",
                   f"name={handler.handler_name}")
    except Exception as e:
        report.add("handler.init", "fail", str(e))
        report.print_summary()
        report.save()
        return False

    # ═══ ۳) پیام فیک با کلیدواژهٔ واقعی ═══
    section("مرحله ۳: ساخت پیام فیک")

    # 🐛 FIX: کلیدواژهٔ واقعی از finder
    real_kw = _pick_real_keyword()
    report.add(
        "keyword.selected", "ok",
        f"«{real_kw}» (از keyword_finder)"
    )
    print(f"\n  🔑 کلیدواژهٔ انتخاب‌شده: «{real_kw}»")

    unique_marker = f"__TEST__{int(time.time())}"
    test_text = (
        f"سلام، {real_kw} موجوده؟ "
        f"قیمت چنده؟ {unique_marker}"
    )

    fake_msg = {
        "id": f"storage_test_{unique_marker}",
        "platform": "storage_test",
        "text": test_text,
        "chat_id": "test_chat_1",
        "chat_name": "تست StorageHandler",
        "chat_type": "group",
        "chat_username": None,
        "chat_link": "https://example.com/test",
        "user_id": "test_user_1",
        "sender_name": "کاربر تست",
        "timestamp": datetime.now(),
        "media_type": "text",
        "file_id": None,
        "local_file_path": None,
        "reply_to": None,
    }
    report.add(
        "fake_msg.created", "ok",
        f"id={fake_msg['id']} | text={test_text[:60]}..."
    )

    # ═══ ۴) چک پیش‌پرواز: آیا متن با finder match می‌شود؟ ═══
    section("مرحله ۴: چک پیش‌پرواز (متن با کلیدواژه)")
    try:
        from analyzers.keyword_finder import get_keyword_finder
        kw = get_keyword_finder()
        matched = kw.match(test_text)
        if matched:
            report.add(
                "kw.prematch", "ok",
                f"متن با {len(matched)} کلیدواژه match شد: "
                f"{matched[:5]}"
            )
        else:
            report.add(
                "kw.prematch", "fail",
                f"متن با هیچ کلیدواژه‌ای match نشد! "
                f"این تست شکست خواهد خورد."
            )
    except Exception as e:
        report.add("kw.prematch", "warn", str(e))

    # ═══ ۵) handle ═══
    section("مرحله ۵: فراخوانی handler.handle()")

    class _FakeEvent:
        def __init__(self, data):
            self.data = data
            self.event_type = "message.received"

    try:
        event = _FakeEvent(fake_msg)
        result = await handler.handle(event)
        report.add(
            "handler.handle", "ok",
            f"result={result} | "
            f"dropped={handler.stats.get('dropped', 0)} | "
            f"saved={handler.stats.get('messages_with_keywords', 0)}"
        )
    except Exception as e:
        report.add("handler.handle", "fail", str(e))

    # ═══ ۶) چک DB تست ═══
    section("مرحله ۶: چک ذخیره‌سازی در DB تست")
    try:
        import sqlite3

        conn = sqlite3.connect(pipeline._db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # جستجو با unique_marker
        cur.execute(
            "SELECT * FROM messages "
            "WHERE platform=? AND text LIKE ? "
            "ORDER BY timestamp DESC LIMIT 1",
            ("storage_test", f"%{unique_marker}%"),
        )
        row = cur.fetchone()

        # اگه پیدا نشد، هر پیام این پلتفرم را بگیر (برای دیباگ)
        fallback_row = None
        if not row:
            cur.execute(
                "SELECT * FROM messages "
                "WHERE platform=? "
                "ORDER BY timestamp DESC LIMIT 1",
                ("storage_test",),
            )
            fallback_row = cur.fetchone()

        conn.close()

        if row:
            stored = dict(row)
            report.add(
                "db.saved", "ok",
                f"id={stored['id']} | "
                f"chat={stored['chat_name']} | "
                f"kw={stored.get('keywords')}"
            )
            print(f"\n  ✅ پیام در DB پیدا شد:")
            print(f"     id={stored['id']}")
            print(f"     chat={stored['chat_name']}")
            print(f"     text={(stored.get('text') or '')[:80]}")
            print(f"     keywords={stored.get('keywords')}")
            print(f"     tier={stored.get('priority_tier')}")

            if stored.get("keywords"):
                report.add("db.keywords", "ok",
                           f"کلیدواژه: {stored['keywords']}")
            else:
                report.add("db.keywords", "warn",
                           "کلیدواژه ذخیره نشده")
        else:
            # ─── دیباگ: چرا ذخیره نشد؟ ───
            detail = f"پیام با {unique_marker} در DB نیست"
            if fallback_row:
                detail += (
                    f" | ولی پیام دیگری در DB هست: "
                    f"id={fallback_row['id']}"
                )
            report.add("db.saved", "fail", detail)

            # ─── اطلاعات دیباگ اضافی ───
            print(f"\n  🔍 دیباگ:")
            print(f"     dropped={handler.stats.get('dropped', 0)}")
            print(f"     total={handler.stats.get('total', 0)}")
            print(
                f"     messages_with_keywords="
                f"{handler.stats.get('messages_with_keywords', 0)}"
            )
            print(
                f"     errors={handler.stats.get('errors', 0)}"
            )
    except Exception as e:
        report.add("db.saved", "fail", str(e))

    # ═══ ۷) آمار DB تست ═══
    section("مرحله ۷: آمار DB تست")
    try:
        import sqlite3
        conn = sqlite3.connect(pipeline._db_path)
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE platform=?",
            ("storage_test",),
        )
        total = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE platform=? AND keywords IS NOT NULL "
            "AND keywords != ''",
            ("storage_test",),
        )
        leads = cur.fetchone()[0]

        cur.execute(
            "SELECT id, chat_name, keywords, priority_tier "
            "FROM messages WHERE platform=? LIMIT 3",
            ("storage_test",),
        )
        samples = cur.fetchall()
        conn.close()

        report.add("db.stats", "ok",
                   f"storage_test: total={total} | leads={leads}")

        if samples:
            print(f"\n  📋 نمونهٔ پیام‌های DB تست:")
            for s in samples:
                print(f"     id={s[0][:30]} | "
                      f"chat={s[1]} | "
                      f"kw={s[2]} | "
                      f"tier={s[3]}")
    except Exception as e:
        report.add("db.stats", "warn", str(e))

    # ═══ ۸) cleanup ═══
    section("مرحله ۸: پاک‌سازی")
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)