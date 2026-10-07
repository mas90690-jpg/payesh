"""ثابت‌های Brain — هیچ منطقی این‌جا نیست.

نسخهٔ ۳ (این نسخه):
  - 🎯 مسیرهای Brain حالا از `data.paths` می‌آیند (منبع یکتا).
  - 🎯 همهٔ سقف‌ها (`MAX_*`) حالا env-based هستند:
      * `BRAIN_MAX_FILE_MB`           (پیش‌فرض 5)
      * `BRAIN_MAX_CONTENT_LEN`       (پیش‌فرض 100_000)
      * `BRAIN_MAX_AGENT_STEPS`       (پیش‌فرض 30)
      * `BRAIN_MAX_CONTEXT_TOKENS`    (پیش‌فرض 16384)
      * `BRAIN_CACHE_TTL_DAYS`        (پیش‌فرض 14)
      * `BRAIN_LOG_TAIL_BYTES`        (پیش‌فرض 256KB)
      * `BRAIN_CACHE_SAVE_INTERVAL_S` (پیش‌فرض 2.0)
  - 🎯 همهٔ timeoutها env-based:
      * `BRAIN_DEFAULT_TIMEOUT_S`          (پیش‌فرض 300)
      * `BRAIN_PY_EXEC_TIMEOUT_S`          (پیش‌فرض 30)
      * `BRAIN_TESTS_TIMEOUT_S`            (پیش‌فرض 120)
      * `BRAIN_PYTEST_COVERAGE_TIMEOUT_S`  (پیش‌فرض 120)
      * `BRAIN_BANDIT_TIMEOUT_S`           (پیش‌فرض 60)
  - 🎯 همهٔ آستانه‌های کندی env-based:
      * `BRAIN_SLOW_STEP_MS`
      * `BRAIN_SLOW_TOOL_MS`
      * `BRAIN_SLOW_THINK_MS`
      * `BRAIN_SLOW_ACTION_MS`
      * `BRAIN_LOG_PREVIEW_LEN`
  - 🐛 FIX: `mkdir` حالا با try/except (پوشه‌های غیرقابل‌دسترس crash نمی‌کنند).
  - 🐛 FIX: `DEFAULT_PROVIDER` fallback از `"ollama"` به `"auto"` تغییر
    (سازگار با `config.AI_PRIORITY`).
  - 🎯 `_env_int` / `_env_float` / `_env_str` helperها.
  - 🎯 `get_stats()` متد جدید برای debug.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
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
    """🎯 FIX: ساخت پوشه‌ها با try/except (نباید crash کند)."""
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

# 🎯 حجم فایل (به بایت)
_BRAIN_MAX_FILE_MB = _env_int(
    "BRAIN_MAX_FILE_MB", 5, min_val=1, max_val=500
)
MAX_FILE_SIZE_BYTES: int = _BRAIN_MAX_FILE_MB * 1024 * 1024

# 🎯 محتوا
MAX_CONTENT_LENGTH: int = _env_int(
    "BRAIN_MAX_CONTENT_LEN", 100_000,
    min_val=1000, max_val=10_000_000,
)

# 🎯 گام‌های agent
MAX_AGENT_STEPS: int = _env_int(
    "BRAIN_MAX_AGENT_STEPS", 30, min_val=1, max_val=1000
)

# 🎯 context tokens
MAX_CONTEXT_TOKENS: int = _env_int(
    "BRAIN_MAX_CONTEXT_TOKENS", 16384,
    min_val=1000, max_val=1_000_000,
)

# 🎯 TTL کش
CACHE_TTL_DAYS: int = _env_int(
    "BRAIN_CACHE_TTL_DAYS", 14, min_val=1, max_val=365
)

# 🎯 لاگ tail
LOG_TAIL_BYTES: int = _env_int(
    "BRAIN_LOG_TAIL_BYTES", 256 * 1024,
    min_val=10_000, max_val=100_000_000,
)

# 🎯 فاصلهٔ save کش (ثانیه)
CACHE_SAVE_INTERVAL_S: float = _env_float(
    "BRAIN_CACHE_SAVE_INTERVAL_S", 2.0,
    min_val=0.1, max_val=300.0,
)


# ═══════════════════════════════════════════════════════════
# Timeoutها (env-based)
# ═══════════════════════════════════════════════════════════
DEFAULT_TIMEOUT_S: int = _env_int(
    "BRAIN_DEFAULT_TIMEOUT_S", 300,
    min_val=10, max_val=3600,
)
PY_EXEC_TIMEOUT_S: int = _env_int(
    "BRAIN_PY_EXEC_TIMEOUT_S", 30,
    min_val=5, max_val=600,
)
TESTS_TIMEOUT_S: int = _env_int(
    "BRAIN_TESTS_TIMEOUT_S", 120,
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
# مدل و provider (از config.AI)
# ═══════════════════════════════════════════════════════════
DEFAULT_MODEL: str = (
    (AI or {}).get("model")
    or _env_str("OLLAMA_MODEL", "qwen2.5:3b")
    or "qwen2.5:3b"
)

# 🎯 FIX: fallback از «ollama» به «auto» (سازگار با config.AI_PRIORITY)
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
        },
        "timeouts": {
            "default": DEFAULT_TIMEOUT_S,
            "py_exec": PY_EXEC_TIMEOUT_S,
            "tests": TESTS_TIMEOUT_S,
            "coverage": PYTEST_COVERAGE_TIMEOUT_S,
            "bandit": BANDIT_TIMEOUT_S,
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