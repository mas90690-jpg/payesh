# data/paths.py
"""مسیرهای متمرکز داده — تنها منبع حقیقت.

نسخهٔ ۵ (این نسخه):
  - 🐛 FIX: حذف `PLATFORMS`, `PARAMS`, `REPORT_TEMPLATES` از `__all__`
    (این‌ها دیگر استفاده نمی‌شوند — تکراری با فایل‌های دیگر).
  - 🐛 FIX: `COMMANDS` که در `__all__` بود ولی تعریف نشده بود،
    حالا تعریف شده (commands.json برای command_parser).
  - ✅ حفظ همهٔ مسیرهای نسخهٔ ۴.

نسخهٔ ۴ (قبلی):
  - 🐛 FIX بحرانی: `REPORT_HEADERS` که قبل از `CONFIGS_DIR` تعریف شده بود، حذف شد.
  - 🐛 FIX: `CRM_HEADERS` و `REPORT_HEADERS` دوبار تعریف شده بودند، یکی حذف شد.
  - 🎯 `DOMAIN` و `EVENTS` و `MARKERS` و `MESSAGES` و `TIERS` اضافه شدند.
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
# configs/ — تنظیمات ساختاری (JSON)
# ══════════════════════════════════════════════════════════
# ⚠️ این باید قبل از هر مسیر configs بیاد
CONFIGS_DIR: Path = DATA_DIR / "configs"

# ─── محصولات (کد پایتون) ───
PRODUCTS_PY: Path = DATA_DIR / "products.py"
PRODUCTS_EN_PY: Path = DATA_DIR / "products_en.py"

# ─── configs موجود ───
ACCESS_CONTROL: Path = CONFIGS_DIR / "access_control.json"
RULES: Path = CONFIGS_DIR / "rules.json"
QUERIES: Path = CONFIGS_DIR / "queries.json"
SETTINGS: Path = CONFIGS_DIR / "settings.json"
SYNONYMS: Path = CONFIGS_DIR / "synonyms.json"
TEMPLATES: Path = CONFIGS_DIR / "templates.json"

# ─── configs جدید (متمرکزسازی داده) ───
ERRORS: Path = CONFIGS_DIR / "errors.json"
REPORT_HEADERS: Path = CONFIGS_DIR / "report_headers.json"
CRM_HEADERS: Path = CONFIGS_DIR / "crm_headers.json"
ACTIONS: Path = CONFIGS_DIR / "actions.json"
DOMAIN: Path = CONFIGS_DIR / "domain.json"
EVENTS: Path = CONFIGS_DIR / "events.json"
MARKERS: Path = CONFIGS_DIR / "markers.json"
MESSAGES: Path = CONFIGS_DIR / "messages.json"
TIERS: Path = CONFIGS_DIR / "tiers.json"
COMMANDS: Path = CONFIGS_DIR / "commands.json"


# ══════════════════════════════════════════════════════════
# schemas/
# ══════════════════════════════════════════════════════════
SCHEMAS_DIR: Path = DATA_DIR / "schemas"
RULE_SCHEMA: Path = SCHEMAS_DIR / "rule.schema.json"
SETTINGS_SCHEMA: Path = SCHEMAS_DIR / "settings.schema.json"


# ══════════════════════════════════════════════════════════
# prompts/
# ══════════════════════════════════════════════════════════
PROMPTS_DIR: Path = DATA_DIR / "prompts"
PERSONA: Path = PROMPTS_DIR / "persona.md"
SYSTEM: Path = PROMPTS_DIR / "system.md"
BOT_KNOWLEDGE: Path = PROMPTS_DIR / "bot_knowledge.md"


# ══════════════════════════════════════════════════════════
# sources/
# ══════════════════════════════════════════════════════════
SOURCES_DIR: Path = DATA_DIR / "sources"
GLOSSARY: Path = SOURCES_DIR / "glossary.txt"
LINKEDIN_SEARCHES: Path = SOURCES_DIR / "linkedin_searches.txt"
WEB_SOURCES: Path = SOURCES_DIR / "web_sources.txt"
TARGET_GROUPS: Path = SOURCES_DIR / "target_groups.txt"


# ══════════════════════════════════════════════════════════
# runtime/
# ══════════════════════════════════════════════════════════
RUNTIME_DIR: Path = DATA_DIR / "runtime"
KEYWORDS_CUSTOM: Path = RUNTIME_DIR / "keywords_custom.json"
CANDIDATE_RULES: Path = RUNTIME_DIR / "candidate_rules.json"
LEARNED_RULES: Path = RUNTIME_DIR / "learned_rules.json"
RULE_STATS: Path = RUNTIME_DIR / "rule_stats.json"
UNKNOWN_QUESTIONS: Path = RUNTIME_DIR / "unknown_questions.jsonl"
MY_FORMULAS: Path = RUNTIME_DIR / "my_formulas.json"


# ══════════════════════════════════════════════════════════
# storage/
# ══════════════════════════════════════════════════════════
LOGS_DIR: Path = STORAGE_DIR / "logs"
LOG_FILE: Path = LOGS_DIR / "app.jsonl"

DB_DIR: Path = STORAGE_DIR / "database"
DB_FILE: Path = DB_DIR / "messages.db"
REPORT_CACHE: Path = DB_DIR / "report_cache.json"

SESSIONS_DIR: Path = STORAGE_DIR / "sessions"
MEDIA_DIR: Path = STORAGE_DIR / "media"
REPORTS_DIR: Path = STORAGE_DIR / "reports"
BACKUPS_DIR: Path = STORAGE_DIR / "backups"

BOT_PID: Path = STORAGE_DIR / "bot.pid"
REQUESTS_FILE: Path = STORAGE_DIR / "requests.jsonl"
AI_MEMORY: Path = STORAGE_DIR / "ai_memory.jsonl"

OFFSET_DIR: Path = STORAGE_DIR / "offset"
FORMULA_AUDIT: Path = OFFSET_DIR / "formula_audit.jsonl"


# ══════════════════════════════════════════════════════════
# Brain
# ══════════════════════════════════════════════════════════
BRAIN_DIR: Path = STORAGE_DIR / "brain"
BRAIN_MEMORY_DIR: Path = BRAIN_DIR / "memory"
BRAIN_CACHE_DIR: Path = BRAIN_DIR / "cache"
BRAIN_BACKUP_DIR: Path = BRAIN_DIR / "backups"
BRAIN_HOOKS_DIR: Path = BRAIN_DIR / "hooks"

LEARNED_PATTERNS: Path = BRAIN_DIR / "learned_patterns.json"
BRAIN_PLANS: Path = BRAIN_DIR / "plans.json"


# ══════════════════════════════════════════════════════════
# SafeBrain
# ══════════════════════════════════════════════════════════
SAFE_BRAIN_DIR: Path = STORAGE_DIR / "safe_brain"
SAFE_BRAIN_MEMORY_DIR: Path = SAFE_BRAIN_DIR / "memory"
SAFE_BRAIN_CACHE_DIR: Path = SAFE_BRAIN_DIR / "cache"


# ══════════════════════════════════════════════════════════
# pycache
# ══════════════════════════════════════════════════════════
PYCACHE_DIR: Path = STORAGE_DIR / "pycache"


# ══════════════════════════════════════════════════════════
# Helper
# ══════════════════════════════════════════════════════════
def ensure_runtime_dirs() -> None:
    """ساخت همهٔ پوشه‌های runtime (idempotent)."""
    dirs = (
        CONFIGS_DIR, SCHEMAS_DIR, PROMPTS_DIR,
        SOURCES_DIR, RUNTIME_DIR,
        LOGS_DIR, DB_DIR, SESSIONS_DIR,
        MEDIA_DIR, REPORTS_DIR, BACKUPS_DIR,
        OFFSET_DIR,
        BRAIN_DIR, BRAIN_MEMORY_DIR, BRAIN_CACHE_DIR,
        BRAIN_BACKUP_DIR, BRAIN_HOOKS_DIR,
        SAFE_BRAIN_DIR, SAFE_BRAIN_MEMORY_DIR, SAFE_BRAIN_CACHE_DIR,
        PYCACHE_DIR,
    )
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


__all__ = [
    # پایه
    "DATA_DIR", "PROJECT_ROOT", "STORAGE_DIR",
    # محصولات
    "PRODUCTS_PY", "PRODUCTS_EN_PY",
    # configs — موجود
    "CONFIGS_DIR",
    "ACCESS_CONTROL", "RULES", "QUERIES", "SETTINGS",
    "SYNONYMS", "TEMPLATES",
    # configs — جدید
    "ERRORS", "REPORT_HEADERS", "CRM_HEADERS",
    "ACTIONS", "DOMAIN", "EVENTS", "MARKERS",
    "MESSAGES", "TIERS", "COMMANDS",
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