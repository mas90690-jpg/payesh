"""رفتار انسانی — تأخیر، تایپ تدریجی، اسکرول، حرکت موس.

این ماژول باعث می‌شه ربات مثل یک کاربر واقعی رفتار کنه،
نه مثل یک اسکریپت خشک.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from core.kernel.logger import log_info, log_debug

log_info("✅ [human_behavior] بارگذاری شد")


class HumanBehavior:
    """شبیه‌سازی رفتار کاربر واقعی."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    async def delay(self, min_s: float = 1.5, max_s: float = 5.0) -> None:
        """تأخیر تصادفی — مثل انسان که قبل از کلیک صبر می‌کنه."""
        await asyncio.sleep(self.rng.uniform(min_s, max_s))

    async def type_slowly(
        self, page: Any, text: str,
        base_delay: float = 0.08,
        mistake_chance: float = 0.02,
    ) -> None:
        """تایپ کاراکتر به کاراکتر — با اشتباه گاهی (مثل انسان)."""
        for ch in text:
            await page.keyboard.type(ch)
            await asyncio.sleep(
                self.rng.uniform(base_delay * 0.5, base_delay * 2.0)
            )
            # گاهی اشتباه بزن و پاک کن (مثل انسان)
            if self.rng.random() < mistake_chance:
                wrong = self.rng.choice("آابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")
                await page.keyboard.type(wrong)
                await asyncio.sleep(0.1)
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.1)

    async def move_mouse_naturally(
        self, page: Any, x: int, y: int,
        steps: int = 8,
    ) -> None:
        """حرکت موس با میانی‌های تصادفی (Bezier ساده)."""
        start_x, start_y = 0, 0
        for i in range(steps):
            t = i / steps
            cx = start_x + (x - start_x) * t
            cy = start_y + (y - start_y) * t
            jitter_x = self.rng.randint(-8, 8)
            jitter_y = self.rng.randint(-8, 8)
            await page.mouse.move(cx + jitter_x, cy + jitter_y)
            await asyncio.sleep(self.rng.uniform(0.01, 0.04))

    async def scroll_naturally(
        self, page: Any, times: int = 3,
        min_px: int = 200, max_px: int = 600,
    ) -> None:
        """اسکرول طبیعی — مثل کاربر که محتوا رو می‌خونه."""
        for _ in range(times):
            dy = self.rng.randint(min_px, max_px)
            await page.mouse.wheel(0, dy)
            await asyncio.sleep(self.rng.uniform(0.4, 1.5))

    async def read_pause(self, page: Any, seconds: float = 4.0) -> None:
        """وقفهٔ خواندن — مثل کاربر که محتوا رو می‌خونه."""
        await asyncio.sleep(self.rng.uniform(seconds, seconds * 1.8))

    async def click_human(self, page: Any, selector: str) -> bool:
        """کلیک انسانی: موس برو، تأخیر، کلیک."""
        try:
            el = await page.query_selector(selector)
            if not el:
                return False
            box = await el.bounding_box()
            if not box:
                return False
            cx = int(box["x"] + box["width"] / 2)
            cy = int(box["y"] + box["height"] / 2)
            await self.move_mouse_naturally(page, cx, cy)
            await asyncio.sleep(self.rng.uniform(0.05, 0.2))
            await page.mouse.click(cx, cy)
            return True
        except Exception as exc:
            log_debug(f'⚠️ {type(exc).__name__}: {exc}')
            return False
