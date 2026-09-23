# data/paths.py
"""مسیرهای متمرکز داده — تنها منبع حقیقت."""
from pathlib import Path

DATA_DIR = Path(__file__).parent

# ─── محصولات (کد پایتون — سر جاشون) ───
PRODUCTS_PY = DATA_DIR / "products.py"
PRODUCTS_EN_PY = DATA_DIR / "products_en.py"

# ─── configs/ ───
CONFIGS_DIR = DATA_DIR / "configs"
ACCESS_CONTROL = CONFIGS_DIR / "access_control.json"
RULES = CONFIGS_DIR / "rules.json"
QUERIES = CONFIGS_DIR / "queries.json"
SETTINGS = CONFIGS_DIR / "settings.json"
SYNONYMS = CONFIGS_DIR / "synonyms.json"
TEMPLATES = CONFIGS_DIR / "templates.json"

# ─── schemas/ ───
SCHEMAS_DIR = DATA_DIR / "schemas"
RULE_SCHEMA = SCHEMAS_DIR / "rule.schema.json"
SETTINGS_SCHEMA = SCHEMAS_DIR / "settings.schema.json"

# ─── prompts/ ───
PROMPTS_DIR = DATA_DIR / "prompts"
PERSONA = PROMPTS_DIR / "persona.md"
SYSTEM = PROMPTS_DIR / "system.md"
BOT_KNOWLEDGE = PROMPTS_DIR / "bot_knowledge.md"

# ─── sources/ (کاربر ویرایش میکنه) ───
SOURCES_DIR = DATA_DIR / "sources"
GLOSSARY = SOURCES_DIR / "glossary.txt"
LINKEDIN_SEARCHES = SOURCES_DIR / "linkedin_searches.txt"
WEB_SOURCES = SOURCES_DIR / "web_sources.txt"
TARGET_GROUPS = SOURCES_DIR / "target_groups.txt"

# ─── runtime/ (ربات مینویسه) ───
RUNTIME_DIR = DATA_DIR / "runtime"
KEYWORDS_CUSTOM = RUNTIME_DIR / "keywords_custom.json"
CANDIDATE_RULES = RUNTIME_DIR / "candidate_rules.json"
LEARNED_RULES = RUNTIME_DIR / "learned_rules.json"
RULE_STATS = RUNTIME_DIR / "rule_stats.json"
UNKNOWN_QUESTIONS = RUNTIME_DIR / "unknown_questions.jsonl"
MY_FORMULAS = RUNTIME_DIR / "my_formulas.json"