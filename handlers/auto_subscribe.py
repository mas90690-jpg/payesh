"""
handlers/auto_subscribe.py — ثبت خودکار هندلرها در EventBus.

استراتژی اصلی (نسخهٔ ۲):
  - هر هندلر خودش می‌گه به چه رویدادهایی علاقه داره — از طریق can_handle()
  - اگه can_handle() برای همه رویدادهای شناخته‌شده False بده، از نام
    هندلر (fallback) استفاده می‌کنیم — برای سازگاری با هندلرهای قدیمی
  - فقط یک‌بار ثبت می‌شه (ضدتکراری)

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: can_handle() به‌عنوان منبع اصلی (نه نام هندلر)
  - 🐛 FIX: خطای subscribe دیگر بقیه هندلرها را متوقف نمی‌کند
  - 🐛 FIX: ضدتکراری — اگه register_all دوبار صدا زده بشه، هندلر دوبار ثبت نمی‌شه
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [SUBSCRIBE:...]:
      * register_all: تعداد هندلرها، هر هندلر با رویدادها و زمان
      * هر رویداد: handler_name → event_name
      * خطا: type + context
  - 🧹 BaseHandler import شد (استفاده می‌شه در type hints)
  - 🧹 _get_handler_events حالا عمومی‌تر است (از self._known_events استفاده می‌کند)
"""
from __future__ import annotations

import time

from core.contracts.base_handler import BaseHandler
from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── رویدادهای شناخته‌شده در سیستم ───
# اگه رویداد جدیدی اضافه شد، اینجا اضافه کن. auto_subscribe از همین لیست
# برای پرسیدن از can_handle() استفاده می‌کند.
_KNOWN_EVENTS: list[str] = [
    "message.received",
    "lead.saved",
    "error.occurred",
    "brain.command",
    "brain.response",
]

# ─── fallback: نام هندلر → رویدادها (فقط اگه can_handle کار نکرد) ───
_FALLBACK_BY_NAME = {
    "storage": ["message.received"],
    "admin": ["message.received"],
    "crm": ["lead.saved"],
    "log": ["error.occurred", "lead.saved"],
    "notify": ["lead.saved"],
    "scoring": ["message.received"],
}

_LOG_PREVIEW_LEN = 80


class AutoSubscriber:
    """ثبت خودکار هندلرها در EventBus — از can_handle() هر هندلر استفاده می‌کند."""

    def __init__(self, event_bus, plugin_loader):
        self.event_bus = event_bus
        self.plugin_loader = plugin_loader
        # ضدتکراری: نگه‌داری (handler_name, event_name)های ثبت‌شده
        self._subscribed: set[tuple] = set()

        log_info(
            "🔌 [SUBSCRIBE:init] AutoSubscriber ساخته شد | "
            f"known_events={len(_KNOWN_EVENTS)}"
        )

    # ═══════════════════════════════════════════
    # register_all
    # ═══════════════════════════════════════════
    async def register_all(self) -> int:
        """ثبت همه هندلرهای کشف‌شده. تعداد subscriptionهای موفق را برمی‌گرداند."""
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

        log_info(
            f"📋 [SUBSCRIBE:register] {len(handlers)} هندلر کشف شد | "
            f"names={[getattr(h, 'handler_name', '?') for h in handlers]}"
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
            f"subs={total_subs} errors={total_errors} dupes={skipped_dupe} | "
            f"{elapsed_ms:.0f}ms"
        )
        return total_subs

    # ═══════════════════════════════════════════
    # کشف رویدادها برای یک هندلر
    # ═══════════════════════════════════════════
    def _get_handler_events(self, handler: BaseHandler) -> list[str]:
        """رویدادهایی که این هندلر به آن‌ها علاقه دارد.

        ترتیب:
          1) can_handle(event) برای هر رویداد شناخته‌شده
          2) اگه هیچ‌کدام True نشد → fallback از نام هندلر
          3) اگه fallback هم شکست خورد → خالی (به register_all می‌گه)
        """
        hname = getattr(handler, "handler_name", "?")

        # ─── ۱) can_handle ───
        via_can_handle: list[str] = []
        for event in _KNOWN_EVENTS:
            try:
                if handler.can_handle(event):
                    via_can_handle.append(event)
            except Exception as e:
                log_warning(
                    f"⚠️ [SUBSCRIBE:events] can_handle({event}) برای "
                    f"{hname} خطا داد | {type(e).__name__}: {e}"
                )

        if via_can_handle:
            log_info(
                f"🎯 [SUBSCRIBE:events] {hname} | via can_handle | "
                f"events={via_can_handle}"
            )
            return via_can_handle

        # ─── ۲) fallback بر اساس نام ───
        hname_low = hname.lower()
        for keyword, events in _FALLBACK_BY_NAME.items():
            if keyword in hname_low:
                log_warning(
                    f"⚠️ [SUBSCRIBE:events] {hname} | can_handle هیچ رویدادی "
                    f"نگرفت → fallback: keyword={keyword!r} | events={events}"
                )
                return list(events)

        # ─── ۳) شکست ───
        log_warning(
            f"⚠️ [SUBSCRIBE:events] {hname} | نه can_handle نه fallback | "
            f"handler ثبت نمی‌شود"
        )
        return []

    # ═══════════════════════════════════════════
    # عیب‌یابی
    # ═══════════════════════════════════════════
    def get_subscriptions(self) -> list[tuple]:
        """لیست subscriptionهای ثبت‌شده — برای debug."""
        return sorted(self._subscribed)

    def clear(self):
        """پاک کردن حافظهٔ ضدتکراری — برای تست."""
        self._subscribed.clear()
        log_info("🗑️ [SUBSCRIBE:state] subscriptionها پاک شدند")
