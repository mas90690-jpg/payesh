# core/base_analyzer.py
"""
BaseAnalyzer — کلاس پایه برای همه آنالیزورها.

قرارداد:
  - هر آنالیزور جدید از این کلاس ارث‌بری می‌کند
  - متد analyze() را پیاده‌سازی می‌کند
  - در صورت نیاز add_keywords/add_metadata/get_score را override می‌کند

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [ANALYZER:...]:
      * combine_analyzers: تعداد + شمارش موفق/خطا/کند + زمان کل
      * هر analyzer: نام + زمان + خطا (با type)
      * add_keywords / add_metadata / get_score: سطح warning فقط برای خطا
        (چون پرتکرارند — لاگ info می‌کنه فایل رو منفجر می‌کنه)
  - 🐛 FIX: print() → log_warning در combine_analyzers
  - 🐛 FIX: add_keywords مقاوم به keywords=None
  - 🐛 FIX: add_metadata با getattr امن‌تر
  - 🐛 FIX: get_score با getattr (به‌جای hasattr + access)
  - 🧹 import Dict (استفاده نمی‌شد) حذف شد
  - 🧹 from __future__ import annotations
  - 🧹 آستانهٔ کندی برای هر analyzer (1s)
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from core.kernel.logger import log_info, log_warning
from core.kernel.models import AnalyzedMessage

# ─── آستانه‌ها ───
_SLOW_ANALYZER_MS = 1000     # هر analyzer > 1s = کند


class BaseAnalyzer(ABC):
    """کلاس پایه برای همه آنالیزورها."""

    # ═══════════════════════════════════════════
    # abstract
    # ═══════════════════════════════════════════
    @abstractmethod
    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        """تحلیل پیام و اضافه کردن اطلاعات به آن.

        Args:
            message: پیام دریافتی.

        Returns:
            AnalyzedMessage: پیام با اطلاعات تحلیل‌شده.
        """
        pass

    # ═══════════════════════════════════════════
    # helpers (قابل override)
    # ═══════════════════════════════════════════
    async def add_keywords(
        self, message: AnalyzedMessage, keywords: list[str]
    ) -> AnalyzedMessage:
        """اضافه کردن کلیدواژه‌ها به پیام (بدون تکرار)."""
        try:
            current = set(getattr(message, "keywords", None) or [])
            current.update(kw for kw in keywords if kw)
            message.keywords = list(current)
        except Exception as e:
            log_warning(
                f"⚠️ [ANALYZER:add_keywords] "
                f"{type(e).__name__}: {e}"
            )
        return message

    async def add_metadata(
        self, message: AnalyzedMessage, key: str, value: Any
    ) -> AnalyzedMessage:
        """اضافه کردن متادیتا به پیام."""
        try:
            if getattr(message, "metadata", None) is None:
                message.metadata = {}
            message.metadata[key] = value
        except Exception as e:
            log_warning(
                f"⚠️ [ANALYZER:add_metadata] key={key!r} | "
                f"{type(e).__name__}: {e}"
            )
        return message

    async def get_score(
        self, message: AnalyzedMessage
    ) -> int | None:
        """برگرداندن امتیاز تحلیل (0..100) یا None."""
        return getattr(message, "score", None)

    # ═══════════════════════════════════════════
    # combine_analyzers — با لاگ دقیق
    # ═══════════════════════════════════════════
    async def combine_analyzers(
        self,
        message: AnalyzedMessage,
        analyzers: list[BaseAnalyzer],
    ) -> AnalyzedMessage:
        """ترکیب چند آنالیزور روی یک پیام.

        Args:
            message: پیام اصلی.
            analyzers: لیست آنالیزورها.

        Returns:
            پیام تحلیل‌شده توسط همه آنالیزورها (حتی اگه بعضی خطا بدن).
        """
        if not analyzers:
            return message

        t_all = time.monotonic()
        ok = 0
        failed = 0
        slow = 0

        for analyzer in analyzers:
            name = analyzer.__class__.__name__
            t0 = time.monotonic()
            try:
                message = await analyzer.analyze(message)
                elapsed_ms = (time.monotonic() - t0) * 1000
                ok += 1
                if elapsed_ms > _SLOW_ANALYZER_MS:
                    slow += 1
                    log_warning(
                        f"🐌 [ANALYZER:combine] {name} کند | "
                        f"{elapsed_ms:.0f}ms "
                        f"(آستانه {_SLOW_ANALYZER_MS}ms)"
                    )
            except Exception as e:
                failed += 1
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_warning(
                    f"⚠️ [ANALYZER:combine] {name} خطا | "
                    f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
                )

        total_ms = (time.monotonic() - t_all) * 1000
        if failed:
            log_warning(
                f"⚠️ [ANALYZER:combine] {ok} موفق / {failed} خطا / "
                f"{slow} کند | {total_ms:.0f}ms"
            )
        else:
            log_info(
                f"✅ [ANALYZER:combine] {ok} آنالیزور موفق | "
                f"{total_ms:.0f}ms"
            )
        return message
