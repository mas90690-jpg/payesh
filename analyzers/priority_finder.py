# analyzers/priority_finder.py
"""
سطح‌بندی کلیدواژه‌ها — فقط محصولات دقیق + کلیدواژه دستی.

منابع کلیدواژه:
  - products.py          (فقط مقادیر)   → T1 (+40)
  - products_en.py       (فقط مقادیر)   → T1 (+40)
  - keywords_custom.json → T1 پیش‌فرض (+40)، یا T2 اگه «سطح 2» بگی (+15)

Tier ها:
  T1 = طلایی  → +40 امتیاز (کلیدواژه)
  T2 = نقره‌ای → +15 امتیاز (کلیدواژه، ولی سبک‌تر)
  T0 = ناشناخته → +0

⚠️ برندها (کلیدهای products.py/products_en.py) کلیدواژه نیستن —
   فقط از طریق محصولات پیدا می‌شن و توی metadata (brands) ذخیره می‌شن.

پشتیبانی از دو فرمت keywords_custom.json:
  قدیمی: ["کود مایع", "سم"]
  جدید:  {"کود مایع": {"tier": "T1"}, "کود": {"tier": "T2"}}
"""
import importlib
import json
import os
import re
import time

from core.kernel.logger import log_info, log_warning

_RELOAD_SECONDS = 5.0

# وزن هر tier
TIER_BONUS = {"T1": 40, "T2": 15, "T3": 5, "T0": 0}


class PriorityFinder:
    def __init__(self):
        self.t1_words: list[str] = []
        self.t2_words: list[str] = []
        self.product_to_brand: dict[str, str] = {}
        self.keyword_tier: dict[str, str] = {}

        self._pat_t1 = None
        self._pat_t2 = None
        self._signature = None
        self._last_check = 0.0
        self._load()

    # ─── منابع ───
    @staticmethod
    def _sources() -> list[str]:
        from data import paths as data_paths
        return [
            str(data_paths.PRODUCTS_PY),
            str(data_paths.PRODUCTS_EN_PY),
            str(data_paths.KEYWORDS_CUSTOM),
        ]

    def _signature_now(self):
        out = []
        for p in self._sources():
            try:
                out.append(os.path.getmtime(p))
            except OSError:
                out.append(None)
        return tuple(out)

    # ─── لود ───
    def _load(self):
        t1: list[str] = []
        t2: list[str] = []
        prod2brand: dict[str, str] = {}

        # ۱) products.py (فارسی) — فقط مقادیر، نه برندها
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
            log_warning(f"⚠️ products.py: {e}")

        # ۲) products_en.py (انگلیسی) — فقط مقادیر
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
            log_warning(f"⚠️ products_en.py: {e}")

        # ۳) keywords_custom.json (دستی) — پیش‌فرض T1، اختیاری T2
        c_t1, c_t2, tiers = self._load_custom()
        t1.extend(c_t1)
        t2.extend(c_t2)
        self.keyword_tier = tiers

        # یکتا + مرتب (بلندترین اول تا تطبیق دقیق‌تر مقدم باشه)
        self.t1_words = sorted(set(t1), key=lambda s: (-len(s), s))
        t1_set = set(t1)
        self.t2_words = sorted(
            {b for b in t2 if b and b not in t1_set},
            key=lambda s: (-len(s), s),
        )
        self.product_to_brand = prod2brand

        # regex با مرزِ کلمه
        self._pat_t1 = self._compile(self.t1_words)
        self._pat_t2 = self._compile(self.t2_words)

        self._signature = self._signature_now()
        self._last_check = time.monotonic()
        log_info(
            f"🎯 Tier — T1={len(self.t1_words)} | T2={len(self.t2_words)} "
            f"| نگاشت محصول→برند={len(self.product_to_brand)} | "
            f"دستی T1={len(c_t1)} | دستی T2={len(c_t2)}"
        )

    @staticmethod
    def _compile(words: list[str]):
        if not words:
            return None
        try:
            _WB = r"\w"
            parts = [rf"(?<!{_WB}){re.escape(w)}(?!{_WB})" for w in words]
            return re.compile("|".join(parts), re.IGNORECASE)
        except re.error as e:
            log_warning(f"⚠️ regex: {e}")
            return None

    def _load_custom(self) -> tuple[list[str], list[str], dict[str, str]]:
        """..."""
        from data import paths as data_paths
        path = str(data_paths.KEYWORDS_CUSTOM)
        t1, t2, tiers = [], [], {}

        if not os.path.exists(path):
            return t1, t2, tiers
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log_warning(f"⚠️ keywords_custom.json: {e}")
            return t1, t2, tiers

        # ─── فرمت قدیمی: لیست خالص ───
        # همه T1 (پیش‌فرض)
        if isinstance(data, list):
            for x in data:
                if isinstance(x, str) and x.strip():
                    s = x.strip()
                    t1.append(s)
                    tiers[s] = "T1"
            return t1, t2, tiers

        # ─── فرمت جدید: دیکشنری ───
        if isinstance(data, dict):
            kws = data.get("keywords")
            if kws is None:
                log_warning(
                    f"⚠️ [PF:custom] dict بدون «keywords» | "
                    f"کلیدها: {list(data.keys())[:5]}"
                )
                return t1, t2, tiers
            if isinstance(kws, dict):
                for kw, meta in kws.items():
                    s = str(kw).strip()
                    if not s:
                        continue
                    # پیش‌فرض T1
                    if isinstance(meta, dict):
                        tier = str(meta.get("tier", "T1")).upper()
                    elif isinstance(meta, str):
                        tier = meta.upper()
                    else:
                        tier = "T1"
                    if tier not in ("T1", "T2", "T3"):
                        tier = "T1"
                    tiers[s] = tier
                    if tier == "T1":
                        t1.append(s)
                    elif tier == "T2":
                        t2.append(s)
                    # T3 فعلاً استفاده نمی‌شه ولی نگه داشته می‌شه
        return t1, t2, tiers

    def _maybe_reload(self):
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SECONDS:
            return
        self._last_check = now
        if self._signature_now() != self._signature:
            self._load()

    # ─── API ───
    def highest_tier(self, text: str | None) -> str:
        """بالاترین tier موجود در متن."""
        self._maybe_reload()
        if not text:
            return "T0"
        if self._pat_t1 and self._pat_t1.search(text):
            return "T1"
        if self._pat_t2 and self._pat_t2.search(text):
            return "T2"
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

        برند از نگاشت product_to_brand میاد (نه از T2).
        """
        self._maybe_reload()
        if not text or not self._pat_t1:
            return []
        out: list[tuple[str, str]] = []
        seen = set()
        for m in self._pat_t1.findall(text):
            if m and m not in seen:
                seen.add(m)
                brand = self.product_to_brand.get(m, "")
                out.append((m, brand))
        return out

    def find_brands(self, text: str | None) -> list[str]:
        """برندهای موجود — فقط از طریق محصولات پیدا شده."""
        self._maybe_reload()
        if not text:
            return []
        prods = self.find_products(text)
        return sorted({b for _, b in prods if b})

    def analyze(self, text: str | None) -> dict:
        """خروجی کامل برای ذخیره."""
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


_shared: PriorityFinder | None = None


def get_priority_finder() -> PriorityFinder:
    global _shared
    if _shared is None:
        _shared = PriorityFinder()
    return _shared
