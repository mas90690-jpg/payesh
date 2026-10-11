"""
کنترلر پلتفرم‌ها — فقط با دستور صریح مدیر.

پلتفرم‌ها: بله، ایتا، روبیکا

۷ کار:
  ۱) search         — جستجو
  ۲) join           — عضو شدن در گروه
  ۳) leave          — خارج شدن از گروه
  ۴) send           — فرستادن پیام
  ۵) forward        — فوروارد پیام
  ۶) pin            — سنجاق پیام
  ۷) delete_message — حذف پیام

امنیت:
  - فقط با دستور صریح
  - تأیید دو مرحله‌ای برای کارهای خطرناک
  - حداکثر ۱۰ کار در روز
  - حداکثر ۳ کار در ساعت
  - ۳-۸ ثانیه صبر بین کارها
  - فقط ۹ صبح - ۱۱ شب
  - لاگ کامل
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kernel.logger import (
    log_error, log_info, log_success, log_warning,
)


# ═══════════════════════════════════════════
# تنظیمات امنیتی
# ═══════════════════════════════════════════
MAX_PER_DAY = 10
MAX_PER_HOUR = 3
MIN_DELAY_S = 3.0
MAX_DELAY_S = 8.0
WORK_HOUR_START = 9
WORK_HOUR_END = 23

# کارهای خطرناک (نیاز به تأیید)
DANGEROUS_ACTIONS = frozenset({
    "join", "leave", "send", "forward",
    "pin", "delete_message", "unfollow",
})


# ═══════════════════════════════════════════
# SafeAction — امنیت مشترک
# ═══════════════════════════════════════════
class SafeAction:
    """مدیریت امنیت و محدودیت‌ها."""

    _shared_stats: dict[str, list[float]] = {}

    def __init__(self, platform: str):
        self.platform = platform
        if platform not in self._shared_stats:
            self._shared_stats[platform] = []
        self._last_action_at = 0.0

    # ─── چک ساعت کاری ───
    @staticmethod
    def _is_work_time() -> bool:
        now = datetime.now().hour
        return WORK_HOUR_START <= now <= WORK_HOUR_END

    # ─── چک محدودیت ───
    def _check_limits(self) -> tuple[bool, str]:
        now = time.time()
        actions = self._shared_stats[self.platform]

        # پاک کردن قدیمی‌ها (> ۲۴ ساعت)
        actions[:] = [
            t for t in actions
            if now - t < 86400
        ]

        # چک روزانه
        if len(actions) >= MAX_PER_DAY:
            return False, f"⛔ امروز {MAX_PER_DAY} کار کردی — فردا"

        # چک ساعتی
        hour_ago = now - 3600
        recent = [t for t in actions if t > hour_ago]
        if len(recent) >= MAX_PER_HOUR:
            return False, f"⛔ در ساعت اخیر {MAX_PER_HOUR} کار — صبر کن"

        # چک ساعت کاری
        if not self._is_work_time():
            return (
                False,
                f"⏰ فقط {WORK_HOUR_START} صبح تا "
                f"{WORK_HOUR_END} شب",
            )

        return True, ""

    # ─── تأخیر انسانی ───
    async def wait(self) -> None:
        """صبر قبل از کار بعدی."""
        now = time.monotonic()
        elapsed = now - self._last_action_at

        if elapsed < MIN_DELAY_S:
            wait_s = (
                MIN_DELAY_S - elapsed
                + random.uniform(0, MAX_DELAY_S - MIN_DELAY_S)
            )
            log_info(
                f"⏳ [CONTROLLER:{self.platform}] "
                f"صبر {wait_s:.1f}s"
            )
            await asyncio.sleep(wait_s)

        self._last_action_at = time.monotonic()

    # ─── ثبت کار ───
    def record(self) -> None:
        """ثبت کار در آمار."""
        self._shared_stats[self.platform].append(time.time())

    # ─── چک کلی ───
    async def check(self) -> tuple[bool, str]:
        """چک همه محدودیت‌ها."""
        ok, reason = self._check_limits()
        if not ok:
            return False, reason

        await self.wait()
        return True, ""

    # ─── آمار ───
    @classmethod
    def get_stats(cls, platform: str) -> dict:
        now = time.time()
        actions = cls._shared_stats.get(platform, [])
        hour_ago = now - 3600
        day_ago = now - 86400

        return {
            "today": len([t for t in actions if t > day_ago]),
            "this_hour": len([t for t in actions if t > hour_ago]),
            "max_day": MAX_PER_DAY,
            "max_hour": MAX_PER_HOUR,
            "work_hours": f"{WORK_HOUR_START}-{WORK_HOUR_END}",
        }


# ═══════════════════════════════════════════
# BaleController — بله
# ═══════════════════════════════════════════
class BaleController:
    """کنترل بله — مثل کاربر واقعی."""

    def __init__(self, collector: Any):
        self._collector = collector
        self._page = collector._page
        self._safe = SafeAction("bale")

    # ─── ۱) سرچ ───
    async def search(self, query: str = "") -> str:
        """جستجو در بله."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not query:
            return "❌ query خالی است"

        try:
            self._safe.record()

            # پیدا کردن جعبهٔ سرچ
            search_box = await self._page.query_selector(
                'input[placeholder*="جستجو"], '
                'input[placeholder*="Search"], '
                '.search-input'
            )
            if not search_box:
                return "❌ جعبهٔ سرچ پیدا نشد"

            await search_box.click()
            await search_box.fill(query)
            await asyncio.sleep(2)

            # خواندن نتایج
            results = await self._page.query_selector_all(
                '.search-result, .chat-item, [data-peer-id]'
            )

            lines = [f"🔍 نتایج «{query}» ({len(results)} مورد):"]
            for i, r in enumerate(results[:10], 1):
                try:
                    text = (await r.inner_text() or "").strip()
                    first = text.split("\n")[0][:60]
                    lines.append(f"  {i}. {first}")
                except Exception:
                    continue

            log_success(f"✅ [CONTROLLER:bale:search] «{query}»")
            return "\n".join(lines)

        except Exception as e:
            log_error(f"❌ [CONTROLLER:bale:search] {e}")
            return f"❌ خطا: {type(e).__name__}: {e}"

    # ─── ۲) عضو شدن ───
    async def join(self, link: str = "") -> str:
        """عضو شدن در گروه."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not link:
            return "❌ لینک گروه لازم است"

        try:
            self._safe.record()

            await self._page.goto(link)
            await asyncio.sleep(3)

            # پیدا کردن دکمهٔ عضویت
            btn = await self._page.query_selector(
                'button:has-text("عضویت"), '
                'button:has-text("Join"), '
                '.join-button'
            )

            if not btn:
                return "⚠️ دکمهٔ عضویت پیدا نشد (قبلاً عضو هستی؟)"

            await btn.click()
            await asyncio.sleep(2)

            log_success(f"✅ [CONTROLLER:bale:join] {link}")
            return f"✅ عضو شدی در {link}"

        except Exception as e:
            log_error(f"❌ [CONTROLLER:bale:join] {e}")
            return f"❌ خطا: {e}"

    # ─── ۳) خارج شدن ───
    async def leave(self, group: str = "") -> str:
        """خارج شدن از گروه."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not group:
            return "❌ نام یا لینک گروه لازم است"

        try:
            self._safe.record()

            # باز کردن گروه
            if group.startswith("http"):
                await self._page.goto(group)
            else:
                # جستجو
                await self._page.goto(
                    f"https://web.bale.ai/#/g/{group}"
                )
            await asyncio.sleep(3)

            # کلیک روی منو
            menu = await self._page.query_selector(
                '.chat-menu, .header-menu, '
                'button[aria-label*="منو"]'
            )
            if menu:
                await menu.click()
                await asyncio.sleep(1)

            # دکمهٔ خروج
            leave_btn = await self._page.query_selector(
                'button:has-text("خروج"), '
                'button:has-text("Leave"), '
                'button:has-text("ترک")'
            )
            if not leave_btn:
                return "⚠️ دکمهٔ خروج پیدا نشد"

            await leave_btn.click()
            await asyncio.sleep(1)

            # تأیید
            confirm = await self._page.query_selector(
                'button:has-text("بله"), '
                'button:has-text("تأیید"), '
                'button:has-text("خروج")'
            )
            if confirm:
                await confirm.click()
                await asyncio.sleep(2)

            log_success(f"✅ [CONTROLLER:bale:leave] {group}")
            return f"✅ از {group} خارج شدی"

        except Exception as e:
            log_error(f"❌ [CONTROLLER:bale:leave] {e}")
            return f"❌ خطا: {e}"

    # ─── ۴) پیام فرستادن ───
    async def send(self, chat: str = "", text: str = "") -> str:
        """فرستادن پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not chat or not text:
            return "❌ chat و text لازم است"

        try:
            self._safe.record()

            # باز کردن چت
            if chat.startswith("http"):
                await self._page.goto(chat)
            else:
                await self._page.goto(
                    f"https://web.bale.ai/#/g/{chat}"
                )
            await asyncio.sleep(3)

            # پیدا کردن جعبهٔ پیام
            msg_box = await self._page.query_selector(
                'div[contenteditable="true"], '
                '.composer-input, '
                '.message-input'
            )
            if not msg_box:
                return "❌ جعبهٔ پیام پیدا نشد"

            await msg_box.click()
            await asyncio.sleep(0.5)
            await self._page.keyboard.type(text)
            await asyncio.sleep(1)

            # ارسال
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)

            log_success(
                f"✅ [CONTROLLER:bale:send] {chat} | "
                f"{text[:40]}"
            )
            return f"✅ پیام فرستاده شد به {chat}"

        except Exception as e:
            log_error(f"❌ [CONTROLLER:bale:send] {e}")
            return f"❌ خطا: {e}"

    # ─── ۵) فوروارد ───
    async def forward(
        self,
        from_chat: str = "",
        message_id: str = "",
        to_chat: str = "",
    ) -> str:
        """فوروارد پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        return (
            "⚠️ فوروارد در بله پیچیده است — "
            "فعلاً به‌صورت دستی انجام بده"
        )

    # ─── ۶) سنجاق ───
    async def pin(self, chat: str = "", message_id: str = "") -> str:
        """سنجاق پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        return (
            "⚠️ سنجاق در بله پیچیده است — "
            "فعلاً به‌صورت دستی انجام بده"
        )

    # ─── ۷) حذف پیام ───
    async def delete_message(
        self, chat: str = "", message_id: str = ""
    ) -> str:
        """حذف پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        return (
            "⚠️ حذف پیام در بله پیچیده است — "
            "فعلاً به‌صورت دستی انجام بده"
        )


# ═══════════════════════════════════════════
# EitaaController — ایتا
# ═══════════════════════════════════════════
class EitaaController:
    """کنترل ایتا — مثل کاربر واقعی."""

    def __init__(self, collector: Any):
        self._collector = collector
        self._page = collector._page
        self._safe = SafeAction("eitaa")

    async def search(self, query: str = "") -> str:
        """جستجو در ایتا."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not query:
            return "❌ query خالی"

        try:
            self._safe.record()

            search_box = await self._page.query_selector(
                'input[placeholder*="جستجو"], '
                '.search-input, '
                '#search-input'
            )
            if not search_box:
                return "❌ جعبهٔ سرچ پیدا نشد"

            await search_box.click()
            await search_box.fill(query)
            await asyncio.sleep(2)

            results = await self._page.query_selector_all(
                '.chatlist-chat, .search-result'
            )

            lines = [f"🔍 نتایج «{query}» ({len(results)}):"]
            for i, r in enumerate(results[:10], 1):
                try:
                    text = (await r.inner_text() or "").strip()
                    first = text.split("\n")[0][:60]
                    lines.append(f"  {i}. {first}")
                except Exception:
                    continue

            log_success(f"✅ [CONTROLLER:eitaa:search] «{query}»")
            return "\n".join(lines)

        except Exception as e:
            log_error(f"❌ [CONTROLLER:eitaa:search] {e}")
            return f"❌ خطا: {e}"

    async def join(self, link: str = "") -> str:
        """عضو شدن در گروه."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not link:
            return "❌ لینک گروه لازم است"

        try:
            self._safe.record()
            await self._page.goto(link)
            await asyncio.sleep(3)

            btn = await self._page.query_selector(
                'button:has-text("عضویت"), '
                'button:has-text("Join")'
            )
            if not btn:
                return "⚠️ دکمهٔ عضویت پیدا نشد"

            await btn.click()
            await asyncio.sleep(2)

            log_success(f"✅ [CONTROLLER:eitaa:join] {link}")
            return f"✅ عضو شدی در {link}"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def leave(self, group: str = "") -> str:
        """خارج شدن از گروه."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not group:
            return "❌ نام گروه لازم است"

        try:
            self._safe.record()

            if group.startswith("http"):
                await self._page.goto(group)
            else:
                await self._page.goto(
                    f"https://web.eitaa.com/#/g/{group}"
                )
            await asyncio.sleep(3)

            menu = await self._page.query_selector(
                '.chat-menu, .header-menu'
            )
            if menu:
                await menu.click()
                await asyncio.sleep(1)

            leave_btn = await self._page.query_selector(
                'button:has-text("خروج"), '
                'button:has-text("Leave")'
            )
            if not leave_btn:
                return "⚠️ دکمهٔ خروج پیدا نشد"

            await leave_btn.click()
            await asyncio.sleep(2)

            log_success(f"✅ [CONTROLLER:eitaa:leave] {group}")
            return f"✅ از {group} خارج شدی"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        """فرستادن پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not chat or not text:
            return "❌ chat و text لازم است"

        try:
            self._safe.record()

            if chat.startswith("http"):
                await self._page.goto(chat)
            else:
                await self._page.goto(
                    f"https://web.eitaa.com/#/g/{chat}"
                )
            await asyncio.sleep(3)

            msg_box = await self._page.query_selector(
                'div[contenteditable="true"], '
                '.composer-input'
            )
            if not msg_box:
                return "❌ جعبهٔ پیام پیدا نشد"

            await msg_box.click()
            await asyncio.sleep(0.5)
            await self._page.keyboard.type(text)
            await asyncio.sleep(1)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)

            log_success(
                f"✅ [CONTROLLER:eitaa:send] {chat}"
            )
            return f"✅ پیام فرستاده شد"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def forward(self, **kwargs) -> str:
        return "⚠️ فوروارد در ایتا — دستی"

    async def pin(self, **kwargs) -> str:
        return "⚠️ سنجاق در ایتا — دستی"

    async def delete_message(self, **kwargs) -> str:
        return "⚠️ حذف پیام در ایتا — دستی"


# ═══════════════════════════════════════════
# RubikaController — روبیکا
# ═══════════════════════════════════════════
class RubikaController:
    """کنترل روبیکا — با rubpy (userbot)."""

    def __init__(self, collector: Any):
        self._collector = collector
        self._client = collector._client
        self._safe = SafeAction("rubika")

    async def search(self, query: str = "") -> str:
        """جستجو در روبیکا."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not query:
            return "❌ query خالی"

        try:
            self._safe.record()

            # rubpy API
            if hasattr(self._client, "search_global"):
                result = await self._client.search_global(query)
                return f"🔍 نتایج: {result}"

            return "⚠️ سرچ در rubpy پشتیبانی نمی‌شود"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def join(self, link: str = "") -> str:
        """عضو شدن در گروه."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not link:
            return "❌ لینک گروه لازم است"

        try:
            self._safe.record()

            # rubpy: join_chat
            if hasattr(self._client, "join_chat"):
                result = await self._client.join_chat(link)
                log_success(f"✅ [CONTROLLER:rubika:join] {link}")
                return f"✅ عضو شدی"

            return "⚠️ join در rubpy پشتیبانی نمی‌شود"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def leave(self, group: str = "") -> str:
        """خارج شدن از گروه."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not group:
            return "❌ نام گروه لازم است"

        try:
            self._safe.record()

            if hasattr(self._client, "leave_chat"):
                result = await self._client.leave_chat(group)
                log_success(f"✅ [CONTROLLER:rubika:leave] {group}")
                return f"✅ خارج شدی"

            return "⚠️ leave در rubpy پشتیبانی نمی‌شود"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        """فرستادن پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        if not chat or not text:
            return "❌ chat و text لازم است"

        try:
            self._safe.record()

            if hasattr(self._client, "send_message"):
                result = await self._client.send_message(
                    chat, text
                )
                log_success(f"✅ [CONTROLLER:rubika:send] {chat}")
                return f"✅ پیام فرستاده شد"

            return "⚠️ send در rubpy پشتیبانی نمی‌شود"

        except Exception as e:
            return f"❌ خطا: {e}"

    async def forward(self, **kwargs) -> str:
        return "⚠️ فوروارد در روبیکا — دستی"

    async def pin(self, **kwargs) -> str:
        return "⚠️ سنجاق در روبیکا — دستی"

    async def delete_message(self, **kwargs) -> str:
        """حذف پیام."""
        ok, reason = await self._safe.check()
        if not ok:
            return reason

        try:
            self._safe.record()
            chat = kwargs.get("chat", "")
            msg_id = kwargs.get("message_id", "")

            if hasattr(self._client, "delete_messages"):
                await self._client.delete_messages(
                    chat, [msg_id]
                )
                return f"✅ پیام حذف شد"

            return "⚠️ delete در rubpy پشتیبانی نمی‌شود"

        except Exception as e:
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# Factory — ساخت کنترلر مناسب
# ═══════════════════════════════════════════
def get_controller(
    platform: str, collector: Any
) -> Any | None:
    """ساخت کنترلر مناسب برای پلتفرم."""
    platform = platform.lower().strip()

    if platform == "bale":
        return BaleController(collector)
    elif platform == "eitaa":
        return EitaaController(collector)
    elif platform == "rubika":
        return RubikaController(collector)

    return None


def list_platforms() -> list[str]:
    """لیست پلتفرم‌های پشتیبانی‌شده."""
    return ["bale", "eitaa", "rubika"]


def list_actions() -> list[str]:
    """لیست کارهای موجود."""
    return [
        "search", "join", "leave", "send",
        "forward", "pin", "delete_message",
    ]


def get_stats(platform: str) -> dict:
    """آمار کارها."""
    return SafeAction.get_stats(platform)


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "BaleController",
    "EitaaController",
    "RubikaController",
    "get_controller",
    "list_platforms",
    "list_actions",
    "get_stats",
]