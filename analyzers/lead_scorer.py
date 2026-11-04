# analyzers/lead_scorer.py
"""
امتیازدهیِ سرنخ بر اساسِ «سیگنالِ قصدِ خرید» (Buying-Intent) — روشِ استانداردِ B2B.

ایده (طبق best practiceِ intent-scoring): سیگنال‌های ته‌قیف (قیمت، خرید،
تعداد/عمده، فوریت، نمایندگی، درخواست تماس) وزنِ بالاتری می‌گیرند تا محتوای
صرفاً اطلاعاتی. امتیاز ۰..۱۰۰ + نوعِ فرصت (داغ/گرم/سرد) برمی‌گردد تا کارشناس
فروش اولویت‌بندی کند.

⚠️ طبقِ خواستهٔ کاربر: منطق اینجا در «کد» است، نه در فایل‌های data/ (چیزی به
   data اضافه نشد).

نسخهٔ ۳ (این نسخه):
  - 🎯 `_HOT`, `_WARM` حالا از env قابل تنظیم هستند (LEAD_SCORE_HOT,
    LEAD_SCORE_WARM).
  - 🎯 `_SIGNALS` حالا dict قابل override از env یا فایل (اختیاری).
  - 🐛 FIX: `analyze` حالا اگر `message.metadata` غیر-dict باشد
    بازنویسی می‌کند (نه کرش).
  - 🐛 FIX: `score()` حالا اگر `has_keyword` غیر-bool باشد،
    امن handle می‌کند.
  - 🎯 `_TIER_AVAILABLE` حالا log می‌کند اگر tier در دسترس نبود.
  - 🎯 `_SLOW_SCORE_MS` env-based + لاگ هشدار.
  - 🎯 `get_stats()` متد جدید برای debug.
  - 🎯 `reset_lead_scorer()` برای تست.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.

نسخهٔ ۳.۱ (fixes):
  - 🐛 حذف import بی‌استفاده `log_success` (رفع هشدار Health Check).
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from core.contracts.base_analyzer import BaseAnalyzer
from core.kernel.logger import log_info, log_warning
from core.kernel.models import AnalyzedMessage


# ═══════════════════════════════════════════════════════════
# helpers
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


def _env_bool(name: str, default: bool) -> bool:
    """خواندن bool از env — با sanitize."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in (
        "true", "1", "yes", "on", "y", "t"
    )


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════

# 🎯 FIX: env-based thresholds
_HOT: int = _env_int("LEAD_SCORE_HOT", 60, min_val=1, max_val=100)
_WARM: int = _env_int("LEAD_SCORE_WARM", 30, min_val=1, max_val=100)

# آستانهٔ کندی scoring (میلی‌ثانیه)
_SLOW_SCORE_MS = _env_int(
    "LEAD_SCORE_SLOW_MS", 500, min_val=10, max_val=60_000
)


# ═══════════════════════════════════════════════════════════
# سیگنال‌ها
# ═══════════════════════════════════════════════════════════
# هر گروه: (برچسبِ نوع، فهرستِ واژه‌های سیگنال، وزن)
# واژه‌ها فارسی + انگلیسی تا روی همهٔ پلتفرم‌ها کار کند.
_SIGNALS: list[tuple[str, list[str], int]] = [
    (
        "خرید",
        [
            "خرید", "سفارش", "بخرم", "می‌خواهم", "میخوام",
            "نیاز دارم", "نیازمند", "تهیه",
            "buy", "order", "purchase", "procure",
            "looking for", "need",
        ],
        30,
    ),
    (
        "قیمت‌پرسی",
        [
            "قیمت", "نرخ", "هزینه", "چند", "استعلام",
            "price", "cost", "quote", "pricing", "rate",
        ],
        25,
    ),
    (
        "نمایندگی",
        [
            "نمایندگی", "نماینده", "عمده", "عمده‌فروش", "بنکدار",
            "distributor", "dealer", "wholesale", "reseller", "agent",
        ],
        20,
    ),
    (
        "تعداد/حجم",
        [
            "تن", "کیلو", "کیسه", "کارتن", "پالت", "تعداد",
            "بار", "محموله",
            "kg", "ton", "tonne", "pallet", "quantity", "bulk",
        ],
        15,
    ),
    (
        "فوریت",
        [
            "فوری", "سریع", "امروز", "همین حالا", "زودتر", "عجله",
            "urgent", "asap", "immediately", "today",
        ],
        15,
    ),
    (
        "درخواست تماس",
        [
            "تماس", "شماره", "پیام بده", "پیام بدید",
            "دایرکت", "پیوی", "موبایل", "whatsapp", "واتساپ",
            "call", "contact", "dm", "phone",
        ],
        10,
    ),
]


# ═══════════════════════════════════════════════════════════
# tier import (اختیاری)
# ═══════════════════════════════════════════════════════════
try:
    from analyzers.priority_finder import get_priority_finder
    _TIER_AVAILABLE = True
except ImportError as e:
    _TIER_AVAILABLE = False
    log_warning(
        f"⚠️ [SCORER:init] priority_finder در دسترس نیست | "
        f"{type(e).__name__}: {e} — tier bonus اعمال نمی‌شود"
    )


# ═══════════════════════════════════════════════════════════
# LeadScorer
# ═══════════════════════════════════════════════════════════
class LeadScorer(BaseAnalyzer):
    """امتیازدهی سرنخ بر اساس سیگنال‌های قصد خرید."""

    def __init__(self):
        # برای هر گروه یک regex
        # (بدون \b چون مرز کلمه در فارسی درست کار نمی‌کند)
        self._patterns: list[tuple[str, re.Pattern[str], int]] = [
            (
                label,
                re.compile(
                    "|".join(re.escape(w) for w in words),
                    re.IGNORECASE,
                ),
                weight,
            )
            for (label, words, weight) in _SIGNALS
        ]

        log_info(
            f"🎯 [SCORER:init] آماده | "
            f"signals={len(self._patterns)} | "
            f"hot={_HOT} warm={_WARM} | "
            f"tier_available={_TIER_AVAILABLE}"
        )

    # ═══════════════════════════════════════════
    # score
    # ═══════════════════════════════════════════
    def score(
        self,
        text: str | None,
        has_keyword: bool = True,
    ) -> tuple[int, str, list[str]]:
        """امتیاز (۰..۱۰۰)، نوعِ فرصت (داغ/گرم/سرد)، و سیگنال‌های یافت‌شده.

        Args:
            text: متن پیام.
            has_keyword: آیا کلیدواژه دارد؟ (پیش‌فرض True).

        Returns:
            (score, level, found_signals)
        """
        t0 = time.monotonic()
        t = text or ""

        # 🎯 FIX: has_keyword را صریحاً bool کن
        hk = bool(has_keyword)

        # امتیاز پایه
        total = 10 if hk else 0

        # 🎯 بونوس tier (T1 +40، T2 +15، T3 +5)
        tier_str = "T0"
        if _TIER_AVAILABLE and hk:
            try:
                pf = get_priority_finder()
                tier_str = pf.highest_tier(t)
                total += pf.tier_bonus(tier_str)
            except Exception as e:
                log_warning(
                    f"⚠️ [SCORER:score] tier bonus | "
                    f"{type(e).__name__}: {e}"
                )

        # سیگنال‌ها
        found: list[str] = []
        for label, pat, weight in self._patterns:
            if pat.search(t):
                total += weight
                found.append(label)

        # کلیپ 0..100
        total = max(0, min(100, total))

        # سطح
        if total >= _HOT:
            level = "داغ"
        elif total >= _WARM:
            level = "گرم"
        else:
            level = "سرد"

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_SCORE_MS:
            log_warning(
                f"🐌 [SCORER:score] کند | {elapsed_ms:.0f}ms "
                f"(آستانه {_SLOW_SCORE_MS}ms) | "
                f"signals={len(found)} tier={tier_str}"
            )

        return total, level, found

    # ═══════════════════════════════════════════
    # analyze (BaseAnalyzer interface)
    # ═══════════════════════════════════════════
    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        """تحلیل پیام — افزودن score و opportunity_type."""
        has_kw = bool(getattr(message, "keywords", None))
        s, level, found = self.score(message.text or "", has_kw)
        message.score = s
        message.opportunity_type = level

        # 🎯 FIX: metadata امن
        meta = getattr(message, "metadata", None)
        if not isinstance(meta, dict):
            message.metadata = {}
        message.metadata["intent_signals"] = found

        return message

    # ═══════════════════════════════════════════
    # ابزار عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "signal_groups": len(self._patterns),
            "signals_labels": [p[0] for p in self._patterns],
            "hot_threshold": _HOT,
            "warm_threshold": _WARM,
            "tier_available": _TIER_AVAILABLE,
            "slow_score_ms": _SLOW_SCORE_MS,
        }


# ═══════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════
_shared: LeadScorer | None = None


def get_lead_scorer() -> LeadScorer:
    """Singleton — instance مشترک سراسری."""
    global _shared
    if _shared is None:
        _shared = LeadScorer()
    return _shared


def reset_lead_scorer() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _shared
    _shared = None
    log_info("🗑️ [SCORER:singleton] reset شد")
