# collectors/eitaa.py
"""
کلکتور ایتا (Eitaa) — نسخهٔ ۹.۰ (نهایی — فوق دقیق)

ویژگی‌ها:
  - 🎯 id پایدار از data-mid + chat_id (بدون تکرار)
  - 🎯 timestamp دقیق از data-timestamp (میلی‌ثانیه)
  - 🎯 since دقیق (فیلتر بر اساس data-timestamp)
  - 🎯 last_run_at — چند روز خاموش بود، از همون‌جا ادامه می‌ده
  - 🎯 چت‌های سیستمی کامل فیلتر می‌شن
  - 🎯 پیام بدون متن معتبر ذخیره نمی‌شه
  - 🎯 آمار تفکیکی (_last_scrape_stats)
  - 🎯 _seen_ids فقط بعد از keyword filter
  - 🎯 messages_per_chat از env واقعاً اعمال می‌شه
  - 🎯 تنظیمات از .env
"""
import asyncio
import hashlib
import json
import os
import random
import re
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from analyzers.target_filter import get_target_groups
from core.adapters.web_media import download_dom_image
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
EITAA_WEB_URL = "https://web.eitaa.com/"
_PLATFORM = "eitaa"

# ⏱️ timeoutها (از env قابل تنظیم)
_CHAT_TIMEOUT = 120
_SELECTOR_TIMEOUT_MS = 5000
_WAIT_FOR_MSG_MS = 5000
_MAX_SCROLL_ROUNDS = 60
_MAX_SCROLL_TIME_TOTAL = 90

# 🎭 تأخیرها
_DELAY_MIN = 0.2
_DELAY_MAX = 0.5
_CHAT_OPEN_DELAY_MIN = 1.5
_CHAT_OPEN_DELAY_MAX = 3.0

# 🎯 چت‌های سیستمی — کامل
_SYSTEM_NAMES = {
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده",
    "ذخیره‌شده", "ذخیره شده", "شما", "تصویر", "پیام",
    "Saved Messages", "Drafts", "You", "Images", "Messages",
    "ایتا", "Eitaa", "تیم ایتا", "پشتیبانی ایتا",
    "فایل", "فایل‌ها", "استیکر", "استیکرها", "گیف", "ویدیو",
    "ویس", "عکس", "لینک", "مخاطبین", "پوشه‌ها", "گروه‌ها",
    "کانال‌ها", "آلبوم",
}

_SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "Channel created", "Group created",
)

# 🎯 سلکتورها
_CHAT_SELECTOR = ".chatlist-chat"
_TITLE_SELECTORS = [
    ".peer-title",
    ".user-title",
    ".dialog-title",
    "[class*='peer-title']",
]
_MSG_SELECTORS = [
    ".bubbles .bubble",
    "#column-center .bubble",
    ".bubble",
]
_SENDER_SELECTORS = [
    ".peer-title",
    ".message-title",
    ".sender-name",
]

# 🎯 مارکرها
LOGIN_MARKERS = [
    'input[type="tel"]',
    ".input-field-phone",
]
CHATLIST_MARKERS = [
    ".chatlist-chat",
    ".chatlist",
    "#column-left",
]

# 🎯 کلاس‌های پیام media خالی
_EMPTY_MEDIA_CLASSES = (
    "just-media",
    "is-message-empty",
    "emoji-big",
    "can-have-big-emoji",
)

# 🎯 کاراکترهای PUA ایتا
_PUA_CHARS = "\ue900\ue901\ue902\ue903\ue904\ue905\ue906\ue907\ue908\ue909"

# 🎯 متن نامعتبر
_INVALID_TEXT_PATTERNS = [
    r"^\s*$",
    r"^\s*[\d۰-۹]{1,2}:[\d۰-۹]{2}\s*$",
    r"^\s*[\d۰-۹]+\s*$",
    r"^\s*[\d۰-۹]{1,2}:[\d۰-۹]{2}\s*\n\s*[\d۰-۹]+\s*$",
]


def _is_invalid_text(text: str) -> bool:
    """آیا متن پیام معتبر نیست؟"""
    if not text or not text.strip():
        return True
    stripped = text.strip()
    for pattern in _INVALID_TEXT_PATTERNS:
        if re.match(pattern, stripped):
            return True
    return False


def _clean_text(text: str) -> str:
    """پاک‌سازی متن پیام ایتا."""
    if not text:
        return ""

    for ch in _PUA_CHARS:
        text = text.replace(ch, "")
    text = re.sub(r'[\ue000-\uf8ff]', '', text)

    lines = [ln.rstrip() for ln in text.split('\n')]

    while lines:
        last = lines[-1].strip()

        if not last:
            lines.pop()
            continue

        if re.match(r'^[\d۰-۹]{1,2}:[\d۰-۹]{2}\s*$', last):
            lines.pop()
            continue

        if re.match(r'^[\d۰-۹]+\s*$', last):
            lines.pop()
            continue

        if re.match(
            r'^[\U0001F300-\U0001F9FF\U00002600-\U000027BF\s]+$',
            last
        ):
            lines.pop()
            continue

        cleaned = re.sub(
            r'\s*[\d۰-۹]{1,2}:[\d۰-۹]{2}\s*$', '', last
        ).strip()
        if not cleaned:
            lines.pop()
            continue
        if cleaned != last:
            lines[-1] = cleaned
        break

    while lines and not lines[-1].strip():
        lines.pop()

    return '\n'.join(lines).strip()


class EitaaCollector(BaseCollector):
    def __init__(
        self,
        phone: str = None,
        password: str = None,
        headless: bool = True,
        poll_seconds: int = None,
        max_chats: int = None,
        chat_open_delay: float = None,
        messages_per_chat: int = None,
        backfill_scrolls: int = None,
        **kwargs,
    ):
        super().__init__()

        # ─── تنظیمات از env ───
        self.phone = phone or os.getenv("EITAA_PHONE", "")
        self.password = password
        self.headless = headless

        self._poll_seconds = int(
            poll_seconds or os.getenv("EITAA_POLL_SECONDS", "180")
        )
        self._max_chats = int(
            max_chats or os.getenv("EITAA_MAX_CHATS", "0")
        )
        self._chat_open_delay = float(
            chat_open_delay or os.getenv("EITAA_CHAT_DELAY", "1.5")
        )
        self._messages_per_chat = int(
            messages_per_chat or os.getenv("EITAA_MESSAGES_PER_CHAT", "500")
        )
        self._backfill_scrolls = int(
            backfill_scrolls or os.getenv("EITAA_BACKFILL_SCROLLS", "10")
        )

        self._needs_backfill = True
        self._pw = None
        self._context = None
        self._page = None
        self._connected = False
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._targets = get_target_groups()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()
        self._stealth_applied = False
        self._last_scrape_stats: dict[str, int] = {}

        if not hasattr(EitaaCollector, "_connect_lock"):
            EitaaCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR
        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "eitaa_web"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "eitaa")
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "eitaa_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        # ─── state (first run + last_run_at) ───
        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "eitaa_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()
        self._last_run_at = self._load_last_run_at()

        log_info(
            f"🔧 [eitaa:init] mpc={self._messages_per_chat} | "
            f"backfill={self._backfill_scrolls} | "
            f"max_chats={self._max_chats}"
        )

    # ═══════════════════════════════════════════════
    # state
    # ═══════════════════════════════════════════════
    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info("♻️ [eitaa:state] بار اول انجام شده")
            else:
                log_info("🆕 [eitaa:state] بار اول — تاریخچه کامل")
            return done
        except Exception:
            log_info("🆕 [eitaa:state] بار اول — تاریخچه کامل")
            return False

    def _load_last_run_at(self) -> datetime | None:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            s = d.get("last_run_at")
            if s:
                return datetime.fromisoformat(s)
        except Exception:
            pass
        return None

    def _save_state(self):
        try:
            data = {
                "first_run_done": True,
                "first_run_at": getattr(
                    self, "_first_run_at", datetime.now().isoformat()
                ),
                "last_run_at": datetime.now().isoformat(),
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_warning(f"ذخیره state ناموفق: {e}")

    # ═══════════════════════════════════════════════
    # seen
    # ═══════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} شناسه دیده‌شده")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیره seen: {e}")

    # ═══════════════════════════════════════════════
    # خواص
    # ═══════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(self, headless: bool | None = None) -> bool:
        if self._connected and self._context:
            return True

        async with EitaaCollector._connect_lock:
            if self._connected and self._context:
                return True

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست")
                    return False

                use_headless = self.headless if headless is None else headless
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

                await self._apply_stealth()
                await self._page.goto(EITAA_WEB_URL, wait_until="domcontentloaded")

                for i in range(15):
                    await asyncio.sleep(1.5)
                    if await self._is_logged_in():
                        self._connected = True
                        self._needs_backfill = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(f"✅ اتصال ایتا بعد از {i+1} تلاش")
                        return True

                log_error("نشست ایتا تشخیص داده نشد")
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _apply_stealth(self):
        if self._stealth_applied:
            return
        try:
            try:
                from playwright_stealth import Stealth
                stealth = Stealth()
                await stealth.apply_stealth_async(self._page)
                log_success("🛡️ stealth فعال شد (v2.x)")
                self._stealth_applied = True
                return
            except (ImportError, AttributeError):
                pass
            try:
                from playwright_stealth import stealth_async
                await stealth_async(self._page)
                log_success("🛡️ stealth فعال شد (قدیمی)")
                self._stealth_applied = True
                return
            except (ImportError, AttributeError):
                pass
        except Exception as e:
            log_warning(f"stealth: {e}")

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
        launch_options: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "viewport": {"width": 1366, "height": 768},
            "user_agent": ua,
            "args": base_args,
            "locale": "fa-IR",
            "timezone_id": "Asia/Tehran",
        }
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_options["executable_path"] = CHROME_PATH
        else:
            launch_options["channel"] = "chrome"
        ctx = await self._pw.chromium.launch_persistent_context(**launch_options)
        log_info("مرورگر ایتا باز شد")
        return ctx

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

    # ═══════════════════════════════════════════════
    # تأخیر متغیر
    # ═══════════════════════════════════════════════
    async def _human_delay(self, min_s: float = None, max_s: float = None):
        min_s = min_s or _DELAY_MIN
        max_s = max_s or _DELAY_MAX
        await asyncio.sleep(random.uniform(min_s, max_s))

    # ═══════════════════════════════════════════════
    # استخراج‌ها
    # ═══════════════════════════════════════════════
    async def _extract_peer_id(self, item) -> str:
        try:
            pid = await item.get_attribute("data-peer-id")
            if pid and str(pid).strip():
                return str(pid).strip()
        except Exception:
            pass
        try:
            href = await item.get_attribute("href") or ""
            m = re.search(r"#(\d+)", href)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    async def _extract_title(self, item) -> str:
        for sel in _TITLE_SELECTORS:
            try:
                el = await item.query_selector(sel)
                if el:
                    t = (await el.inner_text() or "").strip()
                    if t:
                        first = t.split("\n")[0].strip()
                        if first and len(first) < 80:
                            return first
            except Exception:
                continue
        return ""

    async def _extract_sender(self, msg, chat_name: str) -> str:
        try:
            cls = await msg.get_attribute("class") or ""

            if "is-out" in cls:
                return chat_name

            if "is-group" not in cls and "group" not in cls:
                return chat_name

            for sel in _SENDER_SELECTORS:
                try:
                    el = await msg.query_selector(sel)
                    if el:
                        t = (await el.inner_text() or "").strip()
                        if t:
                            first = t.split("\n")[0].strip()
                            if first and len(first) < 60:
                                return first
                except Exception:
                    continue
        except Exception:
            pass

        return chat_name

    def _is_system_name(self, name: str) -> bool:
        if not name:
            return False
        n = name.strip()
        if not n:
            return False
        if n in _SYSTEM_NAMES:
            return True
        for prefix in _SYSTEM_PREFIXES:
            if n.startswith(prefix):
                return True
        return False

    # ═══════════════════════════════════════════════
    # جمع‌آوری چت‌ها
    # ═══════════════════════════════════════════════
    async def _collect_chats(self) -> list[dict]:
        # ۱) برگرد به بالای لیست
        for _ in range(50):
            try:
                await self._page.mouse.wheel(0, -3000)
            except Exception:
                pass
            await asyncio.sleep(0.1)

        chats = []
        seen_keys = set()
        last_count = 0
        stable_rounds = 0

        import time
        t_start = time.monotonic()

        for round_i in range(_MAX_SCROLL_ROUNDS):
            if time.monotonic() - t_start > _MAX_SCROLL_TIME_TOTAL:
                log_warning(f"⏰ [eitaa] timeout اسکرول")
                break

            items = await self._page.query_selector_all(_CHAT_SELECTOR)
            for it in items:
                try:
                    pid = await self._extract_peer_id(it)
                    name = await self._extract_title(it)
                    if not name or self._is_system_name(name):
                        continue
                    key = f"pid:{pid}" if pid else f"name:{name}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    chats.append({
                        "key": key,
                        "peer_id": pid,
                        "name": name,
                    })
                except Exception:
                    continue

            try:
                await self._page.mouse.wheel(0, 2500)
            except Exception:
                pass
            await self._human_delay(0.3, 0.7)

            cur = len(chats)
            if cur == last_count:
                stable_rounds += 1
                if stable_rounds >= 4:
                    break
            else:
                stable_rounds = 0
                last_count = cur

        log_info(f"🎯 {len(chats)} چت یکتا")
        return chats

    # ═══════════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════════
    async def fetch_messages(self, since: datetime = None) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        effective_since = since
        if effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(
                f"♻️ [eitaa:since] از last_run_at: "
                f"{self._last_run_at.isoformat()}"
            )
        elif since:
            log_info(
                f"🎯 [eitaa:since] از پارامتر since: "
                f"{since.isoformat()}"
            )

        self._last_scrape_stats = {
            "total_chats": 0,
            "processed": 0,
            "skipped_system": 0,
            "skipped_target": 0,
            "empty_chats": 0,
            "failed": 0,
            "messages_found": 0,
            "messages_skipped_old": 0,
            "messages_skipped_dup": 0,
        }

        chats = await self._collect_chats()
        self._last_scrape_stats["total_chats"] = len(chats)

        if not chats:
            log_warning("⚠️ هیچ چتی پیدا نشد")
            return []

        if self._max_chats > 0:
            chats = chats[:self._max_chats]

        log_info(f"⚡ {len(chats)} چت (سریالی)")

        for idx, chat in enumerate(chats):
            try:
                await self._scrape_chat(chat, idx, effective_since)
                await self._human_delay(0.3, 0.6)
            except Exception as e:
                log_warning(f"[{idx+1}] {chat.get('name')}: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._needs_backfill = False

        if not self._first_run_done:
            self._first_run_at = datetime.now().isoformat()
            self._first_run_done = True
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام از ایتا")

        stats = self._last_scrape_stats
        log_info(
            f"📊 [eitaa:stats] total={stats['total_chats']} | "
            f"processed={stats['processed']} | "
            f"system={stats['skipped_system']} | "
            f"target={stats['skipped_target']} | "
            f"empty={stats['empty_chats']} | "
            f"failed={stats['failed']} | "
            f"found={stats['messages_found']} | "
            f"old={stats['messages_skipped_old']} | "
            f"dup={stats['messages_skipped_dup']}"
        )

        return new_msgs

    # ═══════════════════════════════════════════════
    # اسکرپ یک چت
    # ═══════════════════════════════════════════════
    async def _scrape_chat(self, chat: dict, idx: int, effective_since: datetime = None):
        peer_id = chat["peer_id"]
        name = chat["name"]

        if not name or self._is_system_name(name):
            self._last_scrape_stats["skipped_system"] += 1
            return

        try:
            if (self._targets.enabled(_PLATFORM) and
                not self._targets.is_target(name, platform=_PLATFORM)):
                log_debug(f"⏭️ [{idx+1}] غیرهدف: {name}")
                self._last_scrape_stats["skipped_target"] += 1
                return
        except Exception:
            pass

        log_info(f"▶️ [{idx+1}] {name}")

        if not await self._click_chat(peer_id, name):
            log_warning(f"⏭️ [{idx+1}] کلیک نشد: {name}")
            self._last_scrape_stats["failed"] += 1
            return

        try:
            await self._page.wait_for_selector(
                ".bubbles .bubble",
                timeout=_WAIT_FOR_MSG_MS,
                state="attached",
            )
        except Exception:
            pass

        await self._human_delay(
            _CHAT_OPEN_DELAY_MIN, _CHAT_OPEN_DELAY_MAX
        )

        try:
            count = await asyncio.wait_for(
                self._scrape_current(name, peer_id, effective_since),
                timeout=_CHAT_TIMEOUT,
            )
            if count == 0:
                self._last_scrape_stats["empty_chats"] += 1
            self._last_scrape_stats["processed"] += 1
        except asyncio.TimeoutError:
            log_warning(f"⏱️ [{idx+1}] timeout: {name}")
            self._last_scrape_stats["failed"] += 1

    async def _click_chat(self, peer_id: str, name: str) -> bool:
        if peer_id:
            try:
                sel = f'.chatlist-chat[data-peer-id="{peer_id}"]'
                loc = self._page.locator(sel).first
                cnt = await loc.count()
                if cnt > 0:
                    try:
                        await loc.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    await loc.click(timeout=3000, force=True)
                    return True
            except Exception as e:
                log_debug(f"   کلیک با peer_id نشد: {e}")

        for attempt in range(30):
            try:
                items = await self._page.query_selector_all(_CHAT_SELECTOR)
                for it in items:
                    try:
                        if peer_id:
                            pid = await self._extract_peer_id(it)
                            if pid == peer_id:
                                try:
                                    await it.scroll_into_view_if_needed(timeout=1000)
                                except Exception:
                                    pass
                                await it.click(timeout=3000, force=True)
                                return True
                        n = await self._extract_title(it)
                        if n and n.strip() == name:
                            try:
                                await it.scroll_into_view_if_needed(timeout=1000)
                            except Exception:
                                pass
                            await it.click(timeout=3000, force=True)
                            return True
                    except Exception:
                        continue

                await self._page.mouse.wheel(0, 400)
                await asyncio.sleep(0.15)
            except Exception as e:
                log_debug(f"   تلاش {attempt}: {e}")
                continue

        return False

    async def _backfill_scroll(self):
        if not (self._needs_backfill and self._backfill_scrolls > 0):
            return
        for _ in range(self._backfill_scrolls):
            try:
                await self._page.evaluate(
                    "() => {"
                    "  const c = document.querySelector("
                    "    '.bubbles, .scrollable-y, #column-center .scrollable'"
                    "  );"
                    "  if (c) c.scrollTop = Math.max(0, c.scrollTop - 2500);"
                    "}"
                )
            except Exception:
                break
            await asyncio.sleep(0.7)

    # ═══════════════════════════════════════════════
    # اسکرپ چت باز فعلی — نسخه دقیق
    # ═══════════════════════════════════════════════
    async def _scrape_current(
        self,
        name: str,
        peer_id: str,
        effective_since: datetime = None,
    ) -> int:
        page = self._page
        chat_id = peer_id if peer_id else name

        await self._backfill_scroll()

        messages = []
        used_sel = None
        for sel in _MSG_SELECTORS:
            try:
                messages = await asyncio.wait_for(
                    page.query_selector_all(sel),
                    timeout=_SELECTOR_TIMEOUT_MS / 1000,
                )
                if messages:
                    used_sel = sel
                    break
            except Exception:
                continue

        if not messages:
            log_debug(f"   ⚠️ [{name}] هیچ پیامی نبود")
            return 0

        total_bubbles = len(messages)
        log_info(f"   📨 {name}: {total_bubbles} پیام یافت ({used_sel})")

        limit = (
            max(self._messages_per_chat, 300)
            if self._needs_backfill
            else self._messages_per_chat
        )

        count = 0
        skipped_service = 0
        skipped_empty_media = 0
        skipped_nomid = 0
        skipped_kw = 0
        skipped_seen = 0
        skipped_short = 0
        skipped_old = 0
        skipped_dup = 0

        _seen_in_this_chat: set[str] = set()

        for msg in messages[-limit:]:
            try:
                cls = await msg.get_attribute("class") or ""

                # ─── ① skip service/date ───
                if "service" in cls or "is-date" in cls:
                    skipped_service += 1
                    continue

                # ─── ② skip پیام‌های media خالی ───
                is_empty_media = any(
                    marker in cls for marker in _EMPTY_MEDIA_CLASSES
                )
                if is_empty_media:
                    skipped_empty_media += 1
                    continue

                # ─── ③ data-mid (از والد بگیر!) ───
                mid = await msg.get_attribute("data-mid") or ""
                if not mid:
                    try:
                        parent = await msg.evaluate_handle("el => el.parentElement")
                        if parent:
                            mid = await parent.get_attribute("data-mid") or ""
                    except Exception:
                        pass

                # ─── ④ data-timestamp (از والد بگیر!) ───
                msg_timestamp = None
                date_ms = 0
                try:
                    parent = await msg.evaluate_handle("el => el.parentElement")
                    if parent:
                        ts_raw = await parent.get_attribute("data-timestamp") or ""
                        if ts_raw:
                            try:
                                ts_val = int(ts_raw)
                                if ts_val > 10_000_000_000:
                                    msg_timestamp = datetime.fromtimestamp(ts_val / 1000)
                                else:
                                    msg_timestamp = datetime.fromtimestamp(ts_val)
                                date_ms = ts_val
                            except (ValueError, OSError):
                                pass
                except Exception:
                    pass

                # ─── ⑤ متن خام ───
                raw_text = (await msg.inner_text() or "").strip()
                if not raw_text:
                    continue

                # ─── ⑥ پاک‌سازی ───
                text = _clean_text(raw_text)
                if not text:
                    continue

                if _is_invalid_text(text):
                    skipped_short += 1
                    continue

                # ─── ⑦ since filter ───
                if effective_since and msg_timestamp:
                    if msg_timestamp < effective_since:
                        skipped_old += 1
                        continue

                # ─── ⑧ id پایدار ───
                if mid:
                    mid_final = hashlib.sha256(
                        f"{chat_id}:{mid}".encode("utf-8")
                    ).hexdigest()[:32]
                elif date_ms:
                    mid_final = hashlib.sha256(
                        f"{chat_id}:{date_ms}".encode("utf-8")
                    ).hexdigest()[:32]
                    skipped_nomid += 1
                else:
                    mid_final = hashlib.sha256(
                        f"{chat_id}:{text[:200]}".encode("utf-8")
                    ).hexdigest()[:32]
                    skipped_nomid += 1

                if mid_final in _seen_in_this_chat:
                    skipped_dup += 1
                    continue
                _seen_in_this_chat.add(mid_final)

                self._last_scrape_stats["messages_found"] += 1

                # ─── ⑨ فیلتر کلیدواژه ───
                if not self._kw.has_match(text):
                    skipped_kw += 1
                    continue

                # ─── ⑩ ضدتکرار سراسری ───
                uniq = f"{chat_id}:{mid_final}"
                if uniq in self._seen_ids:
                    skipped_seen += 1
                    self._last_scrape_stats["messages_skipped_dup"] += 1
                    continue
                self._seen_ids.add(uniq)

                # ─── ⑪ sender ───
                sender = await self._extract_sender(msg, name)

                # ─── ⑫ chat_type ───
                chat_type = "private"
                if "is-group" in cls:
                    chat_type = "group"
                elif "is-channel" in cls or "channel" in cls.lower():
                    chat_type = "channel"

                # ─── ⑬ media ───
                media_type, local_file_path = "text", None
                try:
                    mt, lp = await download_dom_image(
                        self._page, msg, self._media_dir, _PLATFORM
                    )
                    if mt:
                        media_type, local_file_path = mt, lp
                except Exception:
                    pass

                # ─── ⑭ timestamp نهایی ───
                final_ts = msg_timestamp or datetime.now()

                # ─── ⑮ اضافه به صف ───
                self._messages_queue.append({
                    "id": mid_final,
                    "platform": _PLATFORM,
                    "text": text,
                    "chat_id": chat_id,
                    "chat_name": name,
                    "chat_type": chat_type,
                    "chat_username": None,
                    "chat_link": None,
                    "user_id": "",
                    "sender_name": sender,
                    "timestamp": final_ts,
                    "media_type": media_type,
                    "file_id": None,
                    "local_file_path": local_file_path,
                    "reply_to": None,
                })
                count += 1
                log_info(f"   📩 [{name}] ({sender}) {text[:60]}")

            except Exception as e:
                log_debug(f"   خطا در پردازش پیام: {e}")
                continue

        # ─── گزارش نهایی ───
        if count > 0:
            log_success(
                f"   📩 از {name}: {count} پیام "
                f"(از {total_bubbles} چک‌شده)"
            )
        else:
            log_info(
                f"   ℹ️ {name}: 0 پیام جدید "
                f"(service={skipped_service}، "
                f"empty-media={skipped_empty_media}، "
                f"short={skipped_short}، "
                f"nomid→hash={skipped_nomid}، "
                f"seen={skipped_seen}، "
                f"kw-skip={skipped_kw}، "
                f"old={skipped_old}، "
                f"dup={skipped_dup})"
            )

        self._last_scrape_stats["messages_skipped_old"] += skipped_old

        return count

    # ═══════════════════════════════════════════════
    # ابزار
    # ═══════════════════════════════════════════════
    async def send_message(self, chat_id, chat_type, text: str) -> bool:
        return False

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(self._user_data_dir, "eitaa_dom.html")
        try:
            if not self._page:
                return ""
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            return path
        except Exception:
            return ""

    async def disconnect(self) -> None:
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
        log_info("🔌 قطع ارتباط از ایتا")

    async def check_health(self) -> bool:
        return self._connected