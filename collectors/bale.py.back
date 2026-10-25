# collectors/bale.py
"""
کلکتور بله (Bale) — کاربر عادی روی web.bale.ai با Playwright

نسخهٔ ۷.۰ (DOM + State پایدار + ضدتکرار دقیق):
  - 🎯 بار اول: ۶ اسکرول backfill
  - 🎯 بارهای بعد: ۲ اسکرول backfill
  - 🎯 state.json برای تشخیص بار اول
  - 🎯 شناسهٔ پایدار: peer_id → data-attr → name → hash محتوا
  - 🎯 حذف چت‌های سیستمی (شامل «بله»)
  - 🎯 جلوگیری از تکرار با peer_id-based dedup
  - ✅ timeout روی هر چت، هر سلکتور، backfill
  - ✅ BROWSER_LOCK استاندارد پروژه
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

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# ─── ثابت‌ها ───
_MAX_SEEN = 100000
BALE_WEB_URL = "https://web.bale.ai/"
_PLATFORM = "bale"

# 🎯 timeoutها
_MAX_BACKFILL_SCROLLS = 6
_CHAT_TIMEOUT_INITIAL = 60
_CHAT_TIMEOUT_NORMAL = 30
_SELECTOR_TIMEOUT_MS = 3000
_WAIT_FOR_MSG_MS = 3000
_BACKFILL_EVAL_TIMEOUT = 5

# 🎯 پوشه‌های سیستمی (چت واقعی نیستند) — «بله» هم اضافه شد
SYSTEM_FOLDER_NAMES = {
    "فضای شخصی", "پیش‌نویس", "شما", "تصویر", "پیام",
    "ذخیره‌شده", "بایگانی", "آرشیو",
    "Saved Messages", "Drafts", "You", "Images", "Messages",
    "Archive", "Archived",
    # 🆕 چت سیستمی خود Bale
    "بله", "Bale", "Bale Messenger", "پیام‌های بله",
    "Bale Team", "تیم بله", "پشتیبانی بله",
}

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
    "[class*='peer-title']",
    "[class*='dialog-title']",
    "[class*='chat-title']",
    "span[dir='auto']",
]

SENDER_NAME_SELECTORS = [
    ".peer-title",
    ".message-title",
    ".sender-title",
    ".sender-name",
    ".user-title",
    "bdi",
]

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
        max_chats: int = 0,
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
        self._seen_ids: set[str] = set()
        self._failed_chats: set[str] = set()
        self._targets = get_target_groups()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()

        if not hasattr(BaleCollector, "_connect_lock"):
            BaleCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR
        self._storage_dir = STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "bale_web"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "bale")
        os.makedirs(self._media_dir, exist_ok=True)

        # ─── seen (ضدتکراری) ───
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "bale_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        # ─── state (بار اول / بارهای بعد) ───
        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "bale_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()

        # ─── cache چت‌های خصوصی ───
        self._private_chats_file = os.path.join(
            STORAGE_DIR, "offset", "bale_private_chats.json"
        )
        os.makedirs(
            os.path.dirname(self._private_chats_file), exist_ok=True
        )
        self._private_chat_names: list[str] = []
        self._chat_id_map: dict[str, str] = {}
        self._load_private_chats()

        # ─── گارد مدیر ───
        self._admin_chat_name = os.getenv(
            "BALE_ADMIN_CHAT", ""
        ).strip()
        self._admin_user_ids = {
            x.strip()
            for x in os.getenv("ADMIN_IDS", "").split(",")
            if x.strip()
        }

    # ═══════════════════════════════════════════════════
    # state: بار اول / بارهای بعد
    # ═══════════════════════════════════════════════════
    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info(
                    "♻️ [bale:state] بار اول انجام شده — "
                    "چک سریع (۲ اسکرول)"
                )
            else:
                log_info(
                    "🆕 [bale:state] بار اول — تاریخچه کامل "
                    "(۶ اسکرول)"
                )
            return done
        except Exception:
            log_info(
                "🆕 [bale:state] بار اول — تاریخچه کامل "
                "(۶ اسکرول)"
            )
            return False

    def _save_first_run(self):
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "first_run_done": True,
                        "first_run_at": datetime.now().isoformat(),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            log_success(
                "💾 [bale:state] بار اول ثبت شد — "
                "دفعات بعد سریع‌تر"
            )
        except Exception as e:
            log_warning(f"ذخیره state ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # cache چت‌های خصوصی
    # ═══════════════════════════════════════════════════
    def _is_valid_chat_name(self, name: str) -> bool:
        if not name:
            return False
        return bool(str(name).strip())

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
                    if pid_s and self._is_valid_chat_name(name):
                        self._chat_id_map[pid_s] = str(name).strip()
            log_info(
                f"♻️ cache بله: {len(self._private_chat_names)} نام، "
                f"{len(self._chat_id_map)} نگاشت"
            )
        except Exception as e:
            log_warning(f"بارگذاری cache ناموفق: {e}")

    def _save_private_chats(self):
        try:
            safe_map = {
                str(pid): str(name)
                for pid, name in self._chat_id_map.items()
                if str(pid).strip()
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
                    f,
                    ensure_ascii=False,
                    indent=2,
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
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ بله"
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
                json.dump(
                    list(self._seen_ids),
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(
        self, headless: bool | None = None
    ) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [bale:connect] از قبل متصل — رد")
            return True

        async with BaleCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "⚠️ [bale:connect] context قدیمی — بستن"
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
                            f"بعد از {i+1} تلاش"
                        )
                        return True

                await self.dump_dom()
                log_error("نشست بله‌وب تشخیص داده نشد")
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال بله‌وب: {e}")
                self._set_error(str(e))
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
        """فقط chrome های پروفایل خودمون رو می‌کشه (نه Chrome شخصی کاربر)."""
        import subprocess
        import re as _re
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='chrome.exe'",
                 "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore",
            ).stdout
        except Exception:
            return

        target = os.path.normcase(self._user_data_dir)
        for line in out.splitlines():
            if "--user-data-dir" not in line:
                continue
            if target not in os.path.normcase(line):
                continue
            m = _re.search(r"(\d+)\s*$", line.strip())
            if not m:
                continue
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", m.group(1), "/T"],
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
                log_warning("⚠️ پروفایل قفل — پاک‌سازی و تلاش")
                self._force_kill_chrome()
                await asyncio.sleep(2)
                await self._cleanup_profile_locks()
                ctx = (
                    await self._pw.chromium
                    .launch_persistent_context(**launch_options)
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
        """استخراج peer_id — با گسترهٔ وسیع‌تر."""
        # ۱) attributeهای داده‌ای
        for attr in (
            "data-peer-id", "data-id", "data-peer",
            "data-chat-id", "data-uid", "data-object-guid",
            "data-dialog-id", "data-peer-guid",
        ):
            try:
                val = await chat_item.get_attribute(attr)
                if val and str(val).strip():
                    v = str(val).strip()
                    # فیلتر مقادیر بی‌معنی
                    if v not in ("0", "-1", "null", "undefined"):
                        return v
            except Exception:
                continue

        # ۲) از href
        try:
            href = await chat_item.get_attribute("href")
            if href:
                m = re.search(
                    r"#/(?:@|g/)?([A-Za-z0-9_]+)", href
                )
                if m:
                    return m.group(1)
        except Exception:
            pass

        # ۳) از فرزندان (بعضی آیتم‌ها data-peer-id روی child دارند)
        for child_sel in (
            "[data-peer-id]",
            "[data-dialog-id]",
            "[data-chat-id]",
            "a[href*='#/']",
        ):
            try:
                child = await chat_item.query_selector(child_sel)
                if child:
                    for attr in (
                        "data-peer-id", "data-dialog-id",
                        "data-chat-id", "data-id",
                    ):
                        v = await child.get_attribute(attr)
                        if v and str(v).strip():
                            v_s = str(v).strip()
                            if v_s not in ("0", "-1"):
                                return v_s
                    href = await child.get_attribute("href")
                    if href:
                        m = re.search(
                            r"#/(?:@|g/)?([A-Za-z0-9_]+)", href
                        )
                        if m:
                            return m.group(1)
            except Exception:
                continue

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

    async def _detect_chat_type(self) -> str:
        try:
            url = self._page.url or ""
            if re.search(r"#/g/[A-Za-z0-9_]+", url):
                return "group"
            low = url.lower()
            if "/group" in low or "?group=" in low:
                return "group"
        except Exception:
            pass
        return "private"

    async def _extract_chat_meta(
        self,
    ) -> tuple[str | None, str | None]:
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

    async def _extract_item_title(self, chat_item) -> str:
        title = ""
        for sel in TITLE_SELECTORS:
            try:
                el = await chat_item.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if first and len(first) < 80:
                            title = first
                            break
            except Exception:
                continue

        if not title:
            try:
                v = await chat_item.get_attribute("data-name")
                if v and str(v).strip():
                    title = str(v).strip()
            except Exception:
                pass

        if not title:
            try:
                txt = (await chat_item.inner_text() or "").strip()
                if txt:
                    first = txt.split("\n")[0].strip()
                    if first and 2 < len(first) < 80:
                        title = first
            except Exception:
                pass

        if title:
            title = re.sub(r"\s+", " ", title).strip()

        return title

    def _is_admin_chat(self, chat_name: str, peer_id: str) -> bool:
        if peer_id and peer_id in self._admin_user_ids:
            return True
        if self._admin_chat_name and chat_name:
            if chat_name.strip() == self._admin_chat_name:
                return True
        return False

    def _is_system_name(self, name: str) -> bool:
        if not name:
            return False
        n = name.strip()
        if n in SYSTEM_FOLDER_NAMES:
            return True
        return False

    # ═══════════════════════════════════════════════════
    # DOM helpers
    # ═══════════════════════════════════════════════════
    async def _find_chat_items_raw(self):
        for sel in CHAT_SELECTORS:
            try:
                items = await self._page.query_selector_all(sel)
                if items:
                    return items
            except Exception:
                continue
        return []

    # ═══════════════════════════════════════════════════
    # جمع‌آوری همه چت‌ها (با peer_id)
    # ═══════════════════════════════════════════════════
    async def _collect_all_chats(self) -> list[dict]:
        # ۱) برگرد به بالای لیست
        for _ in range(50):
            try:
                await self._page.mouse.wheel(0, -3000)
            except Exception:
                pass
            await asyncio.sleep(0.1)

        # ۲) اسکرول + جمع‌آوری
        chats: list[dict] = []
        seen_keys: set[str] = set()
        stable_rounds = 0
        last_count = 0

        for round_i in range(120):
            items = await self._find_chat_items_raw()
            for it in items:
                try:
                    pid = await self._extract_peer_id_from_item(it)
                    name = await self._extract_item_title(it)

                    # 🎯 رد چت‌های سیستمی
                    if name and self._is_system_name(name):
                        continue
                    if not pid and name and self._is_system_name(name):
                        continue

                    # 🎯 کلید پایدار: peer_id اول، بعد name
                    if pid:
                        key = f"pid:{pid}"
                    elif name and len(name) > 1:
                        key = f"name:{name.strip()}"
                    else:
                        # نه peer_id نه name → skip
                        continue

                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    chats.append({
                        "key": key,
                        "peer_id": pid or "",
                        "name": (name or pid or "?").strip(),
                    })
                except Exception:
                    continue

            try:
                await self._page.mouse.wheel(0, 2500)
            except Exception:
                pass
            await asyncio.sleep(0.7)

            cur = len(chats)
            if cur == last_count:
                stable_rounds += 1
                if stable_rounds >= 6:
                    break
            else:
                stable_rounds = 0
                last_count = cur
                log_debug(f"   ↕️ دور {round_i+1}: {cur} چت")

        log_info(f"🎯 اسکرول کامل شد | {len(chats)} چت یکتا")
        return chats

    # ═══════════════════════════════════════════════════
    # کلیک روی چت با کلید
    # ═══════════════════════════════════════════════════
    async def _click_item(self, item) -> bool:
        try:
            await item.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        await asyncio.sleep(0.2)
        try:
            await item.click(timeout=5000, force=True)
            return True
        except Exception:
            return False

    async def _find_and_click_by_key(
        self, chat_key: str, display_name: str
    ) -> bool:
        target_pid = ""
        target_name = ""
        if chat_key.startswith("pid:"):
            target_pid = chat_key[4:]
        elif chat_key.startswith("name:"):
            target_name = chat_key[5:]
        else:
            target_name = chat_key

        # ۱) از DOM فعلی
        items = await self._find_chat_items_raw()
        for it in items:
            try:
                if target_pid:
                    pid = await self._extract_peer_id_from_item(it)
                    if pid == target_pid:
                        if await self._click_item(it):
                            return True
                if target_name:
                    name = await self._extract_item_title(it)
                    if name and name.strip() == target_name:
                        if await self._click_item(it):
                            return True
            except Exception:
                continue

        # ۲) از بالا به پایین
        for _ in range(50):
            try:
                await self._page.mouse.wheel(0, -3000)
            except Exception:
                pass
            await asyncio.sleep(0.12)

        for _ in range(120):
            items = await self._find_chat_items_raw()
            for it in items:
                try:
                    if target_pid:
                        pid = await self._extract_peer_id_from_item(it)
                        if pid == target_pid:
                            if await self._click_item(it):
                                return True
                    if target_name:
                        name = await self._extract_item_title(it)
                        if name and name.strip() == target_name:
                            if await self._click_item(it):
                                return True
                except Exception:
                    continue
            try:
                await self._page.mouse.wheel(0, 1800)
            except Exception:
                pass
            await asyncio.sleep(0.35)

        return False

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
            log_success(f"📥 {len(new_msgs)} پیام از بله")
        return new_msgs

    async def _backfill_scroll(self):
        if not (
            self._needs_backfill and self._backfill_scrolls > 0
        ):
            return

        max_scrolls = min(
            self._backfill_scrolls, _MAX_BACKFILL_SCROLLS
        )

        for i in range(max_scrolls):
            try:
                scrolled = await asyncio.wait_for(
                    self._page.evaluate(
                        "() => {"
                        "  const c = document.querySelector("
                        "    '.bubbles, .scrollable, "
                        "     .messages-container, "
                        "     #column-center .scrollable'"
                        "  );"
                        "  if (c) {"
                        "    const before = c.scrollTop;"
                        "    c.scrollTop = "
                        "      Math.max(0, before - 2500);"
                        "    return before !== c.scrollTop;"
                        "  }"
                        "  return false;"
                        "}"
                    ),
                    timeout=_BACKFILL_EVAL_TIMEOUT,
                )
                if not scrolled:
                    break
            except Exception:
                break
            await asyncio.sleep(0.8)

    # ═══════════════════════════════════════════════════
    # اسکرپ همه چت‌ها
    # ═══════════════════════════════════════════════════
    async def _scrape_all_chats(self):
        # اگر در صفحهٔ search هستیم، برگرد
        try:
            url = (self._page.url or "").lower()
            if "/search" in url:
                await self._page.goto(
                    BALE_WEB_URL, wait_until="domcontentloaded"
                )
                await asyncio.sleep(2)
        except Exception:
            pass

        # ۱) کشف همه چت‌ها
        chats = await self._collect_all_chats()

        if not chats:
            log_warning("⚠️ هیچ چتی پیدا نشد")
            try:
                await self.dump_dom()
            except Exception:
                pass
            return

        # ۲) حالت
        is_first = not self._first_run_done
        if is_first:
            log_info(
                f"🔍 {len(chats)} چت | "
                f"حالت: بار اول — تاریخچه کامل (۶ اسکرول)"
            )
        else:
            log_info(
                f"🔍 {len(chats)} چت | "
                f"حالت: چک سریع (۲ اسکرول)"
            )

        # 🎯 تعیین تعداد اسکرول backfill
        self._backfill_scrolls = 6 if is_first else 2

        processed = 0
        skipped_target = 0
        skipped_admin = 0
        skipped_system = 0
        failed = 0

        if self._max_chats > 0:
            chats = chats[: self._max_chats]

        for idx, chat in enumerate(chats):
            chat_key = chat["key"]
            chat_name = chat["name"]
            peer_id = chat["peer_id"]

            try:
                # گارد سیستمی (دوباره چک می‌کنیم — احتیاط)
                if self._is_system_name(chat_name):
                    log_debug(
                        f"   ⏭️ [{idx+1}] سیستمی: {chat_name}"
                    )
                    skipped_system += 1
                    continue

                # گارد مدیر
                if self._is_admin_chat(chat_name, peer_id):
                    log_info(
                        f"   ⏭️ [{idx+1}] چتِ مدیر: {chat_name}"
                    )
                    skipped_admin += 1
                    continue

                # گارد گروه هدف
                try:
                    if (
                        self._targets.enabled(_PLATFORM)
                        and not self._targets.is_target(
                            chat_name, platform=_PLATFORM
                        )
                    ):
                        log_debug(
                            f"   ⏭️ [{idx+1}] غیرهدف: {chat_name}"
                        )
                        skipped_target += 1
                        continue
                except Exception as e:
                    log_warning(
                        f"   ⚠️ [bale:target] "
                        f"{type(e).__name__}: {e}"
                    )

                # کلیک
                log_info(
                    f"   ▶️ [{idx+1}/{len(chats)}] {chat_name}"
                )
                clicked = await self._find_and_click_by_key(
                    chat_key, chat_name
                )
                if not clicked:
                    log_warning(
                        f"   ⚠️ [{idx+1}] پیدا نشد: {chat_name}"
                    )
                    failed += 1
                    continue

                await asyncio.sleep(self._chat_open_delay)

                _per_chat_timeout = (
                    _CHAT_TIMEOUT_INITIAL
                    if is_first
                    else _CHAT_TIMEOUT_NORMAL
                )
                try:
                    await asyncio.wait_for(
                        self._scrape_current_chat(
                            chat_name, peer_id
                        ),
                        timeout=_per_chat_timeout,
                    )
                except asyncio.TimeoutError:
                    log_warning(
                        f"⏱️ timeout در {chat_name} — skip"
                    )
                    self._failed_chats.add(chat_key)
                    continue
                processed += 1

                try:
                    await self._page.mouse.wheel(0, -200)
                except Exception:
                    pass

            except Exception as e:
                failed += 1
                log_warning(
                    f"خطا در {chat_name}: "
                    f"{type(e).__name__}: {e}"
                )
                continue

        log_success(
            f"🏁 [bale:scrape] کامل | پردازش={processed} | "
            f"سیستمی={skipped_system} | غیرهدف={skipped_target} | "
            f"مدیر={skipped_admin} | خطا={failed}"
        )

        if self._failed_chats:
            sample = list(self._failed_chats)[:5]
            log_warning(
                f"⏱️ [bale:timeout] {len(self._failed_chats)} چت "
                f"timeout خورد | نمونه: {sample}"
            )

        # 🎯 ثبت بار اول
        if not self._first_run_done:
            self._save_first_run()
            self._first_run_done = True

    # ═══════════════════════════════════════════════════
    # اسکرپ چت باز فعلی
    # ═══════════════════════════════════════════════════
    async def _scrape_current_chat(
        self, chat_name: str = None, peer_id: str = None
    ):
        page = self._page
        if not chat_name:
            chat_name = await self._get_open_chat_title()

        if not chat_name:
            if peer_id:
                chat_name = f"peer_{peer_id[:16]}"
            else:
                log_warning(
                    "⚠️ [bale:scrape] نام و peer_id خالی"
                )
                return

        chat_type = await self._detect_chat_type()
        if not peer_id:
            peer_id = await self._get_current_peer_id()
        chat_id = peer_id or chat_name

        chat_username, chat_link = await self._extract_chat_meta()

        log_info(
            f"📖 اسکرپ: {chat_name} "
            f"(نوع: {chat_type}، id: {chat_id})"
        )

        if chat_type == "private":
            self._remember_private_chat(chat_name)
            if peer_id:
                self._remember_chat_mapping(peer_id, chat_name)

        await self._backfill_scroll()

        selector_csv = ", ".join(MSG_SELECTORS)
        if selector_csv:
            try:
                await page.wait_for_selector(
                    selector_csv,
                    timeout=_WAIT_FOR_MSG_MS,
                    state="attached",
                )
            except Exception:
                pass

        messages = []
        for sel in MSG_SELECTORS:
            try:
                messages = await asyncio.wait_for(
                    page.query_selector_all(sel),
                    timeout=_SELECTOR_TIMEOUT_MS / 1000,
                )
                if messages:
                    log_info(
                        f"✅ پیام‌ها با {sel} ({len(messages)})"
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

        _seen_in_this_chat: set[str] = set()
        for msg in messages[-limit:]:
            try:
                mid = (
                    await msg.get_attribute("data-mid")
                ) or (
                    await msg.get_attribute("data-msgid")
                )
                text = (await msg.inner_text() or "").strip()

                if not mid and text:
                    # 🎯 hash پایدار بر اساس محتوا + نام چت
                    mid = hashlib.sha256(
                        f"{chat_name}:{text}".encode("utf-8")
                    ).hexdigest()[:16]

                if not mid or mid == "0" or not text:
                    continue

                checked += 1
                uniq = f"{chat_id}:{mid}"

                if uniq in _seen_in_this_chat:
                    continue
                _seen_in_this_chat.add(uniq)

                if (
                    chat_type != "private"
                    and not self._kw.has_match(text)
                ):
                    log_debug(
                        f"⏭️ [kw] بدون کلیدواژه | {chat_name}"
                    )
                    continue

                if uniq in self._seen_ids:
                    log_debug(f"⏭️ [seen] تکراری | {chat_name}")
                    continue
                self._seen_ids.add(uniq)

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
                            self._page,
                            msg,
                            self._media_dir,
                            _PLATFORM,
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
                f"📩 از {chat_name}: {count} پیام "
                f"(از {checked} چک‌شده)"
            )
        else:
            log_info(
                f"ℹ️ {chat_name}: هیچ پیام جدید "
                f"(از {checked} چک‌شده)"
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
        return ""

    # ═══════════════════════════════════════════════════
    # ارسال پیام — غیرفعال
    # ═══════════════════════════════════════════════════
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
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
                log_warning("dump_dom: صفحه‌ای نیست")
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
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از بله‌وب")

    async def check_health(self) -> bool:
        return self._connected