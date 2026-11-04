# collectors/facebook.py
"""
کلکتور فیسبوک (Facebook) — کاربر عادی با Playwright.

استراتژی (نسخهٔ ۱ — این نسخه):
  - نشست واقعی مرورگر (login.py → گزینهٔ ۹)
  - دو منبع:
      ۱) فید شخصی (/  یا /home.php)
      ۲) گروه‌های عضوشده (/groups/feed/)
  - فیلتر کلیدواژه روی title+text
  - ضدتکراری ماندگار روی دیسک
  - رفتار انسانی (اسکرول، تأخیر متغیر)
  - BROWSER_LOCK استاندارد پروژه
  - connect idempotent
  - _page_lock برای fetch
  - dump_dom

فایل‌های ورودی:
  - storage/sessions/facebook/    ← پروفایل مرورگر (نشست)
  - data/sources/target_groups.txt ← لیست گروه‌های هدف (اختیاری)
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
from analyzers.target_filter import get_target_groups
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_error, log_info, log_success, log_warning,
)

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# ═══════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════
_PLATFORM = "facebook"
_MAX_SEEN = 100000

FACEBOOK_HOME = "https://www.facebook.com/"
FACEBOOK_GROUPS_FEED = "https://www.facebook.com/groups/feed/"

# ─── سلکتورها ───
LOGIN_MARKERS = [
    'input[name="email"]',
    'input[name="pass"]',
    'form[data-testid="royal_login_form"]',
]

LOGGEDIN_MARKERS = [
    'div[role="navigation"][aria-label*="Facebook"]',
    'div[aria-label="Facebook"][role="banner"]',
    '[data-pagelet="LeftNav"]',
    '[data-pagelet="ProfileTilesFeed_0"]',
    'div[role="feed"]',
    'a[href*="/friends/"]',
]

_POST_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]

# 🎯 انتخابگرهای پست در فید و گروه
POST_SELECTORS = [
    'div[role="article"]',                       # پایدارترین
    'div[data-pagelet^="FeedUnit"]',
    'div[data-pagelet^="GroupFeed"]',
]

# 🎯 انتخابگرهای متن پست
POST_TEXT_SELECTORS = [
    'div[data-ad-preview="message"]',
    'div[data-ad-comet-preview="message"]',
    'div[dir="auto"][style*="text-align"]',
    'div[dir="auto"]',
]

# 🎯 انتخابگرهای لینک پست
POST_LINK_SELECTORS = [
    'a[href*="/posts/"]',
    'a[href*="/permalink/"]',
    'a[href*="story_fbid="]',
    'a[href*="/groups/"][href*="/posts/"]',
]

# 🎯 انتخابگرهای نام نویسنده
POST_AUTHOR_SELECTORS = [
    'h3 a[role="link"]',
    'h4 a[role="link"]',
    'strong a[role="link"]',
    'span a[role="link"]',
]


class FacebookCollector(BaseCollector):
    def __init__(
        self,
        headless: bool = True,
        poll_seconds: int = 1800,
        max_posts: int = 30,
        read_feed: bool = True,
        read_groups: bool = True,
        max_groups: int = 20,
        scroll_rounds: int = 4,
        delay_between: float = 3.0,
        **kwargs,
    ):
        super().__init__()
        self.headless = bool(headless)
        self._poll_seconds = int(poll_seconds)
        self._max_posts = int(max_posts)
        self._read_feed = bool(read_feed)
        self._read_groups = bool(read_groups)
        self._max_groups = int(max_groups)
        self._scroll_rounds = int(scroll_rounds)
        self._delay_between = float(delay_between)

        self._pw = None
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()
        self._targets = get_target_groups()
        self._page_lock = asyncio.Lock()

        if not hasattr(FacebookCollector, "_connect_lock"):
            FacebookCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "facebook"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(
            STORAGE_DIR, "media", "facebook"
        )
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "facebook_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

    # ═══════════════════════════════════════════════
    # خواص
    # ═══════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════
    # ضدتکراری ماندگار
    # ═══════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} پست دیده‌شدهٔ Facebook"
                )
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        """ذخیرهٔ seen — atomic (tmp + replace + fsync)."""
        tmp = self._seen_path + ".tmp"
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:]
                )
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._seen_path)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری فیسبوک ناموفق: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # ═══════════════════════════════════════════════
    # رفتار انسانی
    # ═══════════════════════════════════════════════
    async def _human_delay(
        self, min_s: float = 0.5, max_s: float = 2.5
    ):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_scroll(self, amount: int = 1200):
        sign = 1 if amount > 0 else -1
        remaining = abs(amount)
        while remaining > 0:
            step = random.randint(80, 220)
            step = min(step, remaining)
            try:
                await self._page.mouse.wheel(0, sign * step)
            except Exception:
                break
            remaining -= step
            await self._human_delay(0.15, 0.4)

    # ═══════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(
        self, headless: bool | None = None
    ) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [facebook:connect] از قبل متصل — رد")
            return True

        async with FacebookCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "⚠️ [facebook:connect] context قدیمی — بستن"
                )
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست")
                    return False

                use_headless = (
                    self.headless if headless is None else headless
                )
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(
                    use_headless
                )
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

                await self._page.goto(
                    FACEBOOK_HOME,
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
                            f"✅ اتصال فیسبوک برقرار شد — "
                            f"بعد از {i + 1} تلاش"
                        )
                        return True

                await self.dump_dom()
                log_error(
                    "نشست فیسبوک تشخیص داده نشد. "
                    "`python login.py` (گزینهٔ ۹) را اجرا کن."
                )
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال فیسبوک: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH

        base_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
        ]
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )

        await self._cleanup_profile_locks()

        launch_options: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "viewport": {"width": 1366, "height": 900},
            "user_agent": ua,
            "args": base_args,
            "locale": "en-US",
        }

        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_options["executable_path"] = CHROME_PATH
            source = f"CHROME_PATH={CHROME_PATH}"
        else:
            launch_options["channel"] = "chrome"
            source = "channel=chrome"

        try:
            ctx = await self._pw.chromium.launch_persistent_context(
                **launch_options
            )
            log_info(f"مرورگر فیسبوک باز شد | منبع: {source}")
            return ctx
        except Exception as e:
            err = str(e)
            if (
                "already in use" in err
                or "existing browser" in err
                or "profile is already" in err
            ):
                log_warning("⚠️ پروفایل قفل — پاک‌سازی")
                self._force_kill_chrome()
                await asyncio.sleep(2)
                await self._cleanup_profile_locks()
                return (
                    await self._pw.chromium
                    .launch_persistent_context(**launch_options)
                )
            raise RuntimeError(
                f"هیچ مرورگری برای فیسبوک باز نشد: {e}"
            )

    async def _cleanup_profile_locks(self):
        for name in _POST_LOCK_FILES:
            path = os.path.join(self._user_data_dir, name)
            try:
                if os.path.exists(path):
                    if os.path.isdir(path):
                        import shutil
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.remove(path)
            except Exception:
                pass

    def _force_kill_chrome(self):
        """فقط پروفایل مرورگر خود ربات را می‌کشد، نه Chrome کاربر."""
        import subprocess
        import re as _re
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='chrome.exe'",
                 "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore",
            ).stdout
        except Exception:
            return

        target = os.path.normcase(self._user_data_dir)
        for line in out.splitlines():
            if "--user-data-dir" not in line:
                continue
            if target not in os.path.normcase(line):
                continue
            m = _re.search(r"(\d+)\s*$", line.strip())
            if not m:
                continue
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", m.group(1), "/T"],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

    async def _is_logged_in(self) -> bool:
        try:
            url = (self._page.url or "").lower()
            if "/login" in url or "/checkpoint" in url:
                return False

            for sel in LOGIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return False
                except Exception:
                    continue

            for sel in LOGGEDIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    # ═══════════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════════
    async def fetch_messages(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        async with self._page_lock:
            if self._read_feed:
                try:
                    await self._scrape_feed()
                except Exception as e:
                    log_warning(f"فید فیسبوک: {e}")

            if self._read_groups:
                try:
                    await self._scrape_groups()
                except Exception as e:
                    log_warning(f"گروه‌های فیسبوک: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پست از فیسبوک")
        return new_msgs

    # ═══════════════════════════════════════════════
    # scrape فید شخصی
    # ═══════════════════════════════════════════════
    async def _scrape_feed(self):
        log_info("🏠 [facebook] فید شخصی...")
        try:
            await self._page.goto(
                FACEBOOK_HOME,
                wait_until="domcontentloaded",
                timeout=60000,
            )
        except Exception as e:
            log_warning(f"goto فید: {e}")
            return

        await self._human_delay(4, 6)

        for i in range(self._scroll_rounds):
            await self._human_scroll(random.randint(800, 1400))
            await self._human_delay(1.5, 3)
            if (i + 1) % 2 == 0:
                log_info(f"   ↕️ اسکرول {i + 1}/{self._scroll_rounds}")

        posts = await self._find_posts()
        if not posts:
            log_warning("⚠️ پستی در فید پیدا نشد")
            return

        log_info(f"   📄 {len(posts)} پست در فید")
        selected = posts[: self._max_posts]
        count = await self._harvest_posts(selected, source="feed")

        if count:
            log_success(f"🏠 فید: {count} پست جدید")

    # ═══════════════════════════════════════════════
    # scrape گروه‌ها
    # ═══════════════════════════════════════════════
    async def _scrape_groups(self):
        log_info("👥 [facebook] گروه‌های عضوشده...")
        try:
            await self._page.goto(
                FACEBOOK_GROUPS_FEED,
                wait_until="domcontentloaded",
                timeout=60000,
            )
        except Exception as e:
            log_warning(f"goto گروه‌ها: {e}")
            return

        await self._human_delay(4, 6)

        for i in range(self._scroll_rounds):
            await self._human_scroll(random.randint(1000, 1500))
            await self._human_delay(1.5, 3)
            if (i + 1) % 2 == 0:
                log_info(f"   ↕️ اسکرول {i + 1}/{self._scroll_rounds}")

        posts = await self._find_posts()
        if not posts:
            log_warning("⚠️ پستی در گروه‌ها پیدا نشد")
            return

        log_info(f"   📄 {len(posts)} پست در گروه‌ها")
        selected = posts[: self._max_posts]
        count = await self._harvest_posts(selected, source="groups")

        if count:
            log_success(f"👥 گروه‌ها: {count} پست جدید")

    # ═══════════════════════════════════════════════
    # پیدا کردن پست‌ها
    # ═══════════════════════════════════════════════
    async def _find_posts(self):
        for attempt in range(8):
            for sel in POST_SELECTORS:
                try:
                    posts = await self._page.query_selector_all(sel)
                    if posts:
                        log_info(
                            f"   ✅ {len(posts)} پست با `{sel}`"
                        )
                        return posts
                except Exception:
                    continue
            await asyncio.sleep(1)
        return []

    # ═══════════════════════════════════════════════
    # harvest
    # ═══════════════════════════════════════════════
    async def _harvest_posts(
        self, posts, source: str
    ) -> int:
        count = 0
        total = len(posts)
        log_info(f"🔬 [facebook:harvest] بررسی {total} پست...")

        for idx, post in enumerate(posts, start=1):
            try:
                log_info(f"   ▶️ [{idx}/{total}]")

                # اسکرول به پست
                try:
                    await post.scroll_into_view_if_needed()
                    await self._human_delay(0.3, 0.8)
                except Exception:
                    pass

                # ─── متن پست ───
                text = await self._extract_post_text(post)
                if not text or len(text) < 20:
                    log_info("       ⏭️ متن خیلی کوتاه")
                    continue

                # ─── نویسنده ───
                author = await self._extract_author(post)

                # ─── کلیدواژه ───
                if not self._kw.has_match(text):
                    log_info("       ⏭️ بدون کلیدواژه")
                    continue

                log_info(
                    f"       🎯 کلیدواژه پیدا شد | "
                    f"author={author or '?'}"
                )

                # ─── لینک پست ───
                permalink = await self._extract_permalink(post)

                # ─── ضدتکراری ───
                mid = hashlib.sha256(
                    f"{author}:{text[:500]}".encode("utf-8")
                ).hexdigest()[:16]

                if mid in self._seen_ids:
                    log_info(f"       ⏭️ تکراری | {mid[:20]}")
                    continue
                self._seen_ids.add(mid)

                # ─── عکس ───
                media_type, local_path = "text", None
                try:
                    from core.adapters.web_media import download_dom_image

                    mt, lp = await download_dom_image(
                        self._page, post, self._media_dir, "facebook"
                    )
                    if mt:
                        media_type, local_path = mt, lp
                except Exception:
                    pass

                # ─── ذخیره ───
                self._messages_queue.append({
                    "id": mid,
                    "platform": _PLATFORM,
                    "text": text[:5000],
                    "chat_id": source,
                    "chat_name": f"Facebook: {source}",
                    "chat_type": "facebook",
                    "chat_username": None,
                    "chat_link": permalink,
                    "user_id": author or "",
                    "sender_name": author or "",
                    "timestamp": datetime.now(),
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_path,
                    "reply_to": None,
                })
                count += 1
                log_success(
                    f"       ✅ ذخیره | author={author or '?'}"
                )

                await self._human_delay(0.3, 0.9)

            except Exception as e:
                log_warning(f"       ❌ [{idx}] {type(e).__name__}: {e}")
                continue

        log_info(
            f"🔬 [facebook:harvest] پایان | "
            f"کل={total} | ذخیره={count}"
        )
        return count

    # ═══════════════════════════════════════════════
    # استخراج متن پست
    # ═══════════════════════════════════════════════
    async def _extract_post_text(self, post) -> str:
        """متن کپشن پست رو می‌خونه (سلکتورهای جدید فیسبوک)."""
        # ۱. سلکتورهای جدید (اولویت‌دار)
        for sel in (
            'div[data-ad-preview="message"]',   # سلکتور اصلی متن پست
            'div[dir="auto"]',                 # fallback
            'span[dir="auto"]',                # fallback
            'div[data-ad-comet-preview="message"]',
        ):
            try:
                el = await post.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t and len(t) > 10:  # حداقل ۱۰ کاراکتر
                        return t
            except Exception:
                continue

        # ۲. fallback: کل متن پست
        try:
            return (await post.inner_text() or "").strip()
        except Exception:
            return ""

    # ═══════════════════════════════════════════════
    # استخراج نویسنده
    # ═══════════════════════════════════════════════

    async def _extract_author(self, post) -> str:
        for sel in POST_AUTHOR_SELECTORS:
            try:
                el = await post.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t and 2 < len(t) < 80:
                        return t.split("\n")[0].strip()
            except Exception:
                continue
        return ""

    # ═══════════════════════════════════════════════
    # استخراج لینک پست
    # ═══════════════════════════════════════════════
    async def _extract_permalink(self, post) -> str | None:
        for sel in POST_LINK_SELECTORS:
            try:
                a = await post.query_selector(sel)
                if a:
                    href = await a.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            href = (
                                "https://www.facebook.com" + href
                            )
                        return href.split("?")[0]
            except Exception:
                continue
        return None

    # ═══════════════════════════════════════════════
    # send_message — غیرفعال (فقط‌خواندنی)
    # ═══════════════════════════════════════════════
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        log_info(
            f"⏭️ [facebook:send] غیرفعال | chat_id={chat_id}"
        )
        return False

    # ═══════════════════════════════════════════════
    # dump_dom
    # ═══════════════════════════════════════════════
    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "facebook_dom_dump.html"
        )
        try:
            if not self._page:
                log_warning("dump_dom: صفحه‌ای نیست")
                return ""
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            log_info(f"DOM فیسبوک در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

    # ═══════════════════════════════════════════════
    # disconnect
    # ═══════════════════════════════════════════════
    async def disconnect(self) -> None:
        try:
            if self._page:
                await self._page.close()
        except Exception:
            pass
        self._page = None
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._context = None
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از فیسبوک")

    async def check_health(self) -> bool:
        return self._connected
