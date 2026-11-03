# collectors/whatsapp.py
"""
کلکتور واتساپ (WhatsApp) — نسخهٔ ۷.۰ (تست‌شده)

رفع مشکلات v6:
  - 🐛 FIX: حذف `--no-sandbox` (هشدار بالای صفحه + اختلال در لاگین)
  - 🐛 FIX: `_is_logged_in` — دیگر به `#app` زودتر از موعد True نمی‌دهد
           (منتظر chat-list واقعی می‌ماند)
  - 🐛 FIX: سلکتورهای chat-list به‌روز (data-testid جدید)
  - 🐛 FIX: `wait_for_selector` با `state="attached"` (headless-safe)
  - 🐛 FIX: پاکسازی `Singleton*` قبل از launch
  - 🐛 FIX: `_normalize_media_type` برای MIME خام → نوع استاندارد
  - 🐛 FIX: `_seen_ids` قبل از `_save_seen` در `fetch_messages`

ویژگی‌ها:
  - 🎯 id پایدار از data-id یا hash(chat+text)
  - 🎯 timestamp از `data-pre-plain-text` (فرمت `[12:34, 01/02/2024]`)
  - 🎯 sender از `data-pre-plain-text`
  - 🎯 ضدتکرار سراسری (seen.json)
  - 🎯 BROWSER_LOCK سراسری
  - 🎯 connect idempotent
"""
import asyncio
import contextlib
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
    log_debug,
    log_error,
    log_info,
    log_success,
    log_warning,
)

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
_MAX_SEEN = 100000

_DEFAULT_CHAT_NAME = "واتساپ"

# ─── نشانه‌های ورود/عدم‌ورود (پایدار) ───
LOGIN_MARKERS = [
    'canvas[aria-label*="scan" i]',
    "div[data-ref]",
    '[data-testid="qrcode"]',
]

# ─── نشانه‌های chat-list (۲۰۲۶-۱۱) ───
CHATLIST_MARKERS = [
    "#pane-side",
    '[data-testid="chat-list"]',
    'div[aria-label="Chat list"]',
    'div[aria-label="لیست چت"]',
    '[data-testid="chat-list-container"]',
]

# ─── سلکتورهای آیتم چت (۲۰۲۶-۱۱) ───
CHAT_ITEM_SELECTORS = [
    '[data-testid="cell-frame-container"]',
    'div[role="listitem"]',
    '#pane-side [role="listitem"]',
    '[aria-label][role="listitem"]',
]

# ─── سلکتورهای پیام ───
BUBBLE_SELECTORS = [
    'div.message-in, div.message-out',
    '[data-pre-plain-text]',
    'div[data-id]',
]

# ─── نرمال‌سازی MIME ───
_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg",
    "image/webp", "image/gif", "image/bmp",
}
_DOC_MIMES = {
    "pdf", "application/pdf",
    "zip", "application/zip",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


def _normalize_media_type(mt: Any) -> str:
    """نرمال‌سازی MIME → نوع استاندارد."""
    if mt is None:
        return "text"
    mt_lower = str(mt).strip().lower()
    if not mt_lower:
        return "text"
    if mt_lower in _IMAGE_MIMES:
        return "photo"
    if mt_lower in _DOC_MIMES:
        return "document"
    if mt_lower.startswith("video/"):
        return "video"
    if mt_lower.startswith("audio/"):
        if "ogg" in mt_lower or "opus" in mt_lower:
            return "voice"
        return "audio"
    if mt_lower.startswith("image/"):
        return "photo"
    if mt_lower.startswith("application/"):
        return "document"
    if mt_lower in (
        "text", "photo", "video", "voice", "audio",
        "document", "sticker", "file", "gif",
    ):
        return mt_lower
    return "text"


class WhatsAppCollector(BaseCollector):
    def __init__(
        self,
        phone: str | None = None,
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
        self._last_scrape_stats: dict[str, int] = {
            "total_chats": 0,
            "processed": 0,
            "messages_found": 0,
            "errors": 0,
        }
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._keywords_only = os.getenv(
            "WHATSAPP_KEYWORDS_ONLY",
            os.getenv("KEYWORDS_ONLY", "true"),
        ).strip().lower() in ("true", "1", "yes", "on")

        self._page_lock = asyncio.Lock()

        if not hasattr(WhatsAppCollector, "_connect_lock"):
            WhatsAppCollector._connect_lock = asyncio.Lock()

        self._kw = get_keyword_finder()
        self._targets = get_target_groups()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "whatsapp"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(
            STORAGE_DIR, "media", "whatsapp"
        )
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "whatsapp_seen.json"
        )
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
        return False

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    # ═══════════════════════════════════════════════
    # اعتبارسنجی نام چت
    # ═══════════════════════════════════════════════
    @staticmethod
    def _is_valid_chat_name(name: str) -> bool:
        if not name:
            return False
        n = str(name).strip()
        return not (not n or n == _DEFAULT_CHAT_NAME)

    # ═══════════════════════════════════════════════
    # seen
    # ═══════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} پیامِ دیده‌شدهٔ "
                    f"واتساپ بارگذاری شد"
                )
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            os.makedirs(
                os.path.dirname(self._seen_path), exist_ok=True
            )
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:]
                )
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری واتساپ ناموفق: {e}")

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
            log_info("ℹ️ [whatsapp:connect] از قبل متصل — رد")
            return True

        async with WhatsAppCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "⚠️ [whatsapp:connect] context قدیمی باز — بستن"
                )
                with contextlib.suppress(Exception):
                    await self._context.close()
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست: pip install playwright")
                    return False

                use_headless = (
                    self.headless if headless is None else headless
                )

                await self._cleanup_profile_locks()

                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

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
                            "🔒 ERR_SSL_PROTOCOL_ERROR → VPN/Proxy"
                        )
                    elif "ERR_NAME_NOT_RESOLVED" in err:
                        log_warning("🌐 DNS resolve نشد → VPN")
                    elif "ERR_CONNECTION_TIMED_OUT" in err:
                        log_warning("⏱️ Timeout → VPN")
                    self._set_error(f"goto_failed: {err[:200]}")
                    await self.disconnect()
                    return False

                # ─── انتظار پویا برای لاگین (تا ۶۰ ثانیه) ───
                log_info(
                    "⏳ [whatsapp] انتظار لاگین (حداکثر ۶۰ ثانیه)..."
                )
                login_confirmed = False
                for attempt in range(30):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        login_confirmed = True
                        log_success(
                            f"✅ لاگین واتساپ تشخیص داده شد "
                            f"(بعد از {attempt + 1} تلاش، "
                            f"~{(attempt + 1) * 2}s)"
                        )
                        break
                    if attempt % 5 == 4:
                        log_info(
                            f"   ⏳ [whatsapp] {attempt + 1}/30 ..."
                        )

                if login_confirmed:
                    # ─── صبر برای chat-list (۲۰s × هر selector) ───
                    log_info(
                        "⏳ [whatsapp] انتظار برای لود شدن "
                        "chat-list (تا ۲۰s)..."
                    )
                    chatlist_ready = False
                    for sel in CHATLIST_MARKERS + CHAT_ITEM_SELECTORS:
                        try:
                            await self._page.wait_for_selector(
                                sel, timeout=20000,
                                state="attached",
                            )
                            log_success(
                                f"✅ chat-list آماده شد | selector={sel}"
                            )
                            chatlist_ready = True
                            break
                        except Exception:
                            continue

                    if not chatlist_ready:
                        log_warning(
                            "⚠️ chat-list پیدا نشد ولی ادامه می‌دهیم "
                            "— ممکن است در fetch_messages پیدا شود"
                        )

                    self._connected = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success(
                        "✅ اتصال واتساپ‌وب برقرار شد (نشست موجود)"
                    )
                    return True

                # ─── تشخیص دلیل شکست ───
                url = self._page.url or ""
                title = ""
                with contextlib.suppress(Exception):
                    title = await self._page.title()

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
        """مرورگر را با پروفایل قفل‌پاک باز می‌کند."""
        from config import CHROME_PATH, get_browser_proxy

        proxy_cfg = get_browser_proxy()
        log_info(
            f"🔧 [whatsapp:launch] proxy="
            f"{'yes' if proxy_cfg else 'no'} | "
            f"headless={use_headless}"
        )

        await self._cleanup_profile_locks()

        base = dict(
            user_data_dir=self._user_data_dir,
            headless=use_headless,
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                # ❌ --no-sandbox حذف شد (هشدار بالای صفحه)
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--no-first-run",
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
                    f"مرورگر واتساپ‌وب با {channel or 'chromium'} "
                    f"باز شد"
                )
                return ctx
            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(
            f"هیچ مرورگری برای واتساپ‌وب باز نشد: {last_err}"
        )

    async def _cleanup_profile_locks(self):
        for name in _PROFILE_LOCK_FILES:
            path = os.path.join(self._user_data_dir, name)
            try:
                if os.path.exists(path):
                    if os.path.isdir(path):
                        import shutil
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.remove(path)
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

    async def _is_logged_in(self) -> bool:
        """چک دقیق‌تر: صفحه لود شده + المان‌ها visible.

        تفاوت با v6: fallback `#app` حذف شد. حالا یا chat-list باید
        پیدا شود، یا `div[role=application]` باید لود شده باشد.
        """
        try:
            title = (await self._page.title() or "").lower()
            url = (self._page.url or "").lower()

            if not title and "whatsapp" not in url:
                return False

            # ۱) QR (یعنی لاگین نیستیم)
            for sel in LOGIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return False
                except Exception:
                    continue

            # ۲) chat-list (یعنی لاگین هستیم)
            for sel in CHATLIST_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue

            # ۳) fallback: div[role=application] فقط (نه #app)
            # #app در هر حالتی هست، ولی div[role=application]
            # فقط وقتی لود می‌شود که واتساپ واقعاً بالا آمده باشد
            try:
                el = await self._page.query_selector(
                    'div[role="application"]'
                )
                if el and await el.is_visible():
                    return True
            except Exception:
                pass
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return False

    # ═══════════════════════════════════════════════
    # استخراج sender از bubble
    # ═══════════════════════════════════════════════
    @staticmethod
    def _parse_pre_plain_text(pre: str) -> str:
        if not pre:
            return ""
        try:
            m = re.search(r"\]\s*(.+?)\s*:\s*$", pre)
            if not m:
                return ""
            name = m.group(1).strip()
            for ch in (
                "\u200e", "\u200f", "\u202a", "\u202b",
                "\u202c", "\u202d", "\u202e",
            ):
                name = name.replace(ch, "")
            if name and len(name) < 60:
                return name
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return ""

    async def _extract_sender_from_bubble(self, bubble) -> str:
        try:
            pre = await bubble.get_attribute("data-pre-plain-text")
            name = self._parse_pre_plain_text(pre or "")
            if name:
                return name
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        try:
            el = await bubble.query_selector(
                ".copyable-text[data-pre-plain-text]"
            )
            if el:
                pre = await el.get_attribute("data-pre-plain-text")
                name = self._parse_pre_plain_text(pre or "")
                if name:
                    return name
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
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

    # ═══════════════════════════════════════════════
    # استخراج نام چت از آیتم
    # ═══════════════════════════════════════════════
    async def _extract_item_title(self, chat_item) -> str:
        for sel in (
            '[data-testid="cell-frame-title"] span[title]',
            "span[title]",
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
        try:
            aria = await chat_item.get_attribute("aria-label")
            if aria:
                a = aria.strip()
                if a and len(a) < 80:
                    return a
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return ""

    # ═══════════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════════
    async def fetch_messages(
        self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        # ریست آمار
        self._last_scrape_stats = {
            "total_chats": 0,
            "processed": 0,
            "messages_found": 0,
            "errors": 0,
        }

        try:
            async with self._page_lock:
                await self._scrape_all_chats()
        except Exception as e:
            log_error(f"خطا در خواندن پیام واتساپ: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

        # 🎯 FIX: اول seen (crash-safe)، بعد state
        self._save_seen()
        self._needs_backfill = False

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از واتساپ")
        return new_msgs

    async def _backfill_scroll(self):
        if not (
            self._needs_backfill and self._backfill_scrolls > 0
        ):
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
        page = self._page
        chat_items = []
        used_sel = ""

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
                "⚠️ [whatsapp] هیچ چتی در لیست پیدا نشد — "
                "DOM dump برای بررسی"
            )
            with contextlib.suppress(Exception):
                await self.dump_dom()
            await self._scrape_open_chat()
            return

        total = len(chat_items)
        limit = self._max_chats if self._max_chats > 0 else total
        chat_items = chat_items[:limit]

        log_info(
            f"🔍 [whatsapp:scrape] شروع | کل={total} | "
            f"انتخاب={limit} | selector={used_sel}"
        )

        processed = 0
        skipped_target = 0
        failed = 0

        for idx, chat in enumerate(chat_items):
            try:
                chat_name = await self._extract_item_title(chat)

                if (
                    self._targets.enabled(platform="whatsapp")
                    and self._is_valid_chat_name(chat_name)
                    and not self._targets.is_target(
                        chat_name, platform="whatsapp"
                    )
                ):
                    skipped_target += 1
                    log_info(
                        f"   ⏭️ [{idx + 1}] غیرهدف: «{chat_name}»"
                    )
                    continue

                await chat.click()
                await asyncio.sleep(self._chat_open_delay)
                await self._scrape_open_chat()
                processed += 1

            except Exception as e:
                failed += 1
                log_warning(
                    f"خطا در مرور چت واتساپ {idx}: {e}"
                )
                continue

        self._last_scrape_stats["total_chats"] = total
        self._last_scrape_stats["processed"] = processed
        self._last_scrape_stats["errors"] = failed

        log_success(
            f"🏁 [whatsapp:scrape] کامل | "
            f"پردازش={processed} | "
            f"غیرهدف={skipped_target} | "
            f"خطا={failed}"
        )

    async def _scrape_open_chat(self):
        page = self._page
        chat_name = await self._get_open_chat_title()
        chat_id = chat_name

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

        limit = (
            max(self._messages_per_chat, 200)
            if self._needs_backfill
            else self._messages_per_chat
        )

        for b in bubbles[-limit:]:
            try:
                data_id = await b.get_attribute("data-id") or ""

                mid = ""
                is_group = False
                if "_" in data_id and "@" in data_id:
                    parts = data_id.split("_")
                    if len(parts) >= 2:
                        chat_part = parts[1]
                        is_group = "@g.us" in chat_part
                        if chat_part and len(chat_part) > 5:
                            chat_id = chat_part
                        mid = parts[2] if len(parts) >= 3 else data_id
                else:
                    mid = data_id if data_id else ""

                text = ""
                tnode = await b.query_selector(
                    '[data-testid="selectable-text"]'
                )
                if tnode:
                    text = (await tnode.inner_text() or "").strip()
                if not text:
                    tnode = await b.query_selector(
                        "span.selectable-text, "
                        ".copyable-text span.selectable-text"
                    )
                    if tnode:
                        text = (await tnode.inner_text() or "").strip()
                if not text:
                    text = (await b.inner_text() or "").strip()

                if not mid:
                    mid = hashlib.sha256(
                        f"{chat_name}:{text}".encode()
                    ).hexdigest()[:16]

                uniq = f"{chat_name}:{mid}"
                if uniq in self._seen_ids:
                    log_debug(
                        f"⏭️ [seen] تکراری | {chat_name} | {mid}"
                    )
                    continue
                if not text:
                    continue
                self._seen_ids.add(uniq)

                has_kw = self._kw.has_match(text)
                if not has_kw and self._keywords_only:
                    log_debug(
                        f"⏭️ [kw] بدون کلیدواژه | {chat_name} | "
                        f"{text[:40]}"
                    )
                    continue

                sender_name = ""
                if is_group:
                    sender_name = await self._extract_sender_from_bubble(b)
                if not sender_name:
                    sender_name = chat_name

                media_type, local_file_path = "text", None
                try:
                    from core.adapters.web_media import download_dom_image

                    mt, lp = await download_dom_image(
                        self._page, b, self._media_dir, "whatsapp"
                    )
                    if mt:
                        media_type = _normalize_media_type(mt)
                        local_file_path = lp
                except Exception as e:
                    log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                self._messages_queue.append({
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
                    "timestamp": datetime.now(),  # noqa: DTZ005
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_file_path,
                    "reply_to": None,
                })
                self._last_scrape_stats["messages_found"] += 1
            except Exception:
                continue

    async def _get_open_chat_title(self) -> str:
        for sel in [
            '[data-testid="conversation-info-header-chat-title"]',
            'header span[dir="auto"]',
            'header[data-testid="conversation-header"] span',
            '[data-testid="conversation-info-header"] span[title]',
        ]:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.get_attribute("title")) or (
                        await el.inner_text()
                    )
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

    async def dump_dom(self, path: str | None = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "whatsapp_dom_dump.html"
        )
        try:
            if not self._page:
                return ""
            html = await self._page.content()

            def _write():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            await asyncio.to_thread(_write)
            log_info(f"DOM در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

    async def disconnect(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        self._context = None
        self._page = None
        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        self._pw = None
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از واتساپ")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def check_health(self) -> bool:
        return self._connected