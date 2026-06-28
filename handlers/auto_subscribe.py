# -*- coding: utf-8 -*-
from core.base_handler import BaseHandler
from core.logger import log_info, log_success

class AutoSubscriber:
    """ثبت خودکار هندلرها در event_bus"""
    
    def __init__(self, event_bus, plugin_loader):
        self.event_bus = event_bus
        self.plugin_loader = plugin_loader
    
    async def register_all(self):
        """ثبت همه هندلرهای کشف شده"""
        handlers = self.plugin_loader.discover_handlers()
        
        for handler in handlers:
            # پیدا کردن رویدادهایی که این هندلر می‌تونه پردازش کنه
            # با یک روش هوشمند: یا از متد can_handle یا از annotation ها
            events = self._get_handler_events(handler)
            
            for event in events:
                self.event_bus.subscribe(event, handler.handle)
                log_success(f"هندلر {handler.handler_name} به رویداد {event} متصل شد")
    
    def _get_handler_events(self, handler) -> list:
        """پیدا کردن رویدادهایی که هندلر بهشون علاقه داره"""
        # روش ۱: استفاده از متد can_handle
        # روش ۲: استفاده از دکوریتور @subscribe
        # روش ۳: بررسی نام هندلر
        
        default_events = ["message.received", "lead.saved", "error.occurred"]
        
        # می‌تونیم از روی اسم هندلر تشخیص بدیم
        if "storage" in handler.handler_name.lower():
            return ["message.received"]
        elif "crm" in handler.handler_name.lower():
            return ["lead.saved"]
        elif "log" in handler.handler_name.lower():
            return ["error.occurred", "lead.saved"]
        
        return default_events