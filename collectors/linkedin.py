# collectors/linkedin.py
"""
کلکتور لینکدین (LinkedIn) — کاربر عادی با Playwright + رفتار انسانی.

نسخهٔ ۶.۰ (نهایی — فوق دقیق):
  - نشست واقعی مرورگر (login.py → گزینهٔ ۶)
  - فید کلی (/feed/) با Sort روی "Recent"
  - 🎯 فقط پست‌های "Following" ذخیره می‌شن (فالوور واقعی)
  - 🎯 Company postها skip می‌شن (aria-label="View company:")
  - 🎯 پست‌های نامشخص skip می‌شن (محافظه‌کارانه)
  - 🎯 id پایدار از componentkey (نه hash متن)
  - 🎯 کلیک "…more" و "Show translation"
  - 🎯 ضدتکرار سراسری (seen_ids)
  - 🎯 روزی یک بار (once_per_day)
  - 🎯 رفتار انسانی (delay، scroll تدریجی، click با mouse move)
  - 🎯 scroll_into_view ایمن (timeout + try/except)
  - 🎯 لاگ دقیق لحظه‌به‌لحظه
"""
import asyncio
import hashlib
import json
import os
import random
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug, log_error, log_info, log_success, log_warning,
)

# ─── URLها ───
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_SEARCH_URL = (
    "https://www.linkedin.com/search/results/content/"
    "?keywords={q}&origin=GLOBAL_SEARCH_HEADER&sortBy=%22date_posted%22"
)

# ─── مارکرهای لاگین ───
LOGIN_MARKERS = [
    "input#username",
    'input[name="session_key"]',
    'button[data-litms-control-urn="login-submit"]',
]
LOGGEDIN_MARKERS = [
    "#global-nav",
    ".global-nav",
    "div.scaffold-layout",
    "input.search-global-typeahead-input",
    "input.search-global-typeahead__input",
]

# ─── ثابت‌ها ───
_MAX_SEEN = 100000
_MAX_CONSECUTIVE_NON_FOLLOWERS = 50

# ─── مارکرهای UI ───
_UI_MARKERS = (
    "start a post",
    "select feed view",
    "write article",
    "sort by",
    "new posts",
    "show more results",
)

# ─── مارکرهای Company ───
_COMPANY_MARKERS = ("view company:", "view company profile")


class LinkedInCollector(BaseCollector):
    def __init__(
        self,
        email: str = None,
        password: str = None,
        headless: bool = True,
        poll_seconds: int = 600,
        searches_file: str = None,
        max_results: int = 15,
        search_delay: float = 6.0,
        scrolls: int = 3,
        monitor_feed: bool = True,
        max_feed_posts: int = 100,
        add_query_to_keywords: bool = True,
        feed_url: str = None,
        following_only: bool = True,
        stop_on_non_follower: bool = True,
        once_per_day: bool = True,
        search_enabled: bool = False,
        force_run: bool = False,
        **kwargs,
    ):
        super().__init__()
        self.email = email
        self.password = password
        self.headless = headless
        self._poll_seconds = int(poll_seconds)
        self._max_results = int(max_results)
        self._search_delay = float(search_delay)
        self._scrolls = int(scrolls)
        self._monitor_feed = bool(monitor_feed)
        self._max_feed_posts = int(max_feed_posts)
        self._add_query_to_keywords = bool(add_query_to_keywords)

        self._feed_url = feed_url or LINKEDIN_FEED_URL
        self._following_only = bool(following_only)
        self._stop_on_non_follower = bool(stop_on_non_follower)
        self._once_per_day = bool(once_per_day)
        self._search_enabled = bool(search_enabled)
        self._force_run = bool(force_run)

        self._pw = None
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids = set()
        self._kw = get_keyword_finder()
        self._last_scrape_stats: dict[str, int] = {}

        if not hasattr(LinkedInCollector, "_connect_lock"):
            LinkedInCollector._connect_lock = asyncio.Lock()

        from config import BASE_DIR, STORAGE_DIR

        self._searches_file = searches_file or os.path.join(
            BASE_DIR, "data", "sources", "linkedin_searches.txt"
        )
        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "linkedin_v2"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(
            STORAGE_DIR, "media", "linkedin"
        )
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "linkedin_seen.json"
        )
        os.makedirs(
            os.path.dirname(self._seen_path), exist_ok=True
        )
        self._state_path = os.path.join(
            STORAGE_DIR, "sessions", "linkedin_state.json"
        )
        self._load_seen()

        self._last_run_path = os.path.join(
            STORAGE_DIR, "offset", "linkedin_last_run.json"
        )
        os.makedirs(
            os.path.dirname(self._last_run_path), exist_ok=True
        )
        self._last_run_date = ""
        self._load_last_run()

    # ═══════════════════════════════════════════════════
    # خواص
    # ═══════════════════════════════════════════════════
    @property
    def platform_name(self) -> str:
        return "linkedin"

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    # ═══════════════════════════════════════════════════
    # رفتار انسانی
    # ═══════════════════════════════════════════════════
    async def _human_delay(
        self, min_s: float = 0.5, max_s: float = 2.5,
    ):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_scroll(self, amount: int = 800):
        sign = 1 if amount > 0 else -1
        remaining = abs(amount)
        while remaining > 0:
            step = random.randint(80, 250)
            step = min(step, remaining)
            await self._page.mouse.wheel(0, sign * step)
            remaining -= step
            await self._human_delay(0.15, 0.45)

    async def _human_click(self, element) -> bool:
        try:
            box = await element.bounding_box()
            if not box:
                await element.click()
                return True
            x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
            y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
            steps = random.randint(8, 20)
            await self._page.mouse.move(x, y, steps=steps)
            await self._human_delay(0.1, 0.3)
            await self._page.mouse.click(x, y)
            return True
        except Exception:
            try:
                await element.click()
                return True
            except Exception:
                return False

    # ═══════════════════════════════════════════════════
    # ضدتکراری
    # ═══════════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} پست دیده‌شده")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # last_run
    # ═══════════════════════════════════════════════════
    def _load_last_run(self):
        self._last_run_date = ""
        try:
            if os.path.exists(self._last_run_path):
                with open(self._last_run_path, encoding="utf-8") as f:
                    d = json.load(f)
                self._last_run_date = d.get("last_date", "") or ""
        except Exception:
            self._last_run_date = ""

    def _save_last_run(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            with open(self._last_run_path, "w", encoding="utf-8") as f:
                json.dump({"last_date": today}, f, ensure_ascii=False, indent=2)
            self._last_run_date = today
        except Exception as e:
            log_warning(f"ذخیرهٔ last_run ناموفق: {e}")

    def _already_ran_today(self) -> bool:
        return self._last_run_date == datetime.now().strftime("%Y-%m-%d")

    def _load_searches(self) -> list[str]:
        out = []
        if not os.path.exists(self._searches_file):
            return out
        with open(self._searches_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
        return out

    # ═══════════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [linkedin:connect] از قبل متصل — رد")
            return True

        async with LinkedInCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning("⚠️ [linkedin:connect] context قدیمی باز — بستن")
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            try:
                try:
                    from playwright.async_api import async_playwright
                except ImportError:
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
                await self._page.goto(
                    self._feed_url,
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
                await asyncio.sleep(5)

                if await self._wait_login():
                    await self._export_state()
                    return True

                if await self._restore_state():
                    await self._page.goto(
                        self._feed_url, wait_until="domcontentloaded"
                    )
                    if await self._wait_login(rounds=6):
                        await self._export_state()
                        log_success("✅ نشست از کوکی بازیابی شد")
                        return True

                if self.email and self.password:
                    log_info("🔄 ورود خودکار...")
                    if await self._auto_login():
                        await self._export_state()
                        return True

                log_error("نشست لینکدین نیست — `python login.py` (گزینهٔ ۶)")
                await self.disconnect()
                return False
            except Exception as e:
                log_error(f"خطا در اتصال: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _auto_login(self) -> bool:
        try:
            await self._page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(2)
            if not await self._page.query_selector("input#username"):
                log_warning("صفحهٔ ورود پیدا نشد")
                return False
            await self._page.fill("input#username", self.email)
            await self._page.fill("input#password", self.password)
            await asyncio.sleep(0.5)
            try:
                await self._page.click('button[type="submit"]')
            except Exception:
                pass
            for _ in range(20):
                await asyncio.sleep(2)
                if await self._is_logged_in():
                    self._connected = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success("✅ ورود خودکار موفق")
                    return True
                if "/checkpoint" in (self._page.url or ""):
                    log_warning("⚠️ checkpoint")
                    return False
            return False
        except Exception as e:
            log_warning(f"ورود خودکار ناموفق: {e}")
            return False

    async def _wait_login(self, rounds: int = 15) -> bool:
        for _ in range(rounds):
            await asyncio.sleep(2)
            if await self._is_logged_in():
                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                log_success("✅ اتصال لینکدین برقرار شد")
                return True
        return False

    async def _export_state(self):
        try:
            await self._context.storage_state(path=self._state_path)
        except Exception as e:
            log_warning(f"ذخیرهٔ نشست ناموفق: {e}")

    async def _restore_state(self) -> bool:
        try:
            if not os.path.exists(self._state_path):
                return False
            with open(self._state_path, encoding="utf-8") as f:
                state = json.load(f)
            cookies = state.get("cookies") or []
            if cookies:
                await self._context.add_cookies(cookies)
                return True
        except Exception as e:
            log_warning(f"بازیابی نشست ناموفق: {e}")
        return False

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH
        base = dict(
            user_data_dir=self._user_data_dir,
            headless=use_headless,
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--no-first-run",
            ],
        )
        if CHROME_PATH:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(
                    executable_path=CHROME_PATH, **base
                )
                log_info("مرورگر با CHROME_PATH")
                return ctx
            except Exception as e:
                log_warning(f"CHROME_PATH ناموفق: {e}")
        last_err = None
        for channel in ("chrome", None):
            try:
                kwargs = dict(base)
                if channel:
                    kwargs["channel"] = channel
                ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
                log_info(f"مرورگر با {channel or 'chromium'}")
                return ctx
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"هیچ مرورگری باز نشد: {last_err}")

    async def _is_logged_in(self) -> bool:
        try:
            url = (self._page.url or "").lower()
            if "/login" in url or "/authwall" in url:
                return False
            for sel in LOGGEDIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue
            for sel in LOGIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return False
                except Exception:
                    continue
            return "linkedin.com" in url
        except Exception:
            return False

    async def _check_session_alive(self) -> bool:
        try:
            url = (self._page.url or "").lower()
            if any(p in url for p in ("/login", "/authwall", "/checkpoint")):
                self._connected = False
                self._set_status(ConnectionStatus.ERROR)
                log_warning("⚠️ نشست منقضی — `python login.py` (گزینهٔ ۶)")
                return False
        except Exception:
            pass
        return True

    # ═══════════════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════════════
    async def fetch_messages(self, since: datetime = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        if self._once_per_day and not self._force_run and self._already_ran_today():
            log_info("⏭️ [linkedin] امروز قبلاً چک شده — رد تا فردا")
            return []

        self._last_scrape_stats = {
            "total_posts": 0,
            "stored": 0,
            "skipped_follower": 0,
            "skipped_non_follower": 0,
            "skipped_company": 0,
            "skipped_unknown": 0,
            "skipped_ui": 0,
            "skipped_short": 0,
            "skipped_no_kw": 0,
            "skipped_seen": 0,
            "errors": 0,
        }

        if self._monitor_feed:
            try:
                await self._scrape_feed()
            except Exception as e:
                log_warning(f"خطا در فید: {e}")

        if self._search_enabled:
            searches = self._load_searches()
            for q in searches:
                if not self._connected:
                    break
                try:
                    await self._scrape_search(q)
                    await asyncio.sleep(
                        self._search_delay
                        + random.uniform(0, self._search_delay)
                    )
                except Exception as e:
                    log_warning(f"خطا در «{q}»: {e}")
                    continue

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        await self._export_state()

        if self._once_per_day and not self._force_run:
            self._save_last_run()

        s = self._last_scrape_stats
        log_info(
            f"📊 [linkedin:stats] total={s['total_posts']} | "
            f"stored={s['stored']} | "
            f"follower={s['skipped_follower']} | "
            f"non-follower={s['skipped_non_follower']} | "
            f"company={s['skipped_company']} | "
            f"unknown={s['skipped_unknown']} | "
            f"ui={s['skipped_ui']} | "
            f"short={s['skipped_short']} | "
            f"no-kw={s['skipped_no_kw']} | "
            f"seen={s['skipped_seen']} | "
            f"errors={s['errors']}"
        )

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پست از لینکدین")
        return new_msgs

    # ═══════════════════════════════════════════════════
    # scrape فید
    # ═══════════════════════════════════════════════════
    async def _scrape_feed(self):
        page = self._page

        log_info(f"🏠 LinkedIn: رفتن به feed | url={self._feed_url}")
        try:
            await page.goto(
                self._feed_url,
                wait_until="domcontentloaded",
                timeout=90000,
            )
        except Exception as e:
            log_warning(f"خطا در باز کردن feed: {e}")
            return

        await self._human_delay(5, 8)

        if not await self._check_session_alive():
            return

        log_info("🔍 پیدا کردن دکمهٔ 'Recent'...")
        clicked = await self._click_sort_recent()

        if clicked:
            log_success("✅ Sort → Recent")
            await self._human_delay(5, 8)
            try:
                await self._page.wait_for_selector(
                    "div[role='listitem']", timeout=20000
                )
                log_info("   ✅ پست‌ها لود شدند")
            except Exception:
                log_warning("   ⚠️ پست‌ها لود نشدند")
        else:
            log_warning("⚠️ دکمهٔ Recent پیدا نشد — ادامه بدون sort")

        log_info("🖱️ scroll آهسته...")
        total = self._scrolls + 3
        for i in range(total):
            try:
                await page.mouse.move(
                    random.randint(400, 900),
                    random.randint(300, 700),
                    steps=random.randint(5, 15),
                )
            except Exception:
                pass
            await self._human_scroll(random.randint(800, 1400))
            await self._human_delay(2, 4)
            if (i + 1) % 2 == 0:
                log_info(f"   ↕️ {i+1}/{total}")

        await self._human_delay(2, 4)

        posts = await self._find_posts()
        if not posts:
            log_warning("⚠️ پستی پیدا نشد")
            return

        log_info(f"📄 {len(posts)} پست پیدا شد")
        if self._max_feed_posts <= 0:
            selected = posts
            log_info(f"   🔬 حالت بدون محدودیت — همه {len(posts)} پست")
        else:
            selected = posts[: self._max_feed_posts]
            log_info(f"   🔬 حالت محدود — {len(selected)} پست از {len(posts)}")

        count = await self._harvest_posts(selected, source="فید", human=True)
        if count:
            log_success(f"🏠 فید: {count} پست جدید")

    # ═══════════════════════════════════════════════════
    # scrape search
    # ═══════════════════════════════════════════════════
    async def _scrape_search(self, query: str):
        page = self._page
        url = LINKEDIN_SEARCH_URL.format(q=quote(query))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            log_warning(f"خطا در search «{query}»: {e}")
            return
        await asyncio.sleep(8)
        if not await self._check_session_alive():
            return

        for _ in range(self._scrolls):
            await self._human_scroll(random.randint(900, 1400))
            await self._human_delay(2, 4)

        await asyncio.sleep(3)

        posts = await self._find_posts()
        if not posts:
            log_info(f"ℹ️ نتیجه‌ای برای «{query}» نبود")
            return
        count = await self._harvest_posts(
            posts[: self._max_results],
            source=query,
            query=query,
            human=True,
        )
        if count:
            log_success(f"🔎 «{query}»: {count} پست")

    # ═══════════════════════════════════════════════════
    # Sort → Recent
    # ═══════════════════════════════════════════════════
    async def _click_sort_recent(self) -> bool:
        await asyncio.sleep(2)

        try:
            current = await self._page.evaluate("""
                () => {
                    const all = document.querySelectorAll('p, span, strong');
                    for (const el of all) {
                        const t = (el.innerText || '').trim();
                        if (t.startsWith('Sort by:')) return t;
                    }
                    return '';
                }
            """)
            if current:
                log_info(f"   current sort: {current}")
                if "recent" in current.lower():
                    log_success(f"✅ از قبل Recent | {current}")
                    return True
        except Exception as e:
            log_warning(f"   chk: {e}")

        log_info("   🔍 پیدا کردن دکمهٔ Sort...")
        try:
            sort_loc = self._page.locator("p:has-text('Sort by:')").first
            cnt = await sort_loc.count()
            if cnt == 0:
                log_warning("   ⚠️ دکمهٔ Sort پیدا نشد")
                return False
        except Exception as e:
            log_warning(f"   ⚠️ {e}")
            return False

        log_info("   🖱️ کلیک روی Sort...")
        try:
            await sort_loc.scroll_into_view_if_needed(timeout=2000)
            await asyncio.sleep(0.3)
            await sort_loc.click(timeout=5000)
            await asyncio.sleep(1.5)
            log_success("   ✅ کلیک شد")
        except Exception as e:
            log_warning(f"   ⚠️ کلیک نشد: {e}")
            return False

        log_info("   🔽 چک کردن dropdown...")
        dropdown_open = False
        for _ in range(10):
            await asyncio.sleep(0.3)
            try:
                n = await self._page.evaluate("""
                    () => {
                        const items = document.querySelectorAll(
                            'div[role="menuitem"]'
                        );
                        for (const el of items) {
                            const t = (el.innerText || '').trim();
                            if (t === 'Recent' || t === 'Top') return 1;
                        }
                        return 0;
                    }
                """)
                if n:
                    dropdown_open = True
                    log_success("   ✅ dropdown باز شد")
                    break
            except Exception:
                continue

        if not dropdown_open:
            log_warning("   ⚠️ dropdown باز نشد")
            return False

        log_info("   🎯 کلیک روی 'Recent'...")
        await asyncio.sleep(0.5)
        try:
            recent_loc = self._page.locator(
                "div[role='menuitem']:has-text('Recent')"
            ).first
            cnt = await recent_loc.count()
            if cnt > 0:
                await recent_loc.click(timeout=5000)
                log_success("✅ Sort → Recent")
                await asyncio.sleep(2)
                try:
                    await self._page.wait_for_selector(
                        "div[role='listitem']", timeout=15000
                    )
                    log_info("   ✅ پست‌ها لود شدند")
                except Exception:
                    log_warning("   ⚠️ پست‌ها لود نشدند")
                return True
            else:
                log_warning("   ⚠️ Recent پیدا نشد")
        except Exception as e:
            log_warning(f"   ⚠️ کلیک Recent: {e}")

        return False

    # ═══════════════════════════════════════════════════
    # پیدا کردن پست‌ها
    # ═══════════════════════════════════════════════════
    async def _find_posts(self):
        for attempt in range(10):
            for sel in ("div[role='listitem']", "main ul li"):
                try:
                    posts = await self._page.query_selector_all(sel)
                    if posts:
                        log_info(f"لینکدین: {len(posts)} پست با `{sel}`")
                        return posts
                except Exception:
                    continue
            await asyncio.sleep(1)
        return []

    # ═══════════════════════════════════════════════════
    # چک فالوور (دقیق — نهایی)
    # ═══════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════
    # چک فالوور (دقیق — v2، همخوان با DOM واقعی لینکدین 2026)
    # ═══════════════════════════════════════════════════
    async def _is_follower_post(self, post) -> bool | None:
        """
        True  = فالوور (در header «• Following» دارد)
        False = غیرفالوور (دکمهٔ «Follow <Name>» دارد)
        None  = Company یا نامشخص (skip محافظه‌کارانه)

        ترتیب تشخیص (بر اساس DOM واقعی لینکدین 2026):
          ۱. aria-label شروع‌شده با "Follow " (نه "Following") → False
          ۲. header حاوی "• following" → True
          ۳. aria-label حاوی "view company:" → None (Company)
          ۴. دکمهٔ "Open control menu for post by X" بدون Follow/Following → None (Company)
          ۵. هیچ‌کدام → None (نامشخص)
        """
        try:
            # ─── استخراج aria-labelها ───
            btns = await post.query_selector_all("button[aria-label]")
            labels = []
            for b in btns:
                label = await b.get_attribute("aria-label") or ""
                labels.append(label)

            log_debug(
                f"       🔍 [follower] buttons ({len(labels)}): "
                f"{labels[:5]}"
            )

            # ─── ۱) دکمهٔ Follow (غیرفالوور) ───
            for label in labels:
                lower = label.lower().strip()
                # "follow x" ولی نه "following x" و نه "follow-up"
                if lower.startswith("follow ") and not lower.startswith("following "):
                    log_debug(
                        f"       🔍 [follower] → False "
                        f"(aria-label: {label[:50]})"
                    )
                    return False

            # ─── ۲) «• Following» در header (فالوور) ───
            header = await post.evaluate(
                "(el) => (el.innerText || '').slice(0, 400)"
            )
            if "• following" in (header or "").lower():
                log_debug(
                    f"       🔍 [follower] → True "
                    f"(header: '• Following')"
                )
                return True

            # ─── ۳) View company: (Company) ───
            for label in labels:
                if "view company:" in label.lower():
                    log_debug(
                        f"       🔍 [follower] → None "
                        f"(aria-label: {label[:50]})"
                    )
                    return None

            # ─── ۴) کنترل منوی پست بدون Follow/Following → Company ───
            has_post_menu = any(
                l.lower().startswith("open control menu for post by ")
                for l in labels
            )
            has_follow_signal = any(
                l.lower().startswith("follow ") or l.lower() == "following"
                for l in labels
            )
            if has_post_menu and not has_follow_signal:
                # نام شرکت را برای لاگ استخراج کن
                cname = ""
                for l in labels:
                    if l.lower().startswith("open control menu for post by "):
                        cname = l[len("Open control menu for post by "):]
                        break
                log_debug(
                    f"       🏢 [follower] → Company "
                    f"(no follow btn: {cname[:40]})"
                )
                return None

            # ─── ۵) نامشخص ───
            log_debug(
                f"       🔍 [follower] → None "
                f"(هیچ نشانه‌ای پیدا نشد)"
            )
            return None

        except Exception as e:
            log_debug(
                f"       🔍 [follower] → None "
                f"(خطا: {type(e).__name__}: {e})"
            )
            return None
            
            
    # ═══════════════════════════════════════════════════
    # id پایدار از componentkey
    # ═══════════════════════════════════════════════════
    async def _get_post_id(self, post) -> str:
        """id پایدار از componentkey یا fallback."""
        try:
            # ۱) componentkey
            ck = await post.get_attribute("componentkey") or ""
            if ck:
                if "FeedType" in ck:
                    ck = ck.split("FeedType")[0]
                if ck.startswith("update-card-focus"):
                    ck = ck[len("update-card-focus"):]
                if ck:
                    return hashlib.sha256(
                        f"linkedin:{ck}".encode()
                    ).hexdigest()[:32]

            # ۲) data-urn / data-id
            for attr in ("data-urn", "data-id"):
                v = await post.get_attribute(attr)
                if v:
                    return hashlib.sha256(
                        f"linkedin:{v}".encode()
                    ).hexdigest()[:32]

            # ۳) fallback: hash متن
            text = await post.inner_text() or ""
            if text:
                return hashlib.sha256(
                    f"linkedin:{text[:1000]}".encode()
                ).hexdigest()[:32]
            return ""
        except Exception:
            return ""

    # ═══════════════════════════════════════════════════
    # harvest
    # ═══════════════════════════════════════════════════
    async def _harvest_posts(
        self,
        posts,
        source: str,
        query: str = None,
        human: bool = False,
    ) -> int:
        count = 0
        consecutive_non_followers = 0
        total_posts = len(posts)
        self._last_scrape_stats["total_posts"] += total_posts

        log_info(f"🔬 [linkedin:harvest] شروع پردازش {total_posts} پست...")

        for idx, post in enumerate(posts, start=1):
            try:
                log_info(f"   ▶️ [{idx}/{total_posts}] بررسی پست...")

                # ═══════════════════════════════════════
                # ۰) scroll ایمن
                # ═══════════════════════════════════════
                if human:
                    try:
                        await post.scroll_into_view_if_needed(timeout=3000)
                        await self._human_delay(0.3, 0.8)
                    except Exception as e:
                        log_debug(f"   scroll skip: {type(e).__name__}")
                        pass

                # ═══════════════════════════════════════
                # ۱) کلیک "…more" ← قبل از چک فالوور
                # ═══════════════════════════════════════
                more_clicked = False
                try:
                    more_clicked = await post.evaluate("""
                    (el) => {
                        const all = el.querySelectorAll(
                            'button, span, a, [role="button"]'
                        );
                        for (const x of all) {
                            const t = (x.innerText || '').trim();
                            const lower = t.toLowerCase();
                            if (
                                lower === '…more' ||
                                lower === '… more' ||
                                lower === '...more' ||
                                lower === '... more' ||
                                lower === 'see more' ||
                                lower === 'show more'
                            ) {
                                const rect = x.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    x.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    }
                    """)
                    if more_clicked:
                        await asyncio.sleep(0.8)
                except Exception:
                    pass

                # ═══════════════════════════════════════
                # ۲) کلیک "Show translation" ← قبل از چک فالوور
                # ═══════════════════════════════════════
                tr_clicked = False
                try:
                    tr_clicked = await post.evaluate("""
                    (el) => {
                        const all = el.querySelectorAll(
                            'button, a, span, [role="button"]'
                        );
                        for (const x of all) {
                            const t = (x.innerText || '').trim().toLowerCase();
                            if (
                                t === 'show translation' ||
                                t === 'see translation' ||
                                t === 'show original' ||
                                t.includes('show translation') ||
                                t.includes('see translation')
                            ) {
                                const rect = x.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    x.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    }
                    """)
                    if tr_clicked:
                        await asyncio.sleep(2.0)
                except Exception:
                    pass

                log_info(
                    f"       {'✅ more' if more_clicked else '⏭️ no-more'} | "
                    f"{'✅ ترجمه' if tr_clicked else '⏭️ no-tr'}"
                )

                # ═══════════════════════════════════════
                # ۳) متن نهایی ← حالا کامل شده
                # ═══════════════════════════════════════
                text = (await post.inner_text() or "").strip()
                if not text or len(text) < 20:
                    self._last_scrape_stats["skipped_short"] += 1
                    log_info("       ⏭️ متن خیلی کوتاه — رد")
                    continue

                text_lower = text.lower()
                if any(m in text_lower for m in _UI_MARKERS):
                    self._last_scrape_stats["skipped_ui"] += 1
                    log_info("       ⏭️ UI element — رد")
                    continue

                log_info(f"       📝 text: {text[:80]}...")
                log_info(f"       len={len(text)}")

                # ═══════════════════════════════════════
                # ۴) چک فالوور ← با متن کامل
                # ═══════════════════════════════════════
                if self._following_only and source == "فید":
                    is_follower = await self._is_follower_post(post)

                    if is_follower is False:
                        consecutive_non_followers += 1
                        self._last_scrape_stats["skipped_non_follower"] += 1
                        log_info(
                            f"       ⏭️ غیرفالوور "
                            f"(#{consecutive_non_followers})"
                        )
                        if (
                            self._stop_on_non_follower
                            and consecutive_non_followers
                            >= _MAX_CONSECUTIVE_NON_FOLLOWERS
                        ):
                            log_success(
                                f"⏹️ [linkedin] {consecutive_non_followers} "
                                f"غیرفالوور پشت‌سرهم — توقف"
                            )
                            break
                        continue

                    elif is_follower is True:
                        consecutive_non_followers = 0
                        self._last_scrape_stats["skipped_follower"] += 1
                        log_info("       ✅ فالوور — ادامه")

                    else:
                        # None: یا Company یا نامشخص
                        # از نام پست در header تشخیص می‌دهیم
                        header = await post.evaluate(
                            "(el) => (el.innerText || '').slice(0, 200)"
                        )
                        # اگر «Feed post\n\n<Name>\n\n» و Name شبیه شرکت بود
                        # ساده: چک کنیم آیا در aria-label ها
                        # «Open control menu for post by X» هست بدون Follow
                        btns = await post.query_selector_all(
                            "button[aria-label]"
                        )
                        has_menu = False
                        for b in btns:
                            lbl = await b.get_attribute("aria-label") or ""
                            if lbl.lower().startswith(
                                "open control menu for post by "
                            ):
                                has_menu = True
                                break
                        if has_menu:
                            self._last_scrape_stats["skipped_company"] += 1
                            log_info("       🏢 Company — skip")
                        else:
                            self._last_scrape_stats["skipped_unknown"] += 1
                            log_info("       ❓ نامشخص — skip")
                        continue

                        
                # ═══════════════════════════════════════
                # ۵) کلیدواژه ← با متن کامل
                # ═══════════════════════════════════════
                matched = self._match_post(text, query)
                if not matched:
                    self._last_scrape_stats["skipped_no_kw"] += 1
                    log_info("       ⏭️ بدون کلیدواژه")
                    continue

                log_info(f"       🎯 کلیدواژه: {', '.join(sorted(matched))}")

                # ═══════════════════════════════════════
                # ۶) ضدتکرار
                # ═══════════════════════════════════════
                mid = await self._get_post_id(post)
                if not mid:
                    log_info("       ⏭️ id نامشخص — رد")
                    continue
                if mid in self._seen_ids:
                    self._last_scrape_stats["skipped_seen"] += 1
                    log_info(f"       ⏭️ تکراری (seen) | {mid[:20]}")
                    continue
                self._seen_ids.add(mid)

                # ═══════════════════════════════════════
                # ۷) author + permalink
                # ═══════════════════════════════════════
                author = ""
                try:
                    a = await post.query_selector("a[href*='/in/']")
                    if a:
                        author = (await a.inner_text() or "").strip()[:100]
                except Exception:
                    pass

                permalink = await self._extract_link(post)

                # ═══════════════════════════════════════
                # ۸) media
                # ═══════════════════════════════════════
                media_type, local_path = "text", None
                try:
                    from core.adapters.web_media import download_dom_image
                    mt, lp = await download_dom_image(
                        self._page, post, self._media_dir, "linkedin"
                    )
                    if mt:
                        media_type, local_path = mt, lp
                except Exception:
                    pass

                # ═══════════════════════════════════════
                # ۹) ذخیره
                # ═══════════════════════════════════════
                self._messages_queue.append({
                    "id": str(mid),
                    "platform": "linkedin",
                    "text": text[:5000],
                    "chat_id": source,
                    "chat_name": f"LinkedIn: {source}",
                    "chat_type": "linkedin",
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
                self._last_scrape_stats["stored"] += 1
                log_success(
                    f"       ✅ ذخیره شد | author={author or '?'} | "
                    f"kw={','.join(sorted(matched))}"
                )

                if human:
                    await self._human_delay(0.3, 1.0)

            except Exception as e:
                self._last_scrape_stats["errors"] += 1
                log_warning(f"       ❌ خطا در پست [{idx}]: {e}")
                continue

        log_info(
            f"🔬 [linkedin:harvest] پایان | "
            f"کل={total_posts} | ذخیره={count} | "
            f"skip={total_posts - count}"
        )
        return count
        
        
    # ═══════════════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════════════
    def _match_post(self, text: str, query: str) -> set:
        matched = set(self._kw.match(text))
        if (
            self._add_query_to_keywords
            and query
            and query.lower() in (text or "").lower()
        ):
            matched.add(query)
        return matched

    @staticmethod
    async def _extract_link(post) -> str | None:
        try:
            for sel in (
                'a[href*="/feed/update/"]',
                'a[href*="/posts/"]',
                'a[href*="activity"]',
            ):
                a = await post.query_selector(sel)
                if a:
                    href = await a.get_attribute("href")
                    if href:
                        return href.split("?")[0]
            a = await post.query_selector("a[href*='/in/']")
            if a:
                href = await a.get_attribute("href")
                if href:
                    return href.split("?")[0]
        except Exception:
            pass
        return None

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "linkedin_dom_dump.html"
        )
        html = await self._page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log_info(f"DOM در {path}")
        return path

    async def disconnect(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از لینکدین")

    async def check_health(self) -> bool:
        return self._connected