# data/paths.py
"""
مسیرهای متمرکز داده — تنها منبع حقیقت.

نسخهٔ ۲ (این نسخه):
  - 🎯 افزودن مسیرهای جدید برای حذف hardcode از فایل‌های دیگر:
      * REQUESTS_FILE       → درخواست‌های قابلیت (admin_actions, safe_brain, panel)
      * AI_MEMORY           → حافظهٔ گفتگوی ChatOps (admin_handler)
      * REPORT_CACHE        → کش گزارش روزانه (daily_reporter)
      * FORMULA_AUDIT       → audit log فرمول‌ها (formula_manager)
      * BOT_PID             → PID ربات (panel, login, admin_actions)
      * LEARNED_PATTERNS    → الگوهای یادگیری Brain (brain/memory)
      * BRAIN_PLANS         → پلن‌های Brain (brain/learning)
  - 🎯 افزودن مسیرهای storage فرعی برای وضوح:
      * LOG_FILE            → storage/logs/app.jsonl
      * DB_FILE             → storage/database/messages.db
      * BRAIN_DIR, SAFE_BRAIN_DIR
  - 🎯 افزودن توابع کمکی:
      * ensure_runtime_dirs() → ساخت پوشه‌های runtime
  - 🎯 مستندسازی هر گروه با کامنت واضح.
  - ✅ حفظ همهٔ مسیرهای نسخهٔ ۱ (سازگاری عقب‌رو).
"""
from __future__ import annotations

from pathlib import Path

# ══════════════════════════════════════════════════════════
# پایه
# ══════════════════════════════════════════════════════════
DATA_DIR: Path = Path(__file__).parent
PROJECT_ROOT: Path = DATA_DIR.parent
STORAGE_DIR: Path = PROJECT_ROOT / "storage"


# ══════════════════════════════════════════════════════════
# محصولات (کد پایتون — سر جاشون)
# ══════════════════════════════════════════════════════════
PRODUCTS_PY: Path = DATA_DIR / "products.py"
PRODUCTS_EN_PY: Path = DATA_DIR / "products_en.py"


# ══════════════════════════════════════════════════════════
# configs/ — فایل‌های تنظیمات ساختاری (JSON)
# ══════════════════════════════════════════════════════════
CONFIGS_DIR: Path = DATA_DIR / "configs"
ACCESS_CONTROL: Path = CONFIGS_DIR / "access_control.json"
RULES: Path = CONFIGS_DIR / "rules.json"
QUERIES: Path = CONFIGS_DIR / "queries.json"
SETTINGS: Path = CONFIGS_DIR / "settings.json"
SYNONYMS: Path = CONFIGS_DIR / "synonyms.json"
TEMPLATES: Path = CONFIGS_DIR / "templates.json"


# ══════════════════════════════════════════════════════════
# schemas/ — JSON schemas (برای validation)
# ══════════════════════════════════════════════════════════
SCHEMAS_DIR: Path = DATA_DIR / "schemas"
RULE_SCHEMA: Path = SCHEMAS_DIR / "rule.schema.json"
SETTINGS_SCHEMA: Path = SCHEMAS_DIR / "settings.schema.json"


# ══════════════════════════════════════════════════════════
# prompts/ — شخصیت و دانش AI
# ══════════════════════════════════════════════════════════
PROMPTS_DIR: Path = DATA_DIR / "prompts"
PERSONA: Path = PROMPTS_DIR / "persona.md"
SYSTEM: Path = PROMPTS_DIR / "system.md"
BOT_KNOWLEDGE: Path = PROMPTS_DIR / "bot_knowledge.md"


# ══════════════════════════════════════════════════════════
# sources/ — کاربر ویرایش می‌کند (داده‌های ورودی)
# ══════════════════════════════════════════════════════════
SOURCES_DIR: Path = DATA_DIR / "sources"
GLOSSARY: Path = SOURCES_DIR / "glossary.txt"
LINKEDIN_SEARCHES: Path = SOURCES_DIR / "linkedin_searches.txt"
WEB_SOURCES: Path = SOURCES_DIR / "web_sources.txt"
TARGET_GROUPS: Path = SOURCES_DIR / "target_groups.txt"


# ══════════════════════════════════════════════════════════
# runtime/ — ربات می‌نویسد (داده‌های runtime)
# ══════════════════════════════════════════════════════════
RUNTIME_DIR: Path = DATA_DIR / "runtime"
KEYWORDS_CUSTOM: Path = RUNTIME_DIR / "keywords_custom.json"
CANDIDATE_RULES: Path = RUNTIME_DIR / "candidate_rules.json"
LEARNED_RULES: Path = RUNTIME_DIR / "learned_rules.json"
RULE_STATS: Path = RUNTIME_DIR / "rule_stats.json"
UNKNOWN_QUESTIONS: Path = RUNTIME_DIR / "unknown_questions.jsonl"
MY_FORMULAS: Path = RUNTIME_DIR / "my_formulas.json"


# ══════════════════════════════════════════════════════════
# storage/ — فایل‌های runtime (پایدار، ماندگار)
# ══════════════════════════════════════════════════════════

# ─── لاگ ───
LOGS_DIR: Path = STORAGE_DIR / "logs"
LOG_FILE: Path = LOGS_DIR / "app.jsonl"
"""فایل لاگ اصلی (JSON Lines) — برای خواندن سریع از انتها."""

# ─── دیتابیس ───
DB_DIR: Path = STORAGE_DIR / "database"
DB_FILE: Path = DB_DIR / "messages.db"
"""فایل دیتابیس SQLite اصلی."""

REPORT_CACHE: Path = DB_DIR / "report_cache.json"
"""کش گزارش‌های روزانه (daily_reporter) — پایدار بین ری‌استارت‌ها."""

# ─── نشست‌ها ───
SESSIONS_DIR: Path = STORAGE_DIR / "sessions"

# ─── رسانه و گزارش ───
MEDIA_DIR: Path = STORAGE_DIR / "media"
REPORTS_DIR: Path = STORAGE_DIR / "reports"

# ─── بکاپ ───
BACKUPS_DIR: Path = STORAGE_DIR / "backups"

# ─── PID ───
BOT_PID: Path = STORAGE_DIR / "bot.pid"
"""PID فایل ربات — برای panel.py و login.py."""

# ─── درخواست‌های قابلیت ───
REQUESTS_FILE: Path = STORAGE_DIR / "requests.jsonl"
"""درخواست‌های قابلیت جدید (admin_actions, safe_brain)."""

# ─── حافظهٔ ChatOps ───
AI_MEMORY: Path = STORAGE_DIR / "ai_memory.jsonl"
"""حافظهٔ گفتگوی ChatOps (admin_handler)."""

# ─── offset (seen_ids, chat_offsets) ───
OFFSET_DIR: Path = STORAGE_DIR / "offset"
"""پوشهٔ offset — seen_ids و chat_offsets هر پلتفرم."""

FORMULA_AUDIT: Path = OFFSET_DIR / "formula_audit.jsonl"
"""Audit log اجرای فرمول‌های SQL (formula_manager)."""


# ══════════════════════════════════════════════════════════
# Brain — حافظه و کش
# ══════════════════════════════════════════════════════════
BRAIN_DIR: Path = STORAGE_DIR / "brain"
BRAIN_MEMORY_DIR: Path = BRAIN_DIR / "memory"
BRAIN_CACHE_DIR: Path = BRAIN_DIR / "cache"
BRAIN_BACKUP_DIR: Path = BRAIN_DIR / "backups"
BRAIN_HOOKS_DIR: Path = BRAIN_DIR / "hooks"

LEARNED_PATTERNS: Path = BRAIN_DIR / "learned_patterns.json"
"""الگوهای یادگیری Brain (learning.py)."""

BRAIN_PLANS: Path = BRAIN_DIR / "plans.json"
"""پلن‌های چندمرحله‌ای Brain (learning.py)."""


# ══════════════════════════════════════════════════════════
# SafeBrain — حافظه و کش
# ══════════════════════════════════════════════════════════
SAFE_BRAIN_DIR: Path = STORAGE_DIR / "safe_brain"
SAFE_BRAIN_MEMORY_DIR: Path = SAFE_BRAIN_DIR / "memory"
SAFE_BRAIN_CACHE_DIR: Path = SAFE_BRAIN_DIR / "cache"


# ══════════════════════════════════════════════════════════
# pycache — برای sys.pycache_prefix
# ══════════════════════════════════════════════════════════
PYCACHE_DIR: Path = STORAGE_DIR / "pycache"


# ══════════════════════════════════════════════════════════
# Helper — ساخت پوشه‌های runtime
# ══════════════════════════════════════════════════════════
def ensure_runtime_dirs() -> None:
    """ساخت همهٔ پوشه‌های runtime (قابل صدا زدن از هر جا).

    این تابع idempotent است (اگر پوشه وجود داشته باشد، کاری نمی‌کند).
    """
    dirs = (
        # data
        CONFIGS_DIR, SCHEMAS_DIR, PROMPTS_DIR,
        SOURCES_DIR, RUNTIME_DIR,
        # storage
        LOGS_DIR, DB_DIR, SESSIONS_DIR,
        MEDIA_DIR, REPORTS_DIR, BACKUPS_DIR,
        OFFSET_DIR,
        # brain
        BRAIN_DIR, BRAIN_MEMORY_DIR, BRAIN_CACHE_DIR,
        BRAIN_BACKUP_DIR, BRAIN_HOOKS_DIR,
        # safe_brain
        SAFE_BRAIN_DIR, SAFE_BRAIN_MEMORY_DIR, SAFE_BRAIN_CACHE_DIR,
        # pycache
        PYCACHE_DIR,
    )
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            # بی‌صدا — config.py خودش مدیریت می‌کند
            pass


__all__ = [
    # پایه
    "DATA_DIR", "PROJECT_ROOT", "STORAGE_DIR",
    # محصولات
    "PRODUCTS_PY", "PRODUCTS_EN_PY",
    # configs
    "CONFIGS_DIR", "ACCESS_CONTROL", "RULES", "QUERIES",
    "SETTINGS", "SYNONYMS", "TEMPLATES",
    # schemas
    "SCHEMAS_DIR", "RULE_SCHEMA", "SETTINGS_SCHEMA",
    # prompts
    "PROMPTS_DIR", "PERSONA", "SYSTEM", "BOT_KNOWLEDGE",
    # sources
    "SOURCES_DIR", "GLOSSARY", "LINKEDIN_SEARCHES",
    "WEB_SOURCES", "TARGET_GROUPS",
    # runtime
    "RUNTIME_DIR", "KEYWORDS_CUSTOM", "CANDIDATE_RULES",
    "LEARNED_RULES", "RULE_STATS", "UNKNOWN_QUESTIONS", "MY_FORMULAS",
    # storage
    "LOGS_DIR", "LOG_FILE", "DB_DIR", "DB_FILE", "REPORT_CACHE",
    "SESSIONS_DIR", "MEDIA_DIR", "REPORTS_DIR", "BACKUPS_DIR",
    "BOT_PID", "REQUESTS_FILE", "AI_MEMORY", "OFFSET_DIR", "FORMULA_AUDIT",
    # brain
    "BRAIN_DIR", "BRAIN_MEMORY_DIR", "BRAIN_CACHE_DIR",
    "BRAIN_BACKUP_DIR", "BRAIN_HOOKS_DIR",
    "LEARNED_PATTERNS", "BRAIN_PLANS",
    # safe_brain
    "SAFE_BRAIN_DIR", "SAFE_BRAIN_MEMORY_DIR", "SAFE_BRAIN_CACHE_DIR",
    # pycache
    "PYCACHE_DIR",
    # helpers
    "ensure_runtime_dirs",
]