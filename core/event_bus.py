# -*- coding: utf-8 -*-
import asyncio
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from core.logger import log_info, log_success, log_warning, log_error


class EventPriority(Enum):
    """اولویت رویداد"""
    HIGH = 0      # بالا (اول اجرا شود)
    NORMAL = 1    # معمولی
    LOW = 2       # پایین (آخر اجرا شود)


@dataclass
class Event:
    """کلاس رویداد"""
    name: str
    data: Any
    timestamp: datetime = None
    priority: EventPriority = EventPriority.NORMAL
    source: Optional[str] = None
    id: str = field(default_factory=lambda: f"{datetime.now().timestamp()}")
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
    
    def __repr__(self):
        return f"Event(name={self.name}, timestamp={self.timestamp}, source={self.source})"


class EventBus:
    """
    سیستم مدیریت رویدادها (Event Bus)
    
    قابلیت‌ها:
    - ثبت گوش‌دهنده برای رویدادهای خاص
    - انتشار رویداد با اولویت
    - پشتیبانی از هندلرهای همزمان و ناهمزمان
    - آمارگیری از رویدادها
    """
    
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._handler_priorities: Dict[str, Dict[Callable, EventPriority]] = {}
        self._event_history: List[Event] = []
        self._max_history = 1000
        self._stats = {
            "total_events": 0,
            "total_handlers_called": 0,
            "errors": 0,
            "events_by_name": {}
        }
        log_info("✅ سیستم رویداد راه‌اندازی شد")
    
    def subscribe(self, event_name: str, handler: Callable, priority: EventPriority = EventPriority.NORMAL):
        """
        ثبت گوش‌دهنده برای یک رویداد
        
        Args:
            event_name: نام رویداد (مثلاً 'message.received')
            handler: تابع یا async function که رویداد را پردازش می‌کند
            priority: اولویت اجرا (HIGH, NORMAL, LOW)
        """
        if event_name not in self._handlers:
            self._handlers[event_name] = []
            self._handler_priorities[event_name] = {}
        
        self._handlers[event_name].append(handler)
        self._handler_priorities[event_name][handler] = priority
        
        # مرتب‌سازی هندلرها بر اساس اولویت
        self._handlers[event_name].sort(
            key=lambda h: self._handler_priorities[event_name].get(h, EventPriority.NORMAL).value
        )
        
        log_success(f"📢 گوش‌دهنده ثبت شد: {event_name} -> {handler.__name__} (اولویت: {priority.name})")
    
    def unsubscribe(self, event_name: str, handler: Callable) -> bool:
        """
        حذف گوش‌دهنده از یک رویداد
        
        Returns:
            bool: True اگر حذف شد، False اگر وجود نداشت
        """
        if event_name in self._handlers:
            if handler in self._handlers[event_name]:
                self._handlers[event_name].remove(handler)
                if handler in self._handler_priorities.get(event_name, {}):
                    del self._handler_priorities[event_name][handler]
                log_info(f"📢 گوش‌دهنده حذف شد: {event_name} -> {handler.__name__}")
                return True
        return False
    
    async def emit(self, event_name: str, data: Any, source: str = None, priority: EventPriority = EventPriority.NORMAL):
        """
        انتشار رویداد
        
        Args:
            event_name: نام رویداد
            data: داده‌های رویداد
            source: منبع رویداد (اختیاری)
            priority: اولویت رویداد
        """
        event = Event(name=event_name, data=data, priority=priority, source=source)
        
        # به‌روزرسانی آمار
        self._stats["total_events"] += 1
        self._stats["events_by_name"][event_name] = self._stats["events_by_name"].get(event_name, 0) + 1
        
        # ذخیره در تاریخچه
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
        
        log_info(f"📡 رویداد منتشر شد: {event_name} (اولویت: {priority.name})")
        
        if event_name not in self._handlers:
            return
        
        # اجرای هندلرها
        for handler in self._handlers[event_name]:
            try:
                start_time = datetime.now()
                
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
                
                elapsed = (datetime.now() - start_time).total_seconds()
                self._stats["total_handlers_called"] += 1
                
                if elapsed > 1.0:
                    log_warning(f"⏱️ هندلر {handler.__name__} برای رویداد {event_name} {elapsed:.2f} ثانیه طول کشید")
                    
            except Exception as e:
                self._stats["errors"] += 1
                log_error(f"❌ خطا در هندلر {handler.__name__} برای رویداد {event_name}: {e}")
    
    async def emit_async(self, event_name: str, data: Any, source: str = None):
        """انتشار رویداد بدون اولویت (alias برای emit)"""
        await self.emit(event_name, data, source)
    
    def emit_sync(self, event_name: str, data: Any, source: str = None):
        """
        انتشار رویداد به صورت همزمان (برای استفاده در کدهای غیر async)
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.emit(event_name, data, source))
        finally:
            loop.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """دریافت آمار رویدادها"""
        return {
            **self._stats,
            "active_handlers": {
                event: len(handlers) for event, handlers in self._handlers.items()
            },
            "recent_events": [
                {"name": e.name, "timestamp": e.timestamp.isoformat(), "source": e.source}
                for e in self._event_history[-10:]
            ]
        }
    
    def get_event_history(self, limit: int = 50) -> List[Event]:
        """دریافت تاریخچه رویدادها"""
        return self._event_history[-limit:]
    
    def clear_history(self):
        """پاک کردن تاریخچه رویدادها"""
        self._event_history.clear()
        log_info("تاریخچه رویدادها پاک شد")
    
    def has_handler(self, event_name: str, handler: Callable = None) -> bool:
        """
        بررسی وجود گوش‌دهنده
        
        Args:
            event_name: نام رویداد
            handler: اگر مشخص شود، وجود آن هندلر خاص را بررسی می‌کند
        """
        if event_name not in self._handlers:
            return False
        if handler:
            return handler in self._handlers[event_name]
        return len(self._handlers[event_name]) > 0
    
    def remove_all_handlers(self, event_name: str = None):
        """
        حذف همه هندلرها
        
        Args:
            event_name: اگر مشخص شود، فقط هندلرهای آن رویداد حذف می‌شوند
        """
        if event_name:
            self._handlers[event_name] = []
            self._handler_priorities[event_name] = {}
            log_info(f"همه هندلرهای رویداد {event_name} حذف شدند")
        else:
            self._handlers.clear()
            self._handler_priorities.clear()
            log_info("همه هندلرها حذف شدند")
    
    async def wait_for(self, event_name: str, timeout: float = None, predicate: Callable = None):
        """
        انتظار برای یک رویداد خاص (یکبار مصرف)
        
        Args:
            event_name: نام رویداد
            timeout: حداکثر زمان انتظار (ثانیه)
            predicate: تابع شرط (event) -> bool
            
        Returns:
            Event: رویداد دریافتی یا None در صورت timeout
        """
        future = asyncio.Future()
        
        def handler(event):
            if predicate is None or predicate(event):
                future.set_result(event)
        
        self.subscribe(event_name, handler, priority=EventPriority.HIGH)
        
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.unsubscribe(event_name, handler)


# برای سازگاری با کدهای قدیمی
EventBusOld = EventBus