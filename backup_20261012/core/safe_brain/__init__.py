"""پکیج SafeBrain — گارد امن + کش + حافظه.

این پکیج لایهٔ امنیت بین ChatOps (admin_handler) و AI/Brain است:
  - گارد امنیتی (blocked patterns)
  - کش پاسخ‌ها (SafeCache)
  - حافظهٔ گفتگو (SafeMemory)
  - rate limit API

نحوهٔ استفاده:
    from core.safe_brain import SafeBrain, get_safe_brain
    brain = get_safe_brain(api_key="...")
    answer = await brain.process("سلام")

نسخهٔ ۲ (این نسخه):
  - 🎯 `__all__` کامل‌تر (شامل `reset_safe_brain`, `get_stats`,
    `is_safe_action`, `is_locally_handled`, مسیرها).
  - 🐛 FIX: import با try/except تا یک خطا کل پکیج را نترکاند.
  - 🎯 `__version__` constant.
  - 🎯 docstring کامل‌تر.
  - ✅ حفظ همهٔ importهای نسخهٔ ۱.
"""
from __future__ import annotations


# ═══════════════════════════════════════════
# version
# ═══════════════════════════════════════════
__version__ = "2.0.0"


# ═══════════════════════════════════════════
# constants (مسیرها + امنیت)
# ═══════════════════════════════════════════
from core.safe_brain.constants import (
    # مسیرها
    SAFE_BRAIN_DIR,
    SAFE_MEMORY_DIR,
    SAFE_CACHE_DIR,
    # اکشن‌ها
    SAFE_ACTIONS,
    SAFE_ACTIONS_HANDLED_LOCALLY,
    is_safe_action,
    is_locally_handled,
    # امنیت
    BLOCKED_PATTERNS_REGEX,
    BLOCKED_SUBSTRINGS,
    # تنظیمات
    MAX_API_CALLS_PER_HOUR,
    # kw cache
    _read_custom_kw_dict,
    _write_custom_kw_dict,
    _invalidate_kw_cache,
    reset_kw_cache,
    # debug
    get_stats,
)


# ═══════════════════════════════════════════
# main (SafeBrain)
# ═══════════════════════════════════════════
try:
    from core.safe_brain.main import (
        SafeBrain,
        get_safe_brain,
        reset_safe_brain,
    )
except Exception as e:
    # ⚠️ نمی‌توانیم از log_error استفاده کنیم — circular
    import sys
    try:
        print(
            f"⚠️ [SAFEBRAIN:init] import main ناموفق | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
    except Exception:
        pass

    # ─── fallback placeholders ───
    SafeBrain = None  # type: ignore
    get_safe_brain = None  # type: ignore
    reset_safe_brain = None  # type: ignore


# ═══════════════════════════════════════════
# storage (SafeMemory + SafeCache)
# ═══════════════════════════════════════════
try:
    from core.safe_brain.storage import (
        SafeCache,
        SafeMemory,
    )
except Exception as e:
    import sys
    try:
        print(
            f"⚠️ [SAFEBRAIN:init] import storage ناموفق | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
    except Exception:
        pass

    SafeCache = None  # type: ignore
    SafeMemory = None  # type: ignore


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    # version
    "__version__",
    # main
    "SafeBrain",
    "get_safe_brain",
    "reset_safe_brain",
    # storage
    "SafeMemory",
    "SafeCache",
    # actions
    "SAFE_ACTIONS",
    "SAFE_ACTIONS_HANDLED_LOCALLY",
    "is_safe_action",
    "is_locally_handled",
    # security
    "BLOCKED_PATTERNS_REGEX",
    "BLOCKED_SUBSTRINGS",
    # config
    "MAX_API_CALLS_PER_HOUR",
    # paths
    "SAFE_BRAIN_DIR",
    "SAFE_MEMORY_DIR",
    "SAFE_CACHE_DIR",
    # kw cache
    "_read_custom_kw_dict",
    "_write_custom_kw_dict",
    "_invalidate_kw_cache",
    "reset_kw_cache",
    # debug
    "get_stats",
]