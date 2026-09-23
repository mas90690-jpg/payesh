"""ثابت‌های Brain — هیچ منطقی این‌جا نیست."""
from __future__ import annotations

from pathlib import Path

from core.kernel.logger import log_warning

# ─── config (با fallback) ───
try:
    from config import (
        AI,
        AI_ENABLED,
        BACKUPS_DIR,
        DB_DIR,
        LOGS_DIR,
        PLUGINS_CONFIG,
        STORAGE_DIR,
    )
    from config import (
        BASE_DIR as PROJECT_BASE,
    )
except ImportError as _cfg_err:
    log_warning(
        f"⚠️ [BRAIN:constants] import config ناموفق | {_cfg_err}"
    )
    PROJECT_BASE = Path(__file__).parent.parent.parent.resolve()
    STORAGE_DIR = PROJECT_BASE / "storage"
    DB_DIR = STORAGE_DIR / "database"
    BACKUPS_DIR = STORAGE_DIR / "backups"
    LOGS_DIR = STORAGE_DIR / "logs"
    AI = {
        "enabled": True,
        "model": "deepseek-chat",
        "url": "https://api.deepseek.com/v1",
        "timeout": 120,
        "priority": "auto",
    }
    AI_ENABLED = True
    PLUGINS_CONFIG = {}


# ─── پوشه‌های Brain ───
BRAIN_DIR = Path(STORAGE_DIR) / "brain"
MEMORY_DIR = BRAIN_DIR / "memory"
CACHE_DIR = BRAIN_DIR / "cache"
BACKUP_DIR = BRAIN_DIR / "backups"
HOOKS_DIR = BRAIN_DIR / "hooks"

for _d in [BRAIN_DIR, MEMORY_DIR, CACHE_DIR, BACKUP_DIR, HOOKS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ─── سقف‌ها ───
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_CONTENT_LENGTH = 100_000
MAX_AGENT_STEPS = 30
MAX_CONTEXT_TOKENS = 16384
CACHE_TTL_DAYS = 14
DEFAULT_MODEL = "deepseek-chat"

# ─── سقف‌های داخلی ───
LOG_TAIL_BYTES = 256 * 1024
CACHE_SAVE_INTERVAL_S = 2.0
DEFAULT_TIMEOUT_S = 300
PY_EXEC_TIMEOUT_S = 30
TESTS_TIMEOUT_S = 120
PYTEST_COVERAGE_TIMEOUT_S = 120
BANDIT_TIMEOUT_S = 60

# ─── آستانه‌های هشدار (میلی‌ثانیه) ───
SLOW_STEP_MS = 5000
SLOW_TOOL_MS = 3000
SLOW_THINK_MS = 8000
SLOW_ACTION_MS = 3000
LOG_PREVIEW_LEN = 100
