"""
BaseCollector — کلاس پایه برای همه پیام‌رسان‌ها.

قرارداد:
  - برای اضافه کردن پیام‌رسان جدید، از این کلاس ارث‌بری کن
  - متدهای abstract را پیاده‌سازی کن
  - در config.py تنظیمات مربوطه را اضافه کن

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [COLLECTOR:...]:
      * __init__: پلتفرم + poll_interval
      * _set_status: هر تغییر وضعیت با from→to
      * _set_error: پیام خطا + وضعیت فعلی
      * reconnect: attempt + timing + نتیجه
      * connect/disconnect (وقتی subclass صدا می‌زنه): پیش‌وند برای grep
      * __aenter__/__aexit__: ورود/خروج از context manager
  - 🐛 FIX بحرانی: `_retry_count` حالا بعد از connect موفق ریست می‌شود
    (قبلاً بعد از ۳ خطا همیشه ERROR می‌موند)
  - 🐛 FIX: `to_unified_format` حالا حتی آبجکت غیر-dict رو حفظ می‌کند
    (قبلاً به {} تبدیل می‌شد)
  - 🎯 `_max_retries` از env (COLLECTOR_MAX_RETRIES، پیش‌فرض 3)
  - 🎯 `_set_status` فقط وقتی لاگ می‌زنه که وضعیت **واقعاً** عوض شده
  - 🧹 from __future__ import annotations
  - 🧹 type hints در همه‌جا
  - 🧹 docstrings فشرده‌تر
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from core.kernel.logger import log_error, log_info, log_warning

# ─── ثابت‌ها ───
_DEFAULT_MAX_RETRIES = max(1, int(os.getenv("COLLECTOR_MAX_RETRIES", "3")))


class ConnectionStatus(Enum):
    """وضعیت اتصال کلکتور."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class BaseCollector(ABC):
    """کلاس پایه برای همه پیام‌رسان‌ها."""

    def __init__(self):
        self._status = ConnectionStatus.DISCONNECTED
        self._last_error: str | None = None
        self._retry_count = 0
        self._max_retries = _DEFAULT_MAX_RETRIES

        # لاگ ساخت — با platform_name اگه زیرکلاس تونست بده
        try:
            name = self.platform_name
        except Exception:
            name = self.__class__.__name__

        try:
            interval = self.poll_interval_seconds
        except Exception:
            interval = 2.0

        log_info(
            f"🔧 [COLLECTOR:init] {name} | "
            f"poll={interval:g}s | max_retries={self._max_retries}"
        )

    # ═══════════════════════════════════════════
    # abstract
    # ═══════════════════════════════════════════
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """اسم پلتفرم (مثلاً 'bale', 'telegram', 'rubika')."""
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """اتصال به پیام‌رسان. Returns: True اگر موفق."""
        pass

    @abstractmethod
    async def fetch_messages(
        self, since: datetime
    ) -> list[dict[str, Any]]:
        """دریافت پیام‌های جدید از زمان `since`.

        Returns:
            لیست پیام‌ها با کلیدهای استاندارد:
              id, text, chat_id, chat_name, user_id, sender_name,
              timestamp, media_type, file_id, reply_to
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """قطع اتصال از پیام‌رسان."""
        pass

    # ═══════════════════════════════════════════
    # capabilities (قابل override)
    # ═══════════════════════════════════════════
    @property
    def supports_history(self) -> bool:
        """آیا از catch-up تاریخی پشتیبانی می‌کند؟ (پیش‌فرض: False)"""
        return False

    @property
    def supports_websocket(self) -> bool:
        """آیا از WebSocket (دریافت لحظه‌ای) پشتیبانی می‌کند؟ (پیش‌فرض: False)"""
        return False

    @property
    def poll_interval_seconds(self) -> float:
        """فاصلهٔ بین دو دورِ دریافت پیام (ثانیه).

        - push-based (بله/روبیکا/تلگرام): صفِ سبک → مقدار کوچک
        - اسکرپرهای سنگین (ایتا/وب): مقدار بزرگ‌تر
        """
        return 2.0

    # ═══════════════════════════════════════════
    # status & error
    # ═══════════════════════════════════════════
    @property
    def status(self) -> ConnectionStatus:
        """وضعیت فعلی اتصال."""
        return self._status

    @property
    def is_connected(self) -> bool:
        """آیا متصل است؟"""
        return self._status == ConnectionStatus.CONNECTED

    def get_last_error(self) -> str | None:
        """آخرین خطای ذخیره‌شده."""
        return self._last_error

    def _set_error(self, error: str) -> None:
        """تنظیم خطا (داخلی) — با لاگ."""
        old_status = self._status
        self._last_error = str(error)
        self._status = ConnectionStatus.ERROR
        try:
            name = self.platform_name
        except Exception:
            name = self.__class__.__name__
        log_error(
            f"❌ [COLLECTOR:error] {name} | "
            f"{old_status.value} → ERROR | {error}"
        )

    def _set_status(self, status: ConnectionStatus) -> None:
        """تنظیم وضعیت (داخلی) — فقط در صورت تغییر واقعی لاگ می‌شود."""
        old = self._status
        self._status = status
        if old == status:
            return  # بدون تغییر — بدون لاگ
        try:
            name = self.platform_name
        except Exception:
            name = self.__class__.__name__

        # وضعیت‌های مختلف → سطح لاگ مختلف
        if status == ConnectionStatus.CONNECTED:
            log_info(
                f"✅ [COLLECTOR:status] {name} | "
                f"{old.value} → {status.value}"
            )
        elif status == ConnectionStatus.ERROR:
            log_warning(
                f"⚠️ [COLLECTOR:status] {name} | "
                f"{old.value} → {status.value}"
            )
        else:
            log_info(
                f"🔄 [COLLECTOR:status] {name} | "
                f"{old.value} → {status.value}"
            )

    # ═══════════════════════════════════════════
    # fetch_history (قابل override)
    # ═══════════════════════════════════════════
    async def fetch_history(self, since: datetime) -> list[dict[str, Any]]:
        """خواندن پیام‌های عقب‌افتاده (catch-up). پیش‌فرض: []."""
        return []

    # ═══════════════════════════════════════════
    # reconnect با backoff
    # ═══════════════════════════════════════════
    async def reconnect(self) -> bool:
        """تلاش برای اتصال مجدد — با ریست retry بعد از موفقیت.

        Note:
            این متد فقط یک بار تلاش می‌کند (نه backoff چندمرحله‌ای).
            backoff واقعی در orchestrator._supervise_reconnect است.
        """
        try:
            name = self.platform_name
        except Exception:
            name = self.__class__.__name__

        self._status = ConnectionStatus.RECONNECTING
        self._retry_count += 1
        attempt = self._retry_count

        if attempt > self._max_retries:
            self._status = ConnectionStatus.ERROR
            self._last_error = "Max retries exceeded"
            log_error(
                f"❌ [COLLECTOR:reconnect] {name} | "
                f"حداکثر تلاش ({self._max_retries}) رد شد"
            )
            return False

        log_info(
            f"🔁 [COLLECTOR:reconnect] {name} | "
            f"تلاش #{attempt}/{self._max_retries}"
        )
        t0 = time.monotonic()

        # disconnect (شاید نیمه‌اتصال)
        try:
            await self.disconnect()
        except Exception as e:
            log_warning(
                f"⚠️ [COLLECTOR:reconnect] {name} | disconnect | "
                f"{type(e).__name__}: {e}"
            )

        # connect
        try:
            ok = await self.connect()
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [COLLECTOR:reconnect] {name} | connect استثنا | "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )
            return False

        elapsed_ms = (time.monotonic() - t0) * 1000
        if ok:
            # 🐛 FIX: ریست retry_count بعد از موفقیت (قبلاً نمی‌شد!)
            self._retry_count = 0
            log_info(
                f"✅ [COLLECTOR:reconnect] {name} | موفق | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            log_warning(
                f"⚠️ [COLLECTOR:reconnect] {name} | connect=False | "
                f"{elapsed_ms:.0f}ms"
            )
        return ok

    # ═══════════════════════════════════════════
    # health_check
    # ═══════════════════════════════════════════
    async def health_check(self) -> bool:
        """بررسی سلامت اتصال (پیش‌فرض: وضعیت فعلی).

        کلکتورها می‌تونن override کنن (مثلاً ping واقعی).
        """
        return self.is_connected

    # ═══════════════════════════════════════════
    # format conversion
    # ═══════════════════════════════════════════
    def to_unified_format(
        self, raw_message: Any, platform: str
    ) -> dict[str, Any]:
        """تبدیل پیام خام به فرمت یکسان.

        🐛 FIX: حالا آبجکت‌های غیر-dict رو هم حفظ می‌کنه (قبلاً {}
        برمی‌گشت و آبجکت گم می‌شد).
        """
        if isinstance(raw_message, dict):
            raw_data: Any = raw_message
        else:
            # آبجکت یا هر چیز دیگه → به‌عنوان raw_data نگه‌دار
            raw_data = raw_message
        return {
            "platform": platform,
            "raw_data": raw_data,
        }

    # ═══════════════════════════════════════════
    # context manager
    # ═══════════════════════════════════════════
    async def __aenter__(self) -> BaseCollector:
        """ورود به context manager — اتصال خودکار."""
        try:
            name = self.platform_name
        except Exception:
            name = self.__class__.__name__
        log_info(f"▶️ [COLLECTOR:enter] {name} | connect...")
        t0 = time.monotonic()
        ok = await self.connect()
        elapsed_ms = (time.monotonic() - t0) * 1000
        if not ok:
            log_warning(
                f"⚠️ [COLLECTOR:enter] {name} | connect=False | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"✅ [COLLECTOR:enter] {name} | {elapsed_ms:.0f}ms"
            )
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        """خروج از context manager — قطع اتصال خودکار."""
        try:
            name = self.platform_name
        except Exception:
            name = self.__class__.__name__
        log_info(f"⏹️ [COLLECTOR:exit] {name} | disconnect...")
        t0 = time.monotonic()
        try:
            await self.disconnect()
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"✅ [COLLECTOR:exit] {name} | {elapsed_ms:.0f}ms"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [COLLECTOR:exit] {name} | "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )
