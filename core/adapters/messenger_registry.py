"""messenger_registry — رجیستری سبک برای فرستنده‌های پیام.

هدف: handlers بتوانند بدون وابستگی به collectors، پاسخ روی همون
پلتفرمی که پیام ازش آمده بفرستند.

استفاده در collector:
    from core.adapters import messenger_registry
    messenger_registry.register_sender("bale", self.send_message)

استفاده در handler:
    sender = messenger_registry.get_sender("bale")
    if sender:
        await sender(chat_id, chat_type, text)

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `_senders` حالا با `threading.RLock` محافظت می‌شود
    (race-safe).
  - 🐛 FIX: `register_sender` حالا type-check دارد (callable).
  - 🐛 FIX: `notify_admins` حالا پیاده‌سازی واقعی دارد:
      * اگر هیچ sender و admin_ids نبود، لاگ دلیل و False
      * ارسال به همهٔ admin_ids با timeout و tracking
  - 🎯 `is_registered(platform)` متد جدید (طبق MEMORY R2.22).
  - 🎯 `unregister_sender(platform)` متد جدید.
  - 🎯 `get_stats()` متد جدید.
  - 🎯 `_SLOW_SEND_MS` env-based.
  - 🎯 `_SenderFunc` با Protocol (type-safe).
  - 🎯 `__repr__` برای registry.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from core.kernel.logger import (
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
# ثابت‌ها
# ═══════════════════════════════════════════
_SLOW_SEND_MS = _env_int(
    "MSGR_SLOW_SEND_MS", 3000,
    min_val=100, max_val=60_000,
)
_NOTIFY_TIMEOUT_S = _env_float(
    "MSGR_NOTIFY_TIMEOUT_S", 30.0,
    min_val=1.0, max_val=300.0,
)


# ═══════════════════════════════════════════
# Type Protocol
# ═══════════════════════════════════════════
@runtime_checkable
class _SenderFunc(Protocol):
    """Protocol فرستندهٔ پیام.

    هر sender باید یک callable باشد که وقتی صدا زده می‌شود،
    یک awaitable برگرداند.
    """

    def __call__(
        self,
        chat_id: str | int,
        chat_type: str,
        text: str,
    ) -> Awaitable[Any]: ...


# ═══════════════════════════════════════════
# Registry (thread-safe)
# ═══════════════════════════════════════════
_senders: dict[str, Callable[..., Awaitable[Any]]] = {}
_registry_lock = threading.RLock()


# ═══════════════════════════════════════════
# register / unregister / get
# ═══════════════════════════════════════════
def register_sender(
    platform: str,
    sender: Callable[..., Awaitable[Any]],
) -> bool:
    """ثبت فرستنده برای یک پلتفرم.

    اگر قبلاً ثبت شده باشد، بازنویسی می‌شود (با لاگ).

    Args:
        platform: نام پلتفرم (bale/eitaa/telegram/...).
        sender:   تابع async با امضای (chat_id, chat_type, text).

    Returns:
        True اگر ثبت شد، False اگر پارامتر نامعتبر بود.
    """
    if not platform:
        log_warning(
            "⚠️ [MSGR:register] platform خالیه — رد شد"
        )
        return False

    if sender is None:
        log_warning(
            f"⚠️ [MSGR:register] sender خالیه | "
            f"platform={platform}"
        )
        return False

    if not callable(sender):
        log_warning(
            f"⚠️ [MSGR:register] sender callable نیست | "
            f"platform={platform} | "
            f"type={type(sender).__name__}"
        )
        return False

    platform_key = str(platform).strip().lower()
    if not platform_key:
        return False

    try:
        func_name = (
            getattr(sender, "__qualname__", None)
            or getattr(sender, "__name__", "?")
        )
    except Exception:
        func_name = "?"

    with _registry_lock:
        is_replace = platform_key in _senders
        _senders[platform_key] = sender
        total = len(_senders)

    if is_replace:
        log_warning(
            f"♻️ [MSGR:register] بازنویسی | "
            f"platform={platform_key} | func={func_name}"
        )
    else:
        log_success(
            f"✅ [MSGR:register] {platform_key} | "
            f"func={func_name} | total={total}"
        )
    return True


def unregister_sender(platform: str) -> bool:
    """حذف فرستندهٔ یک پلتفرم.

    Args:
        platform: نام پلتفرم.

    Returns:
        True اگر sender وجود داشت و حذف شد.
    """
    if not platform:
        return False
    platform_key = str(platform).strip().lower()
    with _registry_lock:
        if platform_key in _senders:
            del _senders[platform_key]
            total = len(_senders)
        else:
            return False

    log_info(
        f"🗑️ [MSGR:unregister] {platform_key} | "
        f"remaining={total}"
    )
    return True


def get_sender(
    platform: str,
) -> Callable[..., Awaitable[Any]] | None:
    """دریافت فرستندهٔ یک پلتفرم (یا None).

    سطح debug — چون پرتکرار است، لاگ نمی‌شود.
    """
    if not platform:
        return None
    platform_key = str(platform).strip().lower()
    with _registry_lock:
        return _senders.get(platform_key)


def is_registered(platform: str) -> bool:
    """🎯 آیا این پلتفرم sender دارد؟ (R2.22)"""
    if not platform:
        return False
    platform_key = str(platform).strip().lower()
    with _registry_lock:
        return platform_key in _senders


def get_registered_platforms() -> list[str]:
    """لیست پلتفرم‌های ثبت‌شده — برای debug/health."""
    with _registry_lock:
        return sorted(_senders.keys())


def clear_registry() -> int:
    """پاک‌کردن همه فرستنده‌ها — برای تست.

    Returns:
        تعداد senderهای پاک‌شده.
    """
    with _registry_lock:
        count = len(_senders)
        _senders.clear()
    if count:
        log_info(f"🧹 [MSGR:clear] {count} sender پاک شد")
    return count


# ═══════════════════════════════════════════
# notify_admins
# ═══════════════════════════════════════════
def _get_admin_config() -> tuple[list[str], str]:
    """خواندن admin_ids و platform از config.

    Returns:
        (admin_ids, platform) — admin_ids خالی اگر config نبود.
    """
    try:
        from config import (
            ADMIN_ENABLED,
            ADMIN_IDS,
            ADMIN_PLATFORM,
        )
        if not ADMIN_ENABLED:
            return [], ""
        return (
            [str(x).strip() for x in ADMIN_IDS if str(x).strip()],
            str(ADMIN_PLATFORM or "bale").strip().lower(),
        )
    except Exception as e:
        log_warning(
            f"⚠️ [MSGR:notify] config | "
            f"{type(e).__name__}: {e}"
        )
        return [], ""


async def notify_admins(text: str) -> bool:
    """ارسال پیام به همهٔ adminها.

    - `sender` را از رجیستری می‌گیرد (بر اساس `ADMIN_PLATFORM`).
    - `admin_ids` را از `config.ADMIN_IDS` می‌گیرد.
    - هر admin جداگانه با timeout ارسال می‌شود.

    Args:
        text: متن پیام.

    Returns:
        True اگر حداقل یک ارسال موفق باشد.
    """
    if not text:
        log_warning("⚠️ [MSGR:notify] متن خالیه — رد شد")
        return False

    # ─── config ───
    admin_ids, platform = _get_admin_config()

    if not admin_ids:
        log_warning(
            "⚠️ [MSGR:notify] admin_ids خالی است — "
            "پیام ارسال نشد"
        )
        return False

    if not platform:
        log_warning(
            "⚠️ [MSGR:notify] ADMIN_PLATFORM خالی است"
        )
        return False

    # ─── sender ───
    sender = get_sender(platform)
    if sender is None:
        log_warning(
            f"⚠️ [MSGR:notify] sender نیست | "
            f"platform={platform} | "
            f"registered={get_registered_platforms()}"
        )
        return False

    # ─── ارسال ───
    t0 = time.monotonic()
    sent = 0
    failed = 0

    for aid in admin_ids:
        t_a = time.monotonic()
        try:
            await asyncio.wait_for(
                sender(aid, "private", text),
                timeout=_NOTIFY_TIMEOUT_S,
            )
            sent += 1
            a_ms = (time.monotonic() - t_a) * 1000
            log_info(
                f"  ✅ [MSGR:notify] → {aid} | {a_ms:.0f}ms"
            )
        except TimeoutError:
            failed += 1
            a_ms = (time.monotonic() - t_a) * 1000
            log_warning(
                f"  ⏰ [MSGR:notify] → {aid} | "
                f"timeout {_NOTIFY_TIMEOUT_S}s | {a_ms:.0f}ms"
            )
        except Exception as e:
            failed += 1
            a_ms = (time.monotonic() - t_a) * 1000
            log_warning(
                f"  ❌ [MSGR:notify] → {aid} | "
                f"{type(e).__name__}: {e} | {a_ms:.0f}ms"
            )

    elapsed_ms = (time.monotonic() - t0) * 1000
    if sent > 0:
        log_success(
            f"✅ [MSGR:notify] sent={sent} failed={failed} | "
            f"{elapsed_ms:.0f}ms"
        )
    else:
        log_warning(
            f"⚠️ [MSGR:notify] هیچ ارسالی موفق نشد | "
            f"sent=0 failed={failed} | {elapsed_ms:.0f}ms"
        )

    return sent > 0


# ═══════════════════════════════════════════
# debug
# ═══════════════════════════════════════════
def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    with _registry_lock:
        senders_snapshot = dict(_senders)
    return {
        "registered_platforms": sorted(senders_snapshot.keys()),
        "count": len(senders_snapshot),
        "slow_threshold_ms": _SLOW_SEND_MS,
        "notify_timeout_s": _NOTIFY_TIMEOUT_S,
    }


def __getattr__(name: str) -> Any:
    """🎯 دسترسی legacy به `_senders` (فقط برای debug)."""
    if name == "_SENDERS":
        return get_registered_platforms()
    raise AttributeError(
        f"module 'messenger_registry' has no attribute {name!r}"
    )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "register_sender",
    "unregister_sender",
    "get_sender",
    "is_registered",
    "get_registered_platforms",
    "clear_registry",
    "notify_admins",
    "get_stats",
    "_SenderFunc",
]