# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv

load_dotenv()

# ==================================================
# مسیرهای پایه پروژه
# ==================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# مسیر اختیاریِ مرورگر برای اسکرپرهای Playwright (ایتا/واتساپ/لینکدین).
# اگر خالی باشد، خودکار از Chrome/Edge/Chromium نصب‌شده استفاده می‌شود (قابل‌حمل بین ویندوز/سرور لینوکس).
# اگر لازم شد مسیر دستی بده: CHROME_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
CHROME_PATH = (os.getenv("CHROME_PATH", "") or "").strip() or None

# همه چیز توی یک پوشه به اسم storage
STORAGE_DIR = os.path.join(BASE_DIR, "storage")

# زیرپوشه‌های داخل storage (همه‌ی فایل‌های تولیدشده مرتب اینجا کنار هم می‌مانند)
DB_DIR = os.path.join(STORAGE_DIR, "database")
SESSIONS_DIR = os.path.join(STORAGE_DIR, "sessions")
LOGS_DIR = os.path.join(STORAGE_DIR, "logs")
PYCACHE_DIR = os.path.join(STORAGE_DIR, "pycache")
MEDIA_DIR = os.path.join(STORAGE_DIR, "media")
REPORTS_DIR = os.path.join(STORAGE_DIR, "reports")
BACKUPS_DIR = os.path.join(STORAGE_DIR, "backups")

# ایجاد خودکار همه پوشه‌ها
for d in [DB_DIR, SESSIONS_DIR, LOGS_DIR, PYCACHE_DIR, MEDIA_DIR, REPORTS_DIR, BACKUPS_DIR]:
    os.makedirs(d, exist_ok=True)

# ==================================================
# تنظیمات دیتابیس
# ==================================================
DATABASE_TYPE = os.getenv("DATABASE_TYPE", "sqlite").strip().lower()

SQLITE_CONFIG = {
    "database_path": os.path.join(DB_DIR, "messages.db"),
}

POSTGRES_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "lead_monitor"),
}

# ==================================================
# تنظیمات کلی سیستم
# ==================================================
SYSTEM_CONFIG = {
    "poll_interval_hours": 24,
    "log_level": "INFO",
    "log_to_file": True,
    "log_directory": LOGS_DIR,
    "sessions_directory": SESSIONS_DIR,
    "temp_directory": STORAGE_DIR,
    "media_directory": MEDIA_DIR,
}

# ==================================================
# تنظیمات نگه‌داری و پاک‌سازی خودکار (Retention) — قابل تنظیم در .env
# ۰ یعنی هرگز پاک نشود
# ==================================================
RETENTION = {
    "media_days": int(os.getenv("KEEP_MEDIA_DAYS", 30)),          # عکس/فایل بعد از این تعداد روز پاک
    "reports_days": int(os.getenv("KEEP_REPORTS_DAYS", 30)),      # گزارش‌ها بعد از این تعداد روز پاک
    "messages_days": int(os.getenv("KEEP_MESSAGES_DAYS", 0)),     # پیام‌های دیتابیس (۰ = همیشه نگه‌دار)
    "cleanup_interval_hours": int(os.getenv("CLEANUP_INTERVAL_HOURS", 24)),  # هر چند ساعت پاک‌سازی اجرا شود
}

# ==================================================
# تنظیمات مانیتورینگ سیستم — قابل تنظیم در .env
# ==================================================
MONITOR = {
    "enabled": os.getenv("MONITOR_ENABLED", "true").lower() == "true",
    "interval_seconds": int(os.getenv("MONITOR_INTERVAL_SECONDS", 30)),  # قبلاً ۵ بود (پرنویز)
    "cpu_alert": float(os.getenv("MONITOR_CPU_ALERT", 95)),   # فقط بالای این درصد هشدار
    "ram_alert": float(os.getenv("MONITOR_RAM_ALERT", 92)),
    "disk_alert": float(os.getenv("MONITOR_DISK_ALERT", 92)),
}

# ==================================================
# تنظیمات گزارش خودکار — قابل تنظیم در .env
# ==================================================
REPORT = {
    "enabled": os.getenv("REPORT_ENABLED", "true").lower() == "true",
    "daily_time": os.getenv("REPORT_DAILY_TIME", "08:00"),    # ساعت گزارش روزانه (HH:MM)
    "weekly_day": os.getenv("REPORT_WEEKLY_DAY", "sunday"),   # روز گزارش هفتگی
    "weekly_time": os.getenv("REPORT_WEEKLY_TIME", "09:00"),  # ساعت گزارش هفتگی
}

# ==================================================
# تنظیمات گزارشات (REPORTS_DIR بالاتر زیر storage تعریف شد)
# ==================================================

# ==================================================
# تنظیمات پلاگین‌ها
# ==================================================
# نام پلاگین‌هایی که باید فعال شوند (خالی = همه پلاگین‌ها)
# برای غیرفعال کردن یک پلاگین خاص، آن را در بخش مربوطه enabled=False قرار دهید
PLUGINS_CONFIG = {
    "enabled_plugins": [],  # لیست نام پلاگین‌های فعال (اختیاری)

    # ========== پیام‌رسان بله ==========
    "balecollector": {
        "type": os.getenv("BALE_TYPE", "user"),
        "username": os.getenv("BALE_USERNAME"),
        "password": os.getenv("BALE_PASSWORD"),
        "phone": os.getenv("BALE_PHONE"),
        "session_path": os.path.join(SESSIONS_DIR, "bale", "bale_session"),
        "media_dir": os.path.join(MEDIA_DIR, "bale"),
        "enabled": os.getenv("BALE_ENABLED", "true").lower() == "true"
    },

    # ========== پیام‌رسان روبیکا ==========
    "rubikacollector": {
        "phone": os.getenv("RUBIKA_PHONE"),
        "password": os.getenv("RUBIKA_PASSWORD"),
        "session_path": os.path.join(SESSIONS_DIR, "rubika_session"),
        "media_dir": os.path.join(MEDIA_DIR, "rubika"),
        "enabled": os.getenv("RUBIKA_ENABLED", "false").lower() == "true"
    },

    # ========== پیام‌رسان ایتا ==========
    "eitaacollector": {
        "phone": os.getenv("EITAA_PHONE"),
        "password": os.getenv("EITAA_PASSWORD"),
        "session_path": os.path.join(SESSIONS_DIR, "eitaa_session"),
        "media_dir": os.path.join(MEDIA_DIR, "eitaa"),
        "enabled": os.getenv("EITAA_ENABLED", "false").lower() == "true",
        # ایتا یک اسکرپرِ مرورگری سنگین است؛ نباید مثل کلکتورهای لحظه‌ای هر ۲ ثانیه
        # اجرا شود (همین باعث مصرف زیاد CPU/RAM و رصد تکراری پیام‌های قدیمی بود).
        "poll_seconds": int(os.getenv("EITAA_POLL_SECONDS", 180)),   # هر چند ثانیه یک اسکرپ
        "max_chats": int(os.getenv("EITAA_MAX_CHATS", 15)),          # حداکثر چت در هر دور
        "chat_open_delay": float(os.getenv("EITAA_CHAT_DELAY", 1.5)),# مکث بعد از باز کردن هر چت
        "messages_per_chat": int(os.getenv("EITAA_MSGS_PER_CHAT", 30)),
        # اسکرول به بالا در اولین اسکرپ بعد از اتصال، برای خواندن پیام‌های قدیمی‌تر (catch-up)
        "backfill_scrolls": int(os.getenv("EITAA_BACKFILL_SCROLLS", 6)),
    },

    # ========== پیام‌رسان تلگرام ==========
    "telegramcollector": {
        "api_id": os.getenv("TELEGRAM_API_ID"),
        "api_hash": os.getenv("TELEGRAM_API_HASH"),
        "phone": os.getenv("TELEGRAM_PHONE"),
        "session_path": os.path.join(SESSIONS_DIR, "telegram_session"),
        "media_dir": os.path.join(MEDIA_DIR, "telegram"),
        "enabled": os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
    },

    # ========== پیام‌رسان واتساپ ==========
    "whatsappcollector": {
        "phone": os.getenv("WHATSAPP_PHONE"),
        "session_path": os.path.join(SESSIONS_DIR, "whatsapp_session"),
        "media_dir": os.path.join(MEDIA_DIR, "whatsapp"),
        "enabled": os.getenv("WHATSAPP_ENABLED", "false").lower() == "true",
        # واتساپ هم اسکرپرِ مرورگری سنگین است (مثل ایتا) — کند اجرا شود
        "poll_seconds": int(os.getenv("WHATSAPP_POLL_SECONDS", 180)),
        "max_chats": int(os.getenv("WHATSAPP_MAX_CHATS", 15)),
        "chat_open_delay": float(os.getenv("WHATSAPP_CHAT_DELAY", 1.5)),
        "messages_per_chat": int(os.getenv("WHATSAPP_MSGS_PER_CHAT", 30)),
        "backfill_scrolls": int(os.getenv("WHATSAPP_BACKFILL_SCROLLS", 6)),
        "headless": os.getenv("BROWSER_HEADLESS", "true").lower() == "true",
    },

    # ========== لینکدین (مرورگری، به‌عنوان کاربر) ==========
    "linkedincollector": {
        "email": os.getenv("LINKEDIN_EMAIL"),
        "password": os.getenv("LINKEDIN_PASSWORD"),
        "searches_file": os.path.join(BASE_DIR, "data", "linkedin_searches.txt"),
        "poll_seconds": int(os.getenv("LINKEDIN_POLL_SECONDS", 600)),   # محافظه‌کارانه (۱۰ دقیقه)
        "max_results": int(os.getenv("LINKEDIN_MAX_RESULTS", 15)),
        "search_delay": float(os.getenv("LINKEDIN_SEARCH_DELAY", 6)),   # مکث پایه بین جستجوها
        "scrolls": int(os.getenv("LINKEDIN_SCROLLS", 3)),
        # پایشِ فیدِ خانه (پست‌های پیج‌ها/افرادی که فالو کرده‌ای) — علاوه بر سرچ
        "monitor_feed": os.getenv("LINKEDIN_MONITOR_FEED", "true").lower() == "true",
        "max_feed_posts": int(os.getenv("LINKEDIN_MAX_FEED_POSTS", 25)),
        "enabled": os.getenv("LINKEDIN_ENABLED", "false").lower() == "true",
        "headless": os.getenv("BROWSER_HEADLESS", "true").lower() == "true",
    },

    # ========== منابع وب (سایت/صفحه) ==========
    "webcollector": {
        "sources_file": os.path.join(BASE_DIR, "data", "web_sources.txt"),
        "max_pages": int(os.getenv("WEB_MAX_PAGES", 40)),
        "timeout": int(os.getenv("WEB_TIMEOUT", 20)),
        "translate": os.getenv("WEB_TRANSLATE", "false").lower() == "true",  # ترجمهٔ فارسی + واژه‌نامه
        "enabled": os.getenv("WEB_ENABLED", "false").lower() == "true",
    },

    # ========== هندلرها ==========
    "storagehandler": {
        "enabled": True,
        # فقط پیام‌هایی که کلیدواژه دارند ذخیره شوند (هستهٔ خواستهٔ کاربر).
        # اگر روزی خواستی همهٔ پیام‌ها ذخیره شوند، KEYWORDS_ONLY=false در .env بگذار.
        "keywords_only": os.getenv("KEYWORDS_ONLY", "true").lower() == "true",
        "save_leads": True,
        "save_media": True,
    },
    
    "scoringhandler": {
        "enabled": False,
        "min_score_for_alert": 70,
    },
    
    "notificationhandler": {
        "enabled": False,
        "telegram_bot_token": os.getenv("NOTIFY_TELEGRAM_TOKEN"),
        "telegram_chat_id": os.getenv("NOTIFY_TELEGRAM_CHAT_ID"),
    },

    # ========== مدیریتِ ربات از طریقِ پیام‌رسان (ChatOps) ==========
    "adminhandler": {
        # فقط این کاربران (user_id) اجازهٔ دستوردادن دارند. خالی = حالتِ pairing
        # (بات آی‌دیِ هر کسی که پیام دهد را در لاگ می‌نویسد تا آن را اینجا بگذاری).
        "admin_ids": [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()],
        "platform": os.getenv("ADMIN_PLATFORM", "bale").strip().lower(),  # روی کدام پیام‌رسان
        "enabled": os.getenv("ADMIN_ENABLED", "true").lower() == "true",
    },

    # ========== پایشگرِ خودکار (proactive self-monitor) ==========
    "selfmonitor": {
        "enabled": os.getenv("SELF_MONITOR_ENABLED", "true").lower() == "true",
        "interval_seconds": int(os.getenv("SELF_MONITOR_INTERVAL", 600)),  # هر چند ثانیه چک کند
        "error_threshold": int(os.getenv("SELF_MONITOR_ERROR_THRESHOLD", 5)),  # چند خطا = هشدار
        "stale_minutes": int(os.getenv("SELF_MONITOR_STALE_MINUTES", 60)),  # بی‌فعالیتیِ پلتفرم
    },
}

# ==================================================
# هوشِ محلی (Ollama) برای فهمِ متنِ آزادِ دستورهای مدیریت
# on-demand صدا زده می‌شود؛ رمِ همیشگی صفر. مدل از .env (برای همهٔ نسخه‌ها یکسان).
# ==================================================
AI = {
    "enabled": os.getenv("AI_ASSISTANT_ENABLED", "true").lower() == "true",
    "url": os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
    "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
    "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30s"),
    "timeout": int(os.getenv("AI_TIMEOUT", "60")),
}

# هشدارِ زندهٔ سرنخ: لحظه‌ای که سرنخی پیدا شد، به مدیر در بله پیام بده.
# فعلاً به‌خواستهٔ کاربر خاموش است (کد می‌ماند برای بعد). با LEAD_ALERTS_ENABLED=true روشن می‌شود.
LEAD_ALERTS = {
    "enabled": os.getenv("LEAD_ALERTS_ENABLED", "false").lower() == "true",
    "min_score": int(os.getenv("LEAD_ALERT_MIN_SCORE", "0")),   # فقط سرنخِ بالاتر از این امتیاز
}

# ==================================================
# توابع کمکی
# ==================================================

def get_bale_config():
    return PLUGINS_CONFIG.get("balecollector", {})

def get_rubika_config():
    return PLUGINS_CONFIG.get("rubikacollector", {})

def get_eitaa_config():
    return PLUGINS_CONFIG.get("eitaacollector", {})

def get_telegram_config():
    return PLUGINS_CONFIG.get("telegramcollector", {})

def get_whatsapp_config():
    return PLUGINS_CONFIG.get("whatsappcollector", {})

COLLECTORS_CONFIG = PLUGINS_CONFIG