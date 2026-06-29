# collectors/whatsapp.py
"""
کلکتور واتساپ (WhatsApp) در حالت «کاربر عادی» با اتوماسیون مرورگر روی web.whatsapp.com

واتساپ API رسمی برای خواندن گروه‌ها به‌عنوان کاربر ندارد و کتابخانه‌های توکن‌محور
(Business API) به درد این کار نمی‌خورند. تنها راه رایگانِ «به‌عنوان کاربر» = ورود به
web.whatsapp.com با اسکن QR و خواندن پیام‌ها از صفحه (مثل ایتا).

ورود یک‌بار و دستی: `python login.py` (گزینه واتساپ) → مرورگر باز می‌شود، با گوشی QR را
اسکن می‌کنی؛ نشست در پروفایل مرورگر (user_data_dir) ذخیره می‌ماند.

نکته: واتساپ در ایران ممکن است نیاز به VPN داشته باشد (مشکل شبکه، نه این کد).
"""
import asyncio
import os
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional

from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning
from analyzers.keyword_finder import get_keyword_finder

# ایمپورتِ playwright در سطح ماژول با محافظ (قابل patch در تست، graceful اگر نصب نبود)
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
_MAX_SEEN = 8000

# نشانه‌های ورود/عدم‌ورود (وب‌واتساپ نسبتاً پایدار است)
LOGIN_MARKERS = ['canvas[aria-label*="scan" i]', 'div[data-ref]', '[data-testid="qrcode"]']
CHATLIST_MARKERS = ['#pane-side', '[data-testid="chat-list"]', 'div[aria-label="لیست چت"]']
BUBBLE_SELECTORS = ['div.message-in, div.message-out', 'div[data-id]']


class WhatsAppCollector(BaseCollector):
    def __init__(self, phone: str = None, headless: bool = True,
                 poll_seconds: int = 180, max_chats: int = 15,
                 chat_open_delay: float = 1.5, messages_per_chat: int = 30,
                 backfill_scrolls: int = 6, **kwargs):
        super().__init__()
        self.phone = phone
        self.headless = headless
        self._poll_seconds = int(poll_seconds)
        self._max_chats = int(max_chats)
        self._chat_open_delay = float(chat_open_delay)
        self._messages_per_chat = int(messages_per_chat)
        self._backfill_scrolls = int(backfill_scrolls)
        self._needs_backfill = True
        self._pw = None
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: List[Dict[str, Any]] = []
        self._seen_ids = set()
        self._processed_chats = set()
        # matcher مشترک: عکس فقط برای پیام‌های کلیدواژه‌دار دانلود می‌شود
        self._kw = get_keyword_finder()

        from config import STORAGE_DIR
        self._user_data_dir = os.path.join(STORAGE_DIR, "sessions", "whatsapp")
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "whatsapp")
        os.makedirs(self._media_dir, exist_ok=True)
        # ضدتکراری ماندگار (مثل ایتا) تا بعد از ری‌استارت پیام‌های قدیمی دوباره خوانده نشوند
        self._seen_path = os.path.join(STORAGE_DIR, "offset", "whatsapp_seen.json")
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

    @property
    def platform_name(self) -> str:
        return "whatsapp"

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False  # خواندن دوره‌ای از DOM

    @property
    def poll_interval_seconds(self) -> float:
        # واتساپ هم اسکرپرِ مرورگری سنگین است؛ مثل ایتا کند اجرا شود نه هر ۲ ثانیه.
        return float(self._poll_seconds)

    # ==================================================
    # ضدتکراری ماندگار (روی دیسک)
    # ==================================================
    def _load_seen(self):
        try:
            with open(self._seen_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} پیامِ دیده‌شدهٔ واتساپ بارگذاری شد")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری واتساپ ناموفق: {e}")

    # ==================================================
    # اتصال (با نشست ذخیره‌شده در پروفایل مرورگر)
    # ==================================================
    async def connect(self, headless: Optional[bool] = None) -> bool:
        try:
            if async_playwright is None:
                log_error("playwright نصب نیست: pip install playwright")
                return False

            use_headless = self.headless if headless is None else headless
            self._pw = await async_playwright().start()
            self._context = await self._launch_context(use_headless)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded")
            await asyncio.sleep(8)  # واتساپ‌وب کند بارگذاری می‌شود

            if await self._is_logged_in():
                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                log_success("✅ اتصال واتساپ‌وب برقرار شد (نشست موجود)")
                return True

            log_error(
                "نشست واتساپ وجود ندارد. یک‌بار `python login.py` (گزینه واتساپ) را اجرا کن "
                "و با گوشی QR را اسکن کن. (در ایران ممکن است VPN لازم باشد)"
            )
            await self.disconnect()
            return False

        except Exception as e:
            log_error(f"خطا در اتصال واتساپ‌وب: {e}")
            self._set_error(str(e))
            return False

    async def _launch_context(self, use_headless: bool):
        """مرورگر را قابل‌حمل باز می‌کند: اگر CHROME_PATH ست باشد همان، وگرنه
        خودکار Chrome→Edge→Chromium (روی سرور لینوکس هم بدون مسیر هاردکد کار می‌کند)."""
        from config import CHROME_PATH
        base = dict(
            user_data_dir=self._user_data_dir,
            headless=use_headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        if CHROME_PATH:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(executable_path=CHROME_PATH, **base)
                log_info("مرورگر واتساپ‌وب با مسیرِ CHROME_PATH باز شد")
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
                log_info(f"مرورگر واتساپ‌وب با {channel or 'chromium'} باز شد")
                return ctx
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"هیچ مرورگری برای واتساپ‌وب باز نشد: {last_err}")

    async def _is_logged_in(self) -> bool:
        try:
            for sel in LOGIN_MARKERS:
                if await self._page.query_selector(sel):
                    return False
            for sel in CHATLIST_MARKERS:
                if await self._page.query_selector(sel):
                    return True
        except Exception:
            pass
        return False

    # ==================================================
    # خواندن پیام‌های جدید از چتِ باز
    # ==================================================
    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        if not self._connected or not self._page:
            return []
        try:
            await self._scrape_all_chats()
        except Exception as e:
            log_error(f"خطا در خواندن پیام واتساپ: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()  # ضدتکراری را ماندگار کن
        self._needs_backfill = False
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از واتساپ")
        return new_msgs

    async def _backfill_scroll(self):
        """در اولین اسکرپ بعد از اتصال، چت را به بالا اسکرول می‌کند (catch-up، best-effort)."""
        if not (self._needs_backfill and self._backfill_scrolls > 0):
            return
        try:
            await self._page.mouse.move(640, 400)   # روی ناحیهٔ پیام‌ها
        except Exception:
            return
        for _ in range(self._backfill_scrolls):
            try:
                await self._page.mouse.wheel(0, -3000)
            except Exception:
                break
            await asyncio.sleep(0.8)

    async def _scrape_all_chats(self):
        """در لیست چت‌ها می‌چرخد، هر گروه/چت را باز می‌کند و پیام‌هایش را می‌خواند.
        پیام‌های نخوانده/آفلاین هم همین‌جا گرفته می‌شوند (چون همهٔ چت‌ها مرور می‌شوند)."""
        page = self._page
        chat_selectors = [
            '#pane-side div[role="listitem"]',
            '[data-testid="cell-frame-container"]',
            '#pane-side [role="row"]',
        ]
        chat_items = []
        for sel in chat_selectors:
            chat_items = await page.query_selector_all(sel)
            if chat_items:
                break

        if not chat_items:
            # خوددیتکشن: DOM را ذخیره کن (اگر انتخابگرها قدیمی شدند، فوری قابل اصلاح)
            try:
                await self.dump_dom()
            except Exception:
                pass
            # اگر لیست پیدا نشد، حداقل چت باز فعلی را بخوان
            await self._scrape_open_chat()
            return

        log_info(f"🔍 واتساپ: {len(chat_items)} چت برای مرور")
        for idx, chat in enumerate(chat_items[:self._max_chats]):
            try:
                await chat.click()
                await asyncio.sleep(self._chat_open_delay)
                await self._scrape_open_chat()
            except Exception as e:
                log_warning(f"خطا در مرور چت واتساپ {idx}: {e}")
                continue

    async def _scrape_open_chat(self):
        """پیام‌های چتِ بازِ فعلی را می‌خواند."""
        page = self._page
        chat_name = await self._get_open_chat_title()

        bubbles = []
        for sel in BUBBLE_SELECTORS:
            bubbles = await page.query_selector_all(sel)
            if bubbles:
                break

        for b in bubbles[-self._messages_per_chat:]:
            try:
                data_id = await b.get_attribute("data-id") or ""
                # data-id واتساپ: false_<chat>@g.us_<msgid>_<sender>
                chat_id = chat_name
                if "@g.us" in data_id or "@c.us" in data_id:
                    parts = data_id.split("_")
                    if len(parts) >= 2:
                        chat_id = parts[1]
                # متن
                text = ""
                tnode = await b.query_selector("span.selectable-text, .copyable-text span.selectable-text")
                if tnode:
                    text = (await tnode.inner_text() or "").strip()
                if not text:
                    text = (await b.inner_text() or "").strip()

                # شناسهٔ پایدار (md5 به‌جای hash ناپایدار) و ضدتکراری قبل از هر کار سنگین
                mid = data_id or hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
                uniq = f"{chat_name}:{mid}"
                if uniq in self._seen_ids:
                    continue
                # بدون متن = بدون امکان تطبیق کلیدواژه → اصلاً نگه ندار
                if not text:
                    continue
                self._seen_ids.add(uniq)

                # فقط پیام‌های کلیدواژه‌دار صف شوند (دیده‌شده ثبت شد، دور بعد دوباره چک نمی‌شود)
                if not self._kw.has_match(text):
                    continue

                # دانلود عکس (این پیام کلیدواژه دارد)
                media_type, local_file_path = "text", None
                try:
                    from core.web_media import download_dom_image
                    mt, lp = await download_dom_image(self._page, b, self._media_dir, "whatsapp")
                    if mt:
                        media_type, local_file_path = mt, lp
                except Exception:
                    pass

                self._messages_queue.append({
                    "id": str(mid),
                    "platform": "whatsapp",
                    "text": text,
                    "chat_id": str(chat_id),
                    "chat_name": chat_name,
                    "chat_type": "group" if "@g.us" in data_id else "private",
                    "chat_username": None,
                    "chat_link": None,  # واتساپ یوزرنیم عمومی ندارد؛ لینک دعوت جداست
                    "user_id": "",
                    "sender_name": "",
                    "timestamp": datetime.now(),
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_file_path,
                    "reply_to": None,
                })
            except Exception:
                continue

    async def _get_open_chat_title(self) -> str:
        for sel in ['header span[title]', '[data-testid="conversation-info-header"] span[title]',
                    'header div[role="button"] span[dir="auto"]']:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.get_attribute("title")) or (await el.inner_text())
                    t = (t or "").strip()
                    if t:
                        return t
            except Exception:
                continue
        return "واتساپ"

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(self._user_data_dir, "whatsapp_dom_dump.html")
        html = await self._page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log_info(f"DOM در {path} ذخیره شد")
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
        log_info("🔌 قطع ارتباط از واتساپ")

    async def check_health(self) -> bool:
        return self._connected
