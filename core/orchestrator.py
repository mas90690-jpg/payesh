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

    def __init__(
        self, collectors: List[BaseCollector], event_bus: EventBus, db: BaseDatabase
    ):
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

        await self._greet_admins()  # بعد از هر اجرا/ری‌استارت، به مدیر سلام کن
        await self._generate_report()

        log_info("🔄 سیستم در حالت انتظار پیام‌های جدید است...")

        # یک تسک برای هر کلکتور؛ فاصلهٔ هر کلکتور از خودش خوانده می‌شود
        # (بدون if روی نام پلتفرم). push-basedها ۲ ثانیه، اسکرپرها فاصلهٔ بزرگ.
        tasks = []
        for collector in self.collectors:
            interval = getattr(collector, "poll_interval_seconds", 2)
            log_info(f"⏱️ {collector.platform_name}: هر {interval:g} ثانیه")
            tasks.append(
                asyncio.create_task(self._poll_messages(collector, interval=interval))
            )

        # منتظر اتمام (که هیچ‌وقت نمی‌شود مگر stop صدا زده شود)
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def _poll_messages(self, collector: BaseCollector, interval: float):
        """دریافت دوره‌ای پیام‌ها از هر کلکتور، با نظارت و اتصالِ‌مجددِ خودکار.

        اگر اتصالی افتاد (قطعی شبکه، بسته‌شدن مرورگر، انقضای نشست)، خودکار با backoff
        دوباره وصل می‌شود و پیام‌های ازدست‌رفتهٔ همان بازه را هم می‌خواند (catch-up).
        """
        fails = 0
        while self.running:
            try:
                if not collector.is_connected:
                    await self._supervise_reconnect(collector)
                    if not collector.is_connected:
                        # هنوز وصل نشد؛ کمی صبر کن و دوباره امتحان (نه هر interval کوتاه)
                        await asyncio.sleep(min(300, max(interval, 30)))
                        continue
                await self._process_messages(collector)
                fails = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                fails += 1
                log_error(f"خطا در پولینگ {collector.platform_name} (#{fails}): {e}")
                if fails >= 3:
                    log_warning(f"♻️ {collector.platform_name}: تلاش برای اتصال مجدد...")
                    await self._supervise_reconnect(collector)
                    fails = 0
            await asyncio.sleep(interval)

    async def _supervise_reconnect(self, collector: BaseCollector):
        """قطع و اتصالِ مجددِ یک کلکتور با backoff نمایی + catch-up پیام‌های ازدست‌رفته."""
        platform = collector.platform_name
        delay = 5
        for attempt in range(1, 4):
            if not self.running:
                return
            try:
                try:
                    await collector.disconnect()
                except Exception:
                    pass
                if await collector.connect():
                    log_success(f"✅ اتصال مجددِ {platform} برقرار شد")
                    await self._catch_up(collector)
                    return
            except Exception as e:
                log_error(f"اتصال مجددِ {platform} ناموفق (تلاش {attempt}): {e}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
        log_error(
            f"❌ {platform} بعد از چند تلاش وصل نشد؛ در دورِ بعد دوباره امتحان می‌شود"
        )

    async def _catch_up(self, collector: BaseCollector):
        """بعد از وصلِ مجدد، پیام‌های ازدست‌رفته از آخرین last_poll را می‌خواند."""
        platform = collector.platform_name
        try:
            last_poll = await self.db.get_last_poll(platform) or (
                datetime.now() - timedelta(hours=24)
            )
            history = await collector.fetch_history(last_poll)
            if history:
                log_success(f"📚 catch-up: {len(history)} پیام ازدست‌رفتهٔ {platform}")
                for msg in history:
                    msg["platform"] = platform
                    await self.event_bus.emit("message.received", msg)
            await self.db.update_last_poll(platform, datetime.now())
        except Exception as e:
            log_warning(f"catch-up {platform} ناقص بود: {e}")

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
                log_success(
                    f"📚 {len(history)} پیام عقب‌افتاده از {platform} (از {last_poll:%Y-%m-%d %H:%M})"
                )
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
            # ضربانِ زنده: هر دورِ موفق last_poll را به‌روز کن (حتی بدون پیام) تا
            # پلتفرمِ ساکتِ ولی سالم، اشتباهی «گیرکرده» تشخیص داده نشود.
            await self.db.update_last_poll(platform, datetime.now())
        except Exception as e:
            log_error(f"خطا در دریافت پیام‌های جدید {platform}: {e}")

    # برای سازگاری با کد قبلی
    async def _fetch_new_messages(self, collector: BaseCollector):
        await self._process_messages(collector)

    async def _greet_admins(self):
        """فوراً به مدیر سلام می‌کند (بدون انتظار برای هوش)، و مدل را در پس‌زمینه گرم می‌کند
        تا اولین جوابِ مدیر سریع باشد. مهم: startup را بلاک نمی‌کند."""
        from config import PLUGINS_CONFIG

        if not PLUGINS_CONFIG.get("adminhandler", {}).get("enabled", True):
            return  # مغز (هوش/مدیر) خاموش است → سلام/گرم‌کردن هم نه
        from core import messenger_registry

        text = (
            "🤖 سلام مدیر! ربات بالا آمد و آماده‌ام. هر سؤالی داری همین‌جا بپرس "
            "(وضعیت، گزارش، سلامت، افزودن کلیدواژه، یا هر چیزِ دیگر)."
        )
        ok = await messenger_registry.notify_admins(text)
        log_info(
            f"👋 سلامِ راه‌اندازی به مدیر {'ارسال شد' if ok else 'ارسال نشد (مدیر/فرستنده آماده نیست)'}"
        )
        asyncio.create_task(self._warm_ai())  # گرم‌کردنِ مدل در پس‌زمینه

    async def _warm_ai(self):
        try:
            from core.ai_assistant import get_ai_assistant

            ai = get_ai_assistant()
            if await ai.available():
                await ai.chat(
                    "سلام"
                )  # مدل را لود می‌کند؛ با keep_alive بالا، لود می‌ماند
                log_info("🔥 مدلِ هوش گرم شد (آمادهٔ جوابِ سریع)")
        except Exception:
            pass

    async def _generate_report(self):
        print("\n" + "=" * 60)
        print("📊 گزارش اولیه")
        print("=" * 60)
        log_success("گزارش تولید شد")

    async def stop(self):
        self.running = False
        for collector in self.collectors:
            await collector.disconnect()
        log_info("ارکستریتور متوقف شد")
