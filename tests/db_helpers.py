# tests/db_helpers.py
"""
توابع کمکی برای تست دیتابیس در کنار تست‌های پلتفرم.

هدف:
  - بررسی اینکه پیام‌های scrape‌شده واقعاً در DB ذخیره شده‌اند
  - چک تکراری‌ها
  - چک محتوای ذخیره‌شده (کلیدواژه، tier، text)
  - گزارش دقیق

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: db_schema_version حالا اسم جدول واقعی را کشف می‌کند
    (schema_version | meta | pragma user_version).
  - 🐛 FIX: check_ids_in_db حالا هر دو فرم id را چک می‌کند:
      * id خام (مثل 69907bc6c653ff67 — از کلکتور)
      * id با prefix (مثل bale_69907bc6c653ff67 — از StorageHandler)
    این باعث می‌شد تست بله با «0/9 پیام در DB» شکست بخورد.
  - 🎯 NEW: db_list_tables برای debug.
  - 🎯 NEW: db_table_columns برای debug.
  - ✅ حفظ همهٔ توابع نسخهٔ ۱.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ─── مسیر DB ───
DB_PATH = os.path.join(ROOT, "storage", "database", "messages.db")


# ═════════════════════════════════════════════════════════
# اتصال
# ═════════════════════════════════════════════════════════
def db_available() -> bool:
    """آیا DB موجود است؟"""
    return os.path.exists(DB_PATH)


def connect_db() -> sqlite3.Connection:
    """اتصال به DB با timeout."""
    if not db_available():
        raise FileNotFoundError(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def db_size_kb() -> float:
    """حجم DB به کیلوبایت."""
    if not db_available():
        return 0.0
    return os.path.getsize(DB_PATH) / 1024


# ═════════════════════════════════════════════════════════
# 🆕 Debug helpers
# ═════════════════════════════════════════════════════════
def db_list_tables() -> list[str]:
    """لیست همهٔ جدول‌های DB (برای debug)."""
    try:
        conn = connect_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        conn.close()
        return tables
    except Exception:
        return []


def db_table_columns(table: str) -> list[str]:
    """لیست ستون‌های یک جدول (برای debug)."""
    try:
        conn = connect_db()
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        conn.close()
        return cols
    except Exception:
        return []


# ═════════════════════════════════════════════════════════
# 🔧 FIX: schema version — کشف جدول واقعی
# ═════════════════════════════════════════════════════════
def db_schema_version() -> str:
    """
    نسخهٔ schema — سازگار با جدول‌های واقعی پروژه.

    جدول‌های واقعی: messages, leads, last_poll, chat_offsets,
                    daily_stats, sqlite_sequence, migrations
    """
    try:
        conn = connect_db()
        cur = conn.cursor()

        # ─── ۱) جدول migrations (اسم واقعی پروژه) ───
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN "
            "('migrations', 'schema_version', 'meta')"
        )
        row = cur.fetchone()
        if row:
            table = row[0]
            # چک ستون‌ها
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]

            # حالت migrations: name + applied_at
            if "name" in cols:
                cur.execute(
                    f"SELECT COUNT(*) FROM {table}"
                )
                r = cur.fetchone()
                if r and r[0]:
                    conn.close()
                    return f"migrations={r[0]}"

            # حالت schema_version: version
            if "version" in cols:
                cur.execute(
                    f"SELECT version FROM {table} "
                    f"ORDER BY rowid DESC LIMIT 1"
                )
                r = cur.fetchone()
                if r and r[0] is not None:
                    conn.close()
                    return f"v{r[0]}"

        # ─── ۲) PRAGMA user_version ───
        try:
            cur.execute("PRAGMA user_version")
            r = cur.fetchone()
            if r and r[0]:
                conn.close()
                return f"v{r[0]}"
        except Exception:
            pass

        conn.close()
        return "no_table"
    except Exception as e:
        return f"error:{type(e).__name__}"
# ═════════════════════════════════════════════════════════
# شمارش
# ═════════════════════════════════════════════════════════
def count_messages(
    platform: str | None = None,
    since: datetime | None = None,
) -> int:
    """تعداد پیام‌ها (اختیاری: فقط یک پلتفرم، اختیاری: از یک زمان)."""
    try:
        conn = connect_db()
        cur = conn.cursor()
        sql = "SELECT COUNT(*) FROM messages WHERE 1=1"
        params: list[Any] = []
        if platform:
            sql += " AND platform=?"
            params.append(platform)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def count_leads(platform: str | None = None) -> int:
    """تعداد سرنخ‌ها (پیام‌های دارای کلیدواژه)."""
    try:
        conn = connect_db()
        cur = conn.cursor()
        sql = (
            "SELECT COUNT(*) FROM messages "
            "WHERE keywords IS NOT NULL AND keywords != ''"
        )
        params: list[Any] = []
        if platform:
            sql += " AND platform=?"
            params.append(platform)
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# ═════════════════════════════════════════════════════════
# جستجو
# ═════════════════════════════════════════════════════════
def find_message_by_text(
    platform: str,
    text_fragment: str,
) -> dict[str, Any] | None:
    """جستجوی یک پیام با fragment متن."""
    try:
        conn = connect_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, platform, chat_name, text, keywords, "
            "       opportunity_type, timestamp, media_type "
            "FROM messages "
            "WHERE platform=? AND text LIKE ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (platform, f"%{text_fragment}%"),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "platform": row[1],
            "chat_name": row[2],
            "text": row[3],
            "keywords": row[4],
            "tier": row[5],
            "timestamp": row[6],
            "media_type": row[7],
        }
    except Exception:
        return None


def find_by_ids(
    platform: str,
    ids: list[str],
) -> dict[str, dict[str, Any]]:
    """
    برای مجموعه‌ای از message_idها، پیام‌های موجود در DB را برمی‌گرداند.

    Returns:
        dict[id, {row}] — فقط idهایی که در DB هستند.
    """
    if not ids:
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        conn = connect_db()
        cur = conn.cursor()
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"SELECT id, platform, chat_name, text, keywords, "
                f"       opportunity_type, timestamp, media_type "
                f"FROM messages "
                f"WHERE platform=? AND id IN ({placeholders})",
                [platform] + chunk,
            )
            for row in cur.fetchall():
                result[str(row[0])] = {
                    "id": row[0],
                    "platform": row[1],
                    "chat_name": row[2],
                    "text": row[3],
                    "keywords": row[4],
                    "tier": row[5],
                    "timestamp": row[6],
                    "media_type": row[7],
                }
        conn.close()
    except Exception:
        pass
    return result


def get_recent_messages(
    platform: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """آخرین پیام‌های یک پلتفرم."""
    try:
        conn = connect_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, chat_name, text, keywords, timestamp "
            "FROM messages WHERE platform=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (platform, limit),
        )
        rows = [
            {
                "id": r[0],
                "chat_name": r[1],
                "text": (r[2] or "")[:80],
                "keywords": r[3],
                "timestamp": r[4],
            }
            for r in cur.fetchall()
        ]
        conn.close()
        return rows
    except Exception:
        return []


def get_platforms_stats() -> dict[str, dict[str, int]]:
    """آمار همهٔ پلتفرم‌ها: تعداد کل + تعداد سرنخ."""
    try:
        conn = connect_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT platform, COUNT(*) FROM messages GROUP BY platform"
        )
        total = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT platform, COUNT(*) FROM messages "
            "WHERE keywords IS NOT NULL AND keywords != '' "
            "GROUP BY platform"
        )
        leads = {r[0]: r[1] for r in cur.fetchall()}

        conn.close()

        all_platforms = set(total) | set(leads)
        return {
            p: {"total": total.get(p, 0), "leads": leads.get(p, 0)}
            for p in all_platforms
        }
    except Exception:
        return {}


# ═════════════════════════════════════════════════════════
# 🔧 FIX: check_ids_in_db — چک هر دو فرم id
# ═════════════════════════════════════════════════════════
def check_ids_in_db(
    platform: str,
    msgs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    برای یک لیست پیام (که از scrape آمده)، بررسی می‌کند کدام‌ها در DB هستند.

    🎯 FIX نسخهٔ ۲: StorageHandler ممکن است id را با prefix platform_
    ذخیره کند (مثل `bale_69907bc6c653ff67`)، در حالی که کلکتور
    id خام می‌دهد (مثل `69907bc6c653ff67`).

    این تابع هر دو فرم را چک می‌کند و نتیجه را برمی‌گرداند.

    Returns:
        {
            "total": int,
            "in_db": int,
            "missing": int,
            "ids_in_db": list,       # raw_idهایی که در DB پیدا شدند
            "ids_missing": list,     # raw_idهایی که پیدا نشدند
            "details_in_db": dict,
            "details_missing": list,
        }
    """
    # ─── حالت خالی ───
    if not msgs:
        return {
            "total": 0, "in_db": 0, "missing": 0,
            "ids_in_db": [], "ids_missing": [],
            "details_in_db": {}, "details_missing": [],
        }

    # ─── جمع‌آوری raw_idها ───
    raw_ids = [str(m.get("id", "")) for m in msgs if m.get("id")]
    if not raw_ids:
        return {
            "total": len(msgs), "in_db": 0, "missing": len(msgs),
            "ids_in_db": [], "ids_missing": [],
            "details_in_db": {}, "details_missing": [],
        }

    # ─── ساخت کاندیدها (خام + با prefix) ───
    prefix = f"{platform}_"
    # mapping: candidate_db_id → raw_id
    all_candidates: dict[str, str] = {}
    for rid in raw_ids:
        all_candidates[rid] = rid           # خام
        if not rid.startswith(prefix):
            prefixed = f"{prefix}{rid}"
            all_candidates[prefixed] = rid  # با prefix

    # ─── جستجو در DB ───
    found = find_by_ids(platform, list(all_candidates.keys()))

    # ─── mapping: raw_id → found row ───
    raw_found: dict[str, dict] = {}
    for db_id, row in found.items():
        raw_id = all_candidates.get(db_id)
        if raw_id and raw_id not in raw_found:
            raw_found[raw_id] = row

    # ─── شمارش ───
    ids_in_db = list(raw_found.keys())
    ids_missing = [i for i in raw_ids if i not in raw_found]

    return {
        "total": len(raw_ids),
        "in_db": len(ids_in_db),
        "missing": len(ids_missing),
        "ids_in_db": ids_in_db,
        "ids_missing": ids_missing[:20],
        "details_in_db": {
            k: {
                "chat_name": raw_found[k].get("chat_name"),
                "text": (raw_found[k].get("text") or "")[:60],
                "keywords": raw_found[k].get("keywords"),
                "db_id": raw_found[k].get("id"),
            }
            for k in list(raw_found.keys())[:5]
        },
        "details_missing": ids_missing[:5],
    }