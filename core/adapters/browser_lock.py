"""قفل سراسری مرورگر — فقط یک کلکتور در لحظه.

چرا؟ چون Chrome/Playwright فقط یک instance در یک `user_data_dir`
می‌تواند داشته باشد. برای جلوگیری از تداخل بین کلکتورها (bale,
eitaa, telegram, whatsapp, linkedin, instagram, web) از این قفل
استفاده می‌شود.

استفاده:
    from core.adapters.browser_lock import BROWSER_LOCK

    async with BROWSER_LOCK:
        # فقط یک کلکتور اینجا اجرا می‌شود
        ...

    # یا با timeout:
    async with BROWSER_LOCK.acquire(timeout=30.0):
        ...

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: `_BrowserLockProxy` حالا `_held` را در contextvar
    نگه می‌دارد (نه در instance) — جلوگیری از overwrite در
    هم‌زمانی چند caller.
  - 🐛 FIX: `get_browser_lock` حالا per-event-loop است
    (dict بر اساس `id(loop)`) — جلوگیری از خطای «lock در
    loop دیگر».
  - 🐛 FIX: `get_browser_lock` حالا thread-safe است (lock).
  - 🐛 FIX: `_BrowserLockProxy.__aexit__` حالا در صورت
    `asyncio.CancelledError` هم lock را release می‌کند.
  - 🎯 `acquire(timeout=N)` متد جدید — برای sync acquire با
    timeout.
  - 🎯 `is_locked()`, `get_stats()`, `reset_browser_lock()`.
  - 🎯 type hints کامل.
  - 🎯 docstring واضح‌تر.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar
from typing import Any


# ═══════════════════════════════════════════
# state (per event-loop)
# ═══════════════════════════════════════════
_locks_by_loop: dict[int, asyncio.Lock] = {}
_locks_lock = threading.Lock()

# 🐛 FIX: نگه‌داری lock در contextvar (per-context)
# این کار از overwrite در هم‌زمانی چند caller جلوگیری می‌کند.
_held_lock: ContextVar[asyncio.Lock | None] = ContextVar(
    "_held_browser_lock", default=None
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _current_loop_id() -> int:
    """id لوپ فعلی (یا 0 اگر loop نیست)."""
    try:
        loop = asyncio.get_running_loop()
        return id(loop)
    except RuntimeError:
        return 0


def get_browser_lock() -> asyncio.Lock:
    """🎯 FIX: قفل را برای event loop فعلی برمی‌گرداند.

    - اگر برای این loop قفلی ساخته شده باشد، همان برگردانده
      می‌شود.
    - اگر نه، قفل جدید ساخته و cache می‌شود.

    این طراحی مشکل «lock در loop دیگر» را حل می‌کند.
    """
    loop_id = _current_loop_id()

    # ─── fast path (بدون lock) ───
    cached = _locks_by_loop.get(loop_id)
    if cached is not None:
        return cached

    # ─── slow path ───
    with _locks_lock:
        # double-check
        cached = _locks_by_loop.get(loop_id)
        if cached is not None:
            return cached

        new_lock = asyncio.Lock()
        _locks_by_loop[loop_id] = new_lock
        return new_lock


def reset_browser_lock() -> None:
    """🎯 پاک‌کردن همه قفل‌ها — فقط برای تست."""
    with _locks_lock:
        _locks_by_loop.clear()


def is_locked() -> bool:
    """🎯 آیا قفل در این loop گرفته شده است؟"""
    loop_id = _current_loop_id()
    with _locks_lock:
        lock = _locks_by_loop.get(loop_id)
    if lock is None:
        return False
    try:
        return lock.locked()
    except Exception:
        return False


def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    with _locks_lock:
        loops_count = len(_locks_by_loop)
        locks_info = {
            lid: lock.locked()
            for lid, lock in _locks_by_loop.items()
        }
    return {
        "loops_registered": loops_count,
        "locks": locks_info,
        "currently_held": bool(_held_lock.get()),
    }


# ═══════════════════════════════════════════
# _BrowserLockProxy
# ═══════════════════════════════════════════
class _BrowserLockProxy:
    """Proxy که با `async with BROWSER_LOCK:` سازگار است.

    🐛 FIX: reference به lock گرفته‌شده در **contextvar** نگه داشته
    می‌شود (نه در instance)، تا `__aexit__` روی همان lock عمل کند
    و در هم‌زمانی چند caller، overwrite نشود.

    ویژگی‌ها:
      - `async with BROWSER_LOCK:` → acquire + release
      - `async with BROWSER_LOCK.acquire(timeout=N):` → با timeout
      - `BROWSER_LOCK.locked()` → آیا گرفته شده؟
    """

    __slots__ = ("_timeout",)

    def __init__(self, timeout: float | None = None):
        self._timeout = timeout

    # ═══════════════════════════════════════════
    # acquire (با timeout)
    # ═══════════════════════════════════════════
    def acquire(
        self, timeout: float | None = None
    ) -> "_BrowserLockProxy":
        """🎯 برگرداندن proxy با timeout مشخص.

        استفاده:
            async with BROWSER_LOCK.acquire(timeout=30.0):
                ...

        Args:
            timeout: حداکثر زمان انتظار (ثانیه). None = بی‌نهایت.

        Returns:
            _BrowserLockProxy (خود را برای context manager).
        """
        return _BrowserLockProxy(timeout=timeout)

    # ═══════════════════════════════════════════
    # context manager
    # ═══════════════════════════════════════════
    async def __aenter__(self) -> "_BrowserLockProxy":
        lock = get_browser_lock()

        if self._timeout is not None:
            try:
                await asyncio.wait_for(
                    lock.acquire(),
                    timeout=self._timeout,
                )
            except TimeoutError:
                raise TimeoutError(
                    f"⏰ BROWSER_LOCK در {self._timeout}s آزاد نشد"
                )
        else:
            await lock.acquire()

        # 🐛 FIX: set در contextvar (نه instance)
        _held_lock.set(lock)
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        """🐛 FIX: release امن (حتی در cancel)."""
        lock = _held_lock.get()
        if lock is None:
            return
        try:
            if lock.locked():
                lock.release()
        except RuntimeError:
            # lock در loop دیگری — نادیده بگیر
            pass
        except Exception:
            pass
        finally:
            _held_lock.set(None)

    # ═══════════════════════════════════════════
    # status
    # ═══════════════════════════════════════════
    def locked(self) -> bool:
        """🎯 آیا قفل گرفته شده است؟"""
        return is_locked()

    def __repr__(self) -> str:
        state = "locked" if self.locked() else "free"
        return f"<BROWSER_LOCK {state}>"


# ═══════════════════════════════════════════
# Global instance
# ═══════════════════════════════════════════
BROWSER_LOCK = _BrowserLockProxy()


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "BROWSER_LOCK",
    "get_browser_lock",
    "reset_browser_lock",
    "is_locked",
    "get_stats",
    "_BrowserLockProxy",
]