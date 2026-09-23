"""ثابت‌ها + کش کلیدواژه + مسیرها — برای SafeBrain."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from config import STORAGE_DIR
from core.kernel.logger import log_error, log_warning

# ════════════════════════════════════════════
# مسیرها
# ════════════════════════════════════════════
SAFE_BRAIN_DIR = Path(STORAGE_DIR) / "safe_brain"
SAFE_MEMORY_DIR = SAFE_BRAIN_DIR / "memory"
SAFE_CACHE_DIR = SAFE_BRAIN_DIR / "cache"
for _d in (SAFE_BRAIN_DIR, SAFE_MEMORY_DIR, SAFE_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════
# اکشن‌ها
# ════════════════════════════════════════════

# اکشن‌هایی که SafeBrain مجاز است در نظر بگیرد
SAFE_ACTIONS = frozenset({
    "status", "report", "crm", "logs", "keywords",
    "add_keyword", "remove_keyword", "has_keyword",
    "platform_status", "health", "lead_details",
    "get_param", "set_param", "help", "restart",
    "explain_logs", "feature_request", "ask",
})

# اکشن‌هایی که SafeBrain خودش انجام می‌دهد
SAFE_ACTIONS_HANDLED_LOCALLY = frozenset({
    "help", "add_keyword", "remove_keyword", "feature_request",
})


# ════════════════════════════════════════════
# الگوهای ممنوعه (🟢 FIX: tuple به‌جای list)
# ════════════════════════════════════════════
BLOCKED_PATTERNS_REGEX: tuple = (
    # سیستم
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
    # fork bomb
    r":\(\)\s*\{.*?\};:",
    # دیتابیس
    r"\bdrop\s+table\b",
    r"\bdrop\s+database\b",
    r"\bdelete\s+from\b",
    r"\btruncate\s+table\b",
    # کد خطرناک
    r"\bos\.system\b",
    r"\bsubprocess\.(call|popen|run)\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\b__import__\b",
    r"\bcompile\s*\(",
)

BLOCKED_SUBSTRINGS: tuple = (
    ":(){ :|:& };:",  # fork bomb
)


# ════════════════════════════════════════════
# تنظیمات
# ════════════════════════════════════════════
# 🔴 FIX: کلیپ منفی
MAX_API_CALLS_PER_HOUR = max(
    0, int(os.getenv("SAFE_BRAIN_MAX_API_HOUR", "20"))
)

# cache منفی برین (۵ دقیقه)
_BRAIN_FAIL_RETRY_S = 300

# throttle برای save
_CACHE_SAVE_INTERVAL_S = 3.0
_MEM_SAVE_INTERVAL_S = 2.0

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_ACTION_MS = 2000
_LOG_PREVIEW_LEN = 80


# ════════════════════════════════════════════
# cache برای keywords_custom.json
# ════════════════════════════════════════════
_KW_FILE_VERSION = "2.0.0"
_VALID_TIERS = ("T1", "T2", "T3")

_KW_CACHE: dict[str, str] | None = None
_KW_CACHE_MTIME: float = 0.0
_KW_LOCK = threading.Lock()


def _kw_file_path() -> Path:
    """مسیر فایل keywords_custom.json."""
    from data import paths as data_paths
    return data_paths.KEYWORDS_CUSTOM


def _read_custom_kw_dict(force_reload: bool = False) -> dict[str, str]:
    """
    خواندن {name: tier} با cache mtime-aware.

    Args:
        force_reload: اگر True، cache نادیده گرفته می‌شود.

    Returns:
        dict {نام: tier}
    """
    global _KW_CACHE, _KW_CACHE_MTIME

    path = _kw_file_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
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
            f"line={e.lineno} | {e.msg}"
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

    # فرمت قدیمی: list
    if isinstance(data, list):
        out = {
            str(x).strip(): "T1"
            for x in data
            if isinstance(x, str) and x.strip()
        }

    # فرمت جدید: dict
    elif isinstance(data, dict):
        kws = data.get("keywords")
        if kws is None:
            log_warning(
                f"⚠️ [SAFEBRAIN:kw] dict بدون کلید «keywords» — "
                f"کلیدهای موجود: {list(data.keys())[:5]}"
            )
            return {}

        if isinstance(kws, dict):
            for k, meta in kws.items():
                name = str(k).strip()
                if not name:
                    continue
                if isinstance(meta, dict):
                    tier = str(meta.get("tier", "T1")).upper()
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


def _write_custom_kw_dict(kw_tier: dict[str, str]) -> bool:
    """
    نوشتن {name: tier} — atomic.

    Args:
        kw_tier: dict {نام: tier}.

    Returns:
        True اگر موفق.
    """
    path = _kw_file_path()
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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(str(tmp), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        _invalidate_kw_cache()
        return True
    except Exception as e:
        log_error(
            f"❌ [SAFEBRAIN:kw] نوشتن | "
            f"{type(e).__name__}: {e}"
        )
        return False
