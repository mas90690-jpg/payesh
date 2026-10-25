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
import contextlib

from core.safe_brain.constants import (
    # امنیت
    BLOCKED_PATTERNS_REGEX,
    BLOCKED_SUBSTRINGS,
    # تنظیمات
    MAX_API_CALLS_PER_HOUR,
    # اکشن‌ها
    SAFE_ACTIONS,
    SAFE_ACTIONS_HANDLED_LOCALLY,
    # مسیرها
    SAFE_BRAIN_DIR,
    SAFE_CACHE_DIR,
    SAFE_MEMORY_DIR,
    _invalidate_kw_cache,
    # kw cache
    _read_custom_kw_dict,
    _write_custom_kw_dict,
    # debug
    get_stats,
    is_locally_handled,
    is_safe_action,
    reset_kw_cache,
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
    with contextlib.suppress(Exception):
        print(
            f"⚠️ [SAFEBRAIN:init] import main ناموفق | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )

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
    with contextlib.suppress(Exception):
        print(
            f"⚠️ [SAFEBRAIN:init] import storage ناموفق | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )

    SafeCache = None  # type: ignore
    SafeMemory = None  # type: ignore


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    # security
    "BLOCKED_PATTERNS_REGEX",
    "BLOCKED_SUBSTRINGS",
    # config
    "MAX_API_CALLS_PER_HOUR",
    # actions
    "SAFE_ACTIONS",
    "SAFE_ACTIONS_HANDLED_LOCALLY",
    # paths
    "SAFE_BRAIN_DIR",
    "SAFE_CACHE_DIR",
    "SAFE_MEMORY_DIR",
    # main
    "SafeBrain",
    "SafeCache",
    # storage
    "SafeMemory",
    # version
    "__version__",
    "_invalidate_kw_cache",
    # kw cache
    "_read_custom_kw_dict",
    "_write_custom_kw_dict",
    "get_safe_brain",
    # debug
    "get_stats",
    "is_locally_handled",
    "is_safe_action",
    "reset_kw_cache",
    "reset_safe_brain",
]