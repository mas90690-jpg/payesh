# collectors/facebook.py
"""
کلکتور فیسبوک (Facebook) — نسخهٔ ۹.۰ (Patchright + رفتار انسانی)

تغییرات کلیدی:
  - 🎯 Patchright جای Playwright (بدون leak های CDP)
  - 🎯 کروم واقعی (channel="chrome")
  - 🎯 رفتار انسانی: mouse move منحنی، مکث تصادفی، اسکرول تدریجی
  - 🎯 See more / See translation فقط اگر وجود داشته باشد
  - 🎯 صبر طولانی تا پست‌ها از skeleton خارج شوند
  - 🎯 ضدتکرار با seen_ids
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
    log_debug, log_error, log_info, log_success, log_warning,
)

# ─── Patchright ───
try:
    from patchright.async_api import async_playwright
except ImportError:
    async_playwright = None

_PLATFORM = "facebook"
_MAX_SEEN = 100000

FACEBOOK_HOME = "https://www.facebook.com/?filter=all&sk=h_chr"

LOGIN_MARKERS = [
    'input[name="email"]',
    'input[name="pass"]',
    'form[data-testid="royal_login_form"]',
]

LOGGEDIN_MARKERS = [
    'div[role="navigation"][aria-label*="Facebook"]',
    'div[aria-label="Facebook"][role="banner"]',
    '[data-pagelet="LeftNav"]',
    'div[role="feed"]',
    'a[href*="/friends/"]',
    'input[aria-label*="Search Facebook"]',
]

_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


class FacebookCollector(BaseCollector):
    def __init__(
        self,
        headless: bool = False,
        poll_seconds: int = 1800,
        max_posts: int = 30,
        read_feed: bool = True,
        read_groups: bool = False,
        max_groups: int = 20,
        scroll_rounds: int = 8,
        delay_between: float = 3.0,
        following_only: bool = False,
        min_text_len: int = 15,
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
        self._following_only = bool(following_only)
        self._min_text_len = int(min_text_len)

        self._pw = None
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()
        self._targets = get_target_groups()
        self._page_lock = asyncio.Lock()
        self._last_scrape_stats: dict[str, int] = {}

        if not hasattr(FacebookCollector, "_connect_lock"):
            FacebookCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        # ─── پروفایل جدید برای Patchright ───
        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "facebook_stealth"
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

        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "facebook_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()
        self._last_run_at = self._load_last_run_at()

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
        return True

    @property
    def supports_websocket(self) -> bool:
        return False

    # ═══════════════════════════════════════════════
    # state
    # ═══════════════════════════════════════════════
    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info("♻️ [facebook:state] بار اول انجام شده")
            else:
                log_info("🆕 [facebook:state] بار اول — تاریخچه")
            return done
        except Exception:
            log_info("🆕 [facebook:state] بار اول — تاریخچه")
            return False

    def _load_last_run_at(self) -> datetime | None:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            s = d.get("last_run_at")
            if s:
                return datetime.fromisoformat(s)
        except Exception:
            pass
        return None

    def _save_state(self):
        try:
            data = {
                "first_run_done": True,
                "first_run_at": getattr(self, "_first_run_at", datetime.now().isoformat()),
                "last_run_at": datetime.now().isoformat(),
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_warning(f"ذخیره state ناموفق: {e}")

    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} پست دیده‌شدهٔ Facebook")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیره seen فیسبوک: {e}")

    # ═══════════════════════════════════════════════
    # رفتار انسانی
    # ═══════════════════════════════════════════════
    async def _human_pause(self, min_s: float = 1.0, max_s: float = 3.5):
        """مکث تصادفی مثل آدم که داره محتوا می‌خونه."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_scroll(self, page, total_px: int = 800):
        """اسکرول با پرش‌های نامنظم و مکث."""
        remaining = total_px
        while remaining > 0:
            step = random.randint(80, 200)
            step = min(step, remaining)
            try:
                await page.mouse.wheel(0, step)
            except Exception:
                break
            remaining -= step
            await self._human_pause(0.3, 1.2)

    async def _human_click(self, page, selector: str) -> bool:
        """کلیک با حرکت موس منحنی (نه مستقیم)."""
        try:
            element = await page.query_selector(selector)
            if not element:
                return False
            box = await element.bounding_box()
            if not box:
                return False

            # کلیک از یه نقطه تصادفی داخل المان (نه مرکز دقیق)
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

            # حرکت موس با steps (مسیر منحنی)
            await page.mouse.move(x, y, steps=random.randint(8, 25))
            await self._human_pause(0.1, 0.3)
            await page.mouse.click(x, y)
            return True
        except Exception:
            return False

    # ═══════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [facebook:connect] از قبل متصل — رد")
            return True

        async with FacebookCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning("⚠️ [facebook:connect] context قدیمی — بستن")
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error("patchright نصب نیست — `pip install patchright`")
                    return False

                use_headless = self.headless if headless is None else headless
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

                # ─── تزریق حداقلی ───
                await self._apply_human_behavior()

                await self._page.goto(FACEBOOK_HOME, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)

                for i in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(f"✅ اتصال فیسبوک برقرار شد — بعد از {i+1} تلاش")
                        return True

                await self.dump_dom()
                log_error("نشست فیسبوک تشخیص داده نشد. `python login.py` (گزینهٔ ۹) را اجرا کن.")
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال فیسبوک: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _apply_human_behavior(self):
        try:
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """)
            log_debug("🛡️ [facebook] human behavior تزریق شد")
        except Exception as e:
            log_warning(f"human behavior: {e}")

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH

        await self._cleanup_profile_locks()

        # ─── Patchright: بهترین تمرین با Chrome واقعی ───
        launch_options: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "channel": "chrome",  # کروم واقعی
            "no_viewport": True,  # ویوپورت ثابت نده
        }

        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_options["executable_path"] = CHROME_PATH
            log_info("مرورگر فیسبوک با CHROME_PATH باز شد")
        else:
            log_info("مرورگر فیسبوک با channel=chrome باز شد")

        try:
            ctx = await self._pw.chromium.launch_persistent_context(**launch_options)
            return ctx
        except Exception as e:
            err = str(e)
            if "already in use" in err or "existing browser" in err or "profile is already" in err:
                log_warning("⚠️ پروفایل قفل — پاک‌سازی")
                self._force_kill_chrome()
                await asyncio.sleep(2)
                await self._cleanup_profile_locks()
                return await self._pw.chromium.launch_persistent_context(**launch_options)
            raise RuntimeError(f"هیچ مرورگری برای فیسبوک باز نشد: {e}")

    async def _cleanup_profile_locks(self):
        for name in _PROFILE_LOCK_FILES:
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
        try:
            import subprocess
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=5)
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
    async def fetch_messages(self, since: datetime = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        effective_since = since
        if effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(f"♻️ [facebook:since] از last_run_at: {self._last_run_at.isoformat()}")
        elif since:
            log_info(f"🎯 [facebook:since] از پارامتر: {since.isoformat()}")

        self._last_scrape_stats = {
            "dom_articles": 0, "skeleton": 0,
            "stored": 0, "skipped_seen": 0, "skipped_short": 0,
            "skipped_no_kw": 0, "skipped_old": 0, "failed": 0,
            "more_clicked": 0, "tr_clicked": 0,
        }

        async with self._page_lock:
            if self._read_feed:
                try:
                    await self._scrape_feed(effective_since=effective_since)
                except Exception as e:
                    log_warning(f"فید فیسبوک: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()

        if not self._first_run_done:
            self._first_run_at = datetime.now().isoformat()
            self._first_run_done = True
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پست از فیسبوک")

        s = self._last_scrape_stats
        log_info(
            f"📊 [facebook:stats] articles={s['dom_articles']} | "
            f"skeleton={s['skeleton']} | "
            f"more={s['more_clicked']} | tr={s['tr_clicked']} | "
            f"stored={s['stored']} | seen={s['skipped_seen']} | "
            f"short={s['skipped_short']} | no-kw={s['skipped_no_kw']} | "
            f"old={s['skipped_old']} | failed={s['failed']}"
        )

        return new_msgs

    # ═══════════════════════════════════════════════
    # اسکرپ فید
    # ═══════════════════════════════════════════════
    async def _scrape_feed(self, effective_since: datetime = None):
        log_info("🏠 [facebook] فید Home (Patchright + رفتار انسانی)")

        try:
            await self._page.goto(FACEBOOK_HOME, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_warning(f"goto فید: {e}")
            return

        log_info("⏳ صبر اولیه ۱۰ ثانیه...")
        await self._human_pause(8, 12)

        processed_links = set()

        for scroll_i in range(self._scroll_rounds):
            await self._human_scroll(self._page, random.randint(600, 1000))
            await self._human_pause(2.0, 4.0)

            articles = await self._page.query_selector_all('div[role="article"]')
            self._last_scrape_stats["dom_articles"] = max(
                self._last_scrape_stats["dom_articles"], len(articles)
            )
            log_info(f"   ↕️ اسکرول {scroll_i+1}/{self._scroll_rounds} | articles={len(articles)}")

            for idx, art in enumerate(articles):
                try:
                    loading = await art.query_selector('[aria-label="Loading..."]')
                    if loading:
                        self._last_scrape_stats["skeleton"] += 1
                        continue

                    try:
                        await art.scroll_into_view_if_needed(timeout=3000)
                        await self._human_pause(0.5, 1.2)
                    except Exception:
                        pass

                    # See more
                    more_btn = await art.query_selector(
                        'div[role="button"]:has-text("See more"), '
                        'span[role="button"]:has-text("See more"), '
                        'div[role="button"]:has-text("See More")'
                    )
                    if more_btn and await more_btn.is_visible():
                        box = await more_btn.bounding_box()
                        if box:
                            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                            await self._page.mouse.move(x, y, steps=random.randint(8, 25))
                            await self._human_pause(0.1, 0.3)
                            await self._page.mouse.click(x, y)
                            self._last_scrape_stats["more_clicked"] += 1
                            log_info("      👆 See more کلیک شد")
                            await self._human_pause(1.0, 2.0)

                    # See translation (فقط اگه واقعاً وجود داشت)
                    tr_btn = await art.query_selector(
                        'div[role="button"]:has-text("See translation"), '
                        'span[role="button"]:has-text("See translation"), '
                        'div[role="button"]:has-text("See Translation")'
                    )
                    if tr_btn and await tr_btn.is_visible():
                        box = await tr_btn.bounding_box()
                        if box:
                            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                            await self._page.mouse.move(x, y, steps=random.randint(8, 25))
                            await self._human_pause(0.1, 0.3)
                            await self._page.mouse.click(x, y)
                            self._last_scrape_stats["tr_clicked"] += 1
                            log_info("      👆 See translation کلیک شد")
                            await self._human_pause(2.0, 2.5)

                    try:
                        text = (await art.inner_text() or "").strip()
                    except Exception:
                        text = ""

                    if not text or len(text) < self._min_text_len:
                        self._last_scrape_stats["skipped_short"] += 1
                        continue

                    link = await art.evaluate("""
                        (el) => {
                            const a = el.querySelector(
                                'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/permalink/"]'
                            );
                            return a ? a.href.split('?')[0] : null;
                        }
                    """)
                    if not link:
                        self._last_scrape_stats["failed"] += 1
                        continue

                    if link in processed_links:
                        continue
                    processed_links.add(link)

                    mid = hashlib.sha256(f"facebook:{link}".encode("utf-8")).hexdigest()[:32]

                    if mid in self._seen_ids:
                        self._last_scrape_stats["skipped_seen"] += 1
                        continue
                    self._seen_ids.add(mid)

                    matched = self._kw.match(text)
                    if not matched:
                        self._last_scrape_stats["skipped_no_kw"] += 1
                        log_info(f"      ⏭️ بدون کلیدواژه | {text[:50]}...")
                        continue

                    log_info(f"      🎯 کلیدواژه: {', '.join(sorted(matched))}")

                    author = ""
                    try:
                        author_el = await art.query_selector('h3 a, h4 a, strong a, a[role="link"]')
                        if author_el:
                            author = (await author_el.inner_text() or "").strip()[:80]
                    except Exception:
                        pass

                    media_type, local_file_path = "text", None
                    try:
                        from core.adapters.web_media import download_dom_image
                        mt, lp = await download_dom_image(self._page, art, self._media_dir, "facebook")
                        if mt:
                            media_type, local_file_path = mt, lp
                    except Exception:
                        pass

                    self._messages_queue.append({
                        "id": mid,
                        "platform": _PLATFORM,
                        "text": text[:5000],
                        "chat_id": "feed",
                        "chat_name": "Facebook: feed",
                        "chat_type": "facebook",
                        "chat_username": None,
                        "chat_link": link,
                        "user_id": author or "",
                        "sender_name": author or "",
                        "timestamp": datetime.now(),
                        "media_type": media_type,
                        "file_id": None,
                        "local_file_path": local_file_path,
                        "keywords": ",".join(sorted(matched)),
                        "reply_to": None,
                    })
                    self._last_scrape_stats["stored"] += 1
                    log_success(f"      ✅ ذخیره | {author or '?'} | kw={len(matched)} | media={media_type} | len={len(text)}")

                    await self._human_pause(1.5, 2.5)

                except Exception as e:
                    self._last_scrape_stats["failed"] += 1
                    log_warning(f"      ❌ [{idx}] {type(e).__name__}: {e}")
                    continue

        log_info(f"🏁 [facebook:scrape] پایان | ذخیره={self._last_scrape_stats['stored']}")

    async def send_message(self, chat_id, chat_type, text: str) -> bool:
        log_info(f"⏭️ [facebook:send] غیرفعال | chat_id={chat_id}")
        return False

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(self._user_data_dir, "facebook_dom_dump.html")
        try:
            if not self._page:
                return ""
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            log_info(f"DOM فیسبوک در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

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