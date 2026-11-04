# core/contracts/base_analyzer.py
"""
BaseAnalyzer — کلاس پایه برای همه آنالیزورها.

قرارداد:
  - هر آنالیزور جدید از این کلاس ارث‌بری می‌کند
  - متد analyze() را پیاده‌سازی می‌کند
  - در صورت نیاز add_keywords/add_metadata/get_score را override می‌کند

نسخهٔ ۳ (این نسخه):
  - 🎯 `_SLOW_ANALYZER_MS` حالا از env قابل تنظیم است
    (ANALYZER_SLOW_MS).
  - 🎯 `get_score()` حالا به‌جای `int | None` از `float | None`
    استفاده می‌کند (هماهنگی با `LeadScorer` که float برمی‌گرداند).
  - 🐛 FIX: `add_keywords` حالا اگر `keywords` یک لیست خالی یا
    غیرلیست باشد، امن handle می‌کند.
  - 🐛 FIX: `add_metadata` حالا اگر `message.metadata` غیر-dict باشد
    بازنویسی می‌کند (نه اینکه کرش کند).
  - 🎯 `combine_analyzers` حالا summary کامل دارد
    (ok/failed/slow/total_ms) برای debug.
  - 🎯 `combine_analyzers` حالا `analyzers` را validate می‌کند
    (اگر None یا غیر-لیست بود).
  - 🎯 افزودن `get_info()` برای debug.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any

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


# 🎯 FIX: env-based
_SLOW_ANALYZER_MS = _env_int(
    "ANALYZER_SLOW_MS", 1000, min_val=100, max_val=60_000
)


# ═══════════════════════════════════════════════════════════
# BaseAnalyzer
# ═══════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════
    # helpers (قابل override)
    # ═══════════════════════════════════════════
    async def add_keywords(
        self, message: AnalyzedMessage, keywords: list[str]
    ) -> AnalyzedMessage:
        """🎯 FIX: اضافه کردن کلیدواژه‌ها به پیام (بدون تکرار).

        Args:
            message: پیام.
            keywords: لیست کلیدواژه‌ها (اگر None/غیرلیست → skip).

        Returns:
            پیام با کلیدواژه‌های اضافه‌شده.
        """
        try:
            # 🎯 FIX: keywords می‌تواند None/غیرلیست باشد
            if not keywords or not isinstance(keywords, (list, tuple, set)):
                return message

            current = set(getattr(message, "keywords", None) or [])
            current.update(
                kw for kw in keywords if kw and isinstance(kw, str)
            )
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
        """🎯 FIX: اضافه کردن متادیتا به پیام.

        اگر `message.metadata` غیر-dict باشد، بازنویسی می‌شود.

        Args:
            message: پیام.
            key: کلید.
            value: مقدار.

        Returns:
            پیام با متادیتای اضافه‌شده.
        """
        try:
            if not key:
                return message

            current = getattr(message, "metadata", None)
            # 🎯 FIX: اگر metadata غیر-dict یا None بود، بازنویسی کن
            if not isinstance(current, dict):
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
    ) -> float | None:
        """🎯 FIX: امتیاز تحلیل (0..100) یا None.

        از float استفاده می‌کند (هماهنگی با LeadScorer).

        Args:
            message: پیام.

        Returns:
            امتیاز (float) یا None.
        """
        score = getattr(message, "score", None)
        if score is None:
            return None
        try:
            return float(score)
        except (TypeError, ValueError):
            return None

    # ═══════════════════════════════════════════
    # combine_analyzers — با لاگ دقیق
    # ═══════════════════════════════════════════
    async def combine_analyzers(
        self,
        message: AnalyzedMessage,
        analyzers: list[BaseAnalyzer] | None,
    ) -> AnalyzedMessage:
        """🎯 ترکیب چند آنالیزور روی یک پیام.

        Args:
            message: پیام اصلی.
            analyzers: لیست آنالیزورها (اگر None/خالی → skip).

        Returns:
            پیام تحلیل‌شده توسط همه آنالیزورها (حتی اگه بعضی خطا بدن).
        """
        # 🎯 FIX: validate analyzers
        if not analyzers or not isinstance(
            analyzers, (list, tuple)
        ):
            return message

        t_all = time.monotonic()
        ok = 0
        failed = 0
        slow = 0
        slow_names: list[str] = []

        for analyzer in analyzers:
            if analyzer is None:
                continue

            name = analyzer.__class__.__name__
            t0 = time.monotonic()
            try:
                message = await analyzer.analyze(message)
                elapsed_ms = (time.monotonic() - t0) * 1000
                ok += 1
                if elapsed_ms > _SLOW_ANALYZER_MS:
                    slow += 1
                    slow_names.append(f"{name}({elapsed_ms:.0f}ms)")
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
        total = ok + failed

        if failed:
            log_warning(
                f"⚠️ [ANALYZER:combine] "
                f"{ok}/{total} موفق | {failed} خطا | "
                f"{slow} کند | {total_ms:.0f}ms"
            )
        elif slow:
            log_info(
                f"✅ [ANALYZER:combine] {ok}/{total} موفق | "
                f"{slow} کند: {', '.join(slow_names)} | "
                f"{total_ms:.0f}ms"
            )
        else:
            log_info(
                f"✅ [ANALYZER:combine] {ok}/{total} موفق | "
                f"{total_ms:.0f}ms"
            )
        return message

    # ═══════════════════════════════════════════
    # get_info — 🎯 NEW
    # ═══════════════════════════════════════════
    def get_info(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "class": self.__class__.__name__,
            "slow_threshold_ms": _SLOW_ANALYZER_MS,
        }
