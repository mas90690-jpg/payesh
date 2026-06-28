# -*- coding: utf-8 -*-
import asyncio
from datetime import datetime, timedelta
from typing import List
from core.base_collector import BaseCollector
from core.event_bus import EventBus
from core.database import create_database, BaseDatabase
from core.logger import log_info, log_success, log_error, log_warning
from config import SYSTEM_CONFIG

class Orchestrator:
    
    def __init__(self, collectors: List[BaseCollector], event_bus: EventBus, db: BaseDatabase):
        self.collectors = collectors
        self.event_bus = event_bus
        self.db = db
        self.running = False
        self.poll_interval_seconds = SYSTEM_CONFIG.get("poll_interval_hours", 24) * 3600

    async def start(self):
        self.running = True
        log_info("ارکستریتور شروع به کار کرد")

        for collector in self.collectors:
            await self._initial_fetch(collector)

        await self._generate_report()

        log_info("🔄 سیستم در حالت انتظار پیام‌های جدید است...")
        
        # یک تسک برای هر کلکتور؛ فاصلهٔ هر کلکتور از خودش خوانده می‌شود
        # (بدون if روی نام پلتفرم). push-basedها ۲ ثانیه، اسکرپرها فاصلهٔ بزرگ.
        tasks = []
        for collector in self.collectors:
            interval = getattr(collector, "poll_interval_seconds", 2)
            log_info(f"⏱️ {collector.platform_name}: هر {interval:g} ثانیه")
            tasks.append(asyncio.create_task(self._poll_messages(collector, interval=interval)))

        # منتظر اتمام (که هیچ‌وقت نمی‌شود مگر stop صدا زده شود)
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def _poll_messages(self, collector: BaseCollector, interval: float):
        """دریافت دوره‌ای پیام‌ها از هر کلکتور (عمومی برای همه)"""
        while self.running:
            try:
                await self._process_messages(collector)
            except Exception as e:
                log_error(f"خطا در پولینگ {collector.platform_name}: {e}")
            await asyncio.sleep(interval)

    async def _initial_fetch(self, collector: BaseCollector):
        platform = collector.platform_name
        log_info(f"شروع پردازش اولیه {platform}")

        try:
            last_poll = await self.db.get_last_poll(platform)
            if not last_poll:
                last_poll = datetime.now() - timedelta(hours=24)
                log_info(f"اولین اجرا برای {platform} - از ۲۴ ساعت قبل")

            # ۱) اتصال
            if not await collector.connect():
                log_error(f"❌ عدم امکان اتصال به {platform}")
                return
            log_success(f"✅ اتصال به {platform} برقرار شد")

            # ۲) خواندن پیام‌های عقب‌افتاده (از آخرین باری که روشن بوده تا حالا)
            try:
                history = await collector.fetch_history(last_poll)
            except Exception as e:
                history = []
                log_error(f"خطا در خواندن پیام‌های عقب‌افتاده {platform}: {e}")

            if history:
                log_success(f"📚 {len(history)} پیام عقب‌افتاده از {platform} (از {last_poll:%Y-%m-%d %H:%M})")
                for msg in history:
                    msg["platform"] = platform
                    await self.event_bus.emit("message.received", msg)

            # ۳) ثبت زمان این اجرا (تا دفعهٔ بعد از همین‌جا ادامه دهد)
            await self.db.update_last_poll(platform, datetime.now())

        except Exception as e:
            log_error(f"خطا در پردازش اولیه {platform}: {e}")

    async def _process_messages(self, collector: BaseCollector):
        """دریافت پیام‌های جدید از کلکتور و انتشار رویداد"""
        platform = collector.platform_name
        try:
            # زمان آخرین پول را از دیتابیس می‌خوانیم (برای کلکتورهای pull-based)
            last_poll = await self.db.get_last_poll(platform)
            if not last_poll:
                last_poll = datetime.now() - timedelta(hours=24)
            
            messages = await collector.fetch_messages(last_poll)
            if messages:
                log_success(f"{len(messages)} پیام جدید از {platform}")
                for msg in messages:
                    msg["platform"] = platform
                    await self.event_bus.emit("message.received", msg)
                # به‌روزرسانی آخرین زمان پول (برای pull-basedها مفید است)
                await self.db.update_last_poll(platform, datetime.now())
        except Exception as e:
            log_error(f"خطا در دریافت پیام‌های جدید {platform}: {e}")

    # برای سازگاری با کد قبلی
    async def _fetch_new_messages(self, collector: BaseCollector):
        await self._process_messages(collector)

    async def _generate_report(self):
        print("\n" + "="*60)
        print("📊 گزارش اولیه")
        print("="*60)
        log_success("گزارش تولید شد")

    async def stop(self):
        self.running = False
        for collector in self.collectors:
            await collector.disconnect()
        log_info("ارکستریتور متوقف شد")