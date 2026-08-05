# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum


class ConnectionStatus(Enum):
    """وضعیت اتصال کلکتور"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class BaseCollector(ABC):
    """
    کلاس پایه برای همه پیام‌رسان‌ها

    برای اضافه کردن پیام‌رسان جدید:
    1. از این کلاس ارث‌بری کن
    2. تمام متدهای abstract را پیاده‌سازی کن
    3. در config.py تنظیمات مربوطه را اضافه کن
    """

    def __init__(self):
        self._status = ConnectionStatus.DISCONNECTED
        self._last_error: Optional[str] = None
        self._retry_count = 0
        self._max_retries = 3

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """اسم پلتفرم (مثلاً 'bale', 'telegram', 'rubika')"""
        pass

    @property
    def status(self) -> ConnectionStatus:
        """وضعیت فعلی اتصال"""
        return self._status

    @property
    def is_connected(self) -> bool:
        """آیا متصل است؟"""
        return self._status == ConnectionStatus.CONNECTED

    @property
    def supports_history(self) -> bool:
        """
        آیا این کلکتور از دریافت تاریخچه پشتیبانی می‌کند؟
        پیش‌فرض: False (در کلکتورهای واقعی override شود)
        """
        return False

    @property
    def supports_websocket(self) -> bool:
        """
        آیا این کلکتور از WebSocket (دریافت لحظه‌ای) پشتیبانی می‌کند؟
        پیش‌فرض: False
        """
        return False

    @property
    def poll_interval_seconds(self) -> float:
        """
        فاصلهٔ بین دو دورِ دریافت پیام (ثانیه).

        - کلکتورهای push-based (بله/روبیکا/تلگرام) فقط یک صفِ سبک را تخلیه می‌کنند،
          پس مقدار کوچک (پیش‌فرض ۲) مناسب است و هزینه‌ای ندارد.
        - اسکرپرهای سنگین مرورگری (ایتا/وب) باید مقدار بزرگ‌تری برگردانند تا
          CPU/RAM بیهوده مصرف نشود و پیام‌های قدیمی مدام از نو رصد نشوند.
        """
        return 2.0

    @abstractmethod
    async def connect(self) -> bool:
        """
        اتصال به پیام‌رسان

        Returns:
            bool: True اگر اتصال موفق بود، False اگر ناموفق بود
        """
        pass

    @abstractmethod
    async def fetch_messages(self, since: datetime) -> List[Dict[str, Any]]:
        """
        دریافت پیام‌های جدید از زمان since

        Args:
            since: زمان آخرین دریافت

        Returns:
            List[Dict]: لیست پیام‌ها با کلیدهای استاندارد:
                - id: شناسه پیام (اجباری)
                - text: متن پیام (اجباری)
                - chat_id: شناسه چت (اجباری)
                - chat_name: نام چت (اختیاری)
                - user_id: شناسه کاربر (اختیاری)
                - sender_name: نام فرستنده (اختیاری)
                - timestamp: زمان پیام (اجباری)
                - media_type: نوع رسانه (اختیاری) ['text', 'photo', 'video', 'audio', 'document']
                - file_id: شناسه فایل (اختیاری)
                - reply_to: پاسخ به کدام پیام (اختیاری)
        """
        pass

    async def fetch_history(self, since: datetime) -> List[Dict[str, Any]]:
        """
        خواندن پیام‌های عقب‌افتاده از زمان since تا حالا (catch-up).

        وقتی برنامه خاموش بوده و دوباره روشن می‌شود، این متد پیام‌هایی که در فاصله‌ی
        خاموشی آمده‌اند را می‌خواند. پیش‌فرض: خالی (در کلکتورهایی که پشتیبانی می‌کنند override شود).
        """
        return []

    @abstractmethod
    async def disconnect(self) -> None:
        """قطع اتصال از پیام‌رسان"""
        pass

    async def reconnect(self) -> bool:
        """
        تلاش برای اتصال مجدد

        Returns:
            bool: True اگر reconnect موفق بود
        """
        self._status = ConnectionStatus.RECONNECTING
        self._retry_count += 1

        if self._retry_count > self._max_retries:
            self._status = ConnectionStatus.ERROR
            self._last_error = "Max retries exceeded"
            return False

        await self.disconnect()
        return await self.connect()

    async def health_check(self) -> bool:
        """
        بررسی سلامت اتصال

        Returns:
            bool: True اگر اتصال سالم است
        """
        return self.is_connected

    def get_last_error(self) -> Optional[str]:
        """دریافت آخرین خطا"""
        return self._last_error

    def _set_error(self, error: str):
        """تنظیم خطا (داخلی)"""
        self._last_error = error
        self._status = ConnectionStatus.ERROR

    def _set_status(self, status: ConnectionStatus):
        """تنظیم وضعیت (داخلی)"""
        self._status = status

    def to_unified_format(self, raw_message: Any, platform: str) -> Dict[str, Any]:
        """
        تبدیل پیام خام به فرمت یکسان (قابل استفاده توسط کلکتورها)

        Args:
            raw_message: پیام خام از کتابخانه پلتفرم
            platform: نام پلتفرم

        Returns:
            Dict: پیام در فرمت یکسان
        """
        return {
            "platform": platform,
            "raw_data": raw_message if isinstance(raw_message, dict) else {},
        }

    async def __aenter__(self):
        """Context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.disconnect()
