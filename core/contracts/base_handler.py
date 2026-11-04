"""
BaseHandler — کلاس پایه برای همه هندلرها (قابلیت‌ها).

قرارداد:
  - هر هندلر جدید از این کلاس ارث‌بری می‌کند
  - متدهای abstract را پیاده‌سازی می‌کند
  - در صورت نیاز on_start/on_stop را override می‌کند

نحوه اضافه کردن هندلر جدید:
  ۱) فایل جدید در پوشه handlers/ بساز
  ۲) کلاس را از BaseHandler ارث‌بری کن
  ۳) متدهای required را پیاده‌سازی کن
  ۴) در config.py تنظیمات مربوطه را اضافه کن

نسخهٔ ۳ (این نسخه):
  - 🎯 `priority` حالا یک property است — توسط `auto_subscribe` استفاده
    می‌شود (که هندلرها را بر اساس priority مرتب می‌کند).
  - 🎯 `listens_to()` متد اختیاری — رویدادهای پویا (اگر هندلر
    بخواهد به‌جای can_handle از این استفاده کند).
  - 🎯 `_SLOW_HANDLE_MS` حالا از env قابل تنظیم است.
  - 🐛 FIX: `__call__` حالا زمان را در `_record_handled` ثبت می‌کند
    (قبلاً avg_process_time همیشه صفر می‌ماند).
  - 🐛 FIX: `get_stats()` حالا `priority` را هم برمی‌گرداند.
  - 🎯 `handler_name` و `priority` در `__init__` لاگ می‌شوند.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from core.kernel.logger import log_error, log_info, log_warning


# ═══════════════════════════════════════════════════════════
# آستانه‌ها
# ═══════════════════════════════════════════════════════════
def _env_int(name: str, default: int) -> int:
    """خواندن int از env — با fallback امن."""
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


# 🎯 FIX: قابل تنظیم از env
_SLOW_HANDLE_MS = _env_int("HANDLER_SLOW_MS", 3000)


# ═══════════════════════════════════════════════════════════
# اولویت‌ها
# ═══════════════════════════════════════════════════════════
class HandlerPriority(Enum):
    """اولویت هندلرها (هرچه عدد کمتر، اولویت بالاتر).

    ترتیب اجرا از پایین به بالا:
        CRITICAL  → اول از همه
        HIGH
        NORMAL    → پیش‌فرض
        LOW
        BACKGROUND → آخر از همه
    """
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    BACKGROUND = 40


# ═══════════════════════════════════════════════════════════
# BaseHandler
# ═══════════════════════════════════════════════════════════
class BaseHandler(ABC):
    """کلاس پایه برای همه هندلرها."""

    def __init__(self):
        self._enabled = True
        self._stats: dict[str, Any] = {
            "handled_count": 0,
            "error_count": 0,
            "last_handled": None,
            "avg_process_time": 0.0,
        }

        # لاگ ساخت
        try:
            cls_name = self.__class__.__name__
        except Exception:
            cls_name = "?"

        log_info(
            f"🔧 [HANDLER:init] {cls_name} | "
            f"priority={self.priority.name} | enabled={self._enabled}"
        )

    # ═══════════════════════════════════════════
    # abstract
    # ═══════════════════════════════════════════
    @property
    @abstractmethod
    def handler_name(self) -> str:
        """نام قابلیت (برای لاگ و شناسایی)."""

    @abstractmethod
    async def handle(self, event) -> Any:
        """پردازش رویداد.

        Args:
            event: رویداد دریافتی (از EventBus).

        Returns:
            Any: نتیجه پردازش (اختیاری).
        """

    @abstractmethod
    def can_handle(self, event_name: str) -> bool:
        """آیا این هندلر می‌تواند این رویداد را پردازش کند؟

        Args:
            event_name: نام رویداد (مثلاً 'message.received').

        Returns:
            bool: True اگر می‌تواند پردازش کند.
        """

    # ═══════════════════════════════════════════
    # priority / enabled
    # ═══════════════════════════════════════════
    @property
    def priority(self) -> HandlerPriority:
        """🎯 FIX: اولویت هندلر (پیش‌فرض: NORMAL).

        این property توسط `auto_subscribe` استفاده می‌شود برای
        مرتب‌سازی هندلرها.
        """
        return HandlerPriority.NORMAL

    @property
    def enabled(self) -> bool:
        """آیا هندلر فعال است؟"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """تنظیم فعال/غیرفعال — با لاگ."""
        old = self._enabled
        self._enabled = bool(value)
        if old != self._enabled:
            try:
                name = self.handler_name
            except Exception:
                name = self.__class__.__name__
            icon = "✅" if self._enabled else "⏸️"
            log_info(
                f"{icon} [HANDLER:enabled] {name} | "
                f"{'فعال' if self._enabled else 'غیرفعال'} شد"
            )

    # ═══════════════════════════════════════════
    # 🎯 listens_to — رویدادهای پویا (اختیاری)
    # ═══════════════════════════════════════════
    def listens_to(self) -> list[str]:
        """🎯 رویدادهای پویا — اگر هندلر می‌خواهد رویدادهای خاص خودش.

        اگر این متد را override کنی، `auto_subscribe` به‌جای can_handle
        از این استفاده می‌کند. پیش‌فرض: لیست خالی (از can_handle استفاده می‌شود).

        Returns:
            لیست نام رویدادها.
        """
        return []

    # ═══════════════════════════════════════════
    # lifecycle (قابل override)
    # ═══════════════════════════════════════════
    async def on_start(self) -> None:
        """هنگام شروع سیستم — برای راه‌اندازی منابع."""
        try:
            name = self.handler_name
        except Exception:
            name = self.__class__.__name__
        log_info(f"▶️ [HANDLER:on_start] {name}")

    async def on_stop(self) -> None:
        """هنگام توقف سیستم — برای پاکسازی منابع."""
        try:
            name = self.handler_name
        except Exception:
            name = self.__class__.__name__
        log_info(
            f"⏹️ [HANDLER:on_stop] {name} | "
            f"stats={self.get_stats()}"
        )

    # ═══════════════════════════════════════════
    # on_error
    # ═══════════════════════════════════════════
    async def on_error(self, event, error: Exception) -> None:
        """هنگام خطا در پردازش رویداد."""
        self._stats["error_count"] += 1
        try:
            name = self.handler_name
        except Exception:
            name = self.__class__.__name__

        event_name = "?"
        try:
            event_name = getattr(event, "name", "?")
        except Exception:
            pass

        log_error(
            f"❌ [HANDLER:error] {name} | event={event_name} | "
            f"{type(error).__name__}: {error}"
        )

    # ═══════════════════════════════════════════
    # stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """آمار هندلر — dict قابل JSON-serialize.

        🎯 FIX: حالا `priority` هم برمی‌گردد.
        """
        last = self._stats.get("last_handled")
        return {
            "handled_count": self._stats.get("handled_count", 0),
            "error_count": self._stats.get("error_count", 0),
            "last_handled": (
                last.isoformat() if isinstance(last, datetime) else last
            ),
            "avg_process_time": round(
                self._stats.get("avg_process_time", 0.0), 4
            ),
            "priority": self.priority.name,   # 🎯 NEW
        }

    def _record_handled(self, process_time: float = 0) -> None:
        """ثبت یک پردازش موفق (داخلی) — با میانگین متحرک."""
        self._stats["handled_count"] += 1
        self._stats["last_handled"] = datetime.now()

        old_avg = self._stats["avg_process_time"]
        count = self._stats["handled_count"]
        self._stats["avg_process_time"] = (
            (old_avg * (count - 1) + process_time) / count
        )

    def reset_stats(self) -> None:
        """بازنشانی آمار."""
        old = self.get_stats()
        self._stats = {
            "handled_count": 0,
            "error_count": 0,
            "last_handled": None,
            "avg_process_time": 0.0,
        }
        try:
            name = self.handler_name
        except Exception:
            name = self.__class__.__name__
        log_info(
            f"🔄 [HANDLER:reset_stats] {name} | "
            f"قبل: handled={old['handled_count']} "
            f"errors={old['error_count']} "
            f"avg={old['avg_process_time']}s"
        )

    # ═══════════════════════════════════════════
    # __call__ — entry point از EventBus
    # ═══════════════════════════════════════════
    async def __call__(self, event) -> Any:
        """اجرای هندلر به‌عنوان تابع — با timing خودکار.

        🐛 FIX: قبلاً این متد فقط handle() را صدا می‌زد و هیچ
        timing/record_handled نداشت → avg_process_time همیشه صفر می‌موند.
        """
        try:
            name = self.handler_name
        except Exception:
            name = self.__class__.__name__

        t0 = time.monotonic()
        try:
            result = await self.handle(event)
            elapsed_ms = (time.monotonic() - t0) * 1000
            self._record_handled(elapsed_ms / 1000.0)

            if elapsed_ms > _SLOW_HANDLE_MS:
                log_warning(
                    f"🐌 [HANDLER:call] {name} کند | "
                    f"{elapsed_ms:.0f}ms "
                    f"(آستانه {_SLOW_HANDLE_MS}ms)"
                )
            return result
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_warning(
                f"⚠️ [HANDLER:call] {name} خطا | "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )
            try:
                await self.on_error(event, e)
            except Exception as e2:
                log_error(
                    f"❌ [HANDLER:on_error] {name} | "
                    f"خود on_error خطا داد | "
                    f"{type(e2).__name__}: {e2}"
                )
            raise
