# analyzers/priority_finder.py
"""
سطح‌بندی کلیدواژه‌ها — فقط محصولات دقیق + کلیدواژه دستی.

منابع کلیدواژه:
  - products.py          (فقط مقادیر)   → T1 (+40)
  - products_en.py       (فقط مقادیر)   → T1 (+40)
  - keywords_custom.json → T1 پیش‌فرض (+40)، یا T2/T3 اگه tier بدهی

Tier ها:
  T1 = طلایی  → +40 امتیاز (کلیدواژه محصول)
  T2 = نقره‌ای → +15 امتیاز (کلیدواژه سبک)
  T3 = دستی   → +5 امتیاز (کلیدواژه خیلی سبک)
  T0 = ناشناخته → +0

⚠️ برندها (کلیدهای products.py/products_en.py) کلیدواژه نیستن —
   فقط از طریق محصولات پیدا می‌شن و توی metadata (brands) ذخیره می‌شن.

پشتیبانی از دو فرمت keywords_custom.json:
  قدیمی: ["کود مایع", "سم"]
  جدید:  {"keywords": {"کود مایع": {"tier": "T1"}}}

نسخهٔ ۳ (این نسخه):
  - 🎯 `_RELOAD_SECONDS` حالا از `config.KEYWORDS_CONFIG["reload_seconds"]`
    می‌آید (نه hardcode).
  - 🎯 `TIER_BONUS` حالا از env قابل تنظیم است (TIER_BONUS_T1 و...).
  - 🐛 FIX: `_sources()` حالا فقط یک بار import می‌کند (کش).
  - 🐛 FIX: `find_products` حالا tie-break بر اساس طول (بلندترین
    اول) برای محصولات با طول یکسان.
  - 🎯 `get_stats()` متد جدید برای debug.
  - 🎯 `reset()` برای تست.
  - 🎯 لاگ کامل‌تر با prefix [PF:...] یکدست.
  - 🎯 type hints کامل.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import time
from typing import Any

from config import KEYWORDS_CONFIG
from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 P0: مسیرها از data.paths
from data import paths as data_paths


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


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════

# 🎯 FIX: از config
_RELOAD_SECONDS: float = float(
    KEYWORDS_CONFIG.get("reload_seconds", 120.0)
)

# 🎯 FIX: env-based
TIER_BONUS: dict[str, int] = {
    "T1": _env_int("TIER_BONUS_T1", 40, min_val=0, max_val=100),
    "T2": _env_int("TIER_BONUS_T2", 15, min_val=0, max_val=100),
    "T3": _env_int("TIER_BONUS_T3", 5, min_val=0, max_val=100),
    "T0": 0,
}

# وزن‌های معقول برای نمایش
_VALID_TIERS: tuple[str, ...] = ("T1", "T2", "T3")

# آستانه‌های کندی (میلی‌ثانیه)
_SLOW_LOAD_MS = _env_int(
    "PF_SLOW_LOAD_MS", 500, min_val=50, max_val=60_000
)
_SLOW_PATTERN_MS = _env_int(
    "PF_SLOW_PATTERN_MS", 200, min_val=50, max_val=60_000
)

# کاراکتر کلمه (برای مرز)
_WB = r"\w"


# ═══════════════════════════════════════════════════════════
# PriorityFinder
# ═══════════════════════════════════════════════════════════
class PriorityFinder:
    """سطح‌بندی کلیدواژه‌ها و یافتن محصول/برند در متن."""

    def __init__(self):
        self.t1_words: list[str] = []
        self.t2_words: list[str] = []
        self.t3_words: list[str] = []
        self.product_to_brand: dict[str, str] = {}
        self.keyword_tier: dict[str, str] = {}

        self._pat_t1: re.Pattern | None = None
        self._pat_t2: re.Pattern | None = None
        self._pat_t3: re.Pattern | None = None
        self._signature: tuple | None = None
        self._last_check: float = 0.0

        # 🎯 FIX: کش منابع
        self._sources_cache: list[str] | None = None

        self._load()

    # ═══════════════════════════════════════════
    # منابع
    # ═══════════════════════════════════════════
    def _sources(self) -> list[str]:
        """🎯 FIX: کش منابع — فقط یک بار محاسبه."""
        if self._sources_cache is not None:
            return self._sources_cache
        try:
            self._sources_cache = [
                str(data_paths.PRODUCTS_PY),
                str(data_paths.PRODUCTS_EN_PY),
                str(data_paths.KEYWORDS_CUSTOM),
            ]
        except Exception as e:
            log_warning(
                f"⚠️ [PF:sources] {type(e).__name__}: {e}"
            )
            self._sources_cache = []
        return self._sources_cache

    def _signature_now(self) -> tuple:
        """امضای mtime همهٔ منابع."""
        out: list[Any] = []
        for p in self._sources():
            try:
                out.append(os.path.getmtime(p))
            except OSError:
                out.append(None)
        return tuple(out)

    # ═══════════════════════════════════════════
    # لود
    # ═══════════════════════════════════════════
    def _load(self):
        """بارگذاری کامل — از منابع تا ساخت regex."""
        t0 = time.monotonic()
        t1: list[str] = []
        t2: list[str] = []
        t3: list[str] = []
        prod2brand: dict[str, str] = {}

        # ۱) products.py (فارسی)
        try:
            from data import products as p1
            importlib.reload(p1)
            data = getattr(p1, "PRODUCTS_BY_BRAND", {})
            if isinstance(data, dict):
                for brand, prods in data.items():
                    b = str(brand).strip()
                    if isinstance(prods, (list, tuple)):
                        for x in prods:
                            s = str(x).strip()
                            if s:
                                t1.append(s)
                                if b:
                                    prod2brand[s] = b
        except Exception as e:
            log_warning(
                f"⚠️ [PF:load] products.py | "
                f"{type(e).__name__}: {e}"
            )

        # ۲) products_en.py (انگلیسی)
        try:
            from data import products_en as p2
            importlib.reload(p2)
            data = getattr(p2, "PRODUCTS_EN", {})
            if isinstance(data, dict):
                for brand, prods in data.items():
                    b = str(brand).strip()
                    if isinstance(prods, (list, tuple)):
                        for x in prods:
                            s = str(x).strip()
                            if s:
                                t1.append(s)
                                if b:
                                    prod2brand[s] = b
        except Exception as e:
            log_warning(
                f"⚠️ [PF:load] products_en.py | "
                f"{type(e).__name__}: {e}"
            )

        # ۳) keywords_custom.json (دستی)
        c_t1, c_t2, c_t3, tiers = self._load_custom()
        t1.extend(c_t1)
        t2.extend(c_t2)
        t3.extend(c_t3)
        self.keyword_tier = tiers

        # ─── یکتا + مرتب (بلندترین اول) ───
        self.t1_words = sorted(
            set(t1), key=lambda s: (-len(s), s)
        )
        t1_set = set(t1)
        self.t2_words = sorted(
            {b for b in t2 if b and b not in t1_set},
            key=lambda s: (-len(s), s),
        )
        t12_set = t1_set | set(t2)
        self.t3_words = sorted(
            {b for b in t3 if b and b not in t12_set},
            key=lambda s: (-len(s), s),
        )
        self.product_to_brand = prod2brand

        # ─── regex با مرزِ کلمه ───
        self._pat_t1 = self._compile(self.t1_words)
        self._pat_t2 = self._compile(self.t2_words)
        self._pat_t3 = self._compile(self.t3_words)

        self._signature = self._signature_now()
        self._last_check = time.monotonic()

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"🎯 [PF:load] T1={len(self.t1_words)} | "
            f"T2={len(self.t2_words)} | T3={len(self.t3_words)} | "
            f"product→brand={len(self.product_to_brand)} | "
            f"دستی: T1={len(c_t1)} T2={len(c_t2)} T3={len(c_t3)} | "
            f"{elapsed_ms:.0f}ms"
        )

        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [PF:load] کند | {elapsed_ms:.0f}ms "
                f"(آستانه {_SLOW_LOAD_MS}ms)"
            )

    @staticmethod
    def _compile(words: list[str]) -> re.Pattern | None:
        """ساخت regex با مرزِ کلمه."""
        if not words:
            return None
        t0 = time.monotonic()
        try:
            parts = [
                rf"(?<!{_WB}){re.escape(w)}(?!{_WB})"
                for w in words
            ]
            pat = re.compile("|".join(parts), re.IGNORECASE)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_PATTERN_MS:
                log_warning(
                    f"🐌 [PF:compile] کند | {len(words)} کلمه | "
                    f"{elapsed_ms:.0f}ms"
                )
            return pat
        except re.error as e:
            log_warning(
                f"⚠️ [PF:compile] regex | {type(e).__name__}: {e}"
            )
            return None

    def _load_custom(
        self,
    ) -> tuple[list[str], list[str], list[str], dict[str, str]]:
        """بارگذاری keywords_custom.json — با هر دو فرمت."""
        path = str(data_paths.KEYWORDS_CUSTOM)
        t1: list[str] = []
        t2: list[str] = []
        t3: list[str] = []
        tiers: dict[str, str] = {}

        if not os.path.exists(path):
            log_info(f"ℹ️ [PF:custom] فایل نیست: {path}")
            return t1, t2, t3, tiers

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [PF:custom] JSON نامعتبر | "
                f"line={e.lineno} col={e.colno} | {e.msg}"
            )
            return t1, t2, t3, tiers
        except Exception as e:
            log_warning(
                f"⚠️ [PF:custom] {type(e).__name__}: {e}"
            )
            return t1, t2, t3, tiers

        # ─── فرمت قدیمی: لیست ───
        if isinstance(data, list):
            for x in data:
                if isinstance(x, str) and x.strip():
                    s = x.strip()
                    t1.append(s)
                    tiers[s] = "T1"
            log_info(
                f"📚 [PF:custom] فرمت=list | "
                f"count={len(t1)} (همه T1)"
            )
            return t1, t2, t3, tiers

        # ─── فرمت جدید: dict ───
        if isinstance(data, dict):
            kws = data.get("keywords")
            if kws is None:
                log_warning(
                    f"⚠️ [PF:custom] dict بدون «keywords» | "
                    f"کلیدها: {list(data.keys())[:5]}"
                )
                return t1, t2, t3, tiers

            if isinstance(kws, dict):
                for kw, meta in kws.items():
                    s = str(kw).strip()
                    if not s:
                        continue
                    if isinstance(meta, dict):
                        tier = str(meta.get("tier", "T1")).upper()
                    elif isinstance(meta, str):
                        tier = meta.upper()
                    else:
                        tier = "T1"
                    if tier not in _VALID_TIERS:
                        tier = "T1"
                    tiers[s] = tier
                    if tier == "T1":
                        t1.append(s)
                    elif tier == "T2":
                        t2.append(s)
                    elif tier == "T3":
                        t3.append(s)
                log_info(
                    f"📚 [PF:custom] فرمت=dict | "
                    f"T1={len(t1)} T2={len(t2)} T3={len(t3)}"
                )
            elif isinstance(kws, list):
                for x in kws:
                    if isinstance(x, str) and x.strip():
                        s = x.strip()
                        t1.append(s)
                        tiers[s] = "T1"
                log_info(
                    f"📚 [PF:custom] فرمت=dict_list | "
                    f"count={len(t1)} (همه T1)"
                )

        return t1, t2, t3, tiers

    def _maybe_reload(self):
        """چک mtime — reload اگر تغییر کرده."""
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SECONDS:
            return
        self._last_check = now
        new_sig = self._signature_now()
        if new_sig != self._signature:
            log_info("🔄 [PF:reload] تغییر شناسایی — reload")
            self._load()

    # ═══════════════════════════════════════════
    # API اصلی
    # ═══════════════════════════════════════════
    def highest_tier(self, text: str | None) -> str:
        """بالاترین tier موجود در متن (T1 > T2 > T3 > T0)."""
        self._maybe_reload()
        if not text:
            return "T0"
        if self._pat_t1 and self._pat_t1.search(text):
            return "T1"
        if self._pat_t2 and self._pat_t2.search(text):
            return "T2"
        if self._pat_t3 and self._pat_t3.search(text):
            return "T3"
        return "T0"

    def tier_bonus(self, tier: str) -> int:
        """بونوس امتیاز برای هر tier."""
        return TIER_BONUS.get(tier, 0)

    def tier_label(self, tier: str) -> str:
        """برچسب نمایشی."""
        return {
            "T1": "🔥 محصول",
            "T2": "🥉 سبک",
            "T3": "🔧 دستی",
            "T0": "❓",
        }.get(tier, "❓")

    def find_products(self, text: str | None) -> list[tuple[str, str]]:
        """لیست (محصول, برند)هایی که در متن پیدا شد.

        ترتیب: بلندترین اول (برای اولویت محصولات دقیق‌تر).
        """
        self._maybe_reload()
        if not text or not self._pat_t1:
            return []

        matches = self._pat_t1.findall(text)
        # 🎯 FIX: tie-break بر اساس طول (بلندترین اول)
        unique = sorted(
            {m for m in matches if m},
            key=lambda s: (-len(s), s),
        )
        return [
            (m, self.product_to_brand.get(m, ""))
            for m in unique
        ]

    def find_brands(self, text: str | None) -> list[str]:
        """برندهای موجود — فقط از طریق محصولات پیدا شده."""
        self._maybe_reload()
        if not text:
            return []
        prods = self.find_products(text)
        return sorted({b for _, b in prods if b})

    def analyze(self, text: str | None) -> dict[str, Any]:
        """خروجی کامل برای ذخیره.

        Returns:
            dict با: tier, tier_label, products, brands,
            products_str, brands_str.
        """
        self._maybe_reload()
        text = text or ""
        prods = self.find_products(text)
        brands = self.find_brands(text)
        tier = self.highest_tier(text)

        return {
            "tier": tier,
            "tier_label": self.tier_label(tier),
            "products": prods,
            "brands": brands,
            "products_str": " | ".join(p for p, _ in prods),
            "brands_str": " | ".join(brands),
        }

    # ═══════════════════════════════════════════
    # ابزار عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای /debug یا admin handler."""
        return {
            "t1_words": len(self.t1_words),
            "t2_words": len(self.t2_words),
            "t3_words": len(self.t3_words),
            "product_to_brand": len(self.product_to_brand),
            "keyword_tier": len(self.keyword_tier),
            "pattern_t1": self._pat_t1 is not None,
            "pattern_t2": self._pat_t2 is not None,
            "pattern_t3": self._pat_t3 is not None,
            "tier_bonus": dict(TIER_BONUS),
            "reload_seconds": _RELOAD_SECONDS,
            "last_check_ago_s": round(
                time.monotonic() - self._last_check, 1
            ),
        }

    def reset(self) -> None:
        """🎯 ریست کامل — فقط برای تست."""
        self.t1_words = []
        self.t2_words = []
        self.t3_words = []
        self.product_to_brand = {}
        self.keyword_tier = {}
        self._pat_t1 = None
        self._pat_t2 = None
        self._pat_t3 = None
        self._signature = None
        self._last_check = 0.0
        self._sources_cache = None
        log_info("🗑️ [PF:reset] reset شد")


# ═══════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════
_shared: PriorityFinder | None = None


def get_priority_finder() -> PriorityFinder:
    """Singleton — instance مشترک سراسری."""
    global _shared
    if _shared is None:
        _shared = PriorityFinder()
    return _shared


def reset_priority_finder() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _shared
    _shared = None
    log_info("🗑️ [PF:singleton] reset شد")
