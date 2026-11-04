"""گزارش‌ساز خودکار — روزانه، هفتگی، ماهانه، مدیریتی، تخصصی،
جستجوی سرنخ.

نسخهٔ ۱۰ (این نسخه):
  - 🎯 تیترهای گزارش حالا از `data/configs/report_headers.json`
    می‌آیند (تمرکز داده‌ها).
  - 🎯 `_hdr()` helper جدید با fallback امن.
  - 🎯 `REPORT_HEADERS` + `reload_report_headers()` + `get_headers_source()`.
  - 🎯 `get_stats()` حالا `headers_count` و `headers_source` دارد.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۹ (SEC FIX SQL).

نسخهٔ ۹ (قبلی):
  - 🔒 SEC FIX: حذف f-string از SQL query ها.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۸.
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

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(
    name: str,
    default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# ═══════════════════════════════════════════
# مسیرها (از data.paths — منبع یکتا)
# ═══════════════════════════════════════════
try:
    from data import paths as _data_paths

    DB_PATH: str = str(_data_paths.DB_FILE)
    REPORTS_DIR: str = str(_data_paths.REPORTS_DIR)
    _CACHE_FILE: str = str(_data_paths.REPORT_CACHE)
except Exception:
    from config import DB_DIR, REPORTS_DIR as _CFG_REPORTS

    DB_PATH = os.path.join(str(DB_DIR), "messages.db")
    REPORTS_DIR = str(_CFG_REPORTS)
    _CACHE_FILE = os.path.join(os.path.dirname(DB_PATH), "report_cache.json")


# ─── اطمینان از وجود پوشه‌ها (با try/except) ───
for _d in (REPORTS_DIR, os.path.dirname(DB_PATH), os.path.dirname(_CACHE_FILE)):
    if _d:
        try:
            os.makedirs(_d, exist_ok=True)
        except Exception:
            pass


# ═══════════════════════════════════════════
# 🆕 تیترهای گزارش (از data/configs/report_headers.json)
# ═══════════════════════════════════════════

# ─── Fallback: اگه فایل نبود یا خراب بود ───
_FALLBACK_REPORT_HEADERS: dict[str, str] = {
    "daily_title": "📊 گزارش روزانه سرنخ‌های فروش — {date}",
    "weekly_title": "📊 گزارش هفتگی سرنخ‌های فروش",
    "monthly_title": "📊 گزارش ماهانه سرنخ‌های فروش",
    "executive_title": "📊 گزارش مدیریتی (مدیرعامل)",
    "detail_title": "📄 گزارش تخصصی - جزئیات سرنخ‌ها",
    "period_range": "بازه: {from_date} تا {to_date}",
    "platform_filter": "پلتفرم: {label}",
    "generated_at": "تاریخ تولید: {date}",
    "count_line": "تعداد سرنخ‌ها: {count}",
    "total_leads": "📈 کل سرنخ‌ها: {count}",
    "by_platform": "🧭 به تفکیک پلتفرم: {summary}",
    "no_leads": "📭 هنوز سرنخی نیست.",
    "top_groups": "🏆 پرسرنخ‌ترین گروه‌ها:",
    "hot_keywords": "🔑 کلیدواژه‌های داغ:",
    "hot_brands": "🏢 برندهای داغ:",
    "recommendation": (
        "📌 توصیه: برای مشاهده جزئیات کامل، " "گزارش تخصصی را درخواست کنید."
    ),
    "separator_70": "=" * 70,
    "separator_60": "=" * 60,
    "section_sep": "═" * 70,
    "line_sep": "─" * 70,
    "report_period": "بازه: {from_date} تا {to_date}",
    "no_brand": "   (برندی ثبت نشده)",
    "no_result": "📭 نتیجه‌ای یافت نشد.",
    "all_platforms": "همه پلتفرم‌ها",
}

_REPORT_HEADERS_LOADED_FROM_FILE: bool = False


def _load_report_headers() -> dict[str, str]:
    """🎯 خواندن تیترهای گزارش از data — با fallback امن."""
    global _REPORT_HEADERS_LOADED_FROM_FILE

    try:
        from data.loader import load_json

        data = load_json("report_headers", default={})

        if not isinstance(data, dict):
            _REPORT_HEADERS_LOADED_FROM_FILE = False
            return dict(_FALLBACK_REPORT_HEADERS)

        headers = data.get("headers")
        if not isinstance(headers, dict) or not headers:
            _REPORT_HEADERS_LOADED_FROM_FILE = False
            return dict(_FALLBACK_REPORT_HEADERS)

        # ─── merge با fallback (کلیدهای غایب از fallback) ───
        merged = dict(_FALLBACK_REPORT_HEADERS)
        for k, v in headers.items():
            if isinstance(v, str):
                merged[str(k)] = v

        _REPORT_HEADERS_LOADED_FROM_FILE = True
        log_info(
            f"✅ [REPORT:headers] {len(merged)} تیتر از "
            f"report_headers.json بارگذاری شد"
        )
        return merged

    except Exception as e:
        log_warning(
            f"⚠️ [REPORT:headers] بارگذاری ناموفق | "
            f"{type(e).__name__}: {e} — fallback"
        )
        _REPORT_HEADERS_LOADED_FROM_FILE = False
        return dict(_FALLBACK_REPORT_HEADERS)


# ─── لود اولیه ───
REPORT_HEADERS: dict[str, str] = _load_report_headers()


def _hdr(key: str, default: str = "", **kw) -> str:
    """🎯 خواندن تیتر از فایل — با fallback امن و format.

    Args:
        key: کلید تیتر.
        default: مقدار پیش‌فرض.
        **kw: پارامترهای format.

    Returns:
        متن آماده.
    """
    template = REPORT_HEADERS.get(key) or default or f"[{key}]"
    if not kw:
        return template
    try:
        return template.format(**kw)
    except (KeyError, IndexError, ValueError):
        return template


def reload_report_headers() -> dict[str, str]:
    """🎯 hot-reload تیترها."""
    global REPORT_HEADERS
    try:
        from data.loader import clear_cache

        clear_cache()
    except Exception:
        pass
    REPORT_HEADERS = _load_report_headers()
    return REPORT_HEADERS


def get_headers_source() -> str:
    """🎯 منبع تیترها: file یا fallback."""
    return "file" if _REPORT_HEADERS_LOADED_FROM_FILE else "fallback"


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_SLOW_REPORT_MS = _env_int(
    "REPORT_SLOW_MS",
    10_000,
    min_val=100,
    max_val=600_000,
)
_LOG_PREVIEW_LEN = _env_int(
    "REPORT_LOG_PREVIEW",
    80,
    min_val=20,
    max_val=500,
)
_SQL_TIMEOUT_S = _env_float(
    "REPORT_SQL_TIMEOUT_S",
    5.0,
    min_val=0.5,
    max_val=300.0,
)
_MISFIRE_GRACE_S = _env_int(
    "REPORT_MISFIRE_GRACE_S",
    600,
    min_val=60,
    max_val=86400,
)
_CACHE_MAX_RESULTS = _env_int(
    "REPORT_CACHE_MAX",
    100,
    min_val=10,
    max_val=10_000,
)
_CACHE_VERSION = 3

# ─── کلیدهای فایل‌های گزارش ───
_PATH_KEYS = ("txt", "html", "csv", "json")


# ═══════════════════════════════════════════
# ترتیب و برچسب پلتفرم‌ها
# ═══════════════════════════════════════════
def _build_platform_order() -> list[str]:
    """🎯 FIX: از config.PLATFORMS_ORDER با fallback."""
    try:
        from config import PLATFORMS_ORDER

        if PLATFORMS_ORDER:
            return list(PLATFORMS_ORDER)
    except Exception:
        pass
    return [
        "eitaa",
        "bale",
        "rubika",
        "telegram",
        "whatsapp",
        "web",
        "linkedin",
        "instagram",
    ]


PLATFORM_ORDER: list[str] = _build_platform_order()

PLATFORM_LABELS: dict[str, str] = {
    "eitaa": "📱 ایتا",
    "bale": "📨 بله",
    "rubika": "🟣 روبیکا",
    "telegram": "✈️ تلگرام",
    "whatsapp": "🟢 واتساپ",
    "web": "🌐 وب",
    "linkedin": "💼 لینکدین",
    "instagram": "📷 اینستاگرام",
}


def platform_label(platform: str) -> str:
    """🎯 label پلتفرم."""
    return PLATFORM_LABELS.get(
        (platform or "").lower(),
        f"📍 {platform or 'نامشخص'}",
    )


def platform_rank(platform: str) -> int:
    """🎯 rank پلتفرم — برای مرتب‌سازی."""
    p = (platform or "").lower()
    return PLATFORM_ORDER.index(p) if p in PLATFORM_ORDER else len(PLATFORM_ORDER) + 1


def _safe_get(row: Any, key: str, default: Any = "") -> Any:
    """دسترسی امن به ستون row (dict یا sqlite3.Row)."""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def group_by_platform(rows):
    """🎯 گروه‌بندی ردیف‌ها بر اساس پلتفرم."""
    buckets: dict[str, list] = {}
    for r in rows:
        p = (_safe_get(r, "platform", "") or "").lower()
        buckets.setdefault(p, []).append(r)
    return [(p, buckets[p]) for p in sorted(buckets, key=platform_rank)]


def dedup_rows(rows):
    """🎯 حذف ردیف‌های تکراری."""
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
    """🎯 خلاصهٔ پلتفرم‌ها در یک خط."""
    parts = []
    for p, items in group_by_platform(rows):
        parts.append(f"{platform_label(p)} {len(items)}")
    return " | ".join(parts) if parts else "—"


# ═══════════════════════════════════════════
# cache پایدار (thread-safe)
# ═══════════════════════════════════════════
_CACHE_LOCK = threading.Lock()


def _load_cache() -> dict[str, Any]:
    """🎯 FIX: با dict-check."""
    with _CACHE_LOCK:
        try:
            if os.path.exists(_CACHE_FILE):
                with open(_CACHE_FILE, encoding="utf-8") as f:
                    data = json.load(f) or {}

                if not isinstance(data, dict):
                    log_warning(
                        f"⚠️ [REPORT:cache] cache dict نیست "
                        f"(type={type(data).__name__}) — reset"
                    )
                    return {"_version": _CACHE_VERSION}

                if data.get("_version") != _CACHE_VERSION:
                    log_info(
                        f"♻️ [REPORT:cache] نسخهٔ قدیمی "
                        f"({data.get('_version')}) → reset"
                    )
                    return {"_version": _CACHE_VERSION}
                return data
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [REPORT:cache] JSON نامعتبر | " f"line={e.lineno} | {e.msg}"
            )
        except Exception as e:
            log_warning(f"⚠️ [REPORT:cache] load | " f"{type(e).__name__}: {e}")
    return {"_version": _CACHE_VERSION}


def _save_cache(cache: dict[str, Any]) -> None:
    """🎯 FIX: با fsync + dir fsync."""
    with _CACHE_LOCK:
        try:
            parent = os.path.dirname(_CACHE_FILE)
            if parent:
                os.makedirs(parent, exist_ok=True)

            cache["_version"] = _CACHE_VERSION
            tmp = _CACHE_FILE + ".tmp"

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            os.replace(tmp, _CACHE_FILE)

            if parent:
                try:
                    dir_fd = os.open(parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (AttributeError, OSError):
                    pass
        except Exception as e:
            log_warning(f"⚠️ [REPORT:cache] save | " f"{type(e).__name__}: {e}")
            try:
                tmp = _CACHE_FILE + ".tmp"
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass


def _get_cache_state(
    result: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """🎯 تشخیص per-file (نه all-or-nothing)."""
    if not result or not isinstance(result, dict):
        return "miss", []

    missing: list[str] = []
    for key in _PATH_KEYS:
        path = result.get(key)
        if not path:
            missing.append(key)
            continue
        if not os.path.exists(path):
            missing.append(key)

    if not missing:
        return "hit", []
    return "partial", missing


def _sqlite_connect(path: str) -> sqlite3.Connection:
    """اتصال SQLite با timeout."""
    conn = sqlite3.connect(path, timeout=_SQL_TIMEOUT_S)
    try:
        conn.execute(
            "PRAGMA busy_timeout = ?",
            (int(_SQL_TIMEOUT_S * 1000),),
        )
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
        try:
            self.scheduler = AsyncIOScheduler()
        except Exception as e:
            log_warning(f"⚠️ [REPORT:init] scheduler | " f"{type(e).__name__}: {e}")
            self.scheduler = None

        self.db_path = DB_PATH
        self._cache = _load_cache()

        results = self._cache.get("results", {})
        cache_entries = len(results) if isinstance(results, dict) else 0

        log_info(
            f"📊 [REPORT:init] آماده | db={self.db_path} | "
            f"cache_entries={cache_entries} | "
            f"sql_timeout={_SQL_TIMEOUT_S}s | "
            f"cache_version={_CACHE_VERSION} | "
            f"headers={len(REPORT_HEADERS)} "
            f"({get_headers_source()}) | "
            f"platforms={PLATFORM_ORDER}"
        )

    @staticmethod
    def _filename_suffix(platform: str | None) -> str:
        return f"_{platform}" if platform else ""

    # ═══════════════════════════════════════════
    # گزارش روزانه
    # ═══════════════════════════════════════════
    async def generate_report(
        self,
        target_date: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any] | None:
        """تولید گزارش روزانه با per-file cache."""
        t0 = time.monotonic()
        if target_date:
            report_date = target_date
        else:
            report_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        cache_key = f"{report_date}|{platform or 'all'}"

        # ─── cache check ───
        cached_result = self._cache.get("results", {}).get(cache_key)
        state, missing_keys = _get_cache_state(cached_result)

        if state == "hit":
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"💡 [REPORT:daily] cache HIT | " f"{cache_key} | {elapsed_ms:.0f}ms"
            )
            return cached_result

        if state == "partial":
            log_info(
                f"🔧 [REPORT:daily] partial cache | "
                f"{cache_key} | پاک‌شده: {missing_keys}"
            )
        else:
            log_info(
                f"▶️ [REPORT:daily] شروع | "
                f"date={report_date} | "
                f"platform={platform or 'all'}"
            )

        # ─── واکشی داده ───
        leads_data = await self._get_leads_by_date(report_date, platform=platform)

        if not leads_data or leads_data["total"] == 0:
            log_info(
                f"ℹ️ [REPORT:daily] بدون سرنخ | "
                f"{report_date} | platform={platform or 'all'}"
            )
            if state == "partial":
                self._cache.get("results", {}).pop(cache_key, None)
                _save_cache(self._cache)
            return None

        total = leads_data["total"]
        if state == "partial":
            log_info(
                f"📊 [REPORT:daily] {total} سرنخ — "
                f"بازتولید {len(missing_keys)} فایل"
            )
        else:
            log_info(f"📊 [REPORT:daily] {total} سرنخ پیدا شد")

        # ─── بازتولید فایل‌های پاک‌شده ───
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

        # ─── cache update ───
        self._cache["last_report"] = cache_key
        self._cache["last_report_at"] = datetime.now().isoformat()

        results = self._cache.setdefault("results", {})
        if not isinstance(results, dict):
            results = {}
            self._cache["results"] = results
        results[cache_key] = result

        if len(results) > _CACHE_MAX_RESULTS:
            keys = list(results.keys())
            for k in keys[: len(keys) - _CACHE_MAX_RESULTS]:
                results.pop(k, None)

        _save_cache(self._cache)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if state == "partial":
            log_success(
                f"✅ [REPORT:daily] partial-regen | "
                f"{total} سرنخ | regen={len(missing_keys)} | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            log_success(
                f"✅ [REPORT:daily] کامل | {total} سرنخ | "
                f"{elapsed_ms:.0f}ms | date={report_date}"
            )

        if elapsed_ms > _SLOW_REPORT_MS:
            log_warning(f"🐌 [REPORT:daily] کند | " f"{elapsed_ms:.0f}ms")

        return result

    async def generate_today_report(self, platform: str | None = None):
        return await self.generate_report(
            datetime.now().strftime("%Y-%m-%d"),
            platform=platform,
        )

    # ═══════════════════════════════════════════
    # گزارش هفتگی
    # ═══════════════════════════════════════════
    async def generate_weekly_report(self, platform: str | None = None):
        t0 = time.monotonic()
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        label = platform_label(platform) if platform else _hdr("all_platforms")

        log_info(f"▶️ [REPORT:weekly] {start_date} → {end_date} | " f"platform={label}")

        # 🔒 SEC FIX: where فقط از placeholder ثابت + ? ساخته می‌شود
        where = "date(timestamp) BETWEEN ? AND ? " "AND keywords != ''"
        params: list[Any] = [start_date, end_date]
        if platform:
            where += " AND platform = ?"
            params.append(platform)

        try:
            async with aiosqlite.connect(self.db_path, timeout=_SQL_TIMEOUT_S) as conn:
                conn.row_factory = aiosqlite.Row
                _sql = (
                    "SELECT id, platform, chat_name, user_id, "
                    "text, keywords, media_type, "
                    "opportunity_score, opportunity_type, "
                    "timestamp, brands, matched_products "
                    "FROM messages "
                    "WHERE " + where + " "
                    "ORDER BY opportunity_score DESC, "
                    "timestamp DESC"
                )
                cursor = await conn.execute(_sql, params)
                messages = await cursor.fetchall()
        except Exception as e:
            log_error(f"❌ [REPORT:weekly] query | " f"{type(e).__name__}: {e}")
            return None

        if not messages:
            log_info(f"ℹ️ [REPORT:weekly] بدون سرنخ | " f"{start_date} → {end_date}")
            return None

        suffix = self._filename_suffix(platform)
        filename = os.path.join(
            REPORTS_DIR,
            f"weekly_report{suffix}_" f"{start_date}_to_{end_date}.txt",
        )

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(_hdr("separator_70") + "\n")
                f.write(_hdr("weekly_title") + "\n")
                f.write(
                    _hdr(
                        "period_range",
                        from_date=start_date,
                        to_date=end_date,
                    )
                    + "\n"
                )
                if platform:
                    f.write(_hdr("platform_filter", label=label) + "\n")
                f.write(_hdr("separator_70") + "\n")
                f.write(_hdr("total_leads", count=len(messages)) + "\n")
                f.write(
                    _hdr(
                        "by_platform",
                        summary=platform_summary_line(messages),
                    )
                    + "\n"
                )
                for plat, rows in group_by_platform(messages):
                    f.write("\n" + _hdr("section_sep") + "\n")
                    f.write(f"{platform_label(plat)} — " f"{len(rows)} سرنخ\n")
                    f.write(_hdr("section_sep") + "\n")
                    for i, msg in enumerate(rows, 1):
                        chat = _safe_get(msg, "chat_name", "?")
                        kw = _safe_get(msg, "keywords", "")
                        ts = _safe_get(msg, "timestamp", "")
                        txt = str(_safe_get(msg, "text", "") or "")[:300]
                        f.write(f"[{i}] گروه: {chat}  |  🔑 {kw}\n")
                        f.write(f"    🕒 {ts}\n")
                        f.write(f"    💬 {txt}\n")
        except Exception as e:
            log_error(f"❌ [REPORT:weekly] write | " f"{type(e).__name__}: {e}")
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:weekly] {len(messages)} سرنخ | "
            f"{elapsed_ms:.0f}ms | "
            f"{os.path.basename(filename)}"
        )
        return filename

    # ═══════════════════════════════════════════
    # گزارش مدیریتی (sync)
    # ═══════════════════════════════════════════
    def generate_executive_report(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        platform: str | None = None,
    ) -> str | None:
        """گزارش خلاصه برای مدیرعامل (sync)."""
        t0 = time.monotonic()
        if date_from is None:
            date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        if date_to is None:
            date_to = datetime.now().strftime("%Y-%m-%d")

        label = platform_label(platform) if platform else _hdr("all_platforms")
        log_info(f"▶️ [REPORT:exec] {date_from} → {date_to} | " f"platform={label}")

        where = "keywords != '' AND " "date(timestamp) BETWEEN ? AND ?"
        base_params: list[Any] = [date_from, date_to]
        if platform:
            where += " AND platform = ?"
            base_params.append(platform)

        try:
            conn = _sqlite_connect(self.db_path)
            try:
                cur = conn.cursor()

                _sql_count = "SELECT COUNT(*) FROM messages " "WHERE " + where
                cur.execute(_sql_count, base_params)
                row = cur.fetchone()
                total_leads = row[0] if row else 0

                _sql_platforms = (
                    "SELECT platform, COUNT(*) FROM messages "
                    "WHERE " + where + " "
                    "GROUP BY platform"
                )
                cur.execute(_sql_platforms, base_params)
                platform_stats = cur.fetchall()

                _sql_top_groups = (
                    "SELECT chat_name, platform, "
                    "COUNT(*) AS cnt, "
                    "MAX(chat_link) AS link "
                    "FROM messages "
                    "WHERE " + where + " "
                    "GROUP BY chat_name, platform "
                    "ORDER BY cnt DESC LIMIT 5"
                )
                cur.execute(_sql_top_groups, base_params)
                top_groups = cur.fetchall()

                _sql_top_kw = (
                    "SELECT keywords, COUNT(*) AS cnt "
                    "FROM messages "
                    "WHERE " + where + " "
                    "GROUP BY keywords "
                    "ORDER BY cnt DESC LIMIT 10"
                )
                cur.execute(_sql_top_kw, base_params)
                top_keywords = cur.fetchall()

                _sql_top_brands = (
                    "SELECT brands, COUNT(*) AS cnt "
                    "FROM messages "
                    "WHERE " + where + " AND brands != '' "
                    "GROUP BY brands "
                    "ORDER BY cnt DESC LIMIT 10"
                )
                cur.execute(_sql_top_brands, base_params)
                top_brands = cur.fetchall()
            finally:
                conn.close()
        except Exception as e:
            log_error(f"❌ [REPORT:exec] query | " f"{type(e).__name__}: {e}")
            return None

        suffix = self._filename_suffix(platform)
        report_file = os.path.join(
            REPORTS_DIR,
            f"executive{suffix}_" f"{date_from}_to_{date_to}.txt",
        )

        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(_hdr("executive_title") + "\n")
                f.write(
                    _hdr(
                        "period_range",
                        from_date=date_from,
                        to_date=date_to,
                    )
                    + "\n"
                )
                if platform:
                    f.write(
                        _hdr(
                            "platform_filter",
                            label=platform_label(platform),
                        )
                        + "\n"
                    )
                f.write(_hdr("separator_60") + "\n")
                f.write(f"🔹 تعداد کل فرصت‌ها (سرنخ): " f"{total_leads}\n\n")

                f.write("🔸 تفکیک بر اساس پلتفرم:\n")
                for plat, cnt in sorted(
                    platform_stats,
                    key=lambda x: platform_rank(x[0]),
                ):
                    f.write(f"   • {platform_label(plat)}: " f"{cnt} سرنخ\n")
                f.write("\n")

                f.write("🔸 پرسرنخ‌ترین گروه‌ها:\n")
                for row in sorted(
                    top_groups,
                    key=lambda x: (platform_rank(x[1]), -x[2]),
                ):
                    chat, plat, cnt, link = (row[0], row[1], row[2], row[3])
                    line = f"   • {chat} " f"({platform_label(plat)}): " f"{cnt} سرنخ"
                    if link:
                        line += f" — 🔗 {link}"
                    f.write(line + "\n")
                f.write("\n")

                f.write("🔸 کلیدواژه‌های داغ:\n")
                for kw, cnt in top_keywords:
                    f.write(f"   • {kw}: {cnt} بار\n")
                f.write("\n")

                f.write("🔸 برندهای داغ:\n")
                if top_brands:
                    for brand, cnt in top_brands:
                        f.write(f"   • {brand}: {cnt} بار\n")
                else:
                    f.write(_hdr("no_brand") + "\n")
                f.write("\n")

                f.write(_hdr("recommendation") + "\n")
        except Exception as e:
            log_error(f"❌ [REPORT:exec] write | " f"{type(e).__name__}: {e}")
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:exec] {total_leads} سرنخ | "
            f"{elapsed_ms:.0f}ms | "
            f"{os.path.basename(report_file)}"
        )
        return report_file

    # ═══════════════════════════════════════════
    # گزارش تخصصی (sync)
    # ═══════════════════════════════════════════
    def generate_detailed_report(
        self,
        platform: str | None = None,
        chat_name: str | None = None,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 200,
    ) -> str | None:
        """گزارش کامل برای کارشناس (sync)."""
        t0 = time.monotonic()
        log_info(
            f"▶️ [REPORT:detail] platform={platform or 'all'} | "
            f"chat={chat_name or '—'} | kw={keyword or '—'} | "
            f"limit={limit}"
        )

        try:
            limit = max(1, min(int(limit), 10_000))
        except (TypeError, ValueError):
            limit = 200

        query = (
            "SELECT id, platform, chat_name, chat_link, "
            "chat_username, chat_type, text, timestamp, "
            "keywords, media_type, file_id, caption, "
            "reply_to, brands, matched_products "
            "FROM messages "
            "WHERE keywords != ''"
        )

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
            log_error(f"❌ [REPORT:detail] query | " f"{type(e).__name__}: {e}")
            return None

        rows = sorted(rows, key=lambda r: platform_rank(r[1]))

        if not rows:
            log_info("ℹ️ [REPORT:detail] بدون نتیجه با فیلترها")
            return None

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = self._filename_suffix(platform)
        csv_file = os.path.join(
            REPORTS_DIR,
            f"detailed{suffix}_{timestamp_str}.csv",
        )
        txt_file = os.path.join(
            REPORTS_DIR,
            f"detailed{suffix}_{timestamp_str}.txt",
        )
        json_file = os.path.join(
            REPORTS_DIR,
            f"detailed{suffix}_{timestamp_str}.json",
        )

        try:
            # ─── CSV ───
            with open(
                csv_file,
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "شناسه پیام",
                        "پلتفرم",
                        "نام گروه",
                        "لینک گروه",
                        "یوزرنیم گروه",
                        "نوع گروه",
                        "متن پیام",
                        "زمان",
                        "کلیدواژه‌ها",
                        "نوع رسانه",
                        "فایل ID",
                        "توضیحات",
                        "پاسخ به",
                        "برند",
                        "محصولات",
                    ]
                )
                for row in rows:
                    writer.writerow(row)

            # ─── TXT ───
            with open(txt_file, "w", encoding="utf-8") as f:
                f.write(_hdr("detail_title") + "\n")
                f.write(
                    _hdr(
                        "generated_at",
                        date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    + "\n"
                )
                f.write(_hdr("count_line", count=len(rows)) + "\n")
                if platform:
                    f.write(_hdr("platform_filter", label=platform) + "\n")
                if chat_name:
                    f.write(f"فیلتر گروه: {chat_name}\n")
                if keyword:
                    f.write(f"فیلتر کلیدواژه: {keyword}\n")
                f.write("=" * 80 + "\n\n")

                for idx, row in enumerate(rows, 1):
                    (
                        msg_id,
                        plat,
                        chat,
                        chat_link,
                        chat_username,
                        chat_type,
                        text,
                        ts,
                        keywords,
                        media_type,
                        file_id,
                        caption,
                        reply_to,
                        brands,
                        products,
                    ) = row
                    f.write(f"[{idx}] " f"────────────────────────────────\n")
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
                    if brands:
                        f.write(f"🏢 برند: {brands}\n")
                    if products:
                        f.write(f"🌿 محصولات: {products}\n")
                    if media_type:
                        f.write(f"📎 نوع پیام: {media_type}\n")
                    if file_id:
                        f.write(f"📁 فایل: {file_id}\n")
                    if caption:
                        f.write(f"📝 توضیحات: {caption}\n")
                    f.write(f"💬 متن پیام:\n{text}\n\n")

            # ─── JSON ───
            data = []
            for row in rows:
                (
                    msg_id,
                    plat,
                    chat,
                    chat_link,
                    chat_username,
                    chat_type,
                    text,
                    ts,
                    keywords,
                    media_type,
                    file_id,
                    caption,
                    reply_to,
                    brands,
                    products,
                ) = row
                data.append(
                    {
                        "id": msg_id,
                        "platform": plat,
                        "chat_name": chat,
                        "chat_link": chat_link,
                        "chat_username": chat_username,
                        "chat_type": chat_type,
                        "text": text,
                        "timestamp": ts,
                        "keywords": (keywords.split(",") if keywords else []),
                        "brands": brands,
                        "matched_products": products,
                        "media_type": media_type,
                        "file_id": file_id,
                        "caption": caption,
                        "reply_to": reply_to,
                    }
                )

            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_error(f"❌ [REPORT:detail] write | " f"{type(e).__name__}: {e}")
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:detail] {len(rows)} سرنخ | "
            f"{elapsed_ms:.0f}ms | "
            f"{os.path.basename(txt_file)}"
        )
        return txt_file

    # ═══════════════════════════════════════════
    # جستجوی سرنخ
    # ═══════════════════════════════════════════
    def search_lead_by_message_id(
        self,
        message_id: str,
        platform: str | None = None,
    ):
        """جستجوی یک سرنخ با شناسه پیام."""
        if not message_id:
            return None

        log_info(f"🔍 [REPORT:search] id={message_id} " f"platform={platform or 'any'}")
        try:
            conn = _sqlite_connect(self.db_path)
            try:
                if platform:
                    cur = conn.execute(
                        """
                        SELECT id, platform, chat_name,
                               text, timestamp, keywords
                        FROM messages
                        WHERE id = ? AND platform = ?
                        """,
                        (message_id, platform),
                    )
                else:
                    cur = conn.execute(
                        """
                        SELECT id, platform, chat_name,
                               text, timestamp, keywords
                        FROM messages
                        WHERE id = ?
                        """,
                        (message_id,),
                    )
                row = cur.fetchone()
            finally:
                conn.close()
        except Exception as e:
            log_error(f"❌ [REPORT:search] " f"{type(e).__name__}: {e}")
            return None

        if row:
            log_success(f"✅ [REPORT:search] پیدا شد | " f"{row[1]} | {row[2]}")
            return row
        log_info(f"ℹ️ [REPORT:search] پیدا نشد | id={message_id}")
        return None

    # ═══════════════════════════════════════════
    # داخلی: خواندن سرنخ‌های یک روز
    # ═══════════════════════════════════════════
    async def _get_leads_by_date(
        self,
        date: str,
        platform: str | None = None,
    ) -> dict[str, Any]:
        where = "date(timestamp) = ? AND keywords != ''"
        params: list[Any] = [date]
        if platform:
            where += " AND platform = ?"
            params.append(platform)

        try:
            async with aiosqlite.connect(self.db_path, timeout=_SQL_TIMEOUT_S) as conn:
                conn.row_factory = aiosqlite.Row

                _sql_messages = (
                    "SELECT id, platform, chat_name, user_id, "
                    "text, keywords, media_type, "
                    "opportunity_score, opportunity_type, "
                    "timestamp, brands, matched_products "
                    "FROM messages "
                    "WHERE " + where + " "
                    "ORDER BY opportunity_score DESC, "
                    "timestamp DESC"
                )
                cursor = await conn.execute(_sql_messages, params)
                messages = await cursor.fetchall()

                _sql_groups = (
                    "SELECT chat_name, platform, "
                    "COUNT(*) AS count "
                    "FROM messages "
                    "WHERE " + where + " "
                    "GROUP BY chat_name, platform "
                    "ORDER BY count DESC"
                )
                cursor = await conn.execute(_sql_groups, params)
                group_stats = await cursor.fetchall()

                _sql_keywords = (
                    "SELECT keywords, COUNT(*) AS count "
                    "FROM messages "
                    "WHERE " + where + " "
                    "GROUP BY keywords "
                    "ORDER BY count DESC "
                    "LIMIT 15"
                )
                cursor = await conn.execute(_sql_keywords, params)
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
            log_error(f"❌ [REPORT:_leads_by_date] " f"{type(e).__name__}: {e}")
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
        """🎯 شمارش top-N."""
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
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    # ═══════════════════════════════════════════
    # داخلی: ذخیرهٔ فرمت‌ها
    # ═══════════════════════════════════════════
    async def _save_text_report(self, data, date, platform=None) -> str:
        suffix = self._filename_suffix(platform)
        filename = os.path.join(REPORTS_DIR, f"daily_report{suffix}_{date}.txt")
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(_hdr("separator_70") + "\n")
                f.write(_hdr("daily_title", date=date) + "\n")
                if platform:
                    f.write(
                        _hdr(
                            "platform_filter",
                            label=platform_label(platform),
                        )
                        + "\n"
                    )
                f.write(_hdr("separator_70") + "\n")
                f.write(_hdr("total_leads", count=data["total"]) + "\n")
                f.write(
                    _hdr(
                        "by_platform",
                        summary=platform_summary_line(data["messages"]),
                    )
                    + "\n"
                )
                for plat, rows in group_by_platform(data["messages"]):
                    f.write("\n" + _hdr("section_sep") + "\n")
                    f.write(f"{platform_label(plat)} — " f"{len(rows)} سرنخ\n")
                    f.write(_hdr("section_sep") + "\n")
                    top_groups = self._count_top(rows, "chat_name")
                    if top_groups:
                        f.write(
                            "🏆 پرسرنخ‌ترین گروه‌ها: "
                            + "، ".join(f"{g} ({c})" for g, c in top_groups)
                            + "\n"
                        )
                    top_kw = self._count_top(rows, "keywords")
                    if top_kw:
                        f.write(
                            "🔑 کلیدواژه‌های داغ: "
                            + "، ".join(f"{k} ({c})" for k, c in top_kw)
                            + "\n"
                        )
                    f.write(_hdr("line_sep") + "\n")
                    for i, msg in enumerate(dedup_rows(rows), 1):
                        chat = _safe_get(msg, "chat_name", "?")
                        kw = _safe_get(msg, "keywords", "")
                        brands = _safe_get(msg, "brands", "")
                        score = _safe_get(msg, "opportunity_score", 0)
                        lvl = _safe_get(msg, "opportunity_type", "")
                        ts = _safe_get(msg, "timestamp", "")
                        txt = str(_safe_get(msg, "text", "") or "")[:300]

                        f.write(f"[{i}] گروه: {chat}  |  🔑 {kw}")
                        if brands:
                            f.write(f"  |  🏢 {brands}")
                        if score:
                            f.write(f"  |  🎯 {lvl or ''} " f"(امتیاز {score})")
                        f.write(f"\n    🕒 {ts}\n")
                        f.write(f"    💬 {txt}\n")
        except Exception as e:
            log_error(f"❌ [REPORT:txt] {type(e).__name__}: {e}")
            return ""
        return filename

    async def _save_html_report(self, data, date, platform=None) -> str:
        import html as _html

        suffix = self._filename_suffix(platform)
        filename = os.path.join(REPORTS_DIR, f"daily_report{suffix}_{date}.html")
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
                f"\n        <h2>"
                f"{_html.escape(platform_label(plat))} — "
                f"{len(rows)} سرنخ</h2>"
            )
            for msg in rows:
                chat = _html.escape(str(_safe_get(msg, "chat_name", "") or ""))
                kw = _html.escape(str(_safe_get(msg, "keywords", "") or ""))
                txt = _html.escape(str(_safe_get(msg, "text", "") or "")[:300])
                ts = _html.escape(str(_safe_get(msg, "timestamp", "") or ""))
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
            log_error(f"❌ [REPORT:html] {type(e).__name__}: {e}")
            return ""
        return filename

    async def _save_csv_report(self, data, date, platform=None) -> str:
        suffix = self._filename_suffix(platform)
        filename = os.path.join(REPORTS_DIR, f"daily_report{suffix}_{date}.csv")
        try:
            with open(
                filename,
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "پلتفرم",
                        "شناسه",
                        "گروه",
                        "متن",
                        "کلیدواژه",
                        "برند",
                        "محصولات",
                        "زمان",
                    ]
                )
                for plat, rows in group_by_platform(data["messages"]):
                    for msg in rows:
                        writer.writerow(
                            [
                                plat,
                                _safe_get(msg, "id", ""),
                                _safe_get(msg, "chat_name", ""),
                                str(_safe_get(msg, "text", "") or "")[:500],
                                _safe_get(msg, "keywords", ""),
                                _safe_get(msg, "brands", ""),
                                _safe_get(msg, "matched_products", ""),
                                _safe_get(msg, "timestamp", ""),
                            ]
                        )
        except Exception as e:
            log_error(f"❌ [REPORT:csv] {type(e).__name__}: {e}")
            return ""
        return filename

    async def _save_json_report(self, data, date, platform=None) -> str:
        suffix = self._filename_suffix(platform)
        filename = os.path.join(REPORTS_DIR, f"daily_report{suffix}_{date}.json")
        by_platform = []
        for plat, rows in group_by_platform(data["messages"]):
            by_platform.append(
                {
                    "platform": plat,
                    "label": PLATFORM_LABELS.get(plat, plat),
                    "count": len(rows),
                    "leads": [
                        {
                            "id": _safe_get(msg, "id", ""),
                            "chat_name": _safe_get(msg, "chat_name", ""),
                            "text": str(_safe_get(msg, "text", "") or "")[:500],
                            "keywords": _safe_get(msg, "keywords", ""),
                            "brands": _safe_get(msg, "brands", ""),
                            "matched_products": _safe_get(msg, "matched_products", ""),
                            "timestamp": _safe_get(msg, "timestamp", ""),
                        }
                        for msg in rows
                    ],
                }
            )
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
        report_hour: int = 8,
        report_minute: int = 0,
        weekly_day: str = "sunday",
        weekly_hour: int = 9,
        weekly_minute: int = 0,
    ):
        """شروع زمان‌بندی گزارش."""
        if self.scheduler is None:
            log_warning("⚠️ [REPORT:schedule] scheduler نیست")
            return

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
                "saturday": "sat",
                "sunday": "sun",
                "monday": "mon",
                "tuesday": "tue",
                "wednesday": "wed",
                "thursday": "thu",
                "friday": "fri",
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
                f"📅 [REPORT:schedule] روزانه "
                f"{report_hour:02d}:{report_minute:02d} | "
                f"هفتگی ({dow}) "
                f"{weekly_hour:02d}:{weekly_minute:02d} | "
                f"ماهانه (روز ۱) | "
                f"misfire={_MISFIRE_GRACE_S}s"
            )
        except Exception as e:
            log_error(f"❌ [REPORT:schedule] " f"{type(e).__name__}: {e}")

    async def generate_monthly_report(
        self, platform: str | None = None
    ) -> dict[str, str | None] | None:
        """گزارش ماهانه (۳۰ روز اخیر)."""
        t0 = time.monotonic()
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        date_to = datetime.now().strftime("%Y-%m-%d")
        label = platform_label(platform) if platform else _hdr("all_platforms")

        log_info(f"▶️ [REPORT:monthly] {date_from} → {date_to} | " f"platform={label}")

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
            log_error(f"❌ [REPORT:monthly:exec] " f"{type(e).__name__}: {e}")

        try:
            detail_path = await asyncio.to_thread(
                self.generate_detailed_report,
                platform=platform,
                date_from=date_from,
                date_to=date_to,
                limit=500,
            )
        except Exception as e:
            log_error(f"❌ [REPORT:monthly:detail] " f"{type(e).__name__}: {e}")

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [REPORT:monthly] کامل | "
            f"{elapsed_ms:.0f}ms | "
            f"exec={'✅' if exec_path else '❌'} | "
            f"detail={'✅' if detail_path else '❌'}"
        )

        if not exec_path and not detail_path:
            log_warning("⚠️ [REPORT:monthly] هیچ گزارشی ساخته نشد")
            return None

        return {
            "exec": exec_path,
            "detail": detail_path,
            "platform": platform,
        }

    # ═══════════════════════════════════════════
    # cache management
    # ═══════════════════════════════════════════
    def invalidate_cache(self) -> int:
        """🎯 پاک کردن cache — برای تست."""
        with _CACHE_LOCK:
            n = len(self._cache.get("results", {}))
            self._cache = {"_version": _CACHE_VERSION}
            _save_cache(self._cache)
        log_info(f"🗑️ [REPORT:cache] {n} entry پاک شد")
        return n

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        results = self._cache.get("results", {})
        cache_size = len(results) if isinstance(results, dict) else 0
        return {
            "db_path": self.db_path,
            "reports_dir": REPORTS_DIR,
            "cache_file": _CACHE_FILE,
            "cache_version": _CACHE_VERSION,
            "cache_size": cache_size,
            "last_report": self._cache.get("last_report"),
            "platforms": list(PLATFORM_ORDER),
            "sql_timeout_s": _SQL_TIMEOUT_S,
            "misfire_grace_s": _MISFIRE_GRACE_S,
            "scheduler_running": (
                self.scheduler.running if self.scheduler is not None else False
            ),
            "headers_count": len(REPORT_HEADERS),
            "headers_source": get_headers_source(),
        }

    def __repr__(self) -> str:
        return (
            f"<DailyReporter db={os.path.basename(self.db_path)} "
            f"platforms={len(PLATFORM_ORDER)} "
            f"headers={get_headers_source()}>"
        )


# ═══════════════════════════════════════════
# توابع سطح بالا (سازگاری عقب‌رو)
# ═══════════════════════════════════════════
def _parse_time(t: str, default_h: int, default_m: int) -> tuple[int, int]:
    """parse ساعت — با چک معقول بودن."""
    try:
        h, m = str(t).split(":")
        h_i, m_i = int(h), int(m)
        if 0 <= h_i <= 23 and 0 <= m_i <= 59:
            return h_i, m_i
        log_warning(
            f"⚠️ [REPORT:parse] '{t}' خارج بازه — " f"{default_h:02d}:{default_m:02d}"
        )
    except Exception as e:
        log_warning(
            f"⚠️ [REPORT:parse] '{t}' نامعتبر — "
            f"{default_h:02d}:{default_m:02d} | "
            f"{type(e).__name__}: {e}"
        )
    return default_h, default_m


async def start_daily_reporter(db) -> DailyReporter:
    """راه‌اندازی گزارش‌ساز خودکار (تنظیمات از config)."""
    try:
        from config import REPORT
    except ImportError:
        REPORT = {}

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
    date_from=None,
    date_to=None,
    platform: str | None = None,
):
    """گزارش مدیریتی (sync) - برای استفاده در ترمینال."""
    reporter = DailyReporter()
    return reporter.generate_executive_report(date_from, date_to, platform=platform)


def generate_detailed_report(
    platform=None,
    chat_name=None,
    keyword=None,
    date_from=None,
    date_to=None,
    limit=200,
):
    """گزارش تخصصی (sync) - برای ترمینال."""
    reporter = DailyReporter()
    return reporter.generate_detailed_report(
        platform,
        chat_name,
        keyword,
        date_from,
        date_to,
        limit,
    )


def search_lead(message_id: str, platform: str = None):
    """جستجوی سرنخ با شناسه پیام."""
    reporter = DailyReporter()
    return reporter.search_lead_by_message_id(message_id, platform)


def _check_in_loop(name: str) -> None:
    """🎯 FIX: چک می‌کند داخل event loop فعال نیستیم."""
    try:
        asyncio.get_running_loop()
        raise RuntimeError(
            f"⛔ `{name}` داخل event loop فعال صدا زده شد — "
            f"از نسخهٔ async استفاده کن."
        )
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return
        raise


def generate_daily_report():
    """گزارش روزانه (sync) — برای سازگاری با کد قدیمی."""
    _check_in_loop("generate_daily_report")
    reporter = DailyReporter()
    return asyncio.run(reporter.generate_report())


def generate_weekly_report():
    """گزارش هفتگی (sync) — برای سازگاری با کد قدیمی."""
    _check_in_loop("generate_weekly_report")
    reporter = DailyReporter()
    return asyncio.run(reporter.generate_weekly_report())


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "DailyReporter",
    "PLATFORM_ORDER",
    "PLATFORM_LABELS",
    "platform_label",
    "platform_rank",
    "group_by_platform",
    "platform_summary_line",
    "dedup_rows",
    "start_daily_reporter",
    "generate_executive_report",
    "generate_detailed_report",
    "generate_daily_report",
    "generate_weekly_report",
    "search_lead",
    "REPORT_HEADERS",
    "reload_report_headers",
    "get_headers_source",
]


# ═══════════════════════════════════════════
# اجرای مستقیم
# ═══════════════════════════════════════════
if __name__ == "__main__":
    print("📊 تولید گزارش‌های نمونه...\n")
    generate_executive_report()
    generate_detailed_report(limit=10)
