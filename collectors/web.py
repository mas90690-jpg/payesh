# collectors/web.py
"""
کلکتور وب — صفحات/سایت‌ها را به‌عنوان منبع می‌خواند.

جداسازی (طبق درخواست):
- فایل لینک‌ها (داده):  data/web_sources.txt  → تو فقط لینک می‌گذاری
- این فایل (منطق):      collectors/web.py      → کد را دست نمی‌زنی

دو حالت:
- page → فقط همان یک صفحه
- site → کل سایت تا عمق مشخص (پیش‌فرض depth=1، با محدودیت تعداد صفحه)

ضدتکراری: از محتوای هر صفحه «اثرانگشت» (hash) ساخته می‌شود؛ اگر محتوا عوض نشده باشد،
دوباره خروجی نمی‌گیرد (هم در همین اجرا، هم بین اجراها به‌کمک کلید یکتای دیتابیس).

ترجمه: لایهٔ ترجمه بعداً روی همین خروجی اضافه می‌شود (متن اصلی + فارسی).
"""
import asyncio
import os
import re
import hashlib
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Any, Optional, Set, Tuple

from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class WebCollector(BaseCollector):
    def __init__(self, sources_file: str = None, max_pages: int = 40,
                 timeout: int = 20, translate: bool = False, **kwargs):
        super().__init__()
        from config import BASE_DIR
        self.sources_file = sources_file or os.path.join(BASE_DIR, "data", "web_sources.txt")
        self.max_pages = int(max_pages)
        self.timeout = int(timeout)
        self.translate = bool(translate)
        self._translator = None
        self._sources: List[Tuple[str, str, dict]] = []
        self._connected = False
        self._seen_hashes: Set[str] = set()
        # مرورگرِ تنبل (فقط وقتی درخواست ساده شکست بخورد ساخته می‌شود): برای سایت‌های
        # JS-محور، بلاک‌کنندهٔ ربات (۴۰۳) یا گواهیِ منقضی.
        self._bpw = None
        self._bctx = None

    def _get_translator(self):
        if self._translator is None:
            from core.translator import Translator
            self._translator = Translator(enabled=True)
        return self._translator

    @property
    def platform_name(self) -> str:
        return "web"

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    @property
    def poll_interval_seconds(self) -> float:
        # وب اسکرپر سنگین است؛ با فاصلهٔ سیستم (پیش‌فرض ۲۴ ساعت) اجرا شود نه هر ۲ ثانیه.
        from config import SYSTEM_CONFIG
        return SYSTEM_CONFIG.get("poll_interval_hours", 24) * 3600

    # ==================================================
    # اتصال = بارگذاری فایل لینک‌ها
    # ==================================================
    async def connect(self) -> bool:
        self._sources = self._load_sources()
        self._connected = True
        self._set_status(ConnectionStatus.CONNECTED)
        if self._sources:
            log_success(f"✅ وب: {len(self._sources)} منبع از {os.path.basename(self.sources_file)} بارگذاری شد")
        else:
            log_warning(f"⚠️ وب: هیچ لینکی در {self.sources_file} نیست (فقط لینک اضافه کن)")
        return True

    def _load_sources(self) -> List[Tuple[str, str, dict]]:
        sources = []
        if not os.path.exists(self.sources_file):
            return sources
        with open(self.sources_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 2:
                    continue
                mode = parts[0].lower()
                url = parts[1]
                params = {}
                for extra in parts[2:]:
                    if "=" in extra:
                        k, v = extra.split("=", 1)
                        params[k.strip()] = v.strip()
                if mode not in ("page", "site", "news"):
                    mode = "page"
                sources.append((mode, url, params))
        return sources

    # ==================================================
    # خواندن منابع
    # ==================================================
    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        if not self._connected or not self._sources:
            return []

        results: List[Dict[str, Any]] = []
        try:
            import aiohttp
        except ImportError:
            log_error("aiohttp نصب نیست: pip install aiohttp")
            return []

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for mode, url, params in self._sources:
                    try:
                        if mode == "site":
                            depth = int(params.get("depth", 1))
                            pages = await self._crawl_site(session, url, depth)
                        elif mode == "news":
                            limit = int(params.get("limit", min(self.max_pages, 25)))
                            pages = await self._crawl_news(session, url, limit)
                        else:
                            pages = await self._read_single(session, url)
                        for (page_url, title, text) in pages:
                            msg = self._make_message(page_url, title, text)
                            if msg:
                                results.append(msg)
                    except Exception as e:
                        log_warning(f"وب: خطا در خواندن {url}: {e}")
                        continue
        finally:
            await self._close_browser()   # اگر مرورگرِ رندر باز شده بود، ببند

        if results:
            log_success(f"📥 وب: {len(results)} مطلب جدید")
        return results

    async def _read_single(self, session, url) -> List[Tuple[str, str, str]]:
        html = await self._get_html(session, url)
        if not html:
            return []
        title, text = self._extract(html)
        return [(url, title, text)] if text else []

    async def _crawl_site(self, session, start_url, depth) -> List[Tuple[str, str, str]]:
        """پیمایش هم‌دامنه تا عمق مشخص با محدودیت تعداد صفحه."""
        base_host = urlparse(start_url).netloc
        visited: Set[str] = set()
        out: List[Tuple[str, str, str]] = []
        frontier = [(start_url, 0)]

        while frontier and len(visited) < self.max_pages:
            url, d = frontier.pop(0)
            url = url.split("#")[0]
            if url in visited:
                continue
            visited.add(url)

            html = await self._fetch(session, url)
            if not html:
                continue
            title, text = self._extract(html)
            if text:
                out.append((url, title, text))

            if d < depth:
                for link in self._extract_links(html, url):
                    if urlparse(link).netloc == base_host and link not in visited:
                        frontier.append((link, d + 1))
            await asyncio.sleep(0.3)  # مودب بودن با سرور
        return out

    async def _crawl_news(self, session, index_url, limit) -> List[Tuple[str, str, str]]:
        """صفحهٔ فهرستِ اخبار را باز می‌کند، لینکِ خبرها را پیدا و **وارد خودِ خبرها** می‌شود
        و متنِ کاملِ هر خبر را می‌خواند (نه فقط تیتر). فقط لینک‌های زیرشاخهٔ همان بخشِ خبری."""
        index_html = await self._get_html(session, index_url)
        if not index_html:
            return []

        base = urlparse(index_url)
        host = base.netloc
        index_path = base.path.rstrip("/")           # مثلاً /en/news

        def collect(links):
            out = []
            for link in links:
                link = (link or "").split("#")[0].rstrip("/")
                p = urlparse(link)
                if p.netloc != host:
                    continue
                path = p.path.rstrip("/")
                # صفحهٔ یک خبر = زیرِ همان بخشِ خبری و عمیق‌تر از خودِ فهرست
                if path.startswith(index_path + "/") and path != index_path:
                    out.append(link)
            return out

        # اول لینک‌های HTML ساکن
        article_links = list(dict.fromkeys(collect(self._extract_links(index_html, index_url))))

        # اگر چیزی پیدا نشد (سایت JS-محور)، از DOMِ زندهٔ مرورگر بعد از اسکرول بگیر
        if not article_links:
            article_links = list(dict.fromkeys(collect(await self._rendered_links(index_url))))

        article_links = article_links[:limit]

        if not article_links:
            # اگر ساختار خبرها زیرشاخه نبود، دستِ‌کم خودِ صفحهٔ فهرست را بده
            t, x = self._extract(index_html)
            return [(index_url, t, x)] if x else []

        out: List[Tuple[str, str, str]] = []
        for art_url in article_links:
            html = await self._get_html(session, art_url)
            if not html:
                continue
            title, text = self._extract(html)
            if text:
                out.append((art_url, title, text))
            await asyncio.sleep(0.3)   # مودب بودن با سرور
        log_info(f"وب(news): {len(out)} خبر از {host} خوانده شد")
        return out

    async def _get_html(self, session, url) -> Optional[str]:
        """HTML را می‌گیرد؛ اول سریع (aiohttp)، اگر شکست خورد (۴۰۳/SSL/JS) با مرورگرِ
        واقعی رندر می‌کند تا «مثل یک کاربر» صفحه را ببیند."""
        html = await self._fetch(session, url)
        if html:
            return html
        return await self._fetch_rendered(url)

    async def _fetch(self, session, url) -> Optional[str]:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if resp.status != 200 or "text/html" not in ctype:
                    return None
                return await resp.text(errors="ignore")
        except Exception:
            return None

    async def _ensure_browser(self):
        """مرورگرِ headless را یک‌بار می‌سازد (با چشم‌پوشی از خطای گواهی)."""
        if self._bctx is not None:
            return self._bctx
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log_warning("playwright نصب نیست؛ سایت‌های JS-محور/بلاک‌شده خوانده نمی‌شوند")
            return None
        from config import CHROME_PATH
        try:
            self._bpw = await async_playwright().start()
        except Exception as e:
            log_warning(f"وب: شروع playwright ناموفق: {e}")
            return None

        base = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        # مثل بقیهٔ کلکتورها: اول مرورگرِ نصب‌شدهٔ سیستم (Chrome/Edge)، بعد chromiumِ دانلودی
        attempts = []
        if CHROME_PATH:
            attempts.append({**base, "executable_path": CHROME_PATH})
        for ch in ("chrome", "msedge", None):
            attempts.append({**base, **({"channel": ch} if ch else {})})

        for opt in attempts:
            try:
                browser = await self._bpw.chromium.launch(**opt)
                self._bctx = await browser.new_context(
                    ignore_https_errors=True,   # گواهیِ منقضی را رد کن
                    user_agent=DEFAULT_UA,
                    viewport={"width": 1366, "height": 900},
                )
                log_info(f"وب: مرورگرِ رندر آماده شد ({opt.get('channel') or opt.get('executable_path') or 'chromium'})")
                return self._bctx
            except Exception:
                continue
        log_warning("وب: هیچ مرورگری برای رندر باز نشد (Chrome/Edge/Chromium نصب نیست)")
        try:
            await self._bpw.stop()
        except Exception:
            pass
        self._bpw = None
        return None

    async def _fetch_rendered(self, url) -> Optional[str]:
        ctx = await self._ensure_browser()
        if ctx is None:
            return None
        page = None
        try:
            page = await ctx.new_page()
            # سایت‌های کند/ریدایرکتی (مثل گواهیِ منقضی) به مهلتِ بیشتری نیاز دارند
            await page.goto(url, wait_until="domcontentloaded", timeout=max(self.timeout, 45) * 1000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(2.0)   # فرصت به JS برای لود محتوا
            # گاهی صفحه در حال ناوبری است؛ content() را با چند تلاش بگیر
            for _ in range(3):
                try:
                    return await page.content()
                except Exception:
                    await asyncio.sleep(1.5)
            return None
        except Exception as e:
            log_warning(f"وب: رندرِ {url} ناموفق: {e}")
            return None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _rendered_links(self, url) -> List[str]:
        """صفحه را رندر می‌کند، چند بار اسکرول می‌کند و همهٔ لینک‌های هم‌دامنه را از DOMِ
        زنده برمی‌گرداند (برای سایت‌های خبریِ JS-محور که لینک‌ها بعد از اسکرول لود می‌شوند)."""
        ctx = await self._ensure_browser()
        if ctx is None:
            return []
        page = None
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=max(self.timeout, 45) * 1000)
            await asyncio.sleep(2.0)
            for _ in range(5):
                try:
                    await page.mouse.wheel(0, 2000)
                except Exception:
                    pass
                await asyncio.sleep(1.0)
            hrefs = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
            return [h for h in hrefs if h]
        except Exception as e:
            log_warning(f"وب: استخراج لینکِ رندرشدهٔ {url} ناموفق: {e}")
            return []
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _close_browser(self):
        try:
            if self._bctx:
                await self._bctx.browser.close()
            if self._bpw:
                await self._bpw.stop()
        except Exception:
            pass
        self._bctx = None
        self._bpw = None

    # ==================================================
    # استخراج محتوا و لینک‌ها
    # ==================================================
    @staticmethod
    def _extract(html: str) -> Tuple[str, str]:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
                tag.decompose()
            title = (soup.title.string.strip() if soup.title and soup.title.string else "")
            text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        except Exception:
            # fallback ساده بدون bs4
            title = ""
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
        return title, text[:5000]

    @staticmethod
    def _extract_links(html: str, base_url: str) -> List[str]:
        links = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                links.append(urljoin(base_url, a["href"]))
        except Exception:
            for m in re.findall(r'href=["\']([^"\']+)["\']', html):
                links.append(urljoin(base_url, m))
        return links

    def _make_message(self, url: str, title: str, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        # اثرانگشت محتوا برای حذف تکراری
        fingerprint = hashlib.md5((url + "|" + text[:1000]).encode("utf-8")).hexdigest()
        if fingerprint in self._seen_hashes:
            return None
        self._seen_hashes.add(fingerprint)

        domain = urlparse(url).netloc
        full_text = (f"{title}\n{text}" if title else text)[:5000]

        # ترجمهٔ فارسی با واژه‌نامهٔ اختصاصی (در صورت فعال بودن WEB_TRANSLATE)
        text_translated = None
        if self.translate:
            try:
                text_translated = self._get_translator().translate(full_text)
            except Exception as e:
                log_warning(f"ترجمهٔ صفحه ناموفق بود: {e}")

        return {
            "id": f"web_{fingerprint}",     # کلید یکتا → دیتابیس تکراری را رد می‌کند
            "platform": "web",
            "text": full_text,
            "text_translated": text_translated,
            "chat_id": url,
            "chat_name": title or domain,
            "chat_type": "web",
            "chat_username": None,
            "chat_link": url,               # آدرس دقیق صفحه
            "user_id": domain,
            "sender_name": domain,
            "timestamp": datetime.now(),
            "media_type": "text",
            "file_id": None,
            "local_file_path": None,
            "reply_to": None,
        }

    async def disconnect(self) -> None:
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 وب متوقف شد")

    async def check_health(self) -> bool:
        return self._connected
