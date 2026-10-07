"""ثابت‌ها + کش کلیدواژه + مسیرها — برای SafeBrain.

نسخهٔ ۳ (این نسخه):
  - 🎯 `SAFE_ACTIONS` و `SAFE_ACTIONS_HANDLED_LOCALLY` حالا از
    `data/configs/actions.json` خوانده می‌شوند (تمرکز داده‌ها).
  - 🎯 `_load_actions_from_data()` — تابع جدید با fallback امن.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲ (سازگاری کامل عقب‌رو).

نسخهٔ ۲ (قبلی):
  - 🎯 مسیرهای SafeBrain حالا از `data.paths` می‌آیند (منبع یکتا).
  - 🐛 FIX: `MAX_API_CALLS_PER_HOUR` حالا با `_env_int` (sanitize).
  - 🐛 FIX: `_BRAIN_FAIL_RETRY_S` حالا env-based.
  - 🐛 FIX: `_CACHE_SAVE_INTERVAL_S`, `_MEM_SAVE_INTERVAL_S`,
    `_SLOW_ACTION_MS`, `_LOG_PREVIEW_LEN` حالا env-based.
  - 🐛 FIX: `_write_custom_kw_dict` حالا `os.fsync` + dir fsync
    + cleanup tmp در exception.
  - 🐛 FIX: `SAFE_ACTIONS` حالا شامل همهٔ اکشن‌های
    `ai_assistant._ALLOWED_ACTIONS` است (هماهنگی کامل).
  - 🐛 FIX: `mkdir` حالا با try/except (crash نمی‌کند).
  - 🎯 `is_safe_action()` + `is_locally_handled()` helperها.
  - 🎯 `reset_kw_cache()` alias برای `_invalidate_kw_cache()`.
  - 🎯 `get_stats()` برای debug.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kernel.logger import log_error, log_warning


# ════════════════════════════════════════════
# helpers
# ════════════════════════════════════════════
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


# ════════════════════════════════════════════
# مسیرها (از data.paths — منبع یکتا)
# ════════════════════════════════════════════
try:
    from data import paths as _data_paths
    SAFE_BRAIN_DIR: Path = _data_paths.SAFE_BRAIN_DIR
    SAFE_MEMORY_DIR: Path = _data_paths.SAFE_BRAIN_MEMORY_DIR
    SAFE_CACHE_DIR: Path = _data_paths.SAFE_BRAIN_CACHE_DIR
except Exception:
    # fallback
    try:
        from config import STORAGE_DIR
    except Exception:
        STORAGE_DIR = "storage"  # type: ignore
    SAFE_BRAIN_DIR = Path(str(STORAGE_DIR)) / "safe_brain"
    SAFE_MEMORY_DIR = SAFE_BRAIN_DIR / "memory"
    SAFE_CACHE_DIR = SAFE_BRAIN_DIR / "cache"


# ─── ساخت پوشه‌ها (با try/except) ───
def _ensure_safe_brain_dirs() -> None:
    """🎯 FIX: ساخت پوشه‌ها بدون crash."""
    for d in (
        SAFE_BRAIN_DIR,
        SAFE_MEMORY_DIR,
        SAFE_CACHE_DIR,
    ):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


_ensure_safe_brain_dirs()


# ════════════════════════════════════════════
# اکشن‌ها (از data/configs/actions.json — متمرکز)
# ════════════════════════════════════════════

# 🎯 Fallback: اگه فایل actions.json نبود یا خراب بود
_FALLBACK_SAFE_ACTIONS: frozenset[str] = frozenset({
    "status", "report", "crm", "logs", "keywords",
    "add_keyword", "remove_keyword", "has_keyword",
    "platform_status", "health", "lead_details",
    "get_param", "set_param", "restart", "explain_logs",
    "feature_request", "ask", "help",
})

_FALLBACK_HANDLED_LOCALLY: frozenset[str] = frozenset({
    "help", "add_keyword", "remove_keyword", "feature_request",
})


def _load_actions_from_data() -> tuple[frozenset[str], frozenset[str]]:
    """🎯 خواندن اکشن‌ها از data/configs/actions.json — با fallback امن.

    Returns:
        (safe_actions, handled_locally)
        اگه فایل نبود یا خراب بود → مقادیر fallback.
    """
    try:
        from data.loader import load_json
        data = load_json("actions", default={})

        if not isinstance(data, dict):
            return _FALLBACK_SAFE_ACTIONS, _FALLBACK_HANDLED_LOCALLY

        safe = data.get("safe_actions")
        local = data.get("handled_locally")

        # validate: باید list و غیرخالی باشد
        safe_set: frozenset[str] = (
            frozenset(str(x) for x in safe)
            if isinstance(safe, list) and safe
            else _FALLBACK_SAFE_ACTIONS
        )
        local_set: frozenset[str] = (
            frozenset(str(x) for x in local)
            if isinstance(local, list) and local
            else _FALLBACK_HANDLED_LOCALLY
        )
        return safe_set, local_set
    except Exception:
        # هر خطایی → fallback
        return _FALLBACK_SAFE_ACTIONS, _FALLBACK_HANDLED_LOCALLY


SAFE_ACTIONS, SAFE_ACTIONS_HANDLED_LOCALLY = _load_actions_from_data()


def is_safe_action(action: str) -> bool:
    """🎯 آیا این اکشن مجاز است؟"""
    return bool(action) and action in SAFE_ACTIONS


def is_locally_handled(action: str) -> bool:
    """🎯 آیا این اکشن توسط SafeBrain خودش انجام می‌شود؟"""
    return (
        bool(action)
        and action in SAFE_ACTIONS_HANDLED_LOCALLY
    )


# ════════════════════════════════════════════
# الگوهای ممنوعه
# ════════════════════════════════════════════
BLOCKED_PATTERNS_REGEX: tuple[str, ...] = (
    # ─── سیستم ───
    r"\brm\s+-rf\b",
    r"\brmdir\s+/s\b",
    r"\bdel\s+/f\b",
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\binit\s+[06]\b",
    r">\s*/dev/sd",
    r"\bchmod\s+777\b",
    # ─── fork bomb ───
    r":\(\)\s*\{.*?\};:",
    # ─── دیتابیس ───
    r"\bdrop\s+table\b",
    r"\bdrop\s+database\b",
    r"\bdelete\s+from\b",
    r"\btruncate\s+table\b",
    # ─── کد خطرناک ───
    r"\bos\.system\b",
    r"\bsubprocess\.(call|popen|run)\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\b__import__\b",
    r"\bcompile\s*\(",
)

BLOCKED_SUBSTRINGS: tuple[str, ...] = (
    ":(){ :|:& };:",  # fork bomb
)


# ════════════════════════════════════════════
# تنظیمات (env-based)
# ════════════════════════════════════════════
MAX_API_CALLS_PER_HOUR: int = _env_int(
    "SAFE_BRAIN_MAX_API_HOUR", 20,
    min_val=0, max_val=10_000,
)

# cache منفی برین
_BRAIN_FAIL_RETRY_S: float = _env_float(
    "SAFE_BRAIN_BRAIN_RETRY_S", 300.0,
    min_val=10.0, max_val=3600.0,
)

# throttle برای save
_CACHE_SAVE_INTERVAL_S: float = _env_float(
    "SAFE_BRAIN_CACHE_SAVE_S", 3.0,
    min_val=0.1, max_val=60.0,
)
_MEM_SAVE_INTERVAL_S: float = _env_float(
    "SAFE_BRAIN_MEM_SAVE_S", 2.0,
    min_val=0.1, max_val=60.0,
)

# آستانه‌های هشدار
_SLOW_ACTION_MS: int = _env_int(
    "SAFE_BRAIN_SLOW_ACTION_MS", 2000,
    min_val=100, max_val=60_000,
)
_LOG_PREVIEW_LEN: int = _env_int(
    "SAFE_BRAIN_LOG_PREVIEW", 80,
    min_val=20, max_val=500,
)


# ════════════════════════════════════════════
# cache برای keywords_custom.json
# ════════════════════════════════════════════
_KW_FILE_VERSION = "2.0.0"
_VALID_TIERS: tuple[str, ...] = ("T1", "T2", "T3")

_KW_CACHE: dict[str, str] | None = None
_KW_CACHE_MTIME: float = 0.0
_KW_LOCK = threading.Lock()


def _kw_file_path() -> Path:
    """مسیر فایل keywords_custom.json."""
    try:
        from data import paths as data_paths
        return data_paths.KEYWORDS_CUSTOM
    except Exception:
        return SAFE_BRAIN_DIR / ".." / "runtime" / "keywords_custom.json"


def _read_custom_kw_dict(
    force_reload: bool = False,
) -> dict[str, str]:
    """خواندن {name: tier} با cache mtime-aware.

    Args:
        force_reload: اگر True، cache نادیده گرفته می‌شود.

    Returns:
        dict {نام: tier}
    """
    global _KW_CACHE, _KW_CACHE_MTIME

    path = _kw_file_path()
    try:
        mtime = (
            path.stat().st_mtime if path.exists() else 0.0
        )
    except OSError:
        mtime = 0.0

    # ─── چک cache ───
    with _KW_LOCK:
        if (
            not force_reload
            and _KW_CACHE is not None
            and mtime == _KW_CACHE_MTIME
        ):
            return dict(_KW_CACHE)

    # ─── فایل نیست ───
    if not path.exists():
        with _KW_LOCK:
            _KW_CACHE = {}
            _KW_CACHE_MTIME = mtime
        return {}

    # ─── خواندن JSON ───
    try:
        with open(str(path), encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        log_warning(
            f"⚠️ [SAFEBRAIN:kw] JSON نامعتبر | "
            f"line={e.lineno} col={e.colno} | {e.msg}"
        )
        return {}
    except Exception as e:
        log_warning(
            f"⚠️ [SAFEBRAIN:kw] خواندن | "
            f"{type(e).__name__}: {e}"
        )
        return {}

    # ─── parse ───
    out: dict[str, str] = {}

    # ─── فرمت قدیمی: list ───
    if isinstance(data, list):
        out = {
            str(x).strip(): "T1"
            for x in data
            if isinstance(x, str) and x.strip()
        }

    # ─── فرمت جدید: dict ───
    elif isinstance(data, dict):
        kws = data.get("keywords")
        if kws is None:
            log_warning(
                f"⚠️ [SAFEBRAIN:kw] dict بدون کلید "
                f"«keywords» — کلیدهای موجود: "
                f"{list(data.keys())[:5]}"
            )
            return {}

        if isinstance(kws, dict):
            for k, meta in kws.items():
                name = str(k).strip()
                if not name:
                    continue
                if isinstance(meta, dict):
                    tier = str(
                        meta.get("tier", "T1")
                    ).upper()
                elif isinstance(meta, str):
                    tier = meta.upper()
                else:
                    tier = "T1"
                if tier not in _VALID_TIERS:
                    tier = "T1"
                out[name] = tier
        elif isinstance(kws, list):
            out = {
                str(x).strip(): "T1"
                for x in kws
                if isinstance(x, str) and x.strip()
            }

    # ─── ذخیره در cache ───
    with _KW_LOCK:
        _KW_CACHE = dict(out)
        _KW_CACHE_MTIME = mtime
    return out


def _invalidate_kw_cache() -> None:
    """پاک‌کردن cache کلمات."""
    global _KW_CACHE, _KW_CACHE_MTIME
    with _KW_LOCK:
        _KW_CACHE = None
        _KW_CACHE_MTIME = 0.0


# ─── alias ───
def reset_kw_cache() -> None:
    """🎯 alias عمومی برای `_invalidate_kw_cache`."""
    _invalidate_kw_cache()


def _write_custom_kw_dict(
    kw_tier: dict[str, str],
) -> bool:
    """نوشتن {name: tier} — atomic + fsync.

    Args:
        kw_tier: dict {نام: tier}.

    Returns:
        True اگر موفق.
    """
    path = _kw_file_path()
    tmp = path.with_suffix(".tmp")

    try:
        data = {
            "version": _KW_FILE_VERSION,
            "updated_at": datetime.now().isoformat(),
            "keywords": {
                k.strip(): {
                    "tier": (
                        t.upper()
                        if t and t.upper() in _VALID_TIERS
                        else "T1"
                    )
                }
                for k, t in kw_tier.items()
                if k and k.strip()
            },
        }

        # 🎯 امن: mkdir
        try:
            path.parent.mkdir(
                parents=True, exist_ok=True
            )
        except Exception:
            pass

        # 🎯 write + fsync
        with open(str(tmp), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                f.flush()
                os.fsync(f.fileno())
            except (AttributeError, OSError):
                pass

        tmp.replace(path)

        # 🎯 fsync دایرکتوری (POSIX)
        try:
            dir_fd = os.open(
                str(path.parent), os.O_DIRECTORY
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (AttributeError, OSError):
            pass

        _invalidate_kw_cache()
        return True
    except Exception as e:
        log_error(
            f"❌ [SAFEBRAIN:kw] نوشتن | "
            f"{type(e).__name__}: {e}"
        )
        # 🎯 cleanup tmp
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


# ════════════════════════════════════════════
# debug
# ════════════════════════════════════════════
def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    with _KW_LOCK:
        cache_size = (
            len(_KW_CACHE)
            if _KW_CACHE is not None else 0
        )
        cache_mtime = _KW_CACHE_MTIME

    return {
        "safe_brain_dir": str(SAFE_BRAIN_DIR),
        "memory_dir": str(SAFE_MEMORY_DIR),
        "cache_dir": str(SAFE_CACHE_DIR),
        "safe_actions": len(SAFE_ACTIONS),
        "locally_handled": len(
            SAFE_ACTIONS_HANDLED_LOCALLY
        ),
        "blocked_patterns": len(BLOCKED_PATTERNS_REGEX),
        "blocked_substrings": len(BLOCKED_SUBSTRINGS),
        "max_api_per_hour": MAX_API_CALLS_PER_HOUR,
        "brain_retry_s": _BRAIN_FAIL_RETRY_S,
        "kw_cache_size": cache_size,
        "kw_cache_mtime": cache_mtime,
        "valid_tiers": list(_VALID_TIERS),
        "kw_file_version": _KW_FILE_VERSION,
    }


# ════════════════════════════════════════════
# __all__
# ════════════════════════════════════════════
__all__ = [
    # مسیرها
    "SAFE_BRAIN_DIR",
    "SAFE_MEMORY_DIR",
    "SAFE_CACHE_DIR",
    # اکشن‌ها
    "SAFE_ACTIONS",
    "SAFE_ACTIONS_HANDLED_LOCALLY",
    "is_safe_action",
    "is_locally_handled",
    # امنیت
    "BLOCKED_PATTERNS_REGEX",
    "BLOCKED_SUBSTRINGS",
    # تنظیمات
    "MAX_API_CALLS_PER_HOUR",
    # kw cache
    "_read_custom_kw_dict",
    "_write_custom_kw_dict",
    "_invalidate_kw_cache",
    "reset_kw_cache",
    # debug
    "get_stats",
]