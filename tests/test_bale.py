"""
تست قوی بله — کل مسیر از اتصال تا ذخیره در DB.

اجرا:
    python tests/test_bale_full.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from config import STORAGE_DIR
from core.kernel.logger import log_info, log_success, log_warning, log_error


DB_PATH = os.path.join(STORAGE_DIR, "database", "messages.db")


def db_count_before():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM messages")
    n = cur.fetchone()[0]
    conn.close()
    return n


def db_count_after():
    return db_count_before()


def db_recent(limit=5):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT platform, chat_name, substr(text,1,50), timestamp "
        "FROM messages ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


async def main():
    from collectors.bale import BaleCollector
    from analyzers.keyword_finder import get_keyword_finder

    # ─── ۱) شمارش قبل ───
    before = db_count_before()
    log_info(f"📊 تعداد پیام‌های DB قبل از تست: {before}")

    # ─── ۲) کلیدواژه‌ها ───
    kf = get_keyword_finder()
    log_info(f"🔑 {len(kf.keywords)} کلیدواژه بارگذاری شده")

    # چک کن «دلفان پلاس» توی کلیدواژه‌هاست
    test_text = "قیمت دلفان پلاس برای ۱۰ هکتار چنده؟"
    matches = kf.match(test_text)
    log_info(f"🧪 تست match روی «{test_text[:40]}...» → {matches}")
    if not matches:
        log_error("❌ کلیدواژه «دلفان پلاس» تشخیص داده نشد!")
    else:
        log_success(f"✅ کلیدواژه تشخیص داده شد: {matches}")

    # ─── ۳) اتصال به بله ───
    phone = os.getenv("BALE_PHONE")
    log_info(f"📱 BALE_PHONE = {phone}")

    collector = BaleCollector(phone=phone)
    log_info("🔄 اتصال به بله...")
    ok = await collector.connect()
    if not ok:
        log_error(f"❌ اتصال ناموفق: {collector.get_last_error()}")
        return
    log_success("✅ متصل شد")

    # ─── ۴) انتظار برای پیام ───
    log_info("")
    log_info("=" * 60)
    log_info("⏳ ۶۰ ثانیه صبر می‌کنم — حالا از گوشیت پیام بفرست")
    log_info("   پیام باید «دلفان پلاس» داشته باشه")
    log_info("=" * 60)

    got_message = False
    for i in range(60):
        await asyncio.sleep(1)
        msgs = await collector.fetch_messages()
        if msgs:
            log_success(f"📩 {len(msgs)} پیام گرفتم!")
            for m in msgs:
                log_info(f"   ├ پلتفرم: {m.get('platform')}")
                log_info(f"   ├ گروه: {m.get('chat_name')}")
                log_info(f"   ├ فرستنده: {m.get('user_id')}")
                log_info(f"   ├ متن: {m.get('text', '')[:80]}")
                log_info(f"   ├ کلیدواژه‌ها: {m.get('keywords', '?')}")
                log_info(f"   └ زمان: {m.get('timestamp')}")
            got_message = True
            break
        if i % 10 == 0 and i > 0:
            log_info(f"   ... {i}s گذشته، هنوز پیامی نیومده")

    if not got_message:
        log_warning("⚠️ توی ۶۰ ثانیه هیچ پیامی نیومد")

    # ─── ۵) قطع ───
    await collector.disconnect()
    log_info("🔌 قطع شد")

    # ─── ۶) چک DB ───
    await asyncio.sleep(2)
    after = db_count_after()
    log_info(f"📊 تعداد پیام‌های DB بعد از تست: {after}")
    log_info(f"📊 اختلاف: {after - before}")

    log_info("")
    log_info("=" * 60)
    log_info("📋 ۵ پیام آخر DB:")
    log_info("=" * 60)
    for r in db_recent(5):
        log_info(f"   {r[0]:8} | {r[1][:20]:20} | {r[3]} | {r[2]}")

    if after > before:
        log_success(f"✅ {after - before} پیام جدید توی DB ذخیره شد!")
    else:
        log_warning("⚠️ هیچ پیام جدیدی توی DB ذخیره نشد")


if __name__ == "__main__":
    asyncio.run(main())