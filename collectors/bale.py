# collectors/bale.py
"""
کلکتور بله (Bale) — کاربر عادی روی web.bale.ai

نسخهٔ ۸ (فقط‌خواندن):
  - 🎯 _launch_context استاندارد (channel یا executable_path)
  - 🎯 cleanup خودکار قفل‌های پروفایل
  - 🎯 recovery خودکار در صورت «profile in use»
  - 🎯 گارد گروه هدف
  - 🎯 استخراج sender_id/sender_name در چت گروهی
  - 🎯 نگاشت پایدار peer_id → name
  - ❌ send_message حذف شد — ربات API جداگانه ارسال می‌کند
  - ❌ چک «چت مدیر» حذف شد — همه چت‌ها اسکرپ می‌شوند
  - ❌ تب دوم ارسال حذف شد — فقط یک تب (سریع‌تر)
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from analyzers.target_filter import get_target_groups
from core.adapters import messenger_registry
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import log_error, log_info, log_success, log_warning

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# ─── ثابت‌ها ───
_MAX_SEEN = 8000
BALE_WEB_URL = "https://web.bale.ai/"
_MAX_CHAT_NAME_LEN = 40
_PLATFORM = "bale"

LOGIN_MARKERS = [
    'input[type="tel"]',
    'input[type="number"]',
    'input[placeholder*="شماره"]',
    'input[placeholder*="phone"]',
]

LOGGEDIN_MARKERS = [
    'div[contenteditable="true"]',
    '.composer',
    '.input-message-input',
    '.new-message-wrapper',
    '[class*="chatlist"]',
    '[class*="chat-list"]',
    '[class*="dialog"]',
]

CHAT_SELECTORS = [
    ".chatlist-chat",
    ".chat-item",
    ".dialog-item",
    "[class*='dialog'] > *",
    "[class*='chatlist'] > *",
    "[class*='chat-list'] > *",
    "[data-peer-id]",
    "[role='listitem']",
]

COMPOSER_SELECTORS = [
    'div[contenteditable="true"]',
    '.composer textarea',
    '.input-message-input',
    '.new-message-wrapper textarea',
    'textarea.chat-input',
]

MSG_SELECTORS = [
    ".bubbles .bubble",
    ".bubble",
    ".message-item",
    ".message",
    "[data-mid]",
]

TITLE_SELECTORS = [
    "bdi.oUKPfP",
    "bdi",
    ".peer-title",
    ".chat-title",
    ".dialog-title",
]

SENDER_SELECTORS = [
    ".peer-title",
    ".message-title",
    ".sender-title",
    ".sender-name",
    ".user-title",
]

_BAD_NAME_PATTERNS = [
    r"http", r"www\.", r"\.com", r"\.ir",
    r"^\d", r"^\s*\d{1,2}:\d{2}",
    r"[!؟?!]{2,}",
    r"^\s*[\.\-–—]\s*",
    r"^[a-z]{5,}$",
]
_BAD_NAME_WORDS = [
    "شما", "خدمت", "منو", "سرویس", "انتخاب", "بازگشت",
    "برای", "استفاده", "امن", "آدرس", "کنین",
    "ورود", "جدید", "حساب", "کاربری", "تاریخ",
    "سلام", "خوش", "آمدید", "متن", "پیام",
]

# قفل‌های پروفایل Chrome
_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


class BaleCollector(BaseCollector):
    def __init__(
        self,
        phone: str = None,
        password: str = None,
        headless: bool = True,
        poll_seconds: int = 60,
        max_chats: int = 20,
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
        self._send_page = None
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids = set()

        self._targets = get_target_groups()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()
        # ── قفل سراسری برای ارسال (نگه داشته شد، ولی استفاده نمی‌شود) ──
        if not hasattr(BaleCollector, "_send_lock"):
            BaleCollector._send_lock = asyncio.Lock()

        # ── قفل سراسری برای connect (جلوگیری از race) ──
        if not hasattr(BaleCollector, "_connect_lock"):
            BaleCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "bale_web"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)

        self._media_dir = os.path.join(STORAGE_DIR, "media", "bale")
        os.makedirs(self._media_dir, exist_ok=True)

        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "bale_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        self._private_chats_file = os.path.join(
            STORAGE_DIR, "offset", "bale_private_chats.json"
        )
        os.makedirs(
            os.path.dirname(self._private_chats_file), exist_ok=True
        )
        self._private_chat_names: list[str] = []
        self._chat_id_map: dict[str, str] = {}
        self._load_private_chats()

    # ═══════════════════════════════════════════════════
    # private chats cache
    # ═══════════════════════════════════════════════════
    def _is_valid_chat_name(self, name: str) -> bool:
        if not name:
            return False
        n = str(name).strip()
        if not n or n == "بله":
            return False
        if len(n) > _MAX_CHAT_NAME_LEN:
            return False
        if n.isdigit():
            return False
        for pat in _BAD_NAME_PATTERNS:
            if re.search(pat, n, re.IGNORECASE):
                return False
        for w in _BAD_NAME_WORDS:
            if w in n:
                return False
        if n.count(" ") > 3:
            return False
        return True

    def _load_private_chats(self):
        try:
            self._private_chat_names = []
            self._chat_id_map = {}
            if not os.path.exists(self._private_chats_file):
                return
            with open(self._private_chats_file, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return
            raw_names = list(d.get("names") or [])
            self._private_chat_names = [
                n for n in raw_names if self._is_valid_chat_name(n)
            ][-30:]
            raw_map = d.get("map") or {}
            if isinstance(raw_map, dict):
                for pid, name in raw_map.items():
                    if self._is_valid_chat_name(name):
                        self._chat_id_map[str(pid)] = str(name)
            log_info(
                f"♻️ cache بله: {len(self._private_chat_names)} نام، "
                f"{len(self._chat_id_map)} نگاشت"
            )
        except Exception as e:
            log_warning(f"بارگذاری cache ناموفق: {e}")

    def _save_private_chats(self):
        try:
            valid_map = {
                pid: name
                for pid, name in self._chat_id_map.items()
                if self._is_valid_chat_name(name)
            }
            if len(valid_map) > 100:
                valid_map = dict(list(valid_map.items())[-100:])
            with open(self._private_chats_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "names": self._private_chat_names[-30:],
                        "map": valid_map,
                    },
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            log_warning(f"ذخیره cache ناموفق: {e}")

    def _remember_private_chat(self, chat_name: str):
        if not self._is_valid_chat_name(chat_name):
            return
        name = str(chat_name).strip()
        try:
            if name in self._private_chat_names:
                self._private_chat_names.remove(name)
            self._private_chat_names.append(name)
            self._private_chat_names = self._private_chat_names[-30:]
        except Exception:
            pass

    def _remember_chat_mapping(self, peer_id: str, chat_name: str):
        if not peer_id or not self._is_valid_chat_name(chat_name):
            return
        peer_id = str(peer_id).strip()
        chat_name = str(chat_name).strip()
        if peer_id in self._chat_id_map:
            return
        self._chat_id_map[peer_id] = chat_name

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
    # ضدتکراری
    # ═══════════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ بله "
                    f"بارگذاری شد"
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
            log_warning(f"ذخیرهٔ ضدتکراری ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        # 🎯 اگر از قبل وصل هستیم، رد کن
        if self._connected and self._context:
            log_info("ℹ️ [bale:connect] از قبل متصل — connect تکراری رد شد")
            return True

        # 🎯 قفل: فقط یکی در آنِ واحد connect بزند
        async with BaleCollector._connect_lock:
            if self._connected and self._context:
                log_info("ℹ️ [bale:connect] توی قفل — از قبل متصل")
                return True

            if self._context:
                log_warning("⚠️ [bale:connect] context قدیمی باز — بستن")
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None
                self._send_page = None

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست")
                    return False

                use_headless = (
                    self.headless if headless is None else headless
                )
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)

                # ── فقط یک تب برای خواندن پیام‌ها ──
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
                await self._page.goto(
                    BALE_WEB_URL, wait_until="domcontentloaded"
                )

                # 🎯 تب دوم حذف شد — فقط‌خواندن
                self._send_page = None

                for i in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._needs_backfill = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(
                            f"✅ اتصال بله‌وب برقرار شد — "
                            f"بعد از {i+1} تلاش (فقط‌خواندن)"
                        )
                        # 🎯 register_sender غیرفعال شد
                        log_info(
                            "ℹ️ [bale:connect] send_message غیرفعال — "
                            "ربات API جداگانه ارسال می‌کند"
                        )
                        return True

                await self.dump_dom()
                log_error("نشست بله‌وب تشخیص داده نشد.")
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال بله‌وب: {e}")
                self._set_error(str(e))
                return False

    # ═══════════════════════════════════════════════════
    # 🎯 launch context — استاندارد
    # ═══════════════════════════════════════════════════
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
            except Exception:
                pass

    def _force_kill_chrome(self):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH, get_browser_proxy

        base_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
        ]
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )

        await self._cleanup_profile_locks()

        launch_options: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "viewport": {"width": 1366, "height": 768},
            "user_agent": ua,
            "args": base_args,
            **get_browser_proxy(),
        }

        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_options["executable_path"] = CHROME_PATH
            source = f"CHROME_PATH={CHROME_PATH}"
        else:
            launch_options["channel"] = "chrome"
            source = "channel=chrome"

        try:
            ctx = await self._pw.chromium.launch_persistent_context(
                **launch_options
            )
            log_info(f"مرورگر بله‌وب باز شد | منبع: {source}")
            return ctx
        except Exception as e:
            err_msg = str(e)
            if (
                "already in use" in err_msg
                or "existing browser" in err_msg
                or "profile is already" in err_msg
            ):
                log_warning(
                    "⚠️ پروفایل قفل شده — پاک‌سازی و تلاش مجدد"
                )
                self._force_kill_chrome()
                await asyncio.sleep(2)
                await self._cleanup_profile_locks()
                ctx = await self._pw.chromium.launch_persistent_context(
                    **launch_options
                )
                log_info(
                    f"مرورگر بله‌وب باز شد | منبع: {source} "
                    f"(بعد از پاک‌سازی)"
                )
                return ctx
            raise RuntimeError(
                f"هیچ مرورگری برای بله‌وب باز نشد: {e}"
            )

    async def _is_logged_in(self) -> bool:
        try:
            url = (self._page.url or "").strip()
            low = url.lower()
            if any(
                x in low
                for x in ("/login", "/auth", "/signin", "/verify")
            ):
                return False
            if "bale.ai" not in low:
                return False
            for sel in LOGIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return False
                except Exception:
                    continue
            for sel in LOGGEDIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el:
                        try:
                            if await el.is_visible():
                                return True
                        except Exception:
                            return True
                except Exception:
                    continue
            return True
        except Exception:
            return False

    # ═══════════════════════════════════════════════════
    # ابزار کمکی
    # ═══════════════════════════════════════════════════
    async def _extract_peer_id_from_item(self, chat_item) -> str:
        for attr in (
            "data-peer-id", "data-id", "data-peer", "data-chat-id"
        ):
            try:
                val = await chat_item.get_attribute(attr)
                if val:
                    return str(val)
            except Exception:
                continue
        return ""

    async def _get_current_peer_id(self) -> str:
        try:
            url = self._page.url or ""
            for pattern in (
                r"chat/(\d+)", r"peer/(\d+)", r"@([A-Za-z0-9_]+)"
            ):
                m = re.search(pattern, url)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""

    async def _detect_chat_type(self) -> str:
        """
        تشخیص نوع چت بله — فقط در چت باز فعلی (header).
        
        ترتیب:
          ۱) نشانه‌های گروه (اول)
          ۲) آیکن‌های چت (Tv/Bot/Group) — فقط در header
          ۳) پیش‌فرض: private
        """
        try:
            # ═══ مرحله ۱: نشانه‌های گروه ═══
            group_markers = [
                "button[aria-label*='اعضا']",
                "button[aria-label*='Members']",
                "button[aria-label*='عضو']",
                "[class*='member-count']",
                "[class*='subscriber']",
                "[class*='group-info']",
                "[class*='groupMembers']",
            ]
            for sel in group_markers:
                try:
                    el = await self._page.query_selector(sel)
                    if el:
                        return "group"
                except Exception:
                    continue

            # ═══ مرحله ۲: آیکن‌ها — فقط در header چت باز ═══
            header = None
            for header_sel in (
                '[class*="chat-header"]',
                '[class*="conversation-header"]',
                '[class*="topbar"]',
                '[data-testid="chat-header"]',
                '[class*="header"]',
            ):
                try:
                    header = await self._page.query_selector(header_sel)
                    if header:
                        break
                except Exception:
                    continue

            if header:
                icon_checks = [
                    ('svg[aria-label="Tv-icon"]', "channel"),
                    ('svg[aria-label="Bot-icon"]', "bot"),
                    ('svg[aria-label="Group-icon"]', "group"),
                ]
                for sel, ctype in icon_checks:
                    try:
                        el = await header.query_selector(sel)
                        if el:
                            return ctype
                    except Exception:
                        continue

            # ═══ پیش‌فرض: private ═══
            return "private"

        except Exception as e:
            log_warning(
                f"⚠️ [bale:detect_chat_type] "
                f"{type(e).__name__}: {e} — پیش‌فرض private"
            )
            return "private"
            
    async def _extract_sender_from_bubble(
        self, msg
    ) -> tuple[str, str]:
        sender_id, sender_name = "", ""
        for attr in (
            "data-peer-id", "data-user-id", "data-sender-id",
            "data-author", "data-from-id",
        ):
            try:
                v = await msg.get_attribute(attr)
                if v and str(v).strip():
                    sender_id = str(v).strip()
                    break
            except Exception:
                continue
        for sel in SENDER_SELECTORS:
            try:
                el = await msg.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if first and len(first) < 60:
                            sender_name = first
                            break
            except Exception:
                continue
        return sender_id, sender_name

    # ═══════════════════════════════════════════════════
    # دریافت پیام
    # ═══════════════════════════════════════════════════
    async def fetch_messages(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []
        try:
            async with self._page_lock:
                await self._scrape_all_chats()
        except Exception as e:
            log_error(f"خطا در خواندن پیام بله: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._save_private_chats()
        self._needs_backfill = False
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از بله")
        return new_msgs

    async def _backfill_scroll(self):
        if not (
            self._needs_backfill and self._backfill_scrolls > 0
        ):
            return
        for _ in range(self._backfill_scrolls):
            try:
                await self._page.evaluate(
                    "() => { const c = document.querySelector("
                    "'.bubbles, .scrollable, .messages-container, "
                    "#column-center .scrollable'); "
                    "if (c) c.scrollTop = Math.max(0, c.scrollTop - 2500); }"
                )
            except Exception:
                break
            await asyncio.sleep(0.8)

    async def _find_chat_items(self):
        for sel in CHAT_SELECTORS:
            try:
                items = await self._page.query_selector_all(sel)
                visible = []
                for it in items:
                    try:
                        if await it.is_visible():
                            visible.append(it)
                    except Exception:
                        visible.append(it)
                if visible:
                    log_info(
                        f"✅ [بله] لیست چت با `{sel}` پیدا شد "
                        f"({len(visible)} آیتم)"
                    )
                    return visible, sel
            except Exception:
                continue
        return [], ""

    async def _should_skip_chat(
        self, chat, chat_name: str, idx: int
    ) -> bool:
        # 🎯 فقط بات‌ها رد می‌شوند — چت مدیر دیگر skip نمی‌شود
        try:
            bot_icon = await chat.query_selector(
                'svg[aria-label="Bot-icon"]'
            )
            if bot_icon:
                log_info(f"   ⏭️ [{idx+1}] بات — رد شد")
                return True
        except Exception:
            pass

        # گارد گروه هدف
        if self._is_valid_chat_name(chat_name):
            try:
                if (
                    self._targets.enabled(_PLATFORM)
                    and not self._targets.is_target(
                        chat_name, platform=_PLATFORM
                    )
                ):
                    log_info(
                        f"   ⏭️ [{idx+1}] غیرهدف: «{chat_name}»"
                    )
                    return True
            except Exception as e:
                log_warning(
                    f"   ⚠️ [bale:target] خطا | "
                    f"{type(e).__name__}: {e} — پیش‌فرض: قبول"
                )
        return False

    async def _scrape_all_chats(self):
        try:
            url = (self._page.url or "").lower()
            if "/search" in url:
                await self._page.goto(
                    BALE_WEB_URL, wait_until="domcontentloaded"
                )
                await asyncio.sleep(2)
        except Exception:
            pass

        for _ in range(8):
            try:
                await self._page.mouse.wheel(0, 1500)
            except Exception:
                pass
            await asyncio.sleep(0.6)

        chat_items, _ = await self._find_chat_items()
        if not chat_items:
            log_warning("⚠️ هیچ چتی در لیست بله پیدا نشد")
            try:
                await self.dump_dom()
            except Exception:
                pass
            return

        log_info(f"🔍 {len(chat_items)} چت برای اسکرپ")

        processed = 0
        skipped_target = 0
        skipped_bot = 0
        failed = 0

        chats_to_process = (
            chat_items
            if self._max_chats <= 0
            else chat_items[: self._max_chats]
        )
        for idx, chat in enumerate(chats_to_process):
            try:
                chat_name = await self._extract_item_title(chat)

                # 🎯 skip چت‌های با پیام قدیمی
                try:
                    preview = (await chat.inner_text() or "").strip()
                    old_markers = ("دیروز", "هفته", "ماه", "سال", "روز پیش")
                    if any(m in preview for m in old_markers):
                        skipped_old = getattr(self, "_skipped_old", 0) + 1
                        self._skipped_old = skipped_old
                        continue
                except Exception:
                    pass

                if await self._should_skip_chat(chat, chat_name, idx):
                    try:
                        if await chat.query_selector(
                            'svg[aria-label="Bot-icon"]'
                        ):
                            skipped_bot += 1
                        else:
                            skipped_target += 1
                    except Exception:
                        skipped_target += 1
                    continue

                peer_id = await self._extract_peer_id_from_item(chat)

                try:
                    await self._page.evaluate(
                        "(el) => el.click()", chat
                    )
                except Exception:
                    failed += 1
                    continue

                await asyncio.sleep(self._chat_open_delay)
                await self._scrape_current_chat(chat_name, peer_id)
                processed += 1

                try:
                    await self._page.mouse.wheel(0, -200)
                except Exception:
                    pass
            except Exception as e:
                failed += 1
                log_warning(f"خطا در اسکرپ چت {idx}: {e}")
                continue

        log_success(
            f"🏁 [bale:scrape] کامل | پردازش={processed} | "
            f"غیرهدف={skipped_target} | بات={skipped_bot} | "
            f"قدیمی={getattr(self, '_skipped_old', 0)} | "
            f"خطا={failed}"
        )
        self._skipped_old = 0

    async def _extract_item_title(self, chat_item) -> str:
        for sel in TITLE_SELECTORS:
            try:
                el = await chat_item.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if self._is_valid_chat_name(first):
                            return first
            except Exception:
                continue
        try:
            for attr in ("data-peer-id", "data-name"):
                v = await chat_item.get_attribute(attr)
                if v and self._is_valid_chat_name(str(v)):
                    return str(v)
        except Exception:
            pass
        return "بله"

    async def _scrape_current_chat(
        self, chat_name: str = None, peer_id: str = None
    ):
        page = self._page
        if not chat_name:
            chat_name = await self._get_open_chat_title()
        if not chat_name or chat_name == "بله":
            return

        # 🐛 FIX: تشخیص درست chat_type
        chat_type = await self._detect_chat_type()
        if not peer_id:
            peer_id = await self._get_current_peer_id()
        chat_id = peer_id or chat_name

        log_info(
            f"📖 اسکرپ چت بله: {chat_name} "
            f"(نوع: {chat_type}، id: {chat_id})"
        )

        if chat_type == "private":
            self._remember_private_chat(chat_name)
            if peer_id:
                self._remember_chat_mapping(peer_id, chat_name)

        await self._backfill_scroll()

        messages = []
        for sel in MSG_SELECTORS:
            try:
                messages = await page.query_selector_all(sel)
                if messages:
                    log_info(
                        f"✅ پیام‌ها با {sel} پیدا شد "
                        f"({len(messages)})"
                    )
                    break
            except Exception:
                continue

        if not messages:
            return

        limit = (
            max(self._messages_per_chat, 200)
            if self._needs_backfill
            else self._messages_per_chat
        )
        count = 0
        for msg in messages[-limit:]:
            try:
                mid = (
                    await msg.get_attribute("data-mid")
                ) or (
                    await msg.get_attribute("data-msgid")
                )
                text = (await msg.inner_text() or "").strip()

                if not mid and text:
                    mid = hashlib.md5(
                        text.encode("utf-8")
                    ).hexdigest()[:16]
                if not mid or not text:
                    continue

                uniq = f"{chat_id}:{mid}"
                if uniq in self._seen_ids:
                    continue
                self._seen_ids.add(uniq)

                if (
                    chat_type != "private"
                    and not self._kw.has_match(text)
                ):
                    continue

                sender_id, sender_name = "", ""
                if chat_type == "private":
                    sender_id = peer_id or ""
                    sender_name = chat_name
                else:
                    sender_id, sender_name = (
                        await self._extract_sender_from_bubble(msg)
                    )
                    if not sender_name:
                        sender_name = chat_name

                media_type, local_file_path = "text", None
                if (
                    chat_type == "group"
                    and self._kw.has_match(text)
                ):
                    try:
                        from core.adapters.web_media import (
                            download_dom_image,
                        )
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
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "chat_type": chat_type,
                    "chat_username": None,
                    "chat_link": None,
                    "user_id": sender_id,
                    "sender_name": sender_name,
                    "timestamp": datetime.now(),
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_file_path,
                    "reply_to": None,
                })
                count += 1
            except Exception:
                continue

        if count > 0:
            log_success(f"📩 از چت {chat_name}: {count} پیام جدید")

    async def _get_open_chat_title(self) -> str:
        for sel in TITLE_SELECTORS:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if self._is_valid_chat_name(first):
                            return first
            except Exception:
                continue
        return "بله"

    # ═══════════════════════════════════════════════════
    # ارسال پیام — غیرفعال
    # ═══════════════════════════════════════════════════
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        """🎯 غیرفعال — پایشگر فقط می‌خواند؛ ارسال با ربات API."""
        log_info(
            f"⏭️ [bale:send] غیرفعال (فقط‌خواندن) | "
            f"chat_id={chat_id}"
        )
        return False

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "bale_dom_dump.html"
        )
        try:
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            log_info(f"DOM بله در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

    async def disconnect(self) -> None:
        try:
            self._save_private_chats()
        except Exception:
            pass
        try:
            if self._context:
                await self._context.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از بله‌وب")

    async def check_health(self) -> bool:
        return self._connected