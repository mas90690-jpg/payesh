# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from enum import Enum
from datetime import datetime


class HandlerPriority(Enum):
    """اولویت هندلرها (هرچه عدد کمتر، اولویت بالاتر)"""
    CRITICAL = 0      # بحرانی (اول از همه اجرا شود)
    HIGH = 10         # بالا
    NORMAL = 20       # معمولی (پیش‌فرض)
    LOW = 30          # پایین
    BACKGROUND = 40   # پس‌زمینه (آخر از همه)


class BaseHandler(ABC):
    """
    کلاس پایه برای همه هندلرها (قابلیت‌ها)
    
    هر قابلیت جدید باید:
    1. از این کلاس ارث‌بری کند
    2. متدهای abstract را پیاده‌سازی کند
    3. در صورت نیاز، متدهای on_start/on_stop را override کند
    
    نحوه اضافه کردن هندلر جدید:
    1. فایل جدید در پوشه handlers/ بساز
    2. کلاس را از BaseHandler ارث‌بری کن
    3. متدهای required را پیاده‌سازی کن
    4. در config.py تنظیمات مربوطه را اضافه کن
    """
    
    def __init__(self):
        self._enabled = True
        self._stats = {
            "handled_count": 0,
            "error_count": 0,
            "last_handled": None,
            "avg_process_time": 0
        }
    
    @property
    @abstractmethod
    def handler_name(self) -> str:
        """نام قابلیت (برای لاگ و شناسایی)"""
        pass
    
    @property
    def priority(self) -> HandlerPriority:
        """
        اولویت هندلر (هرچه عدد کمتر، زودتر اجرا شود)
        پیش‌فرض: NORMAL
        """
        return HandlerPriority.NORMAL
    
    @property
    def enabled(self) -> bool:
        """آیا هندلر فعال است؟"""
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool):
        """فعال/غیرفعال کردن هندلر"""
        self._enabled = value
    
    @abstractmethod
    async def handle(self, event) -> Any:
        """
        پردازش رویداد
        
        Args:
            event: رویداد دریافتی (از EventBus)
            
        Returns:
            Any: نتیجه پردازش (اختیاری)
        """
        pass
    
    @abstractmethod
    def can_handle(self, event_name: str) -> bool:
        """
        آیا این هندلر می‌تواند این رویداد را پردازش کند؟
        
        Args:
            event_name: نام رویداد (مثلاً 'message.received')
            
        Returns:
            bool: True اگر می‌تواند پردازش کند
        """
        pass
    
    async def on_start(self) -> None:
        """
        هنگام شروع سیستم (قبل از شروع پردازش رویدادها)
        برای راه‌اندازی منابع، اتصال به دیتابیس و ...
        """
        pass
    
    async def on_stop(self) -> None:
        """
        هنگام توقف سیستم (بعد از آخرین رویداد)
        برای پاکسازی منابع، بستن اتصالات و ...
        """
        pass
    
    async def on_error(self, event, error: Exception) -> None:
        """
        هنگام خطا در پردازش رویداد
        """
        self._stats["error_count"] += 1
        print(f"⚠️ هندلر {self.handler_name} خطا: {error}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        دریافت آمار هندلر
        
        Returns:
            Dict: آمار شامل تعداد پردازش‌ها، خطاها و ...
        """
        return self._stats.copy()
    
    def _record_handled(self, process_time: float = 0) -> None:
        """ثبت یک پردازش موفق (داخلی)"""
        self._stats["handled_count"] += 1
        self._stats["last_handled"] = datetime.now()
        
        # به‌روزرسانی میانگین زمان پردازش
        old_avg = self._stats["avg_process_time"]
        count = self._stats["handled_count"]
        self._stats["avg_process_time"] = (old_avg * (count - 1) + process_time) / count
    
    def reset_stats(self) -> None:
        """بازنشانی آمار"""
        self._stats = {
            "handled_count": 0,
            "error_count": 0,
            "last_handled": None,
            "avg_process_time": 0
        }
    
    async def __call__(self, event) -> Any:
        """اجرای هندلر به عنوان تابع (برای سازگاری با EventBus)"""
        return await self.handle(event)