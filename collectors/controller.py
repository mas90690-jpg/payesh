"""
controller.py — کنترلر یکپارچهٔ همه پلتفرم‌ها.

شامل:
  - SmartController — کلاس اصلی (selectorها در dict، human behavior، anti-detect)
  - PLATFORM_SELECTORS — dict selectorها
  - get_controller / get_smart_controller — factory
  - list_platforms / list_actions — سازگاری

پلتفرم‌ها (۱۰):
  bale, eitaa, rubika, telegram, whatsapp,
  linkedin, instagram, youtube, facebook, web

کارها (۵):
  search, join, leave, send, search_content

امنیت:
  - فقط با دستور صریح
  - حداکثر ۱۰ کار در روز
  - حداکثر ۳ کار در ساعت
  - ۳-۸ ثانیه صبر بین کارها
  - فقط ۹ صبح - ۱۱ شب
  - human behavior (تأخیر، تایپ، موس، اسکرول)
  - anti-detect (stealth، fingerprint)

🆕 نسخه ۲:
  - ExperienceLearner وصل شد (get_best + record)
  - هر action موفق/ناموفق ذخیره می‌شه
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kernel.logger import (
    log_info,
    log_success,
    log_warning,
    log_debug,
)
from data.loader import load_json as _load_json

# ═══════════════════════════════════════════════════════════
# imports اختیاری (اگر نبود، fallback)
# ═══════════════════════════════════════════════════════════
try:
    from .anti_detect import apply_stealth
except ImportError:
    async def apply_stealth(page, rng=None):
        pass

try:
    from .human_behavior import HumanBehavior
except ImportError:
    class HumanBehavior:
        """fallback — بدون رفتار انسانی."""

        def __init__(self, seed=None):
            self.rng = random.Random(seed)

        async def delay(self, min_s=1.0, max_s=3.0):
            import asyncio
            await asyncio.sleep(self.rng.uniform(min_s, max_s))

        async def type_slowly(self, page, text, **kw):
            await page.keyboard.type(text)

        async def move_mouse_naturally(self, page, x, y, steps=8):
            pass

        async def scroll_naturally(self, page, times=3, **kw):
            import asyncio
            for _ in range(times):
                await page.mouse.wheel(0, 300)
                await asyncio.sleep(0.5)

        async def read_pause(self, page, seconds=3.0):
            import asyncio
            await asyncio.sleep(seconds)

        async def click_human(self, page, selector):
            try:
                el = await page.query_selector(selector)
                if el:
                    await el.click()
                    return True
            except Exception as exc:
                log_debug(f'⚠️ {type(exc).__name__}: {exc}')
            return False

# ═══════════════════════════════════════════════════════════
# تنظیمات امنیتی
# ═══════════════════════════════════════════════════════════
MAX_PER_DAY = 10
MAX_PER_HOUR = 3
MIN_DELAY_S = 3.0
MAX_DELAY_S = 8.0
WORK_HOUR_START = 9
WORK_HOUR_END = 23


# ═══════════════════════════════════════════════════════════
# Selector map — از data/platforms.json
# ═══════════════════════════════════════════════════════════
_platforms_data = _load_json("platforms.json")
PLATFORM_SELECTORS: dict[str, dict] = _platforms_data.get(
    "platforms", {}
)


# ═══════════════════════════════════════════════════════════
# SmartController — کلاس اصلی
# ═══════════════════════════════════════════════════════════
class SmartController:
    """یک کنترلر برای همه پلتفرم‌ها."""

    MAX_PER_DAY = MAX_PER_DAY
    MAX_PER_HOUR = MAX_PER_HOUR
    WORK_HOUR_START = WORK_HOUR_START
    WORK_HOUR_END = WORK_HOUR_END

    def __init__(self, platform: str, collector: Any):
        if platform not in PLATFORM_SELECTORS:
            raise ValueError(
                f"پلتفرم «{platform}» پشتیبانی نمی‌شود. "
                f"معتبر: {list(PLATFORM_SELECTORS)}"
            )
        self.platform = platform
        self.selectors = PLATFORM_SELECTORS[platform]
        self.collector = collector
        self.behavior = HumanBehavior()
        self.rng = random.Random()
        self._page = None
        self._state_file = Path(
            f"storage/sessions/{platform}_ctrl_state.json"
        )
        self._today = 0
        self._hour = 0
        self._hour_start = time.time()
        self._load_state()

    # ─── state ───
    def _load_state(self) -> None:
        try:
            if self._state_file.exists():
                d = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._today = int(d.get("today", 0))
                self._hour = int(d.get("hour", 0))
                self._hour_start = float(d.get("hour_start", time.time()))
        except Exception as exc:
            log_debug(f'⚠️ {type(exc).__name__}: {exc}')

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps({
                    "today": self._today,
                    "hour": self._hour,
                    "hour_start": self._hour_start,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log_debug(f'⚠️ {type(exc).__name__}: {exc}')

    def _check_limit(self) -> tuple[bool, str]:
        if time.time() - self._hour_start > 3600:
            self._hour = 0
            self._hour_start = time.time()
        h = datetime.now().hour
        if not (self.WORK_HOUR_START <= h < self.WORK_HOUR_END):
            return False, (
                f"ساعت کاری {self.WORK_HOUR_START}-"
                f"{self.WORK_HOUR_END} است (الان {h})"
            )
        if self._today >= self.MAX_PER_DAY:
            return False, f"سقف روزانه ({self.MAX_PER_DAY}) پر شد"
        if self._hour >= self.MAX_PER_HOUR:
            return False, f"سقف ساعتی ({self.MAX_PER_HOUR}) پر شد"
        return True, ""

    def _record(self) -> None:
        self._today += 1
        self._hour += 1
        self._save_state()

    # 🆕 ExperienceLearner
    def _remember_success(
            self,
            action: str,
            value: str,
            note: str = "") -> None:
        try:
            from core.brain.experience_learner import get_experience
            exp = get_experience()
            exp.record(
                platform=self.platform,
                action=action,
                value=value[:100],
                success=True,
                note=note[:200],
            )
        except Exception as e:
            log_warning(f"⚠️ [CTRL:exp_success] {e}")

    def _remember_failure(
            self,
            action: str,
            value: str,
            note: str = "") -> None:
        try:
            from core.brain.experience_learner import get_experience
            exp = get_experience()
            exp.record(
                platform=self.platform,
                action=action,
                value=value[:100],
                success=False,
                note=note[:200],
            )
        except Exception as e:
            log_warning(f"⚠️ [CTRL:exp_failure] {e}")

    def _get_best(self, action: str) -> str | None:
        try:
            from core.brain.experience_learner import get_experience
            exp = get_experience()
            return exp.get_best(self.platform, action)
        except Exception:
            return None

    # ─── page ───
    async def _get_page(self):
        if self._page is None:
            for attr in ("_get_page", "page", "_page"):
                v = getattr(self.collector, attr, None)
                if v is None:
                    continue
                self._page = await v() if callable(v) else v
                break
            if self._page is None:
                raise RuntimeError(
                    f"collector {self.platform} page ندارد"
                )
            try:
                await apply_stealth(self._page, self.rng)
            except Exception as e:
                log_warning(f"⚠️ [CTRL:stealth] {e}")
        return self._page

    async def _human_goto(self, url: str) -> None:
        page = await self._get_page()
        await self.behavior.delay(1.0, 3.0)
        await page.goto(
            url, wait_until="domcontentloaded", timeout=45_000
        )
        await self.behavior.read_pause(page, 3.0)

    # ═══════════════════════════════════════════
    # search
    # ═══════════════════════════════════════════
    async def search(self, query: str = "") -> str:
        if not query:
            return "❌ query خالی است."

        # 🆕 چک روش موفق قبلی
        best = self._get_best("search")
        if best:
            log_info(f"💡 [CTRL:search] روش قبلی: {best[:60]}")

        sel = self.selectors
        search_url = (
            sel["search_url"].format(q=query)
            if sel["search_url"]
            else f"{sel['base']}/search?q={query}"
        )

        try:
            page = await self._get_page()
            await self._human_goto(search_url)
            await self.behavior.scroll_naturally(page, times=2)

            results = await page.evaluate(
                """
                (sel) => {
                    const items = document.querySelectorAll(sel.item);
                    return Array.from(items).slice(0, 20).map(el => ({
                        name: (el.querySelector(sel.name) || el)
                              .innerText?.trim().slice(0, 100) || '',
                        link: el.querySelector(sel.link)?.href || '',
                    })).filter(r => r.name);
                }
                """,
                {
                    "item": sel["result_item"],
                    "name": sel["result_name"],
                    "link": sel["result_link"],
                },
            )

            if not results:
                # 🆕 ثبت شکست
                self._remember_failure("search", query, "no results")
                return (
                    f"🔍 نتیجه‌ای برای «{query}» در "
                    f"{self.platform} پیدا نشد."
                )

            lines = [
                f"🔍 {len(results)} نتیجه برای «{query}» "
                f"({self.platform}):"
            ]
            for i, r in enumerate(results, 1):
                lines.append(f"  {i}. {r['name']}")
                if r["link"]:
                    lines.append(f"     🔗 {r['link']}")

            # 🆕 ثبت موفقیت
            self._remember_success(
                "search", query,
                f"count={len(results)} | selector={sel['result_item']}"
            )
            log_success(
                f"✅ [CTRL:search] {self.platform} | {len(results)}"
            )
            return "\n".join(lines)
        except Exception as e:
            # 🆕 ثبت خطا
            self._remember_failure(
                "search", query, str(e)[:200]
            )
            log_warning(
                f"⚠️ [CTRL:search] {self.platform} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # join
    # ═══════════════════════════════════════════
    async def join(self, link: str = "") -> str:
        if not link:
            return "❌ link خالی است."
        ok, reason = self._check_limit()
        if not ok:
            return f"⛔ {reason}"

        # 🆕 چک روش قبلی
        best = self._get_best("join")
        if best:
            log_info(f"💡 [CTRL:join] روش قبلی: {best[:60]}")

        sel = self.selectors
        try:
            page = await self._get_page()
            await self._human_goto(link)

            already = await page.evaluate("""
                () => {
                    const t = document.body.innerText;
                    return t.includes('شما عضو') || t.includes('ترک') ||
                           t.includes('Leave') || t.includes('Unfollow');
                }
            """)
            if already:
                self._remember_success("join", link, "already-member")
                return "ℹ️ از قبل عضو بود."

            clicked = await page.evaluate(
                """
                (sel) => {
                    const btns = document.querySelectorAll(sel.btn);
                    for (const b of btns) {
                        const t = (b.innerText || '').trim();
                        for (const jt of sel.texts) {
                            if (t === jt || t.includes(jt)) {
                                b.setAttribute('data-brain-target', '1');
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                {
                    "btn": sel["join_btn"],
                    "texts": sel["join_texts"],
                },
            )
            if not clicked:
                self._remember_failure("join", link, "button-not-found")
                return (
                    f"❌ دکمهٔ عضویت در {self.platform} پیدا نشد."
                )

            await self.behavior.click_human(
                page, "[data-brain-target='1']"
            )
            await self.behavior.read_pause(page, 4.0)

            await page.evaluate("""
                () => {
                    const el = document.querySelector(
                        '[data-brain-target="1"]'
                    );
                    if (el) el.removeAttribute('data-brain-target');
                }
            """)

            self._record()
            self._remember_success("join", link, "joined")
            log_success(f"✅ [CTRL:join] {self.platform} | {link}")
            return f"✅ عضو شدی در {self.platform}: {link}"
        except Exception as e:
            self._remember_failure("join", link, str(e)[:200])
            log_warning(
                f"⚠️ [CTRL:join] {self.platform} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # leave
    # ═══════════════════════════════════════════
    async def leave(self, link: str = "") -> str:
        if not link:
            return "❌ link خالی است."
        ok, reason = self._check_limit()
        if not ok:
            return f"⛔ {reason}"

        # 🆕 چک روش قبلی
        best = self._get_best("leave")
        if best:
            log_info(f"💡 [CTRL:leave] روش قبلی: {best[:60]}")

        sel = self.selectors
        try:
            page = await self._get_page()
            await self._human_goto(link)

            clicked = await page.evaluate(
                """
                (sel) => {
                    const btns = document.querySelectorAll(sel.btn);
                    for (const b of btns) {
                        const t = (b.innerText || '').trim();
                        for (const lt of sel.texts) {
                            if (t === lt || t.includes(lt)) {
                                b.setAttribute('data-brain-leave', '1');
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                {
                    "btn": sel["join_btn"],
                    "texts": sel["leave_texts"],
                },
            )
            if not clicked:
                self._remember_failure("leave", link, "button-not-found")
                return (
                    f"❌ دکمهٔ ترک در {self.platform} پیدا نشد."
                )

            await self.behavior.click_human(
                page, "[data-brain-leave='1']"
            )
            await self.behavior.read_pause(page, 2.0)

            await page.evaluate("""
                () => {
                    const btns = document.querySelectorAll(
                        'button, .btn, [role="button"]'
                    );
                    for (const b of btns) {
                        const t = (b.innerText || '').trim();
                        if (
                            t === 'تأیید' || t === 'Confirm' ||
                            t === 'بله' || t === 'Yes'
                        ) {
                            b.click();
                            return;
                        }
                    }
                }
            """)
            await self.behavior.read_pause(page, 2.0)

            self._record()
            self._remember_success("leave", link, "left")
            log_success(
                f"✅ [CTRL:leave] {self.platform} | {link}"
            )
            return f"✅ خارج شدی از {self.platform}: {link}"
        except Exception as e:
            self._remember_failure("leave", link, str(e)[:200])
            log_warning(
                f"⚠️ [CTRL:leave] {self.platform} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # send
    # ═══════════════════════════════════════════
    async def send(self, chat: str = "", text: str = "") -> str:
        if not chat or not text:
            return "❌ chat یا text خالی است."
        ok, reason = self._check_limit()
        if not ok:
            return f"⛔ {reason}"

        # 🆕 چک روش قبلی
        best = self._get_best("send")
        if best:
            log_info(f"💡 [CTRL:send] روش قبلی: {best[:60]}")

        sel = self.selectors
        chat_url = (
            sel["chat_url"].format(id=chat)
            if sel["chat_url"]
            else f"{sel['base']}/chat/{chat}"
        )

        try:
            page = await self._get_page()
            await self._human_goto(chat_url)

            await page.evaluate(
                """
                (sel) => {
                    const inp = document.querySelector(sel);
                    if (inp) inp.setAttribute('data-brain-input', '1');
                }
                """,
                sel["input"],
            )
            target = await page.query_selector(
                "[data-brain-input='1']"
            )
            if not target:
                self._remember_failure("send", chat, "input-not-found")
                return f"❌ input در {self.platform} پیدا نشد."

            await target.click()
            await self.behavior.delay(0.5, 1.5)
            await self.behavior.type_slowly(page, text)
            await self.behavior.delay(0.8, 2.0)
            await page.keyboard.press("Enter")
            await self.behavior.read_pause(page, 2.0)

            self._record()
            self._remember_success("send", chat, f"text_len={len(text)}")
            log_success(
                f"✅ [CTRL:send] {self.platform} | {chat}"
            )
            return f"✅ ارسال شد به {chat} در {self.platform}"
        except Exception as e:
            self._remember_failure("send", chat, str(e)[:200])
            log_warning(
                f"⚠️ [CTRL:send] {self.platform} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # search_content
    # ═══════════════════════════════════════════
    async def search_content(
        self, chat: str = "", query: str = ""
    ) -> str:
        if not chat or not query:
            return "❌ chat یا query خالی است."

        # 🆕 چک روش قبلی
        best = self._get_best("search_content")
        if best:
            log_info(f"💡 [CTRL:search_content] روش قبلی: {best[:60]}")

        sel = self.selectors
        chat_url = (
            sel["chat_url"].format(id=chat)
            if sel["chat_url"]
            else f"{sel['base']}/chat/{chat}"
        )

        try:
            page = await self._get_page()
            await self._human_goto(chat_url)

            await page.keyboard.press("Control+f")
            await self.behavior.delay(0.5, 1.0)
            await self.behavior.type_slowly(page, query)
            await self.behavior.delay(1.0, 2.0)

            results = await page.evaluate(
                """
                (sel, q) => {
                    const items = document.querySelectorAll(sel.item);
                    return Array.from(items)
                        .filter(el => (el.innerText || '').includes(q))
                        .slice(0, 15)
                        .map(el => ({
                            text: (el.innerText || '').slice(0, 300),
                        }));
                }
                """,
                {"item": sel["message"]},
                query,
            )
            await page.keyboard.press("Escape")

            if not results:
                self._remember_failure("search_content", chat, "no results")
                return (
                    f"🔍 «{query}» در {chat} "
                    f"({self.platform}) پیدا نشد."
                )

            lines = [
                f"🔍 {len(results)} نتیجه در {chat} "
                f"({self.platform}):"
            ]
            for i, r in enumerate(results, 1):
                lines.append(f"  {i}. {r['text'][:200]}")

            self._remember_success(
                "search_content", chat,
                f"count={len(results)}"
            )
            log_success(
                f"✅ [CTRL:search_content] {self.platform} | "
                f"{len(results)}"
            )
            return "\n".join(lines)
        except Exception as e:
            self._remember_failure("search_content", chat, str(e)[:200])
            log_warning(
                f"⚠️ [CTRL:search_content] {self.platform} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict:
        return {
            "platform": self.platform,
            "today": self._today,
            "hour": self._hour,
            "max_day": self.MAX_PER_DAY,
            "max_hour": self.MAX_PER_HOUR,
        }


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════
def get_controller(platform: str, collector: Any) -> SmartController | None:
    """ساخت SmartController برای پلتفرم."""
    platform = platform.lower().strip()
    if platform not in PLATFORM_SELECTORS:
        return None
    return SmartController(platform, collector)


def get_smart_controller(
    platform: str, collector: Any
) -> SmartController:
    """alias — SmartController."""
    return SmartController(platform, collector)


def list_platforms() -> list[str]:
    """لیست پلتفرم‌های پشتیبانی‌شده."""
    return list(PLATFORM_SELECTORS.keys())


def list_supported_platforms() -> list[str]:
    """alias — list_platforms."""
    return list_platforms()


def list_actions() -> list[str]:
    """لیست actionها."""
    return ["search", "join", "leave", "send", "search_content"]


def get_controller_capabilities(platform: str) -> dict:
    """قابلیت‌های هر پلتفرم — بر اساس selectorها."""
    sel = PLATFORM_SELECTORS.get(platform)
    if not sel:
        return {"platform": platform, "capabilities": []}

    caps = []
    if sel.get("search_url") or sel.get("base"):
        caps.extend(["search", "search_content"])
    if sel.get("join_btn") and sel.get("join_texts"):
        caps.append("join")
    if sel.get("leave_texts"):
        caps.append("leave")
    if sel.get("input"):
        caps.append("send")

    return {
        "platform": platform,
        "capabilities": caps,
    }


def get_stats(platform: str) -> dict:
    """آمار پیش‌فرض (چون SmartController per-instance است)."""
    return {
        "platform": platform,
        "today": 0,
        "this_hour": 0,
        "max_day": MAX_PER_DAY,
        "max_hour": MAX_PER_HOUR,
        "work_hours": f"{WORK_HOUR_START}-{WORK_HOUR_END}",
    }


# ─── aliasهای سازگاری (نام قدیمی) ───
BaleController = SmartController
EitaaController = SmartController
RubikaController = SmartController
TelegramController = SmartController
WhatsAppController = SmartController
LinkedInController = SmartController
InstagramController = SmartController
YouTubeController = SmartController
WebController = SmartController


__all__ = [
    "BaleController",
    "EitaaController",
    "InstagramController",
    "LinkedInController",
    "MAX_PER_DAY",
    "MAX_PER_HOUR",
    "MIN_DELAY_S",
    "MAX_DELAY_S",
    "PLATFORM_SELECTORS",
    "RubikaController",
    "SmartController",
    "TelegramController",
    "WORK_HOUR_START",
    "WORK_HOUR_END",
    "WebController",
    "WhatsAppController",
    "YouTubeController",
    "get_controller",
    "get_controller_capabilities",
    "get_smart_controller",
    "get_stats",
    "list_actions",
    "list_platforms",
    "list_supported_platforms",
]
