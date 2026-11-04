"""core/kernel/event_bus.py — سیستم مدیریت رویداد (Event Bus).

قابلیت‌ها:
  - ثبت گوش‌دهنده با اولویت (HIGH/NORMAL/LOW)
  - انتشار رویداد async (emit) و sync (emit_sync) و
    fire-and-forget (emit_nowait)
  - آمار و تاریخچهٔ حلقه‌ای (سقف env-based)
  - پشتیبانی از هندلرهای sync و async

⚠️ لاگ‌کردن هر رویداد پیش‌فرض خاموش است (EVENT_LOG=false) — چون
در پروژه‌ای با ۷ پلتفرم و poll کوتاه، فایل لاگ در چند روز پر
می‌شود. برای دیباگ: EVENT_LOG=true در .env.

نسخهٔ ۴ (این نسخه):
  - 🐛 FIX: `_SLOW_HANDLER_MS`, `_SLOW_EMIT_MS`, `_LOG_PREVIEW_LEN`
    حالا env-based.
  - 🐛 FIX: `_max_history` حالا env-based (`EVENTBUS_MAX_HISTORY`).
  - 🐛 FIX: `_event_log_enabled()` حالا از `config.EVENT_LOG` می‌آید
    (با fallback به env) — طبق MEMORY R2.29.
  - 🐛 FIX: `subscribe` / `unsubscribe` / `remove_all_handlers` حالا
    thread-safe (با `threading.RLock`).
  - 🐛 FIX: `emit` حالا async callable (instance با `__call__` async)
    را هم می‌شناسد (نه فقط coroutine function).
  - 🐛 FIX: `emit` حالا timeout روی هر handler دارد
    (`EVENTBUS_HANDLER_TIMEOUT_S`).
  - 🐛 FIX: `_on_task_done` حالا cancelled task را امن handle
    می‌کند.
  - 🐛 FIX: `get_stats()` حالا deep-copy از `events_by_name` می‌دهد
    (جلوگیری از mutation).
  - 🐛 FIX: `wait_for` حالا در صورت cancel، future را پاک می‌کند.
  - 🎯 `Event.id` حالا با `uuid` (یکتاتر از `time.time()`).
  - 🎯 `clear_stats()` متد جدید.
  - 🎯 `get_handler_stats()` برای debug.
  - 🎯 `__all__` export (بدون `EventBusOld`).
  - 🎯 لاگ‌های دقیق‌تر.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳ (hot-reload EVENT_LOG).
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

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
# آستانه‌ها (env-based)
# ═══════════════════════════════════════════
_SLOW_HANDLER_MS = _env_int(
    "EVENTBUS_SLOW_HANDLER_MS", 1000,
    min_val=100, max_val=60_000,
)
_SLOW_EMIT_MS = _env_int(
    "EVENTBUS_SLOW_EMIT_MS", 3000,
    min_val=100, max_val=300_000,
)
_LOG_PREVIEW_LEN = _env_int(
    "EVENTBUS_LOG_PREVIEW", 60,
    min_val=20, max_val=500,
)
_MAX_HISTORY = _env_int(
    "EVENTBUS_MAX_HISTORY", 1000,
    min_val=10, max_val=100_000,
)
_HANDLER_TIMEOUT_S = _env_float(
    "EVENTBUS_HANDLER_TIMEOUT_S", 60.0,
    min_val=1.0, max_val=1800.0,
)

# ─── مقادیر true/false قابل قبول ───
_TRUE_VALUES = frozenset({
    "true", "1", "yes", "on", "y", "t",
})


# ═══════════════════════════════════════════
# EventPriority
# ═══════════════════════════════════════════
class EventPriority(Enum):
    """اولویت رویداد."""

    HIGH = 0     # اول اجرا شود
    NORMAL = 1
    LOW = 2      # آخر اجرا شود


# ═══════════════════════════════════════════
# Event
# ═══════════════════════════════════════════
@dataclass
class Event:
    """کلاس رویداد."""

    name: str
    data: Any
    timestamp: datetime | None = None
    priority: EventPriority = EventPriority.NORMAL
    source: str | None = None
    # 🎯 FIX: uuid برای id
    id: str = field(
        default_factory=lambda: uuid.uuid4().hex[:12]
    )

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def __repr__(self) -> str:
        return (
            f"Event(name={self.name!r}, "
            f"priority={self.priority.name}, "
            f"source={self.source!r}, id={self.id!r})"
        )


# ═══════════════════════════════════════════
# سوییچ لاگ هر emit — با hot-reload
# ═══════════════════════════════════════════
def _event_log_enabled() -> bool:
    """آیا `EVENT_LOG` فعال است؟

    🐛 FIX: حالا از `config.EVENT_LOG` (top-level) می‌خواند،
    با fallback به `os.getenv`.

    این hot-reload است: هر بار صدا زده می‌شود و مقدار فعلی را
    می‌خواند.

    Returns:
        True اگر لاگ هر emit فعال باشد.
    """
    # ─── اول از config ───
    try:
        from config import EVENT_LOG
        return bool(EVENT_LOG)
    except ImportError:
        pass
    except Exception:
        pass

    # ─── fallback به env ───
    raw = os.getenv("EVENT_LOG", "false")
    return str(raw).strip().lower() in _TRUE_VALUES


def _is_async_callable(handler: Callable) -> bool:
    """🎯 FIX: تشخیص async callable (نه فقط coroutine function)."""
    # ─── coroutine function ───
    if asyncio.iscoroutinefunction(handler):
        return True

    # ─── instance با __call__ async ───
    call = getattr(handler, "__call__", None)
    if call is not None and asyncio.iscoroutinefunction(call):
        return True

    return False


# ═══════════════════════════════════════════
# EventBus
# ═══════════════════════════════════════════
class EventBus:
    """سیستم مدیریت رویدادها (Event Bus).

    thread-safe برای subscribe/unsubscribe.
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
        self._handler_priorities: dict[
            str, dict[Callable, EventPriority]
        ] = {}
        self._event_history: list[Event] = []
        self._max_history = _MAX_HISTORY

        # 🐛 FIX: نگه‌داری reference به taskهای fire-and-forget
        self._pending_tasks: list[asyncio.Task] = []

        # 🐛 FIX: lock برای subscribe/unsubscribe
        self._lock = threading.RLock()

        self._stats: dict[str, Any] = {
            "total_events": 0,
            "total_handlers_called": 0,
            "errors": 0,
            "slow_handlers": 0,
            "timeout_handlers": 0,
            "events_by_name": {},
            "started_at": datetime.now().isoformat(),
        }

        log_success(
            f"✅ [EVENTBUS:init] راه‌اندازی | "
            f"EVENT_LOG={_event_log_enabled()} | "
            f"max_history={self._max_history} | "
            f"slow_handler_ms={_SLOW_HANDLER_MS} | "
            f"handler_timeout_s={_HANDLER_TIMEOUT_S}"
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
        if not event_name or not handler:
            log_warning(
                f"⚠️ [EVENTBUS:subscribe] ورودی نامعتبر | "
                f"event={event_name!r} | handler={handler!r}"
            )
            return

        # 🎯 normalize priority
        if not isinstance(priority, EventPriority):
            priority = EventPriority.NORMAL

        with self._lock:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
                self._handler_priorities[event_name] = {}

            # ─── اگه قبلاً ثبت شده ───
            if handler in self._handlers[event_name]:
                old_prio = self._handler_priorities[
                    event_name
                ].get(handler)
                if old_prio == priority:
                    return
                self._handler_priorities[event_name][
                    handler
                ] = priority
                log_info(
                    f"♻️ [EVENTBUS:subscribe] به‌روز "
                    f"priority | {event_name} | "
                    f"{old_prio.name if old_prio else '?'} → "
                    f"{priority.name}"
                )
            else:
                self._handlers[event_name].append(handler)
                self._handler_priorities[event_name][
                    handler
                ] = priority
                hname = getattr(
                    handler, "__name__",
                    repr(handler)[:40],
                )
                log_success(
                    f"📢 [EVENTBUS:subscribe] "
                    f"{event_name} → {hname} | "
                    f"priority={priority.name} | "
                    f"total={len(self._handlers[event_name])}"
                )

            # ─── مرتب‌سازی بر اساس اولویت ───
            self._handlers[event_name].sort(
                key=lambda h: self._handler_priorities[
                    event_name
                ].get(h, EventPriority.NORMAL).value
            )

    def unsubscribe(
        self, event_name: str, handler: Callable
    ) -> bool:
        """حذف گوش‌دهنده از یک رویداد."""
        if not event_name or not handler:
            return False

        with self._lock:
            if event_name not in self._handlers:
                log_info(
                    f"ℹ️ [EVENTBUS:unsubscribe] "
                    f"رویداد «{event_name}» ثبت نشده"
                )
                return False
            if handler not in self._handlers[event_name]:
                log_info(
                    f"ℹ️ [EVENTBUS:unsubscribe] handler در "
                    f"«{event_name}» نبود"
                )
                return False

            self._handlers[event_name].remove(handler)
            self._handler_priorities.get(event_name, {}).pop(
                handler, None
            )
            hname = getattr(
                handler, "__name__", repr(handler)[:40]
            )
            remaining = len(self._handlers[event_name])
            log_info(
                f"📢 [EVENTBUS:unsubscribe] {event_name} → "
                f"{hname} | remaining={remaining}"
            )
            return True

    def has_handler(
        self,
        event_name: str,
        handler: Callable | None = None,
    ) -> bool:
        """آیا گوش‌دهنده‌ای برای رویداد هست؟"""
        if not event_name:
            return False
        with self._lock:
            if event_name not in self._handlers:
                return False
            if handler is not None:
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
        if not event_name:
            log_warning(
                "⚠️ [EVENTBUS:emit] event_name خالی — رد"
            )
            return 0

        # 🎯 normalize priority
        if not isinstance(priority, EventPriority):
            priority = EventPriority.NORMAL

        t0 = time.monotonic()
        event = Event(
            name=event_name,
            data=data,
            priority=priority,
            source=source,
        )

        # ─── آمار ───
        self._stats["total_events"] += 1
        self._stats["events_by_name"][event_name] = (
            self._stats["events_by_name"].get(
                event_name, 0
            ) + 1
        )

        # ─── تاریخچه (حلقه‌ای) ───
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[
                -self._max_history:
            ]

        # ─── لاگ رویداد (hot-reload) ───
        if _event_log_enabled():
            log_info(
                f"📡 [EVENTBUS:emit] {event_name} | "
                f"priority={priority.name} | "
                f"source={source or '—'} | "
                f"id={event.id}"
            )

        # ─── snapshot handlers ───
        with self._lock:
            handlers = list(
                self._handlers.get(event_name, [])
            )

        if not handlers:
            return 0

        # ─── اجرای هندلرها ───
        success = 0
        for handler in handlers:
            hname = getattr(handler, "__name__", "?")
            t_h = time.monotonic()
            try:
                if _is_async_callable(handler):
                    # 🎯 FIX: timeout روی هر handler
                    await asyncio.wait_for(
                        handler(event),
                        timeout=_HANDLER_TIMEOUT_S,
                    )
                else:
                    handler(event)

                success += 1
                self._stats["total_handlers_called"] += 1

                elapsed_ms = (
                    time.monotonic() - t_h
                ) * 1000
                if elapsed_ms > _SLOW_HANDLER_MS:
                    self._stats["slow_handlers"] += 1
                    log_warning(
                        f"🐌 [EVENTBUS:handler] {event_name} "
                        f"→ {hname} | کند | "
                        f"{elapsed_ms:.0f}ms"
                    )
            except TimeoutError:
                self._stats["timeout_handlers"] += 1
                log_warning(
                    f"⏰ [EVENTBUS:handler] {event_name} → "
                    f"{hname} | timeout "
                    f"{_HANDLER_TIMEOUT_S}s"
                )
            except Exception as e:
                self._stats["errors"] += 1
                log_error(
                    f"❌ [EVENTBUS:handler] {event_name} → "
                    f"{hname} | {type(e).__name__}: {e}"
                )

        # ─── لاگ خلاصه ───
        total_ms = (time.monotonic() - t0) * 1000
        if total_ms > _SLOW_EMIT_MS:
            log_warning(
                f"🐌 [EVENTBUS:emit] {event_name} | "
                f"handlers={success}/{len(handlers)} | "
                f"{total_ms:.0f}ms"
            )
        elif _event_log_enabled():
            log_info(
                f"✅ [EVENTBUS:emit] {event_name} | "
                f"handlers={success}/{len(handlers)} | "
                f"{total_ms:.0f}ms"
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
        """انتشار fire-and-forget روی همون loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log_warning(
                f"⚠️ [EVENTBUS:emit_nowait] خارج از "
                f"loop فعال | {event_name} نادیده گرفته شد"
            )
            return None

        try:
            task = loop.create_task(
                self.emit(event_name, data, source, priority)
            )
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

        # 🐛 FIX: cancelled امن
        if task.cancelled():
            return

        try:
            exc = task.exception()
        except (asyncio.CancelledError, Exception):
            return

        if exc:
            log_error(
                f"❌ [EVENTBUS:task] exception | "
                f"{type(exc).__name__}: {exc}"
            )

    def get_pending_tasks_count(self) -> int:
        """تعداد taskهای در حال اجرا."""
        return len(self._pending_tasks)

    # ═══════════════════════════════════════════
    # emit_sync
    # ═══════════════════════════════════════════
    def emit_sync(
        self,
        event_name: str,
        data: Any,
        source: str | None = None,
    ) -> None:
        """انتشار رویداد از کد sync.

        اگر داخل loop فعال باشه، فقط هشدار می‌دهد.
        """
        try:
            asyncio.get_running_loop()
            log_warning(
                f"⚠️ [EVENTBUS:emit_sync] داخل loop فعال "
                f"صدا زده شد | {event_name} — از await emit "
                f"استفاده کن"
            )
            return
        except RuntimeError:
            pass

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                self.emit(event_name, data, source)
            )
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
        self,
        event_name: str,
        data: Any,
        source: str | None = None,
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
        """انتظار یک‌بارمصرف برای رویداد."""
        if not event_name:
            return None

        future: asyncio.Future = (
            asyncio.get_running_loop().create_future()
        )

        def handler(event: Event) -> None:
            try:
                if not future.done() and (
                    predicate is None or predicate(event)
                ):
                    future.set_result(event)
            except Exception as e:
                log_warning(
                    f"⚠️ [EVENTBUS:wait_for] predicate | "
                    f"{type(e).__name__}: {e}"
                )

        self.subscribe(
            event_name, handler,
            priority=EventPriority.HIGH,
        )

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
        except asyncio.CancelledError:
            # 🐛 FIX: cancel امن
            if not future.done():
                future.cancel()
            raise
        except Exception as e:
            log_warning(
                f"⚠️ [EVENTBUS:wait_for] {event_name} | "
                f"{type(e).__name__}: {e}"
            )
            return None
        finally:
            try:
                self.unsubscribe(event_name, handler)
            except Exception:
                pass

    # ═══════════════════════════════════════════
    # آمار و تاریخچه
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """آمار کامل رویدادها + تاریخچهٔ اخیر."""
        last_age_s: float | None = None
        if self._event_history:
            try:
                delta = (
                    datetime.now()
                    - self._event_history[-1].timestamp
                )
                last_age_s = round(
                    delta.total_seconds(), 1
                )
            except Exception:
                pass

        # 🐛 FIX: deep-copy events_by_name
        with self._lock:
            events_by_name = dict(
                self._stats.get("events_by_name", {})
            )
            active = {
                event: len(handlers)
                for event, handlers in self._handlers.items()
                if handlers
            }

        return {
            **{
                k: v for k, v in self._stats.items()
                if k != "events_by_name"
            },
            "events_by_name": events_by_name,
            "pending_tasks": len(self._pending_tasks),
            "last_event_age_s": last_age_s,
            "event_log_enabled": _event_log_enabled(),
            "active_handlers": active,
            "recent_events": [
                {
                    "name": e.name,
                    "timestamp": (
                        e.timestamp.isoformat()
                        if e.timestamp else None
                    ),
                    "source": e.source,
                    "priority": e.priority.name,
                    "id": e.id,
                }
                for e in self._event_history[-10:]
            ],
        }

    def get_event_history(
        self, limit: int = 50
    ) -> list[Event]:
        """تاریخچهٔ رویدادها (آخرین limit عدد)."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 50
        return list(self._event_history[-limit:])

    def get_event_names(self) -> list[str]:
        """لیست همهٔ رویدادهای ثبت‌شده — برای debug."""
        with self._lock:
            return sorted(self._handlers.keys())

    def get_handler_stats(self) -> dict[str, Any]:
        """🎯 آمار هندلرها per-event — برای debug."""
        with self._lock:
            return {
                event: [
                    {
                        "name": getattr(
                            h, "__name__",
                            repr(h)[:40],
                        ),
                        "priority": (
                            self._handler_priorities
                            .get(event, {})
                            .get(h, EventPriority.NORMAL)
                            .name
                        ),
                    }
                    for h in handlers
                ]
                for event, handlers in self._handlers.items()
                if handlers
            }

    def clear_history(self) -> int:
        """پاک کردن تاریخچه."""
        n = len(self._event_history)
        self._event_history.clear()
        log_info(
            f"🗑️ [EVENTBUS:clear_history] {n} رویداد پاک شد"
        )
        return n

    def clear_stats(self) -> None:
        """🎯 پاک کردن آمار — برای تست."""
        with self._lock:
            self._stats = {
                "total_events": 0,
                "total_handlers_called": 0,
                "errors": 0,
                "slow_handlers": 0,
                "timeout_handlers": 0,
                "events_by_name": {},
                "started_at": datetime.now().isoformat(),
            }
        log_info("🗑️ [EVENTBUS:clear_stats] آمار پاک شد")

    # ═══════════════════════════════════════════
    # مدیریت هندلرها
    # ═══════════════════════════════════════════
    def remove_all_handlers(
        self, event_name: str | None = None
    ) -> None:
        """حذف همهٔ هندلرها (یا فقط برای یک رویداد)."""
        with self._lock:
            if event_name:
                n = len(
                    self._handlers.get(event_name, [])
                )
                self._handlers[event_name] = []
                self._handler_priorities[event_name] = {}
                log_info(
                    f"🗑️ [EVENTBUS:remove_all] {event_name} | "
                    f"{n} هندلر حذف شد"
                )
            else:
                total = sum(
                    len(h)
                    for h in self._handlers.values()
                )
                n_events = len(self._handlers)
                self._handlers.clear()
                self._handler_priorities.clear()
                log_info(
                    f"🗑️ [EVENTBUS:remove_all] همه | "
                    f"{total} هندلر از {n_events} رویداد "
                    f"حذف شد"
                )


# ─── سازگاری عقب‌رو (deprecated) ───
EventBusOld = EventBus


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "EventBus",
    "Event",
    "EventPriority",
    "_event_log_enabled",
]
