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

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [HANDLER:...]:
      * __init__: نام کلاس + priority + enabled
      * __call__: timing خودکار + record_handled
      * on_error: با type(e).__name__ + timing
      * set enabled: تغییر وضعیت لاگ می‌شود
      * reset_stats: لاگ می‌شود
      * on_start/on_stop: لاگ می‌شود
  - 🐛 FIX: __call__ حالا timing اندازه می‌گیرد و _record_handled
    را صدا می‌زند (قبلاً avg_process_time همیشه صفر می‌موند!)
  - 🐛 FIX: get_stats حالا dict قابل JSON-serialize برمی‌گردونه
  - 🐛 FIX: print() → log_error در on_error
  - 🧹 imports اضافه (Dict, Any, List, Optional) حذف شدند
  - 🧹 from __future__ import annotations
  - 🧹 _SLOW_HANDLE_MS برای هشدار کندی
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from core.kernel.logger import log_error, log_info, log_warning

# ─── آستانه‌ها ───
_SLOW_HANDLE_MS = 3000       # هر handle > 3s = کند


class HandlerPriority(Enum):
    """اولویت هندلرها (هرچه عدد کمتر، اولویت بالاتر)."""

    CRITICAL = 0     # بحرانی (اول از همه اجرا شود)
    HIGH = 10        # بالا
    NORMAL = 20      # معمولی (پیش‌فرض)
    LOW = 30         # پایین
    BACKGROUND = 40  # پس‌زمینه (آخر از همه)


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

        # لاگ ساخت — نام کلاس از __class__.__name__ (چون handler_name property
        # هنوز ممکنه آماده نباشه در زیرکلاسها اگه توی __init__ صدا زده بشه)
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
        pass

    @abstractmethod
    async def handle(self, event) -> Any:
        """پردازش رویداد.

        Args:
            event: رویداد دریافتی (از EventBus).

        Returns:
            Any: نتیجه پردازش (اختیاری).
        """
        pass

    @abstractmethod
    def can_handle(self, event_name: str) -> bool:
        """آیا این هندلر می‌تواند این رویداد را پردازش کند؟

        Args:
            event_name: نام رویداد (مثلاً 'message.received').

        Returns:
            bool: True اگر می‌تواند پردازش کند.
        """
        pass

    # ═══════════════════════════════════════════
    # priority / enabled
    # ═══════════════════════════════════════════
    @property
    def priority(self) -> HandlerPriority:
        """اولویت هندلر (پیش‌فرض: NORMAL)."""
        return HandlerPriority.NORMAL

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
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

        # event_name (اگه event آبجکتی با name داشت)
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
        """آمار هندلر — dict قابل JSON-serialize."""
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
        }

    def _record_handled(self, process_time: float = 0) -> None:
        """ثبت یک پردازش موفق (داخلی)."""
        self._stats["handled_count"] += 1
        self._stats["last_handled"] = datetime.now()

        old_avg = self._stats["avg_process_time"]
        count = self._stats["handled_count"]
        # میانگین متحرک ساده
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

        🐛 FIX: قبلاً این متد فقط handle() رو صدا می‌زد و هیچ
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
            self._record_handled(elapsed_ms / 1000.0)  # به ثانیه

            if elapsed_ms > _SLOW_HANDLE_MS:
                log_warning(
                    f"🐌 [HANDLER:call] {name} کند | "
                    f"{elapsed_ms:.0f}ms (آستانه {_SLOW_HANDLE_MS}ms)"
                )
            return result
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_warning(
                f"⚠️ [HANDLER:call] {name} خطا | "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )
            # صدا زدن on_error
            try:
                await self.on_error(event, e)
            except Exception as e2:
                log_error(
                    f"❌ [HANDLER:on_error] {name} | "
                    f"خود on_error خطا داد | "
                    f"{type(e2).__name__}: {e2}"
                )
            raise
