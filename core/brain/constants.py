"""ثابت‌های Brain — هیچ منطقی این‌جا نیست.

نسخهٔ ۴ (این نسخه):
  - 🎯 NEW: Chunk Reading — `CHUNK_SIZE`, `CHUNK_AUTO`, `CHUNK_MAX_PER_FILE`,
    `CHUNK_AUTO_THRESHOLD`.
  - 🎯 NEW: Persistent Memory — `PERSISTENT_MEMORY_ENABLED`, `PERSISTENT_DB_PATH`,
    `PERSISTENT_MAX_ROWS`, `PERSISTENT_REMEMBER_CONVERSATIONS`.
  - 🎯 NEW: Semantic Search — `DISABLE_SEMANTIC`, `SEMANTIC_MAX_RESULTS`.
  - 🎯 NEW: Think config — `THINK_TIMEOUT_S`, `THINK_MAX_TOKENS`,
    `THINK_TEMPERATURE`.
  - 🎯 NEW: Agent config — `AGENT_API_TIMEOUT_S`, `AGENT_MAX_TOKENS`,
    `AGENT_TEMPERATURE`.
  - 🎯 NEW: Context — `CONTEXT_RECENT`, `CONTEXT_API_N`, `CONTEXT_CHROMA_N`.
  - 🎯 NEW: Pending tasks — `MAX_PENDING_TASKS`, `COMPRESS_THRESHOLD`.
  - 🎯 NEW: `MAX_SHORT_TERM` env-based.
  - 🎯 همهٔ مقادیر پیش‌فرض افزایش یافتند (متناسب با `.env` نسخهٔ ۱۴):
      * `BRAIN_MAX_FILE_MB`           (پیش‌فرض 5 → 50)
      * `BRAIN_MAX_CONTENT_LEN`       (پیش‌فرض 100_000 → 2_000_000)
      * `BRAIN_MAX_AGENT_STEPS`       (پیش‌فرض 30 → 100)
      * `BRAIN_MAX_CONTEXT_TOKENS`    (پیش‌فرض 16384 → 128_000)
      * `BRAIN_CACHE_TTL_DAYS`        (پیش‌فرض 14 → 365)
      * `BRAIN_LOG_TAIL_BYTES`        (پیش‌فرض 256KB → 5MB)
      * `BRAIN_PY_EXEC_TIMEOUT_S`     (پیش‌فرض 30 → 60)
      * `BRAIN_TESTS_TIMEOUT_S`       (پیش‌فرض 120 → 180)
      * `BRAIN_AGENT_MAX_TOKENS`      (پیش‌فرض 4096 → 8192)
      * `BRAIN_CONTEXT_RECENT`        (پیش‌فرض 5 → 10)
      * `BRAIN_CONTEXT_API_N`         (پیش‌فرض 3 → 5)
      * `BRAIN_CONTEXT_CHROMA_N`      (پیش‌فرض 3 → 5)
  - 🎯 `get_stats()` کامل‌تر — همهٔ مقادیر جدید را نشان می‌دهد.
  - 🎯 `__all__` صریح برای همهٔ ثابت‌ها.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════
# helpers (env-based، بدون وابستگی به logger)
# ═══════════════════════════════════════════════════════════
def _env_int(
    name: str, default: int,
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
    name: str, default: float,
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


def _env_str(name: str, default: str = "") -> str:
    """خواندن str از env — با strip."""
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    """خواندن bool از env — با پشتیبانی از yes/no/on/off."""
    raw = os.getenv(name)
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in ("true", "1", "yes", "on", "y", "t"):
        return True
    if v in ("false", "0", "no", "off", "n", "f"):
        return False
    return default


# ═══════════════════════════════════════════════════════════
# config (با fallback امن)
# ═══════════════════════════════════════════════════════════
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
    _CONFIG_AVAILABLE = True
except ImportError as _cfg_err:
    # ⚠️ نمی‌توانیم logger import کنیم (circular) — به stderr بنویسیم
    try:
        import sys
        print(
            f"⚠️ [BRAIN:constants] import config ناموفق | "
            f"{_cfg_err}",
            file=sys.stderr,
        )
    except Exception:
        pass

    PROJECT_BASE = Path(__file__).parent.parent.parent.resolve()
    STORAGE_DIR = PROJECT_BASE / "storage"
    DB_DIR = STORAGE_DIR / "database"
    BACKUPS_DIR = STORAGE_DIR / "backups"
    LOGS_DIR = STORAGE_DIR / "logs"
    AI = {
        "enabled": True,
        "model": "qwen2.5:3b",
        "url": "http://127.0.0.1:11434",
        "timeout": 120,
        "priority": "auto",
    }
    AI_ENABLED = True
    PLUGINS_CONFIG = {}
    _CONFIG_AVAILABLE = False


# ═══════════════════════════════════════════════════════════
# پوشه‌های Brain — از data.paths (منبع یکتا)
# ═══════════════════════════════════════════════════════════
try:
    from data import paths as _data_paths
    BRAIN_DIR: Path = _data_paths.BRAIN_DIR
    MEMORY_DIR: Path = _data_paths.BRAIN_MEMORY_DIR
    CACHE_DIR: Path = _data_paths.BRAIN_CACHE_DIR
    BACKUP_DIR: Path = _data_paths.BRAIN_BACKUP_DIR
    HOOKS_DIR: Path = _data_paths.BRAIN_HOOKS_DIR
    _PATHS_AVAILABLE = True
except ImportError as _paths_err:
    try:
        import sys
        print(
            f"⚠️ [BRAIN:constants] import data.paths ناموفق | "
            f"{_paths_err}",
            file=sys.stderr,
        )
    except Exception:
        pass
    BRAIN_DIR = Path(STORAGE_DIR) / "brain"
    MEMORY_DIR = BRAIN_DIR / "memory"
    CACHE_DIR = BRAIN_DIR / "cache"
    BACKUP_DIR = BRAIN_DIR / "backups"
    HOOKS_DIR = BRAIN_DIR / "hooks"
    _PATHS_AVAILABLE = False


# ═══════════════════════════════════════════════════════════
# ساخت پوشه‌ها (با try/except)
# ═══════════════════════════════════════════════════════════
def _ensure_dirs() -> None:
    """ساخت پوشه‌ها با try/except (نباید crash کند)."""
    for d in (
        BRAIN_DIR, MEMORY_DIR, CACHE_DIR, BACKUP_DIR, HOOKS_DIR
    ):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            try:
                import sys
                print(
                    f"⚠️ [BRAIN:constants] mkdir {d} | "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
            except Exception:
                pass


_ensure_dirs()


# ═══════════════════════════════════════════════════════════
# سقف‌ها (env-based)
# ═══════════════════════════════════════════════════════════

# 🎯 حجم فایل (به مگابایت → بایت)
_BRAIN_MAX_FILE_MB = _env_int(
    "BRAIN_MAX_FILE_MB", 50, min_val=1, max_val=500
)
MAX_FILE_SIZE_BYTES: int = _BRAIN_MAX_FILE_MB * 1024 * 1024

# 🎯 محتوا (کاراکتر)
MAX_CONTENT_LENGTH: int = _env_int(
    "BRAIN_MAX_CONTENT_LEN", 2_000_000,
    min_val=1000, max_val=50_000_000,
)

# 🎯 گام‌های agent
MAX_AGENT_STEPS: int = _env_int(
    "BRAIN_MAX_AGENT_STEPS", 100, min_val=1, max_val=1000
)

# 🎯 context tokens
MAX_CONTEXT_TOKENS: int = _env_int(
    "BRAIN_MAX_CONTEXT_TOKENS", 128_000,
    min_val=1000, max_val=1_000_000,
)

# 🎯 TTL کش (روز)
CACHE_TTL_DAYS: int = _env_int(
    "BRAIN_CACHE_TTL_DAYS", 365, min_val=1, max_val=3650
)

# 🎯 لاگ tail (بایت)
LOG_TAIL_BYTES: int = _env_int(
    "BRAIN_LOG_TAIL_BYTES", 5_000_000,
    min_val=10_000, max_val=1_000_000_000,
)

# 🎯 فاصلهٔ save کش (ثانیه)
CACHE_SAVE_INTERVAL_S: float = _env_float(
    "BRAIN_CACHE_SAVE_INTERVAL_S", 2.0,
    min_val=0.1, max_val=300.0,
)

# 🎯 حافظهٔ کوتاه‌مدت (پیام)
MAX_SHORT_TERM: int = _env_int(
    "BRAIN_MAX_SHORT_TERM", 100, min_val=5, max_val=1000
)


# ═══════════════════════════════════════════════════════════
# 🆕 Chunk Reading (خواندن تکه‌تکه)
# ═══════════════════════════════════════════════════════════

# اندازهٔ هر تکه (کاراکتر)
CHUNK_SIZE: int = _env_int(
    "BRAIN_CHUNK_SIZE", 100_000,
    min_val=10_000, max_val=5_000_000,
)

# فعال بودن خودکار
CHUNK_AUTO: bool = _env_bool(
    "BRAIN_CHUNK_AUTO", True
)

# سقف تعداد تکه‌ها در یک فایل
CHUNK_MAX_PER_FILE: int = _env_int(
    "BRAIN_MAX_CHUNKS_PER_FILE", 100,
    min_val=1, max_val=10_000,
)

# آستانه‌ای که بالاتر از آن chunk_auto فعال می‌شود
CHUNK_AUTO_THRESHOLD: int = _env_int(
    "BRAIN_CHUNK_AUTO_THRESHOLD", 20_000,
    min_val=5_000, max_val=5_000_000,
)

# ═══════════════════════════════════════════════════════════
# 🆕 Persistent Memory (حافظهٔ SQLite)
# ═══════════════════════════════════════════════════════════

# فعال بودن حافظهٔ پایدار
PERSISTENT_MEMORY_ENABLED: bool = _env_bool(
    "BRAIN_PERSISTENT_MEMORY", True
)

# مسیر فایل DB (absolute)
_default_pdb = str(BRAIN_DIR / "persistent.db")
PERSISTENT_DB_PATH: str = (
    _env_str("BRAIN_PERSISTENT_DB", _default_pdb)
    or _default_pdb
)

# سقف تعداد رکورد
PERSISTENT_MAX_ROWS: int = _env_int(
    "BRAIN_PERSISTENT_MAX_ROWS", 100_000,
    min_val=100, max_val=10_000_000,
)

# یادآوری خودکار گفتگوها
PERSISTENT_REMEMBER_CONVERSATIONS: bool = _env_bool(
    "SAFE_BRAIN_REMEMBER_CONVERSATIONS", True
)


# ═══════════════════════════════════════════════════════════
# 🆕 Semantic Search
# ═══════════════════════════════════════════════════════════

# غیرفعال بودن Semantic (پیش‌فرض: false = فعال)
DISABLE_SEMANTIC: bool = _env_bool(
    "BRAIN_DISABLE_SEMANTIC", False
)

# سقف نتایج جستجو
SEMANTIC_MAX_RESULTS: int = _env_int(
    "BRAIN_SEMANTIC_MAX_RESULTS", 10,
    min_val=1, max_val=100,
)


# ═══════════════════════════════════════════════════════════
# Timeoutها (env-based)
# ═══════════════════════════════════════════════════════════
DEFAULT_TIMEOUT_S: int = _env_int(
    "BRAIN_DEFAULT_TIMEOUT_S", 300,
    min_val=10, max_val=3600,
)
PY_EXEC_TIMEOUT_S: int = _env_int(
    "BRAIN_PY_EXEC_TIMEOUT_S", 60,
    min_val=5, max_val=600,
)
TESTS_TIMEOUT_S: int = _env_int(
    "BRAIN_TESTS_TIMEOUT_S", 180,
    min_val=10, max_val=1800,
)
PYTEST_COVERAGE_TIMEOUT_S: int = _env_int(
    "BRAIN_PYTEST_COVERAGE_TIMEOUT_S", 120,
    min_val=10, max_val=1800,
)
BANDIT_TIMEOUT_S: int = _env_int(
    "BRAIN_BANDIT_TIMEOUT_S", 60,
    min_val=10, max_val=1800,
)


# ═══════════════════════════════════════════════════════════
# 🆕 Agent config
# ═══════════════════════════════════════════════════════════
AGENT_API_TIMEOUT_S: float = _env_float(
    "BRAIN_AGENT_API_TIMEOUT_S", 240.0,
    min_val=10.0, max_val=1800.0,
)
AGENT_MAX_TOKENS: int = _env_int(
    "BRAIN_AGENT_MAX_TOKENS", 8192,
    min_val=256, max_val=32_000,
)
AGENT_TEMPERATURE: float = _env_float(
    "BRAIN_AGENT_TEMPERATURE", 0.1,
    min_val=0.0, max_val=2.0,
)


# ═══════════════════════════════════════════════════════════
# 🆕 Think config
# ═══════════════════════════════════════════════════════════
THINK_TIMEOUT_S: float = _env_float(
    "BRAIN_THINK_TIMEOUT_S", 60.0,
    min_val=5.0, max_val=300.0,
)
THINK_MAX_TOKENS: int = _env_int(
    "BRAIN_THINK_MAX_TOKENS", 4096,
    min_val=256, max_val=16_000,
)
THINK_TEMPERATURE: float = _env_float(
    "BRAIN_THINK_TEMPERATURE", 0.1,
    min_val=0.0, max_val=2.0,
)


# ═══════════════════════════════════════════════════════════
# 🆕 Context
# ═══════════════════════════════════════════════════════════
CONTEXT_RECENT: int = _env_int(
    "BRAIN_CONTEXT_RECENT", 10,
    min_val=1, max_val=50,
)
CONTEXT_API_N: int = _env_int(
    "BRAIN_CONTEXT_API_N", 5,
    min_val=1, max_val=20,
)
CONTEXT_CHROMA_N: int = _env_int(
    "BRAIN_CONTEXT_CHROMA_N", 5,
    min_val=1, max_val=20,
)


# ═══════════════════════════════════════════════════════════
# 🆕 Pending tasks + Compression
# ═══════════════════════════════════════════════════════════
MAX_PENDING_TASKS: int = _env_int(
    "BRAIN_MAX_PENDING_TASKS", 100,
    min_val=10, max_val=10_000,
)
COMPRESS_THRESHOLD: int = _env_int(
    "BRAIN_COMPRESS_THRESHOLD", 15,
    min_val=3, max_val=200,
)


# ═══════════════════════════════════════════════════════════
# مدل و provider (از config.AI)
# ═══════════════════════════════════════════════════════════
_ai_model = (AI or {}).get("model") or ""
_ai_priority = ((AI or {}).get("priority") or "auto").lower()

if _ai_model and not _ai_model.startswith("ollama"):
    DEFAULT_MODEL: str = _ai_model
elif _ai_priority == "deepseek":
    DEFAULT_MODEL: str = _env_str(
        "DEEPSEEK_MODEL", "deepseek-chat"
    )
else:
    DEFAULT_MODEL: str = (
        _ai_model or _env_str("OLLAMA_MODEL", "qwen2.5:3b")
    )

DEFAULT_PROVIDER: str = (
    (AI or {}).get("priority")
    or "auto"
)

DEFAULT_BASE_URL: str = (
    (AI or {}).get("url")
    or _env_str("OLLAMA_URL", "http://127.0.0.1:11434")
    or "http://127.0.0.1:11434"
)


# ═══════════════════════════════════════════════════════════
# آستانه‌های هشدار (میلی‌ثانیه، env-based)
# ═══════════════════════════════════════════════════════════
SLOW_STEP_MS: int = _env_int(
    "BRAIN_SLOW_STEP_MS", 5000, min_val=100, max_val=60_000
)
SLOW_TOOL_MS: int = _env_int(
    "BRAIN_SLOW_TOOL_MS", 3000, min_val=100, max_val=60_000
)
SLOW_THINK_MS: int = _env_int(
    "BRAIN_SLOW_THINK_MS", 8000, min_val=100, max_val=60_000
)
SLOW_ACTION_MS: int = _env_int(
    "BRAIN_SLOW_ACTION_MS", 3000, min_val=100, max_val=60_000
)
LOG_PREVIEW_LEN: int = _env_int(
    "BRAIN_LOG_PREVIEW_LEN", 100, min_val=20, max_val=1000
)


# ═══════════════════════════════════════════════════════════
# Debug
# ═══════════════════════════════════════════════════════════
def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    return {
        "config_available": _CONFIG_AVAILABLE,
        "paths_available": _PATHS_AVAILABLE,
        "brain_dir": str(BRAIN_DIR),
        "memory_dir": str(MEMORY_DIR),
        "cache_dir": str(CACHE_DIR),
        "limits": {
            "max_file_size_mb": _BRAIN_MAX_FILE_MB,
            "max_content_length": MAX_CONTENT_LENGTH,
            "max_agent_steps": MAX_AGENT_STEPS,
            "max_context_tokens": MAX_CONTEXT_TOKENS,
            "cache_ttl_days": CACHE_TTL_DAYS,
            "max_short_term": MAX_SHORT_TERM,
        },
        "chunk_reading": {
            "enabled": CHUNK_AUTO,
            "chunk_size": CHUNK_SIZE,
            "auto_threshold": CHUNK_AUTO_THRESHOLD,
            "max_chunks": CHUNK_MAX_PER_FILE,
        },
        "persistent_memory": {
            "enabled": PERSISTENT_MEMORY_ENABLED,
            "db_path": PERSISTENT_DB_PATH,
            "max_rows": PERSISTENT_MAX_ROWS,
            "remember_conversations": (
                PERSISTENT_REMEMBER_CONVERSATIONS
            ),
        },
        "semantic": {
            "disabled": DISABLE_SEMANTIC,
            "max_results": SEMANTIC_MAX_RESULTS,
        },
        "timeouts": {
            "default": DEFAULT_TIMEOUT_S,
            "py_exec": PY_EXEC_TIMEOUT_S,
            "tests": TESTS_TIMEOUT_S,
            "coverage": PYTEST_COVERAGE_TIMEOUT_S,
            "bandit": BANDIT_TIMEOUT_S,
            "agent_api": AGENT_API_TIMEOUT_S,
            "think": THINK_TIMEOUT_S,
        },
        "agent": {
            "max_tokens": AGENT_MAX_TOKENS,
            "temperature": AGENT_TEMPERATURE,
        },
        "think": {
            "max_tokens": THINK_MAX_TOKENS,
            "temperature": THINK_TEMPERATURE,
        },
        "context": {
            "recent": CONTEXT_RECENT,
            "api_n": CONTEXT_API_N,
            "chroma_n": CONTEXT_CHROMA_N,
        },
        "pending": {
            "max_tasks": MAX_PENDING_TASKS,
            "compress_threshold": COMPRESS_THRESHOLD,
        },
        "model": {
            "default": DEFAULT_MODEL,
            "provider": DEFAULT_PROVIDER,
            "base_url": DEFAULT_BASE_URL,
        },
        "slow_thresholds_ms": {
            "step": SLOW_STEP_MS,
            "tool": SLOW_TOOL_MS,
            "think": SLOW_THINK_MS,
            "action": SLOW_ACTION_MS,
        },
    }


# ═══════════════════════════════════════════════════════════
# __all__ — همهٔ ثابت‌های public
# ═══════════════════════════════════════════════════════════
__all__ = [
    # ─── paths ───
    "BRAIN_DIR",
    "MEMORY_DIR",
    "CACHE_DIR",
    "BACKUP_DIR",
    "HOOKS_DIR",
    "PROJECT_BASE",
    "STORAGE_DIR",
    "DB_DIR",
    "BACKUPS_DIR",
    "LOGS_DIR",
    # ─── limits ───
    "MAX_FILE_SIZE_BYTES",
    "MAX_CONTENT_LENGTH",
    "MAX_AGENT_STEPS",
    "MAX_CONTEXT_TOKENS",
    "CACHE_TTL_DAYS",
    "LOG_TAIL_BYTES",
    "CACHE_SAVE_INTERVAL_S",
    "MAX_SHORT_TERM",
    # ─── chunk reading ───
    "CHUNK_SIZE",
    "CHUNK_AUTO",
    "CHUNK_MAX_PER_FILE",
    "CHUNK_AUTO_THRESHOLD",
    # ─── persistent memory ───
    "PERSISTENT_MEMORY_ENABLED",
    "PERSISTENT_DB_PATH",
    "PERSISTENT_MAX_ROWS",
    "PERSISTENT_REMEMBER_CONVERSATIONS",
    # ─── semantic ───
    "DISABLE_SEMANTIC",
    "SEMANTIC_MAX_RESULTS",
    # ─── timeouts ───
    "DEFAULT_TIMEOUT_S",
    "PY_EXEC_TIMEOUT_S",
    "TESTS_TIMEOUT_S",
    "PYTEST_COVERAGE_TIMEOUT_S",
    "BANDIT_TIMEOUT_S",
    # ─── agent ───
    "AGENT_API_TIMEOUT_S",
    "AGENT_MAX_TOKENS",
    "AGENT_TEMPERATURE",
    # ─── think ───
    "THINK_TIMEOUT_S",
    "THINK_MAX_TOKENS",
    "THINK_TEMPERATURE",
    # ─── context ───
    "CONTEXT_RECENT",
    "CONTEXT_API_N",
    "CONTEXT_CHROMA_N",
    # ─── pending ───
    "MAX_PENDING_TASKS",
    "COMPRESS_THRESHOLD",
    # ─── model ───
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "DEFAULT_BASE_URL",
    # ─── slow thresholds ───
    "SLOW_STEP_MS",
    "SLOW_TOOL_MS",
    "SLOW_THINK_MS",
    "SLOW_ACTION_MS",
    "LOG_PREVIEW_LEN",
    # ─── config exports ───
    "AI",
    "AI_ENABLED",
    "PLUGINS_CONFIG",
    # ─── debug ───
    "get_stats",
]
