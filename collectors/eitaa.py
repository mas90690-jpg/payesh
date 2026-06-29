# collectors/eitaa.py
"""
کلکتور ایتا (Eitaa) در حالت «کاربر عادی» با اتوماسیون مرورگر روی web.eitaa.com

چرا مرورگر؟ ایتا هیچ کتابخانه‌ی userbot بومیِ سالمی ندارد (pyeitaa خراب است و
بقیه فقط API ربات eitaayar هستند که نمی‌توانند پیام گروه را به‌عنوان کاربر بخوانند).
تنها راه واقعی «به‌عنوان کاربر» = ورود به web.eitaa.com با شماره و خواندن پیام‌ها از صفحه.

ورود یک‌بار و دستی است: `python login.py` (گزینه ایتا) → مرورگر باز می‌شود، با شماره و
کد پیامکی وارد می‌شوی؛ نشست در پروفایل مرورگر (user_data_dir) ذخیره می‌ماند.

نکته: انتخابگرهای DOM بر اساس نسخه فعلی web.eitaa.com تنظیم شده‌اند.
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

# سقف حافظهٔ مجموعهٔ پیام‌های دیده‌شده (تا رم بیهوده پر نشود)
_MAX_SEEN = 8000

EITAA_WEB_URL = "https://web.eitaa.com/"

# انتخابگرهای DOM بر اساس نسخه فعلی ایتا
LOGIN_MARKERS = ['input[type="tel"]', '.input-field-phone', 'input.input-field-input[type="tel"]']
CHATLIST_MARKERS = ['.chatlist-chat', '.chatlist', '#column-left', '.chatlist-container', '.sidebar-left', '.im_dialogs_col']


class EitaaCollector(BaseCollector):
    def __init__(self, phone: str = None, password: str = None,
                 headless: bool = True, poll_seconds: int = 180,
                 max_chats: int = 15, chat_open_delay: float = 1.5,
                 messages_per_chat: int = 30, backfill_scrolls: int = 6, **kwargs):
        super().__init__()
        self.phone = phone
        self.password = password
        self.headless = headless
        self._poll_seconds = int(poll_seconds)
        self._max_chats = int(max_chats)
        self._chat_open_delay = float(chat_open_delay)
        self._messages_per_chat = int(messages_per_chat)
        self._backfill_scrolls = int(backfill_scrolls)
        self._needs_backfill = True   # اولین اسکرپ بعد از اتصال = catch-up (اسکرول به بالا)
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
        self._user_data_dir = os.path.join(STORAGE_DIR, "sessions", "eitaa_web")
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "eitaa")
        os.makedirs(self._media_dir, exist_ok=True)
        # ضدتکراریِ ماندگار: مجموعهٔ پیام‌های دیده‌شده روی دیسک ذخیره می‌شود
        # تا بعد از ری‌استارت، پیام‌های قدیمی دوباره خوانده/پردازش نشوند.
        self._seen_path = os.path.join(STORAGE_DIR, "offset", "eitaa_seen.json")
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

    @property
    def platform_name(self) -> str:
        return "eitaa"

    @property
    def poll_interval_seconds(self) -> float:
        # ایتا اسکرپرِ مرورگری سنگین است؛ هر poll_seconds ثانیه یک‌بار (نه ۲ ثانیه).
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
                log_info(f"♻️ {len(self._seen_ids)} شناسهٔ پیامِ دیده‌شدهٔ ایتا بارگذاری شد")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            # فقط آخرین _MAX_SEEN تا نگه داشته شود (کنترل حجم و رم)
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری ایتا ناموفق: {e}")

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    # ==================================================
    # اتصال
    # ==================================================
    async def connect(self, headless: Optional[bool] = None) -> bool:
        try:
            if async_playwright is None:
                log_error("playwright نصب نیست: pip install playwright && playwright install chromium")
                return False

            use_headless = self.headless if headless is None else headless
            self._pw = await async_playwright().start()
            self._context = await self._launch_context(use_headless)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._page.goto(EITAA_WEB_URL, wait_until="domcontentloaded")

            # انتظار غیرمسدودکننده برای تشخیص ورود (نشست قبلاً با login.py ساخته شده).
            # ⚠️ هرگز از input() اینجا استفاده نکن: کل event loop را بلاک می‌کند و
            # بقیهٔ پلتفرم‌ها (بله/روبیکا) قطع می‌شوند و موقع خروج کرش می‌دهد.
            for _ in range(20):
                await asyncio.sleep(2)
                if await self._is_logged_in():
                    self._connected = True
                    self._needs_backfill = True   # بعد از هر اتصال، یک‌بار catch-up
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success("✅ اتصال ایتا‌وب برقرار شد (نشست موجود)")
                    return True

            await self.dump_dom()  # برای عیب‌یابی اگر ورود تشخیص داده نشد
            log_error(
                "نشست ایتا‌وب تشخیص داده نشد. یک‌بار `python login.py` (گزینه ایتا) را اجرا کن "
                "و در مرورگر با شماره و کد پیامکی وارد شو."
            )
            await self.disconnect()
            return False

        except Exception as e:
            log_error(f"خطا در اتصال ایتا‌وب: {e}")
            self._set_error(str(e))
            return False

    async def _launch_context(self, use_headless: bool):
        """مرورگر را با اولویت مرورگر نصب‌شده‌ی سیستم باز می‌کند."""
        last_err = None
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = dict(
                    user_data_dir=self._user_data_dir,
                    headless=use_headless,
                    viewport={"width": 1366, "height": 768},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--disable-site-isolation-trials",
                        "--disable-web-security",
                        "--disable-features=BlockInsecurePrivateNetworkRequests",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-accelerated-2d-canvas",
                        "--disable-pdf-viewer",
                        "--disable-component-extensions-with-background-pages",
                        "--disable-default-apps",
                        "--disable-extensions",
                        "--disable-plugins",
                        "--disable-translate",
                        "--disable-sync",
                        "--metrics-recording-only",
                        "--no-first-run",
                        "--safebrowsing-disable-auto-update",
                    ],
                )
                if channel:
                    kwargs["channel"] = channel
                ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
                log_info(f"مرورگر ایتا‌وب با {channel or 'chromium'} باز شد")
                return ctx
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"هیچ مرورگری برای ایتا‌وب باز نشد: {last_err}")

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
    # دریافت پیام‌ها
    # ==================================================
    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        if not self._connected or not self._page:
            return []
        try:
            await self._scrape_all_chats()
        except Exception as e:
            log_error(f"خطا در خواندن پیام ایتا: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        # ضدتکراری را ماندگار کن تا بعد از ری‌استارت، پیام‌های قدیمی دوباره خوانده نشوند
        self._save_seen()
        self._needs_backfill = False   # backfill فقط یک‌بار بعد از اتصال
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از ایتا")
        return new_msgs

    async def _backfill_scroll(self):
        """در اولین اسکرپ بعد از اتصال، چت را به بالا اسکرول می‌کند تا پیام‌های
        قدیمی‌ترِ مدتِ خاموشی هم لود و خوانده شوند (catch-up، best-effort)."""
        if not (self._needs_backfill and self._backfill_scrolls > 0):
            return
        for _ in range(self._backfill_scrolls):
            try:
                await self._page.evaluate(
                    "() => { const c = document.querySelector('.bubbles, .scrollable-y, "
                    "#column-center .scrollable'); if (c) c.scrollTop = Math.max(0, c.scrollTop - 2500); }"
                )
            except Exception:
                break
            await asyncio.sleep(0.8)

    # ==================================================
    # اسکرپ همه چت‌ها (بر اساس DOM واقعی ایتا)
    # ==================================================
    async def _scrape_all_chats(self):
        """اسکرپ پیام‌های همه چت‌های موجود در لیست."""
        page = self._page
        
        # انتخابگر دقیق چت‌ها از DOM ایتا
        chat_selectors = [
            '.chatlist-chat',      # کلاس اصلی هر چت
            '.rp.chatlist-chat',   # با کلاس rp
            'li.chatlist-chat'     # تگ li
        ]
        
        chat_items = []
        for sel in chat_selectors:
            chat_items = await page.query_selector_all(sel)
            if chat_items:
                log_info(f"✅ انتخابگر {sel} کار کرد و {len(chat_items)} چت پیدا شد")
                break
        
        if not chat_items:
            log_warning("هیچ چتی در لیست پیدا نشد")
            # خوددیتکشن: DOM را ذخیره کن تا اگر انتخابگرها قدیمی شدند، فوری قابل اصلاح باشد
            try:
                await self.dump_dom()
            except Exception:
                pass
            await self._scrape_current_chat()
            return

        log_info(f"🔍 {len(chat_items)} چت برای اسکرپ پیدا شد")

        for idx, chat in enumerate(chat_items[:self._max_chats]):  # حداکثر max_chats چت اول
            try:
                # نام چت را از خودِ آیتمِ لیست بگیر (نه از هدر سراسری که همیشه اولی را می‌دهد)
                chat_name = await self._extract_item_title(chat)
                # کلیک روی چت تا باز شود
                await chat.click()
                await asyncio.sleep(self._chat_open_delay)

                # هر بار اسکرپ می‌کنیم؛ تکراری‌ها با _seen_ids حذف می‌شوند،
                # پس پیام‌های جدیدِ همان گروه هم در دورهای بعدی خوانده می‌شوند.
                await self._scrape_current_chat(chat_name)

            except Exception as e:
                log_warning(f"خطا در اسکرپ چت {idx}: {e}")
                continue

    async def _extract_item_title(self, chat_item) -> str:
        """نام چت را از خودِ آیتمِ لیست استخراج می‌کند."""
        for sel in ['.peer-title', '.user-title', '.dialog-title']:
            try:
                el = await chat_item.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        return t
            except Exception:
                continue
        try:
            t = (await chat_item.inner_text() or "").strip()
            return t.split("\n")[0] if t else "ایتا"
        except Exception:
            return "ایتا"

    # ==================================================
    # اسکرپ چت باز فعلی
    # ==================================================
    async def _scrape_current_chat(self, chat_name: str = None):
        """اسکرپ پیام‌های چت باز فعلی."""
        page = self._page
        if not chat_name:
            chat_name = await self._get_open_chat_title()

        if chat_name == "ایتا" or not chat_name:
            log_warning(f"نام چت نامشخص: {chat_name}")
            return

        log_info(f"📖 اسکرپ چت: {chat_name}")

        # catch-up: در اولین اسکرپ بعد از اتصال به بالا اسکرول کن تا پیام‌های قدیمی‌تر لود شوند
        await self._backfill_scroll()

        # ⚠️ پیام‌های «چتِ باز» را بخوان، نه پیش‌نمایشِ لیست کناری.
        # .user-last-message متعلق به لیست کناری است و باعث می‌شد همهٔ چت‌ها یکسان خوانده شوند.
        msg_selectors = [
            '.bubbles .bubble',
            '#column-center .bubble',
            '.bubble',
            '[data-mid]',
            '.im_history_message_wrap',
            '.message-list .message',
            # fallback آخر (کم‌دقت): اگر هیچ‌کدام نبود، پیش‌نمایش لیست
            '.user-last-message',
        ]
        
        messages = []
        used_selector = None
        for sel in msg_selectors:
            try:
                messages = await page.query_selector_all(sel)
                if messages:
                    used_selector = sel
                    log_info(f"✅ پیام‌ها با انتخابگر {sel} پیدا شدند ({len(messages)} پیام)")
                    break
            except:
                continue
        
        if not messages:
            log_warning(f"⚠️ هیچ پیامی در چت {chat_name} پیدا نشد")
            try:
                all_text = await page.inner_text('.chat')
                log_info(f"📄 متن چت: {all_text[:200]}...")
            except:
                pass
            return

        # هنگام catch-up پیام‌های بیشتری (که با اسکرول لود شده‌اند) را بخوان
        limit = max(self._messages_per_chat, 200) if self._needs_backfill else self._messages_per_chat
        msg_count = 0
        for msg in messages[-limit:]:
            try:
                mid = await msg.get_attribute("data-mid") or await msg.get_attribute("data-msgid")
                text = (await msg.inner_text() or "").strip()

                # شناسهٔ پایدار: hash داخلیِ پایتون بین اجراها تصادفی است
                # (PYTHONHASHSEED) و باعث می‌شد همان پیام بعد از ری‌استارت id جدید
                # بگیرد و دوباره ذخیره شود. md5 همیشه یکسان است.
                if not mid and text:
                    mid = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]

                if not mid or not text:
                    continue
                    
                uniq = f"{chat_name}:{mid}"
                if uniq in self._seen_ids:
                    continue
                    
                self._seen_ids.add(uniq)

                # فقط پیام‌های کلیدواژه‌دار را صف کن (نه کارِ بیهوده برای پیام‌های بی‌ربط).
                # پیامِ دیده‌شده در _seen_ids ثبت شد، پس دور بعد دوباره بررسی نمی‌شود.
                if not self._kw.has_match(text):
                    continue

                # دانلود عکس پیام (این پیام کلیدواژه دارد)
                media_type, local_file_path = "text", None
                try:
                    from core.web_media import download_dom_image
                    mt, lp = await download_dom_image(self._page, msg, self._media_dir, "eitaa")
                    if mt:
                        media_type, local_file_path = mt, lp
                except Exception:
                    pass

                self._messages_queue.append({
                    "id": str(mid),
                    "platform": "eitaa",
                    "text": text,
                    "chat_id": chat_name,
                    "chat_name": chat_name,
                    "chat_type": "group",
                    "chat_username": None,
                    "chat_link": None,
                    "user_id": "",
                    "sender_name": "",
                    "timestamp": datetime.now(),
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_file_path,
                    "reply_to": None,
                })
                msg_count += 1
                log_info(f"📩 پیام {msg_count} از {chat_name}: {text[:50]}...")
            except Exception as e:
                log_warning(f"خطا در پردازش پیام: {e}")
                continue
        
        if msg_count > 0:
            log_success(f"📩 از چت {chat_name}: {msg_count} پیام جدید")
        else:
            log_warning(f"⚠️ هیچ پیام جدیدی در چت {chat_name} پیدا نشد")

    # ==================================================
    # دریافت نام چت باز
    # ==================================================
    async def _get_open_chat_title(self) -> str:
        """دریافت نام چت باز فعلی با انتخابگرهای دقیق ایتا."""
        for sel in [
            '.peer-title',           # کلاس اصلی نام چت
            '.dialog-title .peer-title',
            '.user-title .peer-title',
            '.chat-info .title',
            '.topbar .user-title',
            '.im_header_peer_name'
        ]:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        return t
            except Exception:
                continue
        return "ایتا"

    # ==================================================
    # ابزار عیب‌یابی
    # ==================================================
    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(self._user_data_dir, "eitaa_dom_dump.html")
        html = await self._page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        file_size = os.path.getsize(path)
        log_info(f"DOM در {path} ذخیره شد (حجم: {file_size} بایت)")
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
        log_info("🔌 قطع ارتباط از ایتا‌وب")

    async def check_health(self) -> bool:
        return self._connected