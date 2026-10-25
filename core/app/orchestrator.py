"""Orchestrator — هماهنگ‌کنندهٔ کلکتورها، پولینگ و مدیریت اتصال.

این فایل جایگاه مرکزی برای monitoring همهٔ collectors است:
  - به هر collector یک task اختصاصی می‌دهد
  - interval هر collector را از خودش می‌پرسد (poll_interval_seconds)
  - در صورت قطع شدن، با backoff نمایی دوباره وصل می‌کند
  - بعد از وصل، پیام‌های ازدست‌رفته را از last_poll می‌خواند (catch-up)
  - هر دور موفق، last_poll را به‌روز می‌کند (ضربان زنده)

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX: `stop()` حالا با timeout 15s هر collector را disconnect
    می‌کند و در صورت timeout پروسه‌های chrome همان collector را
    می‌کشد.
  - 🐛 FIX: `_initial_fetch_all` به شکل درست (connect + catch-up).
  - 🐛 FIX: `_kill_collector_chrome` متد مستقل (نه داخل poll).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import datetime, timedelta
from typing import Any

from config import SYSTEM_CONFIG
from core.contracts.base_collector import BaseCollector
from core.database import BaseDatabase
from core.kernel.event_bus import EventBus
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
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


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_SLOW_POLL_MS = _env_int(
    "ORCH_SLOW_POLL_MS", 30_000,
    min_val=1000, max_val=600_000,
)
_SLOW_INITIAL_MS = _env_int(
    "ORCH_SLOW_INITIAL_MS", 20_000,
    min_val=1000, max_val=600_000,
)
_LOG_PREVIEW_LEN = _env_int(
    "ORCH_LOG_PREVIEW", 80,
    min_val=20, max_val=500,
)
_GREET_TIMEOUT_S = _env_float(
    "ORCH_GREET_TIMEOUT_S", 20.0,
    min_val=1.0, max_val=300.0,
)
_POLL_TIMEOUT_S = _env_float(
    "ORCH_POLL_TIMEOUT_S", 120.0,
    min_val=5.0, max_val=1800.0,
)
_CATCHUP_TIMEOUT_S = _env_float(
    "ORCH_CATCHUP_TIMEOUT_S", 120.0,
    min_val=5.0, max_val=1800.0,
)
_INITIAL_TIMEOUT_S = _env_float(
    "ORCH_INITIAL_TIMEOUT_S", 180.0,
    min_val=5.0, max_val=1800.0,
)
_WARM_AI_TIMEOUT_S = _env_float(
    "ORCH_WARM_AI_TIMEOUT_S", 60.0,
    min_val=5.0, max_val=600.0,
)
_DISCONNECT_TIMEOUT_S = _env_float(
    "ORCH_DISCONNECT_TIMEOUT_S", 15.0,
    min_val=2.0, max_val=120.0,
)

# backoff connection
_RECONNECT_DELAYS: tuple[int, ...] = (
    5, 10, 20,
)
_RECONNECT_MAX_ATTEMPTS = _env_int(
    "ORCH_RECONNECT_MAX_ATTEMPTS", 3,
    min_val=1, max_val=20,
)
_RECONNECT_DELAY_CAP_S = _env_int(
    "ORCH_RECONNECT_DELAY_CAP_S", 60,
    min_val=5, max_val=3600,
)

# حداقل interval poll (برای جلوگیری از busy loop)
_MIN_POLL_INTERVAL_S = _env_float(
    "ORCH_MIN_POLL_INTERVAL_S", 1.0,
    min_val=0.1, max_val=60.0,
)


# ═══════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════
class Orchestrator:
    """هماهنگ‌کنندهٔ کلکتورها و حلقهٔ پولینگ."""

    def __init__(
        self,
        collectors: list[BaseCollector],
        event_bus: EventBus,
        db: BaseDatabase,
    ):
        self.collectors = collectors or []
        self.event_bus = event_bus
        self.db = db
        self.running = False

        # 🐛 FIX: نگه‌داری reference taskها
        self._pending_tasks: list[asyncio.Task] = []

        # 🐛 FIX: event برای بیداری سریع در stop
        self._stop_event: asyncio.Event | None = None

        self.poll_interval_seconds = (
            SYSTEM_CONFIG.get("poll_interval_hours", 24) * 3600
        )

        if not self.collectors:
            log_warning(
                "⚠️ [ORCH:init] هیچ کلکتوری داده نشده — "
                "start() بدون task اجرا می‌شود"
            )

        # لیست intervalهای هر collector
        intervals_info: list[str] = []
        for c in self.collectors:
            try:
                iv = getattr(c, "poll_interval_seconds", 2)
                name = getattr(c, "platform_name", "?")
                intervals_info.append(f"{name}={iv:g}s")
            except Exception:
                intervals_info.append(
                    f"{getattr(c, 'platform_name', '?')}=?s"
                )

        log_info(
            f"🎛️ [ORCH:init] آماده | "
            f"collectors={len(self.collectors)} | "
            f"intervals=[{', '.join(intervals_info) or '—'}] | "
            f"reconnect_attempts={_RECONNECT_MAX_ATTEMPTS} | "
            f"poll_timeout={_POLL_TIMEOUT_S}s"
        )

    # ═══════════════════════════════════════════
    # start
    # ═══════════════════════════════════════════
    async def start(self):
        """شروع: initial_fetch سری، سپس task per collector، سپس عملیات جانبی.

        اصل طراحی:
          ۱) تسک‌های polling اول راه می‌افتند — قبل از هر await طولانی.
          ۲) عملیات جانبی (greet/report) fire-and-forget هستند.
          ۳) هیچ عملیات جانبی هرگز تسک‌های polling را بلاک نمی‌کند.
        """
        if self.running:
            log_warning(
                "⚠️ [ORCH:start] از قبل در حال اجراست — رد شد"
            )
            return

        self.running = True
        self._stop_event = asyncio.Event()

        t_all = time.monotonic()
        log_info(
            f"🚀 [ORCH:start] شروع | "
            f"collectors={len(self.collectors)}"
        )

        # ─── ۱) راه‌اندازی تسک‌های polling ───
        tasks: list[asyncio.Task] = []
        for collector in self.collectors:
            try:
                interval = float(
                    getattr(
                        collector,
                        "poll_interval_seconds",
                        2,
                    )
                )
            except (TypeError, ValueError):
                interval = 2.0
            interval = max(interval, _MIN_POLL_INTERVAL_S)

            name = getattr(collector, "platform_name", "?")
            log_info(
                f"🧵 [ORCH:start] task | {name} | "
                f"interval={interval:g}s"
            )
            task = asyncio.create_task(
                self._poll_messages(
                    collector, interval=interval
                ),
                name=f"poll_{name}",
            )
            tasks.append(task)

        # 🐛 FIX: نگه‌داری reference
        self._pending_tasks = list(tasks)

        log_success(
            f"✅ [ORCH:start] {len(tasks)} تسک polling "
            f"راه افتاد"
        )

        # ─── ۲) initial_fetch (پس‌زمینه، سری) ───
        if self.collectors:
            init_task = asyncio.create_task(
                self._initial_fetch_all(),
                name="orch_initial_fetch",
            )
            self._pending_tasks.append(init_task)

        # ─── ۳) عملیات جانبی — fire-and-forget ───
        greet_task = asyncio.create_task(
            self._greet_admins_safe(), name="orch_greet"
        )
        report_task = asyncio.create_task(
            self._generate_report(), name="orch_report"
        )
        self._pending_tasks.extend([greet_task, report_task])

        startup_s = time.monotonic() - t_all
        log_success(
            f"✅ [ORCH:start] همهٔ تسک‌ها راه افتادند | "
            f"tasks={len(tasks)} | startup={startup_s:.1f}s"
        )
        log_info(
            "🔄 [ORCH:start] سیستم در حالت انتظار "
            "پیام‌های جدید..."
        )

        # ─── ۴) انتظار (تا stop) ───
        if not tasks:
            log_warning(
                "⚠️ [ORCH:start] هیچ task polling وجود ندارد — "
                "start() بی‌کار می‌ماند"
            )
            # منتظر stop_event
            try:
                await self._stop_event.wait()
            except asyncio.CancelledError:
                log_info("🛑 [ORCH:start] cancel")
                raise
            return

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            log_info("🛑 [ORCH:start] تسک‌ها cancel شدند")

    # ═══════════════════════════════════════════
    # initial_fetch_all
    # ═══════════════════════════════════════════
    async def _initial_fetch_all(self) -> None:
        """initial_fetch سری برای همهٔ کلکتورها."""
        for i, collector in enumerate(self.collectors, 1):
            name = getattr(collector, "platform_name", "?")
            log_info(
                f"▶️ [ORCH:init] initial_fetch "
                f"{i}/{len(self.collectors)} | {name}"
            )
            try:
                await self._initial_fetch(collector)
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:init] {name} | "
                    f"{type(e).__name__}: {e}"
                )
        log_success("✅ [ORCH:init] initial_fetch همه تمام شد")

    # ═══════════════════════════════════════════
    # حلقهٔ پولینگ هر کلکتور
    # ═══════════════════════════════════════════
    async def _poll_messages(
        self, collector: BaseCollector, interval: float
    ):
        """حلقهٔ poll یک collector با reconnect و timing."""
        platform = getattr(
            collector, "platform_name", "?"
        )
        fails = 0
        iteration = 0

        while self.running:
            iteration += 1
            t0 = time.monotonic()

            try:
                # ─── چک اتصال ───
                if not getattr(
                    collector, "is_connected", False
                ):
                    log_warning(
                        f"🔌 [ORCH:poll:{platform}] "
                        f"قطع است (iter#{iteration}) — "
                        f"شروع reconnect"
                    )
                    await self._supervise_reconnect(collector)
                    if not getattr(
                        collector, "is_connected", False
                    ):
                        wait = min(
                            120,
                            max(interval * 3, 15),
                        )
                        log_warning(
                            f"⏳ [ORCH:poll:{platform}] "
                            f"هنوز وصل نیست | خواب {wait:.0f}s "
                            f"(iter#{iteration})"
                        )
                        if await self._sleep_or_stop(wait):
                            break
                        continue

                # ─── پردازش (با timeout) ───
                try:
                    await asyncio.wait_for(
                        self._process_messages(collector),
                        timeout=_POLL_TIMEOUT_S,
                    )
                except TimeoutError:
                    log_warning(
                        f"⏰ [ORCH:poll:{platform}] "
                        f"timeout {_POLL_TIMEOUT_S}s | "
                        f"iter#{iteration}"
                    )
                    fails += 1
                    continue

                fails = 0

                elapsed_ms = (time.monotonic() - t0) * 1000
                if elapsed_ms > _SLOW_POLL_MS:
                    log_warning(
                        f"🐌 [ORCH:poll:{platform}] کند | "
                        f"{elapsed_ms:.0f}ms "
                        f"(iter#{iteration})"
                    )
                else:
                    log_info(
                        f"✔️ [ORCH:poll:{platform}] "
                        f"iter#{iteration} | "
                        f"{elapsed_ms:.0f}ms | "
                        f"next={interval:g}s"
                    )

            except asyncio.CancelledError:
                log_info(
                    f"🛑 [ORCH:poll:{platform}] cancel "
                    f"(iter#{iteration})"
                )
                raise
            except Exception as e:
                fails += 1
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_error(
                    f"❌ [ORCH:poll:{platform}] "
                    f"iter#{iteration} | "
                    f"fails={fails} | {elapsed_ms:.0f}ms | "
                    f"{type(e).__name__}: {e}"
                )
                if fails >= 3:
                    log_warning(
                        f"♻️ [ORCH:poll:{platform}] "
                        f"{fails} خطا → reconnect اجباری"
                    )
                    try:
                        await self._supervise_reconnect(
                            collector
                        )
                    except Exception as e2:
                        log_error(
                            f"❌ [ORCH:poll:{platform}] "
                            f"reconnect اجباری | "
                            f"{type(e2).__name__}: {e2}"
                        )
                    fails = 0

            if await self._sleep_or_stop(interval):
                break

        log_info(
            f"🛑 [ORCH:poll:{platform}] حلقه متوقف شد | "
            f"iterations={iteration}"
        )

    # ═══════════════════════════════════════════
    # kill chrome پروسه‌های یک collector
    # ═══════════════════════════════════════════
    def _kill_collector_chrome(self, collector) -> None:
        """فقط پروسه‌های chrome مربوط به user-data-dir این collector
        را می‌کشد (نه Chrome شخصی کاربر، نه collectorهای دیگر)."""
        import os as _os
        import re as _re
        import subprocess as _sp

        # مسیر user_data_dir collector را پیدا کن
        user_data_dir = None
        for attr in (
            "_user_data_dir", "user_data_dir",
            "_profile_dir", "_user_data",
        ):
            v = getattr(collector, attr, None)
            if v:
                user_data_dir = str(v)
                break

        if not user_data_dir:
            return

        normalized = _os.path.normcase(user_data_dir)

        try:
            out = _sp.run(
                ["wmic", "process", "where", "name='chrome.exe'",
                 "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore",
                check=False,
            ).stdout
        except Exception:
            return

        for line in out.splitlines():
            if "--user-data-dir" not in line:
                continue
            if normalized not in _os.path.normcase(line):
                continue
            m = _re.search(r"(\d+)\s*$", line.strip())
            if not m:
                continue
            with contextlib.suppress(Exception):
                _sp.run(
                    ["taskkill", "/F", "/PID", m.group(1), "/T"],
                    capture_output=True, timeout=5,
                    check=False,
                )

    # ═══════════════════════════════════════════
    # sleep_or_stop
    # ═══════════════════════════════════════════
    async def _sleep_or_stop(self, seconds: float) -> bool:
        """خواب یا بیداری زودتر در stop.

        Returns:
            True اگر باید متوقف شود، False اگر فقط خواب.
        """
        if self._stop_event is None:
            await asyncio.sleep(seconds)
            return not self.running

        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=seconds,
            )
            return True  # stop شد
        except TimeoutError:
            return not self.running  # معمولی

    # ═══════════════════════════════════════════
    # reconnect
    # ═══════════════════════════════════════════
    async def _supervise_reconnect(
        self, collector: BaseCollector
    ):
        """قطع و اتصالِ مجدد با backoff نمایی + catch-up."""
        platform = getattr(
            collector, "platform_name", "?"
        )
        delay = _RECONNECT_DELAYS[0]
        t0 = time.monotonic()

        log_info(
            f"🔁 [ORCH:reconnect:{platform}] شروع | "
            f"max_attempts={_RECONNECT_MAX_ATTEMPTS} | "
            f"delays={list(_RECONNECT_DELAYS)}s"
        )

        for attempt in range(
            1, _RECONNECT_MAX_ATTEMPTS + 1
        ):
            if not self.running:
                log_info(
                    f"🛑 [ORCH:reconnect:{platform}] "
                    f"running=False — توقف"
                )
                return

            # ─── disconnect ───
            try:
                await asyncio.wait_for(
                    collector.disconnect(),
                    timeout=_DISCONNECT_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                log_warning(
                    f"⏰ [ORCH:reconnect:{platform}] "
                    f"disconnect timeout "
                    f"{_DISCONNECT_TIMEOUT_S}s — kill chrome"
                )
                with contextlib.suppress(Exception):
                    self._kill_collector_chrome(collector)
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:reconnect:{platform}] "
                    f"disconnect | "
                    f"{type(e).__name__}: {e}"
                )

            # ─── connect ───
            try:
                ok = await collector.connect()
                if ok:
                    elapsed_ms = (
                        time.monotonic() - t0
                    ) * 1000
                    log_success(
                        f"✅ [ORCH:reconnect:{platform}] "
                        f"وصل شد | attempt={attempt} | "
                        f"{elapsed_ms:.0f}ms"
                    )
                    try:
                        await asyncio.wait_for(
                            self._catch_up(collector),
                            timeout=_CATCHUP_TIMEOUT_S,
                        )
                    except TimeoutError:
                        log_warning(
                            f"⏰ [ORCH:reconnect:{platform}] "
                            f"catch-up timeout "
                            f"{_CATCHUP_TIMEOUT_S}s"
                        )
                    except Exception as e:
                        log_error(
                            f"❌ [ORCH:reconnect:{platform}] "
                            f"catch-up | "
                            f"{type(e).__name__}: {e}"
                        )
                    return
                else:
                    log_warning(
                        f"⚠️ [ORCH:reconnect:{platform}] "
                        f"attempt {attempt} connect()=False"
                    )
            except Exception as e:
                log_error(
                    f"❌ [ORCH:reconnect:{platform}] "
                    f"attempt {attempt} | "
                    f"{type(e).__name__}: {e}"
                )

            # ─── صبر قبل از تلاش بعدی ───
            if attempt < _RECONNECT_MAX_ATTEMPTS:
                log_info(
                    f"💤 [ORCH:reconnect:{platform}] "
                    f"صبر {delay}s "
                    f"(attempt {attempt}→{attempt+1})"
                )
                if await self._sleep_or_stop(delay):
                    return
                delay = min(
                    delay * 2, _RECONNECT_DELAY_CAP_S
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_error(
            f"❌ [ORCH:reconnect:{platform}] پس از "
            f"{_RECONNECT_MAX_ATTEMPTS} تلاش وصل نشد | "
            f"{elapsed_ms:.0f}ms | در دورِ بعد دوباره "
            f"امتحان می‌شود"
        )

    # ═══════════════════════════════════════════
    # catch-up
    # ═══════════════════════════════════════════
    async def _catch_up(self, collector: BaseCollector):
        """بعد از وصلِ مجدد، پیام‌های ازدست‌رفته را می‌خواند."""
        platform = getattr(
            collector, "platform_name", "?"
        )
        t0 = time.monotonic()

        try:
            # ─── last_poll ───
            try:
                db_last = await self.db.get_last_poll(
                    platform
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:catchup:{platform}] "
                    f"get_last_poll | "
                    f"{type(e).__name__}: {e} — "
                    f"default(-24h)"
                )
                db_last = None

            if db_last:
                last_poll = db_last
                source = "db"
            else:
                last_poll = (
                    datetime.now() - timedelta(hours=24)  # noqa: DTZ005
                )
                source = "default(-24h)"

            log_info(
                f"📚 [ORCH:catchup:{platform}] شروع | "
                f"from={last_poll:%Y-%m-%d %H:%M} | "
                f"source={source}"
            )

            history = await collector.fetch_history(
                last_poll
            )

            if history:
                log_success(
                    f"✅ [ORCH:catchup:{platform}] "
                    f"{len(history)} پیام ازدست‌رفته"
                )
                await self._emit_batch(
                    platform, history
                )
            else:
                log_info(
                    f"ℹ️ [ORCH:catchup:{platform}] "
                    f"پیام ازدست‌رفته‌ای نبود"
                )

            await self.db.update_last_poll(
                platform, datetime.now()  # noqa: DTZ005
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"✅ [ORCH:catchup:{platform}] کامل | "
                f"history={len(history) if history else 0} | "
                f"{elapsed_ms:.0f}ms"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_warning(
                f"⚠️ [ORCH:catchup:{platform}] ناقص | "
                f"{elapsed_ms:.0f}ms | "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # initial_fetch
    # ═══════════════════════════════════════════
    async def _initial_fetch(self, collector: BaseCollector):
        """اتصال اولیه + catch-up."""
        platform = getattr(
            collector, "platform_name", "?"
        )
        t0 = time.monotonic()
        log_info(
            f"🎬 [ORCH:init:{platform}] "
            f"شروع پردازش اولیه"
        )

        try:
            # ─── last_poll ───
            try:
                db_last = await self.db.get_last_poll(
                    platform
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:init:{platform}] "
                    f"get_last_poll | "
                    f"{type(e).__name__}: {e}"
                )
                db_last = None

            if not db_last:
                last_poll = (
                    datetime.now() - timedelta(hours=24)  # noqa: DTZ005
                )
                src = "default(-24h)"
                log_info(
                    f"ℹ️ [ORCH:init:{platform}] "
                    f"اولین اجرا — از ۲۴ ساعت قبل"
                )
            else:
                last_poll = db_last
                src = "db"
                log_info(
                    f"ℹ️ [ORCH:init:{platform}] "
                    f"ادامه از "
                    f"{last_poll:%Y-%m-%d %H:%M} ({src})"
                )

            # ─── connect ───
            t_conn = time.monotonic()
            try:
                connected = await asyncio.wait_for(
                    collector.connect(),
                    timeout=_INITIAL_TIMEOUT_S,
                )
            except TimeoutError:
                log_error(
                    f"⏰ [ORCH:init:{platform}] "
                    f"connect timeout "
                    f"{_INITIAL_TIMEOUT_S}s"
                )
                return
            conn_ms = (time.monotonic() - t_conn) * 1000

            if not connected:
                log_error(
                    f"❌ [ORCH:init:{platform}] "
                    f"connect()=False | {conn_ms:.0f}ms"
                )
                return
            log_success(
                f"✅ [ORCH:init:{platform}] متصل | "
                f"{conn_ms:.0f}ms"
            )

            # ─── catch-up ───
            try:
                history = await asyncio.wait_for(
                    collector.fetch_history(last_poll),
                    timeout=_INITIAL_TIMEOUT_S,
                )
            except TimeoutError:
                history = []
                log_error(
                    f"⏰ [ORCH:init:{platform}] "
                    f"fetch_history timeout "
                    f"{_INITIAL_TIMEOUT_S}s"
                )
            except Exception as e:
                history = []
                log_error(
                    f"❌ [ORCH:init:{platform}] "
                    f"fetch_history | "
                    f"{type(e).__name__}: {e}"
                )

            if history:
                log_success(
                    f"📚 [ORCH:init:{platform}] "
                    f"{len(history)} پیام عقب‌افتاده "
                    f"(از {last_poll:%Y-%m-%d %H:%M})"
                )
                await self._emit_batch(platform, history)

            await self.db.update_last_poll(
                platform, datetime.now()  # noqa: DTZ005
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_INITIAL_MS:
                log_warning(
                    f"🐌 [ORCH:init:{platform}] کند | "
                    f"{elapsed_ms:.0f}ms"
                )
            else:
                log_success(
                    f"✅ [ORCH:init:{platform}] کامل | "
                    f"history="
                    f"{len(history) if history else 0} | "
                    f"{elapsed_ms:.0f}ms"
                )

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [ORCH:init:{platform}] "
                f"{elapsed_ms:.0f}ms | "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # process_messages
    # ═══════════════════════════════════════════
    async def _process_messages(
        self, collector: BaseCollector
    ):
        """دریافت پیام‌های جدید + انتشار رویداد + ضربان."""
        platform = getattr(
            collector, "platform_name", "?"
        )

        # ─── last_poll (امن) ───
        try:
            last_poll = await self.db.get_last_poll(
                platform
            )
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:msg:{platform}] "
                f"get_last_poll | "
                f"{type(e).__name__}: {e} — default(-24h)"
            )
            last_poll = None

        if not last_poll:
            last_poll = (
                datetime.now() - timedelta(hours=24)  # noqa: DTZ005
            )

        # ─── fetch ───
        t0 = time.monotonic()
        messages = await collector.fetch_messages(last_poll)
        fetch_ms = (time.monotonic() - t0) * 1000

        if messages:
            log_success(
                f"📥 [ORCH:msg:{platform}] "
                f"{len(messages)} پیام جدید | "
                f"fetch={fetch_ms:.0f}ms"
            )
            await self._emit_batch(platform, messages)
        else:
            log_info(
                f"ℹ️ [ORCH:msg:{platform}] "
                f"بدون پیام جدید | fetch={fetch_ms:.0f}ms"
            )

        # ─── ضربان زنده ───
        await self.db.update_last_poll(
            platform, datetime.now()  # noqa: DTZ005
        )

    # ─── سازگاری ───
    async def _fetch_new_messages(
        self, collector: BaseCollector
    ):
        await self._process_messages(collector)

    # ═══════════════════════════════════════════
    # emit batch (helper مشترک)
    # ═══════════════════════════════════════════
    async def _emit_batch(
        self, platform: str, messages: list[dict]
    ) -> None:
        """انتشار گروهی پیام‌ها — با شمارش خطا."""
        emit_errors = 0
        for msg in messages:
            with contextlib.suppress(Exception):
                msg["platform"] = platform
            try:
                await self.event_bus.emit(
                    "message.received", msg
                )
            except Exception as e:
                emit_errors += 1
                if emit_errors <= 2:
                    log_warning(
                        f"⚠️ [ORCH:emit:{platform}] "
                        f"{type(e).__name__}: {e}"
                    )
        if emit_errors:
            log_warning(
                f"⚠️ [ORCH:emit:{platform}] "
                f"{emit_errors} emit ناموفق از "
                f"{len(messages)}"
            )

    # ═══════════════════════════════════════════
    # greet + warm AI
    # ═══════════════════════════════════════════
    async def _greet_admins(self):
        """سلام به مدیر + گرم‌کردن AI (غیر-blocking)."""
        try:
            from config import PLUGINS_CONFIG
            enabled = (
                PLUGINS_CONFIG.get(
                    "adminhandler", {}
                ).get("enabled", True)
            )
        except Exception:
            enabled = True

        if not enabled:
            log_info(
                "ℹ️ [ORCH:greet] adminhandler غیرفعال — رد"
            )
            return

        # 🐛 FIX: import امن
        try:
            from core.adapters import messenger_registry
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:greet] messenger_registry | "
                f"{type(e).__name__}: {e}"
            )
            return

        text = (
            "🤖 سلام مدیر! ربات بالا آمد و آماده‌ام. "
            "هر سؤالی داری همین‌جا بپرس "
            "(وضعیت، گزارش، سلامت، افزودن کلیدواژه، "
            "یا هر چیزِ دیگر)."
        )

        t0 = time.monotonic()
        try:
            ok = await messenger_registry.notify_admins(
                text
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            if ok:
                log_success(
                    f"👋 [ORCH:greet] سلام ارسال شد | "
                    f"{elapsed_ms:.0f}ms"
                )
            else:
                log_warning(
                    f"⚠️ [ORCH:greet] ارسال نشد "
                    f"(مدیر/فرستنده آماده نیست) | "
                    f"{elapsed_ms:.0f}ms"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:greet] "
                f"{type(e).__name__}: {e}"
            )

        # warm AI در پس‌زمینه
        warm_task = asyncio.create_task(
            self._warm_ai(), name="orch_warm_ai"
        )
        self._pending_tasks.append(warm_task)

    async def _greet_admins_safe(self):
        """فراخوانی امنِ _greet_admins — fire-and-forget."""
        try:
            await asyncio.wait_for(
                self._greet_admins(),
                timeout=_GREET_TIMEOUT_S,
            )
        except TimeoutError:
            log_warning(
                f"⚠️ [ORCH:greet] timeout "
                f"{_GREET_TIMEOUT_S}s — رد شد"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:greet] "
                f"{type(e).__name__}: {e}"
            )

    async def _warm_ai(self):
        """گرم‌کردن مدل AI — با timeout."""
        t0 = time.monotonic()
        try:
            from core.services.ai_assistant import (
                get_ai_assistant,
            )
            ai = get_ai_assistant()

            try:
                available = await asyncio.wait_for(
                    ai.available(),
                    timeout=_WARM_AI_TIMEOUT_S,
                )
            except TimeoutError:
                log_warning(
                    f"⏰ [ORCH:warm] available timeout "
                    f"{_WARM_AI_TIMEOUT_S}s"
                )
                return

            if not available:
                log_info(
                    "ℹ️ [ORCH:warm] AI در دسترس نیست — رد"
                )
                return

            try:
                ans = await asyncio.wait_for(
                    ai.chat("سلام"),
                    timeout=_WARM_AI_TIMEOUT_S,
                )
            except TimeoutError:
                log_warning(
                    f"⏰ [ORCH:warm] chat timeout "
                    f"{_WARM_AI_TIMEOUT_S}s"
                )
                return

            elapsed_ms = (time.monotonic() - t0) * 1000

            if (
                not ans
                or "در دسترس نیست" in ans
                or ans.startswith("⚠️")
            ):
                log_warning(
                    f"⚠️ [ORCH:warm] AI جواب نداد | "
                    f"{elapsed_ms:.0f}ms | "
                    f"ans={str(ans)[:60]!r}"
                )
                return

            log_success(
                f"🔥 [ORCH:warm] مدل گرم شد | "
                f"{elapsed_ms:.0f}ms"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:warm] "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # گزارش اولیه
    # ═══════════════════════════════════════════
    async def _generate_report(self):
        log_info("📊 [ORCH:report] تولید گزارش اولیه...")
        try:
            total = await asyncio.wait_for(
                self.db.get_total_count(),
                timeout=30.0,
            )
            log_success(
                f"✅ [ORCH:report] "
                f"پیام={total.get('messages', 0)} "
                f"سرنخ={total.get('leads', 0)} "
                f"گروه={total.get('groups', 0)}"
            )
        except TimeoutError:
            log_warning("⏰ [ORCH:report] timeout 30s")
        except Exception as e:
            log_warning(
                f"⚠️ [ORCH:report] "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # stop
    # ═══════════════════════════════════════════
    async def stop(self):
        """توقف کامل — با بیداری فوری taskهای خواب‌رفته."""
        self.running = False
        log_info(
            f"🛑 [ORCH:stop] شروع | "
            f"collectors={len(self.collectors)}"
        )
        t0 = time.monotonic()

        # 🐛 FIX: بیداری سریع taskهای خواب‌رفته
        if self._stop_event is not None:
            self._stop_event.set()
            
        # 🐛 FIX: cancel صریح تسک‌های polling
        if self._pending_tasks:
            pending = [t for t in self._pending_tasks if not t.done()]
            if pending:
                log_info(
                    f"🛑 [ORCH:stop] cancel تسک‌ها | "
                    f"count={len(pending)}"
                )
                for t in pending:
                    t.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *pending, return_exceptions=True
                        ),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    log_warning(
                        "⏰ [ORCH:stop] cancel تسک‌ها timeout 5s"
                    )
                    

        for i, collector in enumerate(
            self.collectors, 1
        ):
            name = getattr(
                collector, "platform_name", "?"
            )
            try:
                await asyncio.wait_for(
                    collector.disconnect(),
                    timeout=_DISCONNECT_TIMEOUT_S,
                )
                log_info(
                    f"🔌 [ORCH:stop] {i}/"
                    f"{len(self.collectors)} {name} قطع شد"
                )
            except asyncio.TimeoutError:
                log_warning(
                    f"⏰ [ORCH:stop] {name} | "
                    f"timeout {_DISCONNECT_TIMEOUT_S}s — "
                    f"kill پروسه‌های chrome"
                )
                try:
                    self._kill_collector_chrome(collector)
                except Exception as ke:
                    log_warning(
                        f"⚠️ [ORCH:stop] {name} kill | "
                        f"{type(ke).__name__}: {ke}"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [ORCH:stop] {name} | "
                    f"{type(e).__name__}: {e}"
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [ORCH:stop] همه قطع شدند | "
            f"{elapsed_ms:.0f}ms"
        )

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        pending = sum(
            1 for t in self._pending_tasks
            if not t.done()
        )
        return {
            "running": self.running,
            "collectors": len(self.collectors),
            "pending_tasks": pending,
            "total_tasks": len(self._pending_tasks),
            "stop_event_set": (
                self._stop_event.is_set()
                if self._stop_event else False
            ),
            "poll_timeout_s": _POLL_TIMEOUT_S,
            "catchup_timeout_s": _CATCHUP_TIMEOUT_S,
            "initial_timeout_s": _INITIAL_TIMEOUT_S,
            "reconnect_max_attempts": _RECONNECT_MAX_ATTEMPTS,
            "reconnect_delays": list(_RECONNECT_DELAYS),
        }

    def __repr__(self) -> str:
        state = "running" if self.running else "idle"
        return (
            f"<Orchestrator {state} "
            f"collectors={len(self.collectors)}>"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = ["Orchestrator"]