"""
handlers/auto_subscribe.py — ثبت خودکار هندلرها در EventBus.

استراتژی اصلی (نسخهٔ ۴ — داده‌محور):
  - هر هندلر خودش می‌گه به چه رویدادهایی علاقه داره — از طریق can_handle()
  - اگه can_handle() برای همه رویدادهای شناخته‌شده False بده، از نام
    هندلر (fallback) استفاده می‌کنیم — برای سازگاری با هندلرهای قدیمی
  - فقط یک‌بار ثبت می‌شه (ضدتکراری)

🎯 داده‌محور:
  - `data/configs/events.json`:
      * known             → لیست رویدادهای شناخته‌شده
      * fallback_by_name  → نگاشت نام هندلر → رویدادها

نسخهٔ ۴ (این نسخه):
  - 🎯 `_KNOWN_EVENTS` و `_FALLBACK_BY_NAME` حذف شدند از top-level.
  - 🎯 حالا `self._known_events` و `self._fallback_by_name` از
    `events.json` بارگذاری می‌شوند.
  - 🐛 FIX: اگر فایل events.json نبود → مقادیر پیش‌فرض امن استفاده می‌شود
    (تا subscribe کاملاً از کار نیفتد).
  - 🐛 FIX: `get_known_events()` حالا `self._known_events` برمی‌گرداند
    (نه متغیر ماژول حذف‌شده).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳ (can_handle به‌عنوان منبع اصلی،
    ضدتکراری، خطای subscribe بقیه را متوقف نمی‌کند).
"""
from __future__ import annotations

import time
from typing import Any

from core.contracts.base_handler import BaseHandler, HandlerPriority
from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 داده‌محور
from data.loader import load_json


# ═══════════════════════════════════════════════════════════
# helpers — داده‌محور
# ═══════════════════════════════════════════════════════════
# مقادیر پیش‌فرض (اگر events.json نبود یا خراب بود)
_DEFAULT_KNOWN_EVENTS: tuple[str, ...] = (
    "message.received",
    "lead.saved",
    "error.occurred",
)

_DEFAULT_FALLBACK_BY_NAME: dict[str, list[str]] = {
    "storage": ["message.received"],
    "admin": ["message.received"],
    "crm": ["lead.saved"],
    "log": ["error.occurred", "lead.saved"],
    "notify": ["lead.saved"],
    "scoring": ["message.received"],
    "telegram": ["message.received"],
    "whatsapp": ["message.received"],
    "instagram": ["message.received"],
    "lead": ["lead.saved"],
    "error": ["error.occurred"],
}


def _get_events_data() -> dict:
    """بارگذاری events.json — با fallback امن."""
    return load_json("events")


def _load_known_events() -> tuple[str, ...]:
    """خواندن known از events.json — با sanitize."""
    data = _get_events_data()
    raw = data.get("known")

    if not isinstance(raw, list) or not raw:
        return _DEFAULT_KNOWN_EVENTS

    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s:
            out.append(s)

    return tuple(out) if out else _DEFAULT_KNOWN_EVENTS


def _load_fallback_by_name() -> dict[str, list[str]]:
    """خواندن fallback_by_name از events.json — با sanitize."""
    data = _get_events_data()
    raw = data.get("fallback_by_name")

    if not isinstance(raw, dict) or not raw:
        return dict(_DEFAULT_FALLBACK_BY_NAME)

    out: dict[str, list[str]] = {}
    for keyword, events in raw.items():
        k = str(keyword).strip().lower()
        if not k:
            continue

        if isinstance(events, list):
            evs = [
                str(e).strip()
                for e in events
                if str(e).strip()
            ]
            if evs:
                out[k] = evs

    return out if out else dict(_DEFAULT_FALLBACK_BY_NAME)


_LOG_PREVIEW_LEN = 80


# ═══════════════════════════════════════════════════════════
# AutoSubscriber
# ═══════════════════════════════════════════════════════════
class AutoSubscriber:
    """ثبت خودکار هندلرها در EventBus — داده‌محور."""

    def __init__(self, event_bus, plugin_loader):
        self.event_bus = event_bus
        self.plugin_loader = plugin_loader

        # 🎯 داده‌محور — بارگذاری از events.json
        self._known_events: tuple[str, ...] = _load_known_events()
        self._fallback_by_name: dict[str, list[str]] = (
            _load_fallback_by_name()
        )

        # ضدتکراری: نگه‌داری (handler_name, event_name)های ثبت‌شده
        self._subscribed: set[tuple[str, str]] = set()

        log_info(
            f"🔌 [SUBSCRIBE:init] AutoSubscriber ساخته شد | "
            f"known_events={len(self._known_events)} | "
            f"fallback_names={len(self._fallback_by_name)} | "
            f"events={list(self._known_events)}"
        )

    # ═══════════════════════════════════════════
    # register_all
    # ═══════════════════════════════════════════
    async def register_all(self) -> int:
        """ثبت همه هندلرهای کشف‌شده.

        Returns:
            تعداد subscriptionهای موفق.
        """
        t0 = time.monotonic()
        log_info("▶️ [SUBSCRIBE:register] شروع | discover_handlers()...")

        try:
            handlers = self.plugin_loader.discover_handlers()
        except Exception as e:
            log_error(
                f"❌ [SUBSCRIBE:register] discover_handlers | "
                f"{type(e).__name__}: {e}"
            )
            return 0

        if not handlers:
            log_warning("⚠️ [SUBSCRIBE:register] هیچ هندلری کشف نشد")
            return 0

        # ─── مرتب‌سازی بر اساس priority (بالاترین اول) ───
        try:
            handlers = sorted(
                handlers,
                key=lambda h: self._get_priority_value(h),
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SUBSCRIBE:register] sort by priority | "
                f"{type(e).__name__}: {e}"
            )

        log_info(
            f"📋 [SUBSCRIBE:register] {len(handlers)} هندلر کشف شد | "
            f"names={[getattr(h, 'handler_name', '?') for h in handlers]} | "
            f"priorities={[self._get_priority_value(h) for h in handlers]}"
        )

        total_subs = 0
        total_errors = 0
        skipped_dupe = 0

        for handler in handlers:
            hname = getattr(handler, "handler_name", "?")
            t_h = time.monotonic()

            # پیدا کردن رویدادها
            try:
                events = self._get_handler_events(handler)
            except Exception as e:
                log_error(
                    f"❌ [SUBSCRIBE:register] _get_handler_events | "
                    f"handler={hname} | {type(e).__name__}: {e}"
                )
                total_errors += 1
                continue

            if not events:
                log_warning(
                    f"⚠️ [SUBSCRIBE:register] هیچ رویدادی برای "
                    f"{hname} پیدا نشد — رد شد"
                )
                continue

            # subscribe هر رویداد
            handler_subs = 0
            for event in events:
                # ضدتکراری
                key = (hname, event)
                if key in self._subscribed:
                    skipped_dupe += 1
                    log_info(
                        f"⏭️ [SUBSCRIBE:register] تکراری | "
                        f"{hname} → {event}"
                    )
                    continue

                try:
                    self.event_bus.subscribe(event, handler.handle)
                    self._subscribed.add(key)
                    handler_subs += 1
                    total_subs += 1
                    log_info(
                        f"  ✅ {hname} → {event}"
                    )
                except Exception as e:
                    log_error(
                        f"❌ [SUBSCRIBE:register] subscribe | "
                        f"{hname} → {event} | "
                        f"{type(e).__name__}: {e}"
                    )
                    total_errors += 1

            elapsed_h_ms = (time.monotonic() - t_h) * 1000
            if handler_subs > 0:
                log_success(
                    f"✅ [SUBSCRIBE:register] {hname} | "
                    f"events={handler_subs} | {elapsed_h_ms:.0f}ms"
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"🏁 [SUBSCRIBE:register] کامل | "
            f"subs={total_subs} errors={total_errors} "
            f"dupes={skipped_dupe} | {elapsed_ms:.0f}ms"
        )
        return total_subs

    # ═══════════════════════════════════════════
    # کمک: priority
    # ═══════════════════════════════════════════
    @staticmethod
    def _get_priority_value(handler: Any) -> int:
        """استخراج مقدار priority از handler.

        ترتیب اولویت: CRITICAL=0 < HIGH=10 < NORMAL=20 < LOW=30
        → هرچه کمتر، اول اجرا می‌شود.
        """
        try:
            prio = getattr(handler, "priority", None)
            if isinstance(prio, HandlerPriority):
                return prio.value
            if isinstance(prio, int):
                return prio
        except Exception:
            pass
        # fallback: NORMAL
        return HandlerPriority.NORMAL.value

    # ═══════════════════════════════════════════
    # کشف رویدادها برای یک هندلر
    # ═══════════════════════════════════════════
    def _get_handler_events(self, handler: BaseHandler) -> list[str]:
        """رویدادهایی که این هندلر به آن‌ها علاقه دارد.

        ترتیب اولویت:
          ۱) `handler.listens_to()` اگر تعریف شده باشد (رویدادهای پویا)
          ۲) `can_handle(event)` برای هر رویداد شناخته‌شده
          ۳) fallback از نام هندلر (compatibility)
          ۴) خالی (handler ثبت نمی‌شود)

        Returns:
            لیست رویدادها.
        """
        hname = getattr(handler, "handler_name", "?")

        # ─── ۱) listens_to (رویدادهای پویا) ───
        listens_to = getattr(handler, "listens_to", None)
        if callable(listens_to):
            try:
                dynamic_events = listens_to()
                if dynamic_events and isinstance(
                    dynamic_events, (list, tuple, set)
                ):
                    events_list = [
                        str(e) for e in dynamic_events if e
                    ]
                    if events_list:
                        log_info(
                            f"🎯 [SUBSCRIBE:events:dynamic] {hname} | "
                            f"via listens_to() | events={events_list}"
                        )
                        return events_list
            except Exception as e:
                log_warning(
                    f"⚠️ [SUBSCRIBE:events:dynamic] {hname} | "
                    f"listens_to() خطا | {type(e).__name__}: {e}"
                )

        # ─── ۲) can_handle ───
        via_can_handle: list[str] = []
        for event in self._known_events:
            try:
                if handler.can_handle(event):
                    via_can_handle.append(event)
            except Exception as e:
                log_warning(
                    f"⚠️ [SUBSCRIBE:events:can_handle] {hname} | "
                    f"event={event} | {type(e).__name__}: {e}"
                )

        if via_can_handle:
            log_info(
                f"🎯 [SUBSCRIBE:events:can_handle] {hname} | "
                f"events={via_can_handle}"
            )
            return via_can_handle

        # ─── ۳) fallback بر اساس نام ───
        hname_low = hname.lower()
        for keyword, events in self._fallback_by_name.items():
            if keyword in hname_low:
                log_warning(
                    f"⚠️ [SUBSCRIBE:events:fallback] {hname} | "
                    f"can_handle خالی | keyword={keyword!r} | "
                    f"events={events}"
                )
                return list(events)

        # ─── ۴) شکست ───
        log_warning(
            f"⚠️ [SUBSCRIBE:events:none] {hname} | "
            f"نه can_handle نه fallback | handler ثبت نمی‌شود"
        )
        return []

    # ═══════════════════════════════════════════
    # عیب‌یابی
    # ═══════════════════════════════════════════
    def get_subscriptions(self) -> list[tuple[str, str]]:
        """لیست subscriptionهای ثبت‌شده — برای debug."""
        return sorted(self._subscribed)

    def get_subscription_count(self) -> int:
        """تعداد subscriptionهای ثبت‌شده."""
        return len(self._subscribed)

    def is_subscribed(self, handler_name: str, event: str) -> bool:
        """آیا این (handler, event) قبلاً ثبت شده؟"""
        return (handler_name, event) in self._subscribed

    def clear(self) -> int:
        """پاک کردن حافظهٔ ضدتکراری — برای تست.

        Returns:
            تعداد subscriptionهایی که پاک شدند.
        """
        count = len(self._subscribed)
        self._subscribed.clear()
        log_info(f"🗑️ [SUBSCRIBE:state] {count} subscription پاک شد")
        return count

    def get_known_events(self) -> list[str]:
        """لیست رویدادهای شناخته‌شده — برای debug."""
        return list(self._known_events)

    def get_fallback_names(self) -> list[str]:
        """لیست نام‌های fallback — برای debug."""
        return sorted(self._fallback_by_name.keys())

    def get_stats(self) -> dict:
        """خلاصهٔ وضعیت — برای debug."""
        return {
            "known_events": list(self._known_events),
            "known_events_count": len(self._known_events),
            "fallback_names": sorted(self._fallback_by_name.keys()),
            "fallback_names_count": len(self._fallback_by_name),
            "subscribed_count": len(self._subscribed),
            "subscribed": sorted(self._subscribed),
        }