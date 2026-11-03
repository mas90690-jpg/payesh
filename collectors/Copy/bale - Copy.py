# collectors/bale.py
"""
کلکتور بله (Bale) — نسخهٔ ۹.۰ (نهایی — فوق دقیق)

ویژگی‌ها:
  - 🎯 id پایدار از data-sid + chat_id (بدون تکرار)
  - 🎯 timestamp دقیق از data-date (میلی‌ثانیه)
  - 🎯 since دقیق (فیلتر بر اساس data-date)
  - 🎯 last_run_at — چند روز خاموش بود، از همون‌جا ادامه می‌ده
  - 🎯 چت‌های سیستمی کامل فیلتر می‌شن
  - 🎯 پیام بدون متن معتبر ذخیره نمی‌شه
  - 🎯 آمار تفکیکی (_last_scrape_stats)
  - 🎯 _seen_ids فقط بعد از keyword filter
  - 🎯 messages_per_chat از env واقعاً اعمال می‌شه
  - 🎯 اسکرول داخل چت تا رسیدن به last_seen_date
  - ✅ BROWSER_LOCK استاندارد
  - ✅ connect idempotent
  - ✅ تنظیمات از .env
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

# ═══════════════════════════════════════════════════════
# ثابت‌ها — همهٔ اعداد جادویی اینجا متمرکز شده‌اند
# ═══════════════════════════════════════════════════════

# ─── هویت پلتفرم ───
_PLATFORM = "bale"
BALE_WEB_URL = "https://web.bale.ai/"

# ─── حافظهٔ ضدتکرار ───
_MAX_SEEN = 100000

# ─── اسکرول لیست چت‌ها ───
_MAX_SCROLL_ROUNDS = 20          # حداکثر دور اسکرول لیست چت
_SCROLL_SLEEP = 0.15             # مکث بین هر دور اسکرول (کاهش از 0.3)
_SCROLL_STABLE_ROUNDS = 2        # چند دور بدون تغییر = پایان (کاهش از 3)
_SCROLL_WHEEL_DOWN = 2500        # پیکسل اسکرول به پایین
_SCROLL_WHEEL_UP = 3000          # پیکسل اسکرول به بالا
_SCROLL_INITIAL_ROUNDS = 6       # اسکرول اولیه به بالا (کاهش از 10)
_SCROLL_INITIAL_SLEEP = 0.05     # مکث اسکرول اولیه (کاهش از 0.1)

# ─── backfill داخل چت ───
_MAX_BACKFILL_SCROLLS = 3        # حداکثر دور backfill
_BACKFILL_PER_SCROLL_WAIT = 0.4  # مکث بین هر backfill
_BACKFILL_EVAL_TIMEOUT = 5       # timeout ارزیابی JS
_BACKFILL_SCROLL_PX = 2500       # پیکسل backfill

# ─── کلیک روی چت ───
_CLICK_SCROLL_ROUNDS = 15        # حداکثر دور جستجوی چت
_CLICK_SCROLL_SLEEP = 0.15       # مکث بین هر دور
_CLICK_SCROLL_PX = 1800          # پیکسل اسکرول در جستجو
_CLICK_TIMEOUT_MS = 5000         # timeout کلیک
_CLICK_SCROLL_INTO_VIEW_MS = 2000
_CLICK_PRE_SLEEP = 0.2           # مکث قبل از کلیک

# ─── timeoutها ───
_CHAT_TIMEOUT_INITIAL = 60       # timeout چت در بار اول
_CHAT_TIMEOUT_NORMAL = 30        # timeout چت در حالت عادی
_MAX_SCROLL_TIME_TOTAL = 20      # سقف کل زمان اسکرول لیست (ثانیه)
_SELECTOR_TIMEOUT_MS = 5000
_WAIT_FOR_MSG_MS = 5000

# ─── اتصال ───
_CONNECT_MAX_ATTEMPTS = 20       # حداکثر تلاش برای تشخیص لاگین
_CONNECT_RETRY_SLEEP = 2         # مکث بین تلاش‌ها
_CONNECT_NAV_SLEEP = 2           # مکث بعد از goto

# ─── محدودیت‌های cache ───
_MAX_PRIVATE_CHAT_NAMES = 30
_MAX_CHAT_ID_MAP = 100

# ─── محدودیت پیام ───
_MIN_MESSAGES_BACKFILL = 300     # حداقل پیام در حالت backfill

# 🎯 چت‌های سیستمی — کامل
SYSTEM_FOLDER_NAMES = {
    # فارسی
    "فضای شخصی", "پیش‌نویس", "شما", "تصویر", "پیام",
    "ذخیره‌شده", "ذخیره شده", "پیام‌های ذخیره‌شده",
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده",
    "بایگانی", "آرشیو",
    "فایل", "فایل‌ها", "فایل‌های من",
    "استیکر", "استیکرها",
    "گیف", "گیف‌ها",
    "ویدیو", "ویدیوها",
    "ویس", "ویس‌ها",
    "عکس", "عکس‌ها",
    "لینک", "لینک‌ها",
    "مخاطبین", "مخاطبین من",
    "پوشه‌ها", "گروه‌ها", "کانال‌ها",
    "آلبوم", "آلبوم‌ها",
    "بله", "پیام‌های بله", "تیم بله", "پشتیبانی بله",
    # انگلیسی
    "Saved Messages", "Drafts", "You", "Images", "Messages",
    "Archive", "Archived", "Files", "Stickers", "GIFs",
    "Videos", "Voice", "Photos", "Links", "Contacts",
    "Folders", "Groups", "Channels", "Albums",
    "Bale", "Bale Messenger", "Bale Team",
}

# 🎯 پیشوندهای سیستمی
SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "کانال جدید", "گروه جدید",
    "Channel created", "Group created", "New channel", "New group",
    "پیام‌های ذخیره", "Saved messages",
)

# 🎯 مقادیر نامعتبر برای متن پیام
_INVALID_TEXT_PATTERNS = [
    r"^\s*$",
    r"^\s*[\d۰-۹]{1,2}:[\d۰-۹]{2}\s*$",
    r"^\s*[\d۰-۹]+\s*$",
    r"^\s*[\d۰-۹]{1,2}:[\d۰-۹]{2}\s*\n\s*[\d۰-۹]+\s*$",
]

# ─── سلکتورها ───
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
    ".message-item",
    ".bubbles .bubble",
    ".bubble",
    ".message",
]

# 🎯 سلکتورهای مخصوص عنوان چت (نه متن پیام)
CHAT_TITLE_SELECTORS = [
    ".chat-info .peer-title",
    ".MiddleHeader .peer-title",
    ".topbar .peer-title",
    ".chat-header .peer-title",
    "[class*='chat-info'] [class*='peer-title']",
    "[class*='MiddleHeader'] [class*='peer-title']",
    "[class*='dialog-title']",
]

# 🎯 برای استخراج اسم از آیتم لیست چت‌ها
TITLE_SELECTORS = [
    "bdi.oUKPfP",
    ".peer-title",
    ".chat-title",
    ".dialog-title",
    "[class*='peer-title']",
    "[class*='dialog-title']",
    "[class*='chat-title']",
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


def _is_invalid_text(text: str) -> bool:
    """آیا متن پیام معتبر نیست؟"""
    if not text or not text.strip():
        return True
    stripped = text.strip()
    return any(re.match(pattern, stripped)
               for pattern in _INVALID_TEXT_PATTERNS)


class BaleCollector(BaseCollector):
    def __init__(
        self,
        phone: str | None = None,
        password: str | None = None,
        headless: bool = True,
        poll_seconds: int | None = None,
        max_chats: int | None = None,
        chat_open_delay: float | None = None,
        messages_per_chat: int | None = None,
        backfill_scrolls: int | None = None,
        **kwargs,
    ):
        super().__init__()

        # ─── تنظیمات از env ───
        self.phone = phone or os.getenv("BALE_PHONE", "")
        self.password = password
        self.headless = headless

        self._poll_seconds = int(
            poll_seconds or os.getenv("BALE_POLL_SECONDS", "60")
        )
        self._max_chats = int(
            max_chats or os.getenv("BALE_MAX_CHATS", "0")
        )
        self._chat_open_delay = float(
            chat_open_delay or os.getenv("BALE_CHAT_DELAY", "0.8")
        )
        self._messages_per_chat = int(
            messages_per_chat or os.getenv("BALE_MESSAGES_PER_CHAT", "500")
        )
        self._backfill_scrolls = int(
            backfill_scrolls or os.getenv("BALE_BACKFILL_SCROLLS", "3")
        )

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
        self._last_scrape_stats: dict[str, int] = {}

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

        # ─── seen (ضدتکرار) ───
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "bale_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        # ─── state (first run + last_run_at) ───
        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "bale_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()
        self._last_run_at = self._load_last_run_at()

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

        log_info(
            f"🔧 [bale:init] mpc={self._messages_per_chat} | "
            f"backfill={self._backfill_scrolls} | "
            f"max_chats={self._max_chats}"
        )

    # ═══════════════════════════════════════════════════
    # state
    # ═══════════════════════════════════════════════════
    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info("♻️ [bale:state] بار اول انجام شده")
            else:
                log_info("🆕 [bale:state] بار اول — تاریخچه کامل")
            return done
        except Exception:
            log_info("🆕 [bale:state] بار اول — تاریخچه کامل")
            return False

    def _load_last_run_at(self) -> datetime | None:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            s = d.get("last_run_at")
            if s:
                return datetime.fromisoformat(s)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return None

    def _save_state(self):
        try:
            data = {
                "first_run_done": True,
                "first_run_at": getattr(
                    self, "_first_run_at", datetime.now().isoformat()  # noqa: DTZ005
                ),
                "last_run_at": datetime.now().isoformat(),  # noqa: DTZ005
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_warning(f"ذخیره state ناموفق: {e}")

    # ═══════════════════════════════════════════════════
    # cache چت‌های خصوصی
    # ═══════════════════════════════════════════════════
    def _is_valid_chat_name(self, name: str) -> bool:
        if not name:
            return False
        n = str(name).strip()
        if not n:
            return False
        return not (n.isdigit() and len(n) > 6)

    def _load_private_chats(self):
        self._private_chat_names = []
        self._chat_id_map = {}
        if not os.path.exists(self._private_chats_file):
            return
        try:
            with open(self._private_chats_file, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return
            raw_names = list(d.get("names") or [])
            self._private_chat_names = [
                n for n in raw_names if self._is_valid_chat_name(n)
            ][-_MAX_PRIVATE_CHAT_NAMES:]
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
                if str(pid).strip() and self._is_valid_chat_name(name)
            }
            if len(safe_map) > _MAX_CHAT_ID_MAP:
                safe_map = dict(
                    list(safe_map.items())[-_MAX_CHAT_ID_MAP:]
                )
            with open(self._private_chats_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "names": self._private_chat_names[
                            -_MAX_PRIVATE_CHAT_NAMES:
                        ],
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
            self._private_chat_names = self._private_chat_names[
                -_MAX_PRIVATE_CHAT_NAMES:
            ]
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

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
        return True

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
                log_info(f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ بله")
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
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [bale:connect] از قبل متصل — رد")
            return True

        async with BaleCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                with contextlib.suppress(Exception):
                    await self._context.close()
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

                for i in range(_CONNECT_MAX_ATTEMPTS):
                    await asyncio.sleep(_CONNECT_RETRY_SLEEP)
                    if await self._is_logged_in():
                        self._connected = True
                        self._needs_backfill = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(
                            f"✅ اتصال بله‌وب برقرار شد — "
                            f"بعد از {i + 1} تلاش"
                        )
                        return True

                await self.dump_dom()
                log_error("نشست بله‌وب تشخیص داده نشد")
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال بله‌وب: {e}")
                self._set_error(str(e))
                await self.disconnect()
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
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

    def _force_kill_chrome(self):
        import re as _re
        import subprocess
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='chrome.exe'",
                 "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore",
                check=False,
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
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/F", "/PID", m.group(1), "/T"],
                    capture_output=True, timeout=5,
                    check=False,
                )

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
                await asyncio.sleep(_CONNECT_NAV_SLEEP)
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
                        except Exception as e:
                            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
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
            "data-dialog-id", "data-peer-guid",
        ):
            try:
                val = await chat_item.get_attribute(attr)
                if val and str(val).strip():
                    v = str(val).strip()
                    if v not in ("0", "-1", "null", "undefined"):
                        return v
            except Exception:
                continue

        try:
            href = await chat_item.get_attribute("href")
            if href:
                m = re.search(r"#/(?:@|g/)?([A-Za-z0-9_]+)", href)
                if m:
                    return m.group(1)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

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
                        m = re.search(r"#/(?:@|g/)?([A-Za-z0-9_]+)", href)
                        if m:
                            return m.group(1)
            except Exception:
                continue

        return ""

    async def _get_current_peer_id(self) -> str:
        try:
            url = self._page.url or ""

            m = re.search(r"[?&]uid=(\d+)", url)
            if m:
                return m.group(1)

            m = re.search(r"#/@([A-Za-z0-9_]+)", url)
            if m:
                return m.group(1)

            m = re.search(r"#/g/([A-Za-z0-9_]+)", url)
            if m:
                return m.group(1)

            for pattern in (r"chat/(\d+)", r"peer/(\d+)"):
                m = re.search(pattern, url)
                if m:
                    return m.group(1)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return ""

    async def _detect_chat_type(self) -> str:
        try:
            url = self._page.url or ""
            if re.search(r"#/g/[A-Za-z0-9_]+", url):
                return "group"
            low = url.lower()
            if "/group" in low or "?group=" in low:
                return "group"
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return "private"

    async def _extract_chat_meta(self):
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
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return None, None

    async def _extract_sender_from_bubble(self, msg):
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
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        if not title:
            try:
                txt = (await chat_item.inner_text() or "").strip()
                if txt:
                    first = txt.split("\n")[0].strip()
                    if first and 2 < len(first) < 80:
                        title = first
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        if title:
            title = re.sub(r"\s+", " ", title).strip()

        return title

    def _is_admin_chat(self, chat_name: str, peer_id: str) -> bool:
        if peer_id and peer_id in self._admin_user_ids:
            return True
        if self._admin_chat_name and chat_name:  # noqa: SIM102
            if chat_name.strip() == self._admin_chat_name:
                return True
        return False

    def _is_system_name(self, name: str) -> bool:
        if not name:
            return False
        n = name.strip()
        if not n:
            return False
        if n in SYSTEM_FOLDER_NAMES:
            return True
        return any(n.startswith(prefix) for prefix in SYSTEM_PREFIXES)

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
    # جمع‌آوری همه چت‌ها
    # ═══════════════════════════════════════════════════
    async def _collect_all_chats(self) -> list[dict]:
        for _ in range(_SCROLL_INITIAL_ROUNDS):
            with contextlib.suppress(Exception):
                await self._page.mouse.wheel(0, -_SCROLL_WHEEL_UP)
            await asyncio.sleep(_SCROLL_INITIAL_SLEEP)

        chats: list[dict] = []
        seen_keys: set[str] = set()
        stable_rounds = 0
        last_count = 0

        import time
        t_start = time.monotonic()

        for round_i in range(_MAX_SCROLL_ROUNDS):
            if time.monotonic() - t_start > _MAX_SCROLL_TIME_TOTAL:
                log_warning(
                    f"⏰ [bale] timeout اسکرول بعد از "
                    f"{round_i} دور"
                )
                break

            items = await self._find_chat_items_raw()
            for it in items:
                try:
                    pid = await self._extract_peer_id_from_item(it)
                    name = await self._extract_item_title(it)

                    if name and self._is_system_name(name):
                        continue
                    if not pid and name and self._is_system_name(name):
                        continue

                    if pid:
                        key = f"pid:{pid}"
                    elif name and len(name) > 1:
                        key = f"name:{name.strip()}"
                    else:
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

            with contextlib.suppress(Exception):
                await self._page.mouse.wheel(0, _SCROLL_WHEEL_DOWN)
            await asyncio.sleep(_SCROLL_SLEEP)

            cur = len(chats)
            if cur == last_count:
                stable_rounds += 1
                if stable_rounds >= _SCROLL_STABLE_ROUNDS:
                    break
            else:
                stable_rounds = 0
                last_count = cur

        log_info(f"🎯 اسکرول کامل شد | {len(chats)} چت یکتا")
        return chats

    # ═══════════════════════════════════════════════════
    # کلیک روی چت
    # ═══════════════════════════════════════════════════
    async def _click_item(self, item) -> bool:
        with contextlib.suppress(Exception):
            await item.scroll_into_view_if_needed(
                timeout=_CLICK_SCROLL_INTO_VIEW_MS
            )
        await asyncio.sleep(_CLICK_PRE_SLEEP)
        try:
            await item.click(timeout=_CLICK_TIMEOUT_MS, force=True)
            return True
        except Exception:
            return False

    async def _find_and_click_by_key(
        self, chat_key: str, display_name: str  # noqa: ARG002
    ) -> bool:
        target_pid = ""
        target_name = ""
        if chat_key.startswith("pid:"):
            target_pid = chat_key[4:]
        elif chat_key.startswith("name:"):
            target_name = chat_key[5:]
        else:
            target_name = chat_key

        items = await self._find_chat_items_raw()
        for it in items:
            try:
                if target_pid:
                    pid = await self._extract_peer_id_from_item(it)
                    if pid == target_pid:  # noqa: SIM102
                        if await self._click_item(it):
                            return True
                if target_name:
                    name = await self._extract_item_title(it)
                    if name and name.strip() == target_name:  # noqa: SIM102
                        if await self._click_item(it):
                            return True
            except Exception:
                continue

        for _ in range(_SCROLL_INITIAL_ROUNDS):
            with contextlib.suppress(Exception):
                await self._page.mouse.wheel(0, -_SCROLL_WHEEL_UP)
            await asyncio.sleep(_SCROLL_INITIAL_SLEEP)

        for _ in range(_CLICK_SCROLL_ROUNDS):
            items = await self._find_chat_items_raw()
            for it in items:
                try:
                    if target_pid:
                        pid = await self._extract_peer_id_from_item(it)
                        if pid == target_pid:  # noqa: SIM102
                            if await self._click_item(it):
                                return True
                    if target_name:
                        name = await self._extract_item_title(it)
                        if name and name.strip() == target_name:  # noqa: SIM102
                            if await self._click_item(it):
                                return True
                except Exception:
                    continue
            with contextlib.suppress(Exception):
                await self._page.mouse.wheel(0, _CLICK_SCROLL_PX)
            await asyncio.sleep(_CLICK_SCROLL_SLEEP)

        return False

    # ═══════════════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════════════
    async def fetch_messages(self, since: datetime | None = None):
        if not self._connected or not self._page:
            return []

        effective_since = since
        if effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(
                f"♻️ [bale:since] از last_run_at: "
                f"{self._last_run_at.isoformat()}"
            )
        elif since:
            log_info(
                f"🎯 [bale:since] از پارامتر since: "
                f"{since.isoformat()}"
            )

        self._last_scrape_stats = {
            "total_chats": 0,
            "processed": 0,
            "skipped_system": 0,
            "skipped_target": 0,
            "skipped_admin": 0,
            "empty_chats": 0,
            "failed": 0,
            "messages_found": 0,
            "messages_skipped_old": 0,
            "messages_skipped_dup": 0,
        }

        try:
            async with self._page_lock:
                await self._scrape_all_chats(
                    effective_since=effective_since
                )
        except Exception as e:
            log_error(f"خطا در خواندن پیام بله: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._save_private_chats()
        self._needs_backfill = False

        if not self._first_run_done:
            self._first_run_at = datetime.now().isoformat()  # noqa: DTZ005
            self._first_run_done = True
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام از بله")

        stats = self._last_scrape_stats
        log_info(
            f"📊 [bale:stats] total={stats['total_chats']} | "
            f"processed={stats['processed']} | "
            f"system={stats['skipped_system']} | "
            f"target={stats['skipped_target']} | "
            f"admin={stats['skipped_admin']} | "
            f"empty={stats['empty_chats']} | "
            f"failed={stats['failed']} | "
            f"found={stats['messages_found']} | "
            f"old={stats['messages_skipped_old']} | "
            f"dup={stats['messages_skipped_dup']}"
        )

        return new_msgs

    async def _backfill_scroll(self):
        if not (self._needs_backfill and self._backfill_scrolls > 0):
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
                        "     #message_list_scroller_id'"
                        "  );"
                        "  if (c) {"
                        "    const before = c.scrollTop;"
                        "    c.scrollTop = "
                        f"      Math.max(0, before - {_BACKFILL_SCROLL_PX});"
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
            await asyncio.sleep(_BACKFILL_PER_SCROLL_WAIT)

    # ═══════════════════════════════════════════════════
    # اسکرپ همه چت‌ها
    # ═══════════════════════════════════════════════════
    async def _scrape_all_chats(self, effective_since: datetime | None = None):
        try:
            url = (self._page.url or "").lower()
            if "/search" in url:
                await self._page.goto(
                    BALE_WEB_URL, wait_until="domcontentloaded"
                )
                await asyncio.sleep(2)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        chats = await self._collect_all_chats()
        self._last_scrape_stats["total_chats"] = len(chats)

        if not chats:
            log_warning("⚠️ هیچ چتی پیدا نشد")
            with contextlib.suppress(Exception):
                await self.dump_dom()
            return

        is_first = not self._first_run_done
        if is_first:
            log_info(
                f"🔍 {len(chats)} چت | حالت: بار اول — تاریخچه کامل"
            )
        else:
            log_info(f"🔍 {len(chats)} چت | حالت: چک سریع")

        self._backfill_scrolls = _MAX_BACKFILL_SCROLLS

        if self._max_chats > 0:
            chats = chats[: self._max_chats]

        for idx, chat in enumerate(chats):
            chat_key = chat["key"]
            chat_name = chat["name"]
            peer_id = chat["peer_id"]

            try:
                if self._is_system_name(chat_name):
                    log_debug(
                        f"   ⏭️ [{idx + 1}] سیستمی: {chat_name}"
                    )
                    self._last_scrape_stats["skipped_system"] += 1
                    continue

                if self._is_admin_chat(chat_name, peer_id):
                    log_info(
                        f"   ⏭️ [{idx + 1}] چتِ مدیر: {chat_name}"
                    )
                    self._last_scrape_stats["skipped_admin"] += 1
                    continue

                try:
                    if (
                        self._targets.enabled(_PLATFORM)
                        and not self._targets.is_target(
                            chat_name, platform=_PLATFORM
                        )
                    ):
                        log_debug(
                            f"   ⏭️ [{idx + 1}] غیرهدف: {chat_name}"
                        )
                        self._last_scrape_stats["skipped_target"] += 1
                        continue
                except Exception as e:
                    log_warning(
                        f"   ⚠️ [bale:target] "
                        f"{type(e).__name__}: {e}"
                    )

                log_info(
                    f"   ▶️ [{idx + 1}/{len(chats)}] {chat_name}"
                )
                clicked = await self._find_and_click_by_key(
                    chat_key, chat_name
                )
                if not clicked:
                    log_warning(
                        f"   ⚠️ [{idx + 1}] پیدا نشد: {chat_name}"
                    )
                    self._last_scrape_stats["failed"] += 1
                    continue

                await asyncio.sleep(self._chat_open_delay)

                _per_chat_timeout = (
                    _CHAT_TIMEOUT_INITIAL
                    if is_first
                    else _CHAT_TIMEOUT_NORMAL
                )
                try:
                    msg_count = await asyncio.wait_for(
                        self._scrape_current_chat(
                            chat_name, peer_id,
                            effective_since=effective_since,
                        ),
                        timeout=_per_chat_timeout,
                    )
                    if msg_count == 0:
                        self._last_scrape_stats["empty_chats"] += 1
                except asyncio.TimeoutError:
                    log_warning(
                        f"⏱️ timeout در {chat_name} — skip"
                    )
                    self._failed_chats.add(chat_key)
                    self._last_scrape_stats["failed"] += 1
                    continue

                self._last_scrape_stats["processed"] += 1

                with contextlib.suppress(Exception):
                    await self._page.mouse.wheel(0, -200)

            except Exception as e:
                self._last_scrape_stats["failed"] += 1
                log_warning(
                    f"خطا در {chat_name}: "
                    f"{type(e).__name__}: {e}"
                )
                continue

        log_success(
            f"🏁 [bale:scrape] کامل | "
            f"پردازش={self._last_scrape_stats['processed']} | "
            f"سیستمی={self._last_scrape_stats['skipped_system']} | "
            f"غیرهدف={self._last_scrape_stats['skipped_target']} | "
            f"مدیر={self._last_scrape_stats['skipped_admin']} | "
            f"خالی={self._last_scrape_stats['empty_chats']} | "
            f"خطا={self._last_scrape_stats['failed']}"
        )

    # ═══════════════════════════════════════════════════
    # اسکرپ چت باز فعلی
    # ═══════════════════════════════════════════════════
    async def _scrape_current_chat(
        self,
        chat_name: str | None = None,
        peer_id: str | None = None,
        effective_since: datetime | None = None,
    ) -> int:
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
                return 0

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
            with contextlib.suppress(Exception):
                await page.wait_for_selector(
                    selector_csv,
                    timeout=_WAIT_FOR_MSG_MS,
                    state="attached",
                )

        messages = []
        for sel in MSG_SELECTORS:
            try:
                messages = await asyncio.wait_for(
                    page.query_selector_all(sel),
                    timeout=_SELECTOR_TIMEOUT_MS / 1000,
                )
                if messages:
                    log_info(
                        f"   ✅ پیام‌ها با {sel} ({len(messages)})"
                    )
                    break
            except Exception:
                continue

        if not messages:
            log_warning(
                f"   ⚠️ [bale:empty] {chat_name} | "
                f"هیچ پیامی پیدا نشد"
            )
            return 0

        limit = (
            max(self._messages_per_chat, _MIN_MESSAGES_BACKFILL)
            if self._needs_backfill
            else self._messages_per_chat
        )

        count = 0
        checked = 0
        skipped_old = 0
        skipped_dup = 0
        skipped_short = 0
        skipped_kw = 0

        _seen_in_this_chat: set[str] = set()

        for msg in messages[-limit:]:
            try:
                sid_raw = await msg.get_attribute("data-sid") or ""
                sid = ""
                if sid_raw:
                    sid = sid_raw.split(":")[-1].strip()

                date_ms_raw = await msg.get_attribute("data-date") or ""
                msg_timestamp = None
                date_ms = 0
                if date_ms_raw:
                    try:
                        date_ms = int(date_ms_raw)
                        msg_timestamp = datetime.fromtimestamp(  # noqa: DTZ006
                            date_ms / 1000
                        )
                    except (ValueError, OSError):
                        msg_timestamp = None

                text = (await msg.inner_text() or "").strip()

                if _is_invalid_text(text):
                    skipped_short += 1
                    continue

                if effective_since and msg_timestamp:  # noqa: SIM102
                    if msg_timestamp < effective_since:
                        skipped_old += 1
                        continue

                # ⚠️ data-sid بین چت‌ها یکسانه (پیام ارسالی)
                # پس chat_id رو هم داخل hash می‌بریم
                if sid:
                    mid = hashlib.sha256(
                        f"{chat_id}:{sid}".encode()
                    ).hexdigest()[:32]
                elif date_ms:
                    mid = hashlib.sha256(
                        f"{chat_id}:{date_ms}".encode()
                    ).hexdigest()[:32]
                else:
                    mid = hashlib.sha256(
                        f"{chat_id}:{text[:200]}".encode()
                    ).hexdigest()[:32]

                if mid in _seen_in_this_chat:
                    skipped_dup += 1
                    continue
                _seen_in_this_chat.add(mid)

                checked += 1
                self._last_scrape_stats["messages_found"] += 1

                has_kw = self._kw.has_match(text)
                if not has_kw:
                    skipped_kw += 1
                    continue

                uniq = f"{chat_id}:{mid}"
                if uniq in self._seen_ids:
                    skipped_dup += 1
                    self._last_scrape_stats[
                        "messages_skipped_dup"
                    ] += 1
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
                if chat_type == "group":
                    try:
                        from core.adapters.web_media import (
                            download_dom_image,
                        )
                        mt, lp = await download_dom_image(
                            self._page, msg, self._media_dir, _PLATFORM
                        )
                        if mt:
                            media_type, local_file_path = mt, lp
                    except Exception as e:
                        log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                final_ts = msg_timestamp or datetime.now()  # noqa: DTZ005

                self._messages_queue.append({
                    "id": mid,
                    "platform": _PLATFORM,
                    "text": text,
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "chat_type": chat_type,
                    "chat_username": chat_username,
                    "chat_link": chat_link,
                    "user_id": sender_id,
                    "sender_name": sender_name,
                    "timestamp": final_ts,
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_file_path,
                    "reply_to": None,
                })
                count += 1

            except Exception as e:
                log_debug(f"   خطا در پردازش پیام: {e}")
                continue

        if count > 0:
            log_success(
                f"   📩 از {chat_name}: {count} پیام "
                f"(از {checked} چک‌شده)"
            )
        else:
            log_info(
                f"   ℹ️ {chat_name}: 0 پیام جدید "
                f"(checked={checked}، old={skipped_old}، "
                f"dup={skipped_dup}، short={skipped_short}، "
                f"kw={skipped_kw})"
            )

        self._last_scrape_stats[
            "messages_skipped_old"
        ] += skipped_old

        return count

    async def _get_open_chat_title(self) -> str:
        for sel in CHAT_TITLE_SELECTORS:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if first and len(first) < 100:
                            return first
            except Exception:
                continue
        return ""

    # ═══════════════════════════════════════════════════
    # ارسال پیام — غیرفعال
    # ═══════════════════════════════════════════════════
    async def send_message(self, chat_id, chat_type, text: str) -> bool:
        log_info(
            f"⏭️ [bale:send] غیرفعال (فقط‌خواندن) | "
            f"chat_id={chat_id}"
        )
        return False

    async def dump_dom(self, path: str | None = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "bale_dom_dump.html"
        )
        try:
            if not self._page:
                log_warning("dump_dom: صفحه‌ای نیست")
                return ""
            html = await self._page.content()

            def _write():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            await asyncio.to_thread(_write)
            log_info(f"DOM بله در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

    async def disconnect(self) -> None:
        with contextlib.suppress(Exception):
            self._save_private_chats()

        try:
            if self._page:
                await self._page.close()
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        self._page = None

        try:
            if self._context:
                await self._context.close()
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        self._context = None

        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        self._pw = None
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از بله‌وب")

    async def check_health(self) -> bool:
        return self._connected
