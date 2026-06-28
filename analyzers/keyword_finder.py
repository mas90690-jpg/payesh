# analyzers/keyword_finder.py
"""
یابندهٔ کلیدواژه — منبع یکتای کلیدواژه‌ها برای کل سیستم.

کلیدواژه‌ها از این منابع داخل پوشهٔ data خوانده می‌شوند:
  - data/products.py      → ALL_PRODUCTS (نام فارسی محصولات)
  - data/products_en.py   → PRODUCTS_EN (نام انگلیسی محصولات)
  - هر فایل data/*.json   → لیست رشته یا دیکشنریِ لیست‌ها

نکتهٔ مهمِ کارایی: فایل‌ها فقط وقتی دوباره خوانده می‌شوند که تغییر کرده باشند
(بر اساس mtime) و حداکثر هر چند ثانیه یک‌بار بررسی می‌شود — نه به‌ازای هر پیام.
این از خواندن دیسک به‌ازای هر پیام (که قبلاً مصرف CPU بیهوده داشت) جلوگیری می‌کند.

این کلاس هم به‌عنوان آنالیزور (analyze) و هم به‌عنوان matcher مشترک
(match/has_match) استفاده می‌شود. کلکتورها از همین instance مشترک
(get_keyword_finder) برای تصمیم‌گیری «این پیام کلیدواژه دارد؟ پس عکسش را دانلود کن»
استفاده می‌کنند تا چیزی غیرکلیدواژه‌ای ذخیره/دانلود نشود.
"""
import os
import re
import json
import time
import importlib
from typing import List, Optional

from core.base_analyzer import BaseAnalyzer
from core.models import AnalyzedMessage

# هر چند ثانیه یک‌بار حداکثر بررسی شود که آیا فایل‌ها عوض شده‌اند
_RELOAD_CHECK_SECONDS = 5.0


class KeywordFinder(BaseAnalyzer):
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.keywords: List[str] = []
        self.pattern: Optional[re.Pattern] = None
        self._signature = None          # امضای mtime منابع (برای تشخیص تغییر)
        self._last_check = 0.0
        self._load()

    # ==================================================
    # منابع کلیدواژه و امضای تغییر
    # ==================================================
    def _source_files(self) -> List[str]:
        files = [
            os.path.join(self.data_dir, "products.py"),
            os.path.join(self.data_dir, "products_en.py"),
        ]
        if os.path.isdir(self.data_dir):
            for fn in sorted(os.listdir(self.data_dir)):
                if fn.endswith(".json"):
                    files.append(os.path.join(self.data_dir, fn))
        return files

    def _signature_now(self):
        sig = []
        for p in self._source_files():
            try:
                sig.append((p, os.path.getmtime(p)))
            except OSError:
                sig.append((p, None))
        return tuple(sig)

    # ==================================================
    # بارگذاری کلیدواژه‌ها
    # ==================================================
    def _collect_keywords(self) -> List[str]:
        words: List[str] = []

        # ۱) محصولات فارسی
        words += self._import_list("data.products", "ALL_PRODUCTS")
        # ۲) محصولات انگلیسی (دیکشنری برند → لیست)
        words += self._import_dict_values("data.products_en", "PRODUCTS_EN")
        words += self._import_dict_values("data.products", "PRODUCTS_BY_BRAND")

        # ۳) فایل‌های JSON (لیست یا دیکشنریِ لیست‌ها)
        if os.path.isdir(self.data_dir):
            for fn in sorted(os.listdir(self.data_dir)):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.data_dir, fn), "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if isinstance(data, list):
                    words += [str(x) for x in data]
                elif isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            words += [str(x) for x in v]
                        elif isinstance(v, str):
                            words.append(v)
        return words

    @staticmethod
    def _import_list(module_path: str, attr: str) -> List[str]:
        try:
            mod = importlib.import_module(module_path)
            importlib.reload(mod)  # تا تغییرات فایل دیده شود
            val = getattr(mod, attr, None)
            if isinstance(val, (list, tuple, set)):
                return [str(x) for x in val]
        except Exception:
            pass
        return []

    @staticmethod
    def _import_dict_values(module_path: str, attr: str) -> List[str]:
        out: List[str] = []
        try:
            mod = importlib.import_module(module_path)
            importlib.reload(mod)
            val = getattr(mod, attr, None)
            if isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, (list, tuple, set)):
                        out += [str(x) for x in v]
                    elif isinstance(v, str):
                        out.append(v)
        except Exception:
            pass
        return out

    def _load(self):
        raw = self._collect_keywords()
        # فقط کلیدواژه‌های غیرخالی؛ یکتا و مرتب بر اساس طول (بلندترین اول)
        # تا در alternation، تطبیقِ دقیق‌ترِ بلندتر مقدم باشد.
        cleaned = {str(k).strip() for k in raw if str(k).strip()}
        self.keywords = sorted(cleaned, key=lambda s: (-len(s), s))
        if self.keywords:
            self.pattern = re.compile(
                "|".join(re.escape(k) for k in self.keywords),
                re.IGNORECASE,
            )
        else:
            self.pattern = None  # هیچ کلیدواژه‌ای = هیچ تطبیقی
        self._signature = self._signature_now()
        self._last_check = time.monotonic()

    def _maybe_reload(self):
        now = time.monotonic()
        if now - self._last_check < _RELOAD_CHECK_SECONDS:
            return
        self._last_check = now
        if self._signature_now() != self._signature:
            self._load()

    # ==================================================
    # تطبیق (برای استفادهٔ مشترک کلکتورها و هندلر)
    # ==================================================
    def match(self, text: Optional[str]) -> List[str]:
        """فهرست کلیدواژه‌های یافت‌شده در متن (مرتب و یکتا)."""
        self._maybe_reload()
        if not self.pattern or not text:
            return []
        found = {m for m in self.pattern.findall(text) if m}
        return sorted(found)

    def has_match(self, text: Optional[str]) -> bool:
        """آیا متن حداقل یک کلیدواژه دارد؟ (سریع، بدون ساخت لیست کامل)"""
        self._maybe_reload()
        if not self.pattern or not text:
            return False
        return self.pattern.search(text) is not None

    # ==================================================
    # رابط آنالیزور
    # ==================================================
    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        message.keywords = self.match(message.text or "")
        return message


# ==================================================
# instance مشترک سراسری (یک‌بار ساخته می‌شود، همه‌جا استفاده می‌شود)
# ==================================================
_shared: Optional[KeywordFinder] = None


def get_keyword_finder(data_dir: str = "data") -> KeywordFinder:
    global _shared
    if _shared is None:
        _shared = KeywordFinder(data_dir)
    return _shared
