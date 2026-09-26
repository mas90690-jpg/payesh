# collectors/bale.py
"""
کلکتور بله (Bale) — کاربر عادی روی web.bale.ai

نسخهٔ ۷ (استاندارد + دقیق):
  - 🎯 _launch_context استاندارد با تصمیم‌گیری صریح (channel یا executable_path)
  - 🎯 cleanup خودکار قفل‌های پروفایل قبل از launch
  - 🎯 recovery خودکار در صورت «profile in use»
  - 🎯 حذف کامل CHROME_PATH از fallback زنجیره‌ای
  - 🎯 ترتیب درست ارسال: اول اسم، بعد URL عددی
  - 🎯 گارد گروه هدف (مثل ایتا)
  - 🎯 استخراج sender_id/sender_name در چت گروهی
  - 🎯 نگاشت پایدار peer_id → name
  - 🎯 استخراج chat_username/chat_link
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
        # ── قفل سراسری برای ارسال (جلوگیری از تداخل بین تسک‌ها) ──
        if not hasattr(BaleCollector, "_send_lock"):
            BaleCollector._send_lock = asyncio.Lock()

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
        # ── فقط اگه چت خصوصی و معتبر بود ──
        # (از آلودگی نگاشت با چت‌های غیرمرتبط جلوگیری می‌کنه)
        if peer_id in self._chat_id_map:
            # اگه قبلاً ذخیره شده، بازنویسی نکن
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
        try:
            if async_playwright is None:
                log_error("playwright نصب نیست")
                return False

            use_headless = (
                self.headless if headless is None else headless
            )
            self._pw = await async_playwright().start()
            self._context = await self._launch_context(use_headless)

            # ── تب اصلی برای خواندن پیام‌ها ──
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
            await self._page.goto(
                BALE_WEB_URL, wait_until="domcontentloaded"
            )

            # ── تب دوم برای ارسال پیام (جدا از تب خواندن) ──
            self._send_page = await self._context.new_page()
            await self._send_page.goto(
                BALE_WEB_URL, wait_until="domcontentloaded"
            )

            for i in range(20):
                await asyncio.sleep(2)
                if await self._is_logged_in():
                    self._connected = True
                    self._needs_backfill = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    log_success(
                        f"✅ اتصال بله‌وب برقرار شد — "
                        f"بعد از {i+1} تلاش"
                    )
                    messenger_registry.register_sender(
                        _PLATFORM, self.send_message
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
        """پاک کردن فایل‌های قفل پروفایل."""
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
        """کشتن پروسه‌های Chrome."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    async def _launch_context(self, use_headless: bool):
        """باز کردن context با تصمیم‌گیری صریح (فقط یک منبع)."""
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
        try:
            url = (self._page.url or "").lower()
            if "/g" in url or "group" in url:
                return "group"
        except Exception:
            pass
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
        # رد بات‌ها
        try:
            bot_icon = await chat.query_selector(
                'svg[aria-label="Bot-icon"]'
            )
            if bot_icon:
                log_info(f"   ⏭️ [{idx+1}] بات — رد شد")
                return True
        except Exception:
            pass
        # ── 🎯 skip چت مدیر ──
        admin_name_env = os.getenv("BALE_ADMIN_CHAT", "").strip()
        if (
            admin_name_env
            and chat_name.strip() == admin_name_env.strip()
        ):
            log_info(f"   ⏭️ [{idx+1}] چت مدیر — رد شد")
            return True            

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

        for idx, chat in enumerate(chat_items[: self._max_chats]):
            try:
                chat_name = await self._extract_item_title(chat)

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
            f"خطا={failed}"
        )

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
    # ارسال پیام
    # ═══════════════════════════════════════════════════
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        if not self._page or not chat_id or not text:
            return False
        if not self._connected:
            return False

        # ── قفل سراسری: فقط یکی می‌تونه پیام بفرسته ──
        async with BaleCollector._send_lock:
            try:
                return await asyncio.wait_for(
                    self._send_message_inner(chat_id, chat_type, text),
                    timeout=60,
                )
            except TimeoutError:
                log_warning(
                    f"⏰ [bale:send] timeout 60s | chat_id={chat_id}"
                )
                try:
                    await self._return_to_main()
                except Exception:
                    pass
                return False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_warning(
                    f"⚠️ [bale:send] {type(e).__name__}: {e} | "
                    f"chat_id={chat_id}"
                )
                return False
                
    async def _send_message_inner(
        self, chat_id, chat_type, text: str
    ) -> bool:
        original_chat_id = str(chat_id)
        is_numeric = original_chat_id.isdigit()
        admin_name_env = os.getenv("BALE_ADMIN_CHAT", "").strip()

        async with self._page_lock:
            chunks = [
                text[i:i + 4000]
                for i in range(0, len(text), 4000)
            ] or [text]

            opened = False
            for chunk in chunks:
                opened = False

                if is_numeric:
                    cached = self._chat_id_map.get(original_chat_id)
                    if cached and self._is_valid_chat_name(cached):
                        log_info(
                            f"🔁 نگاشت پایدار: "
                            f"{original_chat_id} → «{cached}»"
                        )
                        opened = await self._open_chat_by_scrolling(
                            cached
                        )
                    if not opened and admin_name_env:
                        log_info(
                            f"🔍 تلاش با BALE_ADMIN_CHAT: "
                            f"«{admin_name_env}»"
                        )
                        opened = await self._open_chat_by_scrolling(
                            admin_name_env
                        )
                    if not opened:
                        log_info(
                            f"🔍 تلاش نهایی با URL عددی: "
                            f"{original_chat_id}"
                        )
                        opened = await self._open_chat_direct_strict(
                            original_chat_id
                        )
                    if not opened:
                        log_warning(
                            f"❌ چت «{original_chat_id}» پیدا نشد. "
                            f"در .env مقدار BALE_ADMIN_CHAT را "
                            f"دقیقاً برابر نامِ چتِ مدیر در بله "
                            f"قرار بده."
                        )
                        await self._return_to_main()
                        return False
                else:
                    opened = await self._open_chat_direct(chat_id)
                    if not opened:
                        opened = await self._open_chat_by_scrolling(
                            chat_id
                        )
                    if not opened:
                        log_warning(f"❌ چت «{chat_id}» پیدا نشد")
                        await self._return_to_main()
                        return False

                if not await self._type_and_send(chunk):
                    log_warning("❌ ارسال ناموفق")
                    await self._return_to_main()
                    return False
                await asyncio.sleep(0.5)

            if is_numeric and opened:
                opened_title = await self._get_open_chat_title()
                # فقط اگه عنوان معتبر بود AND با admin_name_env هماهنگ بود
                if (
                    opened_title
                    and opened_title != "بله"
                    and self._is_valid_chat_name(opened_title)
                    and (
                        not admin_name_env
                        or opened_title.strip() == admin_name_env.strip()
                    )
                ):
                    self._chat_id_map[original_chat_id] = opened_title
                    self._save_private_chats()
                    
            await self._return_to_main()
            log_success(
                f"📤 بله: پیام به «{original_chat_id}» ارسال شد"
            )
            return True

    async def _open_chat_direct_strict(self, chat_id) -> bool:
        try:
            cid = str(chat_id).strip()
            if not cid.isdigit():
                return False
            prev_url = self._page.url or ""
            url = f"https://web.bale.ai/#/chat/{cid}"
            await self._page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2.5)
            new_url = self._page.url or ""
            if new_url == prev_url:
                log_warning(
                    f"⚠️ URL تغییر نکرد — بله آی‌دی {cid} را "
                    f"در URL پشتیبانی نمی‌کند"
                )
                return False
            if cid not in new_url:
                log_warning(f"⚠️ آی‌دی {cid} در URL نیست: {new_url}")
                return False
            if not await self._has_composer():
                log_warning(f"⚠️ composer در چت {cid} پیدا نشد")
                return False
            title = await self._get_open_chat_title()
            if title == "بله":
                log_warning(
                    "⚠️ عنوان چت «بله» شد — به چت نرسیدیم"
                )
                return False
            
            # ── چک نهایی: عنوان باید با admin_name_env یکی باشه ──
            admin_name_env = os.getenv("BALE_ADMIN_CHAT", "").strip()
            if admin_name_env and title.strip() != admin_name_env.strip():
                log_warning(
                    f"⚠️ چت اشتباه در URL: «{title}» "
                    f"(انتظار: «{admin_name_env}»)"
                )
                return False
            log_success(
                f"✅ چت «{title}» (آی‌دی {cid}) با URL باز شد"
            )
            return True
        except Exception as e:
            log_warning(f"_open_chat_direct_strict خطا: {e}")
            return False

    async def _return_to_main(self):
        try:
            await self._page.goto(
                BALE_WEB_URL, wait_until="domcontentloaded"
            )
            await asyncio.sleep(1)
        except Exception:
            pass

    async def _has_composer(self) -> bool:
        for sel in COMPOSER_SELECTORS:
            try:
                el = await self._page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _open_chat_direct(self, chat_id) -> bool:
        from urllib.parse import quote
        try:
            cid = str(chat_id).strip()
            if cid.startswith("@"):
                url = f"https://web.bale.ai/#/@{quote(cid[1:])}"
            elif cid.isdigit():
                url = f"https://web.bale.ai/#/chat/{cid}"
            else:
                return False
            await self._page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            return await self._has_composer()
        except Exception:
            return False

    async def _open_chat_by_scrolling(self, chat_id) -> bool:
        try:
            await self._page.goto(
                BALE_WEB_URL, wait_until="domcontentloaded"
            )
            await asyncio.sleep(1.5)
            cid = str(chat_id).strip().lower()
            for _ in range(3):
                try:
                    await self._page.mouse.wheel(0, 600)
                except Exception:
                    pass
                await asyncio.sleep(0.4)
            items, _ = await self._find_chat_items()
            for it in items[:60]:
                try:
                    title = await self._extract_item_title(it)
                    if not title or title == "بله":
                        continue
                    tl = title.lower()
                    # فقط تطبیق دقیق یا تطبیق کامل کلمه
                    if cid == tl:
                        try:
                            await self._page.evaluate(
                                "(el) => el.click()", it
                            )
                        except Exception:
                            continue
                        await asyncio.sleep(1.5)
                        if await self._has_composer():
                            opened_title = (
                                await self._get_open_chat_title()
                            )
                            log_success(
                                f"✅ چت باز شد: «{opened_title}» "
                                f"(جستجو با «{chat_id}»)"
                            )
                            return True
                        try:
                            await self._page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass
                except Exception:
                    continue
            return False
        except Exception as e:
            log_warning(f"_open_chat_by_scrolling ناموفق: {e}")
            return False

    async def _type_and_send(self, text: str) -> bool:
        try:
            composer = None
            for sel in COMPOSER_SELECTORS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        composer = el
                        break
                except Exception:
                    continue
            if not composer:
                return False
            await composer.click()
            await asyncio.sleep(0.3)
            try:
                await composer.fill("")
            except Exception:
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Delete")
            await asyncio.sleep(0.2)
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line:
                    await composer.type(line, delay=8)
                if i < len(lines) - 1:
                    await self._page.keyboard.press("Shift+Enter")
                    await asyncio.sleep(0.05)
            await asyncio.sleep(0.3)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(0.6)
            return True
        except Exception:
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