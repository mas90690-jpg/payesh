# collectors/instagram.py
"""
Instagram collector — نسخه ۳.۰ (پایدار و تست‌شده)
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import log_debug, log_error, log_info, log_success, log_warning

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

_PLATFORM = "instagram"
_MAX_SEEN = 100000

INSTAGRAM_URL = "https://www.instagram.com/"
FEED_FOLLOWING_URL = "https://www.instagram.com/feed/following/"

LOGGEDIN_MARKERS = [
    'svg[aria-label="Home"]',
    'a[href="/direct/inbox/"]',
    'a[href="/explore/"]',
]

_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


class InstagramCollector(BaseCollector):
    def __init__(
        self,
        headless: bool = False,
        poll_seconds: int = 28800,
        max_posts: int = 10,
        max_stories: int = 20,
        story_delay_min: float = 4.0,
        story_delay_max: float = 9.0,
        read_stories: bool = True,
        read_posts: bool = True,
        **kwargs,
    ):
        super().__init__()
        self.headless = bool(headless)
        self._poll_seconds = int(poll_seconds)
        self._max_posts = int(max_posts)
        self._max_stories = int(max_stories)
        self._story_delay_min = float(story_delay_min)
        self._story_delay_max = float(story_delay_max)
        self._read_stories = bool(read_stories)
        self._read_posts = bool(read_posts)

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._connected = False
        self._is_cdp = False

        self._last_scrape_stats: dict[str, int] = {
            "total_chats": 0, "processed": 0, "messages_found": 0, "errors": 0,
        }
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()

        if not hasattr(InstagramCollector, "_connect_lock"):
            InstagramCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(STORAGE_DIR, "sessions", "instagram")
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "instagram")
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(STORAGE_DIR, "offset", "instagram_seen.json")
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

    @property
    def platform_name(self) -> str:
        return _PLATFORM

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} آیتم دیده‌شدهٔ Instagram")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری: {e}")

    async def _human_delay(self, min_s: float = 0.5, max_s: float = 2.5):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_scroll(self, amount: int = 500):
        remaining = abs(amount)
        sign = 1 if amount > 0 else -1
        while remaining > 0:
            step = random.randint(80, 200)
            step = min(step, remaining)
            try:
                await self._page.mouse.wheel(0, sign * step)
            except Exception:
                break
            remaining -= step
            await self._human_delay(0.15, 0.4)

    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._page:
            return True

        async with InstagramCollector._connect_lock:
            if self._connected and self._page:
                return True

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست")
                    return False

                self._pw = await async_playwright().start()

                # ۱. تلاش برای اتصال به CDP (اگر Chrome از قبل باز باشد)
                try:
                    self._browser = await self._pw.chromium.connect_over_cdp("http://localhost:9224")
                    log_info("✅ به Chrome باز شده (CDP) متصل شدیم")
                    self._is_cdp = True
                except Exception:
                    log_info("ℹ️ Chrome CDP باز نیست، پروفایل دائمی باز می‌شود...")
                    self._is_cdp = False

                if self._is_cdp:
                    if not self._browser.contexts:
                        log_error("مرورگر متصل شد ولی Context ندارد")
                        return False
                    self._context = self._browser.contexts[0]
                    self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                else:
                    use_headless = self.headless if headless is None else headless
                    self._context = await self._launch_context(use_headless)
                    self._page = (
                        self._context.pages[0]
                        if self._context.pages
                        else await self._context.new_page()
                    )

                # ۲. اول به صفحه اصلی برو تا لاگین چک شود
                log_info("🌐 رفتن به صفحه اصلی اینستاگرام...")
                await self._page.goto(
                    INSTAGRAM_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await asyncio.sleep(5)

                # ۳. چک لاگین
                if not await self._is_logged_in():
                    log_error("❌ در صفحه اصلی هم لاگین تشخیص داده نشد.")
                    await self.disconnect()
                    return False

                log_success("✅ لاگین در صفحه اصلی تایید شد.")

                # ۴. حالا به فید فالوورها برو
                log_info("🌐 رفتن به feed/following...")
                await self._page.goto(
                    FEED_FOLLOWING_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await asyncio.sleep(3)

                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                log_success("✅ اتصال Instagram برقرار شد.")
                return True

            except Exception as e:
                log_error(f"خطا در اتصال اینستاگرام: {type(e).__name__}: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

        for name in _PROFILE_LOCK_FILES:
            p = os.path.join(self._user_data_dir, name)
            if os.path.exists(p):
                with contextlib.suppress(Exception):
                    os.remove(p)

        opts: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "viewport": {"width": 1366, "height": 900},
            "user_agent": ua,
            "args": args,
        }
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            opts["executable_path"] = CHROME_PATH
        else:
            opts["channel"] = "chrome"

        try:
            return await self._pw.chromium.launch_persistent_context(**opts)
        except Exception as e:
            log_warning(f"خطا در باز کردن مرورگر با پروفایل: {e}")
            opts.pop("channel", None)
            opts.pop("executable_path", None)
            return await self._pw.chromium.launch_persistent_context(**opts)

    async def _is_logged_in(self) -> bool:
        try:
            if not self._page:
                return False
            url = (self._page.url or "").lower()
            if "/accounts/login" in url or "/challenge" in url:
                return False
            for sel in LOGGEDIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return False

    async def fetch_messages(self, since: datetime | None = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        async with self._page_lock:
            if self._read_stories:
                try:
                    await self._check_stories()
                except Exception as e:
                    log_warning(f"استوری‌ها: {e}")
            if self._read_posts:
                try:
                    await self._check_posts()
                except Exception as e:
                    log_warning(f"پست‌ها: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} آیتم از Instagram")
        return new_msgs

    async def _check_stories(self):
        log_info("📖 چک استوری‌های فالوورها...")
        await self._page.goto(FEED_FOLLOWING_URL, wait_until="domcontentloaded", timeout=60000)
        await self._human_delay(3, 5)

        story_circles = await self._find_story_circles()
        if not story_circles:
            log_info("ℹ️ استوری‌ای در نوار بالا نیست")
            return

        log_info(f"   {len(story_circles)} دایرهٔ استوری پیدا شد")
        count = 0
        skipped = 0

        for circle in story_circles[: self._max_stories]:
            try:
                if await self._is_suggested_story(circle):
                    skipped += 1
                    continue
                await circle.click()
                await asyncio.sleep(random.uniform(self._story_delay_min, self._story_delay_max))

                if await self._story_has_follow_button():
                    skipped += 1
                    await self._page.keyboard.press("Escape")
                    await self._human_delay(1, 2)
                    continue

                text = await self._extract_story_text()
                username = await self._extract_story_username()
                if text:
                    await self._process_story(username, text)
                    count += 1

                await self._page.keyboard.press("Escape")
                await self._human_delay(1, 2)
            except Exception as e:
                log_warning(f"خطا در استوری: {e}")
                with contextlib.suppress(Exception):
                    await self._page.keyboard.press("Escape")

        log_info(f"   ✅ {count} استوری فالوور | ⏭️ {skipped} suggested رد شد")

    async def _find_story_circles(self) -> list:
        for sel in ['div[role="button"] canvas', 'header ul li div[role="button"]', 'div[role="menu"] + div ul li']:
            try:
                items = await self._page.query_selector_all(sel)
                if items:
                    return items
            except Exception:
                continue
        return []

    async def _is_suggested_story(self, circle) -> bool:
        try:
            text = (await circle.inner_text() or "").lower()
            if "suggested" in text or "پیشنهاد" in text:
                return True
        except Exception:
            pass
        try:
            parent = await circle.evaluate_handle("el => el.parentElement")
            if parent:
                plus = await parent.query_selector('svg[aria-label*="plus" i], svg[aria-label*="Follow" i]')
                if plus:
                    return True
        except Exception:
            pass
        return False

    async def _story_has_follow_button(self) -> bool:
        try:
            el = await self._page.query_selector('button:has-text("Follow"), div[role="button"]:has-text("Follow"), button:has-text("دنبال")')
            if el and await el.is_visible():
                return True
        except Exception:
            pass
        return False

    async def _extract_story_text(self) -> str:
        try:
            el = await self._page.query_selector('div[role="dialog"] span, section span[dir="auto"]')
            if el:
                return ((await el.inner_text()) or "").strip()
        except Exception:
            pass
        return ""

    async def _extract_story_username(self) -> str:
        try:
            el = await self._page.query_selector('div[role="dialog"] header a[href^="/"], section header a[href^="/"]')
            if el:
                href = await el.get_attribute("href")
                if href:
                    return href.strip("/").split("/")[0]
        except Exception:
            pass
        return "instagram_user"

    async def _process_story(self, username: str, text: str):
        if not text or not self._kw.has_match(text):
            return
        mid = hashlib.sha256(f"story:{username}:{text[:200]}".encode()).hexdigest()[:16]
        uniq = f"ig_story:{mid}"
        if uniq in self._seen_ids:
            return
        self._seen_ids.add(uniq)

        self._messages_queue.append({
            "id": mid, "platform": _PLATFORM, "text": text,
            "chat_id": f"story_{username}", "chat_name": f"IG Story: @{username}",
            "chat_type": "instagram_story", "chat_username": username,
            "chat_link": f"https://instagram.com/{username}",
            "user_id": "", "sender_name": username,
            "timestamp": datetime.now(), "media_type": "text",
            "file_id": None, "local_file_path": None, "reply_to": None,
        })

    async def _check_posts(self):
        log_info("📖 چک پست‌های فالوورها...")
        await self._page.goto(FEED_FOLLOWING_URL, wait_until="domcontentloaded", timeout=60000)
        await self._human_delay(3, 5)

        for _ in range(3):
            await self._human_scroll(700)
            await self._human_delay(1, 2)

        try:
            posts = await self._page.query_selector_all("article")
        except Exception:
            posts = []

        if not posts:
            log_info("ℹ️ پستی در feed نیست")
            return

        log_info(f"   {len(posts)} پست پیدا شد")
        count = 0
        skipped = 0

        for post in posts[: self._max_posts]:
            try:
                if await self._is_suggested_post(post):
                    skipped += 1
                    continue
                text = await self._extract_post_text(post)
                username = await self._extract_post_username(post)
                if text and self._kw.has_match(text):
                    await self._process_post(username, text)
                    count += 1
            except Exception as e:
                log_warning(f"خطا در پست: {e}")

        log_info(f"   ✅ {count} پست فالوور | ⏭️ {skipped} suggested رد شد")

    async def _is_suggested_post(self, post) -> bool:
        try:
            text = (await post.inner_text() or "").lower()
            if "suggested for you" in text or "پیشنهادی برای شما" in text or "پیشنهاد شده" in text:
                return True
        except Exception:
            pass
        try:
            follow = await post.query_selector('button:has-text("Follow"), div[role="button"]:has-text("Follow")')
            if follow:
                return True
        except Exception:
            pass
        return False

    async def _extract_post_text(self, post) -> str:
        try:
            el = await post.query_selector('h1, div[role="button"] span[dir="auto"], span[dir="auto"]')
            if el:
                return ((await el.inner_text()) or "").strip()
        except Exception:
            pass
        return ""

    async def _extract_post_username(self, post) -> str:
        try:
            a = await post.query_selector('header a[href^="/"]')
            if a:
                href = await a.get_attribute("href")
                if href:
                    return href.strip("/").split("/")[0]
        except Exception:
            pass
        return "instagram_user"

    async def _process_post(self, username: str, text: str):
        mid = hashlib.sha256(f"post:{username}:{text[:200]}".encode()).hexdigest()[:16]
        uniq = f"ig_post:{mid}"
        if uniq in self._seen_ids:
            return
        self._seen_ids.add(uniq)

        self._messages_queue.append({
            "id": mid, "platform": _PLATFORM, "text": text[:5000],
            "chat_id": f"post_{username}", "chat_name": f"IG Post: @{username}",
            "chat_type": "instagram_post", "chat_username": username,
            "chat_link": f"https://instagram.com/{username}",
            "user_id": "", "sender_name": username,
            "timestamp": datetime.now(), "media_type": "text",
            "file_id": None, "local_file_path": None, "reply_to": None,
        })

    async def dump_dom(self, path: str | None = None) -> str:
        path = path or os.path.join(self._user_data_dir, "instagram_dom_dump.html")
        try:
            if not self._page:
                return ""
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            return path
        except Exception as e:
            log_warning(f"dump_dom: {e}")
            return ""

    async def disconnect(self) -> None:
        # اگر از CDP استفاده شده بود، مرورگر را نبند (تا کاربر خودش ببندد)
        if self._is_cdp:
            log_info("🔌 اتصال CDP قطع می‌شود (مرورگر باز می‌ماند)")
            with contextlib.suppress(Exception):
                await self._browser.close()
        else:
            with contextlib.suppress(Exception):
                if self._context:
                    await self._context.close()
            with contextlib.suppress(Exception):
                if self._pw:
                    await self._pw.stop()

        self._context = None
        self._page = None
        self._pw = None
        self._browser = None
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از Instagram")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def check_health(self) -> bool:
        return self._connected