"""
config.py — منبع یکتای تنظیمات پروژه (از .env).

⚠️ این فایل اولین import پروژه‌ست و نباید از core.kernel.logger یا هر core دیگر
استفاده کند (circular). برای لاگ کردن، یا print به stderr استفاده کن، یا
`log_config_summary()` را بعد از setup logger صدا بزن.

نسخهٔ ۹ (این نسخه):
  - 🎯 NEW: `AI_SYSTEM_PROMPT` — شخصیت متخصص پایش کد برای DeepSeek.
  - 🎯 NEW: `AI_PERSONA` — دیکشنری کامل شخصیت.
  - 🎯 NEW: `get_ai_system_prompt()` — تابع دسترسی.
  - 🎯 NEW: `AI_MODE_PROMPTS` — پرامپت‌های تخصصی برای هر mode.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۸.۱.

نسخهٔ ۸ (قبلی):
  - 🎯 افزودن top-level برای پارامترهایی که قبلاً فقط در PLUGINS_CONFIG
    یا MAIN_CONFIG بودند.
  - 🎯 افزودن `get_active_platforms()` با پشتیبانی کامل از instagram.
  - 🎯 افزودن `PLATFORMS_ORDER` و `ALL_PLATFORMS`.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۷.

نسخهٔ ۸.۱ (fixes):
  - 🐛 `_warn` حالا در صورت خطای print، به os.write fallback می‌کند.
  - 🐛 `log_config_summary` خطاها را لاگ می‌کند (نه silent pass).
  - 🎯 `__all__` برای public API اضافه شد (رفع هشدار Health Check).
"""
from __future__ import annotations

import os
from typing import Any

from data import paths as data_paths
from dotenv import load_dotenv
from core.utils.env import (
    env_bool as _env_bool,
    env_int as _env_int,
    env_float as _env_float,
    env_str as _env_str,
    _warn,
)
# ─── Load .env (force override) ───
load_dotenv(override=False)

def _env_secret(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip()
    
# ══════════════════════════════════════════════════════════
# helpers (type-safe env readers)
# ══════════════════════════════════════════════════════════
def _env_int_fallback(
    primary: str, fallback: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    raw = os.getenv(primary)
    if raw is None or str(raw).strip() == "":
        raw = os.getenv(fallback)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = int(str(raw).strip())
    except (ValueError, TypeError):
        _warn(f"{primary}/{fallback}={raw!r} نامعتبر — {default}")
        return default

    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_list(
    name: str, default: list[str] | None = None,
    max_items: int = 50,
) -> list[str]:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return list(default or [])
    parts = [x.strip() for x in str(raw).split(",") if x.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= max_items:
            break
    return out


# ══════════════════════════════════════════════════════════
# ثابت‌های سراسری (canonical)
# ══════════════════════════════════════════════════════════
PLATFORMS_ORDER: tuple[str, ...] = (
    "eitaa", "bale", "rubika", "telegram",
    "whatsapp", "web", "linkedin", "instagram",
    "youtube",
)
"""ترتیب canonical پلتفرم‌ها — برای نمایش/گزارش/مرتب‌سازی."""

ALL_PLATFORMS: frozenset[str] = frozenset(PLATFORMS_ORDER)
"""مجموعهٔ پلتفرم‌های مجاز (برای validation)."""


# ══════════════════════════════════════════════════════════
# مسیرهای پایه
# ══════════════════════════════════════════════════════════
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
CHROME_PATH: str | None = _env_str("CHROME_PATH") or None


# ══════════════════════════════════════════════════════════
# پروکسی
# ══════════════════════════════════════════════════════════
def get_proxy() -> str:
    return (
        _env_str("BROWSER_PROXY")
        or _env_str("PROXY_URL")
        or _env_str("BALE_PROXY")
    )


def get_browser_proxy() -> dict[str, Any]:
    raw = get_proxy()
    if not raw:
        return {}
    if "://" not in raw:
        raw = "http://" + raw
    return {"proxy": {"server": raw}}


# ══════════════════════════════════════════════════════════
# سوییچ‌های اصلی
# ══════════════════════════════════════════════════════════
AI_ENABLED: bool = _env_bool("AI_ENABLED", True)
EVENT_LOG: bool = _env_bool("EVENT_LOG", False)
# 🆕 حالت آفلاین
OFFLINE_MODE_ENABLED: bool = _env_bool(
    "OFFLINE_MODE_ENABLED", False
)
_VALID_AI_PRIORITIES = {"auto", "deepseek", "ollama"}
_ai_priority_raw = (_env_str("AI_PRIORITY", "auto").lower() or "auto")
if _ai_priority_raw not in _VALID_AI_PRIORITIES:
    _warn(
        f"AI_PRIORITY={_ai_priority_raw!r} نامعتبر — "
        f"معتبر: {sorted(_VALID_AI_PRIORITIES)} → auto"
    )
    _ai_priority_raw = "auto"
AI_PRIORITY: str = _ai_priority_raw
# خط ~۱۵۰ — اضافه کن:
OLLAMA_ENABLED: bool = _env_bool("OLLAMA_ENABLED", False)

# ══════════════════════════════════════════════════════════
# مسیرهای storage
# ══════════════════════════════════════════════════════════
STORAGE_DIR: str = os.path.join(BASE_DIR, "storage")

DB_DIR: str = os.path.join(STORAGE_DIR, "database")
SESSIONS_DIR: str = os.path.join(STORAGE_DIR, "sessions")
LOGS_DIR: str = os.path.join(STORAGE_DIR, "logs")
PYCACHE_DIR: str = os.path.join(STORAGE_DIR, "pycache")
MEDIA_DIR: str = os.path.join(STORAGE_DIR, "media")
REPORTS_DIR: str = os.path.join(STORAGE_DIR, "reports")
BACKUPS_DIR: str = os.path.join(STORAGE_DIR, "backups")

for _d in (
    DB_DIR, SESSIONS_DIR, LOGS_DIR, PYCACHE_DIR,
    MEDIA_DIR, REPORTS_DIR, BACKUPS_DIR,
):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception as _e:
        _warn(f"makedirs({_d}) | {type(_e).__name__}: {_e}")


# ══════════════════════════════════════════════════════════
# دیتابیس
# ══════════════════════════════════════════════════════════
_VALID_DB_TYPES = {"sqlite", "postgres"}
_db_type_raw = _env_str("DATABASE_TYPE", "sqlite").lower()
if _db_type_raw not in _VALID_DB_TYPES:
    _warn(
        f"DATABASE_TYPE={_db_type_raw!r} نامعتبر — "
        f"معتبر: {sorted(_VALID_DB_TYPES)} → sqlite"
    )
    _db_type_raw = "sqlite"
DATABASE_TYPE: str = _db_type_raw

SQLITE_CONFIG: dict[str, Any] = {
    "database_path": os.path.join(DB_DIR, "messages.db"),
}

POSTGRES_CONFIG: dict[str, Any] = {
    "host": _env_str("DB_HOST", "localhost"),
    "port": _env_int("DB_PORT", 5432, min_val=1, max_val=65535),
    "user": _env_str("DB_USER", "postgres"),
    "password": _env_secret("DB_PASSWORD"),
    "database": _env_str("DB_NAME", "lead_monitor"),
}

if DATABASE_TYPE == "postgres" and not POSTGRES_CONFIG["password"]:
    _warn(
        "DATABASE_TYPE=postgres ولی DB_PASSWORD خالیه — "
        "اتصال ممکنه شکست بخوره"
    )


# ══════════════════════════════════════════════════════════
# CRM
# ══════════════════════════════════════════════════════════
CRM_CONFIG: dict[str, Any] = {
    "sql_timeout_s": _env_float(
        "CRM_SQL_TIMEOUT_S", 5.0, min_val=0.5, max_val=120.0
    ),
}


# ══════════════════════════════════════════════════════════
# تنظیمات کلی سیستم
# ══════════════════════════════════════════════════════════
SYSTEM_CONFIG: dict[str, Any] = {
    "poll_interval_hours": _env_int(
        "SYSTEM_POLL_HOURS", 24, min_val=1, max_val=720
    ),
    "log_level": _env_str("LOG_LEVEL", "INFO").upper() or "INFO",
    "log_to_file": _env_bool("LOG_TO_FILE", True),
    "log_directory": LOGS_DIR,
    "sessions_directory": SESSIONS_DIR,
    "temp_directory": STORAGE_DIR,
    "media_directory": MEDIA_DIR,
}


# ══════════════════════════════════════════════════════════
# نگه‌داری
# ══════════════════════════════════════════════════════════
RETENTION: dict[str, int] = {
    "media_days": max(0, _env_int("KEEP_MEDIA_DAYS", 7)),
    "reports_days": max(0, _env_int("KEEP_REPORTS_DAYS", 30)),
    "messages_days": max(0, _env_int("KEEP_MESSAGES_DAYS", 0)),
    "cleanup_interval_hours": max(
        1, _env_int("CLEANUP_INTERVAL_HOURS", 24)
    ),
    "cleanup_min_interval_hours": max(
        1, _env_int("CLEANUP_MIN_INTERVAL_HOURS", 1)
    ),
    "cleanup_max_interval_hours": max(
        1, _env_int("CLEANUP_MAX_INTERVAL_HOURS", 720)
    ),
}


# ══════════════════════════════════════════════════════════
# مانیتورینگ (System Monitor)
# ══════════════════════════════════════════════════════════
MONITOR: dict[str, Any] = {
    "enabled": _env_bool("MONITOR_ENABLED", True),
    "interval_seconds": _env_int(
        "MONITOR_INTERVAL_SECONDS", 1800, min_val=5, max_val=86400
    ),
    "cpu_alert": _env_float(
        "MONITOR_CPU_ALERT", 90.0, min_val=1.0, max_val=100.0
    ),
    "ram_alert": _env_float(
        "MONITOR_RAM_ALERT", 90.0, min_val=1.0, max_val=100.0
    ),
    "disk_alert": _env_float(
        "MONITOR_DISK_ALERT", 70.0, min_val=1.0, max_val=100.0
    ),
}

# 🎯 top-level export (سهولت استفاده)
MONITOR_ENABLED: bool = MONITOR["enabled"]
MONITOR_INTERVAL_SECONDS: int = MONITOR["interval_seconds"]


# ══════════════════════════════════════════════════════════
# Self-Monitor
# ══════════════════════════════════════════════════════════
SELF_MONITOR: dict[str, Any] = {
    "enabled": _env_bool("SELF_MONITOR_ENABLED", True),
    "interval_seconds": _env_int(
        "SELF_MONITOR_INTERVAL", 600,
        min_val=60, max_val=86400,
    ),
    "error_threshold": _env_int(
        "SELF_MONITOR_ERROR_THRESHOLD", 5,
        min_val=1, max_val=1000,
    ),
    "stale_minutes": _env_int(
        "SELF_MONITOR_STALE_MINUTES", 60,
        min_val=5, max_val=10080,
    ),
}

# 🎯 top-level export
SELF_MONITOR_ENABLED: bool = SELF_MONITOR["enabled"]
SELF_MONITOR_INTERVAL: int = SELF_MONITOR["interval_seconds"]
SELF_MONITOR_ERROR_THRESHOLD: int = SELF_MONITOR["error_threshold"]
SELF_MONITOR_STALE_MINUTES: int = SELF_MONITOR["stale_minutes"]


# ══════════════════════════════════════════════════════════
# کلیدواژه‌ها
# ══════════════════════════════════════════════════════════
KEYWORDS_CONFIG: dict[str, Any] = {
    "reload_seconds": _env_float(
        "KEYWORDS_RELOAD_SECONDS", 120.0, min_val=1.0, max_val=3600.0
    ),
}

# 🎯 Brand token — قبلاً فقط در PLUGINS_CONFIG بود
BRAND_TOKEN_ENABLED: bool = _env_bool("BRAND_TOKEN_ENABLED", False)
BRAND_TOKEN_MIN_LEN: int = _env_int(
    "BRAND_TOKEN_MIN_LEN", 4, min_val=1, max_val=30
)


# ══════════════════════════════════════════════════════════
# لاگ
# ══════════════════════════════════════════════════════════
LOG_CONFIG: dict[str, Any] = {
    "level": _env_str("LOG_LEVEL", "INFO").upper() or "INFO",
    "json": _env_bool("LOG_JSON", True),
    "to_file": _env_bool("LOG_TO_FILE", True),
    "console": _env_bool("LOG_CONSOLE", True),
    "max_mb": _env_int("LOG_MAX_MB", 10, min_val=1, max_val=1000),
    "backups": _env_int("LOG_BACKUPS", 30, min_val=0, max_val=1000),
}


# ══════════════════════════════════════════════════════════
# پشتیبان‌گیری
# ══════════════════════════════════════════════════════════
BACKUP_CONFIG: dict[str, Any] = {
    "daily_hours": _env_int(
        "DAILY_BACKUP_HOURS", 24, min_val=1, max_val=720
    ),
    "keep_daily": _env_int(
        "KEEP_DAILY_BACKUPS", 10, min_val=1, max_val=1000
    ),
    "keep_full": _env_int(
        "KEEP_FULL_BACKUPS", 2, min_val=1, max_val=100
    ),
    "full_days": _env_int(
        "FULL_BACKUP_DAYS", 7, min_val=1, max_val=365
    ),
    "backup_poke_seconds": _env_int(
        "BACKUP_POKE_SECONDS", 3600, min_val=60, max_val=86400
    ),
}


# ══════════════════════════════════════════════════════════
# گزارش (Reporting)
# ══════════════════════════════════════════════════════════
# 🐛 FIX: یکسان‌سازی همهٔ حالت‌های ZWNJ/space برای روزهای هفته
_WEEKLY_DAY_MAP: dict[str, str] = {
    # انگلیسی کامل
    "saturday": "sat", "sunday": "sun", "monday": "mon",
    "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
    "friday": "fri",
    # انگلیسی کوتاه
    "sat": "sat", "sun": "sun", "mon": "mon",
    "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri",
    # فارسی — همهٔ حالت‌های ZWNJ / فاصله / ساده
    "شنبه": "sat",
    "یکشنبه": "sun", "یک‌شنبه": "sun", "یک شنبه": "sun",
    "دوشنبه": "mon", "دو‌شنبه": "mon", "دو شنبه": "mon",
    "سه‌شنبه": "tue", "سه شنبه": "tue", "سهشنبه": "tue",
    "چهارشنبه": "wed", "چهار‌شنبه": "wed",
    "چهار شنبه": "wed",
    "پنجشنبه": "thu", "پنج‌شنبه": "thu",
    "پنج شنبه": "thu",
    "جمعه": "fri",
}


def _normalize_weekly_day(raw: str) -> str:
    if not raw:
        return "sat"
    key = str(raw).strip().lower()
    # نرمال‌سازی: ZWNJ → فاصله
    key = key.replace("\u200c", " ")
    mapped = _WEEKLY_DAY_MAP.get(key)
    if mapped:
        return mapped
    _warn(f"REPORT_WEEKLY_DAY={raw!r} نامعتبر — استفاده از saturday")
    return "sat"


REPORT: dict[str, Any] = {
    "enabled": _env_bool("REPORT_ENABLED", True),
    "daily_time": _env_str("REPORT_DAILY_TIME", "08:00") or "08:00",
    "weekly_day": _normalize_weekly_day(
        _env_str("REPORT_WEEKLY_DAY", "Saturday")
    ),
    "weekly_time": _env_str("REPORT_WEEKLY_TIME", "08:10") or "08:10",
    "sql_timeout_s": _env_float(
        "REPORT_SQL_TIMEOUT_S", 5.0, min_val=0.5, max_val=300.0
    ),
    "misfire_grace_s": _env_int(
        "REPORT_MISFIRE_GRACE_S", 600, min_val=60, max_val=86400
    ),
}


# ══════════════════════════════════════════════════════════
# Observability
# ══════════════════════════════════════════════════════════
OBS_CONFIG: dict[str, Any] = {
    "enabled": _env_bool("OBS_ENABLED", True),
    "host": _env_str("OBS_HOST", "0.0.0.0") or "0.0.0.0",
    "port": _env_int("OBS_PORT", 8787, min_val=1, max_val=65535),
    "chat_max_len": _env_int(
        "OBS_CHAT_MAX_LEN", 2000, min_val=100, max_val=50000
    ),
    "max_body_bytes": _env_int(
        "OBS_MAX_BODY_BYTES", 10000, min_val=1024, max_val=10_000_000
    ),
    "rate_window_s": _env_int(
        "OBS_RATE_WINDOW", 60, min_val=1, max_val=3600
    ),
    "rate_max": _env_int(
        "OBS_RATE_MAX", 30, min_val=1, max_val=10000
    ),
    "stats_cache_s": _env_int(
        "OBS_STATS_CACHE_S", 10, min_val=0, max_val=600
    ),
    "sys_cache_s": _env_int(
        "OBS_SYS_CACHE_S", 5, min_val=0, max_val=600
    ),
    "health_cache_s": _env_int(
        "OBS_HEALTH_CACHE_S", 30, min_val=0, max_val=600
    ),
    "counts_timeout_s": _env_int(
        "OBS_COUNTS_TIMEOUT_S", 5, min_val=1, max_val=120
    ),
}


# ══════════════════════════════════════════════════════════
# main / login
# ══════════════════════════════════════════════════════════
MAIN_CONFIG: dict[str, Any] = {
    "bale_wait_s": _env_int(
        "MAIN_BALE_WAIT_S", 10, min_val=10, max_val=300
    ),
    "login_input_timeout_s": _env_int(
        "LOGIN_INPUT_TIMEOUT_S", 600, min_val=30, max_val=3600
    ),
    "skip_venv_check": _env_bool("MAIN_SKIP_VENV_CHECK", False),
}

# 🎯 top-level export (سهولت استفاده در login.py, main.py)
MAIN_BALE_WAIT_S: int = MAIN_CONFIG["bale_wait_s"]
LOGIN_INPUT_TIMEOUT_S: int = MAIN_CONFIG["login_input_timeout_s"]
MAIN_SKIP_VENV_CHECK: bool = MAIN_CONFIG["skip_venv_check"]


# ══════════════════════════════════════════════════════════
# SafeBrain
# ══════════════════════════════════════════════════════════
SAFE_BRAIN_CONFIG: dict[str, Any] = {
    "max_api_hour": max(
        0, _env_int("SAFE_BRAIN_MAX_API_HOUR", 0)
    ),
    "ai_timeout_s": _env_float(
        "SAFE_BRAIN_AI_TIMEOUT_S", 45.0,
        min_val=5.0, max_val=300.0,
    ),
}


# ══════════════════════════════════════════════════════════
# ChatOps / Admin — 🎯 top-level export (P0 fix)
# ══════════════════════════════════════════════════════════
ADMIN_ENABLED: bool = _env_bool("ADMIN_ENABLED", True)
ADMIN_PLATFORM: str = _env_str("ADMIN_PLATFORM", "bale") or "bale"
ADMIN_IDS: list[str] = [
    x.strip() for x in _env_str("ADMIN_IDS", "").split(",") if x.strip()
]


# 🎯 FIX: MEMORY_MAX — قبلاً در admin_handler.py تعریف نشده بود
ADMIN_MEMORY_MAX: int = _env_int(
    "ADMIN_MEMORY_MAX", 200, min_val=10, max_val=10_000
)
# نام مستعار برای سازگاری با admin_handler.py
MEMORY_MAX: int = ADMIN_MEMORY_MAX

def get_admin_ids() -> list[str]:
    """لیست user_idهای مدیر (خالص)."""
    return list(ADMIN_IDS)


# ══════════════════════════════════════════════════════════
# پلاگین‌ها
# ══════════════════════════════════════════════════════════
def _build_plugins_config() -> dict[str, Any]:
    return {
        "enabled_plugins": _env_list("ENABLED_PLUGINS"),

        # بله
        "balecollector": {
            "phone": _env_str("BALE_PHONE"),
            "password": _env_str("BALE_PASSWORD"),
            "session_path": os.path.join(SESSIONS_DIR, "bale_web"),
            "headless": _env_bool("BROWSER_HEADLESS", True),
            "poll_seconds": _env_int(
                "BALE_POLL_SECONDS", 60, min_val=10, max_val=86400
            ),
            "max_chats": _env_int(
                "BALE_MAX_CHATS", 0, min_val=0, max_val=1000
            ),
            "chat_open_delay": _env_float(
                "BALE_CHAT_DELAY", 3.0, min_val=0.1, max_val=60.0
            ),
            "messages_per_chat": _env_int(
                "BALE_MESSAGES_PER_CHAT", 300, min_val=5, max_val=1000
            ),
            "backfill_scrolls": _env_int(
                "BALE_BACKFILL_SCROLLS", 6, min_val=0, max_val=100
            ),
            "history_limit": _env_int(
                "BALE_HISTORY_LIMIT", 200, min_val=10, max_val=10000
            ),
            "delay_min": _env_float(
                "BALE_DELAY_MIN", 1.5, min_val=0.0, max_val=60.0
            ),
            "delay_max": _env_float(
                "BALE_DELAY_MAX", 3.0, min_val=0.0, max_val=60.0
            ),
            "between_min": _env_float(
                "BALE_BETWEEN_MIN", 2.0, min_val=0.0, max_val=120.0
            ),
            "between_max": _env_float(
                "BALE_BETWEEN_MAX", 4.0, min_val=0.0, max_val=120.0
            ),
            "typing_min": _env_int(
                "BALE_TYPING_MIN", 40, min_val=0, max_val=1000
            ),
            "typing_max": _env_int(
                "BALE_TYPING_MAX", 120, min_val=0, max_val=1000
            ),
            "ua_suffix": _env_str(
                "BALE_UA_SUFFIX", "SrooyeshLeadMonitor/1.0"
            ),
            "media_dir": os.path.join(MEDIA_DIR, "bale"),
            "enabled": _env_bool("BALE_ENABLED", True),
        },

        # روبیکا
        "rubikacollector": {
            "phone": _env_str("RUBIKA_PHONE"),
            "password": _env_str("RUBIKA_PASSWORD"),
            "session_path": os.path.join(SESSIONS_DIR, "rubika_session"),
            "media_dir": os.path.join(MEDIA_DIR, "rubika"),
            "enabled": _env_bool("RUBIKA_ENABLED", True),
            "poll_seconds": _env_int(
                "RUBIKA_POLL_SECONDS", 90, min_val=5, max_val=3600
            ),
            "history_limit": _env_int(
                "RUBIKA_HISTORY_LIMIT", 200, min_val=10, max_val=10000
            ),
            "max_chats": _env_int(
                "RUBIKA_MAX_CHATS", 50, min_val=0, max_val=1000
            ),
        },

        # ایتا
        "eitaacollector": {
            "phone": _env_str("EITAA_PHONE"),
            "password": _env_str("EITAA_PASSWORD"),
            "session_path": os.path.join(SESSIONS_DIR, "eitaa_web"),
            "media_dir": os.path.join(MEDIA_DIR, "eitaa"),
            "enabled": _env_bool("EITAA_ENABLED", True),
            "poll_seconds": _env_int(
                "EITAA_POLL_SECONDS", 90, min_val=10, max_val=86400
            ),
            "max_chats": _env_int(
                "EITAA_MAX_CHATS", 0, min_val=0, max_val=1000
            ),
            "chat_open_delay": _env_float(
                "EITAA_CHAT_DELAY", 3.0, min_val=0.1, max_val=60.0
            ),
            "messages_per_chat": _env_int_fallback(
                "EITAA_MESSAGES_PER_CHAT",
                "EITAA_MSGS_PER_CHAT",
                10, min_val=5, max_val=1000,
            ),
            "backfill_scrolls": _env_int(
                "EITAA_BACKFILL_SCROLLS", 3, min_val=0, max_val=100
            ),
            "headless": _env_bool("BROWSER_HEADLESS", True),
        },

        # تلگرام
        "telegramcollector": {
            "api_id": _env_str("TELEGRAM_API_ID"),
            "api_hash": _env_str("TELEGRAM_API_HASH"),
            "phone": _env_str("TELEGRAM_PHONE"),
            "session_path": os.path.join(SESSIONS_DIR, "telegram_session"),
            "media_dir": os.path.join(MEDIA_DIR, "telegram"),
            "enabled": _env_bool("TELEGRAM_ENABLED", True),
            "poll_seconds": _env_int(
                "TELEGRAM_POLL_SECONDS", 30, min_val=1, max_val=3600
            ),
            "history_limit": _env_int(
                "TELEGRAM_HISTORY_LIMIT", 3000, min_val=10, max_val=50000
            ),
        },

        # واتساپ
        "whatsappcollector": {
            "phone": _env_str("WHATSAPP_PHONE"),
            "session_path": os.path.join(SESSIONS_DIR, "whatsapp"),
            "media_dir": os.path.join(MEDIA_DIR, "whatsapp"),
            "enabled": _env_bool("WHATSAPP_ENABLED", True),
            "poll_seconds": _env_int(
                "WHATSAPP_POLL_SECONDS", 300, min_val=10, max_val=86400
            ),
            "max_chats": _env_int(
                "WHATSAPP_MAX_CHATS", 15, min_val=0, max_val=1000
            ),
            "chat_open_delay": _env_float(
                "WHATSAPP_CHAT_DELAY", 3.0, min_val=0.1, max_val=60.0
            ),
            "messages_per_chat": _env_int_fallback(
                "WHATSAPP_MESSAGES_PER_CHAT",
                "WHATSAPP_MSGS_PER_CHAT",
                30, min_val=5, max_val=1000,
            ),
            "backfill_scrolls": _env_int(
                "WHATSAPP_BACKFILL_SCROLLS", 6, min_val=0, max_val=100
            ),
            "headless": _env_bool("BROWSER_HEADLESS", True),
        },

        # لینکدین
        "linkedincollector": {
            "email": _env_str("LINKEDIN_EMAIL"),
            "password": _env_str("LINKEDIN_PASSWORD"),
            "session_path": os.path.join(SESSIONS_DIR, "linkedin_v2"),
            "searches_file": str(data_paths.LINKEDIN_SEARCHES),
            "poll_seconds": _env_int(
                "LINKEDIN_POLL_SECONDS", 600,
                min_val=60, max_val=86400,
            ),
            "max_results": _env_int(
                "LINKEDIN_MAX_RESULTS", 15, min_val=1, max_val=100
            ),
            "search_delay": _env_float(
                "LINKEDIN_SEARCH_DELAY", 6.0,
                min_val=1.0, max_val=120.0,
            ),
            "scrolls": _env_int(
                "LINKEDIN_SCROLLS", 3, min_val=0, max_val=50
            ),
            "monitor_feed": _env_bool("LINKEDIN_MONITOR_FEED", True),
            "max_feed_posts": _env_int(
                "LINKEDIN_MAX_FEED_POSTS", 30, min_val=1, max_val=500
            ),
            "enabled": _env_bool("LINKEDIN_ENABLED", True),
            "headless": _env_bool("BROWSER_HEADLESS", True),
            "add_query_to_keywords": _env_bool(
                "LINKEDIN_ADD_QUERY_TO_KEYWORDS", True
            ),
            "feed_url": _env_str(
                "LINKEDIN_FEED_URL",
                "https://www.linkedin.com/feed/",
            ),
            "following_only": _env_bool(
                "LINKEDIN_FOLLOWING_ONLY", True
            ),
            "stop_on_non_follower": _env_bool(
                "LINKEDIN_STOP_ON_NON_FOLLOWER", True
            ),
            "once_per_day": _env_bool(
                "LINKEDIN_ONCE_PER_DAY", True
            ),
            "search_enabled": _env_bool(
                "LINKEDIN_SEARCH_ENABLED", False
            ),
        },

        # اینستاگرام
        "instagramcollector": {
            "session_path": os.path.join(SESSIONS_DIR, "instagram"),
            "media_dir": os.path.join(MEDIA_DIR, "instagram"),
            "enabled": _env_bool("INSTAGRAM_ENABLED", False),
            "headless": _env_bool("BROWSER_HEADLESS", True),
            "poll_seconds": _env_int(
                "INSTAGRAM_POLL_SECONDS", 28800,
                min_val=300, max_val=86400,
            ),
            "max_posts": _env_int(
                "INSTAGRAM_MAX_POSTS", 10, min_val=1, max_val=50
            ),
            "max_stories": _env_int(
                "INSTAGRAM_MAX_STORIES", 20, min_val=1, max_val=100
            ),
            "story_delay_min": _env_float(
                "INSTAGRAM_STORY_DELAY_MIN", 4.0,
                min_val=1.0, max_val=60.0,
            ),
            "story_delay_max": _env_float(
                "INSTAGRAM_STORY_DELAY_MAX", 9.0,
                min_val=2.0, max_val=120.0,
            ),
            "read_stories": _env_bool("INSTAGRAM_READ_STORIES", True),
            "read_posts": _env_bool("INSTAGRAM_READ_POSTS", True),
        },

        # یوتیوب
        "youtubecollector": {
            "session_path": os.path.join(SESSIONS_DIR, "youtube_web"),
            "media_dir": os.path.join(MEDIA_DIR, "youtube"),
            "enabled": _env_bool("YOUTUBE_ENABLED", False),
            "headless": _env_bool("BROWSER_HEADLESS", True),
            "poll_seconds": _env_int(
                "YOUTUBE_POLL_SECONDS", 1800,
                min_val=60, max_val=86400,
            ),
            "max_videos": _env_int(
                "YOUTUBE_MAX_VIDEOS", 20, min_val=1, max_val=100
            ),
            "delay_between": _env_float(
                "YOUTUBE_DELAY_BETWEEN", 3.0,
                min_val=0.5, max_val=30.0,
            ),
        },
        
        # وب
        "webcollector": {
            "sources_file": str(data_paths.WEB_SOURCES),
            "max_pages": _env_int(
                "WEB_MAX_PAGES", 40, min_val=1, max_val=1000
            ),
            "timeout": _env_int(
                "WEB_TIMEOUT", 20, min_val=5, max_val=300
            ),
            "translate": _env_bool("WEB_TRANSLATE", False),
            "enabled": _env_bool("WEB_ENABLED", True),
        },

        # کلیدواژه‌یاب
        "keywordsfinder": {
            "brand_token_enabled": BRAND_TOKEN_ENABLED,
            "brand_token_min_len": BRAND_TOKEN_MIN_LEN,
        },

        # هندلرها
        "storagehandler": {
            "enabled": _env_bool("STORAGE_ENABLED", True),
            "keywords_only": _env_bool("KEYWORDS_ONLY", True),
            "require_t1": _env_bool("STORAGE_REQUIRE_T1", True),
            "allowed_tiers": [
                t.upper()
                for t in _env_list("STORAGE_ALLOWED_TIERS", ["T1"])
            ],
            "save_leads": True,
            "save_media": True,
            "save_self_messages": _env_bool(
                "STORAGE_SAVE_SELF_MESSAGES", True
            ),
        },
        "scoringhandler": {
            "enabled": _env_bool("SCORING_ENABLED", False),
            "min_score_for_alert": _env_int(
                "SCORING_MIN_ALERT", 70, min_val=0, max_val=100
            ),
        },
        "notificationhandler": {
            "enabled": _env_bool("NOTIFY_ENABLED", False),
            "telegram_bot_token": _env_str("NOTIFY_TELEGRAM_TOKEN"),
            "telegram_chat_id": _env_str("NOTIFY_TELEGRAM_CHAT_ID"),
        },

        # ChatOps
        "adminhandler": {
            "admin_ids": list(ADMIN_IDS),
            "platform": ADMIN_PLATFORM,
            "enabled": ADMIN_ENABLED,
            "admin_phone": "",
            "admin_chat": "",
        },

        # پایشگر خودکار
        "selfmonitor": {
            "enabled": SELF_MONITOR_ENABLED,
            "interval_seconds": SELF_MONITOR_INTERVAL,
            "error_threshold": SELF_MONITOR_ERROR_THRESHOLD,
            "stale_minutes": SELF_MONITOR_STALE_MINUTES,
        },
    }


PLUGINS_CONFIG: dict[str, Any] = _build_plugins_config()


# ══════════════════════════════════════════════════════════
# AI (Ollama / DeepSeek)
# ══════════════════════════════════════════════════════════
AI: dict[str, Any] = {
    "enabled": (
        AI_ENABLED and _env_bool("AI_ASSISTANT_ENABLED", True)
    ),
    "priority": AI_PRIORITY,
    "url": _env_str(
        "OLLAMA_URL", "http://127.0.0.1:11434"
    ).rstrip("/"),
    "model": _env_str("OLLAMA_MODEL", "qwen2.5:3b") or "qwen2.5:3b",
    "keep_alive": _env_str("OLLAMA_KEEP_ALIVE", "5s") or "5s",
    "timeout": _env_int("AI_TIMEOUT", 120, min_val=10, max_val=600),
    "deepseek_api_key": _env_str("DEEPSEEK_API_KEY"),
    "available_cache_s": _env_float(
        "AI_AVAILABLE_CACHE_S", 300.0, min_val=0.0, max_val=600.0
    ),
}


# ══════════════════════════════════════════════════════════
# 🎯 AI Prompt — از data/ می‌خونیم (منبع یکتا)
# ══════════════════════════════════════════════════════════

def _load_ai_persona(force: bool = False) -> dict[str, Any]:
    """بارگذاری شخصیت AI از data/configs/ai_persona.json."""
    try:
        from data.loader import load_json
        data = load_json("ai_persona", default={}, force=force)
        if isinstance(data, dict) and "persona" in data:
            return data["persona"]
    except Exception as e:
        _warn(f"ai_persona.json load | {type(e).__name__}: {e}")

    # ─── fallback امن ───
    return {
        "role": "دستیار هوشمند پایش سرنخ",
        "name": "WinDoctor",
        "language": "fa",
        "expertise": [],
        "behavior": {"speak_persian": True},
    }


def _load_ai_system_prompt() -> str:
    """بارگذاری system prompt از data/prompts/ai_system_prompt.md."""
    try:
        path = data_paths.PROMPTS_DIR / "ai_system_prompt.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        _warn(f"ai_system_prompt.md load | {type(e).__name__}: {e}")

    # ─── fallback امن ───
    return (
        "تو «WinDoctor» هستی — دستیار هوشمند پایش سرنخ.\n"
        "همیشه فارسی، دقیق، کوتاه جواب بده."
    )


def _load_ai_mode_prompts(force: bool = False) -> dict[str, str]:
    """بارگذاری prompt های mode از data/configs/ai_mode_prompts.json."""
    try:
        from data.loader import load_json
        data = load_json("ai_mode_prompts", default={}, force=force)
        if isinstance(data, dict) and "modes" in data:
            return data["modes"]
    except Exception as e:
        _warn(f"ai_mode_prompts.json load | {type(e).__name__}: {e}")

    # ─── fallback امن ───
    return {
        "chat": "حالت گفتگو فعال است.",
        "precise": "حالت دقیق فعال است.",
    }


# ─── لود اولیه ───
AI_PERSONA: dict[str, Any] = _load_ai_persona()
AI_SYSTEM_PROMPT: str = _load_ai_system_prompt()
AI_MODE_PROMPTS: dict[str, str] = _load_ai_mode_prompts()


def reload_ai_prompts() -> None:
    """🎯 hot-reload prompt های AI بدون ری‌استارت.

    Example:
        >>> from config import reload_ai_prompts
        >>> reload_ai_prompts()
    """
    global AI_PERSONA, AI_SYSTEM_PROMPT, AI_MODE_PROMPTS

    AI_PERSONA = _load_ai_persona(force=True)
    AI_SYSTEM_PROMPT = _load_ai_system_prompt()
    AI_MODE_PROMPTS = _load_ai_mode_prompts(force=True)

    _warn(
        f"🔄 [config:reload] AI prompt ها | "
        f"persona={AI_PERSONA.get('name')} | "
        f"modes={len(AI_MODE_PROMPTS)}"
    )


def get_ai_persona() -> dict[str, Any]:
    """دسترسی به شخصیت AI."""
    return AI_PERSONA


def get_ai_system_prompt() -> str:
    """دسترسی به system prompt."""
    return AI_SYSTEM_PROMPT


def get_ai_mode_prompt(mode: str) -> str:
    """دسترسی به prompt یک mode خاص."""
    return AI_MODE_PROMPTS.get(mode, "")
    
    
# ══════════════════════════════════════════════════════════
# Brain — 🎯 top-level export برای FULL_ACCESS
# ══════════════════════════════════════════════════════════
FULL_ACCESS: bool = _env_bool("FULL_ACCESS", True)
USE_API_MEMORY: bool = _env_bool("USE_API_MEMORY", True)
MEMORY_API_URL: str = _env_str("MEMORY_API_URL")
MEMORY_API_KEY: str = _env_secret("MEMORY_API_KEY")

BRAIN_CONFIG: dict[str, Any] = {
    "full_access": FULL_ACCESS,
    "use_api_memory": USE_API_MEMORY,
    "memory_api_url": MEMORY_API_URL,
    "memory_api_key": MEMORY_API_KEY,
    "ai_priority": AI_PRIORITY,
    # 🎯 NEW: شخصیت
    "ai_system_prompt": AI_SYSTEM_PROMPT,
    "ai_persona": AI_PERSONA,
}


# ══════════════════════════════════════════════════════════
# هشدار زنده سرنخ
# ══════════════════════════════════════════════════════════
def _parse_tiers(raw: str, default: list[str]) -> list[str]:
    if not raw:
        return list(default)
    valid = {"T1", "T2", "T3", "T0"}
    out: list[str] = []
    seen: set[str] = set()
    for t in str(raw).split(","):
        t_up = t.strip().upper()
        if not t_up:
            continue
        if t_up not in valid:
            _warn(
                f"tier نامعتبر «{t}» در LEAD_ALERT_TIERS — "
                f"معتبر: {sorted(valid)}"
            )
            continue
        if t_up in seen:
            continue
        seen.add(t_up)
        out.append(t_up)
    return out or list(default)


LEAD_ALERTS: dict[str, Any] = {
    "enabled": _env_bool("LEAD_ALERTS_ENABLED", False),
    "min_score": _env_int(
        "LEAD_ALERT_MIN_SCORE", 0, min_val=0, max_val=100
    ),
    "tiers": _parse_tiers(
        _env_str("LEAD_ALERT_TIERS", "T1"), ["T1"]
    ),
}


# ══════════════════════════════════════════════════════════
# توابع کمکی — دسترسی به config پلتفرم‌ها
# ══════════════════════════════════════════════════════════
def get_bale_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("balecollector", {})


def get_rubika_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("rubikacollector", {})


def get_eitaa_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("eitaacollector", {})


def get_telegram_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("telegramcollector", {})


def get_whatsapp_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("whatsappcollector", {})


def get_linkedin_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("linkedincollector", {})


def get_instagram_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("instagramcollector", {})

def get_youtube_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("youtubecollector", {})
    
def get_web_config() -> dict[str, Any]:
    return PLUGINS_CONFIG.get("webcollector", {})


_PLATFORM_KEY_MAP: tuple[tuple[str, str], ...] = (
    ("eitaa", "eitaacollector"),
    ("bale", "balecollector"),
    ("rubika", "rubikacollector"),
    ("telegram", "telegramcollector"),
    ("whatsapp", "whatsappcollector"),
    ("web", "webcollector"),
    ("linkedin", "linkedincollector"),
    ("instagram", "instagramcollector"),
    ("youtube", "youtubecollector"),
)


def get_active_platforms() -> list[str]:
    """پلتفرم‌های فعال — به ترتیب PLATFORMS_ORDER."""
    return [
        name
        for name, key in _PLATFORM_KEY_MAP
        if PLUGINS_CONFIG.get(key, {}).get("enabled", False)
    ]


PLATFORMS_ACTIVE: list[str] = get_active_platforms()
COLLECTORS_CONFIG: dict[str, Any] = PLUGINS_CONFIG


# ══════════════════════════════════════════════════════════
# Config summary logger
# ══════════════════════════════════════════════════════════
def log_config_summary() -> None:
    """لاگ خلاصهٔ تنظیمات — بعد از setup logger صدا بزن."""
    try:
        from core.kernel.logger import log_info
    except Exception:
        return

    try:
        ai_state = "✅" if AI_ENABLED else "❌"
        admin_state = "✅" if ADMIN_ENABLED else "❌"
        obs_state = "✅" if OBS_CONFIG.get("enabled") else "❌"

        log_info(
            f"⚙️ [config] سیستم | "
            f"db={DATABASE_TYPE} | "
            f"ai={ai_state} ({AI_PRIORITY}) | "
            f"admin={admin_state} ({ADMIN_PLATFORM}, "
            f"{len(ADMIN_IDS)} ids) | "
            f"obs={obs_state} "
            f"({OBS_CONFIG['host']}:{OBS_CONFIG['port']})"
        )

        active = PLATFORMS_ACTIVE or []
        if active:
            log_info(f"⚙️ [config] پلتفرم‌های فعال: {active}")
        else:
            log_info("⚠️ [config] هیچ پلتفرمی فعال نیست")

        log_info(
            f"⚙️ [config] retention | "
            f"media={RETENTION['media_days']}d | "
            f"reports={RETENTION['reports_days']}d | "
            f"messages="
            f"{'∞' if RETENTION['messages_days'] == 0 else str(RETENTION['messages_days'])+'d'}"
        )

        la_state = "✅" if LEAD_ALERTS["enabled"] else "❌"
        log_info(
            f"⚙️ [config] lead_alerts | "
            f"enabled={la_state} | "
            f"min_score={LEAD_ALERTS['min_score']} | "
            f"tiers={LEAD_ALERTS['tiers']}"
        )

        if REPORT["enabled"]:
            log_info(
                f"⚙️ [config] report | "
                f"daily={REPORT['daily_time']} | "
                f"weekly={REPORT['weekly_day']} "
                f"{REPORT['weekly_time']}"
            )

        storage_cfg = PLUGINS_CONFIG.get("storagehandler", {})
        if storage_cfg:
            log_info(
                f"⚙️ [config] storage | "
                f"allowed_tiers={storage_cfg.get('allowed_tiers')} | "
                f"require_t1={storage_cfg.get('require_t1')}"
            )

        log_info(
            f"⚙️ [config] monitor | "
            f"interval={MONITOR['interval_seconds']}s | "
            f"cpu={MONITOR['cpu_alert']}% ram={MONITOR['ram_alert']}% "
            f"disk={MONITOR['disk_alert']}%"
        )
        log_info(
            f"⚙️ [config] log | level={LOG_CONFIG['level']} | "
            f"json={LOG_CONFIG['json']} | console={LOG_CONFIG['console']}"
        )
        log_info(
            f"⚙️ [config] backup | daily={BACKUP_CONFIG['daily_hours']}h | "
            f"keep_daily={BACKUP_CONFIG['keep_daily']} | "
            f"keep_full={BACKUP_CONFIG['keep_full']}"
        )

        log_info(
            f"⚙️ [config] safe_brain | "
            f"max_api_hour={SAFE_BRAIN_CONFIG['max_api_hour']} | "
            f"ai_timeout={SAFE_BRAIN_CONFIG['ai_timeout_s']}s"
        )

        # 🎯 NEW: شخصیت AI
        log_info(
            f"⚙️ [config] AI persona | "
            f"role={AI_PERSONA['role']} | "
            f"name={AI_PERSONA['name']} | "
            f"expertise={len(AI_PERSONA['expertise'])} حوزه"
        )

        log_info(
            f"⚙️ [config] paths | "
            f"base={BASE_DIR} | storage={STORAGE_DIR}"
        )
    except Exception as e:
        _warn(
            f"log_config_summary failed | "
            f"{type(e).__name__}: {e}"
        )


# ══════════════════════════════════════════════════════════
# reload (hot-reload)
# ══════════════════════════════════════════════════════════
def reload() -> None:
    """بارگذاری مجدد همهٔ تنظیمات از .env — برای hot-reload."""
    load_dotenv(override=False)

    global AI_ENABLED, EVENT_LOG, AI_PRIORITY
    global DATABASE_TYPE, CHROME_PATH
    global PLATFORMS_ACTIVE
    global ADMIN_ENABLED, ADMIN_PLATFORM, ADMIN_IDS
    global BRAND_TOKEN_ENABLED, BRAND_TOKEN_MIN_LEN
    global MONITOR_ENABLED, MONITOR_INTERVAL_SECONDS
    global SELF_MONITOR_ENABLED, SELF_MONITOR_INTERVAL
    global SELF_MONITOR_ERROR_THRESHOLD, SELF_MONITOR_STALE_MINUTES
    global MAIN_BALE_WAIT_S, LOGIN_INPUT_TIMEOUT_S, MAIN_SKIP_VENV_CHECK
    global FULL_ACCESS, USE_API_MEMORY, MEMORY_API_URL, MEMORY_API_KEY

    # ─── سوییچ‌های اصلی ───
    AI_ENABLED = _env_bool("AI_ENABLED", True)
    EVENT_LOG = _env_bool("EVENT_LOG", False)

    _prio = _env_str("AI_PRIORITY", "auto").lower() or "auto"
    if _prio not in _VALID_AI_PRIORITIES:
        _warn(f"AI_PRIORITY={_prio!r} نامعتبر → auto")
        _prio = "auto"
    AI_PRIORITY = _prio

    _db = _env_str("DATABASE_TYPE", "sqlite").lower()
    if _db not in _VALID_DB_TYPES:
        _warn(f"DATABASE_TYPE={_db!r} نامعتبر → sqlite")
        _db = "sqlite"
    DATABASE_TYPE = _db

    CHROME_PATH = _env_str("CHROME_PATH") or None

    # ─── Admin (top-level) ───
    ADMIN_ENABLED = _env_bool("ADMIN_ENABLED", True)
    ADMIN_PLATFORM = _env_str("ADMIN_PLATFORM", "bale") or "bale"
    ADMIN_IDS = [
        x.strip()
        for x in _env_str("ADMIN_IDS", "").split(",")
        if x.strip()
    ]

    # ─── Brand tokens ───
    BRAND_TOKEN_ENABLED = _env_bool("BRAND_TOKEN_ENABLED", False)
    BRAND_TOKEN_MIN_LEN = _env_int(
        "BRAND_TOKEN_MIN_LEN", 4, min_val=1, max_val=30
    )

    # ─── Brain (top-level) ───
    FULL_ACCESS = _env_bool("FULL_ACCESS", False)
    USE_API_MEMORY = _env_bool("USE_API_MEMORY", False)
    MEMORY_API_URL = _env_str("MEMORY_API_URL")
    MEMORY_API_KEY = _env_secret("MEMORY_API_KEY")

    # ─── main/login ───
    MAIN_BALE_WAIT_S = _env_int(
        "MAIN_BALE_WAIT_S", 10, min_val=10, max_val=300
    )
    LOGIN_INPUT_TIMEOUT_S = _env_int(
        "LOGIN_INPUT_TIMEOUT_S", 600, min_val=30, max_val=3600
    )
    MAIN_SKIP_VENV_CHECK = _env_bool("MAIN_SKIP_VENV_CHECK", False)

    # ─── CRM ───
    CRM_CONFIG.update({
        "sql_timeout_s": _env_float(
            "CRM_SQL_TIMEOUT_S", 5.0, min_val=0.5, max_val=120.0
        ),
    })

    # ─── سیستم ───
    SYSTEM_CONFIG.update({
        "poll_interval_hours": _env_int(
            "SYSTEM_POLL_HOURS", 24, min_val=1, max_val=720
        ),
        "log_level": _env_str("LOG_LEVEL", "INFO").upper() or "INFO",
        "log_to_file": _env_bool("LOG_TO_FILE", True),
    })

    # ─── Retention ───
    RETENTION.update({
        "media_days": max(0, _env_int("KEEP_MEDIA_DAYS", 7)),
        "reports_days": max(0, _env_int("KEEP_REPORTS_DAYS", 30)),
        "messages_days": max(0, _env_int("KEEP_MESSAGES_DAYS", 0)),
        "cleanup_interval_hours": max(
            1, _env_int("CLEANUP_INTERVAL_HOURS", 24)
        ),
        "cleanup_min_interval_hours": max(
            1, _env_int("CLEANUP_MIN_INTERVAL_HOURS", 1)
        ),
        "cleanup_max_interval_hours": max(
            1, _env_int("CLEANUP_MAX_INTERVAL_HOURS", 720)
        ),
    })

    # ─── Monitor ───
    MONITOR.update({
        "enabled": _env_bool("MONITOR_ENABLED", True),
        "interval_seconds": _env_int(
            "MONITOR_INTERVAL_SECONDS", 1800, min_val=5, max_val=86400
        ),
        "cpu_alert": _env_float(
            "MONITOR_CPU_ALERT", 90.0, min_val=1.0, max_val=100.0
        ),
        "ram_alert": _env_float(
            "MONITOR_RAM_ALERT", 90.0, min_val=1.0, max_val=100.0
        ),
        "disk_alert": _env_float(
            "MONITOR_DISK_ALERT", 70.0, min_val=1.0, max_val=100.0
        ),
    })
    MONITOR_ENABLED = MONITOR["enabled"]
    MONITOR_INTERVAL_SECONDS = MONITOR["interval_seconds"]

    # ─── Self-Monitor ───
    SELF_MONITOR.update({
        "enabled": _env_bool("SELF_MONITOR_ENABLED", True),
        "interval_seconds": _env_int(
            "SELF_MONITOR_INTERVAL", 600,
            min_val=60, max_val=86400,
        ),
        "error_threshold": _env_int(
            "SELF_MONITOR_ERROR_THRESHOLD", 5,
            min_val=1, max_val=1000,
        ),
        "stale_minutes": _env_int(
            "SELF_MONITOR_STALE_MINUTES", 60,
            min_val=5, max_val=10080,
        ),
    })
    SELF_MONITOR_ENABLED = SELF_MONITOR["enabled"]
    SELF_MONITOR_INTERVAL = SELF_MONITOR["interval_seconds"]
    SELF_MONITOR_ERROR_THRESHOLD = SELF_MONITOR["error_threshold"]
    SELF_MONITOR_STALE_MINUTES = SELF_MONITOR["stale_minutes"]

    # ─── Keywords ───
    KEYWORDS_CONFIG.update({
        "reload_seconds": _env_float(
            "KEYWORDS_RELOAD_SECONDS", 120.0,
            min_val=1.0, max_val=3600.0,
        ),
    })

    # ─── Log ───
    LOG_CONFIG.update({
        "level": _env_str("LOG_LEVEL", "INFO").upper() or "INFO",
        "json": _env_bool("LOG_JSON", True),
        "to_file": _env_bool("LOG_TO_FILE", True),
        "console": _env_bool("LOG_CONSOLE", True),
        "max_mb": _env_int("LOG_MAX_MB", 10, min_val=1, max_val=1000),
        "backups": _env_int("LOG_BACKUPS", 30, min_val=0, max_val=1000),
    })

    # ─── Backup ───
    BACKUP_CONFIG.update({
        "daily_hours": _env_int(
            "DAILY_BACKUP_HOURS", 24, min_val=1, max_val=720
        ),
        "keep_daily": _env_int(
            "KEEP_DAILY_BACKUPS", 10, min_val=1, max_val=1000
        ),
        "keep_full": _env_int(
            "KEEP_FULL_BACKUPS", 2, min_val=1, max_val=100
        ),
        "full_days": _env_int(
            "FULL_BACKUP_DAYS", 7, min_val=1, max_val=365
        ),
        "backup_poke_seconds": _env_int(
            "BACKUP_POKE_SECONDS", 3600, min_val=60, max_val=86400
        ),
    })

    # ─── Report ───
    REPORT.update({
        "enabled": _env_bool("REPORT_ENABLED", True),
        "daily_time": (
            _env_str("REPORT_DAILY_TIME", "08:00") or "08:00"
        ),
        "weekly_day": _normalize_weekly_day(
            _env_str("REPORT_WEEKLY_DAY", "Saturday")
        ),
        "weekly_time": (
            _env_str("REPORT_WEEKLY_TIME", "08:10") or "08:10"
        ),
        "sql_timeout_s": _env_float(
            "REPORT_SQL_TIMEOUT_S", 5.0, min_val=0.5, max_val=300.0
        ),
        "misfire_grace_s": _env_int(
            "REPORT_MISFIRE_GRACE_S", 600, min_val=60, max_val=86400
        ),
    })

    # ─── OBS ───
    OBS_CONFIG.update({
        "enabled": _env_bool("OBS_ENABLED", True),
        "host": _env_str("OBS_HOST", "0.0.0.0") or "0.0.0.0",
        "port": _env_int(
            "OBS_PORT", 8787, min_val=1, max_val=65535
        ),
        "chat_max_len": _env_int(
            "OBS_CHAT_MAX_LEN", 2000, min_val=100, max_val=50000
        ),
        "max_body_bytes": _env_int(
            "OBS_MAX_BODY_BYTES", 10000, min_val=1024, max_val=10_000_000
        ),
        "rate_window_s": _env_int(
            "OBS_RATE_WINDOW", 60, min_val=1, max_val=3600
        ),
        "rate_max": _env_int(
            "OBS_RATE_MAX", 30, min_val=1, max_val=10000
        ),
        "stats_cache_s": _env_int(
            "OBS_STATS_CACHE_S", 10, min_val=0, max_val=600
        ),
        "sys_cache_s": _env_int(
            "OBS_SYS_CACHE_S", 5, min_val=0, max_val=600
        ),
        "health_cache_s": _env_int(
            "OBS_HEALTH_CACHE_S", 30, min_val=0, max_val=600
        ),
        "counts_timeout_s": _env_int(
            "OBS_COUNTS_TIMEOUT_S", 5, min_val=1, max_val=120
        ),
    })

    # ─── main ───
    MAIN_CONFIG.update({
        "bale_wait_s": MAIN_BALE_WAIT_S,
        "login_input_timeout_s": LOGIN_INPUT_TIMEOUT_S,
        "skip_venv_check": MAIN_SKIP_VENV_CHECK,
    })

    # ─── SafeBrain ───
    SAFE_BRAIN_CONFIG.update({
        "max_api_hour": max(
            0, _env_int("SAFE_BRAIN_MAX_API_HOUR", 0)
        ),
        "ai_timeout_s": _env_float(
            "SAFE_BRAIN_AI_TIMEOUT_S", 45.0,
            min_val=5.0, max_val=300.0,
        ),
    })

    # ─── AI ───
    AI.update({
        "enabled": (
            AI_ENABLED and _env_bool("AI_ASSISTANT_ENABLED", True)
        ),
        "priority": AI_PRIORITY,
        "url": _env_str(
            "OLLAMA_URL", "http://127.0.0.1:11434"
        ).rstrip("/"),
        "model": (
            _env_str("OLLAMA_MODEL", "qwen2.5:3b") or "qwen2.5:3b"
        ),
        "keep_alive": (
            _env_str("OLLAMA_KEEP_ALIVE", "5s") or "5s"
        ),
        "timeout": _env_int(
            "AI_TIMEOUT", 120, min_val=10, max_val=600
        ),
        "deepseek_api_key": _env_str("DEEPSEEK_API_KEY"),
        "available_cache_s": _env_float(
            "AI_AVAILABLE_CACHE_S", 300.0,
            min_val=0.0, max_val=600.0,
        ),
    })

    # ─── Brain ───
    BRAIN_CONFIG.update({
        "full_access": FULL_ACCESS,
        "use_api_memory": USE_API_MEMORY,
        "memory_api_url": MEMORY_API_URL,
        "memory_api_key": MEMORY_API_KEY,
        "ai_priority": AI_PRIORITY,
        "ai_system_prompt": AI_SYSTEM_PROMPT,
        "ai_persona": AI_PERSONA,
    })

    # ─── Lead alerts ───
    LEAD_ALERTS.update({
        "enabled": _env_bool("LEAD_ALERTS_ENABLED", False),
        "min_score": _env_int(
            "LEAD_ALERT_MIN_SCORE", 0, min_val=0, max_val=100
        ),
        "tiers": _parse_tiers(
            _env_str("LEAD_ALERT_TIERS", "T1"), ["T1"]
        ),
    })

    # ─── Plugins ───
    new_plugins = _build_plugins_config()
    PLUGINS_CONFIG.clear()
    PLUGINS_CONFIG.update(new_plugins)

    PLATFORMS_ACTIVE = get_active_platforms()


# ══════════════════════════════════════════════════════════
# Public API (برای Health Check، IDE، ابزارهای تحلیل)
# ══════════════════════════════════════════════════════════
__all__ = [
    # ─── مسیرها ───
    "BASE_DIR", "CHROME_PATH", "STORAGE_DIR",
    "DB_DIR", "SESSIONS_DIR", "LOGS_DIR", "PYCACHE_DIR",
    "MEDIA_DIR", "REPORTS_DIR", "BACKUPS_DIR",
    # ─── ثابت‌های سراسری ───
    "PLATFORMS_ORDER", "ALL_PLATFORMS", "PLATFORMS_ACTIVE",
    # ─── سوییچ‌های اصلی ───
    "AI_ENABLED", "AI_PRIORITY", "EVENT_LOG", "DATABASE_TYPE",
    # ─── Admin ───
    "ADMIN_ENABLED", "ADMIN_PLATFORM", "ADMIN_IDS",
    "ADMIN_MEMORY_MAX", "MEMORY_MAX",    
    # ─── Brand token ───
    "BRAND_TOKEN_ENABLED", "BRAND_TOKEN_MIN_LEN",
    # ─── Monitor ───
    "MONITOR_ENABLED", "MONITOR_INTERVAL_SECONDS",
    "SELF_MONITOR_ENABLED", "SELF_MONITOR_INTERVAL",
    "SELF_MONITOR_ERROR_THRESHOLD", "SELF_MONITOR_STALE_MINUTES",
    # ─── main/login ───
    "MAIN_BALE_WAIT_S", "LOGIN_INPUT_TIMEOUT_S", "MAIN_SKIP_VENV_CHECK",
    # ─── Brain ───
    "FULL_ACCESS", "USE_API_MEMORY",
    "MEMORY_API_URL", "MEMORY_API_KEY",
    # ─── AI Persona (NEW) ───
    "AI_PERSONA", "AI_SYSTEM_PROMPT", "AI_MODE_PROMPTS",
    "get_ai_system_prompt", "get_ai_persona", "get_ai_mode_prompt",
    # ─── Config dicts ───
    "SQLITE_CONFIG", "POSTGRES_CONFIG", "CRM_CONFIG",
    "SYSTEM_CONFIG", "RETENTION",
    "MONITOR", "SELF_MONITOR",
    "KEYWORDS_CONFIG", "LOG_CONFIG", "BACKUP_CONFIG",
    "REPORT", "OBS_CONFIG", "MAIN_CONFIG", "SAFE_BRAIN_CONFIG",
    "PLUGINS_CONFIG", "COLLECTORS_CONFIG",
    "AI", "BRAIN_CONFIG", "LEAD_ALERTS",
    # ─── توابع کمکی ───
    "get_proxy", "get_browser_proxy",
    "get_admin_ids",
    "get_bale_config", "get_rubika_config", "get_eitaa_config",
    "get_telegram_config", "get_whatsapp_config",
    "get_linkedin_config", "get_instagram_config", "get_web_config",
    "get_youtube_config",
    "get_active_platforms",
    "log_config_summary", "reload",
]
