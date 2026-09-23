# core/translator.py
"""
موتور ترجمه با «واژه‌نامهٔ اختصاصی» (Glossary).

منطق (طبق درخواست):
  ۱) اول کلمات تخصصی که خودت در data/glossary.txt تعریف کرده‌ای پیدا می‌شوند
     و با یک نشانهٔ موقت (placeholder) جایگزین می‌شوند تا مترجم آن‌ها را عوض نکند.
  ۲) بقیهٔ متن با هوش مصنوعی (Google) به فارسی ترجمه می‌شود.
  ۳) نشانه‌های موقت با «معنی دلخواه تو» برگردانده می‌شوند.

یعنی معنیِ تو همیشه برندهٔ ترجمهٔ هوش مصنوعی است.
حتی اگر اینترنت/ترجمه در دسترس نباشد، باز هم واژه‌های واژه‌نامه به فارسیِ تو
تبدیل می‌شوند.

فایل‌ها جدا هستند:
  - data/glossary.txt   → معانی تو (داده)
  - core/translator.py  → منطق (کد)

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX بحرانی: re.sub دیگر trans را به‌عنوان regex تفسیر نمی‌کند
    (از lambda m: trans استفاده می‌کنیم، نه string raw)
  - 🐛 FIX: _load_glossary حالا try/except دارد + لاگ خطوط invalid
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [TRANS:...]:
      * init: path + target + enabled
      * glossary load: تعداد terms + خطوط skip‌شده با دلیل
      * engine: کِی ساخته شد، کِی fail خورد
      * translate: تعداد glossary hit + engine + زمان + ورودی/خروجی
      * خطاها: type(e).__name__ + context
  - 🎯 tokens تصادفی‌تر (uuid-based) — collision-proof
  - 🎯 هشدار در صورت truncation (protected > 4900)
  - 🧹 from __future__ import annotations
  - 🧹 type hints در همه‌جا
  - 🧹 reload_glossary + get_translator + translate_text همه لاگ دارن
"""
from __future__ import annotations

import os
import re
import time
import uuid

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── ثابت‌ها ───
_GOOGLE_TRANSLATE_MAX = 4900   # سقف per-request (deep_translator/Google)
_LOG_PREVIEW_LEN = 60
_SLOW_GLOSSARY_MS = 500
_SLOW_TRANSLATE_MS = 3000


class Translator:
    """مترجم با واژه‌نامهٔ اختصاصی — اول واژه‌نامه، بعد هوش مصنوعی."""

    def __init__(
        self,
        glossary_file: str | None = None,
        target: str = "fa",
        enabled: bool = True,
    ):
        from data import paths as data_paths

        self.glossary_file = glossary_file or str(data_paths.GLOSSARY)
        self.target = target
        self.enabled = enabled
        # _engine: None=هنوز ساخته نشده | object=آماده | False=در دسترس نیست
        self._engine: object = None
        self.glossary: dict[str, tuple[str, str]] = {}

        log_info(
            f"🌐 [TRANS:init] آماده‌سازی | file={self.glossary_file} | "
            f"target={target} | enabled={enabled}"
        )

        t0 = time.monotonic()
        self.glossary = self._load_glossary()
        elapsed_ms = (time.monotonic() - t0) * 1000

        log_success(
            f"✅ [TRANS:init] کامل | glossary_terms={len(self.glossary)} | "
            f"{elapsed_ms:.0f}ms"
        )
        if elapsed_ms > _SLOW_GLOSSARY_MS:
            log_warning(
                f"🐌 [TRANS:init] بارگذاری glossary کند | "
                f"{elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # بارگذاری واژه‌نامه
    # ═══════════════════════════════════════════
    def _load_glossary(self) -> dict[str, tuple[str, str]]:
        """خواندن data/glossary.txt — هر خط: `term | trans`."""
        g: dict[str, tuple[str, str]] = {}

        if not os.path.exists(self.glossary_file):
            log_warning(
                f"⚠️ [TRANS:glossary] فایل نیست | {self.glossary_file} — "
                f"glossary خالی"
            )
            return g

        try:
            with open(self.glossary_file, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            log_error(
                f"❌ [TRANS:glossary] خواندن | {type(e).__name__}: {e} | "
                f"{self.glossary_file}"
            )
            return g

        skipped_empty = 0
        skipped_comment = 0
        skipped_no_pipe = 0
        skipped_invalid = 0
        loaded = 0

        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line:
                skipped_empty += 1
                continue
            if line.startswith("#"):
                skipped_comment += 1
                continue
            if "|" not in line:
                skipped_no_pipe += 1
                if skipped_no_pipe <= 3:
                    log_warning(
                        f"⚠️ [TRANS:glossary] خط {lineno}: بدون `|` | "
                        f"{line[:_LOG_PREVIEW_LEN]!r}"
                    )
                continue

            term, trans = line.split("|", 1)
            term, trans = term.strip(), trans.strip()
            if not term or not trans:
                skipped_invalid += 1
                if skipped_invalid <= 3:
                    log_warning(
                        f"⚠️ [TRANS:glossary] خط {lineno}: term/trans خالی | "
                        f"term={term!r} trans={trans!r}"
                    )
                continue

            g[term.lower()] = (term, trans)
            loaded += 1

        # لاگ خلاصه
        log_info(
            f"📚 [TRANS:glossary] {loaded} term بارگذاری شد از "
            f"{len(lines)} خط | skip: "
            f"empty={skipped_empty} comment={skipped_comment} "
            f"no_pipe={skipped_no_pipe} invalid={skipped_invalid}"
        )
        return g

    def reload_glossary(self) -> int:
        """واژه‌نامه را دوباره می‌خواند (اگر فایل را عوض کردی).

        Returns:
            تعداد terms جدید.
        """
        old_count = len(self.glossary)
        t0 = time.monotonic()
        self.glossary = self._load_glossary()
        elapsed_ms = (time.monotonic() - t0) * 1000
        new_count = len(self.glossary)
        delta = new_count - old_count
        sign = "+" if delta >= 0 else ""

        log_success(
            f"♻️ [TRANS:reload] {old_count} → {new_count} ({sign}{delta}) | "
            f"{elapsed_ms:.0f}ms"
        )
        return new_count

    # ═══════════════════════════════════════════
    # engine (lazy)
    # ═══════════════════════════════════════════
    def _get_engine(self):
        """ساخت lazy از deep_translator.GoogleTranslator.

        Returns:
            engine object | False (اگه در دسترس نیست)
        """
        if self._engine is not None:
            return self._engine

        t0 = time.monotonic()
        try:
            from deep_translator import GoogleTranslator

            self._engine = GoogleTranslator(
                source="auto", target=self.target
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_success(
                f"✅ [TRANS:engine] GoogleTranslator آماده | "
                f"target={self.target} | {elapsed_ms:.0f}ms"
            )
            return self._engine
        except ImportError as e:
            log_warning(
                f"⚠️ [TRANS:engine] deep_translator نصب نیست | "
                f"{e} — فقط glossary اعمال می‌شود"
            )
            self._engine = False
            return False
        except Exception as e:
            log_warning(
                f"⚠️ [TRANS:engine] ساخت ناموفق | "
                f"{type(e).__name__}: {e} — فقط glossary اعمال می‌شود"
            )
            self._engine = False
            return False

    # ═══════════════════════════════════════════
    # translate — هسته
    # ═══════════════════════════════════════════
    def translate(self, text: str) -> str:
        """متن را با اولویت واژه‌نامه ترجمه می‌کند."""
        if not text:
            return text
        if not self.enabled:
            log_info(
                f"ℹ️ [TRANS:translate] غیرفعال — متن دست‌نخورده برگشت | "
                f"len={len(text)}"
            )
            return text

        t_all = time.monotonic()
        in_len = len(text)

        # ─── ۱) protect glossary terms ───
        protected, restore = self._protect_glossary_terms(text)
        glossary_hits = len(restore)

        # ─── ۲) ترجمه با engine (اگر در دسترس) ───
        engine = self._get_engine()
        translated = protected
        engine_used = False

        if engine:
            try:
                truncated = protected[:_GOOGLE_TRANSLATE_MAX]
                if len(protected) > _GOOGLE_TRANSLATE_MAX:
                    log_warning(
                        f"⚠️ [TRANS:translate] متن بلند | "
                        f"original={len(protected)} → "
                        f"truncated={_GOOGLE_TRANSLATE_MAX}"
                    )
                t_eng = time.monotonic()
                result = engine.translate(truncated)
                eng_ms = (time.monotonic() - t_eng) * 1000
                if result:
                    translated = result
                    engine_used = True
                    if eng_ms > _SLOW_TRANSLATE_MS:
                        log_warning(
                            f"🐌 [TRANS:translate] engine کند | "
                            f"{eng_ms:.0f}ms"
                        )
                else:
                    log_warning(
                        "⚠️ [TRANS:translate] engine پاسخ خالی داد — "
                        "glossary-only"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [TRANS:translate] engine خطا | "
                    f"{type(e).__name__}: {e} — glossary-only"
                )
                translated = protected

        # ─── ۳) restore glossary terms ───
        restored_count = 0
        for token, trans in restore.items():
            # 🐛 FIX: از lambda استفاده می‌کنیم تا trans به‌عنوان regex تفسیر نشود
            # (مثلاً اگه trans شامل `\1` یا `\\` باشه)
            new_translated, n = re.subn(
                re.escape(token),
                lambda m: trans,
                translated,
                flags=re.IGNORECASE,
            )
            if n > 0:
                translated = new_translated
                restored_count += n
            else:
                # مترجم نشانه را عوض کرده — هشدار
                log_warning(
                    f"⚠️ [TRANS:translate] token گم شد | "
                    f"token={token!r} trans={trans[:_LOG_PREVIEW_LEN]!r}"
                )

        elapsed_ms = (time.monotonic() - t_all) * 1000
        out_len = len(translated)
        changed = translated != text

        log_info(
            f"🌐 [TRANS:translate] in={in_len}ch out={out_len}ch | "
            f"glossary: hits={glossary_hits} restored={restored_count} | "
            f"engine={'✅' if engine_used else '—'} | "
            f"changed={'✅' if changed else '—'} | "
            f"{elapsed_ms:.0f}ms"
        )

        if elapsed_ms > _SLOW_TRANSLATE_MS:
            log_warning(
                f"🐌 [TRANS:translate] کند | {elapsed_ms:.0f}ms | "
                f"engine_used={engine_used}"
            )

        return translated

    # ═══════════════════════════════════════════
    # Protect glossary terms
    # ═══════════════════════════════════════════
    def _protect_glossary_terms(
        self, text: str
    ) -> tuple[str, dict[str, str]]:
        """واژه‌های glossary را با token موقت جایگزین می‌کند.

        Args:
            text: متن اصلی.

        Returns:
            (protected_text, {token: trans})
        """
        protected = text
        restore: dict[str, str] = {}
        # 🎯 token با uuid — collision-proof (نسبت به zzg0zz)
        run_id = uuid.uuid4().hex[:8]

        for i, (_, (term, trans)) in enumerate(self.glossary.items()):
            try:
                pattern = re.compile(re.escape(term), re.IGNORECASE)
            except re.error as e:
                log_warning(
                    f"⚠️ [TRANS:protect] regex term نامعتبر | "
                    f"term={term!r} | {type(e).__name__}: {e}"
                )
                continue

            if not pattern.search(protected):
                continue

            token = f"__T{run_id}_{i}__"
            protected = pattern.sub(token, protected)
            restore[token] = trans

        return protected, restore

    # ═══════════════════════════════════════════
    # عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict:
        """خلاصهٔ وضعیت — برای /debug یا admin handler."""
        return {
            "glossary_terms": len(self.glossary),
            "glossary_file": self.glossary_file,
            "target": self.target,
            "enabled": self.enabled,
            "engine": (
                "not_built" if self._engine is None
                else ("unavailable" if self._engine is False else "ready")
            ),
        }


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_default_translator: Translator | None = None


def get_translator(enabled: bool = True) -> Translator:
    """دریافت نمونهٔ Singleton Translator."""
    global _default_translator
    if _default_translator is None:
        log_info(
            f"🏗️ [TRANS:singleton] ساخت نمونهٔ اول | enabled={enabled}"
        )
        _default_translator = Translator(enabled=enabled)
    return _default_translator


def translate_text(text: str) -> str:
    """ترجمهٔ سریع — برای استفاده در یک خط."""
    return get_translator().translate(text)
