# collectors/linkedin.py
"""
ع©ظ„ع©طھظˆط± ظ„غŒظ†ع©ط¯غŒظ† (LinkedIn) â€” ع©ط§ط±ط¨ط± ط¹ط§ط¯غŒ ط¨ط§ Playwright + ط±ظپطھط§ط± ط§ظ†ط³ط§ظ†غŒ.

ظ†ط³ط®ظ‡ظ” غ¶.غ° (ظ†ظ‡ط§غŒغŒ â€” ظپظˆظ‚ ط¯ظ‚غŒظ‚):
  - ظ†ط´ط³طھ ظˆط§ظ‚ط¹غŒ ظ…ط±ظˆط±ع¯ط± (login.py â†’ ع¯ط²غŒظ†ظ‡ظ” غ¶)
  - ظپغŒط¯ ع©ظ„غŒ (/feed/) ط¨ط§ Sort ط±ظˆغŒ "Recent"
  - ًںژ¯ ظپظ‚ط· ظ¾ط³طھâ€Œظ‡ط§غŒ "Following" ط°ط®غŒط±ظ‡ ظ…غŒâ€Œط´ظ† (ظپط§ظ„ظˆظˆط± ظˆط§ظ‚ط¹غŒ)
  - ًںژ¯ Company postظ‡ط§ skip ظ…غŒâ€Œط´ظ† (aria-label="View company:")
  - ًںژ¯ ظ¾ط³طھâ€Œظ‡ط§غŒ ظ†ط§ظ…ط´ط®طµ skip ظ…غŒâ€Œط´ظ† (ظ…ط­ط§ظپط¸ظ‡â€Œع©ط§ط±ط§ظ†ظ‡)
  - ًںژ¯ id ظ¾ط§غŒط¯ط§ط± ط§ط² componentkey (ظ†ظ‡ hash ظ…طھظ†)
  - ًںژ¯ ع©ظ„غŒع© "â€¦more" ظˆ "Show translation"
  - ًںژ¯ ط¶ط¯طھع©ط±ط§ط± ط³ط±ط§ط³ط±غŒ (seen_ids)
  - ًںژ¯ ط±ظˆط²غŒ غŒع© ط¨ط§ط± (once_per_day)
  - ًںژ¯ ط±ظپطھط§ط± ط§ظ†ط³ط§ظ†غŒ (delayطŒ scroll طھط¯ط±غŒط¬غŒطŒ click ط¨ط§ mouse move)
  - ًںژ¯ scroll_into_view ط§غŒظ…ظ† (timeout + try/except)
  - ًںژ¯ ظ„ط§ع¯ ط¯ظ‚غŒظ‚ ظ„ط­ط¸ظ‡â€Œط¨ظ‡â€Œظ„ط­ط¸ظ‡
"""
import asyncio
import contextlib
import hashlib
import json
import os
import random
from datetime import datetime
from typing import Any
from urllib.parse import quote

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug,
    log_error,
    log_info,
    log_success,
    log_warning,
)

# â”€â”€â”€ URLظ‡ط§ â”€â”€â”€
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_SEARCH_URL = (
    "https://www.linkedin.com/search/results/content/"
    "?keywords={q}&origin=GLOBAL_SEARCH_HEADER&sortBy=%22date_posted%22"
)

# â”€â”€â”€ ظ…ط§ط±ع©ط±ظ‡ط§غŒ ظ„ط§ع¯غŒظ† â”€â”€â”€
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

# â”€â”€â”€ ط«ط§ط¨طھâ€Œظ‡ط§ â”€â”€â”€
_MAX_SEEN = 100000
_MAX_CONSECUTIVE_NON_FOLLOWERS = 50

# â”€â”€â”€ ظ…ط§ط±ع©ط±ظ‡ط§غŒ UI â”€â”€â”€
_UI_MARKERS = (
    "start a post",
    "select feed view",
    "write article",
    "sort by",
    "new posts",
    "show more results",
)

# â”€â”€â”€ ظ…ط§ط±ع©ط±ظ‡ط§غŒ Company â”€â”€â”€
_COMPANY_MARKERS = ("view company:", "view company profile")


class LinkedInCollector(BaseCollector):
    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        headless: bool = True,
        poll_seconds: int = 600,
        searches_file: str | None = None,
        max_results: int = 15,
        search_delay: float = 6.0,
        scrolls: int = 3,
        monitor_feed: bool = True,
        max_feed_posts: int = 100,
        add_query_to_keywords: bool = True,
        feed_url: str | None = None,
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

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ط®ظˆط§طµ
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ط±ظپطھط§ط± ط§ظ†ط³ط§ظ†غŒ
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ط¶ط¯طھع©ط±ط§ط±غŒ
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"â™»ï¸ڈ {len(self._seen_ids)} ظ¾ط³طھ ط¯غŒط¯ظ‡â€Œط´ط¯ظ‡")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ط°ط®غŒط±ظ‡ظ” ط¶ط¯طھع©ط±ط§ط±غŒ ظ†ط§ظ…ظˆظپظ‚: {e}")

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # last_run
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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
            today = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
            with open(self._last_run_path, "w", encoding="utf-8") as f:
                json.dump({"last_date": today}, f,
                          ensure_ascii=False, indent=2)
            self._last_run_date = today
        except Exception as e:
            log_warning(f"ط°ط®غŒط±ظ‡ظ” last_run ظ†ط§ظ…ظˆظپظ‚: {e}")

    def _already_ran_today(self) -> bool:
        return self._last_run_date == datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005

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

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ط§طھطµط§ظ„
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._context:
            log_info("â„¹ï¸ڈ [linkedin:connect] ط§ط² ظ‚ط¨ظ„ ظ…طھطµظ„ â€” ط±ط¯")
            return True

        async with LinkedInCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "âڑ ï¸ڈ [linkedin:connect] context ظ‚ط¯غŒظ…غŒ ط¨ط§ط² â€” ط¨ط³طھظ†")
                with contextlib.suppress(Exception):
                    await self._context.close()
                self._context = None
                self._page = None

            try:
                try:
                    from playwright.async_api import async_playwright
                except ImportError:
                    log_error("playwright ظ†طµط¨ ظ†غŒط³طھ")
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
                        log_success(
                            "âœ… ظ†ط´ط³طھ ط§ط² ع©ظˆع©غŒ ط¨ط§ط²غŒط§ط¨غŒ ط´ط¯")
                        return True

                if self.email and self.password:
                    log_info("ًں”„ ظˆط±ظˆط¯ ط®ظˆط¯ع©ط§ط±...")
                    if await self._auto_login():
                        await self._export_state()
                        return True

                log_error(
                    "ظ†ط´ط³طھ ظ„غŒظ†ع©ط¯غŒظ† ظ†غŒط³طھ â€” `python login.py` (ع¯ط²غŒظ†ظ‡ظ” غ¶)")
                await self.disconnect()
                return False
            except Exception as e:
                log_error(f"ط®ط·ط§ ط¯ط± ط§طھطµط§ظ„: {e}")
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
                log_warning("طµظپط­ظ‡ظ” ظˆط±ظˆط¯ ظ¾غŒط¯ط§ ظ†ط´ط¯")
                return False
            await self._page.fill("input#username", self.email)
            await self._page.fill("input#password", self.password)
            await asyncio.sleep(0.5)
            with contextlib.suppress(Exception):
                await self._page.click('button[type="submit"]')
            for _ in range(20):
                await asyncio.sleep(2)
                if await self._is_logged_in():
                    self._connected = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success("âœ… ظˆط±ظˆط¯ ط®ظˆط¯ع©ط§ط± ظ…ظˆظپظ‚")
                    return True
                if "/checkpoint" in (self._page.url or ""):
                    log_warning("âڑ ï¸ڈ checkpoint")
                    return False
            return False
        except Exception as e:
            log_warning(f"ظˆط±ظˆط¯ ط®ظˆط¯ع©ط§ط± ظ†ط§ظ…ظˆظپظ‚: {e}")
            return False

    async def _wait_login(self, rounds: int = 15) -> bool:
        for _ in range(rounds):
            await asyncio.sleep(2)
            if await self._is_logged_in():
                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                log_success("âœ… ط§طھطµط§ظ„ ظ„غŒظ†ع©ط¯غŒظ† ط¨ط±ظ‚ط±ط§ط± ط´ط¯")
                return True
        return False

    async def _export_state(self):
        try:
            await self._context.storage_state(path=self._state_path)
        except Exception as e:
            log_warning(f"ط°ط®غŒط±ظ‡ظ” ظ†ط´ط³طھ ظ†ط§ظ…ظˆظپظ‚: {e}")

    async def _restore_state(self) -> bool:
        try:
            if not os.path.exists(self._state_path):
                return False

            def _load():
                with open(self._state_path, encoding="utf-8") as f:
                    return json.load(f)
            state = await asyncio.to_thread(_load)
            cookies = state.get("cookies") or []
            if cookies:
                await self._context.add_cookies(cookies)
                return True
        except Exception as e:
            log_warning(f"ط¨ط§ط²غŒط§ط¨غŒ ظ†ط´ط³طھ ظ†ط§ظ…ظˆظپظ‚: {e}")
        return False

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH
        base = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "viewport": {"width": 1366, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--no-first-run",
            ],
        }
        if CHROME_PATH:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(
                    executable_path=CHROME_PATH, **base
                )
                log_info("ظ…ط±ظˆط±ع¯ط± ط¨ط§ CHROME_PATH")
                return ctx
            except Exception as e:
                log_warning(f"CHROME_PATH ظ†ط§ظ…ظˆظپظ‚: {e}")
        last_err = None
        for channel in ("chrome", None):
            try:
                kwargs = dict(base)
                if channel:
                    kwargs["channel"] = channel
                ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
                log_info(f"ظ…ط±ظˆط±ع¯ط± ط¨ط§ {channel or 'chromium'}")
                return ctx
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"ظ‡غŒع† ظ…ط±ظˆط±ع¯ط±غŒ ط¨ط§ط² ظ†ط´ط¯: {last_err}")

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
                log_warning(
                    "âڑ ï¸ڈ ظ†ط´ط³طھ ظ…ظ†ظ‚ط¶غŒ â€” `python login.py` (ع¯ط²غŒظ†ظ‡ظ” غ¶)")
                return False
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return True

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # fetch_messages
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def fetch_messages(self, since: datetime |
                             None = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
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
                log_warning(f"ط®ط·ط§ ط¯ط± ظپغŒط¯: {e}")

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
                    log_warning(f"ط®ط·ط§ ط¯ط± آ«{q}آ»: {e}")
                    continue

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        await self._export_state()

        if self._once_per_day and not self._force_run:
            self._save_last_run()

        s = self._last_scrape_stats
        log_info(
            f"ًں“ٹ [linkedin:stats] total={s['total_posts']} | "
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
            log_success(f"ًں“¥ {len(new_msgs)} ظ¾ط³طھ ط§ط² ظ„غŒظ†ع©ط¯غŒظ†")
        return new_msgs

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # scrape ظپغŒط¯
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def _scrape_feed(self):
        page = self._page

        log_info(f"ًںڈ  LinkedIn: ط±ظپطھظ† ط¨ظ‡ feed | url={self._feed_url}")
        try:
            await page.goto(
                self._feed_url,
                wait_until="domcontentloaded",
                timeout=90000,
            )
        except Exception as e:
            log_warning(f"ط®ط·ط§ ط¯ط± ط¨ط§ط² ع©ط±ط¯ظ† feed: {e}")
            return

        await self._human_delay(5, 8)

        if not await self._check_session_alive():
            return

        log_info("ًں”چ ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ط¯ع©ظ…ظ‡ظ” 'Recent'...")
        clicked = await self._click_sort_recent()

        if clicked:
            log_success("âœ… Sort â†’ Recent")
            await self._human_delay(5, 8)
            try:
                await self._page.wait_for_selector(
                    "div[role='listitem']", timeout=20000
                )
                log_info("   âœ… ظ¾ط³طھâ€Œظ‡ط§ ظ„ظˆط¯ ط´ط¯ظ†ط¯")
            except Exception:
                log_warning("   âڑ ï¸ڈ ظ¾ط³طھâ€Œظ‡ط§ ظ„ظˆط¯ ظ†ط´ط¯ظ†ط¯")
        else:
            log_warning(
                "âڑ ï¸ڈ ط¯ع©ظ…ظ‡ظ” Recent ظ¾غŒط¯ط§ ظ†ط´ط¯ â€” ط§ط¯ط§ظ…ظ‡ ط¨ط¯ظˆظ† sort")

        log_info("ًں–±ï¸ڈ scroll ط¢ظ‡ط³طھظ‡...")
        total = self._scrolls + 3
        for i in range(total):
            with contextlib.suppress(Exception):
                await page.mouse.move(
                    random.randint(400, 900),
                    random.randint(300, 700),
                    steps=random.randint(5, 15),
                )
            await self._human_scroll(random.randint(800, 1400))
            await self._human_delay(2, 4)
            if (i + 1) % 2 == 0:
                log_info(f"   â†•ï¸ڈ {i + 1}/{total}")

        await self._human_delay(2, 4)

        posts = await self._find_posts()
        if not posts:
            log_warning("âڑ ï¸ڈ ظ¾ط³طھغŒ ظ¾غŒط¯ط§ ظ†ط´ط¯")
            return

        log_info(f"ًں“„ {len(posts)} ظ¾ط³طھ ظ¾غŒط¯ط§ ط´ط¯")
        if self._max_feed_posts <= 0:
            selected = posts
            log_info(
                f"   ًں”¬ ط­ط§ظ„طھ ط¨ط¯ظˆظ† ظ…ط­ط¯ظˆط¯غŒطھ â€” ظ‡ظ…ظ‡ {
                    len(posts)} ظ¾ط³طھ")
        else:
            selected = posts[: self._max_feed_posts]
            log_info(
                f"   ًں”¬ ط­ط§ظ„طھ ظ…ط­ط¯ظˆط¯ â€” {
                    len(selected)} ظ¾ط³طھ ط§ط² {
                    len(posts)}")

        count = await self._harvest_posts(selected, source="ظپغŒط¯", human=True)
        if count:
            log_success(f"ًںڈ  ظپغŒط¯: {count} ظ¾ط³طھ ط¬ط¯غŒط¯")

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # scrape search
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def _scrape_search(self, query: str):
        page = self._page
        url = LINKEDIN_SEARCH_URL.format(q=quote(query))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            log_warning(f"ط®ط·ط§ ط¯ط± search آ«{query}آ»: {e}")
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
            log_info(f"â„¹ï¸ڈ ظ†طھغŒط¬ظ‡â€Œط§غŒ ط¨ط±ط§غŒ آ«{query}آ» ظ†ط¨ظˆط¯")
            return
        count = await self._harvest_posts(
            posts[: self._max_results],
            source=query,
            query=query,
            human=True,
        )
        if count:
            log_success(f"ًں”ژ آ«{query}آ»: {count} ظ¾ط³طھ")

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # Sort â†’ Recent
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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
                    log_success(f"âœ… ط§ط² ظ‚ط¨ظ„ Recent | {current}")
                    return True
        except Exception as e:
            log_warning(f"   chk: {e}")

        log_info("   ًں”چ ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ط¯ع©ظ…ظ‡ظ” Sort...")
        try:
            sort_loc = self._page.locator("p:has-text('Sort by:')").first
            cnt = await sort_loc.count()
            if cnt == 0:
                log_warning("   âڑ ï¸ڈ ط¯ع©ظ…ظ‡ظ” Sort ظ¾غŒط¯ط§ ظ†ط´ط¯")
                return False
        except Exception as e:
            log_warning(f"   âڑ ï¸ڈ {e}")
            return False

        log_info("   ًں–±ï¸ڈ ع©ظ„غŒع© ط±ظˆغŒ Sort...")
        try:
            await sort_loc.scroll_into_view_if_needed(timeout=2000)
            await asyncio.sleep(0.3)
            await sort_loc.click(timeout=5000)
            await asyncio.sleep(1.5)
            log_success("   âœ… ع©ظ„غŒع© ط´ط¯")
        except Exception as e:
            log_warning(f"   âڑ ï¸ڈ ع©ظ„غŒع© ظ†ط´ط¯: {e}")
            return False

        log_info("   ًں”½ ع†ع© ع©ط±ط¯ظ† dropdown...")
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
                    log_success("   âœ… dropdown ط¨ط§ط² ط´ط¯")
                    break
            except Exception:
                continue

        if not dropdown_open:
            log_warning("   âڑ ï¸ڈ dropdown ط¨ط§ط² ظ†ط´ط¯")
            return False

        log_info("   ًںژ¯ ع©ظ„غŒع© ط±ظˆغŒ 'Recent'...")
        await asyncio.sleep(0.5)
        try:
            recent_loc = self._page.locator(
                "div[role='menuitem']:has-text('Recent')"
            ).first
            cnt = await recent_loc.count()
            if cnt > 0:
                await recent_loc.click(timeout=5000)
                log_success("âœ… Sort â†’ Recent")
                await asyncio.sleep(2)
                try:
                    await self._page.wait_for_selector(
                        "div[role='listitem']", timeout=15000
                    )
                    log_info("   âœ… ظ¾ط³طھâ€Œظ‡ط§ ظ„ظˆط¯ ط´ط¯ظ†ط¯")
                except Exception:
                    log_warning("   âڑ ï¸ڈ ظ¾ط³طھâ€Œظ‡ط§ ظ„ظˆط¯ ظ†ط´ط¯ظ†ط¯")
                return True
            else:
                log_warning("   âڑ ï¸ڈ Recent ظ¾غŒط¯ط§ ظ†ط´ط¯")
        except Exception as e:
            log_warning(f"   âڑ ï¸ڈ ع©ظ„غŒع© Recent: {e}")

        return False

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ظ¾ط³طھâ€Œظ‡ط§
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def _find_posts(self):
        for attempt in range(10):
            for sel in ("div[role='listitem']", "main ul li"):
                try:
                    posts = await self._page.query_selector_all(sel)
                    if posts:
                        log_info(
                            f"ظ„غŒظ†ع©ط¯غŒظ†: {
                                len(posts)} ظ¾ط³طھ ط¨ط§ `{sel}`")
                        return posts
                except Exception:
                    continue
            await asyncio.sleep(1)
        return []

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ع†ع© ظپط§ظ„ظˆظˆط± (ط¯ظ‚غŒظ‚ â€” ظ†ظ‡ط§غŒغŒ)
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # ع†ع© ظپط§ظ„ظˆظˆط± (ط¯ظ‚غŒظ‚ â€” v2طŒ ظ‡ظ…ط®ظˆط§ظ† ط¨ط§ DOM ظˆط§ظ‚ط¹غŒ ظ„غŒظ†ع©ط¯غŒظ† 2026)
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def _is_follower_post(self, post) -> bool | None:
        """
        True  = ظپط§ظ„ظˆظˆط± (ط¯ط± header آ«â€¢ Followingآ» ط¯ط§ط±ط¯)
        False = ط؛غŒط±ظپط§ظ„ظˆظˆط± (ط¯ع©ظ…ظ‡ظ” آ«Follow <Name>آ» ط¯ط§ط±ط¯)
        None  = Company غŒط§ ظ†ط§ظ…ط´ط®طµ (skip ظ…ط­ط§ظپط¸ظ‡â€Œع©ط§ط±ط§ظ†ظ‡)

        طھط±طھغŒط¨ طھط´ط®غŒطµ (ط¨ط± ط§ط³ط§ط³ DOM ظˆط§ظ‚ط¹غŒ ظ„غŒظ†ع©ط¯غŒظ† 2026):
          غ±. aria-label ط´ط±ظˆط¹â€Œط´ط¯ظ‡ ط¨ط§ "Follow " (ظ†ظ‡ "Following") â†’ False
          غ². header ط­ط§ظˆغŒ "â€¢ following" â†’ True
          غ³. aria-label ط­ط§ظˆغŒ "view company:" â†’ None (Company)
          غ´. ط¯ع©ظ…ظ‡ظ” "Open control menu for post by X" ط¨ط¯ظˆظ† Follow/Following â†’ None (Company)
          غµ. ظ‡غŒع†â€Œع©ط¯ط§ظ… â†’ None (ظ†ط§ظ…ط´ط®طµ)
        """
        try:
            # â”€â”€â”€ ط§ط³طھط®ط±ط§ط¬ aria-labelظ‡ط§ â”€â”€â”€
            btns = await post.query_selector_all("button[aria-label]")
            labels = []
            for b in btns:
                label = await b.get_attribute("aria-label") or ""
                labels.append(label)

            log_debug(
                f"       ًں”چ [follower] buttons ({len(labels)}): "
                f"{labels[:5]}"
            )

            # â”€â”€â”€ غ±) ط¯ع©ظ…ظ‡ظ” Follow (ط؛غŒط±ظپط§ظ„ظˆظˆط±) â”€â”€â”€
            for label in labels:
                lower = label.lower().strip()
                # "follow x" ظˆظ„غŒ ظ†ظ‡ "following x" ظˆ ظ†ظ‡ "follow-up"
                if lower.startswith(
                        "follow ") and not lower.startswith("following "):
                    log_debug(
                        f"       ًں”چ [follower] â†’ False "
                        f"(aria-label: {label[:50]})"
                    )
                    return False

            # â”€â”€â”€ غ²) آ«â€¢ Followingآ» ط¯ط± header (ظپط§ظ„ظˆظˆط±)
            # â”€â”€â”€
            header = await post.evaluate(
                "(el) => (el.innerText || '').slice(0, 400)"
            )
            if "â€¢ following" in (header or "").lower():
                log_debug(
                    "       ًں”چ [follower] â†’ True "
                    "(header: 'â€¢ Following')"
                )
                return True

            # â”€â”€â”€ غ³) View company: (Company) â”€â”€â”€
            for label in labels:
                if "view company:" in label.lower():
                    log_debug(
                        f"       ًں”چ [follower] â†’ None "
                        f"(aria-label: {label[:50]})"
                    )
                    return None

            # â”€â”€â”€ غ´) ع©ظ†طھط±ظ„ ظ…ظ†ظˆغŒ ظ¾ط³طھ ط¨ط¯ظˆظ†
            # Follow/Following â†’ Company â”€â”€â”€
            has_post_menu = any(
                lbl.lower().startswith("open control menu for post by ")
                for lbl in labels
            )
            has_follow_signal = any(
                lbl.lower().startswith("follow ") or lbl.lower() == "following"
                for lbl in labels
            )
            if has_post_menu and not has_follow_signal:
                # ظ†ط§ظ… ط´ط±ع©طھ ط±ط§ ط¨ط±ط§غŒ ظ„ط§ع¯ ط§ط³طھط®ط±ط§ط¬ ع©ظ†
                cname = ""
                for lbl in labels:
                    if lbl.lower().startswith("open control menu for post by "):
                        cname = lbl[len("Open control menu for post by "):]
                        break
                log_debug(
                    f"       ًںڈ¢ [follower] â†’ Company "
                    f"(no follow btn: {cname[:40]})"
                )
                return None

            # â”€â”€â”€ غµ) ظ†ط§ظ…ط´ط®طµ â”€â”€â”€
            log_debug(
                "       ًں”چ [follower] â†’ None "
                "(ظ‡غŒع† ظ†ط´ط§ظ†ظ‡â€Œط§غŒ ظ¾غŒط¯ط§ ظ†ط´ط¯)"
            )
            return None

        except Exception as e:
            log_debug(
                f"       ًں”چ [follower] â†’ None "
                f"(ط®ط·ط§: {type(e).__name__}: {e})"
            )
            return None

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # id ظ¾ط§غŒط¯ط§ط± ط§ط² componentkey
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ

    async def _get_post_id(self, post) -> str:
        """id ظ¾ط§غŒط¯ط§ط± ط§ط² componentkey غŒط§ fallback."""
        try:
            # غ±) componentkey
            ck = await post.get_attribute("componentkey") or ""
            if ck:
                if "FeedType" in ck:
                    ck = ck.split("FeedType")[0]
                ck = ck.removeprefix("update-card-focus")
                if ck:
                    return hashlib.sha256(
                        f"linkedin:{ck}".encode()
                    ).hexdigest()[:32]

            # غ²) data-urn / data-id
            for attr in ("data-urn", "data-id"):
                v = await post.get_attribute(attr)
                if v:
                    return hashlib.sha256(
                        f"linkedin:{v}".encode()
                    ).hexdigest()[:32]

            # غ³) fallback: hash ظ…طھظ†
            text = await post.inner_text() or ""
            if text:
                return hashlib.sha256(
                    f"linkedin:{text[:1000]}".encode()
                ).hexdigest()[:32]
            return ""
        except Exception:
            return ""

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # harvest
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    async def _harvest_posts(
        self,
        posts,
        source: str,
        query: str | None = None,
        human: bool = False,
    ) -> int:
        count = 0
        consecutive_non_followers = 0
        total_posts = len(posts)
        self._last_scrape_stats["total_posts"] += total_posts

        log_info(
            f"ًں”¬ [linkedin:harvest] ط´ط±ظˆط¹ ظ¾ط±ط¯ط§ط²ط´ {total_posts} ظ¾ط³طھ...")

        for idx, post in enumerate(posts, start=1):
            try:
                log_info(
                    f"   â–¶ï¸ڈ [{idx}/{total_posts}] ط¨ط±ط±ط³غŒ ظ¾ط³طھ...")

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ°) scroll ط§غŒظ…ظ†
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                if human:
                    try:
                        await post.scroll_into_view_if_needed(timeout=3000)
                        await self._human_delay(0.3, 0.8)
                    except Exception as e:
                        log_debug(f"   scroll skip: {type(e).__name__}")

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ±) ع©ظ„غŒع© "â€¦more" â†گ ظ‚ط¨ظ„ ط§ط² ع†ع© ظپط§ظ„ظˆظˆط±
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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
                                lower === 'â€¦more' ||
                                lower === 'â€¦ more' ||
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
                except Exception as e:
                    log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ²) ع©ظ„غŒع© "Show translation" â†گ ظ‚ط¨ظ„ ط§ط² ع†ع© ظپط§ظ„ظˆظˆط±
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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
                except Exception as e:
                    log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                log_info(
                    f"       {'âœ… more' if more_clicked else 'âڈ­ï¸ڈ no-more'} | "
                    f"{'âœ… طھط±ط¬ظ…ظ‡' if tr_clicked else 'âڈ­ï¸ڈ no-tr'}"
                )

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ³) ظ…طھظ† ظ†ظ‡ط§غŒغŒ â†گ ط­ط§ظ„ط§ ع©ط§ظ…ظ„ ط´ط¯ظ‡
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                text = (await post.inner_text() or "").strip()
                if not text or len(text) < 20:
                    self._last_scrape_stats["skipped_short"] += 1
                    log_info("       âڈ­ï¸ڈ ظ…طھظ† ط®غŒظ„غŒ ع©ظˆطھط§ظ‡ â€” ط±ط¯")
                    continue

                text_lower = text.lower()
                if any(m in text_lower for m in _UI_MARKERS):
                    self._last_scrape_stats["skipped_ui"] += 1
                    log_info("       âڈ­ï¸ڈ UI element â€” ط±ط¯")
                    continue

                log_info(f"       ًں“‌ text: {text[:80]}...")
                log_info(f"       len={len(text)}")

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ´) ع†ع© ظپط§ظ„ظˆظˆط± â†گ ط¨ط§ ظ…طھظ† ع©ط§ظ…ظ„
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                if self._following_only and source == "ظپغŒط¯":
                    is_follower = await self._is_follower_post(post)

                    if is_follower is False:
                        consecutive_non_followers += 1
                        self._last_scrape_stats["skipped_non_follower"] += 1
                        log_info(
                            f"       âڈ­ï¸ڈ ط؛غŒط±ظپط§ظ„ظˆظˆط± "
                            f"(#{consecutive_non_followers})"
                        )
                        if (
                            self._stop_on_non_follower
                            and consecutive_non_followers
                            >= _MAX_CONSECUTIVE_NON_FOLLOWERS
                        ):
                            log_success(
                                f"âڈ¹ï¸ڈ [linkedin] {consecutive_non_followers} "
                                f"ط؛غŒط±ظپط§ظ„ظˆظˆط± ظ¾ط´طھâ€Œط³ط±ظ‡ظ… â€” طھظˆظ‚ظپ"
                            )
                            break
                        continue

                    elif is_follower is True:
                        consecutive_non_followers = 0
                        self._last_scrape_stats["skipped_follower"] += 1
                        log_info("       âœ… ظپط§ظ„ظˆظˆط± â€” ط§ط¯ط§ظ…ظ‡")

                    else:
                        # None: غŒط§ Company غŒط§ ظ†ط§ظ…ط´ط®طµ
                        # ط§ع¯ط± آ«Feed post\n\n<Name>\n\nآ» ظˆ Name ط´ط¨غŒظ‡ ط´ط±ع©طھ ط¨ظˆط¯
                        # ط³ط§ط¯ظ‡: ع†ع© ع©ظ†غŒظ… ط¢غŒط§ ط¯ط± aria-label ظ‡ط§
                        # آ«Open control menu for post by Xآ» ظ‡ط³طھ ط¨ط¯ظˆظ†
                        # Follow
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
                            log_info("       ًںڈ¢ Company â€” skip")
                        else:
                            self._last_scrape_stats["skipped_unknown"] += 1
                            log_info("       â‌“ ظ†ط§ظ…ط´ط®طµ â€” skip")
                        continue

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غµ) ع©ظ„غŒط¯ظˆط§عکظ‡ â†گ ط¨ط§ ظ…طھظ† ع©ط§ظ…ظ„
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                matched = self._match_post(text, query)
                if not matched:
                    self._last_scrape_stats["skipped_no_kw"] += 1
                    log_info("       âڈ­ï¸ڈ ط¨ط¯ظˆظ† ع©ظ„غŒط¯ظˆط§عکظ‡")
                    continue

                log_info(
                    f"       ًںژ¯ ع©ظ„غŒط¯ظˆط§عکظ‡: {
                        ', '.join(
                            sorted(matched))}")

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ¶) ط¶ط¯طھع©ط±ط§ط±
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                mid = await self._get_post_id(post)
                if not mid:
                    log_info("       âڈ­ï¸ڈ id ظ†ط§ظ…ط´ط®طµ â€” ط±ط¯")
                    continue
                if mid in self._seen_ids:
                    self._last_scrape_stats["skipped_seen"] += 1
                    log_info(f"       âڈ­ï¸ڈ طھع©ط±ط§ط±غŒ (seen) | {mid[:20]}")
                    continue
                self._seen_ids.add(mid)

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ·) author + permalink
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                author = ""
                try:
                    a = await post.query_selector("a[href*='/in/']")
                    if a:
                        author = (await a.inner_text() or "").strip()[:100]
                except Exception as e:
                    log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                permalink = await self._extract_link(post)

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ¸) media
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                media_type, local_path = "text", None
                try:
                    from core.adapters.web_media import download_dom_image
                    mt, lp = await download_dom_image(
                        self._page, post, self._media_dir, "linkedin"
                    )
                    if mt:
                        media_type, local_path = mt, lp
                except Exception as e:
                    log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
                # غ¹) ط°ط®غŒط±ظ‡
                # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
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
                    "timestamp": datetime.now(),  # noqa: DTZ005
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_path,
                    "keywords": ",".join(sorted(matched)),
                    "reply_to": None,
                })
                count += 1
                self._last_scrape_stats["stored"] += 1
                log_success(
                    f"       âœ… ط°ط®غŒط±ظ‡ ط´ط¯ | author={author or '?'} | "
                    f"kw={','.join(sorted(matched))}"
                )

                if human:
                    await self._human_delay(0.3, 1.0)

            except Exception as e:
                self._last_scrape_stats["errors"] += 1
                log_warning(f"       â‌Œ ط®ط·ط§ ط¯ط± ظ¾ط³طھ [{idx}]: {e}")
                continue

        log_info(
            f"ًں”¬ [linkedin:harvest] ظ¾ط§غŒط§ظ† | "
            f"ع©ظ„={total_posts} | ط°ط®غŒط±ظ‡={count} | "
            f"skip={total_posts - count}"
        )
        return count

    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ
    # helpers
    # â•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گâ•گ

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
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return None

    async def dump_dom(self, path: str | None = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "linkedin_dom_dump.html"
        )
        html = await self._page.content()

        def _write():
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        await asyncio.to_thread(_write)
        log_info(f"DOM ط¯ط± {path}")
        return path

    async def disconnect(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("ًں”Œ ظ‚ط·ط¹ ط§ط±طھط¨ط§ط· ط§ط² ظ„غŒظ†ع©ط¯غŒظ†")

    async def check_health(self) -> bool:
        return self._connected
