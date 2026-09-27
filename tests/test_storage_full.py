# tests/test_storage_full.py
"""
تست مستقل StorageHandler — بدون نیاز به scrape.

این تست:
  - یک پیام فیک می‌سازد
  - آن را از طریق StorageHandler.handle() به DB می‌فرستد
  - چک می‌کند که در DB ذخیره شده است

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
    report_db_summary, report_db_final_stats,
)
from tests.db_helpers import (
    connect_db, count_messages, count_leads,
    find_message_by_text,
)


async def main():
    banner("تست مستقل StorageHandler")
    report = TestReport("storage")

    # ۱) DB
    section("مرحله ۱: چک دیتابیس")
    db_info = report_db_summary(report, "storage_test")
    before_total = count_messages()

    # ۲) ساخت StorageHandler
    section("مرحله ۲: ساخت StorageHandler")
    try:
        from core.database.factory import create_database
        from handlers.storage_handler import StorageHandler

        db = create_database()
        await db.init()

        handler = StorageHandler(
            db=db,
            scorer=None,
            enabled=True,
            keywords_only=False,  # برای تست، همه را قبول کن
            require_t1=False,
            allowed_tiers=["T1", "T2"],
            save_leads=True,
            save_media=False,
        )
        report.add("handler.init", "ok",
                   f"name={handler.handler_name}")
    except Exception as e:
        report.add("handler.init", "fail", str(e))
        report.print_summary()
        report.save()
        return False

    # ۳) پیام فیک با کلیدواژه
    section("مرحله ۳: ساخت پیام فیک")
    unique_marker = f"__TEST__{int(time.time())}"
    fake_msg = {
        "id": f"storage_test_{unique_marker}",
        "platform": "storage_test",
        "text": f"سلام، قیمت کود مایع سپاهان رویش چنده؟ {unique_marker}",
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
    report.add("fake_msg.created", "ok",
               f"id={fake_msg['id']} | text={fake_msg['text'][:40]}...")

    # ۴) handle (شبیه‌سازی event bus)
    section("مرحله ۴: فراخوانی handler.handle()")
    try:
        # StorageHandler.handle انتظار یک رویداد دارد
        # بسته به ساختار، ممکن است مستقیم پیام را بگیرد
        result = await handler.handle(
            {"message": fake_msg, "type": "message.received"}
        )
        report.add("handler.handle", "ok",
                   f"result={result} | 200 OK" if result else "None")
    except Exception as e:
        report.add("handler.handle", "fail", str(e))

    # صبر کن تا write تمام شود
    await asyncio.sleep(0.5)

    # ۵) چک DB
    section("مرحله ۵: چک ذخیره‌سازی در دیتابیس")
    try:
        stored = find_message_by_text("storage_test", unique_marker)
        if stored:
            report.add("db.saved", "ok",
                       f"id={stored['id']} | "
                       f"chat={stored['chat_name']} | "
                       f"kw={stored['keywords']}")
            print(f"\n  ✅ پیام در DB پیدا شد:")
            print(f"     id={stored['id']}")
            print(f"     chat={stored['chat_name']}")
            print(f"     text={(stored['text'] or '')[:80]}")
            print(f"     keywords={stored['keywords']}")
            print(f"     tier={stored['tier']}")

            if stored.get("keywords"):
                report.add("db.keywords", "ok",
                           f"کلیدواژه: {stored['keywords']}")
            else:
                report.add("db.keywords", "warn",
                           "کلیدواژه ذخیره نشده")
        else:
            report.add("db.saved", "fail",
                       f"پیام با {unique_marker} در DB نیست")
    except Exception as e:
        report.add("db.saved", "fail", str(e))

    # ۶) آمار
    section("مرحله ۶: آمار نهایی")
    after_total = count_messages()
    added = after_total - before_total
    report.add("db.delta", "ok" if added >= 1 else "warn",
               f"قبل={before_total} | بعد={after_total} | +{added}")

    # ۷) cleanup
    section("مرحله ۷: پاک‌سازی")
    try:
        await db.close()
        report.add("db.close", "ok")
    except Exception as e:
        report.add("db.close", "warn", str(e))

    # آمار نهایی
    report_db_final_stats(report, "storage_test")

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)