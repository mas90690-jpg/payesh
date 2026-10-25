# core/manual/brain.py
"""
هوش دستی پیشرفته — نسخهٔ ۸ (صفر تا صد) — Facade.

این فایل حالا فقط coordinator است:
  - ترکیب همهٔ mixinها
  - __init__ اصلی
  - singleton

منطق در ماژول‌های کنار منتقل شد:
  - _brain_core.py      → helpers + init state + load/save + stats + templates
  - _brain_rules.py     → تطبیق قوانین + اجرای action + callables
  - _brain_keywords.py  → CRUD کلیدواژه + تحلیل خطاها
  - _brain_learning.py  → یادگیری + python + formulas + process
  - _brain_settings.py  → تنظیمات + پلتفرم‌ها

⚠️ API بیرونی تغییر نکرده:
  - `from core.manual.brain import ManualBrain, get_manual_brain`
  - `mbrain.process(text, sender, ai_fallback)`

نسخهٔ ۸ (این نسخه):
  - 🎯 Refactor به mixinها (بدون شکستن API).
  - 🎯 استفاده از config برای top-level params.
  - 🎯 استفاده از data.paths برای همهٔ مسیرها.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۷.
"""
from __future__ import annotations

import time

from core.adapters.formula_manager import get_formula_manager
from core.kernel.logger import log_info, log_success, log_warning
from core.manual.data_api import DataAPI

# fallback برای DEFAULT_RULES
try:
    from core.manual.rules import RULES as DEFAULT_RULES
except ImportError:
    DEFAULT_RULES = []

try:
    from core.adapters.access_manager import get_access_manager
    _ACCESS_AVAILABLE = True
except ImportError:
    _ACCESS_AVAILABLE = False

# mixinها
from core.manual._brain_core import BrainCoreMixin
from core.manual._brain_keywords import BrainKeywordsMixin
from core.manual._brain_learning import BrainLearningMixin
from core.manual._brain_rules import BrainRulesMixin
from core.manual._brain_settings import BrainSettingsMixin


# ═══════════════════════════════════════════════════════════
# ManualBrain — Facade
# ═══════════════════════════════════════════════════════════
class ManualBrain(
    BrainCoreMixin,
    BrainRulesMixin,
    BrainKeywordsMixin,
    BrainLearningMixin,
    BrainSettingsMixin,
):
    """هوش دستی: لایهٔ اول و مرجع اصلی ربات — صفر تا صد."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.api = DataAPI(db_path)
        self._fm = get_formula_manager(db_path)

        # ─── state اولیه (از BrainCoreMixin) ───
        self._init_core_state()

        # ─── core rules از rules.json یا fallback ───
        self.core_rules = list(DEFAULT_RULES)

        # ─── access manager (اختیاری) ───
        self._access_available = _ACCESS_AVAILABLE
        if _ACCESS_AVAILABLE:
            try:
                self._access_mgr = get_access_manager()
            except Exception as e:
                log_warning(
                    f"⚠️ [MBRAIN:init] access_mgr | "
                    f"{type(e).__name__}: {e}"
                )
                self._access_mgr = None
                self._access_available = False
        else:
            self._access_mgr = None

        # ─── بارگذاری ───
        t0 = time.monotonic()
        self._load_learned()
        self._load_candidates()
        self._load_stats()
        self._cleanup_polluted()
        elapsed_ms = (time.monotonic() - t0) * 1000

        log_success(
            f"✅ [MBRAIN:init] آماده | "
            f"core={len(self.core_rules)} "
            f"learned={len(self.learned_rules)} "
            f"candidates={len(self._candidates)} "
            f"stats={len(self.stats)} | "
            f"{elapsed_ms:.0f}ms"
        )


# ═══════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════
_shared: ManualBrain | None = None


def get_manual_brain(db_path: str) -> ManualBrain:
    """Singleton — instance مشترک سراسری."""
    global _shared
    if _shared is None:
        log_info(
            f"🏗️ [MBRAIN:singleton] ساخت نمونهٔ اول | db={db_path}"
        )
        _shared = ManualBrain(db_path)
    return _shared


def reset_manual_brain() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _shared
    _shared = None
    log_info("🗑️ [MBRAIN:singleton] reset شد")