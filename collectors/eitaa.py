# collectors/eitaa.py
"""
کلکتور ایتا (Eitaa) در حالت «کاربر عادی» با اتوماسیون مرورگر روی web.eitaa.com

نسخهٔ ۸ (برگشت به منطق v3 + سازگاری کامل با ساختار جدید):
  - 🔄 منطق اصلی = نسخهٔ قدیم (v3) که کار می‌کرد
  - 🔴 FIX import paths به ساختار جدید:
      core.base_collector → core.contracts.base_collector
      core.logger         → core.kernel.logger
      core.web_media      → core.adapters.web_media
  - 🎯 NEW: import download_dom_image به بالای فایل (نه داخل تابع)
  - ✅ حفظ: ضدتکراری ماندگار روی دیسک
  - ✅ حفظ: backfill scroll (catch-up)
  - ✅ حفظ: گارد گروه هدف (با پارامتر platform="eitaa")
  - ✅ حفظ: md5 برای id پایدار (نه hash تصادفی)
  - ⚡ بهینه: sender extraction فقط برای گروه (اختیاری)

چرا مرورگر؟ ایتا هیچ کتابخانه‌ی userbot بومیِ سالمی ندارد.
تنها راه واقعی «به‌عنوان کاربر» = ورود به web.eitaa.com با شماره.

ورود یک‌بار و دستی: `python login.py` (گزینه ایتا).
"""
import asyncio
import hashlib
import json
import os
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from analyzers.target_filter import get_target_groups
from core.adapters.web_media import download_dom_image
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug, log_error, log_info, log_success, log_warning,
)

# ایمپورتِ playwright در سطح ماژول با محافظ
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# ─── ثابت‌ها ───
_MAX_SEEN = 100000
EITAA_WEB_URL = "https://web.eitaa.com/"
_PLATFORM = "eitaa"

# ─── انتخابگرهای DOM ───
LOGIN_MARKERS = [
    'input[type="tel"]',
    ".input-field-phone",
    'input.input-field-input[type="tel"]',
]
CHATLIST_MARKERS = [
    ".chatlist-chat",
    ".chatlist",
    "#column-left",
    ".chatlist-container",
    ".sidebar-left",
    ".im_dialogs_col",
]


class EitaaCollector(BaseCollector):
    def __init__(
        self,
        phone: str = None,
        password: str = None,
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
        self.password = password
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
        self._processed_chats = set()
        self._targets = get_target_groups()
        self._kw = get_keyword_finder()

        # 🎯 قفل سراسری connect — جلوگیری از race
        if not hasattr(EitaaCollector, "_connect_lock"):
            EitaaCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "eitaa_web"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(
            STORAGE_DIR, "media", "eitaa"
        )
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "eitaa_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

    # ═══════════════════════════════════════════════════
    # خواص
    # ═══════════════════════════════════════════════════
    @property
    def platform_name(self) -> str:
        return _PLATFORM

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    # ═══════════════════════════════════════════════════
    # ضدتکراری ماندگار (روی دیسک)
    # ═══════════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} شناسهٔ پیامِ دیده‌شدهٔ ایتا "
                    f"بارگذاری شد"
                )
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:]
                )
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری ایتا ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        """🎯 قفل سراسری مرورگر — جلوگیری از تداخل."""
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        # 🎯 اگر از قبل وصل هستیم، رد کن
        if self._connected and self._context:
            log_info("ℹ️ [eitaa:connect] از قبل متصل — connect تکراری رد شد")
            return True

        async with EitaaCollector._connect_lock:
            # چک دوباره داخل قفل
            if self._connected and self._context:
                log_info("ℹ️ [eitaa:connect] توی قفل — از قبل متصل")
                return True

            # اگر context قبلی مرده، پاکش کن
            if self._context:
                log_warning("⚠️ [eitaa:connect] context قدیمی باز — بستن")
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error(
                        "playwright نصب نیست: pip install playwright "
                        "&& playwright install chromium"
                    )
                    return False

                use_headless = (
                    self.headless if headless is None else headless
                )
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
                await self._page.goto(
                    EITAA_WEB_URL, wait_until="domcontentloaded"
                )

                for _ in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._needs_backfill = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(
                            "✅ اتصال ایتا‌وب برقرار شد (نشست موجود)"
                        )
                        return True

                await self.dump_dom()
                log_error(
                    "نشست ایتا‌وب تشخیص داده نشد. یک‌بار "
                    "`python login.py` (گزینه ایتا) را اجرا کن "
                    "و در مرورگر با شماره و کد پیامکی وارد شو."
                )
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال ایتا‌وب: {e}")
                self._set_error(str(e))
                # 🎯 پاک‌سازی context تا Chrome قفل نشود
                try:
                    if self._context:
                        await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None
                try:
                    if self._pw:
                        await self._pw.stop()
                except Exception:
                    pass
                self._pw = None
                self._connected = False
                return False
                
    async def _launch_context(self, use_headless: bool):
        last_err = None
        for channel in ("chrome", "msedge"):
            try:
                kwargs = dict(
                    user_data_dir=self._user_data_dir,
                    headless=use_headless,
                    viewport={"width": 1366, "height": 768},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
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
                ctx = await self._pw.chromium.launch_persistent_context(
                    **kwargs
                )
                log_info(
                    f"مرورگر ایتا‌وب با {channel or 'chromium'} باز شد"
                )
                return ctx
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(
            f"هیچ مرورگری برای ایتا‌وب باز نشد: {last_err}"
        )

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

    # ═══════════════════════════════════════════════════
    # دریافت پیام‌ها
    # ═══════════════════════════════════════════════════
    async def fetch_messages(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []
        try:
            await self._scrape_all_chats()
        except Exception as e:
            log_error(f"خطا در خواندن پیام ایتا: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._needs_backfill = False
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از ایتا")
        return new_msgs

    async def _backfill_scroll(self):
        """در اولین اسکرپ بعد از اتصال، چت را به بالا اسکرول می‌کند (catch-up)."""
        if not (
            self._needs_backfill and self._backfill_scrolls > 0
        ):
            return
        for _ in range(self._backfill_scrolls):
            try:
                await self._page.evaluate(
                    "() => { const c = document.querySelector("
                    "'.bubbles, .scrollable-y, "
                    "#column-center .scrollable'); "
                    "if (c) c.scrollTop = Math.max(0, c.scrollTop - 2500); }"
                )
            except Exception:
                break
            await asyncio.sleep(0.8)

    # ═══════════════════════════════════════════════════
    # اسکرپ همه چت‌ها (منطق v3 — کار می‌کرد)
    # ═══════════════════════════════════════════════════
    async def _scrape_all_chats(self):
        """اسکرپ پیام‌های همه چت‌های موجود در لیست."""
        page = self._page

        # انتخابگر دقیق چت‌ها از DOM ایتا
        chat_selectors = [
            ".chatlist-chat",
            ".rp.chatlist-chat",
            "li.chatlist-chat",
        ]

        used_sel = None
        total_chats = 0
        for sel in chat_selectors:
            items = await page.query_selector_all(sel)
            if items:
                used_sel = sel
                total_chats = len(items)
                log_info(
                    f"✅ انتخابگر {sel} کار کرد و "
                    f"{total_chats} چت پیدا شد"
                )
                break

        if not used_sel or not total_chats:
            log_warning("هیچ چتی در لیست پیدا نشد")
            try:
                await self.dump_dom()
            except Exception:
                pass
            await self._scrape_current_chat()
            return

        limit = (
            total_chats
            if self._max_chats <= 0
            else min(total_chats, self._max_chats)
        )
        log_info(f"🔍 {limit} چت برای اسکرپ پیدا شد")

        for idx in range(limit):
            try:
                # 🐛 FIX 1: چک کن idx معتبر است (SPA ممکن است تغییر کند)
                cnt = await page.locator(used_sel).count()
                if idx >= cnt:
                    log_info(f"   ⏭️ [ایتا] چت {idx} وجود ندارد (total={cnt}) — توقف")
                    break

                # 🎯 هر بار locator جدید (نه element قدیمی)
                locator = page.locator(used_sel).nth(idx)

                # ─── نام چت ───
                try:
                    chat_name = await locator.locator(
                        ".peer-title, .user-title, .dialog-title"
                    ).first.inner_text()
                    chat_name = (chat_name or "").strip()
                    # 🎯 فقط خط اول
                    if chat_name:
                        chat_name = chat_name.split("\n")[0].strip()
                    if not chat_name:
                        chat_name = "ایتا"
                except Exception:
                    chat_name = "ایتا"

                # ─── گارد گروه هدف ───
                if (
                    self._targets.enabled(_PLATFORM)
                    and not self._targets.is_target(
                        chat_name, platform=_PLATFORM
                    )
                ):
                    log_info(
                        f"   ⏭️ [{idx+1}] غیرهدف: «{chat_name}»"
                    )
                    continue

                # ─── کلیک با locator (خودش re-find می‌کند) ───
                try:
                    await locator.click(timeout=5000)
                except Exception as e:
                    log_warning(
                        f"خطا در کلیک چت {idx} «{chat_name}»: {e}"
                    )
                    continue

                await asyncio.sleep(self._chat_open_delay)

                # ─── اسکرپ چت ───
                await self._scrape_current_chat(chat_name)

            except Exception as e:
                log_warning(f"خطا در اسکرپ چت {idx}: {e}")
                continue
                
    async def _extract_item_title(self, chat_item) -> str:
        """نام چت را از خودِ آیتمِ لیست استخراج می‌کند."""
        for sel in [".peer-title", ".user-title", ".dialog-title"]:
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

    # ═══════════════════════════════════════════════════
    # اسکرپ چت باز فعلی (منطق v3)
    # ═══════════════════════════════════════════════════
    async def _scrape_current_chat(self, chat_name: str = None):
        """اسکرپ پیام‌های چت باز فعلی."""
        page = self._page
        if not chat_name:
            chat_name = await self._get_open_chat_title()

        if chat_name == "ایتا" or not chat_name:
            log_warning(f"نام چت نامشخص: {chat_name}")
            return

        log_info(f"📖 اسکرپ چت: {chat_name}")

        # catch-up
        await self._backfill_scroll()

        # ⚠️ پیام‌های «چتِ باز» را بخوان، نه پیش‌نمایشِ لیست کناری.
        msg_selectors = [
            ".bubbles .bubble",
            "#column-center .bubble",
            ".bubble",
            "[data-mid]",
            ".im_history_message_wrap",
            ".message-list .message",
            ".user-last-message",  # fallback آخر
        ]

        messages = []
        used_selector = None
        for sel in msg_selectors:
            try:
                messages = await page.query_selector_all(sel)
                if messages:
                    used_selector = sel
                    log_info(
                        f"✅ پیام‌ها با انتخابگر {sel} پیدا شدند "
                        f"({len(messages)} پیام)"
                    )
                    break
            except Exception:
                continue

        if not messages:
            log_warning(
                f"⚠️ هیچ پیامی در چت {chat_name} پیدا نشد"
            )
            try:
                all_text = await page.inner_text(".chat")
                log_info(f"📄 متن چت: {all_text[:200]}...")
            except Exception:
                pass
            return

        # هنگام catch-up پیام‌های بیشتری بخوان
        limit = (
            max(self._messages_per_chat, 200)
            if self._needs_backfill
            else self._messages_per_chat
        )
        msg_count = 0
        for msg in messages[-limit:]:
            try:
                mid = (
                    await msg.get_attribute("data-mid")
                ) or (
                    await msg.get_attribute("data-msgid")
                )
                text = (await msg.inner_text() or "").strip()

                # شناسهٔ پایدار با md5
                if not mid and text:
                    # 🎯 FIX: chat_name را هم داخل hash بیاور
                    mid = hashlib.md5(
                        f"{chat_name}:{text}".encode("utf-8")
                    ).hexdigest()[:16]
                if not mid or not text:
                    continue

                uniq = f"{chat_name}:{mid}"
                if uniq in self._seen_ids:
                    log_debug(f"⏭️ [seen] تکراری | {chat_name} | {mid}")
                    continue

                self._seen_ids.add(uniq)

                # فیلتر کلیدواژه
                if not self._kw.has_match(text):
                    log_debug(f"⏭️ [kw] بدون کلیدواژه | {chat_name} | {text[:40]}")
                    continue

                # دانلود عکس پیام (این پیام کلیدواژه دارد)
                media_type, local_file_path = "text", None
                try:
                    mt, lp = await download_dom_image(
                        self._page, msg, self._media_dir, _PLATFORM
                    )
                    if mt:
                        media_type, local_file_path = mt, lp
                except Exception:
                    pass

                self._messages_queue.append({
                    "id": str(mid),
                    "platform": _PLATFORM,
                    "text": text,
                    "chat_id": chat_name,
                    "chat_name": chat_name,
                    "chat_type": "group",  # ← طبق v3 (هاردکد)
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
                log_info(
                    f"📩 پیام {msg_count} از {chat_name}: "
                    f"{text[:50]}..."
                )
            except Exception as e:
                log_warning(f"خطا در پردازش پیام: {e}")
                continue

        if msg_count > 0:
            log_success(
                f"📩 از چت {chat_name}: {msg_count} پیام جدید"
            )
        else:
            log_warning(
                f"⚠️ هیچ پیام جدیدی در چت {chat_name} پیدا نشد"
            )

    # ═══════════════════════════════════════════════════
    # دریافت نام چت باز
    # ═══════════════════════════════════════════════════
    async def _get_open_chat_title(self) -> str:
        """دریافت نام چت باز فعلی — فقط خط اول."""
        for sel in [
            ".peer-title",
            ".dialog-title .peer-title",
            ".user-title .peer-title",
            ".chat-info .title",
            ".topbar .user-title",
            ".im_header_peer_name",
        ]:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        # 🎯 فقط خط اول
                        first_line = t.split("\n")[0].strip()
                        if first_line:
                            return first_line
            except Exception:
                continue
        return "ایتا"
        
    # ═══════════════════════════════════════════════════
    # ابزار عیب‌یابی
    # ═══════════════════════════════════════════════════
    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "eitaa_dom_dump.html"
        )
        html = await self._page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        file_size = os.path.getsize(path)
        log_info(
            f"DOM در {path} ذخیره شد (حجم: {file_size} بایت)"
        )
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