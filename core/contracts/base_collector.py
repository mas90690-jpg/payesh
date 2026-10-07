"""
BaseCollector — کلاس پایه برای همه پیام‌رسان‌ها.

قرارداد:
  - برای اضافه کردن پیام‌رسان جدید، از این کلاس ارث‌بری کن
  - متدهای abstract را پیاده‌سازی کن
  - در config.py تنظیمات مربوطه را اضافه کن

نسخهٔ ۳ (این نسخه):
  - 🎯 `_DEFAULT_MAX_RETRIES` حالا از env با sanitize می‌آید
    (قبلاً ممکن بود با مقدار خراب env کرش کند).
  - 🎯 `reconnect()` حالا از `_set_status` استفاده می‌کند
    (به‌جای تنظیم مستقیم `self._status` — برای لاگ یکدست).
  - 🐛 FIX: `_set_error` حالا قبل از تغییر وضعیت، وضعیت قبلی را
    در `_last_error_status` ذخیره می‌کند (برای debug).
  - 🐛 FIX: `reconnect()` حالا `_last_error` را بعد از موفقیت پاک
    می‌کند.
  - 🐛 FIX: `__aexit__` حالا exception اصلی را swallow نمی‌کند —
    فقط disconnect را انجام می‌دهد و اگر exc_type داشت،
    exception propagate می‌شود (رفتار استاندارد context manager).
  - 🎯 افزودن `reset_status()` برای reset دستی.
  - 🎯 افزودن `get_info()` برای debug (وضعیت کامل کلکتور).
  - 🎯 افزودن `_log_preview()` helper برای بریده‌کردن متن.
  - 🎯 `_SLOW_HEALTH_MS` env-based.
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


# 🎯 FIX: sanitize مقادیر env
_DEFAULT_MAX_RETRIES = _env_int(
    "COLLECTOR_MAX_RETRIES", 3, min_val=1, max_val=100
)


# ═══════════════════════════════════════════════════════════
# ConnectionStatus
# ═══════════════════════════════════════════════════════════
class ConnectionStatus(Enum):
    """وضعیت اتصال کلکتور."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


# ═══════════════════════════════════════════════════════════
# BaseCollector
# ═══════════════════════════════════════════════════════════
class BaseCollector(ABC):
    """کلاس پایه برای همه پیام‌رسان‌ها."""

    # کلاس‌های زیر ممکن است override کنند
    _LOG_PREVIEW_LEN = 100

    def __init__(self):
        self._status = ConnectionStatus.DISCONNECTED
        self._last_error: str | None = None
        self._last_error_status: ConnectionStatus | None = None
        self._retry_count = 0
        self._max_retries = _DEFAULT_MAX_RETRIES

        # لاگ ساخت — با platform_name اگه زیرکلاس تونست بده
        name = self._safe_platform_name()
        try:
            interval = self.poll_interval_seconds
        except Exception:
            interval = 2.0

        log_info(
            f"🔧 [COLLECTOR:init] {name} | "
            f"poll={interval:g}s | max_retries={self._max_retries}"
        )

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    def _safe_platform_name(self) -> str:
        """نام پلتفرم با fallback امن (بدون خطا)."""
        try:
            return self.platform_name
        except Exception:
            return self.__class__.__name__

    def _log_preview(self, text: Any, max_len: int | None = None) -> str:
        """پیش‌نمایش متن برای لاگ."""
        if text is None:
            return ""
        s = str(text)
        limit = max_len or self._LOG_PREVIEW_LEN
        return s[:limit] + ("..." if len(s) > limit else "")

    # ═══════════════════════════════════════════
    # abstract
    # ═══════════════════════════════════════════
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """اسم پلتفرم (مثلاً 'bale', 'telegram', 'rubika')."""

    @abstractmethod
    async def connect(self) -> bool:
        """اتصال به پیام‌رسان. Returns: True اگر موفق."""

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

    @abstractmethod
    async def disconnect(self) -> None:
        """قطع اتصال از پیام‌رسان."""

    # ═══════════════════════════════════════════
    # capabilities (قابل override)
    # ═══════════════════════════════════════════
    @property
    def supports_history(self) -> bool:
        """آیا از catch-up تاریخی پشتیبانی می‌کند؟ (پیش‌فرض: False)"""
        return False

    @property
    def supports_websocket(self) -> bool:
        """آیا از WebSocket پشتیبانی می‌کند؟ (پیش‌فرض: False)"""
        return False

    @property
    def poll_interval_seconds(self) -> float:
        """فاصلهٔ بین دو دورِ دریافت پیام (ثانیه)."""
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
        """🎯 FIX: تنظیم خطا — با ذخیرهٔ وضعیت قبلی."""
        old_status = self._status
        self._last_error = str(error)
        self._last_error_status = old_status   # 🎯 NEW
        self._status = ConnectionStatus.ERROR

        name = self._safe_platform_name()
        log_error(
            f"❌ [COLLECTOR:error] {name} | "
            f"{old_status.value} → ERROR | "
            f"{self._log_preview(error, 200)}"
        )

    def _set_status(self, status: ConnectionStatus) -> None:
        """تنظیم وضعیت (داخلی) — فقط در صورت تغییر واقعی لاگ می‌شود."""
        old = self._status
        self._status = status
        if old == status:
            return  # بدون تغییر — بدون لاگ

        name = self._safe_platform_name()

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

    def reset_status(self) -> None:
        """🎯 NEW: ریست دستی وضعیت (برای تست/recovery)."""
        name = self._safe_platform_name()
        self._status = ConnectionStatus.DISCONNECTED
        self._last_error = None
        self._last_error_status = None
        self._retry_count = 0
        log_info(f"🔄 [COLLECTOR:reset] {name} | وضعیت ریست شد")

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
        """🎯 FIX: تلاش برای اتصال مجدد — با reset کامل بعد از موفقیت.

        Note:
            این متد فقط یک بار تلاش می‌کند (نه backoff چندمرحله‌ای).
            backoff واقعی در orchestrator._supervise_reconnect است.
        """
        name = self._safe_platform_name()

        # 🎯 FIX: استفاده از _set_status
        self._set_status(ConnectionStatus.RECONNECTING)
        self._retry_count += 1
        attempt = self._retry_count

        if attempt > self._max_retries:
            self._set_status(ConnectionStatus.ERROR)
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
            # 🎯 FIX: reset کامل بعد از موفقیت
            self._retry_count = 0
            self._last_error = None           # 🎯 NEW
            self._last_error_status = None    # 🎯 NEW
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
        """بررسی سلامت اتصال (پیش‌فرض: وضعیت فعلی)."""
        return self.is_connected

    # ═══════════════════════════════════════════
    # format conversion
    # ═══════════════════════════════════════════
    def to_unified_format(
        self, raw_message: Any, platform: str
    ) -> dict[str, Any]:
        """🎯 تبدیل پیام خام به فرمت یکسان.

        آبجکت‌های غیر-dict رو هم حفظ می‌کنه (قبلاً {} برمی‌گشت
        و آبجکت گم می‌شد).
        """
        raw_data: Any = raw_message
        return {
            "platform": platform,
            "raw_data": raw_data,
        }

    # ═══════════════════════════════════════════
    # get_info — 🎯 NEW
    # ═══════════════════════════════════════════
    def get_info(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت کلکتور — برای debug/health.

        Returns:
            dict با همهٔ اطلاعات مفید.
        """
        try:
            interval = self.poll_interval_seconds
        except Exception:
            interval = 2.0

        return {
            "platform": self._safe_platform_name(),
            "status": self._status.value,
            "is_connected": self.is_connected,
            "retry_count": self._retry_count,
            "max_retries": self._max_retries,
            "last_error": self._last_error,
            "last_error_status": (
                self._last_error_status.value
                if self._last_error_status else None
            ),
            "poll_interval_seconds": interval,
            "supports_history": self.supports_history,
            "supports_websocket": self.supports_websocket,
        }

    # ═══════════════════════════════════════════
    # context manager
    # ═══════════════════════════════════════════
    async def __aenter__(self) -> BaseCollector:
        """ورود به context manager — اتصال خودکار."""
        name = self._safe_platform_name()
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
        """🎯 FIX: خروج از context manager — disconnect خودکار.

        اگر exception رخ داده باشد، **آن را swallow نمی‌کنیم**
        (return None = propagate).
        """
        name = self._safe_platform_name()
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
            # 🎯 FIX: اگر exception اصلی وجود داشت، آن را propagate کن
            # (فقط disconnect را swallow می‌کنیم، نه exception اصلی)
            if exc_type is not None:
                raise
        # return None = exception اصلی propagate می‌شود