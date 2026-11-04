"""
کنترلر پلتفرم‌ها — فقط با دستور صریح مدیر.

پلتفرم‌ها: بله، ایتا، روبیکا، تلگرام، واتساپ، لینکدین،
          اینستاگرام، یوتیوب، وب

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
  - حداکثر ۱۰ کار در روز
  - حداکثر ۳ کار در ساعت
  - ۳-۸ ثانیه صبر بین کارها
  - فقط ۹ صبح - ۱۱ شب (تهران)
  - آمار در فایل ذخیره می‌شود (ری‌استارت پاک نمی‌کند)
  - record فقط بعد از موفقیت
  - لاگ کامل

نسخهٔ ۸ (این نسخه — استاندارد):
  - 🎯 آمار در فایل ذخیره می‌شود (نه کلاس‌متغیر)
  - 🎯 ساعت کاری به timezone تهران
  - 🎯 record() فقط بعد از موفقیت (توسط caller)
  - 🎯 atomic write برای آمار (tmp + replace + fsync)
  - 🎯 قفل per-platform برای جلوگیری از race
  - 🎯 timeout صریح در _goto_chat
  - 🎯 log صریح در get_controller
  - 🎯 fill() به‌جای keyboard.type در WhatsApp (سریع‌تر)
  - ✅ حفظ API قبلی (همه توابع و امضاها یکسان)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
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
WORK_TIMEZONE = "Asia/Tehran"

# سقف ذخیره‌سازی آمار
_MAX_STATS_AGE_S = 7 * 86400
_MAX_STATS_ENTRIES = 1000

DANGEROUS_ACTIONS = frozenset({
    "join", "leave", "send", "forward",
    "pin", "delete_message", "unfollow",
})


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _get_stats_path() -> str:
    """مسیر فایل آمار — از data.paths."""
    try:
        from data import paths as dp
        offset_dir = os.path.join(str(dp.STORAGE_DIR), "offset")
        os.makedirs(offset_dir, exist_ok=True)
        return os.path.join(offset_dir, "controller_stats.json")
    except Exception:
        fallback = "storage/offset"
        os.makedirs(fallback, exist_ok=True)
        return os.path.join(fallback, "controller_stats.json")


def _atomic_write_json(path: str, data: dict) -> bool:
    """نوشتن atomic JSON — tmp + replace + fsync."""
    tmp = path + ".tmp"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════
# SafeAction — امنیت مشترک
# ═══════════════════════════════════════════
class SafeAction:
    """مدیریت امن اکشن‌ها — per-platform، persistent، timezone-aware."""

    # قفل per-platform برای جلوگیری از race
    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, platform: str):
        self.platform = platform
        self._last_action_at = 0.0
        self._stats_file = _get_stats_path()

        if platform not in SafeAction._locks:
            SafeAction._locks[platform] = asyncio.Lock()
        self._lock = SafeAction._locks[platform]

    # ─── خواندن/نوشتن آمار ───
    def _load_all_stats(self) -> dict[str, list[float]]:
        """همهٔ آمار را از فایل می‌خواند."""
        try:
            if not os.path.exists(self._stats_file):
                return {}
            with open(self._stats_file, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            return {}

    def _get_actions(self) -> list[float]:
        """آمار این پلتفرم — با فیلتر قدیمی‌ها."""
        data = self._load_all_stats()
        actions = data.get(self.platform, [])
        if not isinstance(actions, list):
            return []
        now = time.time()
        return [
            float(t) for t in actions
            if isinstance(t, (int, float))
            and now - float(t) < _MAX_STATS_AGE_S
        ]

    def _save_actions(self, actions: list[float]) -> bool:
        """ذخیرهٔ آمار این پلتفرم — atomic."""
        data = self._load_all_stats()
        data[self.platform] = actions[-_MAX_STATS_ENTRIES:]
        return _atomic_write_json(self._stats_file, data)

    # ─── ساعت کاری ───
    @staticmethod
    def _is_work_time() -> bool:
        """آیا الان ساعت کاری است؟ (تهران، نه UTC)."""
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(WORK_TIMEZONE))
        except Exception:
            now = datetime.now()
        return WORK_HOUR_START <= now.hour <= WORK_HOUR_END

    # ─── چک محدودیت‌ها ───
    def _check_limits(self) -> tuple[bool, str]:
        """چک سقف روزانه/ساعتی + ساعت کاری."""
        now = time.time()
        actions = self._get_actions()

        if len(actions) >= MAX_PER_DAY:
            return False, f"⛔ امروز {MAX_PER_DAY} کار کردی — فردا"

        hour_ago = now - 3600
        recent = [t for t in actions if t > hour_ago]
        if len(recent) >= MAX_PER_HOUR:
            return False, f"⛔ در ساعت اخیر {MAX_PER_HOUR} کار — صبر کن"

        if not self._is_work_time():
            try:
                from zoneinfo import ZoneInfo
                now_tz = datetime.now(ZoneInfo(WORK_TIMEZONE))
                current_h = now_tz.hour
            except Exception:
                current_h = datetime.now().hour
            return (
                False,
                f"⏰ فقط {WORK_HOUR_START} صبح تا "
                f"{WORK_HOUR_END} شب (تهران) | "
                f"الان ساعت {current_h}",
            )

        return True, ""

    # ─── wait ───
    async def wait(self) -> None:
        """تأخیر انسانی قبل از اکشن بعدی."""
        now = time.monotonic()
        elapsed = now - self._last_action_at
        if elapsed < MIN_DELAY_S:
            wait_s = (
                MIN_DELAY_S - elapsed
                + random.uniform(0, MAX_DELAY_S - MIN_DELAY_S)
            )
            log_info(
                f"⏳ [CONTROLLER:{self.platform}] صبر {wait_s:.1f}s"
            )
            await asyncio.sleep(wait_s)
        self._last_action_at = time.monotonic()

    # ─── record ───
    def record(self) -> None:
        """ثبت اکشن موفق — هم در حافظه هم در فایل."""
        actions = self._get_actions()
        actions.append(time.time())
        if not self._save_actions(actions):
            log_warning(
                f"⚠️ [CONTROLLER:{self.platform}] "
                f"ذخیرهٔ آمار ناموفق"
            )

    # ─── check (async، با قفل) ───
    async def check(self) -> tuple[bool, str]:
        """چک کامل + تأخیر — با قفل per-platform."""
        async with self._lock:
            ok, reason = self._check_limits()
            if not ok:
                return False, reason
            await self.wait()
            return True, ""

    # ─── آمار ───
    @classmethod
    def get_stats(cls, platform: str) -> dict:
        """آمار یک پلتفرم — برای debug/admin."""
        helper = cls.__new__(cls)
        helper.platform = platform
        helper._stats_file = _get_stats_path()
        actions = helper._get_actions()

        now = time.time()
        hour_ago = now - 3600
        day_ago = now - 86400

        return {
            "today": len([t for t in actions if t > day_ago]),
            "this_hour": len([t for t in actions if t > hour_ago]),
            "max_day": MAX_PER_DAY,
            "max_hour": MAX_PER_HOUR,
            "work_hours": f"{WORK_HOUR_START}-{WORK_HOUR_END}",
            "timezone": WORK_TIMEZONE,
            "is_work_time": cls._is_work_time(),
        }


# ═══════════════════════════════════════════
# کلاس پایه — برای کاهش تکرار کد
# ═══════════════════════════════════════════
class _BaseController:
    """کلاس پایه با پیاده‌سازی پیش‌فرض."""

    platform_name: str = "unknown"
    web_url: str = ""

    def __init__(self, collector: Any):
        self._collector = collector
        self._safe = SafeAction(self.platform_name)

    @property
    def _page(self):
        return getattr(self._collector, "_page", None)

    @property
    def _client(self):
        return getattr(self._collector, "_client", None)

    # ─── پیش‌فرض‌ها (قابل override) ───
    async def search(self, query: str = "") -> str:
        return f"⚠️ search در {self.platform_name} پشتیبانی نمی‌شود"

    async def join(self, link: str = "") -> str:
        return f"⚠️ join در {self.platform_name} پشتیبانی نمی‌شود"

    async def leave(self, group: str = "") -> str:
        return f"⚠️ leave در {self.platform_name} پشتیبانی نمی‌شود"

    async def send(self, chat: str = "", text: str = "") -> str:
        return f"⚠️ send در {self.platform_name} پشتیبانی نمی‌شود"

    async def forward(
        self, from_chat: str = "",
        message_id: str = "", to_chat: str = "",
    ) -> str:
        return f"⚠️ forward در {self.platform_name} — دستی"

    async def pin(self, chat: str = "", message_id: str = "") -> str:
        return f"⚠️ pin در {self.platform_name} — دستی"

    async def delete_message(
        self, chat: str = "", message_id: str = "",
    ) -> str:
        return f"⚠️ delete_message در {self.platform_name} — دستی"

    # ─── helper: باز کردن یک چت با URL ───
    async def _goto_chat(self, chat: str, url_tmpl: str = "") -> bool:
        if not self._page:
            return False
        if not chat:
            return False
        try:
            if chat.startswith("http"):
                target = chat
            elif url_tmpl:
                target = url_tmpl.format(chat=chat)
            else:
                return False

            await self._page.goto(
                target,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(3)
            return True
        except Exception as e:
            log_error(f"❌ [{self.platform_name}:goto] {e}")
            return False


# ═══════════════════════════════════════════
# 1) BaleController — بله
# ═══════════════════════════════════════════
class BaleController(_BaseController):
    platform_name = "bale"
    web_url = "https://web.bale.ai/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی است"

        try:
            box = await self._page.query_selector(
                'input[placeholder*="جستجو"], '
                'input[placeholder*="Search"], '
                '.search-input'
            )
            if not box:
                return "❌ جعبهٔ سرچ پیدا نشد"

            await box.click()
            await box.fill(query)
            await asyncio.sleep(2)

            results = await self._page.query_selector_all(
                '.search-result, .chat-item, [data-peer-id]'
            )
            lines = [f"🔍 نتایج «{query}» ({len(results)} مورد):"]
            for i, r in enumerate(results[:10], 1):
                try:
                    text = (await r.inner_text() or "").strip()
                    lines.append(f"  {i}. {text.split(chr(10))[0][:60]}")
                except Exception:
                    continue
            self._safe.record()
            log_success(f"✅ [bale:search] «{query}»")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {type(e).__name__}: {e}"

    async def join(self, link: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not link:
            return "❌ لینک گروه لازم است"
        try:
            await self._page.goto(
                link, wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(3)
            btn = await self._page.query_selector(
                'button:has-text("عضویت"), '
                'button:has-text("Join"), .join-button'
            )
            if not btn:
                return "⚠️ دکمهٔ عضویت پیدا نشد"
            await btn.click()
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ عضو شدی در {link}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def leave(self, group: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not group:
            return "❌ نام یا لینک گروه لازم است"
        try:
            await self._goto_chat(
                group, "https://web.bale.ai/#/g/{chat}"
            )
            menu = await self._page.query_selector(
                '.chat-menu, .header-menu, button[aria-label*="منو"]'
            )
            if menu:
                await menu.click()
                await asyncio.sleep(1)
            btn = await self._page.query_selector(
                'button:has-text("خروج"), button:has-text("Leave")'
            )
            if not btn:
                return "⚠️ دکمهٔ خروج پیدا نشد"
            await btn.click()
            await asyncio.sleep(1)
            confirm = await self._page.query_selector(
                'button:has-text("بله"), button:has-text("تأیید")'
            )
            if confirm:
                await confirm.click()
                await asyncio.sleep(2)
            self._safe.record()
            return f"✅ از {group} خارج شدی"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not chat or not text:
            return "❌ chat و text لازم است"
        try:
            await self._goto_chat(
                chat, "https://web.bale.ai/#/g/{chat}"
            )
            box = await self._page.query_selector(
                'div[contenteditable="true"], .composer-input'
            )
            if not box:
                return "❌ جعبهٔ پیام پیدا نشد"
            await box.click()
            await asyncio.sleep(0.5)
            try:
                await box.fill(text)
            except Exception:
                await self._page.keyboard.type(text)
            await asyncio.sleep(1)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ پیام فرستاده شد به {chat}"
        except Exception as e:
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# 2) EitaaController — ایتا
# ═══════════════════════════════════════════
class EitaaController(_BaseController):
    platform_name = "eitaa"
    web_url = "https://web.eitaa.com/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            box = await self._page.query_selector(
                'input[placeholder*="جستجو"], .search-input'
            )
            if not box:
                return "❌ جعبهٔ سرچ پیدا نشد"
            await box.click()
            await box.fill(query)
            await asyncio.sleep(2)
            results = await self._page.query_selector_all(
                '.chatlist-chat, .search-result'
            )
            lines = [f"🔍 نتایج «{query}» ({len(results)}):"]
            for i, r in enumerate(results[:10], 1):
                try:
                    t = (await r.inner_text() or "").strip()
                    lines.append(f"  {i}. {t.split(chr(10))[0][:60]}")
                except Exception:
                    continue
            self._safe.record()
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {e}"

    async def join(self, link: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not link:
            return "❌ لینک گروه لازم است"
        try:
            await self._page.goto(
                link, wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(3)
            btn = await self._page.query_selector(
                'button:has-text("عضویت"), button:has-text("Join")'
            )
            if not btn:
                return "⚠️ دکمهٔ عضویت پیدا نشد"
            await btn.click()
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ عضو شدی در {link}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def leave(self, group: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not group:
            return "❌ نام گروه لازم است"
        try:
            await self._goto_chat(
                group, "https://web.eitaa.com/#/g/{chat}"
            )
            menu = await self._page.query_selector(
                '.chat-menu, .header-menu'
            )
            if menu:
                await menu.click()
                await asyncio.sleep(1)
            btn = await self._page.query_selector(
                'button:has-text("خروج"), button:has-text("Leave")'
            )
            if not btn:
                return "⚠️ دکمهٔ خروج پیدا نشد"
            await btn.click()
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ از {group} خارج شدی"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not chat or not text:
            return "❌ chat و text لازم است"
        try:
            await self._goto_chat(
                chat, "https://web.eitaa.com/#/g/{chat}"
            )
            box = await self._page.query_selector(
                'div[contenteditable="true"], .composer-input'
            )
            if not box:
                return "❌ جعبهٔ پیام پیدا نشد"
            await box.click()
            await asyncio.sleep(0.5)
            try:
                await box.fill(text)
            except Exception:
                await self._page.keyboard.type(text)
            await asyncio.sleep(1)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)
            self._safe.record()
            return "✅ پیام فرستاده شد"
        except Exception as e:
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# 3) RubikaController — روبیکا (rubpy)
# ═══════════════════════════════════════════
class RubikaController(_BaseController):
    platform_name = "rubika"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            if self._client and hasattr(self._client, "search_global"):
                result = await self._client.search_global(query)
                self._safe.record()
                return f"🔍 نتایج: {result}"
            return "⚠️ search در rubpy پشتیبانی نمی‌شود"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def join(self, link: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not link:
            return "❌ لینک گروه لازم است"
        try:
            if self._client and hasattr(self._client, "join_chat"):
                await self._client.join_chat(link)
                self._safe.record()
                return "✅ عضو شدی"
            return "⚠️ join در rubpy پشتیبانی نمی‌شود"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def leave(self, group: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not group:
            return "❌ نام گروه لازم است"
        try:
            if self._client and hasattr(self._client, "leave_chat"):
                await self._client.leave_chat(group)
                self._safe.record()
                return "✅ خارج شدی"
            return "⚠️ leave در rubpy پشتیبانی نمی‌شود"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not chat or not text:
            return "❌ chat و text لازم است"
        try:
            if self._client and hasattr(self._client, "send_message"):
                await self._client.send_message(chat, text)
                self._safe.record()
                return "✅ پیام فرستاده شد"
            return "⚠️ send در rubpy پشتیبانی نمی‌شود"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def delete_message(
            self,
            chat: str = "",
            message_id: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not chat or not message_id:
            return "❌ chat و message_id لازم است"
        try:
            if self._client and hasattr(self._client, "delete_messages"):
                await self._client.delete_messages(chat, [message_id])
                self._safe.record()
                return "✅ پیام حذف شد"
            return "⚠️ delete در rubpy پشتیبانی نمی‌شود"
        except Exception as e:
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# 4) TelegramController — تلگرام
# ═══════════════════════════════════════════
class TelegramController(_BaseController):
    platform_name = "telegram"
    web_url = "https://web.telegram.org/k/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            box = await self._page.query_selector(
                'input.input-search-input, .input-search input, '
                '[contenteditable="true"].input-search-input'
            )
            if not box:
                return "❌ جعبهٔ سرچ پیدا نشد"
            await box.click()
            try:
                await box.fill(query)
            except Exception:
                await self._page.keyboard.type(query)
            await asyncio.sleep(2)
            results = await self._page.query_selector_all(
                '.chatlist-chat, .search-result, .ListItem'
            )
            lines = [f"🔍 نتایج «{query}» ({len(results)}):"]
            for i, r in enumerate(results[:10], 1):
                try:
                    t = (await r.inner_text() or "").strip()
                    lines.append(f"  {i}. {t.split(chr(10))[0][:60]}")
                except Exception:
                    continue
            self._safe.record()
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {e}"

    async def join(self, link: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not link:
            return "❌ لینک گروه لازم است"
        try:
            await self._page.goto(
                link, wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(3)
            btn = await self._page.query_selector(
                'button:has-text("Join"), button:has-text("عضویت"), '
                '.btn-primary'
            )
            if not btn:
                return "⚠️ دکمهٔ عضویت پیدا نشد"
            await btn.click()
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ عضو شدی در {link}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def leave(self, group: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not group:
            return "❌ نام گروه لازم است"
        try:
            if group.startswith("http"):
                await self._page.goto(
                    group,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            await asyncio.sleep(3)
            header = await self._page.query_selector(
                '.chat-info, .MiddleHeader .info, .topbar'
            )
            if header:
                await header.click()
                await asyncio.sleep(1)
            menu = await self._page.query_selector(
                'button[aria-label*="More"], .btn-menu-toggle'
            )
            if menu:
                await menu.click()
                await asyncio.sleep(1)
            btn = await self._page.query_selector(
                'button:has-text("Leave"), button:has-text("خروج")'
            )
            if not btn:
                return "⚠️ دکمهٔ خروج پیدا نشد"
            await btn.click()
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ از {group} خارج شدی"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not chat or not text:
            return "❌ chat و text لازم است"
        try:
            if chat.startswith("http"):
                await self._page.goto(
                    chat,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            await asyncio.sleep(3)
            box = await self._page.query_selector(
                'div[contenteditable="true"].input-message-input, '
                '.input-message-input[contenteditable="true"]'
            )
            if not box:
                return "❌ جعبهٔ پیام پیدا نشد"
            await box.click()
            await asyncio.sleep(0.5)
            try:
                await box.fill(text)
            except Exception:
                await self._page.keyboard.type(text)
            await asyncio.sleep(1)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)
            self._safe.record()
            return "✅ پیام فرستاده شد"
        except Exception as e:
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# 5) WhatsAppController — واتساپ
# ═══════════════════════════════════════════
class WhatsAppController(_BaseController):
    platform_name = "whatsapp"
    web_url = "https://web.whatsapp.com/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            box = await self._page.query_selector(
                'div[contenteditable="true"][data-tab="3"], '
                '[data-testid="chat-list-search"]'
            )
            if not box:
                return "❌ جعبهٔ سرچ پیدا نشد"
            await box.click()
            await asyncio.sleep(0.5)
            try:
                await box.fill(query)
            except Exception:
                await self._page.keyboard.type(query)
            await asyncio.sleep(2)
            results = await self._page.query_selector_all(
                '[data-testid="cell-frame-container"], '
                'div[role="listitem"]'
            )
            lines = [f"🔍 نتایج «{query}» ({len(results)}):"]
            for i, r in enumerate(results[:10], 1):
                try:
                    t = (await r.inner_text() or "").strip()
                    lines.append(f"  {i}. {t.split(chr(10))[0][:60]}")
                except Exception:
                    continue
            self._safe.record()
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not chat or not text:
            return "❌ chat و text لازم است"
        try:
            # جستجوی چت
            search_box = await self._page.query_selector(
                'div[contenteditable="true"][data-tab="3"]'
            )
            if search_box:
                await search_box.click()
                await asyncio.sleep(0.5)
                try:
                    await search_box.fill(chat)
                except Exception:
                    await self._page.keyboard.type(chat)
                await asyncio.sleep(2)
                # کلیک روی اولین نتیجه
                first = await self._page.query_selector(
                    '[data-testid="cell-frame-container"], '
                    'div[role="listitem"]'
                )
                if first:
                    await first.click()
                    await asyncio.sleep(2)

            # تایپ پیام
            box = await self._page.query_selector(
                'div[contenteditable="true"][data-tab="10"], '
                'footer div[contenteditable="true"]'
            )
            if not box:
                return "❌ جعبهٔ پیام پیدا نشد"
            await box.click()
            await asyncio.sleep(0.5)
            try:
                await box.fill(text)
            except Exception:
                await self._page.keyboard.type(text)
            await asyncio.sleep(1)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)
            self._safe.record()
            return f"✅ پیام فرستاده شد به {chat}"
        except Exception as e:
            return f"❌ خطا: {e}"

    # واتساپ join/leave ندارد (وب محدود است)
    async def join(self, link: str = "") -> str:
        return "⚠️ join در واتساپ‌وب پشتیبانی نمی‌شود"

    async def leave(self, group: str = "") -> str:
        return "⚠️ leave در واتساپ‌وب پشتیبانی نمی‌شود"


# ═══════════════════════════════════════════
# 6) LinkedInController — لینکدین
# ═══════════════════════════════════════════
class LinkedInController(_BaseController):
    platform_name = "linkedin"
    web_url = "https://www.linkedin.com/feed/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            url = (
                "https://www.linkedin.com/search/results/content/"
                f"?keywords={query}&origin=GLOBAL_SEARCH_HEADER"
            )
            await self._page.goto(
                url, wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(5)
            posts = await self._page.query_selector_all(
                "div[role='listitem']"
            )
            self._safe.record()
            return f"🔍 نتایج «{query}»: {len(posts)} پست"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        return "⚠️ send در لینکدین پشتیبانی نمی‌شود (فقط خواندن)"

    async def join(self, link: str = "") -> str:
        return "⚠️ join در لینکدین پشتیبانی نمی‌شود"


# ═══════════════════════════════════════════
# 7) InstagramController — اینستاگرام
# ═══════════════════════════════════════════
class InstagramController(_BaseController):
    platform_name = "instagram"
    web_url = "https://www.instagram.com/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            await self._page.goto(
                f"https://www.instagram.com/explore/tags/{query}/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(4)
            posts = await self._page.query_selector_all("article")
            self._safe.record()
            return f"🔍 نتایج «{query}»: {len(posts)} پست"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def send(self, chat: str = "", text: str = "") -> str:
        return "⚠️ send در اینستاگرام پشتیبانی نمی‌شود"


# ═══════════════════════════════════════════
# 8) YouTubeController — یوتیوب
# ═══════════════════════════════════════════
class YouTubeController(_BaseController):
    platform_name = "youtube"
    web_url = "https://www.youtube.com/"

    async def search(self, query: str = "") -> str:
        ok, reason = await self._safe.check()
        if not ok:
            return reason
        if not query:
            return "❌ query خالی"
        try:
            url = (
                "https://www.youtube.com/results"
                f"?search_query={query.replace(' ', '+')}"
            )
            await self._page.goto(
                url, wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(4)
            videos = await self._page.query_selector_all(
                "ytd-video-renderer"
            )
            lines = [f"🔍 نتایج «{query}» ({len(videos)}):"]
            for i, v in enumerate(videos[:10], 1):
                try:
                    t = await v.query_selector("#video-title")
                    if t:
                        title = (await t.inner_text() or "").strip()
                        lines.append(f"  {i}. {title[:60]}")
                except Exception:
                    continue
            self._safe.record()
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# 9) WebController — وب
# ═══════════════════════════════════════════
class WebController(_BaseController):
    platform_name = "web"

    async def search(self, query: str = "") -> str:
        return "⚠️ search در وب پشتیبانی نمی‌شود (فقط خواندن)"


# ═══════════════════════════════════════════
# Factory — ساخت کنترلر مناسب
# ═══════════════════════════════════════════
_CONTROLLER_MAP = {
    "bale": BaleController,
    "eitaa": EitaaController,
    "rubika": RubikaController,
    "telegram": TelegramController,
    "whatsapp": WhatsAppController,
    "linkedin": LinkedInController,
    "instagram": InstagramController,
    "youtube": YouTubeController,
    "web": WebController,
}


def get_controller(platform: str, collector: Any) -> Any | None:
    """ساخت کنترلر مناسب برای پلتفرم."""
    if not collector:
        log_warning(
            f"⚠️ [CONTROLLER] collector خالی | platform={platform}"
        )
        return None

    platform = platform.lower().strip()
    cls = _CONTROLLER_MAP.get(platform)
    if not cls:
        log_warning(
            f"⚠️ [CONTROLLER] پلتفرم پشتیبانی نمی‌شود | "
            f"platform={platform} | "
            f"available={list(_CONTROLLER_MAP.keys())}"
        )
        return None

    try:
        return cls(collector)
    except Exception as e:
        log_error(
            f"❌ [CONTROLLER] ساخت کنترلر ناموفق | "
            f"platform={platform} | {type(e).__name__}: {e}"
        )
        return None


def list_platforms() -> list[str]:
    """لیست همه پلتفرم‌های پشتیبانی‌شده."""
    return list(_CONTROLLER_MAP.keys())


def list_actions() -> list[str]:
    """لیست کارهای موجود."""
    return [
        "search", "join", "leave", "send",
        "forward", "pin", "delete_message",
    ]


def get_stats(platform: str) -> dict:
    """آمار کارها."""
    return SafeAction.get_stats(platform)


def get_controller_capabilities(platform: str) -> dict:
    """قابلیت‌های هر کنترلر."""
    caps = {
        "bale": ["search", "join", "leave", "send"],
        "eitaa": ["search", "join", "leave", "send"],
        "rubika": ["search", "join", "leave", "send", "delete_message"],
        "telegram": ["search", "join", "leave", "send"],
        "whatsapp": ["search", "send"],
        "linkedin": ["search"],
        "instagram": ["search"],
        "youtube": ["search"],
        "web": [],
    }
    return {
        "platform": platform,
        "capabilities": caps.get(platform, []),
    }


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "SafeAction",
    "BaleController",
    "EitaaController",
    "RubikaController",
    "TelegramController",
    "WhatsAppController",
    "LinkedInController",
    "InstagramController",
    "YouTubeController",
    "WebController",
    "get_controller",
    "list_platforms",
    "list_actions",
    "get_stats",
    "get_controller_capabilities",
]
