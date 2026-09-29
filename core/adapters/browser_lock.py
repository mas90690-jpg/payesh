"""قفل سراسری مرورگر — فقط یک کلکتور در لحظه."""
import asyncio
from typing import Optional

_BROWSER_LOCK: Optional[asyncio.Lock] = None


def get_browser_lock() -> asyncio.Lock:
    """قفل را در event loop فعلی برمی‌گرداند (lazy)."""
    global _BROWSER_LOCK
    if _BROWSER_LOCK is None:
        _BROWSER_LOCK = asyncio.Lock()
    return _BROWSER_LOCK


class _BrowserLockProxy:
    """Proxy که با `async with BROWSER_LOCK:` سازگار است."""

    async def __aenter__(self):
        await get_browser_lock().acquire()
        return self

    async def __aexit__(self, *args):
        try:
            get_browser_lock().release()
        except Exception:
            pass


# برای سازگاری با import فعلی پروژه
BROWSER_LOCK = _BrowserLockProxy()