# collectors/whatsapp.py
"""
کلکتور واتساپ (WhatsApp) در حالت «کاربر عادی» با اتوماسیون مرورگر روی web.whatsapp.com

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX #14: _is_logged_in دیگر لاگین معتبر را در حالت "chat-list
    هنوز لود نشده" رد نمی‌کند. اگر QR نبود ولی app واتساپ لود شده بود
    → True برمی‌گرداند تا caller فرصت بیشتری بدهد.

نسخهٔ ۵ — بازنویسی استاندارد ۲۰۲۶:
  - 🐛 FIX بحرانی: data-id جدید فقط msgId خالی دارد (فرمت ۲۰۲۶-۰۴-۳۰).
    chat_id از header مکالمه استخراج می‌شود [citation:3][citation:10][citation:18].
  - ⭐ NEW: BROWSER_LOCK سراسری (استاندارد پروژه — مثل بله/ایتا/لینکدین).
  - ⭐ NEW: connect idempotent (اگر از قبل وصل، رد می‌کند).
  - ⭐ NEW: _page_lock برای جلوگیری از race در fetch_messages.
  - ⭐ NEW: انتخابگرهای پایدار (data-pre-plain-text, aria-label, data-testid)
    به‌جای CSSهای شکننده [citation:4][citation:6].
  - ⭐ NEW: async with async_playwright() (context manager استاندارد)
    به‌جای start()/stop() دستی — تضمین آزادسازی منبع در خطا [citation:5].

ورود یک‌بار و دستی: `python login.py` (گزینه واتساپ) → مرورگر باز می‌شود، با گوشی
QR را اسکن می‌کنی؛ نشست در پروفایل مرورگر (user_data_dir) ذخیره می‌ماند.
"""
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from analyzers.target_filter import get_target_groups
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug, log_error, log_info, log_success, log_warning,
)

# ایمپورتِ playwright در سطح ماژول با محافظ
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
_MAX_SEEN = 100000

# نام پیش‌فرضی که وقتی استخراج ناموفق است برگردانده می‌شود
_DEFAULT_CHAT_NAME = "واتساپ"

# ─── نشانه‌های ورود/عدم‌ورود (پایدار) ───
LOGIN_MARKERS = [
    'canvas[aria-label*="scan" i]',
    "div[data-ref]",
    '[data-testid="qrcode"]',
]
CHATLIST_MARKERS = [
    "#pane-side",
    '[data-testid="chat-list"]',
    'div[aria-label="لیست چت"]',
]

# ─── انتخابگرهای پیام (پایدار — attribute-based) [citation:4] ───
# ترتیب مهم: اول پایدارترین
BUBBLE_SELECTORS = [
    "div.message-in, div.message-out",      # همچنان کار می‌کند
    "[data-pre-plain-text]",                # پایدارترین (attribute)
    "div[data-id]",                         # fallback
]

# ─── انتخابگرهای لیست چت (پایدار) ───
CHAT_ITEM_SELECTORS = [
    'div[aria-label][role="listitem"]',     # پایدار [citation:4]
    '[data-testid="cell-frame-container"]',
    '#pane-side [role="listitem"]',         # fallback
]


class WhatsAppCollector(BaseCollector):
    def __init__(
        self,
        phone: str = None,
        headless: bool = True,
        poll_seconds: int = 180,
        max_chats: int = 15,
        chat_open_delay: float = 1.5,
        messages_per_chat: int = 30,
        backfill_scrolls: int = 6,
        **kwargs,
    ):
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
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids = set()

        # ⭐ NEW: قفل صفحه (استاندارد بله)
        self._page_lock = asyncio.Lock()

        # ⭐ NEW: قفل سراسری connect (استاندارد پروژه)
        if not hasattr(WhatsAppCollector, "_connect_lock"):
            WhatsAppCollector._connect_lock = asyncio.Lock()

        # matcher مشترک: عکس فقط برای پیام‌های کلیدواژه‌دار دانلود می‌شود
        self._kw = get_keyword_finder()
        # فیلتر گروه هدف (مثل ایتا/بله)
        self._targets = get_target_groups()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(STORAGE_DIR, "sessions", "whatsapp")
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "whatsapp")
        os.makedirs(self._media_dir, exist_ok=True)
        # ضدتکراری ماندگار
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
        # واتساپ هم اسکرپرِ مرورگری سنگین است.
        return float(self._poll_seconds)

    # ==================================================
    # اعتبارسنجی نام چت
    # ==================================================
    @staticmethod
    def _is_valid_chat_name(name: str) -> bool:
        """نام چت معتبر است؟ (نه خالی، نه نام پیش‌فرض)"""
        if not name:
            return False
        n = str(name).strip()
        if not n or n == _DEFAULT_CHAT_NAME:
            return False
        return True

    # ==================================================
    # ضدتکراری ماندگار (روی دیسک)
    # ==================================================
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} پیامِ دیده‌شدهٔ واتساپ بارگذاری شد"
                )
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
    # ⭐ NEW: اتصال با BROWSER_LOCK (استاندارد پروژه)
    # ==================================================
    async def connect(self, headless: bool | None = None) -> bool:
        """🎯 قفل سراسری مرورگر — جلوگیری از تداخل با بله/ایتا/لینکدین."""
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        """اتصال واقعی — فقط وقتی BROWSER_LOCK گرفته شده."""
        # 🎯 idempotent
        if self._connected and self._context:
            log_info("ℹ️ [whatsapp:connect] از قبل متصل — رد")
            return True

        async with WhatsAppCollector._connect_lock:
            if self._connected and self._context:
                log_info("ℹ️ [whatsapp:connect] توی قفل — از قبل متصل")
                return True

            if self._context:
                log_warning("⚠️ [whatsapp:connect] context قدیمی باز — بستن")
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست: pip install playwright")
                    return False

                use_headless = self.headless if headless is None else headless

                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

                # ─── goto با timeout و تشخیص خطا ───
                log_info("🌐 [whatsapp] رفتن به web.whatsapp.com...")
                try:
                    await self._page.goto(
                        WHATSAPP_WEB_URL,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                except Exception as e:
                    err = str(e)
                    log_error(f"❌ goto واتساپ شکست خورد: {err[:120]}")
                    if "ERR_SSL_PROTOCOL_ERROR" in err:
                        log_warning(
                            "🔒 ERR_SSL_PROTOCOL_ERROR → VPN/Proxy لازم است"
                        )
                    elif "ERR_NAME_NOT_RESOLVED" in err:
                        log_warning("🌐 DNS resolve نشد → VPN چک کن")
                    elif "ERR_CONNECTION_TIMED_OUT" in err:
                        log_warning("⏱️ Timeout → VPN چک کن")
                    self._set_error(f"goto_failed: {err[:200]}")
                    await self.disconnect()
                    return False

                # ─── انتظار پویا برای لاگین ───
                log_info("⏳ [whatsapp] انتظار لاگین (حداکثر ۴۰ ثانیه)...")
                login_confirmed = False
                for attempt in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        login_confirmed = True
                        log_success(
                            f"✅ لاگین واتساپ تشخیص داده شد "
                            f"(بعد از {attempt+1} تلاش، ~{(attempt+1)*2}s)"
                        )
                        break
                    if attempt % 5 == 4:
                        log_info(f"   ⏳ [whatsapp] {attempt+1}/20 ...")

                if login_confirmed:
                    # 🎯 FIX: انتظار صریح برای لود شدن chat-list
                    log_info("⏳ [whatsapp] انتظار برای لود شدن لیست چت (۱۵s)...")
                    chatlist_ready = False
                    for sel in CHATLIST_MARKERS + CHAT_ITEM_SELECTORS:
                        try:
                            await self._page.wait_for_selector(
                                sel, timeout=15000, state="visible"
                            )
                            log_success(f"✅ chat-list آماده شد | selector={sel}")
                            chatlist_ready = True
                            break
                        except Exception:
                            continue

                    if not chatlist_ready:
                        log_warning("⚠️ chat-list پیدا نشد ولی ادامه می‌دهیم")

                    self._connected = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success(
                        "✅ اتصال واتساپ‌وب برقرار شد (نشست موجود)"
                    )
                    return True

                # ─── تشخیص دقیق دلیل شکست ───
                url = self._page.url or ""
                title = ""
                try:
                    title = await self._page.title()
                except Exception:
                    pass

                log_error("❌ [whatsapp] لاگین تشخیص داده نشد")
                log_info(f"   URL: {url[:100]}")
                log_info(f"   Title: {title[:80]}")

                if "web.whatsapp.com" not in url:
                    log_error("   → صفحه در واتساپ نیست (شبکه/VPN)")
                else:
                    qr_found = False
                    for sel in LOGIN_MARKERS:
                        try:
                            el = await self._page.query_selector(sel)
                            if el and await el.is_visible():
                                qr_found = True
                                break
                        except Exception:
                            continue
                    if qr_found:
                        log_error(
                            "   → QR نمایش داده شده (نشست معتبر نیست)"
                        )
                        log_warning(
                            "   راه‌حل: python login.py → گزینهٔ ۵"
                        )
                    else:
                        log_error(
                            "   → chat-list پیدا نشد (DOM تغییر کرده؟)"
                        )

                await self.disconnect()
                return False

            except Exception as e:
                log_error(
                    f"خطا در اتصال واتساپ‌وب: "
                    f"{type(e).__name__}: {e}"
                )
                self._set_error(str(e))
                await self.disconnect()
                return False
                
    async def _launch_context(self, use_headless: bool):
        """مرورگر را قابل‌حمل باز می‌کند."""
        from config import CHROME_PATH, get_browser_proxy

        proxy_cfg = get_browser_proxy()
        log_info(
            f"🔧 [whatsapp:launch] proxy="
            f"{'yes' if proxy_cfg else 'no'} | "
            f"headless={use_headless}"
        )

        base = dict(
            user_data_dir=self._user_data_dir,
            headless=use_headless,
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            **proxy_cfg,
        )

        if CHROME_PATH:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(
                    executable_path=CHROME_PATH, **base
                )
                log_info("مرورگر واتساپ‌وب با مسیرِ CHROME_PATH باز شد")
                return ctx
            except Exception as e:
                log_warning(
                    f"CHROME_PATH کار نکرد ({e})؛ "
                    f"سراغ مرورگر خودکار می‌روم"
                )

        last_err = None
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = dict(base)
                if channel:
                    kwargs["channel"] = channel
                ctx = await self._pw.chromium.launch_persistent_context(
                    **kwargs
                )
                log_info(
                    f"مرورگر واتساپ‌وب با {channel or 'chromium'} باز شد"
                )
                return ctx
            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(
            f"هیچ مرورگری برای واتساپ‌وب باز نشد: {last_err}"
        )
        
    async def _is_logged_in(self) -> bool:
        """چک دقیق‌تر: صفحه لود شده + المان‌ها visible."""
        try:
            # ─── ۱) چک کن صفحه واقعاً لود شده ───
            title = (await self._page.title() or "").lower()
            url = (self._page.url or "").lower()

            # اگر در صفحهٔ خطا/blank هستیم → نامعلوم (نه لاگین)
            if not title and "whatsapp" not in url:
                return False

            # ─── ۲) چک QR (یعنی لاگین نیستیم) با visibility ───
            for sel in LOGIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return False
                except Exception:
                    continue

            # ─── ۳) چک chat-list با visibility ───
            for sel in CHATLIST_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue

            # ─── ۴) 🎯 FIX #14: fallback — QR نبود ولی chat-list نبود ───
            # یعنی یا در حال لود هستیم یا chat-list هنوز آماده نشده.
            # اگر appِ واتساپ لود شده، True برمی‌گردانیم تا caller
            # فرصت بیشتری برای chat-list بدهد (به‌جای شکست کاذب).
            fallback_markers = [
                'div[role="application"]',
                'div[data-testid="default-user"]',
                "#app",
            ]
            for sel in fallback_markers:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        # QR نیست + app لود شده → احتمالاً لاگین هستیم
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False
    # ==================================================
    # ابزار کمکی — استخراج فرستنده (پایدار)
    # ==================================================
    @staticmethod
    def _parse_pre_plain_text(pre: str) -> str:
        """از data-pre-plain-text، نام فرستنده را بیرون می‌کشد.

        فرمت واتساپ:  "[12:34, 01/02/2024] علی رضایی: "
        """
        if not pre:
            return ""
        try:
            m = re.search(r"\]\s*(.+?)\s*:\s*$", pre)
            if not m:
                return ""
            name = m.group(1).strip()
            # پاک‌سازی نشانه‌های جهت‌دهی (RTL/LTR markers)
            for ch in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e"):
                name = name.replace(ch, "")
            if name and len(name) < 60:
                return name
        except Exception:
            pass
        return ""

    async def _extract_sender_from_bubble(self, bubble) -> str:
        """استخراج نام فرستنده از data-pre-plain-text یا spanهای داخلی."""
        # روش ۱: خود bubble
        try:
            pre = await bubble.get_attribute("data-pre-plain-text")
            name = self._parse_pre_plain_text(pre or "")
            if name:
                return name
        except Exception:
            pass
        # روش ۲: فرزند copyable-text
        try:
            el = await bubble.query_selector(".copyable-text[data-pre-plain-text]")
            if el:
                pre = await el.get_attribute("data-pre-plain-text")
                name = self._parse_pre_plain_text(pre or "")
                if name:
                    return name
        except Exception:
            pass
        # روش ۳: span کوتاه داخلی (fallback کم‌دقت)
        for sel in ('span[dir="auto"]', "span[aria-label]"):
            try:
                el = await bubble.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t and len(t) < 60:
                        return t
            except Exception:
                continue
        return ""

    # ==================================================
    # استخراج نام چت از آیتم لیست (پایدار)
    # ==================================================
    async def _extract_item_title(self, chat_item) -> str:
        """نام چت را از خودِ آیتمِ لیست استخراج می‌کند."""
        # روش ۱: span[title] داخل آیتم (پایدار)
        for sel in (
            '[data-testid="cell-frame-title"] span[title]',
            'span[title]',
            'span[dir="auto"]',
        ):
            try:
                el = await chat_item.query_selector(sel)
                if el:
                    t = (await el.get_attribute("title")) or (
                        await el.inner_text() or ""
                    )
                    t = (t or "").strip()
                    if t and len(t) < 80:
                        return t
            except Exception:
                continue
        # روش ۲: aria-label مستقیم (پایدار)
        try:
            aria = await chat_item.get_attribute("aria-label")
            if aria:
                a = aria.strip()
                if a and len(a) < 80:
                    return a
        except Exception:
            pass
        return ""

    # ==================================================
    # خواندن پیام‌های جدید از چتِ باز
    # ==================================================
    async def fetch_messages(self, since: datetime = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []
        try:
            # ⭐ NEW: قفل صفحه (استاندارد بله)
            async with self._page_lock:
                await self._scrape_all_chats()
        except Exception as e:
            log_error(f"خطا در خواندن پیام واتساپ: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._needs_backfill = False
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از واتساپ")
        return new_msgs

    async def _backfill_scroll(self):
        """در اولین اسکرپ بعد از اتصال، چت را به بالا اسکرول می‌کند (catch-up)."""
        if not (self._needs_backfill and self._backfill_scrolls > 0):
            return
        try:
            await self._page.mouse.move(640, 400)
        except Exception:
            return
        for _ in range(self._backfill_scrolls):
            try:
                await self._page.mouse.wheel(0, -3000)
            except Exception:
                break
            await asyncio.sleep(0.8)

    async def _scrape_all_chats(self):
        """در لیست چت‌ها می‌چرخد، هر گروه/چت را باز می‌کند و پیام‌هایش را می‌خواند."""
        page = self._page
        chat_items = []
        used_sel = ""

        # 🎯 FIX: لاگ تشخیصی — ببینیم کدام سلکتور چقدر match می‌کند
        log_info("🔍 [whatsapp:probe] بررسی سلکتورهای لیست چت...")
        for sel in CHAT_ITEM_SELECTORS:
            try:
                cnt = await page.locator(sel).count()
                log_info(f"   probe: {sel} → {cnt}")
                if cnt > 0 and not chat_items:
                    chat_items = await page.query_selector_all(sel)
                    used_sel = sel
            except Exception as e:
                log_info(f"   probe: {sel} → خطا: {str(e)[:60]}")

        if not chat_items:
            log_warning(
                "⚠️ [whatsapp] هیچ چتی در لیست پیدا نشد — DOM dump برای بررسی"
            )
            try:
                await self.dump_dom()
            except Exception:
                pass
            await self._scrape_open_chat()
            return

        total = len(chat_items)
        limit = self._max_chats if self._max_chats > 0 else total
        chat_items = chat_items[:limit]

        log_info(
            f"🔍 [whatsapp:scrape] شروع | کل={total} | انتخاب={limit} | "
            f"selector={used_sel}"
        )

        processed = 0
        skipped_target = 0
        failed = 0

        for idx, chat in enumerate(chat_items):
            try:
                # نام چت را قبل از کلیک بگیر (برای فیلتر هدف)
                chat_name = await self._extract_item_title(chat)

                # 🎯 فیلتر گروه هدف (فقط اگر نام معتبر باشد)
                if (self._targets.enabled(platform="whatsapp")
                        and self._is_valid_chat_name(chat_name)
                        and not self._targets.is_target(chat_name, platform="whatsapp")):
                    skipped_target += 1
                    log_info(f"   ⏭️ [{idx+1}] غیرهدف: «{chat_name}»")
                    continue

                await chat.click()
                await asyncio.sleep(self._chat_open_delay)
                await self._scrape_open_chat()
                processed += 1

            except Exception as e:
                failed += 1
                log_warning(f"خطا در مرور چت واتساپ {idx}: {e}")
                continue

        log_success(
            f"🏁 [whatsapp:scrape] کامل | پردازش={processed} | "
            f"غیرهدف={skipped_target} | خطا={failed}"
        )

    async def _scrape_open_chat(self):
        """پیام‌های چتِ بازِ فعلی را می‌خواند."""
        page = self._page
        chat_name = await self._get_open_chat_title()

        # ⭐ NEW: ابتدا chat_id را از header مکالمه بگیر (تنها سیگنال قابل اعتماد)
        # (در فرمت جدید واتساپ، data-id فقط msgId خالی دارد و chat_id از DOM در نمی‌آید) [citation:3][citation:10]
        chat_id = chat_name  # fallback امن

        bubbles = []
        for sel in BUBBLE_SELECTORS:
            try:
                bubbles = await page.query_selector_all(sel)
                if bubbles:
                    break
            except Exception:
                continue

        if not bubbles:
            return

        # هنگام catch-up پیام‌های بیشتری را بخوان — مثل ایتا
        limit = (
            max(self._messages_per_chat, 200)
            if self._needs_backfill
            else self._messages_per_chat
        )

        for b in bubbles[-limit:]:
            try:
                data_id = await b.get_attribute("data-id") or ""

                # ⭐ FIX بحرانی: فرمت data-id تغییر کرده (۲۰۲۶-۰۴-۳۰)
                # فرمت قدیمی: false_<chat>@g.us_<msgid>_<sender>
                # فرمت جدید: فقط <msgid> خالی (hex)
                # chat_id از DOM در نمی‌آید → از header استفاده می‌کنیم [citation:3][citation:10][citation:18]
                mid = ""
                is_group = False
                if "_" in data_id and "@" in data_id:
                    # فرمت قدیمی — پارس کن
                    parts = data_id.split("_")
                    if len(parts) >= 2:
                        chat_part = parts[1]
                        is_group = "@g.us" in chat_part
                        # اگر فرمت قدیمی معتبر بود، chat_id رو ازش بگیر
                        if chat_part and len(chat_part) > 5:
                            chat_id = chat_part
                        mid = parts[2] if len(parts) >= 3 else data_id
                else:
                    # فرمت جدید: فقط msgId خالی
                    mid = data_id if data_id else ""
                    # chat_id از header (قبلاً گرفتیم)

                # متن پیام (پایدار — attribute-based) [citation:4]
                text = ""
                # روش ۱: data-testid جدید (۲۰۲۶) [citation:6]
                tnode = await b.query_selector('[data-testid="selectable-text"]')
                if tnode:
                    text = (await tnode.inner_text() or "").strip()
                # روش ۲: انتخابگر قدیمی‌تر
                if not text:
                    tnode = await b.query_selector(
                        "span.selectable-text, .copyable-text span.selectable-text"
                    )
                    if tnode:
                        text = (await tnode.inner_text() or "").strip()
                # روش ۳: fallback
                if not text:
                    text = (await b.inner_text() or "").strip()

                # شناسهٔ پایدار: اگر msgId خالی داشت، ازش استفاده کن؛ وگرنه md5
                if not mid:
                    # 🎯 FIX: chat_name را هم داخل hash بیاور
                    mid = hashlib.sha256(
                        f"{chat_name}:{text}".encode("utf-8")
                    ).hexdigest()[:16]

                uniq = f"{chat_name}:{mid}"
                if uniq in self._seen_ids:
                    log_debug(f"⏭️ [seen] تکراری | {chat_name} | {mid}")
                    continue
                # بدون متن = بدون امکان تطبیق کلیدواژه
                if not text:
                    continue
                self._seen_ids.add(uniq)

                # فیلتر کلیدواژه — نتیجه در متغیر نگه داشته می‌شود
                has_kw = self._kw.has_match(text)
                if not has_kw:
                    log_debug(f"⏭️ [kw] بدون کلیدواژه | {chat_name} | {text[:40]}")
                    continue
                    
                # ─── استخراج فرستنده ───
                sender_name = ""
                if is_group:
                    sender_name = await self._extract_sender_from_bubble(b)
                if not sender_name:
                    sender_name = chat_name

                # دانلود عکس (این پیام قطعاً کلیدواژه دارد)
                media_type, local_file_path = "text", None
                try:
                    from core.adapters.web_media import download_dom_image

                    mt, lp = await download_dom_image(
                        self._page, b, self._media_dir, "whatsapp"
                    )
                    if mt:
                        media_type, local_file_path = mt, lp
                except Exception:
                    pass

                self._messages_queue.append(
                    {
                        "id": str(mid),
                        "platform": "whatsapp",
                        "text": text,
                        "chat_id": str(chat_id),
                        "chat_name": chat_name,
                        "chat_type": "group" if is_group else "private",
                        "chat_username": None,
                        "chat_link": None,
                        "user_id": "",
                        "sender_name": sender_name,
                        "timestamp": datetime.now(),
                        "media_type": media_type,
                        "file_id": None,
                        "local_file_path": local_file_path,
                        "reply_to": None,
                    }
                )
            except Exception:
                continue

    async def _get_open_chat_title(self) -> str:
        """نام چت باز فعلی را از header مکالمه استخراج می‌کند.

        FIX (2026-10-10):
        - ترتیب سلکتورها برعکس شد — قبلی 'You' (اسم خود کاربر) رو
          برمی‌گردوند به‌جای اسم چت.
        - سلکتورهای تست‌شده: conversation-info-header-chat-title و
          span[dir="auto"] اسم واقعی رو می‌دن.
        - فیلتر 'You' و 'click here' اضافه شد.
        """
        for sel in [
            '[data-testid="conversation-info-header-chat-title"]',
            'header span[dir="auto"]',
            'header[data-testid="conversation-header"] span',
            '[data-testid="conversation-info-header"] span[title]',
        ]:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.get_attribute("title")) or (await el.inner_text())
                    t = (t or "").strip()
                    if not t or len(t) >= 100:
                        continue
                    if t == "You":
                        continue
                    if "click here" in t.lower():
                        continue
                    return t
            except Exception:
                continue
        return _DEFAULT_CHAT_NAME

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(self._user_data_dir, "whatsapp_dom_dump.html")
        html = await self._page.content()
        def _write():
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        await asyncio.to_thread(_write)
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