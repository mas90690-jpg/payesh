# analyzers/lead_scorer.py
"""
امتیازدهیِ سرنخ بر اساسِ «سیگنالِ قصدِ خرید» (Buying-Intent) — روشِ استانداردِ B2B.

ایده (طبق best practiceِ intent-scoring): سیگنال‌های ته‌قیف (قیمت، خرید، تعداد/عمده،
فوریت، نمایندگی، درخواست تماس) وزنِ بالاتری می‌گیرند تا محتوای صرفاً اطلاعاتی.
امتیاز ۰..۱۰۰ + نوعِ فرصت (داغ/گرم/سرد) برمی‌گردد تا کارشناس فروش اولویت‌بندی کند.

⚠️ طبقِ خواستهٔ کاربر: منطق اینجا در «کد» است، نه در فایل‌های data/ (چیزی به data اضافه نشد).
"""
import re

from core.contracts.base_analyzer import BaseAnalyzer
from core.kernel.models import AnalyzedMessage

# 🎯 tier از priority_finder: T1 +40، T2 +15
try:
    from analyzers.priority_finder import get_priority_finder
    _TIER_AVAILABLE = True
except ImportError:
    _TIER_AVAILABLE = False

# هر گروه: (برچسبِ نوع، فهرستِ واژه‌های سیگنال، وزن)
# واژه‌ها فارسی + انگلیسی تا روی همهٔ پلتفرم‌ها (از جمله لینکدین/وب) کار کند.
_SIGNALS = [
    (
        "خرید",
        [
            "خرید",
            "سفارش",
            "بخرم",
            "می‌خواهم",
            "میخوام",
            "نیاز دارم",
            "نیازمند",
            "تهیه",
            "buy",
            "order",
            "purchase",
            "procure",
            "looking for",
            "need",
        ],
        30,
    ),
    (
        "قیمت‌پرسی",
        [
            "قیمت",
            "نرخ",
            "هزینه",
            "چند",
            "استعلام",
            "price",
            "cost",
            "quote",
            "pricing",
            "rate",
        ],
        25,
    ),
    (
        "نمایندگی",
        [
            "نمایندگی",
            "نماینده",
            "عمده",
            "عمده‌فروش",
            "بنکدار",
            "distributor",
            "dealer",
            "wholesale",
            "reseller",
            "agent",
        ],
        20,
    ),
    (
        "تعداد/حجم",
        [
            "تن",
            "کیلو",
            "کیسه",
            "کارتن",
            "پالت",
            "تعداد",
            "بار",
            "محموله",
            "kg",
            "ton",
            "tonne",
            "pallet",
            "quantity",
            "bulk",
        ],
        15,
    ),
    (
        "فوریت",
        [
            "فوری",
            "سریع",
            "امروز",
            "همین حالا",
            "زودتر",
            "عجله",
            "urgent",
            "asap",
            "immediately",
            "today",
        ],
        15,
    ),
    (
        "درخواست تماس",
        [
            "تماس",
            "شماره",
            "پیام بده",
            "پیام بدید",
            "دایرکت",
            "پیوی",
            "موبایل",
            "whatsapp",
            "واتساپ",
            "call",
            "contact",
            "dm",
            "phone",
        ],
        10,
    ),
]

_HOT, _WARM = 60, 30  # آستانه‌های داغ/گرم


class LeadScorer(BaseAnalyzer):
    def __init__(self):
        # برای هر گروه یک regex (بدون \b چون مرز کلمه در فارسی درست کار نمی‌کند)
        self._patterns = [
            (
                label,
                re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE),
                weight,
            )
            for (label, words, weight) in _SIGNALS
        ]

    def score(
        self, text: str | None, has_keyword: bool = True
    ) -> tuple[int, str, list[str]]:
        """امتیاز (۰..۱۰۰)، نوعِ فرصت (داغ/گرم/سرد)، و سیگنال‌های یافت‌شده."""
        t = text or ""
        total = 10 if has_keyword else 0

        # 🎯 بونوس tier (T1 +40، T2 +15)
        if _TIER_AVAILABLE and has_keyword:
            try:
                pf = get_priority_finder()
                tier = pf.highest_tier(t)
                total += pf.tier_bonus(tier)
            except Exception:
                pass

        found: list[str] = []
        for label, pat, weight in self._patterns:
            if pat.search(t):
                total += weight
                found.append(label)
        total = max(0, min(100, total))
        level = "داغ" if total >= _HOT else ("گرم" if total >= _WARM else "سرد")
        return total, level, found

    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        has_kw = bool(getattr(message, "keywords", None))
        s, level, found = self.score(message.text or "", has_kw)
        message.score = s
        message.opportunity_type = level
        message.metadata = getattr(message, "metadata", {}) or {}
        message.metadata["intent_signals"] = found
        return message


_shared: LeadScorer | None = None


def get_lead_scorer() -> LeadScorer:
    global _shared
    if _shared is None:
        _shared = LeadScorer()
    return _shared
