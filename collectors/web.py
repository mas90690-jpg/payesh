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
                if mode not in ("page", "site"):
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
        headers = {"User-Agent": DEFAULT_UA}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for mode, url, params in self._sources:
                try:
                    if mode == "site":
                        depth = int(params.get("depth", 1))
                        pages = await self._crawl_site(session, url, depth)
                    else:
                        pages = await self._read_single(session, url)
                    for (page_url, title, text) in pages:
                        msg = self._make_message(page_url, title, text)
                        if msg:
                            results.append(msg)
                except Exception as e:
                    log_warning(f"وب: خطا در خواندن {url}: {e}")
                    continue

        if results:
            log_success(f"📥 وب: {len(results)} مطلب جدید")
        return results

    async def _read_single(self, session, url) -> List[Tuple[str, str, str]]:
        html = await self._fetch(session, url)
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

    async def _fetch(self, session, url) -> Optional[str]:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if resp.status != 200 or "text/html" not in ctype:
                    return None
                return await resp.text(errors="ignore")
        except Exception:
            return None

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
