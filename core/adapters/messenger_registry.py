"""
messenger_registry — رجیستری سبک برای فرستنده‌های پیام.

هدف: handlers بتونن بدون وابستگی به collectors، پاسخ روی همون پلتفرمی که
پیام ازش اومده بفرستن.

استفاده در collector:
    from core import messenger_registry
    messenger_registry.register_sender("bale", self.send_message)

استفاده در handler:
    sender = messenger_registry.get_sender("bale")
    if sender:
        await sender(chat_id, chat_type, text)

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [MSGR:...]:
      * register_sender: کدوم پلتفرم ثبت شد (اولین‌بار / بازنویسی)
      * get_sender: فقط در سطح debug (پرتکراره)
      * notify_admins: هر admin جدا + شمارش موفق/ناموفق + timing
      * خطاها: type(e).__name__ + context (admin_id)
  - 🐛 FIX: except: pass → لاگ با type
  - 🐛 FIX: لاگ دلیل False برگردوندن notify_admins
  - 🎯 آستانهٔ کندی برای per-admin ارسال (3s)
  - 🎯 get_registered_platforms() + clear_registry() برای debug
  - 🧹 from __future__ import annotations
  - 🧹 type hints دقیق (Callable[..., Awaitable[Any]])
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── ثابت‌ها ───
_SLOW_SEND_MS = 3000   # هر ارسال > 3s = کند


# ─── رجیستری ───
# نوع sender: async (chat_id, chat_type, text) -> Any
_SenderFunc = Callable[..., Awaitable[Any]]
_senders: dict[str, _SenderFunc] = {}


# ═══════════════════════════════════════════
# register / get
# ═══════════════════════════════════════════
def register_sender(platform: str, sender: _SenderFunc) -> None:
    """ثبت فرستنده برای یک پلتفرم.

    اگه قبلاً ثبت شده باشه، بازنویسی می‌شه (با لاگ).

    Args:
        platform: نام پلتفرم (bale/eitaa/telegram/...).
        sender:   تابع async با امضای (chat_id, chat_type, text).
    """
    if not platform:
        log_warning("⚠️ [MSGR:register] platform خالیه — رد شد")
        return
    if sender is None:
        log_warning(
            f"⚠️ [MSGR:register] sender خالیه | platform={platform}"
        )
        return

    platform = str(platform).strip().lower()
    is_replace = platform in _senders

    _senders[platform] = sender

    try:
        func_name = getattr(sender, "__qualname__", None) or getattr(
            sender, "__name__", "?"
        )
    except Exception:
        func_name = "?"

    if is_replace:
        log_warning(
            f"♻️ [MSGR:register] بازنویسی | platform={platform} | "
            f"func={func_name}"
        )
    else:
        log_success(
            f"✅ [MSGR:register] {platform} | func={func_name} | "
            f"total={len(_senders)}"
        )


def get_sender(platform: str) -> _SenderFunc | None:
    """دریافت فرستندهٔ یک پلتفرم (یا None)."""
    if not platform:
        return None
    sender = _senders.get(str(platform).strip().lower())
    # سطح debug برای read پرتکرار — ولی لاگ نکن که نویز بشه
    return sender


def get_registered_platforms() -> list[str]:
    """لیست پلتفرم‌های ثبت‌شده — برای debug/health."""
    return sorted(_senders.keys())


def clear_registry() -> int:
    """پاک‌کردن همه فرستنده‌ها — برای تست.

    Returns:
        تعداد senderهای پاک‌شده.
    """
    count = len(_senders)
    _senders.clear()
    if count:
        log_info(f"🧹 [MSGR:clear] {count} sender پاک شد")
    return count


# ═══════════════════════════════════════════
# notify_admins
# ═══════════════════════════════════════════
async def notify_admins(text: str) -> bool:
    """ارسال پیام به همهٔ مدیران روی پلتفرمِ مدیریت (پیش‌فرض بله).

    در صورت نبود sender یا admin — لاگ می‌کند و False برمی‌گرداند.

    Args:
        text: متن پیام.

    Returns:
        True اگه حداقل به یک admin ارسال موفق شد.
    """
    t_all = time.monotonic()

    if not text:
        log_warning("⚠️ [MSGR:notify] متن خالی — رد شد")
        return False

    # ─── خواندن config ───
    try:
        from config import PLUGINS_CONFIG
    except Exception as e:
        log_error(
            f"❌ [MSGR:notify] import config | "
            f"{type(e).__name__}: {e}"
        )
        return False

    try:
        cfg = PLUGINS_CONFIG.get("adminhandler", {})
        ids = cfg.get("admin_ids", []) or []
        platform = cfg.get("platform", "bale")
    except Exception as e:
        log_error(
            f"❌ [MSGR:notify] خواندن PLUGINS_CONFIG | "
            f"{type(e).__name__}: {e}"
        )
        return False

    # ─── بررسی admin_ids ───
    if not ids:
        log_warning(
            "⚠️ [MSGR:notify] هیچ admin_ids تنظیم نشده — ارسال نشد"
        )
        return False

    # ─── بررسی sender ───
    sender = get_sender(platform)
    if not sender:
        log_warning(
            f"⚠️ [MSGR:notify] sender نیست برای platform={platform} | "
            f"registered={get_registered_platforms()}"
        )
        return False

    # ─── ارسال به هر admin ───
    sent = 0
    failed = 0
    total = len(ids)

    for aid in ids:
        t0 = time.monotonic()
        try:
            await sender(aid, "private", text)
            sent += 1
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_SEND_MS:
                log_warning(
                    f"🐌 [MSGR:notify] → {aid} کند | "
                    f"{elapsed_ms:.0f}ms"
                )
        except Exception as e:
            failed += 1
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_warning(
                f"⚠️ [MSGR:notify] → {aid} ناموفق | "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )

    total_ms = (time.monotonic() - t_all) * 1000
    ok = sent > 0

    if failed == 0:
        log_success(
            f"✅ [MSGR:notify] {sent}/{total} admin | "
            f"{total_ms:.0f}ms | platform={platform}"
        )
    elif sent > 0:
        log_warning(
            f"⚠️ [MSGR:notify] {sent}/{total} موفق، {failed} خطا | "
            f"{total_ms:.0f}ms | platform={platform}"
        )
    else:
        log_error(
            f"❌ [MSGR:notify] هیچ admin پیام نگرفت | "
            f"{failed} خطا | {total_ms:.0f}ms | platform={platform}"
        )

    return ok
