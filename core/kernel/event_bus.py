"""
core/event_bus.py — سیستم مدیریت رویداد (Event Bus).

قابلیت‌ها:
  - ثبت گوش‌دهنده با اولویت (HIGH/NORMAL/LOW)
  - انتشار رویداد async (emit) و sync (emit_sync) و fire-and-forget (emit_nowait)
  - آمار و تاریخچهٔ حلقه‌ای (سقف ۱۰۰۰ رویداد)
  - پشتیبانی از هندلرهای sync و async

⚠️ لاگ‌کردن هر رویداد پیش‌فرض خاموش است (EVENT_LOG=false) — چون در پروژه‌ای
با ۷ پلتفرم و poll کوتاه، فایل لاگ در چند روز پر می‌شود. برای دیباگ:
EVENT_LOG=true در .env.

نسخهٔ ۳ (این نسخه):
  - 🔴 FIX: EVENT_LOG حالا hot-reload می‌شود (به‌جای خواندن یک‌بار در import).
    قبلاً تغییر `EVENT_LOG` از ChatOps نیاز به restart داشت — الان
    بدون restart اعمال می‌شود (سازگار با بقیهٔ پروژه).

    پیاده‌سازی: تابع `_event_log_enabled()` که هر بار از env می‌خواند.
    (هزینهٔ os.getenv ناچیز است — چند میکروثانیه — و سادگی از
     caching ارزش بیشتری دارد.)

  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── آستانه‌های هشدار (میلی‌ثانیه) ───
_SLOW_HANDLER_MS = 1000      # هر handler > 1s
_SLOW_EMIT_MS = 3000         # کل emit > 3s
_LOG_PREVIEW_LEN = 60

# ─── مقادیر true/false قابل قبول ───
_TRUE_VALUES = frozenset({"true", "1", "yes", "on", "y", "t"})


class EventPriority(Enum):
    """اولویت رویداد."""

    HIGH = 0    # اول اجرا شود
    NORMAL = 1
    LOW = 2     # آخر اجرا شود


@dataclass
class Event:
    """کلاس رویداد."""

    name: str
    data: Any
    timestamp: datetime | None = None
    priority: EventPriority = EventPriority.NORMAL
    source: str | None = None
    id: str = field(default_factory=lambda: f"{time.time()}")

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def __repr__(self) -> str:
        return (
            f"Event(name={self.name}, priority={self.priority.name}, "
            f"timestamp={self.timestamp}, source={self.source})"
        )


# ═══════════════════════════════════════════
# سوییچ لاگ هر emit — با hot-reload
# ═══════════════════════════════════════════
def _event_log_enabled() -> bool:
    """
    آیا EVENT_LOG فعال است؟

    🔴 FIX نسخهٔ ۳: حالا هر بار از env می‌خواند (hot-reload).
    قبلاً یک متغیر module-level بود که در import خونده می‌شد.

    مقادیر قابل قبول برای true: true/1/yes/on/y/t (case-insensitive).
    پیش‌فرض اگر env تنظیم نشده باشد: False.

    Returns:
        True اگر لاگ هر emit فعال باشد.
    """
    raw = os.getenv("EVENT_LOG", "false")
    return str(raw).strip().lower() in _TRUE_VALUES


# ═══════════════════════════════════════════
# EventBus
# ═══════════════════════════════════════════
class EventBus:
    """سیستم مدیریت رویدادها (Event Bus)."""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
        self._handler_priorities: dict[str, dict[Callable, EventPriority]] = {}
        self._event_history: list[Event] = []
        self._max_history = 1000

        # 🐛 FIX: نگه‌داشتن reference به taskهای fire-and-forget
        self._pending_tasks: list[asyncio.Task] = []

        self._stats: dict[str, Any] = {
            "total_events": 0,
            "total_handlers_called": 0,
            "errors": 0,
            "slow_handlers": 0,
            "events_by_name": {},
            "started_at": datetime.now().isoformat(),
        }

        log_success(
            f"✅ [EVENTBUS:init] راه‌اندازی | "
            f"EVENT_LOG={_event_log_enabled()} | "
            f"max_history={self._max_history}"
        )

    # ═══════════════════════════════════════════
    # subscribe / unsubscribe
    # ═══════════════════════════════════════════
    def subscribe(
        self,
        event_name: str,
        handler: Callable,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """ثبت گوش‌دهنده برای رویداد با اولویت مشخص."""
        if event_name not in self._handlers:
            self._handlers[event_name] = []
            self._handler_priorities[event_name] = {}

        # 🐛 FIX: اگه handler قبلاً ثبت شده، priority رو به‌روز کن نه دوباره اضافه
        if handler in self._handlers[event_name]:
            old_prio = self._handler_priorities[event_name].get(handler)
            if old_prio == priority:
                return
            self._handler_priorities[event_name][handler] = priority
            log_info(
                f"♻️ [EVENTBUS:subscribe] به‌روز priority | "
                f"{event_name} | "
                f"{old_prio.name if old_prio else '?'} → {priority.name}"
            )
        else:
            self._handlers[event_name].append(handler)
            self._handler_priorities[event_name][handler] = priority
            hname = getattr(handler, "__name__", repr(handler)[:40])
            log_success(
                f"📢 [EVENTBUS:subscribe] {event_name} → {hname} | "
                f"priority={priority.name} | "
                f"total={len(self._handlers[event_name])}"
            )

        # مرتب‌سازی بر اساس اولویت (HIGH=0 اول)
        self._handlers[event_name].sort(
            key=lambda h: self._handler_priorities[event_name]
            .get(h, EventPriority.NORMAL).value
        )

    def unsubscribe(self, event_name: str, handler: Callable) -> bool:
        """حذف گوش‌دهنده از یک رویداد."""
        if event_name not in self._handlers:
            log_info(
                f"ℹ️ [EVENTBUS:unsubscribe] رویداد «{event_name}» ثبت نشده"
            )
            return False
        if handler not in self._handlers[event_name]:
            log_info(
                f"ℹ️ [EVENTBUS:unsubscribe] handler در «{event_name}» نبود"
            )
            return False

        self._handlers[event_name].remove(handler)
        self._handler_priorities.get(event_name, {}).pop(handler, None)
        hname = getattr(handler, "__name__", repr(handler)[:40])
        remaining = len(self._handlers[event_name])
        log_info(
            f"📢 [EVENTBUS:unsubscribe] {event_name} → {hname} | "
            f"remaining={remaining}"
        )
        return True

    def has_handler(
        self, event_name: str, handler: Callable | None = None
    ) -> bool:
        """آیا گوش‌دهنده‌ای برای رویداد هست؟"""
        if event_name not in self._handlers:
            return False
        if handler:
            return handler in self._handlers[event_name]
        return len(self._handlers[event_name]) > 0

    # ═══════════════════════════════════════════
    # emit (async)
    # ═══════════════════════════════════════════
    async def emit(
        self,
        event_name: str,
        data: Any,
        source: str | None = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> int:
        """انتشار رویداد و اجرای هندلرها به ترتیب اولویت.

        Returns:
            تعداد handlerهای موفق (یا 0 اگه هیچ handlerی نبود).
        """
        t0 = time.monotonic()
        event = Event(
            name=event_name, data=data, priority=priority, source=source
        )

        # ─── آمار ───
        self._stats["total_events"] += 1
        self._stats["events_by_name"][event_name] = (
            self._stats["events_by_name"].get(event_name, 0) + 1
        )

        # ─── تاریخچه (حلقه‌ای O(1)) ───
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

        # ─── لاگ رویداد (فقط اگه EVENT_LOG=true) ───
        # 🔴 FIX: حالا هر بار از env می‌خواند (hot-reload)
        if _event_log_enabled():
            log_info(
                f"📡 [EVENTBUS:emit] {event_name} | "
                f"priority={priority.name} | "
                f"source={source or '—'}"
            )

        handlers = self._handlers.get(event_name)
        if not handlers:
            return 0

        # ─── اجرای هندلرها ───
        success = 0
        for handler in handlers:
            hname = getattr(handler, "__name__", "?")
            t_h = time.monotonic()
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
                success += 1
                self._stats["total_handlers_called"] += 1

                elapsed_ms = (time.monotonic() - t_h) * 1000
                if elapsed_ms > _SLOW_HANDLER_MS:
                    self._stats["slow_handlers"] += 1
                    log_warning(
                        f"🐌 [EVENTBUS:handler] {event_name} → {hname} | "
                        f"کند | {elapsed_ms:.0f}ms"
                    )
            except Exception as e:
                self._stats["errors"] += 1
                log_error(
                    f"❌ [EVENTBUS:handler] {event_name} → {hname} | "
                    f"{type(e).__name__}: {e}"
                )

        # ─── لاگ خلاصه اگر کند یا خطا داشته یا EVENT_LOG ───
        total_ms = (time.monotonic() - t0) * 1000
        if total_ms > _SLOW_EMIT_MS:
            log_warning(
                f"🐌 [EVENTBUS:emit] {event_name} | "
                f"handlers={success}/{len(handlers)} | {total_ms:.0f}ms"
            )
        # 🔴 FIX: حالا هر بار از env می‌خواند (hot-reload)
        elif _event_log_enabled():
            log_info(
                f"✅ [EVENTBUS:emit] {event_name} | "
                f"handlers={success}/{len(handlers)} | {total_ms:.0f}ms"
            )

        return success

    # ═══════════════════════════════════════════
    # emit_nowait (fire-and-forget)
    # ═══════════════════════════════════════════
    def emit_nowait(
        self,
        event_name: str,
        data: Any,
        source: str | None = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> asyncio.Task | None:
        """انتشار fire-and-forget روی همون loop.

        🐛 FIX: reference به task نگه داشته می‌شه تا GC وسط کار نکشه.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log_warning(
                f"⚠️ [EVENTBUS:emit_nowait] خارج از loop فعال | "
                f"{event_name} نادیده گرفته شد"
            )
            return None

        try:
            task = loop.create_task(
                self.emit(event_name, data, source, priority)
            )
            # نگه‌داری reference (تا اتمام)
            self._pending_tasks.append(task)
            task.add_done_callback(self._on_task_done)
            return task
        except Exception as e:
            log_error(
                f"❌ [EVENTBUS:emit_nowait] create_task | "
                f"{event_name} | {type(e).__name__}: {e}"
            )
            return None

    def _on_task_done(self, task: asyncio.Task) -> None:
        """حذف taskهای تمام‌شده از لیست."""
        try:
            self._pending_tasks.remove(task)
        except ValueError:
            pass
        # اگه task با exception تموم شد، لاگ بزن
        try:
            exc = task.exception()
            if exc:
                log_error(
                    f"❌ [EVENTBUS:task] exception | "
                    f"{type(exc).__name__}: {exc}"
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def get_pending_tasks_count(self) -> int:
        """تعداد taskهای در حال اجرا."""
        return len(self._pending_tasks)

    # ═══════════════════════════════════════════
    # emit_sync
    # ═══════════════════════════════════════════
    def emit_sync(
        self, event_name: str, data: Any, source: str | None = None
    ) -> None:
        """انتشار رویداد از کد sync.

        اگر داخل loop فعال باشه، فقط هشدار می‌ده (نمی‌تونه loop جدید بسازه).
        """
        # گارد: داخل loop فعال
        try:
            asyncio.get_running_loop()
            log_warning(
                f"⚠️ [EVENTBUS:emit_sync] داخل loop فعال صدا زده شد | "
                f"{event_name} — از await emit استفاده کن"
            )
            return
        except RuntimeError:
            pass  # ok — loop در حال اجرا نیست

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.emit(event_name, data, source))
        except Exception as e:
            log_error(
                f"❌ [EVENTBUS:emit_sync] {event_name} | "
                f"{type(e).__name__}: {e}"
            )
        finally:
            try:
                loop.close()
            except Exception as e:
                log_warning(
                    f"⚠️ [EVENTBUS:emit_sync] loop.close | "
                    f"{type(e).__name__}: {e}"
                )

    # ═══════════════════════════════════════════
    # emit_async (alias)
    # ═══════════════════════════════════════════
    async def emit_async(
        self, event_name: str, data: Any, source: str | None = None
    ) -> int:
        """alias برای emit — سازگاری عقب‌رو."""
        return await self.emit(event_name, data, source)

    # ═══════════════════════════════════════════
    # wait_for
    # ═══════════════════════════════════════════
    async def wait_for(
        self,
        event_name: str,
        timeout: float | None = None,
        predicate: Callable | None = None,
    ) -> Event | None:
        """انتظار یک‌بارمصرف برای رویداد (با timeout و predicate اختیاری)."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def handler(event: Event) -> None:
            if not future.done() and (
                predicate is None or predicate(event)
            ):
                future.set_result(event)

        self.subscribe(event_name, handler, priority=EventPriority.HIGH)
        try:
            t0 = time.monotonic()
            result = await asyncio.wait_for(future, timeout)
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"⏳ [EVENTBUS:wait_for] {event_name} | "
                f"دریافت شد | {elapsed_ms:.0f}ms"
            )
            return result
        except TimeoutError:
            log_info(
                f"⏰ [EVENTBUS:wait_for] {event_name} | "
                f"timeout={timeout}s"
            )
            return None
        except Exception as e:
            log_warning(
                f"⚠️ [EVENTBUS:wait_for] {event_name} | "
                f"{type(e).__name__}: {e}"
            )
            return None
        finally:
            self.unsubscribe(event_name, handler)

    # ═══════════════════════════════════════════
    # آمار و تاریخچه
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """آمار کامل رویدادها + تاریخچهٔ اخیر."""
        last_age_s: float | None = None
        if self._event_history:
            try:
                delta = datetime.now() - self._event_history[-1].timestamp
                last_age_s = round(delta.total_seconds(), 1)
            except Exception:
                pass

        return {
            **self._stats,
            "pending_tasks": len(self._pending_tasks),
            "last_event_age_s": last_age_s,
            "event_log_enabled": _event_log_enabled(),
            "active_handlers": {
                event: len(handlers)
                for event, handlers in self._handlers.items()
                if handlers
            },
            "recent_events": [
                {
                    "name": e.name,
                    "timestamp": (
                        e.timestamp.isoformat() if e.timestamp else None
                    ),
                    "source": e.source,
                    "priority": e.priority.name,
                }
                for e in self._event_history[-10:]
            ],
        }

    def get_event_history(self, limit: int = 50) -> list[Event]:
        """تاریخچهٔ رویدادها (آخرین limit عدد)."""
        return self._event_history[-limit:]

    def get_event_names(self) -> list[str]:
        """لیست همهٔ رویدادهای ثبت‌شده — برای debug."""
        return sorted(self._handlers.keys())

    def clear_history(self) -> int:
        """پاک کردن تاریخچه. Returns: تعداد رویدادهای پاک‌شده."""
        n = len(self._event_history)
        self._event_history.clear()
        log_info(
            f"🗑️ [EVENTBUS:clear_history] {n} رویداد پاک شد"
        )
        return n

    # ═══════════════════════════════════════════
    # مدیریت هندلرها
    # ═══════════════════════════════════════════
    def remove_all_handlers(self, event_name: str | None = None) -> None:
        """حذف همهٔ هندلرها (یا فقط برای یک رویداد)."""
        if event_name:
            n = len(self._handlers.get(event_name, []))
            self._handlers[event_name] = []
            self._handler_priorities[event_name] = {}
            log_info(
                f"🗑️ [EVENTBUS:remove_all] {event_name} | "
                f"{n} هندلر حذف شد"
            )
        else:
            total = sum(len(h) for h in self._handlers.values())
            n_events = len(self._handlers)
            self._handlers.clear()
            self._handler_priorities.clear()
            log_info(
                f"🗑️ [EVENTBUS:remove_all] همه | "
                f"{total} هندلر از {n_events} رویداد حذف شد"
            )


# ─── سازگاری عقب‌رو ───
EventBusOld = EventBus