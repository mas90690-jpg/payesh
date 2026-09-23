"""
Orchestrator — هماهنگ‌کنندهٔ کلکتورها، پولینگ و مدیریت اتصال.

این فایل جایگاه مرکزی برای monitoring همهٔ collectors است:
  - به هر collector یک task اختصاصی می‌ده
  - interval هر collector را از خودش می‌پرسد (poll_interval_seconds)
  - در صورت قطع شدن، با backoff نمایی دوباره وصل می‌کند
  - بعد از وصل، پیام‌های ازدست‌رفته را از last_poll می‌خواند (catch-up)
  - هر دور موفق، last_poll را به‌روز می‌کند (ضربان زنده)

نسخهٔ ۳ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [ORCH:...]:
      * init: تعداد collectors + interval هر کدام
      * start: per-collector startup + زمان کل
      * poll iteration: زمان اجرا + تعداد پیام + فاصلهٔ بعدی
      * reconnect: تلاش N + delay + دلیل خطا
      * catch-up: از چه زمانی + تعداد پیام
      * initial_fetch: زمان هر collector
  - 🐛 FIX: _supervise_reconnect دیگر خطا را بی‌صدا swallow نمی‌کند
  - 🐛 FIX: _warm_ai خطا را لاگ می‌کند (قبلاً pass)
  - 🧹 stop() حالا per-collector لاگ دارد
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

from config import SYSTEM_CONFIG
from core.contracts.base_collector import BaseCollector
from core.database import BaseDatabase
from core.kernel.event_bus import EventBus
from core.kernel.logger import log_error, log_info, log_success, log_warning

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_POLL_MS = 30_000      # هر poll > 30s
_SLOW_INITIAL_MS = 20_000   # initial_fetch > 20s
_LOG_PREVIEW_LEN = 80


class Orchestrator:
    """هماهنگ‌کنندهٔ کلکتورها و حلقهٔ پولینگ."""

    def __init__(
        self, collectors: list[BaseCollector], event_bus: EventBus, db: BaseDatabase
    ):
        self.collectors = collectors
        self.event_bus = event_bus
        self.db = db
        self.running = False
        self.poll_interval_seconds = (
            SYSTEM_CONFIG.get("poll_interval_hours", 24) * 3600
        )

        # لیست intervalهای هر collector برای لاگ init
        intervals_info = []
        for c in collectors:
            try:
                iv = getattr(c, "poll_interval_seconds", 2)
                intervals_info.append(f"{c.platform_name}={iv:g}s")
            except Exception:
                intervals_info.append(f"{getattr(c, 'platform_name', '?')}=?s")

        log_info(
            f"🎛️ [ORCH:init] آماده | collectors={len(collectors)} | "
            f"intervals=[{', '.join(intervals_info)}]"
        )

    # ═══════════════════════════════════════════
    # start
    # ═══════════════════════════════════════════
    async def start(self):
        """شروع: initial_fetch سری برای همه، سپس یک task per collector."""
        self.running = True
        t_all = time.monotonic()
        log_info(
            f"🚀 [ORCH:start] شروع | collectors={len(self.collectors)}"
        )

        # ۱) پردازش اولیه (سری، برای جلوگیری از فشار منابع)
        for i, collector in enumerate(self.collectors, 1):
            log_info(
                f"▶️ [ORCH:start] initial_fetch {i}/{len(self.collectors)} | "
                f"{collector.platform_name}"
            )
            try:
                await self._initial_fetch(collector)
            except Exception as e:
                log_error(
                    f"❌ [ORCH:start] initial_fetch {collector.platform_name} | "
                    f"{type(e).__name__}: {e}"
                )

        # ۲) سلام به مدیر + گرم‌کردن AI
        await self._greet_admins()
        await self._generate_report()

        # ۳) راه‌اندازی تسک‌ها
        tasks = []
        for collector in self.collectors:
            interval = getattr(collector, "poll_interval_seconds", 2)
            log_info(
                f"🧵 [ORCH:start] task | {collector.platform_name} | "
                f"interval={interval:g}s"
            )
            tasks.append(
                asyncio.create_task(
                    self._poll_messages(collector, interval=interval),
                    name=f"poll_{collector.platform_name}",
                )
            )

        startup_s = time.monotonic() - t_all
        log_success(
            f"✅ [ORCH:start] همهٔ تسک‌ها راه افتادند | "
            f"tasks={len(tasks)} | startup={startup_s:.1f}s"
        )
        log_info("🔄 [ORCH:start] سیستم در حالت انتظار پیام‌های جدید...")

        # ۴) انتظار (تا stop())
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log_info("🛑 [ORCH:start] تسک‌ها cancel شدند")

    # ═══════════════════════════════════════════
    # حلقهٔ پولینگ هر کلکتور
    # ═══════════════════════════════════════════
    async def _poll_messages(self, collector: BaseCollector, interval: float):
        """حلقهٔ poll یک collector با reconnect و timing.

        backoff اتصال-نشدن:
          wait = min(120, max(interval * 3, 15))
        """
        platform = collector.platform_name
        fails = 0
        iteration = 0

        while self.running:
            iteration += 1
            t0 = time.monotonic()

            try:
                # چک اتصال
                if not collector.is_connected:
                    log_warning(
                        f"🔌 [ORCH:poll:{platform}] قطع است (iter#{iteration}) — "
                        f"شروع reconnect"
                    )
                    await self._supervise_reconnect(collector)
                    if not collector.is_connected:
                        wait = min(120, max(interval * 3, 15))
                        log_warning(
                            f"⏳ [ORCH:poll:{platform}] هنوز وصل نیست | "
                            f"خواب {wait:.0f}s (iter#{iteration})"
                        )
                        await asyncio.sleep(wait)
                        continue

                # پردازش
                await self._process_messages(collector)
                fails = 0

                elapsed_ms = (time.monotonic() - t0) * 1000
                if elapsed_ms > _SLOW_POLL_MS:
                    log_warning(
                        f"🐌 [ORCH:poll:{platform}] کند | {elapsed_ms:.0f}ms "
                        f"(iter#{iteration})"
                    )
                else:
                    log_info(
                        f"✔️ [ORCH:poll:{platform}] iter#{iteration} | "
                        f"{elapsed_ms:.0f}ms | next={interval:g}s"
                    )

            except asyncio.CancelledError:
                log_info(
                    f"🛑 [ORCH:poll:{platform}] cancel (iter#{iteration})"
                )
                raise
            except Exception as e:
                fails += 1
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_error(
                    f"❌ [ORCH:poll:{platform}] iter#{iteration} | "
                    f"fails={fails} | {elapsed_ms:.0f}ms | "
                    f"{type(e).__name__}: {e}"
                )
                if fails >= 3:
                    log_warning(
                        f"♻️ [ORCH:poll:{platform}] {fails} خطا → "
                        f"reconnect اجباری"
                    )
                    try:
                        await self._supervise_reconnect(collector)
                    except Exception as e2:
                        log_error(
                            f"❌ [ORCH:poll:{platform}] reconnect اجباری | "
                            f"{type(e2).__name__}: {e2}"
                        )
                    fails = 0

            await asyncio.sleep(interval)

    # ═══════════════════════════════════════════
    # reconnect
    # ═══════════════════════════════════════════
    async def _supervise_reconnect(self, collector: BaseCollector):
        """قطع و اتصالِ مجدد با backoff نمایی + catch-up.

        تأخیر: ۵ → ۱۰ → ۲۰ ثانیه (cap 60s).
        """
        platform = collector.platform_name
        delay = 5
        t0 = time.monotonic()
        log_info(
            f"🔁 [ORCH:reconnect:{platform}] شروع | max_attempts=3 | "
            f"delays=[5,10,20]s"
        )

        for attempt in range(1, 4):
            if not self.running:
                log_info(
                    f"🛑 [ORCH:reconnect:{platform}] running=False — توقف"
                )
                return

            # قطع قبلی (اگر هست)
            try:
                await collector.disconnect()
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:reconnect:{platform}] disconnect | "
                    f"{type(e).__name__}: {e}"
                )

            # تلاش اتصال
            try:
                ok = await collector.connect()
                if ok:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    log_success(
                        f"✅ [ORCH:reconnect:{platform}] وصل شد | "
                        f"attempt={attempt} | {elapsed_ms:.0f}ms"
                    )
                    try:
                        await self._catch_up(collector)
                    except Exception as e:
                        log_error(
                            f"❌ [ORCH:reconnect:{platform}] catch-up | "
                            f"{type(e).__name__}: {e}"
                        )
                    return
                else:
                    log_warning(
                        f"⚠️ [ORCH:reconnect:{platform}] attempt {attempt} "
                        f"connect()=False"
                    )
            except Exception as e:
                log_error(
                    f"❌ [ORCH:reconnect:{platform}] attempt {attempt} | "
                    f"{type(e).__name__}: {e}"
                )

            # صبر قبل از تلاش بعدی (به‌جز آخرین تلاش)
            if attempt < 3:
                log_info(
                    f"💤 [ORCH:reconnect:{platform}] صبر {delay}s "
                    f"(attempt {attempt}→{attempt+1})"
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_error(
            f"❌ [ORCH:reconnect:{platform}] پس از ۳ تلاش وصل نشد | "
            f"{elapsed_ms:.0f}ms | در دورِ بعد دوباره امتحان می‌شود"
        )

    # ═══════════════════════════════════════════
    # catch-up
    # ═══════════════════════════════════════════
    async def _catch_up(self, collector: BaseCollector):
        """بعد از وصلِ مجدد، پیام‌های ازدست‌رفته را از last_poll می‌خواند."""
        platform = collector.platform_name
        t0 = time.monotonic()

        try:
            db_last = await self.db.get_last_poll(platform)
            if db_last:
                last_poll = db_last
                source = "db"
            else:
                last_poll = datetime.now() - timedelta(hours=24)
                source = "default(-24h)"

            log_info(
                f"📚 [ORCH:catchup:{platform}] شروع | "
                f"from={last_poll:%Y-%m-%d %H:%M} | source={source}"
            )

            history = await collector.fetch_history(last_poll)

            if history:
                log_success(
                    f"✅ [ORCH:catchup:{platform}] {len(history)} پیام ازدست‌رفته"
                )
                for msg in history:
                    msg["platform"] = platform
                    try:
                        await self.event_bus.emit("message.received", msg)
                    except Exception as e:
                        log_warning(
                            f"⚠️ [ORCH:catchup:{platform}] emit | "
                            f"{type(e).__name__}: {e}"
                        )
            else:
                log_info(
                    f"ℹ️ [ORCH:catchup:{platform}] پیام ازدست‌رفته‌ای نبود"
                )

            await self.db.update_last_poll(platform, datetime.now())

            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"✅ [ORCH:catchup:{platform}] کامل | "
                f"history={len(history) if history else 0} | {elapsed_ms:.0f}ms"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_warning(
                f"⚠️ [ORCH:catchup:{platform}] ناقص | "
                f"{elapsed_ms:.0f}ms | {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # initial_fetch
    # ═══════════════════════════════════════════
    async def _initial_fetch(self, collector: BaseCollector):
        """اتصال اولیه + catch-up."""
        platform = collector.platform_name
        t0 = time.monotonic()
        log_info(f"🎬 [ORCH:init:{platform}] شروع پردازش اولیه")

        try:
            db_last = await self.db.get_last_poll(platform)
            if not db_last:
                last_poll = datetime.now() - timedelta(hours=24)
                src = "default(-24h)"
                log_info(
                    f"ℹ️ [ORCH:init:{platform}] اولین اجرا — از ۲۴ ساعت قبل"
                )
            else:
                last_poll = db_last
                src = "db"
                log_info(
                    f"ℹ️ [ORCH:init:{platform}] ادامه از "
                    f"{last_poll:%Y-%m-%d %H:%M} ({src})"
                )

            # ۱) اتصال
            t_conn = time.monotonic()
            connected = await collector.connect()
            conn_ms = (time.monotonic() - t_conn) * 1000

            if not connected:
                log_error(
                    f"❌ [ORCH:init:{platform}] connect()=False | "
                    f"{conn_ms:.0f}ms"
                )
                return
            log_success(
                f"✅ [ORCH:init:{platform}] متصل | {conn_ms:.0f}ms"
            )

            # ۲) catch-up
            try:
                history = await collector.fetch_history(last_poll)
            except Exception as e:
                history = []
                log_error(
                    f"❌ [ORCH:init:{platform}] fetch_history | "
                    f"{type(e).__name__}: {e}"
                )

            if history:
                log_success(
                    f"📚 [ORCH:init:{platform}] {len(history)} پیام عقب‌افتاده "
                    f"(از {last_poll:%Y-%m-%d %H:%M})"
                )
                for msg in history:
                    msg["platform"] = platform
                    try:
                        await self.event_bus.emit("message.received", msg)
                    except Exception as e:
                        log_warning(
                            f"⚠️ [ORCH:init:{platform}] emit | "
                            f"{type(e).__name__}: {e}"
                        )

            # ۳) به‌روزرسانی last_poll
            await self.db.update_last_poll(platform, datetime.now())

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_INITIAL_MS:
                log_warning(
                    f"🐌 [ORCH:init:{platform}] کند | {elapsed_ms:.0f}ms"
                )
            else:
                log_success(
                    f"✅ [ORCH:init:{platform}] کامل | "
                    f"history={len(history) if history else 0} | "
                    f"{elapsed_ms:.0f}ms"
                )

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [ORCH:init:{platform}] {elapsed_ms:.0f}ms | "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # process_messages
    # ═══════════════════════════════════════════
    async def _process_messages(self, collector: BaseCollector):
        """دریافت پیام‌های جدید + انتشار رویداد + ضربان last_poll."""
        platform = collector.platform_name
        try:
            last_poll = await self.db.get_last_poll(platform)
            if not last_poll:
                last_poll = datetime.now() - timedelta(hours=24)

            t0 = time.monotonic()
            messages = await collector.fetch_messages(last_poll)
            fetch_ms = (time.monotonic() - t0) * 1000

            if messages:
                log_success(
                    f"📥 [ORCH:msg:{platform}] {len(messages)} پیام جدید | "
                    f"fetch={fetch_ms:.0f}ms"
                )
                emit_errors = 0
                for msg in messages:
                    msg["platform"] = platform
                    try:
                        await self.event_bus.emit("message.received", msg)
                    except Exception as e:
                        emit_errors += 1
                        if emit_errors <= 2:
                            log_warning(
                                f"⚠️ [ORCH:msg:{platform}] emit | "
                                f"{type(e).__name__}: {e}"
                            )
                if emit_errors:
                    log_warning(
                        f"⚠️ [ORCH:msg:{platform}] {emit_errors} emit ناموفق "
                        f"از {len(messages)}"
                    )
            else:
                # حتی وقتی پیامی نیست، یک log سبک می‌زنیم
                # (سطح info — چون هر poll اتفاق می‌افته و نباید نویز بشه)
                log_info(
                    f"ℹ️ [ORCH:msg:{platform}] بدون پیام جدید | "
                    f"fetch={fetch_ms:.0f}ms"
                )

            # ضربان زنده: هر poll موفق، last_poll را به‌روز می‌کنیم
            await self.db.update_last_poll(platform, datetime.now())
        except Exception as e:
            log_error(
                f"❌ [ORCH:msg:{platform}] {type(e).__name__}: {e}"
            )
            raise  # به _poll_messages می‌ذاریم تا fails++ کنه

    # برای سازگاری با کد قدیمی
    async def _fetch_new_messages(self, collector: BaseCollector):
        await self._process_messages(collector)

    # ═══════════════════════════════════════════
    # greet + warm AI
    # ═══════════════════════════════════════════
    async def _greet_admins(self):
        """سلام به مدیر + گرم‌کردن AI در پس‌زمینه (غیر-blocking)."""
        from config import PLUGINS_CONFIG

        if not PLUGINS_CONFIG.get("adminhandler", {}).get("enabled", True):
            log_info("ℹ️ [ORCH:greet] adminhandler غیرفعال — رد شد")
            return

        from core import messenger_registry

        text = (
            "🤖 سلام مدیر! ربات بالا آمد و آماده‌ام. هر سؤالی داری همین‌جا بپرس "
            "(وضعیت، گزارش، سلامت، افزودن کلیدواژه، یا هر چیزِ دیگر)."
        )
        t0 = time.monotonic()
        try:
            ok = await messenger_registry.notify_admins(text)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if ok:
                log_success(
                    f"👋 [ORCH:greet] سلام ارسال شد | {elapsed_ms:.0f}ms"
                )
            else:
                log_warning(
                    f"⚠️ [ORCH:greet] ارسال نشد (مدیر/فرستنده آماده نیست) | "
                    f"{elapsed_ms:.0f}ms"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:greet] {type(e).__name__}: {e}"
            )

        # گرم‌کردن AI در پس‌زمینه (fire-and-forget)
        asyncio.create_task(self._warm_ai())

    async def _warm_ai(self):
        """گرم‌کردن مدل AI تا اولین جواب سریع باشه."""
        t0 = time.monotonic()
        try:
            from core.services.ai_assistant import get_ai_assistant
            ai = get_ai_assistant()
            if not await ai.available():
                log_info("ℹ️ [ORCH:warm] AI در دسترس نیست — رد شد")
                return

            ans = await ai.chat("سلام")
            elapsed_ms = (time.monotonic() - t0) * 1000

            # 🐛 FIX: chat() در صورت شکست، پیام خطای متنی برمی‌گردونه
            # (نه None) → باید چک کنیم که واقعاً جواب داده یا نه
            if not ans or "در دسترس نیست" in ans or ans.startswith("⚠️"):
                log_warning(
                    f"⚠️ [ORCH:warm] AI جواب نداد | "
                    f"{elapsed_ms:.0f}ms | ans={str(ans)[:60]!r}"
                )
                return

            log_success(
                f"🔥 [ORCH:warm] مدل گرم شد | {elapsed_ms:.0f}ms"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:warm] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # گزارش اولیه
    # ═══════════════════════════════════════════
    async def _generate_report(self):
        log_info("📊 [ORCH:report] تولید گزارش اولیه...")
        try:
            total = await self.db.get_total_count()
            log_success(
                f"✅ [ORCH:report] "
                f"پیام={total.get('messages', 0)} "
                f"سرنخ={total.get('leads', 0)} "
                f"گروه={total.get('groups', 0)}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:report] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # stop
    # ═══════════════════════════════════════════
    async def stop(self):
        """توقف کامل — disconnect همه collectorها."""
        self.running = False
        log_info(
            f"🛑 [ORCH:stop] شروع | collectors={len(self.collectors)}"
        )
        t0 = time.monotonic()

        for i, collector in enumerate(self.collectors, 1):
            name = getattr(collector, "platform_name", "?")
            try:
                await collector.disconnect()
                log_info(f"🔌 [ORCH:stop] {i}/{len(self.collectors)} {name} قطع شد")
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:stop] {name} | {type(e).__name__}: {e}"
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [ORCH:stop] همه قطع شدند | {elapsed_ms:.0f}ms"
        )
