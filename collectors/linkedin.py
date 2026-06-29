# collectors/linkedin.py
"""
کلکتور لینکدین (LinkedIn) در حالت «کاربر عادی» با اتوماسیون مرورگر (Playwright).

چرا مرورگر و نه کتابخانه؟
- API رسمی لینکدین اجازهٔ خواندن پست/جستجوی محتوای دیگران را نمی‌دهد (فقط OAuthِ محدود).
- کتابخانهٔ غیررسمی `linkedin-api` (Voyager) خلاف قوانین لینکدین است و **خطر بن حساب** دارد.
- پس امن‌ترین و حرفه‌ای‌ترین راهِ «به‌عنوان کاربر»، همان نشستِ واقعیِ مرورگرِ خودِ کاربر است:
  یک‌بار دستی لاگین می‌کنی (`python login.py` → لینکدین)، نشست در پروفایل مرورگر می‌ماند،
  بعد این کلکتور آرام و انسان‌گونه نتایجِ جستجوی محتوا را برای کلیدواژه‌ها می‌خواند.

برای کم‌کردن ریسک: فاصلهٔ زیاد بین دورها (پیش‌فرض ۱۰ دقیقه)، مکث تصادفی بین جستجوها،
و سقف تعداد نتیجه در هر جستجو.

عبارت‌های جستجو را در `data/linkedin_searches.txt` می‌گذاری (هر خط یک عبارت).
"""
import asyncio
import os
import json
import random
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import quote

from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning
from analyzers.keyword_finder import get_keyword_finder

LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_SEARCH_URL = (
    "https://www.linkedin.com/search/results/content/"
    "?keywords={q}&origin=GLOBAL_SEARCH_HEADER&sortBy=%22date_posted%22"
)

# نشانه‌های صفحهٔ ورود (یعنی هنوز لاگین نیستیم)
LOGIN_MARKERS = ['input#username', 'input[name="session_key"]', 'button[data-litms-control-urn="login-submit"]']
# نشانه‌های ورودِ موفق
LOGGEDIN_MARKERS = ['#global-nav', '.global-nav', 'div.scaffold-layout', 'input.search-global-typeahead__input']

_MAX_SEEN = 8000


class LinkedInCollector(BaseCollector):
    def __init__(self, email: str = None, password: str = None,
                 headless: bool = True, poll_seconds: int = 600,
                 searches_file: str = None, max_results: int = 15,
                 search_delay: float = 6.0, scrolls: int = 3,
                 monitor_feed: bool = True, max_feed_posts: int = 25, **kwargs):
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
        self._pw = None
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: List[Dict[str, Any]] = []
        self._seen_ids = set()
        self._kw = get_keyword_finder()

        from config import BASE_DIR, STORAGE_DIR
        self._searches_file = searches_file or os.path.join(BASE_DIR, "data", "linkedin_searches.txt")
        self._user_data_dir = os.path.join(STORAGE_DIR, "sessions", "linkedin_web")
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "linkedin")
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(STORAGE_DIR, "offset", "linkedin_seen.json")
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        # نشستِ ذخیره‌شده (کوکی li_at و...) برای پایداریِ بیشتر؛ علاوه بر پروفایل مرورگر
        self._state_path = os.path.join(STORAGE_DIR, "sessions", "linkedin_state.json")
        self._load_seen()

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
        # محافظه‌کارانه (پیش‌فرض ۱۰ دقیقه) تا رفتارِ ربات‌گونه و خطر بن کم شود.
        return float(self._poll_seconds)

    # ==================================================
    # ضدتکراری ماندگار
    # ==================================================
    def _load_seen(self):
        try:
            with open(self._seen_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} پستِ دیده‌شدهٔ لینکدین بارگذاری شد")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری لینکدین ناموفق: {e}")

    def _load_searches(self) -> List[str]:
        out = []
        if not os.path.exists(self._searches_file):
            return out
        with open(self._searches_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
        return out

    # ==================================================
    # اتصال (نشست از قبل با login.py ساخته شده)
    # ==================================================
    async def connect(self, headless: Optional[bool] = None) -> bool:
        try:
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                log_error("playwright نصب نیست: pip install playwright && playwright install chromium")
                return False

            use_headless = self.headless if headless is None else headless
            self._pw = await async_playwright().start()
            self._context = await self._launch_context(use_headless)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded")

            if await self._wait_login():
                await self._export_state()    # نشست را برای دفعهٔ بعد پایدار کن
                return True

            # پروفایل مرورگر نشست نداشت؛ از فایلِ نشستِ ذخیره‌شده (کوکی li_at) امتحان کن
            if await self._restore_state():
                await self._page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded")
                if await self._wait_login(rounds=6):
                    await self._export_state()
                    log_success("✅ نشست لینکدین از کوکی ذخیره‌شده بازیابی شد")
                    return True

            log_error(
                "نشست لینکدین تشخیص داده نشد یا منقضی شده. یک‌بار `python login.py` (گزینهٔ لینکدین) "
                "را اجرا کن و در مرورگر وارد حساب شو (تیک «keep me logged in»)."
            )
            await self.disconnect()
            return False
        except Exception as e:
            log_error(f"خطا در اتصال لینکدین: {e}")
            self._set_error(str(e))
            return False

    async def _wait_login(self, rounds: int = 15) -> bool:
        for _ in range(rounds):
            await asyncio.sleep(2)
            if await self._is_logged_in():
                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                log_success("✅ اتصال لینکدین برقرار شد (نشست موجود)")
                return True
        return False

    async def _export_state(self):
        """کوکی‌ها/localStorage نشست را ذخیره می‌کند تا نشست پایدار بماند."""
        try:
            await self._context.storage_state(path=self._state_path)
        except Exception as e:
            log_warning(f"ذخیرهٔ نشست لینکدین ناموفق: {e}")

    async def _restore_state(self) -> bool:
        """کوکی‌های نشستِ ذخیره‌شده را به context تزریق می‌کند."""
        try:
            if not os.path.exists(self._state_path):
                return False
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            cookies = state.get("cookies") or []
            if cookies:
                await self._context.add_cookies(cookies)
                return True
        except Exception as e:
            log_warning(f"بازیابی نشست لینکدین ناموفق: {e}")
        return False

    async def _launch_context(self, use_headless: bool):
        """مرورگر را قابل‌حمل باز می‌کند: اگر CHROME_PATH ست باشد همان، وگرنه
        خودکار Chrome→Edge→Chromium (روی سرور لینوکس هم بدون مسیر هاردکد کار می‌کند)."""
        from config import CHROME_PATH
        base = dict(
            user_data_dir=self._user_data_dir,
            headless=use_headless,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--no-first-run",
            ],
        )
        # اگر مسیر مرورگر دستی داده شده، همان (بدون channel)
        if CHROME_PATH:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(executable_path=CHROME_PATH, **base)
                log_info(f"مرورگر لینکدین با مسیرِ CHROME_PATH باز شد")
                return ctx
            except Exception as e:
                log_warning(f"CHROME_PATH کار نکرد ({e})؛ سراغ مرورگر خودکار می‌روم")
        last_err = None
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = dict(base)
                if channel:
                    kwargs["channel"] = channel
                ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
                log_info(f"مرورگر لینکدین با {channel or 'chromium'} باز شد")
                return ctx
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"هیچ مرورگری برای لینکدین باز نشد: {last_err}")

    async def _is_logged_in(self) -> bool:
        try:
            for sel in LOGIN_MARKERS:
                if await self._page.query_selector(sel):
                    return False
            for sel in LOGGEDIN_MARKERS:
                if await self._page.query_selector(sel):
                    return True
        except Exception:
            pass
        return False

    # ==================================================
    # دریافت پیام‌ها = اجرای جستجوها
    # ==================================================
    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        # ۱) فیدِ خانه: پست‌های پیج‌ها/افرادی که فالو کرده‌ای (رفتارِ یک کاربر واقعی)
        if self._monitor_feed:
            try:
                await self._scrape_feed()
            except Exception as e:
                log_warning(f"لینکدین: خطا در خواندن فید: {e}")

        # ۲) جستجوی محتوا برای عبارت‌های اختیاریِ data/linkedin_searches.txt
        searches = self._load_searches()
        for q in searches:
            if not self._connected:    # اگر وسطِ کار نشست منقضی شد، ادامه نده
                break
            try:
                await self._scrape_search(q)
                await asyncio.sleep(self._search_delay + random.uniform(0, self._search_delay))
            except Exception as e:
                log_warning(f"لینکدین: خطا در جستجوی «{q}»: {e}")
                continue

        if not self._monitor_feed and not searches:
            log_warning("⚠️ لینکدین: نه فید فعال است نه عبارتِ سرچ — چیزی برای پایش نیست")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        await self._export_state()     # نشست را تازه نگه دار (پایداری)
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پست مرتبط از لینکدین")
        return new_msgs

    async def _check_session_alive(self) -> bool:
        """اگر لینکدین به صفحهٔ ورود/authwall پرت کرد، یعنی نشست منقضی شده."""
        try:
            url = (self._page.url or "").lower()
            if "/login" in url or "/authwall" in url or "/checkpoint" in url:
                self._connected = False
                self._set_status(ConnectionStatus.ERROR)
                log_warning("⚠️ نشست لینکدین منقضی شد — یک‌بار دوباره `python login.py` (گزینهٔ ۶) را اجرا کن")
                return False
        except Exception:
            pass
        return True

    async def _scrape_feed(self):
        """فیدِ خانه را می‌خواند؛ پست‌هایی که کلیدواژهٔ محصول دارند نگه داشته می‌شوند."""
        page = self._page
        await page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        if not await self._check_session_alive():
            return
        for _ in range(self._scrolls + 2):
            try:
                await page.mouse.wheel(0, 1400)
            except Exception:
                pass
            await asyncio.sleep(1.2 + random.uniform(0, 1.0))

        posts = await self._find_posts()
        count = await self._harvest_posts(posts[:self._max_feed_posts], source="فید")
        if count:
            log_success(f"🏠 فید لینکدین: {count} پست مرتبط جدید")

    async def _scrape_search(self, query: str):
        page = self._page
        url = LINKEDIN_SEARCH_URL.format(q=quote(query))
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        if not await self._check_session_alive():
            return

        # کمی اسکرول تا پست‌های بیشتری لود شوند (انسان‌گونه)
        for _ in range(self._scrolls):
            try:
                await page.mouse.wheel(0, 1200)
            except Exception:
                pass
            await asyncio.sleep(1.2 + random.uniform(0, 1.0))

        posts = await self._find_posts()
        if not posts:
            log_info(f"لینکدین: نتیجه‌ای برای «{query}» پیدا نشد")
            return
        count = await self._harvest_posts(posts[:self._max_results], source=query, query=query)
        if count:
            log_success(f"🔎 لینکدین «{query}»: {count} پست مرتبط جدید")

    async def _find_posts(self):
        """عناصرِ پست را با چند انتخابگرِ پشتیبان پیدا می‌کند (فید و سرچ مشترک)."""
        post_selectors = [
            'div.feed-shared-update-v2',
            'div.update-components-text',
            'li.reusable-search__result-container',
            'div.fie-impression-container',
        ]
        for sel in post_selectors:
            posts = await self._page.query_selector_all(sel)
            if posts:
                return posts
        return []

    async def _harvest_posts(self, posts, source: str, query: str = None) -> int:
        """منطقِ مشترکِ استخراجِ پست‌ها: تطبیق کلیدواژه، ضدتکراری، عکسِ گیت‌شده، صف."""
        count = 0
        for post in posts:
            try:
                text = (await post.inner_text() or "").strip()
                if not text:
                    continue

                # کلیدواژه: محصولاتِ یافت‌شده + (در سرچ) خودِ عبارتِ جستجو اگر در متن باشد
                matched = self._match_post(text, query)
                if not matched:
                    continue  # پستِ بی‌ربط را اصلاً نگه ندار

                urn = await post.get_attribute("data-urn") or await post.get_attribute("data-id")
                mid = urn or hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
                if mid in self._seen_ids:
                    continue
                self._seen_ids.add(mid)

                author = await self._extract(post, [
                    '.update-components-actor__name', '.update-components-actor__title',
                    'span.feed-shared-actor__name',
                ])
                permalink = await self._extract_link(post)

                # عکس فقط برای پست‌های مرتبط (کلیدواژه‌دار) دانلود شود
                media_type, local_file_path = "text", None
                try:
                    from core.web_media import download_dom_image
                    mt, lp = await download_dom_image(self._page, post, self._media_dir, "linkedin")
                    if mt:
                        media_type, local_file_path = mt, lp
                except Exception:
                    pass

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
                    "local_file_path": local_file_path,
                    "keywords": ",".join(sorted(matched)),  # storage این‌ها را حفظ می‌کند
                    "reply_to": None,
                })
                count += 1
            except Exception as e:
                log_warning(f"لینکدین: خطا در یک پست: {e}")
                continue
        return count

    def _match_post(self, text: str, query: str) -> set:
        """کلیدواژه‌های مرتبط با یک پست: محصولاتِ یافت‌شده + عبارتِ جستجو اگر در متن باشد."""
        matched = set(self._kw.match(text))
        if query and query.lower() in (text or "").lower():
            matched.add(query)
        return matched

    @staticmethod
    async def _extract(post, selectors) -> str:
        for sel in selectors:
            try:
                el = await post.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        return t.split("\n")[0]
            except Exception:
                continue
        return ""

    @staticmethod
    async def _extract_link(post) -> Optional[str]:
        try:
            a = await post.query_selector('a.app-aware-link[href*="/feed/update/"]') \
                or await post.query_selector('a[href*="/posts/"]') \
                or await post.query_selector('a.app-aware-link[href]')
            if a:
                href = await a.get_attribute("href")
                if href:
                    return href.split("?")[0]
        except Exception:
            pass
        return None

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(self._user_data_dir, "linkedin_dom_dump.html")
        html = await self._page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log_info(f"DOM لینکدین در {path} ذخیره شد")
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
