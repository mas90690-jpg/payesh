# collectors/bale.py
"""
کلکتور بله (Bale) — کاربر عادی روی web.bale.ai

نسخهٔ ۵.۱ (سازگار با ساختار جدید + حفظ منطق دقیق نسخهٔ قدیمی):
  - ✅ منطق دقیق نسخهٔ قدیمی حفظ شد
  - ✅ importها با ساختار جدید هماهنگ شدند:
      core.base_collector       → core.contracts.base_collector
      core.logger               → core.kernel.logger
      core.web_media            → core.adapters.web_media
      core (messenger_registry) → core.adapters.messenger_registry
  - ✅ _detect_chat_type ساده و درست (URL-based) — نه header-based
  - ✅ _is_admin_chat حفظ شد (چت مدیر skip می‌شود)
  - ✅ SENDER_NAME_SELECTORS شامل "bdi" (مهم برای Bale web)
  - ✅ _skipped_old حذف شد (چت‌های قدیمی کلاً رد نمی‌شوند)
  - ✅ connect idempotent (اگر از قبل وصل، رد می‌کند)
  - ✅ connect_lock برای جلوگیری از race
  - ✅ send_message غیرفعال (فقط‌خواندن — ربات API جداگانه)
  - ✅ dump_dom امن (اگر _page نبود، خطا نمی‌دهد)
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

# ⚠️ "bdi" مهم است برای Bale web — حذف نشود
SENDER_NAME_SELECTORS = [
    ".peer-title",
    ".message-title",
    ".sender-title",
    ".sender-name",
    ".user-title",
    "bdi",
]

_BAD_NAME_PATTERNS = []

_BAD_NAME_WORDS = []

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
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids = set()
        self._targets = get_target_groups()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()

        # 🎯 قفل سراسری برای connect (جلوگیری از race)
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

        # 🚫 چتِ مدیر
        self._admin_chat_name = os.getenv(
            "BALE_ADMIN_CHAT", ""
        ).strip()
        self._admin_user_ids = {
            x.strip()
            for x in os.getenv("ADMIN_IDS", "").split(",")
            if x.strip()
        }

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
    # cache چت‌های خصوصی
    # ═══════════════════════════════════════════════════
    def _is_valid_chat_name(self, name: str) -> bool:
        if not name:
            return False
        n = str(name).strip()
        if not n:
            return False
        return True
        
    def _load_private_chats(self):
        self._private_chat_names = []
        self._chat_id_map = {}
        if not os.path.exists(self._private_chats_file):
            return
        try:
            with open(
                self._private_chats_file, encoding="utf-8"
            ) as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return
            raw_names = list(d.get("names") or [])
            self._private_chat_names = [
                n for n in raw_names
                if self._is_valid_chat_name(n)
            ][-30:]
            raw_map = d.get("map") or {}
            if isinstance(raw_map, dict):
                for pid, name in raw_map.items():
                    pid_s = str(pid).strip()
                    if (
                        pid_s
                        and pid_s.isdigit()
                        and self._is_valid_chat_name(name)
                    ):
                        self._chat_id_map[pid_s] = str(name).strip()
            log_info(
                f"♻️ cache بله: {len(self._private_chat_names)} نام، "
                f"{len(self._chat_id_map)} نگاشت معتبر"
            )
        except Exception as e:
            log_warning(f"بارگذاری cache ناموفق: {e}")

    def _save_private_chats(self):
        try:
            safe_map = {
                str(pid): str(name)
                for pid, name in self._chat_id_map.items()
                if str(pid).strip().isdigit()
                and self._is_valid_chat_name(name)
            }
            if len(safe_map) > 100:
                safe_map = dict(list(safe_map.items())[-100:])
            with open(
                self._private_chats_file, "w", encoding="utf-8"
            ) as f:
                json.dump(
                    {
                        "names": self._private_chat_names[-30:],
                        "map": safe_map,
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

    def _remember_chat_mapping(
        self, peer_id: str, chat_name: str
    ):
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
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ "
                    f"بله بارگذاری شد"
                )
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:]
                )
            with open(
                self._seen_path, "w", encoding="utf-8"
            ) as f:
                json.dump(
                    list(self._seen_ids), f, ensure_ascii=False
                )
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════════
    async def connect(
        self, headless: bool | None = None
    ) -> bool:
        """🎯 قفل سراسری مرورگر — جلوگیری از تداخل با ایتا/لینکدین."""
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(
        self, headless: bool | None = None
    ) -> bool:
        """اتصال واقعی — فقط وقتی BROWSER_LOCK گرفته شده."""
        # 🎯 idempotent: اگر از قبل وصل، رد کن
        if self._connected and self._context:
            log_info(
                "ℹ️ [bale:connect] از قبل متصل — رد"
            )
            return True

        async with BaleCollector._connect_lock:
            if self._connected and self._context:
                log_info(
                    "ℹ️ [bale:connect] توی قفل — از قبل متصل"
                )
                return True

            if self._context:
                log_warning(
                    "⚠️ [bale:connect] context قدیمی باز — بستن"
                )
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست")
                    return False

                use_headless = (
                    self.headless
                    if headless is None
                    else headless
                )
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(
                    use_headless
                )
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
                await self._page.goto(
                    BALE_WEB_URL, wait_until="domcontentloaded"
                )

                for i in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._needs_backfill = True
                        self._set_status(
                            ConnectionStatus.CONNECTED
                        )
                        log_success(
                            f"✅ اتصال بله‌وب برقرار شد — "
                            f"بعد از {i+1} تلاش (فقط‌خواندن)"
                        )
                        log_info(
                            "ℹ️ [bale:connect] send_message "
                            "غیرفعال — ربات API جداگانه ارسال "
                            "می‌کند"
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
    # launch context
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
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    async def _launch_context(self, use_headless: bool):
        from config import CHROME_PATH

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
                for x in (
                    "/login", "/auth", "/signin", "/verify"
                )
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
                            pass
                except Exception:
                    continue

            return False

        except Exception:
            return False
            
    # ═══════════════════════════════════════════════════
    # ابزار کمکی
    # ═══════════════════════════════════════════════════
    async def _extract_peer_id_from_item(self, chat_item) -> str:
        for attr in (
            "data-peer-id", "data-id", "data-peer",
            "data-chat-id", "data-uid", "data-object-guid",
        ):
            try:
                val = await chat_item.get_attribute(attr)
                if val and str(val).strip():
                    return str(val).strip()
            except Exception:
                continue

        try:
            href = await chat_item.get_attribute("href")
            if href:
                m = re.search(r"#/(?:@|g/)?([A-Za-z0-9_]+)", href)
                if m:
                    return m.group(1)
        except Exception:
            pass

        return ""
        
    async def _get_current_peer_id(self) -> str:
        try:
            url = self._page.url or ""
            for pattern in (
                r"#/@([A-Za-z0-9_]+)",
                r"#/g/([A-Za-z0-9_]+)",
                r"chat/(\d+)",
                r"peer/(\d+)",
                r"@([A-Za-z0-9_]+)",
            ):
                m = re.search(pattern, url)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""
        
    # 🎯 FIX: منطق ساده و درست (URL-based) — نسخهٔ قدیمی
    async def _detect_chat_type(self) -> str:
        try:
            url = (self._page.url or "").lower()
            if "/g" in url or "group" in url:
                return "group"
        except Exception:
            pass
        return "private"

    async def _extract_chat_meta(
        self,
    ) -> tuple[str | None, str | None]:
        """استخراج (chat_username, chat_link) از URL."""
        try:
            url = self._page.url or ""
            m = re.search(r"#/@([A-Za-z0-9_]+)", url)
            if m:
                uname = m.group(1)
                return uname, f"https://web.bale.ai/#/@{uname}"
            m = re.search(r"#/g/([A-Za-z0-9_]+)", url)
            if m:
                uname = m.group(1)
                return uname, f"https://web.bale.ai/#/g/{uname}"
        except Exception:
            pass
        return None, None

    async def _extract_sender_from_bubble(
        self, msg
    ) -> tuple[str, str]:
        sender_id, sender_name = "", ""
        for attr in (
            "data-peer-id", "data-user-id", "data-sender-id",
            "data-from-id",
        ):
            try:
                v = await msg.get_attribute(attr)
                if v and str(v).strip():
                    sender_id = str(v).strip()
                    break
            except Exception:
                continue
        for sel in SENDER_NAME_SELECTORS:
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

        for i in range(self._backfill_scrolls):
            try:
                scrolled = await self._page.evaluate(
                    "() => {"
                    "  const c = document.querySelector("
                    "    '.bubbles, .scrollable, "
                    "     .messages-container, "
                    "     #column-center .scrollable'"
                    "  );"
                    "  if (c) {"
                    "    const before = c.scrollTop;"
                    "    c.scrollTop = Math.max(0, before - 2500);"
                    "    return before !== c.scrollTop;"
                    "  }"
                    "  return false;"
                    "}"
                )
                if not scrolled:
                    break
            except Exception:
                break
            await asyncio.sleep(0.8)
            
    async def _find_chat_items(self):
        for attempt in range(20):
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
                            f"✅ [بله] لیست چت با `{sel}` پیدا "
                            f"شد ({len(visible)} آیتم) — "
                            f"تلاش {attempt + 1}"
                        )
                        return visible, sel
                except Exception:
                    continue
            await asyncio.sleep(1)
        return [], ""
        
    # 🎯 FIX: گارد چت مدیر — حفظ شد از نسخهٔ قدیمی
    def _is_admin_chat(
        self, chat_name: str, peer_id: str
    ) -> bool:
        if peer_id and peer_id in self._admin_user_ids:
            return True
        if self._admin_chat_name and chat_name:
            if chat_name.strip() == self._admin_chat_name:
                return True
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
        skipped_admin = 0
        failed = 0

        chats_to_process = (
            chat_items
            if self._max_chats <= 0
            else chat_items[: self._max_chats]
        )

        for idx, chat in enumerate(chats_to_process):
            try:
                # 🚫 بات‌ها
                try:
                    bot_icon = await chat.query_selector(
                        'svg[aria-label="Bot-icon"]'
                    )
                    if bot_icon:
                        log_info(f"   ⏭️ [{idx+1}] بات — رد شد")
                        skipped_bot += 1
                        continue
                except Exception:
                    pass

                chat_name = await self._extract_item_title(chat)
                peer_id = await self._extract_peer_id_from_item(
                    chat
                )

                # 🎯 FIX: چتِ مدیر skip شود
                if self._is_admin_chat(chat_name, peer_id):
                    log_info(
                        f"   ⏭️ [{idx+1}] چتِ مدیر — رد شد: "
                        f"{chat_name}"
                    )
                    skipped_admin += 1
                    continue

                # گارد گروه هدف
                if chat_name:
                    try:
                        if (
                            self._targets.enabled(_PLATFORM)
                            and not self._targets.is_target(
                                chat_name, platform=_PLATFORM
                            )
                        ):
                            log_info(
                                f"   ⏭️ [{idx+1}] غیرهدف: "
                                f"«{chat_name}»"
                            )
                            skipped_target += 1
                            continue
                    except Exception as e:
                        log_warning(
                            f"   ⚠️ [bale:target] خطا | "
                            f"{type(e).__name__}: {e} — پیش‌فرض: قبول"
                        )

                try:
                    await chat.click(timeout=5000, force=True)
                except Exception as e:
                    log_warning(f"کلیک ناموفق روی چت {chat_name}: {e}")
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
            f"مدیر={skipped_admin} | خطا={failed}"
        )

    async def _extract_item_title(self, chat_item) -> str:
        for sel in TITLE_SELECTORS:
            try:
                el = await chat_item.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if first:
                            return first
            except Exception:
                continue
        try:
            for attr in ("data-peer-id", "data-name"):
                v = await chat_item.get_attribute(attr)
                if v:
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
        if not chat_name:
            return

        chat_type = await self._detect_chat_type()
        if not peer_id:
            peer_id = await self._get_current_peer_id()
        chat_id = peer_id or chat_name

        chat_username, chat_link = await self._extract_chat_meta()

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
        checked = 0

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

                checked += 1
                uniq = f"{chat_id}:{mid}"

                if (
                    chat_type != "private"
                    and not self._kw.has_match(text)
                ):
                    continue

                if uniq in self._seen_ids:
                    continue
                self._seen_ids.add(uniq)

                if chat_type == "private":
                    sender_id = peer_id or ""
                    sender_name = chat_name
                else:
                    sender_id, sender_name = (
                        await self._extract_sender_from_bubble(
                            msg
                        )
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
                            self._page, msg,
                            self._media_dir, _PLATFORM,
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
                    "chat_username": chat_username,
                    "chat_link": chat_link,
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
            log_success(
                f"📩 از چت {chat_name}: {count} پیام جدید "
                f"(از {checked} پیام چک‌شده)"
            )
        else:
            log_info(
                f"ℹ️ چت {chat_name}: هیچ پیام جدیدی "
                f"(از {checked} پیام چک‌شده)"
            )
            
    async def _get_open_chat_title(self) -> str:
        for sel in TITLE_SELECTORS:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if first:
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
            if not self._page:
                log_warning("dump_dom: صفحه‌ای وجود ندارد")
                return ""
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
            if self._page:
                await self._page.close()
        except Exception:
            pass
        self._page = None

        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._context = None

        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None

        try:
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از بله‌وب")
        
    async def check_health(self) -> bool:
        return self._connected