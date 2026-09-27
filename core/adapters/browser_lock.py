"""قفل سراسری مرورگر — فقط یک کلکتور در لحظه."""
import asyncio

BROWSER_LOCK = asyncio.Lock()