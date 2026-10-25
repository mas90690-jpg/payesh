# collectors/instagram.py
"""
Instagram collector — فقط فالوورها.

اصلاحات:
  - 🎯 فقط /feed/following/ استفاده می‌کنه (نه home)
    → نتیجه: فقط پست‌ها و استوری‌های فالوورها
    → non-followers و suggested حذف می‌شن
  - 🎯 فیلتر «Suggested for you» — قبل از پردازش چک می‌کنه
  - 🎯 فیلتر «Follow» button در استوری — یعنی فالوور نیست
  - ❌ هیچ API key نیاز نیست (فقط لاگین مرورگر)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import log_error, log_info, log_success, log_warning

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

_PLATFORM = "instagram"
_MAX_SEEN = 100000

# ─── URLها ───
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
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()

        if not hasattr(InstagramCollector, "_connect_lock"):
            InstagramCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "instagram"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "instagram")
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "instagram_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

    # ═══════════════════════════════════════════
    # خواص
    # ═══════════════════════════════════════════
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

    # ═══════════════════════════════════════════
    # ضدتکراری
    # ═══════════════════════════════════════════
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

    # ═══════════════════════════════════════════
    # رفتار انسانی
    # ═══════════════════════════════════════════
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

    # ═══════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._context:
            return True

        async with InstagramCollector._connect_lock:
            if self._connected and self._context:
                return True

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست")
                    return False

                use_headless = self.headless if headless is None else headless
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

                # ✅ مستقیم به /feed/following/ (نه home)
                await self._page.goto(
                    FEED_FOLLOWING_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await asyncio.sleep(3)

                for i in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(
                            f"✅ اتصال Instagram (فقط فالوورها) — "
                            f"بعد از {i+1} تلاش"
                        )
                        return True

                log_error(
                    "نشست Instagram نیست — "
                    "`python login.py` گزینهٔ ۸"
                )
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--no-first-run",
        ]
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )

        for name in _PROFILE_LOCK_FILES:
            p = os.path.join(self._user_data_dir, name)
            try:
                if os.path.exists(p):
                    if os.path.isdir(p):
                        import shutil
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
            except Exception:
                pass

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
        except Exception:
            opts.pop("channel", None)
            opts.pop("executable_path", None)
            return await self._pw.chromium.launch_persistent_context(**opts)

    async def _is_logged_in(self) -> bool:
        try:
            url = (self._page.url or "").lower()
            if "/accounts/login" in url:
                return False
            for sel in LOGGEDIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    # ═══════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════
    async def fetch_messages(self, since: datetime = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        async with self._page_lock:
            # ─── ۱) استوری‌های فالوورها ───
            if self._read_stories:
                try:
                    await self._check_stories()
                except Exception as e:
                    log_warning(f"استوری‌ها: {e}")

            # ─── ۲) پست‌های فالوورها ───
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

    # ═══════════════════════════════════════════
    # استوری‌های فالوورها (با فیلتر Suggested)
    # ═══════════════════════════════════════════
    async def _check_stories(self):
        """
        استوری‌های فالوورها رو چک می‌کنه.

        ⚠️ فیلتر:
          - استوری‌های «Suggested» رد می‌شن
          - استوری‌هایی که دکمهٔ «Follow» دارن (یعنی فالوور نیستن) رد می‌شن
        """
        log_info("📖 چک استوری‌های فالوورها...")

        await self._page.goto(
            FEED_FOLLOWING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await self._human_delay(3, 5)

        # ─── پیدا کردن دایره‌های استوری در نوار بالا ───
        story_circles = await self._find_story_circles()

        if not story_circles:
            log_info("ℹ️ استوری‌ای در نوار بالا نیست")
            return

        log_info(f"   {len(story_circles)} دایرهٔ استوری پیدا شد")

        count = 0
        skipped = 0

        for circle in story_circles[: self._max_stories]:
            try:
                # ─── فیلتر: آیا Suggested است؟ ───
                is_suggested = await self._is_suggested_story(circle)
                if is_suggested:
                    skipped += 1
                    continue

                # ─── کلیک روی استوری ───
                await circle.click()
                await asyncio.sleep(random.uniform(
                    self._story_delay_min,
                    self._story_delay_max,
                ))

                # ─── چک دوباره: داخل استوری، دکمهٔ Follow داری؟ ───
                has_follow = await self._story_has_follow_button()
                if has_follow:
                    skipped += 1
                    await self._page.keyboard.press("Escape")
                    await self._human_delay(1, 2)
                    continue

                # ─── خوندن متن ───
                text = await self._extract_story_text()
                username = await self._extract_story_username()

                if text:
                    await self._process_story(username, text)
                    count += 1

                await self._page.keyboard.press("Escape")
                await self._human_delay(1, 2)

            except Exception as e:
                log_warning(f"خطا در استوری: {e}")
                try:
                    await self._page.keyboard.press("Escape")
                except Exception:
                    pass

        log_info(
            f"   ✅ {count} استوری فالوور | "
            f"⏭️ {skipped} suggested رد شد"
        )

    async def _find_story_circles(self) -> list:
        """دایره‌های استوری در نوار بالا رو پیدا می‌کنه."""
        selectors = [
            'div[role="button"] canvas',
            'header ul li div[role="button"]',
            'div[role="menu"] + div ul li',
        ]
        for sel in selectors:
            try:
                items = await self._page.query_selector_all(sel)
                if items:
                    return items
            except Exception:
                continue
        return []

    async def _is_suggested_story(self, circle) -> bool:
        """
        آیا این دایره استوری Suggested است؟
        (با چک کردن متن داخل دایره)
        """
        try:
            text = await circle.inner_text()
            text = (text or "").lower()
            if "suggested" in text or "پیشنهاد" in text:
                return True
        except Exception:
            pass

        # ─── چک: آیا کنارش دکمهٔ + هست؟ (suggested) ───
        try:
            parent = await circle.evaluate_handle("el => el.parentElement")
            if parent:
                plus = await parent.query_selector(
                    'svg[aria-label*="plus" i], '
                    'svg[aria-label*="Follow" i]'
                )
                if plus:
                    return True
        except Exception:
            pass

        return False

    async def _story_has_follow_button(self) -> bool:
        """داخل استوری، دکمهٔ Follow داره؟ (یعنی فالوور نیست)"""
        try:
            el = await self._page.query_selector(
                'button:has-text("Follow"), '
                'div[role="button"]:has-text("Follow"), '
                'button:has-text("دنبال")'
            )
            if el and await el.is_visible():
                return True
        except Exception:
            pass
        return False

    async def _extract_story_text(self) -> str:
        try:
            el = await self._page.query_selector(
                'div[role="dialog"] span, section span[dir="auto"]'
            )
            if el:
                t = await el.inner_text()
                return (t or "").strip()
        except Exception:
            pass
        return ""

    async def _extract_story_username(self) -> str:
        try:
            el = await self._page.query_selector(
                'div[role="dialog"] header a[href^="/"], '
                'section header a[href^="/"]'
            )
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

        # 🔧 FIX: رشته از "story:" استفاده می‌کند (نه "post:")
        mid = hashlib.sha256(
            f"story:{username}:{text[:200]}".encode("utf-8")
        ).hexdigest()[:16]
        uniq = f"ig_story:{mid}"

        if uniq in self._seen_ids:
            return
        self._seen_ids.add(uniq)

        self._messages_queue.append({
            "id": mid,
            "platform": _PLATFORM,
            "text": text,
            "chat_id": f"story_{username}",
            "chat_name": f"IG Story: @{username}",
            "chat_type": "instagram_story",
            "chat_username": username,
            "chat_link": f"https://instagram.com/{username}",
            "user_id": "",
            "sender_name": username,
            "timestamp": datetime.now(),
            "media_type": "text",
            "file_id": None,
            "local_file_path": None,
            "reply_to": None,
        })

    # ═══════════════════════════════════════════
    # پست‌های فالوورها (با فیلتر Suggested)
    # ═══════════════════════════════════════════
    async def _check_posts(self):
        """
        پست‌های فالوورها رو چک می‌کنه.

        ⚠️ فیلتر:
          - پست‌هایی که بالاشون «Suggested for you» هست رد می‌شن
        """
        log_info("📖 چک پست‌های فالوورها...")

        await self._page.goto(
            FEED_FOLLOWING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await self._human_delay(3, 5)

        # ─── اسکرول برای لود پست‌ها ───
        for _ in range(3):
            await self._human_scroll(700)
            await self._human_delay(1, 2)

        # ─── پیدا کردن articleها ───
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
                # ─── فیلتر: Suggested? ───
                is_suggested = await self._is_suggested_post(post)
                if is_suggested:
                    skipped += 1
                    continue

                # ─── متن کپشن ───
                text = await self._extract_post_text(post)
                username = await self._extract_post_username(post)

                if text and self._kw.has_match(text):
                    await self._process_post(username, text)
                    count += 1

            except Exception as e:
                log_warning(f"خطا در پست: {e}")

        log_info(
            f"   ✅ {count} پست فالوور | "
            f"⏭️ {skipped} suggested رد شد"
        )

    async def _is_suggested_post(self, post) -> bool:
        """آیا این پست Suggested for you است؟"""
        try:
            # ─── چک: آیا متن «Suggested for you» داره؟ ───
            text = (await post.inner_text() or "").lower()
            if (
                "suggested for you" in text
                or "پیشنهادی برای شما" in text
                or "پیشنهاد شده" in text
            ):
                return True
        except Exception:
            pass

        # ─── چک: آیا دکمهٔ Follow کنارش هست؟ ───
        try:
            follow = await post.query_selector(
                'button:has-text("Follow"), '
                'div[role="button"]:has-text("Follow")'
            )
            if follow:
                return True
        except Exception:
            pass

        return False

    async def _extract_post_text(self, post) -> str:
        """متن کپشن پست رو می‌خونه."""
        try:
            el = await post.query_selector(
                'h1, div[role="button"] span[dir="auto"], '
                'span[dir="auto"]'
            )
            if el:
                t = await el.inner_text()
                return (t or "").strip()
        except Exception:
            pass
        return ""

    async def _extract_post_username(self, post) -> str:
        """یوزرنیم پیجی که پست گذاشته."""
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
        # 🔧 FIX: رشته از "post:" استفاده می‌کند (نه "story:")
        mid = hashlib.sha256(
            f"post:{username}:{text[:200]}".encode("utf-8")
        ).hexdigest()[:16]
        uniq = f"ig_post:{mid}"

        if uniq in self._seen_ids:
            return
        self._seen_ids.add(uniq)

        self._messages_queue.append({
            "id": mid,
            "platform": _PLATFORM,
            "text": text[:5000],
            "chat_id": f"post_{username}",
            "chat_name": f"IG Post: @{username}",
            "chat_type": "instagram_post",
            "chat_username": username,
            "chat_link": f"https://instagram.com/{username}",
            "user_id": "",
            "sender_name": username,
            "timestamp": datetime.now(),
            "media_type": "text",
            "file_id": None,
            "local_file_path": None,
            "reply_to": None,
        })

    # ═══════════════════════════════════════════
    # disconnect
    # ═══════════════════════════════════════════
    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "instagram_dom_dump.html"
        )
        try:
            if not self._page:
                return ""
            html = await self._page.content()
            def _write():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            await asyncio.to_thread(_write)
            return path
        except Exception as e:
            log_warning(f"dump_dom: {e}")
            return ""

    async def disconnect(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._context = None
        self._page = None
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از Instagram")

    async def check_health(self) -> bool:
        return self._connected