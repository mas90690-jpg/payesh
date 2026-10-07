# tests/db_helpers.py
"""
توابع کمکی برای تست دیتابیس در کنار تست‌های پلتفرم.

هدف:
  - بررسی اینکه پیام‌های scrape‌شده واقعاً در DB ذخیره شده‌اند
  - چک تکراری‌ها
  - چک محتوای ذخیره‌شده (کلیدواژه، tier، text)
  - گزارش دقیق

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: Connection leak در ۹ تابع — همهٔ توابع حالا try/finally دارند
    (قبلاً اگر بین connect_db و conn.close خطا رخ می‌داد، conn بسته نمی‌شد).
  - 🐛 FIX: db_available() حالا isfile() استفاده می‌کند (نه exists()).
  - 🐛 FIX: find_message_by_text حالا LIKE wildcards را escape می‌کند.
  - 🎯 NEW: _safe_close(conn) helper برای کاهش تکرار.
  - 🎯 NEW: _escape_like(s) helper.
  - ✅ حفظ همهٔ API نسخهٔ ۲ (تمام توابع و امضاها یکسان).
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
# helpers داخلی
# ═════════════════════════════════════════════════════════
def _safe_close(conn: sqlite3.Connection | None) -> None:
    """بستن امن یک connection — بدون raise."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def _escape_like(s: str) -> str:
    """Escape کردن wildcards در LIKE (برای امنیت کوئری)."""
    return (
        str(s or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


# ═════════════════════════════════════════════════════════
# اتصال
# ═════════════════════════════════════════════════════════
def db_available() -> bool:
    """آیا DB موجود است؟"""
    return os.path.isfile(DB_PATH)


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
    try:
        return os.path.getsize(DB_PATH) / 1024
    except OSError:
        return 0.0


# ═════════════════════════════════════════════════════════
# Debug helpers
# ═════════════════════════════════════════════════════════
def db_list_tables() -> list[str]:
    """لیست همهٔ جدول‌های DB (برای debug)."""
    conn = None
    try:
        conn = connect_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        )
        return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        _safe_close(conn)


def db_table_columns(table: str) -> list[str]:
    """لیست ستون‌های یک جدول (برای debug)."""
    conn = None
    try:
        conn = connect_db()
        cur = conn.cursor()
        # PRAGMA table_info با پارامتر bind نمی‌شود → whitelist
        if not str(table).replace("_", "").isalnum():
            return []
        cur.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        _safe_close(conn)


# ═════════════════════════════════════════════════════════
# schema version
# ═════════════════════════════════════════════════════════
def db_schema_version() -> str:
    """
    نسخهٔ schema — سازگار با جدول‌های واقعی پروژه.

    جدول‌های واقعی: messages, leads, last_poll, chat_offsets,
                    daily_stats, sqlite_sequence, migrations
    """
    conn = None
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
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]

            if "name" in cols:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                r = cur.fetchone()
                if r and r[0]:
                    return f"migrations={r[0]}"

            if "version" in cols:
                cur.execute(
                    f"SELECT version FROM {table} "
                    f"ORDER BY rowid DESC LIMIT 1"
                )
                r = cur.fetchone()
                if r and r[0] is not None:
                    return f"v{r[0]}"

        # ─── ۲) PRAGMA user_version ───
        try:
            cur.execute("PRAGMA user_version")
            r = cur.fetchone()
            if r and r[0]:
                return f"v{r[0]}"
        except Exception:
            pass

        return "no_table"
    except Exception as e:
        return f"error:{type(e).__name__}"
    finally:
        _safe_close(conn)


# ═════════════════════════════════════════════════════════
# شمارش
# ═════════════════════════════════════════════════════════
def count_messages(
    platform: str | None = None,
    since: datetime | None = None,
) -> int:
    """تعداد پیام‌ها (اختیاری: فقط یک پلتفرم، اختیاری: از یک زمان)."""
    conn = None
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
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        _safe_close(conn)


def count_leads(platform: str | None = None) -> int:
    """تعداد سرنخ‌ها (پیام‌های دارای کلیدواژه)."""
    conn = None
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
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        _safe_close(conn)


# ═════════════════════════════════════════════════════════
# جستجو
# ═════════════════════════════════════════════════════════
def find_message_by_text(
    platform: str,
    text_fragment: str,
) -> dict[str, Any] | None:
    """جستجوی یک پیام با fragment متن."""
    conn = None
    try:
        conn = connect_db()
        cur = conn.cursor()
        safe = _escape_like(text_fragment)
        cur.execute(
            "SELECT id, platform, chat_name, text, keywords, "
            "       opportunity_type, timestamp, media_type "
            "FROM messages "
            "WHERE platform=? AND text LIKE ? ESCAPE '\\' "
            "ORDER BY timestamp DESC LIMIT 1",
            (platform, f"%{safe}%"),
        )
        row = cur.fetchone()
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
    finally:
        _safe_close(conn)


def find_by_ids(
    platform: str,
    ids: list[str],
) -> dict[str, dict[str, Any]]:
    """برای مجموعه‌ای از message_idها، پیام‌های موجود در DB را برمی‌گرداند."""
    if not ids:
        return {}
    result: dict[str, dict[str, Any]] = {}
    conn = None
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
    except Exception:
        pass
    finally:
        _safe_close(conn)
    return result


def get_recent_messages(
    platform: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """آخرین پیام‌های یک پلتفرم."""
    conn = None
    try:
        # کلیپ limit
        try:
            limit_int = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit_int = 5

        conn = connect_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, chat_name, text, keywords, timestamp "
            "FROM messages WHERE platform=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (platform, limit_int),
        )
        return [
            {
                "id": r[0],
                "chat_name": r[1],
                "text": (r[2] or "")[:80],
                "keywords": r[3],
                "timestamp": r[4],
            }
            for r in cur.fetchall()
        ]
    except Exception:
        return []
    finally:
        _safe_close(conn)


def get_platforms_stats() -> dict[str, dict[str, int]]:
    """آمار همهٔ پلتفرم‌ها: تعداد کل + تعداد سرنخ."""
    conn = None
    try:
        conn = connect_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT platform, COUNT(*) FROM messages "
            "GROUP BY platform"
        )
        total = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT platform, COUNT(*) FROM messages "
            "WHERE keywords IS NOT NULL AND keywords != '' "
            "GROUP BY platform"
        )
        leads = {r[0]: r[1] for r in cur.fetchall()}

        all_platforms = set(total) | set(leads)
        return {
            p: {"total": total.get(p, 0), "leads": leads.get(p, 0)}
            for p in all_platforms
        }
    except Exception:
        return {}
    finally:
        _safe_close(conn)


# ═════════════════════════════════════════════════════════
# check_ids_in_db — چک هر دو فرم id
# ═════════════════════════════════════════════════════════
def check_ids_in_db(
    platform: str,
    msgs: list[dict[str, Any]],
) -> dict[str, Any]:
    """بررسی می‌کند کدام‌یک از پیام‌ها در DB هستند."""
    if not msgs:
        return {
            "total": 0, "in_db": 0, "missing": 0,
            "ids_in_db": [], "ids_missing": [],
            "details_in_db": {}, "details_missing": [],
        }

    raw_ids = [str(m.get("id", "")) for m in msgs if m.get("id")]
    if not raw_ids:
        return {
            "total": len(msgs), "in_db": 0, "missing": len(msgs),
            "ids_in_db": [], "ids_missing": [],
            "details_in_db": {}, "details_missing": [],
        }

    prefix = f"{platform}_"
    all_candidates: dict[str, str] = {}
    for rid in raw_ids:
        all_candidates[rid] = rid
        if not rid.startswith(prefix):
            prefixed = f"{prefix}{rid}"
            all_candidates[prefixed] = rid

    found = find_by_ids(platform, list(all_candidates.keys()))

    raw_found: dict[str, dict] = {}
    for db_id, row in found.items():
        raw_id = all_candidates.get(db_id)
        if raw_id and raw_id not in raw_found:
            raw_found[raw_id] = row

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