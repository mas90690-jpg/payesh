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
                 search_delay: float = 6.0, scrolls: int = 3, **kwargs):
        super().__init__()
        self.email = email
        self.password = password
        self.headless = headless
        self._poll_seconds = int(poll_seconds)
        self._max_results = int(max_results)
        self._search_delay = float(search_delay)
        self._scrolls = int(scrolls)
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

            for _ in range(15):
                await asyncio.sleep(2)
                if await self._is_logged_in():
                    self._connected = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success("✅ اتصال لینکدین برقرار شد (نشست موجود)")
                    return True

            log_error(
                "نشست لینکدین تشخیص داده نشد. یک‌بار `python login.py` (گزینهٔ لینکدین) را اجرا کن "
                "و در مرورگر وارد حساب شو."
            )
            await self.disconnect()
            return False
        except Exception as e:
            log_error(f"خطا در اتصال لینکدین: {e}")
            self._set_error(str(e))
            return False

    async def _launch_context(self, use_headless: bool):
        """مرورگر را با اولویت مرورگر نصب‌شدهٔ سیستم باز می‌کند (مثل ایتا)."""
        last_err = None
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = dict(
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
        searches = self._load_searches()
        if not searches:
            log_warning(f"⚠️ لینکدین: عبارتی در {os.path.basename(self._searches_file)} نیست (فقط عبارت اضافه کن)")
            return []

        for q in searches:
            try:
                await self._scrape_search(q)
                # مکث تصادفیِ انسان‌گونه بین جستجوها (کاهش ریسک)
                await asyncio.sleep(self._search_delay + random.uniform(0, self._search_delay))
            except Exception as e:
                log_warning(f"لینکدین: خطا در جستجوی «{q}»: {e}")
                continue

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پست مرتبط از لینکدین")
        return new_msgs

    async def _scrape_search(self, query: str):
        page = self._page
        url = LINKEDIN_SEARCH_URL.format(q=quote(query))
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # کمی اسکرول تا پست‌های بیشتری لود شوند (انسان‌گونه)
        for _ in range(self._scrolls):
            try:
                await page.mouse.wheel(0, 1200)
            except Exception:
                pass
            await asyncio.sleep(1.2 + random.uniform(0, 1.0))

        post_selectors = [
            'div.feed-shared-update-v2',
            'div.update-components-text',
            'li.reusable-search__result-container',
            'div.fie-impression-container',
        ]
        posts = []
        for sel in post_selectors:
            posts = await page.query_selector_all(sel)
            if posts:
                break
        if not posts:
            log_info(f"لینکدین: نتیجه‌ای برای «{query}» پیدا نشد")
            return

        count = 0
        for post in posts[:self._max_results]:
            try:
                text = (await post.inner_text() or "").strip()
                if not text:
                    continue

                # کلیدواژه: محصولاتِ یافت‌شده + خودِ عبارتِ جستجو اگر در متن باشد
                matched = self._match_post(text, query)
                if not matched:
                    continue  # پستِ بی‌ربط را اصلاً نگه ندار

                # شناسهٔ پایدار از urn یا hash متن
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

                # عکس فقط برای پست‌های مرتبط (که کلیدواژه دارند) دانلود شود
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
                    "chat_id": query,
                    "chat_name": f"LinkedIn: {query}",
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
        if count:
            log_success(f"🔎 لینکدین «{query}»: {count} پست مرتبط جدید")

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
