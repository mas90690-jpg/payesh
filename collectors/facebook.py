# collectors/facebook.py
"""
کلکتور فیسبوک (Facebook) — کاربر عادی با Playwright.

نسخهٔ ۲ (این نسخه — مثل LinkedIn):
  - 🎯 Sort → Most recent (مثل LinkedIn)
  - 🎯 کلیک «See more» روی هر پست
  - 🎯 کلیک «See translation» روی هر پست
  - 🎯 استخراج متن با سلکتورهای واقعی فیسبوک 2026
  - 🎯 Smart Wait تا رندر پست‌های واقعی (غیر skeleton)
  - 🎯 فیلتر فالوور/غیرفالوور (مثل LinkedIn)
  - 🎯 ضدتکراری ماندگار
  - 🎯 BROWSER_LOCK استاندارد
  - 🎯 connect idempotent
  - 🎯 _page_lock برای fetch
  - ✅ حفظ همهٔ امکانات نسخهٔ ۱ (فید + گروه‌ها)

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
import re
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from analyzers.target_filter import get_target_groups
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug, log_error, log_info, log_success, log_warning,
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

# 🎯 URL ها (از اسکرین‌شات تأیید شد)
FACEBOOK_HOME = "https://www.facebook.com/?filter=all&sk=h_chr"
FACEBOOK_HOME_FOLLOWING = "https://www.facebook.com/?filter=following"
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
    'input[aria-label*="Search Facebook"]',
    'div[role="banner"] svg',
]

_POST_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]

# 🎯 سلکتورهای پست
POST_SELECTORS = [
    'div[role="article"]',
    'div[data-pagelet^="FeedUnit"]',
    'div[data-pagelet^="GroupFeed"]',
]

# 🎯 سلکتورهای متن پست (به ترتیب اولویت)
POST_TEXT_SELECTORS = [
    # ─── دقیق‌ترین (از DOM واقعی 2026) ───
    'div.xdj266r.x14z9mp.xat24cr.x1lziwak.x1vvkbs.x126k92a > div[dir="auto"]',
    'div[dir="auto"][style*="text-align"]',
    # ─── ad-preview (قدیمی ولی هنوز کار می‌کند) ───
    'div[data-ad-preview="message"]',
    'div[data-ad-comet-preview="message"]',
    'div[data-ad-rendering-role="story_message"]',
    # ─── fallback ───
    'div[dir="auto"]',
]

# 🎯 سلکتورهای لینک پست
POST_LINK_SELECTORS = [
    'a[href*="/posts/"]',
    'a[href*="/permalink/"]',
    'a[href*="story_fbid="]',
    'a[href*="/groups/"][href*="/posts/"]',
]

# 🎯 سلکتورهای نویسنده
POST_AUTHOR_SELECTORS = [
    'h3 a[role="link"]',
    'h4 a[role="link"]',
    'strong a[role="link"]',
    'span a[role="link"]',
    'a[role="link"][href*="/profile.php"]',
    'a[role="link"][href*="facebook.com/"]',
]

# 🎯 UI markers (برای فیلتر)
_UI_TEXT_MARKERS = {
    "see more", "see more…", "see more...",
    "show more", "see all", "more",
    "see translation", "see original",
    "show translation", "translate",
    "following", "follow", "like", "comment", "share",
    "·", "…", "...", "sponsored", "suggested for you",
    "مشاهدهٔ بیشتر", "بیشتر", "ادامه",
    "دیدن ترجمه", "مشاهدهٔ ترجمه", "ترجمه",
    "دنبال", "پسندیدن", "نظر دادن", "اشتراک",
}


class FacebookCollector(BaseCollector):
    def __init__(
        self,
        headless: bool = False,
        poll_seconds: int = 1800,
        max_posts: int = 30,
        read_feed: bool = True,
        read_groups: bool = True,
        max_groups: int = 20,
        scroll_rounds: int = 4,
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
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:]
                )
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری فیسبوک ناموفق: {e}")

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

                # 🎯 anti-detection (مثل YouTube)
                await self._apply_stealth()

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
                            f"بعد از {i+1} تلاش"
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

    async def _apply_stealth(self):
        """حذف نشانه‌های webdriver — نسخه قوی (مثل YouTube)."""
        try:
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {name: 'Chrome PDF Plugin'},
                        {name: 'Chrome PDF Viewer'},
                        {name: 'Native Client'}
                    ]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (params) => (
                    params.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery(params)
                );
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8
                });
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8
                });
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(p) {
                    if (p === 37445) return 'Intel Inc.';
                    if (p === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter.call(this, p);
                };
            """)
            log_debug("🛡️ [facebook] stealth قوی تزریق شد")
        except Exception as e:
            log_warning(f"stealth: {e}")
            
            
    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH

        base_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
        ]
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
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
        try:
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
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
    # scrape فید
    # ═══════════════════════════════════════════════
    async def _scrape_feed(self):
        log_info("🏠 [facebook] فید Home — ترتیب زمانی")

        try:
            await self._page.goto(
                FACEBOOK_HOME,
                wait_until="domcontentloaded",
                timeout=60000,
            )
        except Exception as e:
            log_warning(f"goto فید: {e}")
            return

        # ─── صبر اولیه ───
        await self._human_delay(5, 7)

        # ─── Sort → Recent ───
        log_info("🔍 پیدا کردن دکمهٔ Recent فیسبوک...")
        sorted_ok = await self._click_sort_recent()

        # ─── Smart Wait ───
        log_info("⏳ [facebook] Smart Wait تا رندر پست‌ها...")
        real_count = await self._wait_for_real_posts(timeout_s=60)

        if real_count == 0:
            log_warning(
                "⚠️ [facebook] هیچ پست واقعی لود نشد — "
                "فیسبوک anti-bot دارد"
            )
            return

        log_success(
            f"✅ [facebook] {real_count} پست واقعی لود شد | "
            f"sort_recent={sorted_ok}"
        )

        # ─── اسکرول ───
        for i in range(self._scroll_rounds):
            await self._human_scroll(random.randint(800, 1400))
            await self._human_delay(1.5, 3)
            if (i + 1) % 2 == 0:
                log_info(f"   ↕️ اسکرول {i+1}/{self._scroll_rounds}")

        posts = await self._find_posts()
        if not posts:
            log_warning("⚠️ پستی در فید پیدا نشد")
            return

        log_info(f"   📄 {len(posts)} پست در فید")
        selected = posts[: self._max_posts]
        count = await self._harvest_posts(
            selected, source="feed",
            following_only=self._following_only,
        )

        if count:
            log_success(f"🏠 فید: {count} پست جدید")

    # ═══════════════════════════════════════════════
    # Sort → Recent
    # ═══════════════════════════════════════════════
    async def _click_sort_recent(self) -> bool:
        """دکمهٔ sort فیسبوک را باز می‌کند و Most Recent را می‌زند."""
        await asyncio.sleep(2)

        # ─── چک از قبل Recent هستیم؟ ───
        try:
            current = await self._page.evaluate("""
                () => {
                    const all = document.querySelectorAll(
                        'span, div[role="button"], p'
                    );
                    for (const el of all) {
                        const t = (el.innerText || '').trim();
                        const low = t.toLowerCase();
                        if (low === 'most recent' || low === 'most recent first'
                            || low === 'جدیدترین') {
                            return t;
                        }
                        if (low === 'top' || low === 'featured'
                            || low === 'برتر') {
                            return 'NOT_RECENT:' + t;
                        }
                    }
                    return '';
                }
            """)
            if current and not current.startswith("NOT_RECENT"):
                log_success(f"✅ فیسبوک از قبل Recent | {current}")
                return True
            if current:
                log_info(f"   current: {current} — نیاز به تغییر")
        except Exception:
            pass

        # ─── باز کردن dropdown sort ───
        log_info("   🔽 باز کردن dropdown sort...")
        try:
            opened = await self._page.evaluate("""
                () => {
                    const candidates = document.querySelectorAll(
                        'div[role="button"], span, a, p'
                    );
                    const targets = [
                        'top', 'featured', 'most recent',
                        'most recent first',
                        'برتر', 'ویژه', 'جدیدترین',
                    ];
                    for (const el of candidates) {
                        const t = (el.innerText || '').trim().toLowerCase();
                        if (targets.includes(t)) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            if opened:
                log_success("   ✅ dropdown باز شد")
            else:
                log_warning("   ⚠️ dropdown پیدا نشد")
                return False
        except Exception as e:
            log_warning(f"   ⚠️ dropdown: {e}")
            return False

        await asyncio.sleep(1.5)

        # ─── کلیک Most Recent ───
        log_info("   🎯 کلیک روی 'Most recent'...")
        try:
            clicked = await self._page.evaluate("""
                () => {
                    const targets = [
                        'most recent', 'most recent first',
                        'جدیدترین', 'تازه‌ترین',
                    ];
                    const all = document.querySelectorAll(
                        'div[role="menuitem"], div[role="option"], '
                        + 'span, div, p'
                    );
                    for (const el of all) {
                        const t = (el.innerText || '').trim().toLowerCase();
                        if (targets.includes(t)) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            if clicked:
                log_success("✅ Sort → Most recent")
                await asyncio.sleep(3)
                return True
        except Exception as e:
            log_warning(f"   ⚠️ کلیک Recent: {e}")

        log_warning("⚠️ 'Most recent' پیدا نشد")
        return False

    # ═══════════════════════════════════════════════
    # Smart Wait
    # ═══════════════════════════════════════════════
    async def _wait_for_real_posts(self, timeout_s: int = 60) -> int:
        """صبر تا رندر پست‌های واقعی (غیر skeleton)."""
        import time as _t
        t0 = _t.monotonic()
        last_log = -5

        while True:
            elapsed = _t.monotonic() - t0
            if elapsed > timeout_s:
                break

            state = await self._page.evaluate("""
                () => {
                    const articles = document.querySelectorAll(
                        'div[role="article"]'
                    );
                    let loading = 0;
                    let real = 0;
                    for (const a of articles) {
                        if (a.querySelector('[aria-label="Loading..."]')) {
                            loading++;
                        } else if ((a.innerText || '').trim().length >= 30) {
                            real++;
                        }
                    }
                    return { total: articles.length, loading, real };
                }
            """)

            total = state.get("total", 0)
            loading = state.get("loading", 0)
            real = state.get("real", 0)

            if elapsed - last_log >= 5:
                last_log = elapsed
                log_info(
                    f"   ⏳ [{int(elapsed)}s] "
                    f"articles={total} | real={real} | loading={loading}"
                )

            if real >= 1 and loading == 0:
                return real

            try:
                await self._page.mouse.wheel(0, 1200)
            except Exception:
                pass
            await asyncio.sleep(1)

        return 0

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
        await self._wait_for_real_posts(timeout_s=45)

        for i in range(self._scroll_rounds):
            await self._human_scroll(random.randint(1000, 1500))
            await self._human_delay(1.5, 3)
            if (i + 1) % 2 == 0:
                log_info(f"   ↕️ اسکرول {i+1}/{self._scroll_rounds}")

        posts = await self._find_posts()
        if not posts:
            log_warning("⚠️ پستی در گروه‌ها پیدا نشد")
            return

        log_info(f"   📄 {len(posts)} پست در گروه‌ها")
        selected = posts[: self._max_posts]
        count = await self._harvest_posts(
            selected, source="groups", following_only=False,
        )

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
    # چک فالوور (مثل LinkedIn)
    # ═══════════════════════════════════════════════
    async def _is_follower_post(self, post) -> bool | None:
        """آیا از فالوورها است؟ (مثل LinkedIn)"""
        try:
            header = await post.evaluate("""
                (el) => (el.innerText || '').slice(0, 400)
            """)
            if not header:
                return None
            low = header.lower()

            # Sponsored / Suggested
            if "sponsored" in low or "suggested for you" in low:
                return False
            if "تبلیغ" in low or "پیشنهادی" in low:
                return False

            # Following
            if re.search(r"\bfollowing\b", low):
                return True
            if "دنبال می‌کنید" in low:
                return True

            # Follow (بدون Following)
            if re.search(r"\bfollow\b", low):
                return False
            if "دنبال کنید" in low:
                return False

            return None
        except Exception:
            return None

    # ═══════════════════════════════════════════════
    # کلیک See more
    # ═══════════════════════════════════════════════
    async def _click_see_more(self, post) -> bool:
        """کلیک «See more» در پست."""
        try:
            clicked = await post.evaluate("""
                (el) => {
                    const targets = [
                        'see more', 'see more…', 'see more...',
                        'show more', 'see all', 'more',
                        'مشاهدهٔ بیشتر', 'بیشتر', 'ادامه', 'نمایش بیشتر',
                    ];
                    const all = el.querySelectorAll(
                        'a[role="link"], div[role="button"], '
                        + 'span[role="button"], span, a, div'
                    );
                    for (const x of all) {
                        const t = (x.innerText || '').trim().toLowerCase();
                        if (!t || t.length > 25) continue;
                        for (const w of targets) {
                            if (t === w || t === w + '…' || t === w + '...') {
                                const rect = x.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    try {
                                        x.scrollIntoView({block:'center'});
                                    } catch(e) {}
                                    x.click();
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }
            """)
            return bool(clicked)
        except Exception:
            return False

    # ═══════════════════════════════════════════════
    # کلیک See translation
    # ═══════════════════════════════════════════════
    async def _click_see_translation(self, post) -> bool:
        """کلیک «See translation» در پست."""
        try:
            clicked = await post.evaluate("""
                (el) => {
                    const targets = [
                        'see translation', 'see original',
                        'show translation', 'translate',
                        'دیدن ترجمه', 'مشاهدهٔ ترجمه', 'ترجمه',
                    ];
                    const all = el.querySelectorAll(
                        'a[role="link"], div[role="button"], '
                        + 'span[role="button"], span, a, div'
                    );
                    for (const x of all) {
                        const t = (x.innerText || '').trim().toLowerCase();
                        if (!t || t.length > 30) continue;
                        for (const w of targets) {
                            if (t === w) {
                                const rect = x.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    try {
                                        x.scrollIntoView({block:'center'});
                                    } catch(e) {}
                                    x.click();
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }
            """)
            return bool(clicked)
        except Exception:
            return False

    # ═══════════════════════════════════════════════
    # harvest
    # ═══════════════════════════════════════════════
    async def _harvest_posts(
        self, posts, source: str,
        following_only: bool = False,
    ) -> int:
        count = 0
        total = len(posts)
        consecutive_non_followers = 0

        log_info(
            f"🔬 [facebook:harvest] بررسی {total} پست "
            f"(following_only={following_only})..."
        )

        for idx, post in enumerate(posts, start=1):
            try:
                log_info(f"   ▶️ [{idx}/{total}]")

                # اسکرول به پست
                try:
                    await post.scroll_into_view_if_needed()
                    await self._human_delay(0.3, 0.8)
                except Exception:
                    pass

                # ─── چک فالوور ───
                if following_only:
                    is_follower = await self._is_follower_post(post)
                    if is_follower is False:
                        consecutive_non_followers += 1
                        log_info(
                            f"       ⏭️ غیرفالوور "
                            f"(#{consecutive_non_followers})"
                        )
                        if consecutive_non_followers >= 10:
                            log_success(
                                f"⏹️ {consecutive_non_followers} "
                                f"غیرفالوور پشت‌سرهم — توقف"
                            )
                            break
                        continue
                    elif is_follower is True:
                        consecutive_non_followers = 0
                        log_info("       ✅ فالوور — ادامه")

                # ─── 🎯 کلیک See more ───
                more_ok = await self._click_see_more(post)
                if more_ok:
                    log_info("       ✅ See more کلیک شد")
                    await asyncio.sleep(1.0)

                # ─── 🎯 کلیک See translation ───
                tr_ok = await self._click_see_translation(post)
                if tr_ok:
                    log_info("       ✅ See translation کلیک شد")
                    await asyncio.sleep(2.5)

                # ─── متن پست ───
                text = await self._extract_post_text(post)
                if not text or len(text) < self._min_text_len:
                    log_info(
                        f"       ⏭️ متن کوتاه | "
                        f"more={more_ok} | tr={tr_ok} | "
                        f"len={len(text) if text else 0}"
                    )
                    continue

                # ─── نویسنده ───
                author = await self._extract_author(post)

                # ─── کلیدواژه ───
                matched = self._kw.match(text)
                if not matched:
                    log_info(
                        f"       ⏭️ بدون کلیدواژه | "
                        f"more={more_ok} | tr={tr_ok} | "
                        f"len={len(text)}"
                    )
                    continue

                log_info(
                    f"       🎯 کلیدواژه: "
                    f"{', '.join(sorted(matched))} | "
                    f"more={more_ok} | tr={tr_ok} | "
                    f"author={author or '?'}"
                )

                # ─── لینک ───
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
                    "keywords": ",".join(sorted(matched)),
                    "reply_to": None,
                })
                count += 1
                log_success(
                    f"       ✅ ذخیره | author={author or '?'} | "
                    f"len={len(text)} | kw={len(matched)}"
                )

                await self._human_delay(0.3, 0.9)

            except Exception as e:
                log_warning(f"       ❌ [{idx}] {type(e).__name__}: {e}")
                continue

        log_info(
            f"🔬 [facebook:harvest] پایان | "
            f"کل={total} | ذخیره={count} | "
            f"skip={total - count}"
        )
        return count

    # ═══════════════════════════════════════════════
    # استخراج متن
    # ═══════════════════════════════════════════════
    async def _extract_post_text(self, post) -> str:
        """استخراج متن پست از DOM واقعی فیسبوک 2026."""

        def _is_ui(t: str) -> bool:
            low = t.strip().lower()
            if not low:
                return True
            if low in _UI_TEXT_MARKERS:
                return True
            if len(low) < 30:
                for w in ("see more", "see translation", "following",
                         "sponsored", "suggested",
                         "مشاهدهٔ", "دیدن ترجمه", "دنبال"):
                    if w in low:
                        return True
            if re.match(r"^[\d\s:؛،.\-·]+$", low):
                return True
            return False

        # ─── ۱) سلکتور دقیق ───
        for sel in POST_TEXT_SELECTORS:
            try:
                els = await post.query_selector_all(sel)
                candidates = []
                for el in els:
                    t = (await el.inner_text() or "").strip()
                    if t and len(t) >= self._min_text_len and not _is_ui(t):
                        candidates.append(t)
                if candidates:
                    candidates.sort(key=len, reverse=True)
                    return candidates[0]
            except Exception:
                continue

        # ─── ۲) fallback: خطوط بلند ───
        try:
            t = (await post.inner_text() or "").strip()
            lines = []
            for ln in t.split("\n"):
                ln = ln.strip()
                if len(ln) >= 30 and not _is_ui(ln):
                    lines.append(ln)
            if lines:
                lines.sort(key=len, reverse=True)
                return lines[0]
        except Exception:
            pass

        return ""

    # ═══════════════════════════════════════════════
    # استخراج نویسنده
    # ═══════════════════════════════════════════════
    async def _extract_author(self, post) -> str:
        # ─── ۱) سلکتورهای خاص ───
        for sel in POST_AUTHOR_SELECTORS:
            try:
                el = await post.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t and 2 < len(t) < 80:
                        return t.split("\n")[0].strip()
            except Exception:
                continue

        # ─── ۲) strong یا h3/h4 ───
        try:
            el = await post.query_selector(
                'strong, h3, h4, [role="heading"]'
            )
            if el:
                t = (await el.inner_text() or "").strip()
                if t and 2 < len(t) < 80:
                    return t.split("\n")[0].strip()
        except Exception:
            pass

        return ""

    # ═══════════════════════════════════════════════
    # استخراج لینک
    # ═══════════════════════════════════════════════
    async def _extract_permalink(self, post) -> str | None:
        for sel in POST_LINK_SELECTORS:
            try:
                a = await post.query_selector(sel)
                if a:
                    href = await a.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            href = "https://www.facebook.com" + href
                        return href.split("?")[0]
            except Exception:
                continue
        return None

    # ═══════════════════════════════════════════════
    # send_message — غیرفعال
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