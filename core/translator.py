# core/translator.py
"""
موتور ترجمه با «واژه‌نامهٔ اختصاصی» (Glossary).

منطق (طبق درخواست):
1) اول کلمات تخصصی که خودت در data/glossary.txt تعریف کرده‌ای پیدا می‌شوند و با یک
   نشانهٔ موقت (placeholder) جایگزین می‌شوند تا هوش مصنوعی آن‌ها را عوض نکند.
2) بقیهٔ متن با هوش مصنوعی (Google) به فارسی ترجمه می‌شود.
3) نشانه‌های موقت با «معنی دلخواه تو» برگردانده می‌شوند.

یعنی معنیِ تو همیشه برندهٔ ترجمهٔ هوش مصنوعی است.
حتی اگر اینترنت/ترجمه در دسترس نباشد، باز هم واژه‌های واژه‌نامه به فارسیِ تو تبدیل می‌شوند.

فایل‌ها جدا هستند:
- data/glossary.txt   → معانی تو (داده)
- core/translator.py  → منطق (کد)
"""
import os
import re
from typing import Dict, Tuple
from core.logger import log_info, log_warning


class Translator:
    def __init__(self, glossary_file: str = None, target: str = "fa", enabled: bool = True):
        from config import BASE_DIR
        self.glossary_file = glossary_file or os.path.join(BASE_DIR, "data", "glossary.txt")
        self.target = target
        self.enabled = enabled
        self.glossary: Dict[str, Tuple[str, str]] = self._load_glossary()
        self._engine = None  # None=هنوز ساخته نشده، False=در دسترس نیست

    def _load_glossary(self) -> Dict[str, Tuple[str, str]]:
        g: Dict[str, Tuple[str, str]] = {}
        if not os.path.exists(self.glossary_file):
            return g
        with open(self.glossary_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                term, trans = line.split("|", 1)
                term, trans = term.strip(), trans.strip()
                if term and trans:
                    g[term.lower()] = (term, trans)
        return g

    def reload_glossary(self):
        """واژه‌نامه را دوباره می‌خواند (اگر فایل را عوض کردی)."""
        self.glossary = self._load_glossary()

    def _get_engine(self):
        if self._engine is None:
            try:
                from deep_translator import GoogleTranslator
                self._engine = GoogleTranslator(source="auto", target=self.target)
            except Exception as e:
                log_warning(f"موتور ترجمه در دسترس نیست: {e}")
                self._engine = False
        return self._engine

    def translate(self, text: str) -> str:
        """متن را به فارسی ترجمه می‌کند با اولویتِ واژه‌نامهٔ اختصاصی."""
        if not text or not self.enabled:
            return text

        # ۱) جایگزینی واژه‌های اختصاصی با نشانهٔ موقت
        protected = text
        restore: Dict[str, str] = {}
        for i, (_, (term, trans)) in enumerate(self.glossary.items()):
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            if pattern.search(protected):
                token = f"zzg{i}zz"   # نشانه‌ای که مترجم دست‌نخورده نگه می‌دارد
                protected = pattern.sub(token, protected)
                restore[token] = trans

        # ۲) ترجمهٔ بقیه با هوش مصنوعی (اگر در دسترس باشد)
        engine = self._get_engine()
        translated = protected
        if engine:
            try:
                translated = engine.translate(protected[:4900]) or protected
            except Exception as e:
                log_warning(f"ترجمه ناموفق بود (واژه‌نامه اعمال شد): {e}")
                translated = protected

        # ۳) برگرداندن معانی دلخواه تو
        for token, trans in restore.items():
            translated = re.sub(token, trans, translated, flags=re.IGNORECASE)

        return translated


# نمونهٔ سراسری ساده (برای استفادهٔ آسان)
_default_translator = None


def get_translator(enabled: bool = True) -> Translator:
    global _default_translator
    if _default_translator is None:
        _default_translator = Translator(enabled=enabled)
    return _default_translator


def translate_text(text: str) -> str:
    return get_translator().translate(text)
