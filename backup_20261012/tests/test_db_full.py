# tests/test_db_full.py
"""
تست کامل Database — بررسی سلامت DB اصلی + جداول + schema.

نسخهٔ ۱.۱ (این نسخه):
  - 🐛 FIX: db.schema حالا status را بر اساس نتیجهٔ واقعی تعیین می‌کند
    (قبلاً همیشه "ok" بود، حتی اگر schema_version خطا برمی‌گرداند).
  - 🐛 FIX: import sqlite3 حذف شد (استفاده نمی‌شد — از connect_db می‌آید).
  - 🎯 بهبود: نمونهٔ پیام‌ها حالا پیام راهنما نشان می‌دهد اگر stats خالی باشد.
  - ✅ حفظ همهٔ مراحل نسخهٔ ۱.

اجرا:
  python tests/test_db_full.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import TestReport, banner, section
from tests.db_helpers import (
    DB_PATH, db_available, connect_db, db_size_kb,
    db_schema_version, db_list_tables, db_table_columns,
    count_messages, count_leads, get_platforms_stats,
    get_recent_messages,
)


_EXPECTED_TABLES = {
    "messages", "leads", "last_poll",
    "chat_offsets", "daily_stats", "migrations",
}


def main() -> bool:
    banner("تست کامل Database")
    report = TestReport("db")

    # ═══ ۱) وجود DB ═══
    section("مرحله ۱: بررسی وجود DB")
    if not db_available():
        report.add("db.exists", "fail", f"پیدا نشد: {DB_PATH}")
        report.print_summary()
        report.save()
        return False
    report.add("db.exists", "ok", f"{db_size_kb():.1f}KB")

    # ═══ ۲) نسخهٔ schema ═══
    section("مرحله ۲: نسخهٔ schema")
    schema_ver = db_schema_version()
    schema_ok = (
        schema_ver.startswith("v")
        or schema_ver.startswith("migrations=")
        or schema_ver.isdigit()
    )
    report.add(
        "db.schema",
        "ok" if schema_ok else "warn",
        f"version={schema_ver}",
    )

    # ═══ ۳) جداول ═══
    section("مرحله ۳: جداول")
    tables = db_list_tables()
    report.add("db.tables", "ok", f"{len(tables)} جدول")
    missing = _EXPECTED_TABLES - set(tables)
    if missing:
        report.add(
            "db.tables_expected", "warn",
            f"missing={sorted(missing)}",
            items=sorted(missing),
        )
    else:
        report.add("db.tables_expected", "ok", "همه موجود")

    # ═══ ۴) ستون‌های مهم ═══
    section("مرحله ۴: ستون‌های مهم")
    for t in ("messages", "leads"):
        cols = db_table_columns(t)
        report.add(f"db.cols.{t}", "ok", f"{len(cols)} ستون")

    # ═══ ۵) آمار کلی ═══
    section("مرحله ۵: آمار")
    report.add("db.total_messages", "ok", f"{count_messages()}")
    report.add("db.total_leads", "ok", f"{count_leads()}")

    # ═══ ۶) به تفکیک پلتفرم ═══
    section("مرحله ۶: به تفکیک پلتفرم")
    stats = get_platforms_stats()
    if not stats:
        report.add("db.platforms", "info", "هیچ پلتفرمی نیست")
    else:
        report.add("db.platforms", "ok", f"{len(stats)} پلتفرم")
        for p in sorted(stats.keys()):
            print(f"     {p:12s} | total={stats[p]['total']:>5d} | "
                  f"leads={stats[p]['leads']:>5d}")

    # ═══ ۷) نمونهٔ پیام‌ها ═══
    section("مرحله ۷: نمونهٔ پیام‌ها")
    if stats:
        for p in list(stats.keys())[:2]:
            recent = get_recent_messages(p, limit=2)
            for r in recent:
                print(f"     [{r['timestamp']}] {r['chat_name']}: "
                      f"{r['text'][:50]}")
    else:
        print("     (هیچ پلتفرمی برای نمایش نمونه نیست)")

    # ═══ ۸) یکپارچگی ═══
    section("مرحله ۸: یکپارچگی")
    conn = connect_db()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE platform IS NULL OR platform=''"
        )
        n = cur.fetchone()[0]
        report.add(
            "db.no_platform",
            "ok" if n == 0 else "warn",
            f"{n} پیام بدون پلتفرم",
        )

        cur.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT id FROM messages "
            "  GROUP BY id HAVING COUNT(*) > 1"
            ")"
        )
        n = cur.fetchone()[0]
        report.add(
            "db.no_dup_ids",
            "ok" if n == 0 else "fail",
            f"{n} id تکراری",
        )

        cur.execute(
            "SELECT COUNT(*) FROM leads "
            "WHERE keywords IS NULL OR keywords=''"
        )
        n = cur.fetchone()[0]
        report.add(
            "db.leads_have_kw",
            "ok" if n == 0 else "warn",
            f"{n} سرنخ بدون کلیدواژه",
        )
    finally:
        conn.close()

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
    