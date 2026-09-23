# core/services/daily_reporter.py
"""
گزارش‌ساز خودکار — روزانه، هفتگی، ماهانه، مدیریتی، تخصصی، جستجوی سرنخ.

نسخهٔ ۶ (این نسخه):
  - 🔴 FIX: cache حالا per-file است. قبلاً اگه یک فایل (مثلاً txt)
    دستی پاک می‌شد، کل گزارش از صفر بازتولید می‌شد (شامل SQL + ۴
    نوشتن فایل). حالا فقط فایل‌های پاک‌شده بازتولید می‌شن.

    پیاده‌سازی: تابع `_get_cache_state()` که به‌جای True/False،
    یکی از این سه حالت رو برمی‌گردونه:
      - "hit"     → همهٔ فایل‌ها موجود
      - "partial" → بعضی فایل‌ها پاک شدن (لیست missing برمی‌گرده)
      - "miss"    → cache خالی یا invalid

    و `generate_report` فقط برای missing فایل‌ها writer صدا می‌زنه.

  - 🎯 لاگ دقیق‌تر برای partial cache (کدوم فایل‌ها پاک شدن).

  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import DB_DIR, REPORTS_DIR
from core.kernel.logger import log_error, log_info, log_success, log_warning

os.makedirs(REPORTS_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "messages.db")

# ─── ثابت‌ها ───
_SLOW_REPORT_MS = 10_000
_LOG_PREVIEW_LEN = 80

# ─── timeout برای SQLite (جلوگیری از قفل دائمی) ───
_SQL_TIMEOUT_S = float(os.getenv("REPORT_SQL_TIMEOUT_S", "5"))

# ─── cache path (پایدار بین ری‌استارت‌ها) ───
_CACHE_FILE = os.path.join(DB_DIR, "report_cache.json")
_CACHE_LOCK = threading.Lock()
_CACHE_VERSION = 3   # ← افزایش یافت (ساختار جدید partial cache)

# ─── misfire grace time برای scheduler ───
_MISFIRE_GRACE_S = int(os.getenv("REPORT_MISFIRE_GRACE_S", "600"))

# ─── کلیدهای فایل‌های گزارش ───
_PATH_KEYS = ("txt", "html", "csv", "json")


# ═══════════════════════════════════════════
# ترتیب و برچسب پلتفرم‌ها
# ═══════════════════════════════════════════
PLATFORM_ORDER = [
    "eitaa", "bale", "rubika", "telegram", "whatsapp", "web", "linkedin",
]
PLATFORM_LABELS = {
    "eitaa": "📱 ایتا",
    "bale": "📨 بله",
    "rubika": "🟣 روبیکا",
    "telegram": "✈️ تلگرام",
    "whatsapp": "🟢 واتساپ",
    "web": "🌐 وب",
    "linkedin": "💼 لینکدین",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(
        (platform or "").lower(), f"📍 {platform or 'نامشخص'}"
    )


def platform_rank(platform: str) -> int:
    p = (platform or "").lower()
    return (
        PLATFORM_ORDER.index(p)
        if p in PLATFORM_ORDER
        else len(PLATFORM_ORDER) + 1
    )


def _safe_get(row, key: str, default: Any = "") -> Any:
    """دسترسی امن به ستون row (dict یا sqlite3.Row)."""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def group_by_platform(rows):
    buckets: dict[str, list] = {}
    for r in rows:
        p = (_safe_get(r, "platform", "") or "").lower()
        buckets.setdefault(p, []).append(r)
    return [(p, buckets[p]) for p in sorted(buckets, key=platform_rank)]


def dedup_rows(rows):
    seen = set()
    out = []
    for r in rows:
        chat = _safe_get(r, "chat_name", "") or ""
        txt = str(_safe_get(r, "text", "") or "")[:200].strip()
        key = (chat, txt)
        if txt and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def platform_summary_line(rows) -> str:
    parts = []
    for p, items in group_by_platform(rows):
        parts.append(f"{platform_label(p)} {len(items)}")
    return " | ".join(parts) if parts else "—"


# ═══════════════════════════════════════════
# cache پایدار (thread-safe)
# ═══════════════════════════════════════════
def _load_cache() -> dict[str, Any]:
    with _CACHE_LOCK:
        try:
            if os.path.exists(_CACHE_FILE):
                with open(_CACHE_FILE, encoding="utf-8") as f:
                    data = json.load(f) or {}
                # سازگاری: اگر نسخهٔ قدیمی بود، reset کن
                if data.get("_version") != _CACHE_VERSION:
                    log_info(
                        f"♻️ [REPORT:cache] نسخهٔ قدیمی "
                        f"({data.get('_version')}) → reset"
                    )
                    return {"_version": _CACHE_VERSION}
                return data
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [REPORT:cache] JSON نامعتبر | "
                f"line={e.lineno} | {e.msg}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [REPORT:cache] load | {type(e).__name__}: {e}"
            )
    return {"_version": _CACHE_VERSION}


def _save_cache(cache: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            cache["_version"] = _CACHE_VERSION
            tmp = _CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _CACHE_FILE)
        except Exception as e:
            log_warning(
                f"⚠️ [REPORT:cache] save | {type(e).__name__}: {e}"
            )


def _get_cache_state(
    result: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """
    🔴 FIX نسخهٔ ۶: تشخیص per-file به‌جای all-or-nothing.

    Args:
        result: دیکشنری نتیجه از cache.

    Returns:
        (state, missing_keys):
          - ("hit", [])                  → همهٔ فایل‌ها موجود
          - ("partial", [missing keys])  → بعضی فایل‌ها پاک شدن
          - ("miss", [])                 → cache خالی/invalid
    """
    if not result or not isinstance(result, dict):
        return "miss", []

    missing: list[str] = []
    for key in _PATH_KEYS:
        path = result.get(key)
        # مسیر خالی/None → فایل «در دسترس نیست»
        if not path:
            missing.append(key)
            continue
        if not os.path.exists(path):
            missing.append(key)

    if not missing:
        return "hit", []
    return "partial", missing


def _sqlite_connect(path: str) -> sqlite3.Connection:
    """اتصال SQLite با timeout (جلوگیری از قفل دائمی)."""
    conn = sqlite3.connect(path, timeout=_SQL_TIMEOUT_S)
    try:
        conn.execute("PRAGMA busy_timeout = ?", (int(_SQL_TIMEOUT_S * 1000),))
    except Exception:
        pass
    return conn


# ═══════════════════════════════════════════
# DailyReporter
# ═══════════════════════════════════════════
class DailyReporter:
    """گزارش‌ساز خودکار."""

    def __init__(self, db=None):
        self.db = db
        self.scheduler = AsyncIOScheduler()
        self.db_path = DB_PATH
        self._cache = _load_cache()
        log_info(
            f"📊 [REPORT:init] آماده | db={self.db_path} | "
            f"cache_entries={len(self._cache.get('results', {}))} | "
            f"sql_timeout={_SQL_TIMEOUT_S}s | "
            f"cache_version={_CACHE_VERSION}"
        )

    @staticmethod
    def _filename_suffix(platform: str | None) -> str:
        return f"_{platform}" if platform else ""

    # ═══════════════════════════════════════════
    # گزارش روزانه
    # ═══════════════════════════════════════════
    async def generate_report(
        self, target_date: str = None, platform: str = None
    ) -> dict[str, Any] | None:
        """
        تولید گزارش روزانه.

        🔴 FIX نسخهٔ ۶: حالا per-file cache دارد.
          - "hit": همهٔ فایل‌ها موجود → برمی‌گردونه بدون بازتولید.
          - "partial": فقط فایل‌های پاک‌شده بازتولید می‌شن.
          - "miss": همه از صفر.

        Returns:
            dict با کلیدهای txt/html/csv/json + date + platform + total
            یا None اگر سرنخی نبود.
        """
        t0 = time.monotonic()
        if target_date:
            report_date = target_date
        else:
            report_date = (datetime.now() - timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )

        cache_key = f"{report_date}|{platform or 'all'}"

        # ─── cache check (per-file) ───
        cached_result = self._cache.get("results", {}).get(cache_key)
        state, missing_keys = _get_cache_state(cached_result)

        if state == "hit":
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"💡 [REPORT:daily] cache HIT | {cache_key} | "
                f"{elapsed_ms:.0f}ms"
            )
            return cached_result

        if state == "partial":
            log_info(
                f"🔧 [REPORT:daily] partial cache | {cache_key} | "
                f"پاک‌شده: {missing_keys} → فقط این‌ها بازتولید می‌شن"
            )
        else:
            log_info(
                f"▶️ [REPORT:daily] شروع | date={report_date} | "
                f"platform={platform or 'all'}"
            )

        # ─── واکشی داده از DB (در هر دو حالت partial/miss) ───
        leads_data = await self._get_leads_by_date(
            report_date, platform=platform
        )

        if not leads_data or leads_data["total"] == 0:
            log_info(
                f"ℹ️ [REPORT:daily] بدون سرنخ | {report_date} | "
                f"platform={platform or 'all'}"
            )
            # اگه partial بود، cache رو invalid کن (چون داده‌ها
            # دیگه نیستن ولی cache قدیمی داشتیم)
            if state == "partial":
                self._cache.get("results", {}).pop(cache_key, None)
                _save_cache(self._cache)
            return None

        total = leads_data["total"]
        if state == "partial":
            log_info(
                f"📊 [REPORT:daily] {total} سرنخ — بازتولید "
                f"{len(missing_keys)} فایل"
            )
        else:
            log_info(f"📊 [REPORT:daily] {total} سرنخ پیدا شد")

        # ─── 🔴 FIX: فقط فایل‌های پاک‌شده رو بازتولید کن ───
        prev = cached_result or {}

        txt_file = (
            prev.get("txt")
            if state == "partial" and "txt" not in missing_keys
            else await self._save_text_report(leads_data, report_date, platform)
        )
        html_file = (
            prev.get("html")
            if state == "partial" and "html" not in missing_keys
            else await self._save_html_report(leads_data, report_date, platform)
        )
        csv_file = (
            prev.get("csv")
            if state == "partial" and "csv" not in missing_keys
            else await self._save_csv_report(leads_data, report_date, platform)
        )
        json_file = (
            prev.get("json")
            if state == "partial" and "json" not in missing_keys
            else await self._save_json_report(leads_data, report_date, platform)
        )

        result = {
            "txt": txt_file,
            "html": html_file,
            "csv": csv_file,
            "json": json_file,
            "date": report_date,
            "platform": platform,
            "total": total,
        }

        # ─── ذخیرهٔ cache ───
        self._cache["last_report"] = cache_key
        self._cache["last_report_at"] = datetime.now().isoformat()
        self._cache.setdefault("results", {})[cache_key] = result

        # جلوگیری از رشد بی‌نهایت cache
        results = self._cache.get("results", {})
        if len(results) > 100:
            keys = list(results.keys())
            for k in keys[: len(keys) - 100]:
                results.pop(k, None)

        _save_cache(self._cache)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if state == "partial":
            log_success(
                f"✅ [REPORT:daily] partial-regen کامل | {total} سرنخ | "
                f"regen={len(missing_keys)} | {elapsed_ms:.0f}ms | "
                f"date={report_date}"
            )
        else:
            log_success(
                f"✅ [REPORT:daily] کامل | {total} سرنخ | "
                f"{elapsed_ms:.0f}ms | date={report_date} | "
                f"platform={platform or 'all'}"
            )

        if elapsed_ms > _SLOW_REPORT_MS:
            log_warning(
                f"🐌 [REPORT:daily] کند | {elapsed_ms:.0f}ms "
                f"(آستانه {_SLOW_REPORT_MS}ms)"
            )

        return result

    async def generate_today_report(self, platform: str = None):
        return await self.generate_report(
            datetime.now().strftime("%Y-%m-%d"), platform=platform
        )

    # ═══════════════════════════════════════════
    # گزارش هفتگی
    # ═══════════════════════════════════════════
    async def generate_weekly_report(self, platform: str = None):
        t0 = time.monotonic()
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (
            datetime.now() - timedelta(days=7)
        ).strftime("%Y-%m-%d")
        label = platform_label(platform) if platform else "همه پلتفرم‌ها"

        log_info(
            f"▶️ [REPORT:weekly] شروع | {start_date} → {end_date} | "
            f"platform={label}"
        )

        where = "date(timestamp) BETWEEN ? AND ? AND keywords != ''"
        params: list[Any] = [start_date, end_date]
        if platform:
            where += " AND platform = ?"
            params.append(platform)

        try:
            async with aiosqlite.connect(
                self.db_path, timeout=_SQL_TIMEOUT_S
            ) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute(
                    f"""
                    SELECT id, platform, chat_name, text, keywords, timestamp
                    FROM messages
                    WHERE {where}
                    ORDER BY timestamp DESC
                    """,
                    params,
                )
                messages = await cursor.fetchall()
        except Exception as e:
            log_error(
                f"❌ [REPORT:weekly] query | "
                f"{type(e).__name__}: {e}"
            )
            return None

        if not messages:
            log_info(
                f"ℹ️ [REPORT:weekly] بدون سرنخ | "
                f"{start_date} → {end_date}"
            )
            return None

        suffix = self._filename_suffix(platform)
        filename = os.path.join(
            REPORTS_DIR,
            f"weekly_report{suffix}_{start_date}_to_{end_date}.txt",
        )

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write("=" * 70 + "\n")
                f.write("📊 گزارش هفتگی سرنخ‌های فروش\n")
                f.write(f"بازه: {start_date} تا {end_date}\n")
                if platform:
                    f.write(f"پلتفرم: {label}\n")
                f.write("=" * 70 + "\n")
                f.write(f"📈 کل سرنخ‌ها: {len(messages)}\n")
                f.write(
                    f"🧭 به تفکیک پلتفرم: "
                    f"{platform_summary_line(messages)}\n"
                )
                for plat, rows in group_by_platform(messages):
                    f.write("\n" + "═" * 70 + "\n")
                    f.write(
                        f"{platform_label(plat)} — {len(rows)} سرنخ\n"
                    )
                    f.write("═" * 70 + "\n")
                    for i, msg in enumerate(rows, 1):
                        chat = _safe_get(msg, "chat_name", "?")
                        kw = _safe_get(msg, "keywords", "")
                        ts = _safe_get(msg, "timestamp", "")
                        txt = str(_safe_get(msg, "text", "") or "")[:300]
                        f.write(
                            f"[{i}] گروه: {chat}  |  🔑 {kw}\n"
                        )
                        f.write(f"    🕒 {ts}\n")
                        f.write(f"    💬 {txt}\n")
        except Exception as e:
            log_error(
                f"❌ [REPORT:weekly] write | {type(e).__name__}: {e}"
            )
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:weekly] {len(messages)} سرنخ | "
            f"{elapsed_ms:.0f}ms | {os.path.basename(filename)}"
        )
        return filename

    # ═══════════════════════════════════════════
    # گزارش مدیریتی (sync)
    # ═══════════════════════════════════════════
    def generate_executive_report(
        self, date_from=None, date_to=None, platform: str | None = None
    ) -> str | None:
        """گزارش خلاصه برای مدیرعامل (sync)."""
        t0 = time.monotonic()
        if date_from is None:
            date_from = (
                datetime.now() - timedelta(days=7)
            ).strftime("%Y-%m-%d")
        if date_to is None:
            date_to = datetime.now().strftime("%Y-%m-%d")

        label = platform_label(platform) if platform else "همه پلتفرم‌ها"
        log_info(
            f"▶️ [REPORT:exec] شروع | {date_from} → {date_to} | "
            f"platform={label}"
        )

        where = "keywords != '' AND date(timestamp) BETWEEN ? AND ?"
        base_params: list[Any] = [date_from, date_to]
        if platform:
            where += " AND platform = ?"
            base_params.append(platform)

        try:
            conn = _sqlite_connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT COUNT(*) FROM messages WHERE {where}",
                    base_params,
                )
                row = cur.fetchone()
                total_leads = row[0] if row else 0

                cur.execute(
                    f"""
                    SELECT platform, COUNT(*) FROM messages
                    WHERE {where}
                    GROUP BY platform
                    """,
                    base_params,
                )
                platform_stats = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT chat_name, platform, COUNT(*) AS cnt,
                           MAX(chat_link) AS link
                    FROM messages
                    WHERE {where}
                    GROUP BY chat_name, platform
                    ORDER BY cnt DESC LIMIT 5
                    """,
                    base_params,
                )
                top_groups = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT keywords, COUNT(*) AS cnt FROM messages
                    WHERE {where}
                    GROUP BY keywords
                    ORDER BY cnt DESC LIMIT 10
                    """,
                    base_params,
                )
                top_keywords = cur.fetchall()
            finally:
                conn.close()
        except Exception as e:
            log_error(
                f"❌ [REPORT:exec] query | {type(e).__name__}: {e}"
            )
            return None

        suffix = self._filename_suffix(platform)
        report_file = os.path.join(
            REPORTS_DIR,
            f"executive{suffix}_{date_from}_to_{date_to}.txt",
        )

        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write("📊 گزارش مدیریتی (مدیرعامل)\n")
                f.write(f"بازه: {date_from} تا {date_to}\n")
                if platform:
                    f.write(f"پلتفرم: {platform_label(platform)}\n")
                f.write("=" * 60 + "\n")
                f.write(
                    f"🔹 تعداد کل فرصت‌ها (سرنخ): {total_leads}\n\n"
                )

                f.write("🔸 تفکیک بر اساس پلتفرم:\n")
                for plat, cnt in sorted(
                    platform_stats, key=lambda x: platform_rank(x[0])
                ):
                    f.write(
                        f"   • {platform_label(plat)}: {cnt} سرنخ\n"
                    )
                f.write("\n")

                f.write("🔸 پرسرنخ‌ترین گروه‌ها:\n")
                for row in sorted(
                    top_groups,
                    key=lambda x: (platform_rank(x[1]), -x[2]),
                ):
                    chat, plat, cnt, link = row[0], row[1], row[2], row[3]
                    line = (
                        f"   • {chat} ({platform_label(plat)}): "
                        f"{cnt} سرنخ"
                    )
                    if link:
                        line += f" — 🔗 {link}"
                    f.write(line + "\n")
                f.write("\n")

                f.write("🔸 کلیدواژه‌های داغ:\n")
                for kw, cnt in top_keywords:
                    f.write(f"   • {kw}: {cnt} بار\n")
                f.write("\n")

                f.write(
                    "📌 توصیه: برای مشاهده جزئیات کامل، "
                    "گزارش تخصصی را درخواست کنید.\n"
                )
        except Exception as e:
            log_error(
                f"❌ [REPORT:exec] write | {type(e).__name__}: {e}"
            )
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:exec] {total_leads} سرنخ | "
            f"{elapsed_ms:.0f}ms | {os.path.basename(report_file)}"
        )
        return report_file

    # ═══════════════════════════════════════════
    # گزارش تخصصی (sync)
    # ═══════════════════════════════════════════
    def generate_detailed_report(
        self,
        platform=None,
        chat_name=None,
        keyword=None,
        date_from=None,
        date_to=None,
        limit=200,
    ) -> str | None:
        """گزارش کامل برای کارشناس (sync). خروجی: CSV، TXT، JSON."""
        t0 = time.monotonic()
        log_info(
            f"▶️ [REPORT:detail] شروع | platform={platform or 'all'} | "
            f"chat={chat_name or '—'} | kw={keyword or '—'} | "
            f"limit={limit}"
        )

        query = """
            SELECT id, platform, chat_name, chat_link, chat_username,
                   chat_type, text, timestamp, keywords, media_type,
                   file_id, caption, reply_to
            FROM messages
            WHERE keywords != ''
        """
        params: list[Any] = []
        conditions: list[str] = []

        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if chat_name:
            conditions.append("chat_name LIKE ?")
            params.append(f"%{chat_name}%")
        if keyword:
            conditions.append("keywords LIKE ?")
            params.append(f"%{keyword}%")
        if date_from:
            conditions.append("date(timestamp) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(timestamp) <= ?")
            params.append(date_to)

        if conditions:
            query += " AND " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            conn = _sqlite_connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as e:
            log_error(
                f"❌ [REPORT:detail] query | {type(e).__name__}: {e}"
            )
            return None

        rows = sorted(rows, key=lambda r: platform_rank(r[1]))

        if not rows:
            log_info(
                "ℹ️ [REPORT:detail] بدون نتیجه با فیلترهای انتخابی"
            )
            return None

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = self._filename_suffix(platform)
        csv_file = os.path.join(
            REPORTS_DIR, f"detailed{suffix}_{timestamp_str}.csv"
        )
        txt_file = os.path.join(
            REPORTS_DIR, f"detailed{suffix}_{timestamp_str}.txt"
        )
        json_file = os.path.join(
            REPORTS_DIR, f"detailed{suffix}_{timestamp_str}.json"
        )

        try:
            # CSV
            with open(
                csv_file, "w", newline="", encoding="utf-8-sig"
            ) as f:
                writer = csv.writer(f)
                writer.writerow([
                    "شناسه پیام", "پلتفرم", "نام گروه", "لینک گروه",
                    "یوزرنیم گروه", "نوع گروه", "متن پیام", "زمان",
                    "کلیدواژه‌ها", "نوع رسانه", "فایل ID", "توضیحات",
                    "پاسخ به",
                ])
                for row in rows:
                    writer.writerow(row)

            # TXT
            with open(txt_file, "w", encoding="utf-8") as f:
                f.write("📄 گزارش تخصصی - جزئیات سرنخ‌ها\n")
                f.write(
                    f"تاریخ تولید: "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(f"تعداد سرنخ‌ها: {len(rows)}\n")
                if platform:
                    f.write(f"فیلتر پلتفرم: {platform}\n")
                if chat_name:
                    f.write(f"فیلتر گروه: {chat_name}\n")
                if keyword:
                    f.write(f"فیلتر کلیدواژه: {keyword}\n")
                f.write("=" * 80 + "\n\n")

                for idx, row in enumerate(rows, 1):
                    (
                        msg_id, plat, chat, chat_link, chat_username,
                        chat_type, text, ts, keywords, media_type,
                        file_id, caption, reply_to,
                    ) = row
                    f.write(
                        f"[{idx}] "
                        f"───────────────────────────────────────\n"
                    )
                    f.write(f"🆔 شناسه: {msg_id}\n")
                    f.write(f"📱 پلتفرم: {plat}\n")
                    f.write(f"👥 گروه: {chat}\n")
                    if chat_type:
                        f.write(f"🏷️ نوع: {chat_type}\n")
                    if chat_username:
                        f.write(f"🔖 یوزرنیم: @{chat_username}\n")
                    if chat_link:
                        f.write(f"🔗 لینک گروه: {chat_link}\n")
                    f.write(f"🕒 زمان: {ts}\n")
                    f.write(f"🔑 کلیدواژه‌ها: {keywords}\n")
                    if media_type:
                        f.write(f"📎 نوع پیام: {media_type}\n")
                    if file_id:
                        f.write(f"📁 فایل: {file_id}\n")
                    if caption:
                        f.write(f"📝 توضیحات: {caption}\n")
                    f.write(f"💬 متن پیام:\n{text}\n\n")

            # JSON
            data = []
            for row in rows:
                (
                    msg_id, plat, chat, chat_link, chat_username,
                    chat_type, text, ts, keywords, media_type,
                    file_id, caption, reply_to,
                ) = row
                data.append({
                    "id": msg_id,
                    "platform": plat,
                    "chat_name": chat,
                    "chat_link": chat_link,
                    "chat_username": chat_username,
                    "chat_type": chat_type,
                    "text": text,
                    "timestamp": ts,
                    "keywords": (
                        keywords.split(",") if keywords else []
                    ),
                    "media_type": media_type,
                    "file_id": file_id,
                    "caption": caption,
                    "reply_to": reply_to,
                })
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_error(
                f"❌ [REPORT:detail] write | {type(e).__name__}: {e}"
            )
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:detail] {len(rows)} سرنخ | "
            f"{elapsed_ms:.0f}ms | {os.path.basename(txt_file)}"
        )
        return txt_file

    # ═══════════════════════════════════════════
    # جستجوی سرنخ
    # ═══════════════════════════════════════════
    def search_lead_by_message_id(
        self, message_id: str, platform: str = None
    ):
        """جستجوی یک سرنخ با شناسه پیام و پلتفرم."""
        log_info(
            f"🔍 [REPORT:search] id={message_id} "
            f"platform={platform or 'any'}"
        )
        try:
            conn = _sqlite_connect(self.db_path)
            try:
                if platform:
                    cur = conn.execute(
                        """
                        SELECT id, platform, chat_name, text, timestamp,
                               keywords
                        FROM messages
                        WHERE id = ? AND platform = ?
                        """,
                        (message_id, platform),
                    )
                else:
                    cur = conn.execute(
                        """
                        SELECT id, platform, chat_name, text, timestamp,
                               keywords
                        FROM messages
                        WHERE id = ?
                        """,
                        (message_id,),
                    )
                row = cur.fetchone()
            finally:
                conn.close()
        except Exception as e:
            log_error(
                f"❌ [REPORT:search] {type(e).__name__}: {e}"
            )
            return None

        if row:
            log_success(
                f"✅ [REPORT:search] پیدا شد | {row[1]} | {row[2]}"
            )
            return row
        log_info(f"ℹ️ [REPORT:search] پیدا نشد | id={message_id}")
        return None

    # ═══════════════════════════════════════════
    # داخلی: خواندن سرنخ‌های یک روز
    # ═══════════════════════════════════════════
    async def _get_leads_by_date(
        self, date: str, platform: str | None = None
    ) -> dict[str, Any]:
        where = "date(timestamp) = ? AND keywords != ''"
        params: list[Any] = [date]
        if platform:
            where += " AND platform = ?"
            params.append(platform)

        try:
            async with aiosqlite.connect(
                self.db_path, timeout=_SQL_TIMEOUT_S
            ) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute(
                    f"""
                    SELECT id, platform, chat_name, user_id, text, keywords,
                           media_type, opportunity_score, opportunity_type,
                           timestamp
                    FROM messages
                    WHERE {where}
                    ORDER BY opportunity_score DESC, timestamp DESC
                    """,
                    params,
                )
                messages = await cursor.fetchall()

                cursor = await conn.execute(
                    f"""
                    SELECT chat_name, platform, COUNT(*) AS count
                    FROM messages
                    WHERE {where}
                    GROUP BY chat_name, platform
                    ORDER BY count DESC
                    """,
                    params,
                )
                group_stats = await cursor.fetchall()

                cursor = await conn.execute(
                    f"""
                    SELECT keywords, COUNT(*) AS count
                    FROM messages
                    WHERE {where}
                    GROUP BY keywords
                    ORDER BY count DESC
                    LIMIT 15
                    """,
                    params,
                )
                keyword_stats = await cursor.fetchall()

                return {
                    "date": date,
                    "platform": platform,
                    "total": len(messages),
                    "messages": messages,
                    "group_stats": group_stats,
                    "keyword_stats": keyword_stats,
                }
        except Exception as e:
            log_error(
                f"❌ [REPORT:_leads_by_date] {type(e).__name__}: {e}"
            )
            return {
                "date": date,
                "platform": platform,
                "total": 0,
                "messages": [],
                "group_stats": [],
                "keyword_stats": [],
            }

    @staticmethod
    def _count_top(items, key, limit=8):
        counts: dict[str, int] = {}
        for it in items:
            val = _safe_get(it, key, None)
            if not val:
                continue
            parts = (
                [p.strip() for p in str(val).split(",")]
                if key == "keywords"
                else [str(val)]
            )
            for p in parts:
                if p:
                    counts[p] = counts.get(p, 0) + 1
        return sorted(
            counts.items(), key=lambda x: x[1], reverse=True
        )[:limit]

    # ═══════════════════════════════════════════
    # داخلی: ذخیرهٔ فرمت‌ها
    # ═══════════════════════════════════════════
    async def _save_text_report(self, data, date, platform=None):
        suffix = self._filename_suffix(platform)
        filename = os.path.join(
            REPORTS_DIR, f"daily_report{suffix}_{date}.txt"
        )
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write("=" * 70 + "\n")
                f.write(f"📊 گزارش روزانه سرنخ‌های فروش — {date}\n")
                if platform:
                    f.write(f"پلتفرم: {platform_label(platform)}\n")
                f.write("=" * 70 + "\n")
                f.write(f"📈 کل سرنخ‌ها: {data['total']}\n")
                f.write(
                    f"🧭 به تفکیک پلتفرم: "
                    f"{platform_summary_line(data['messages'])}\n"
                )
                for plat, rows in group_by_platform(data["messages"]):
                    f.write("\n" + "═" * 70 + "\n")
                    f.write(
                        f"{platform_label(plat)} — {len(rows)} سرنخ\n"
                    )
                    f.write("═" * 70 + "\n")
                    top_groups = self._count_top(rows, "chat_name")
                    if top_groups:
                        f.write(
                            "🏆 پرسرنخ‌ترین گروه‌ها: "
                            + "، ".join(
                                f"{g} ({c})" for g, c in top_groups
                            )
                            + "\n"
                        )
                    top_kw = self._count_top(rows, "keywords")
                    if top_kw:
                        f.write(
                            "🔑 کلیدواژه‌های داغ: "
                            + "، ".join(
                                f"{k} ({c})" for k, c in top_kw
                            )
                            + "\n"
                        )
                    f.write("-" * 70 + "\n")
                    for i, msg in enumerate(dedup_rows(rows), 1):
                        chat = _safe_get(msg, "chat_name", "?")
                        kw = _safe_get(msg, "keywords", "")
                        score = _safe_get(msg, "opportunity_score", 0)
                        lvl = _safe_get(msg, "opportunity_type", "")
                        ts = _safe_get(msg, "timestamp", "")
                        txt = str(_safe_get(msg, "text", "") or "")[:300]

                        f.write(
                            f"[{i}] گروه: {chat}  |  🔑 {kw}"
                        )
                        if score:
                            f.write(
                                f"  |  🎯 {lvl or ''} (امتیاز {score})"
                            )
                        f.write(f"\n    🕒 {ts}\n")
                        f.write(f"    💬 {txt}\n")
        except Exception as e:
            log_error(
                f"❌ [REPORT:txt] {type(e).__name__}: {e}"
            )
            return ""
        return filename

    async def _save_html_report(self, data, date, platform=None):
        import html as _html

        suffix = self._filename_suffix(platform)
        filename = os.path.join(
            REPORTS_DIR, f"daily_report{suffix}_{date}.html"
        )
        safe_date = _html.escape(str(date))
        platform_banner = (
            f"<p><strong>پلتفرم:</strong> "
            f"{_html.escape(platform_label(platform))}</p>"
            if platform
            else ""
        )
        head = f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <title>گزارش سرنخ‌ها - {safe_date}</title>
    <style>
        body {{ font-family: Tahoma, Arial, sans-serif; margin: 20px; background: #f0f2f5; }}
        .container {{ max-width: 900px; margin: auto; background: white; padding: 25px; border-radius: 12px; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #27ae60; padding-bottom: 10px; }}
        h2 {{ color: #fff; background: #34495e; padding: 8px 12px; border-radius: 8px; margin-top: 25px; }}
        .stat-box {{ background: #e8f5e9; padding: 15px; border-radius: 10px; margin: 15px 0; text-align: center; }}
        .stat-number {{ font-size: 36px; font-weight: bold; color: #27ae60; }}
        .summary {{ background: #eef3f8; padding: 10px 14px; border-radius: 8px; margin: 10px 0; }}
        .message-card {{ background: #fafafa; padding: 12px; margin: 12px 0; border-radius: 8px; border-right: 3px solid #27ae60; }}
        .kw {{ color: #c0392b; font-weight: bold; }}
        small {{ color: #888; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 گزارش روزانه سرنخ‌های فروش</h1>
        <p><strong>تاریخ:</strong> {safe_date}</p>
        {platform_banner}
        <div class="stat-box">
            <div class="stat-number">{data['total']}</div>
            <div>تعداد کل سرنخ‌ها</div>
        </div>
        <div class="summary">🧭 به تفکیک پلتفرم: {_html.escape(platform_summary_line(data['messages']))}</div>
"""
        body = ""
        for plat, rows in group_by_platform(data["messages"]):
            body += (
                f"\n        <h2>{_html.escape(platform_label(plat))} — "
                f"{len(rows)} سرنخ</h2>"
            )
            for msg in rows:
                chat = _html.escape(
                    str(_safe_get(msg, "chat_name", "") or "")
                )
                kw = _html.escape(
                    str(_safe_get(msg, "keywords", "") or "")
                )
                txt = _html.escape(
                    str(_safe_get(msg, "text", "") or "")[:300]
                )
                ts = _html.escape(
                    str(_safe_get(msg, "timestamp", "") or "")
                )
                body += f"""
        <div class="message-card">
            <strong>{chat}</strong> | <span class="kw">🔑 {kw}</span><br>
            <p>{txt}</p>
            <small>{ts}</small>
        </div>"""
        tail = """
    </div>
</body>
</html>"""
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(head + body + tail)
        except Exception as e:
            log_error(
                f"❌ [REPORT:html] {type(e).__name__}: {e}"
            )
            return ""
        return filename

    async def _save_csv_report(self, data, date, platform=None):
        suffix = self._filename_suffix(platform)
        filename = os.path.join(
            REPORTS_DIR, f"daily_report{suffix}_{date}.csv"
        )
        try:
            with open(
                filename, "w", newline="", encoding="utf-8-sig"
            ) as f:
                writer = csv.writer(f)
                writer.writerow([
                    "پلتفرم", "شناسه", "گروه", "متن", "کلیدواژه", "زمان",
                ])
                for plat, rows in group_by_platform(data["messages"]):
                    for msg in rows:
                        writer.writerow([
                            plat,
                            _safe_get(msg, "id", ""),
                            _safe_get(msg, "chat_name", ""),
                            str(_safe_get(msg, "text", "") or "")[:500],
                            _safe_get(msg, "keywords", ""),
                            _safe_get(msg, "timestamp", ""),
                        ])
        except Exception as e:
            log_error(f"❌ [REPORT:csv] {type(e).__name__}: {e}")
            return ""
        return filename

    async def _save_json_report(self, data, date, platform=None):
        suffix = self._filename_suffix(platform)
        filename = os.path.join(
            REPORTS_DIR, f"daily_report{suffix}_{date}.json"
        )
        by_platform = []
        for plat, rows in group_by_platform(data["messages"]):
            by_platform.append({
                "platform": plat,
                "label": PLATFORM_LABELS.get(plat, plat),
                "count": len(rows),
                "leads": [
                    {
                        "id": _safe_get(msg, "id", ""),
                        "chat_name": _safe_get(msg, "chat_name", ""),
                        "text": str(
                            _safe_get(msg, "text", "") or ""
                        )[:500],
                        "keywords": _safe_get(msg, "keywords", ""),
                        "timestamp": _safe_get(msg, "timestamp", ""),
                    }
                    for msg in rows
                ],
            })
        output = {
            "date": date,
            "platform": platform,
            "total": data["total"],
            "summary": platform_summary_line(data["messages"]),
            "platforms": by_platform,
        }
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_error(f"❌ [REPORT:json] {type(e).__name__}: {e}")
            return ""
        return filename

    # ═══════════════════════════════════════════
    # زمان‌بندی
    # ═══════════════════════════════════════════
    def start(
        self,
        report_hour=8,
        report_minute=0,
        weekly_day="sunday",
        weekly_hour=9,
        weekly_minute=0,
    ):
        """شروع زمان‌بندی گزارش روزانه/هفتگی/ماهانه."""
        try:
            self.scheduler.add_job(
                self.generate_report,
                "cron",
                hour=report_hour,
                minute=report_minute,
                id="daily_report",
                misfire_grace_time=_MISFIRE_GRACE_S,
                replace_existing=True,
            )
            day_map = {
                "saturday": "sat", "sunday": "sun", "monday": "mon",
                "tuesday": "tue", "wednesday": "wed",
                "thursday": "thu", "friday": "fri",
            }
            dow = day_map.get(str(weekly_day).lower(), "sun")
            self.scheduler.add_job(
                self.generate_weekly_report,
                "cron",
                day_of_week=dow,
                hour=weekly_hour,
                minute=weekly_minute,
                id="weekly_report",
                misfire_grace_time=_MISFIRE_GRACE_S,
                replace_existing=True,
            )
            self.scheduler.add_job(
                self.generate_monthly_report,
                "cron",
                day=1,
                hour=report_hour,
                minute=(report_minute + 5) % 60,
                id="monthly_report",
                misfire_grace_time=_MISFIRE_GRACE_S,
                replace_existing=True,
            )
            self.scheduler.start()
            log_success(
                f"📅 [REPORT:schedule] روزانه {report_hour:02d}:"
                f"{report_minute:02d} | هفتگی ({dow}) "
                f"{weekly_hour:02d}:{weekly_minute:02d} | ماهانه (روز ۱) | "
                f"misfire={_MISFIRE_GRACE_S}s"
            )
        except Exception as e:
            log_error(
                f"❌ [REPORT:schedule] {type(e).__name__}: {e}"
            )

    async def generate_monthly_report(
        self, platform: str | None = None
    ) -> dict[str, str | None] | None:
        """
        گزارش ماهانه (۳۰ روز اخیر) — مدیریتی + تخصصی.

        Returns:
            {
                "exec": <مسیر گزارش مدیریتی یا None>,
                "detail": <مسیر گزارش تخصصی یا None>,
                "platform": platform,
            }
            یا None اگر هر دو شکست خوردند.
        """
        t0 = time.monotonic()
        date_from = (
            datetime.now() - timedelta(days=30)
        ).strftime("%Y-%m-%d")
        date_to = datetime.now().strftime("%Y-%m-%d")
        label = platform_label(platform) if platform else "همه پلتفرم‌ها"

        log_info(
            f"▶️ [REPORT:monthly] شروع | {date_from} → {date_to} | "
            f"platform={label}"
        )

        exec_path: str | None = None
        detail_path: str | None = None

        try:
            exec_path = await asyncio.to_thread(
                self.generate_executive_report,
                date_from,
                date_to,
                platform,
            )
        except Exception as e:
            log_error(
                f"❌ [REPORT:monthly:exec] {type(e).__name__}: {e}"
            )

        try:
            detail_path = await asyncio.to_thread(
                self.generate_detailed_report,
                platform=platform,
                date_from=date_from,
                date_to=date_to,
                limit=500,
            )
        except Exception as e:
            log_error(
                f"❌ [REPORT:monthly:detail] {type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:monthly] کامل | {elapsed_ms:.0f}ms | "
            f"exec={'✅' if exec_path else '❌'} | "
            f"detail={'✅' if detail_path else '❌'}"
        )

        if not exec_path and not detail_path:
            log_warning(
                "⚠️ [REPORT:monthly] هیچ گزارشی ساخته نشد"
            )
            return None

        return {
            "exec": exec_path,
            "detail": detail_path,
            "platform": platform,
        }


# ═══════════════════════════════════════════
# توابع سطح بالا (سازگاری عقب‌رو)
# ═══════════════════════════════════════════
def _parse_time(
    t: str, default_h: int, default_m: int
) -> tuple[int, int]:
    """parse ساعت — با چک معقول بودن."""
    try:
        h, m = str(t).split(":")
        h_i, m_i = int(h), int(m)
        if 0 <= h_i <= 23 and 0 <= m_i <= 59:
            return h_i, m_i
        log_warning(
            f"⚠️ [REPORT:parse] '{t}' خارج بازه — "
            f"استفاده از {default_h:02d}:{default_m:02d}"
        )
    except Exception as e:
        log_warning(
            f"⚠️ [REPORT:parse] '{t}' نامعتبر — "
            f"استفاده از {default_h:02d}:{default_m:02d} | "
            f"{type(e).__name__}: {e}"
        )
    return default_h, default_m


async def start_daily_reporter(db):
    """راه‌اندازی گزارش‌ساز خودکار (تنظیمات از .env)."""
    from config import REPORT

    reporter = DailyReporter(db)
    if not REPORT.get("enabled", True):
        log_info("ℹ️ [REPORT] غیرفعال (REPORT_ENABLED=false)")
        return reporter

    dh, dm = _parse_time(REPORT.get("daily_time", "08:00"), 8, 0)
    wh, wm = _parse_time(REPORT.get("weekly_time", "09:00"), 9, 0)
    reporter.start(
        report_hour=dh,
        report_minute=dm,
        weekly_day=REPORT.get("weekly_day", "sunday"),
        weekly_hour=wh,
        weekly_minute=wm,
    )
    return reporter


def generate_executive_report(
    date_from=None, date_to=None, platform: str | None = None
):
    """گزارش مدیریتی (sync) - برای استفاده در ترمینال."""
    reporter = DailyReporter()
    return reporter.generate_executive_report(
        date_from, date_to, platform=platform
    )


def generate_detailed_report(
    platform=None, chat_name=None, keyword=None,
    date_from=None, date_to=None, limit=200,
):
    """گزارش تخصصی (sync) - برای استفاده در ترمینال."""
    reporter = DailyReporter()
    return reporter.generate_detailed_report(
        platform, chat_name, keyword, date_from, date_to, limit
    )


def search_lead(message_id: str, platform: str = None):
    """جستجوی سرنخ با شناسه پیام."""
    reporter = DailyReporter()
    return reporter.search_lead_by_message_id(message_id, platform)


def generate_daily_report():
    """گزارش روزانه (sync) - برای سازگاری با کد قدیمی.

    ⚠️ اگر داخل event loop فعال صدا زده شود، crash می‌کند.
    """
    reporter = DailyReporter()
    return asyncio.run(reporter.generate_report())


def generate_weekly_report():
    """گزارش هفتگی (sync) - برای سازگاری با کد قدیمی.

    ⚠️ اگر داخل event loop فعال صدا زده شود، crash می‌کند.
    """
    reporter = DailyReporter()
    return asyncio.run(reporter.generate_weekly_report())


# ═══════════════════════════════════════════
# اجرای مستقیم
# ═══════════════════════════════════════════
if __name__ == "__main__":
    print("📊 تولید گزارش‌های نمونه...\n")
    generate_executive_report()
    generate_detailed_report(limit=10)