# collectors/telegram.py
"""
کلکتور تلگرام (Telegram Web k) — نسخهٔ ۳.۱ (نهایی — فوق دقیق)

تغییرات نسخهٔ ۳.۱:
  - 🎯 FIX: کلیک force=True (بدون چک visibility)
  - 🎯 FIX: _find_chat_items با bounding_box (نه is_visible)
  - 🎯 FIX: force_no_since برای تست بدون فیلتر تاریخ
  - 🎯 FIX: صبر ۱۵ ثانیه‌ای برای لود چت‌ها
"""
from __future__ import annotations

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

_PLATFORM = "telegram"
TELEGRAM_WEB_URL = "https://web.telegram.org/k/"
_MAX_SEEN = 100000

_SYSTEM_NAMES = {
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده", "ذخیره‌شده",
    "ذخیره شده", "پیش‌نویس", "پیش‌نویس‌ها", "شما",
    "تصویر", "پیام", "بایگانی", "آرشیو",
    "Saved Messages", "Saved", "Drafts", "You", "Images",
    "Messages", "Archive", "Archived",
    "تلگرام", "Telegram", "تیم تلگرام", "Telegram Team",
}

_SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "Channel created",
    "Group created", "New channel", "New group",
)

LOGGEDIN_MARKERS = [
    ".chatlist", ".chat-list", "#column-left",
    ".LeftMainHeader", "input.input-search-input",
    '[contenteditable="true"].input-message-input',
]

CHAT_ITEMS_SELECTOR = "a.chatlist-chat[data-peer-id]"
MSG_SELECTOR = "[role='article'][data-mid]"

TITLE_SELECTORS = [
    ".chat-info .peer-title", ".chat-info .info .peer-title",
    ".topbar .peer-title", ".MiddleHeader .peer-title",
    ".info .peer-title", ".peer-title",
]

SENDER_NAME_SELECTORS = [
    ".peer-title", ".message-title", ".sender-title", ".sender-name",
]

_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


def _is_system_name(name: str) -> bool:
    if not name:
        return False
    n = name.strip()
    if not n:
        return False
    if n in _SYSTEM_NAMES:
        return True
    n_lower = n.lower()
    if "saved" in n_lower or "draft" in n_lower:
        return True
    return any(n.startswith(prefix) for prefix in _SYSTEM_PREFIXES)


class TelegramWebCollector(BaseCollector):
    def __init__(
        self,
        phone: str | None = None,
        headless: bool = True,
        poll_seconds: int = 180,
        max_chats: int = 0,
        chat_open_delay: float = 1.5,
        messages_per_chat: int = 500,
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
        self._seen_ids: set[str] = set()
        self._targets = get_target_groups()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()
        self._last_scrape_stats: dict[str, int] = {}

        if not hasattr(TelegramWebCollector, "_connect_lock"):
            TelegramWebCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "telegram_web"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(
            STORAGE_DIR, "media", "telegram"
        )
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "telegram_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "telegram_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()
        self._last_run_at = self._load_last_run_at()

        log_info(
            f"🔧 [telegram:init] mpc={self._messages_per_chat} | "
            f"bf={self._backfill_scrolls} | "
            f"max_chats={self._max_chats}"
        )

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

    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info("♻️ [telegram:state] بار اول انجام شده")
            else:
                log_info("🆕 [telegram:state] بار اول — تاریخچه کامل")
            return done
        except Exception:
            log_info("🆕 [telegram:state] بار اول — تاریخچه کامل")
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

    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ "
                    f"تلگرام بارگذاری شد"
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
            log_warning(f"ذخیرهٔ ضدتکراری تلگرام ناموفق: {e}")

    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(
        self, headless: bool | None = None
    ) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [telegram:connect] از قبل متصل — رد")
            return True

        async with TelegramWebCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "⚠️ [telegram:connect] context قدیمی — بستن"
                )
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
                self._context = await self._launch_context(
                    use_headless
                )
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
                await self._page.goto(
                    TELEGRAM_WEB_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                for i in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._needs_backfill = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(
                            f"✅ اتصال تلگرام‌وب برقرار شد — "
                            f"بعد از {i + 1} تلاش"
                        )
                        return True

                await self.dump_dom()
                log_error(
                    "نشست تلگرام‌وب نیست. `python login.py` "
                    "(گزینه ۴) را اجرا کن."
                )
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال تلگرام‌وب: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

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
            "viewport": {"width": 1920, "height": 1080},
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
            log_info(f"مرورگر تلگرام‌وب باز شد | منبع: {source}")
            return ctx
        except Exception as e:
            err = str(e)
            if (
                "already in use" in err
                or "existing browser" in err
                or "profile is already" in err
            ):
                log_warning("⚠️ پروفایل قفل شده — پاک‌سازی")
                self._force_kill_chrome()
                await asyncio.sleep(2)
                await self._cleanup_profile_locks()
                return (
                    await self._pw.chromium
                    .launch_persistent_context(**launch_options)
                )
            raise RuntimeError(
                f"هیچ مرورگری برای تلگرام‌وب باز نشد: {e}"
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

    def _force_kill_chrome(self):
        try:
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=5,
                check=False,
            )
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

    async def _is_logged_in(self) -> bool:
        try:
            for sel in LOGGEDIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    async def fetch_messages(
        self,
        since: datetime | None = None,
        force_no_since: bool = False,
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        effective_since = since
        if force_no_since:
            effective_since = None
            log_info(
                "🎯 [telegram:since] غیرفعال (force_no_since=True)"
            )
        elif effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(
                f"♻️ [telegram:since] از last_run_at: "
                f"{self._last_run_at.isoformat()}"
            )
        elif since:
            log_info(
                f"🎯 [telegram:since] از پارامتر since: "
                f"{since.isoformat()}"
            )

        self._last_scrape_stats = {
            "total_chats": 0,
            "unique_chats": 0,
            "processed": 0,
            "skipped_system": 0,
            "skipped_target": 0,
            "failed": 0,
            "messages_found": 0,
            "messages_skipped_old": 0,
            "messages_skipped_dup": 0,
            "messages_skipped_kw": 0,
        }

        try:
            async with self._page_lock:
                await self._scrape_all_chats(
                    effective_since=effective_since
                )
        except Exception as e:
            log_error(f"خطا در خواندن پیام تلگرام: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._needs_backfill = False

        if not self._first_run_done:
            self._first_run_at = datetime.now().isoformat()  # noqa: DTZ005
            self._first_run_done = True
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از تلگرام")

        stats = self._last_scrape_stats
        log_info(
            f"📊 [telegram:stats] total={stats['total_chats']} | "
            f"unique={stats['unique_chats']} | "
            f"processed={stats['processed']} | "
            f"system={stats['skipped_system']} | "
            f"target={stats['skipped_target']} | "
            f"failed={stats['failed']} | "
            f"found={stats['messages_found']} | "
            f"old={stats['messages_skipped_old']} | "
            f"dup={stats['messages_skipped_dup']} | "
            f"kw={stats['messages_skipped_kw']}"
        )

        return new_msgs

    async def _backfill_scroll(self):
        if not (
            self._needs_backfill and self._backfill_scrolls > 0
        ):
            return
        for _ in range(self._backfill_scrolls):
            try:
                scrolled = await self._page.evaluate(
                    "() => {"
                    "  const c = document.querySelector("
                    "    '#column-center .bubbles, "
                    "     #column-center .scrollable-y'"
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

    async def _find_chat_items(self) -> list:
        """پیدا کردن چت‌ها با bounding_box (headless-safe)"""
        for attempt in range(30):
            try:
                items = await self._page.query_selector_all(
                    CHAT_ITEMS_SELECTOR
                )
                if items:
                    visible_count = 0
                    for it in items:
                        try:
                            box = await it.bounding_box()
                            if (
                                box
                                and box.get("width", 0) > 0
                                and box.get("height", 0) > 0
                            ):
                                visible_count += 1
                        except Exception as e:
                            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
                    if visible_count > 0:
                        log_info(
                            f"✅ [تلگرام] {len(items)} چت با "
                            f"`{CHAT_ITEMS_SELECTOR}` "
                            f"({visible_count} visible) — "
                            f"تلاش {attempt + 1}"
                        )
                        return items
                    else:
                        log_info(
                            f"⏳ [تلگرام] {len(items)} چت یافت ولی "
                            f"هنوز رندر نشده — صبر..."
                        )
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
            await asyncio.sleep(1)
        return []

    async def _detect_chat_type(self) -> str:
        try:
            url = (self._page.url or "").lower()
            if "?channel=" in url or "/c/" in url:
                return "channel"
            if "?group=" in url or "/g/" in url:
                return "group"
            try:
                active = await self._page.query_selector(
                    "a.chatlist-chat.active[data-peer-id], "
                    "a.chatlist-chat[data-peer-id][aria-selected='true']"
                )
                if active:
                    pid = await active.get_attribute("data-peer-id") or ""
                    if pid.startswith("-"):
                        if re.search(r"#@[A-Za-z0-9_]+", url):
                            return "channel"
                        return "group"
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
            try:
                sub_el = await self._page.query_selector(
                    'text=/[Ss]ubscribers/'
                )
                if sub_el and await sub_el.is_visible():
                    return "channel"
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
            try:
                mem_el = await self._page.query_selector(
                    'text=/[Mm]embers/'
                )
                if mem_el and await mem_el.is_visible():
                    return "group"
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return "private"

    async def _extract_chat_meta(self):
        try:
            url = self._page.url or ""
            m = re.search(r"#@([A-Za-z0-9_]+)", url)
            if m:
                uname = m.group(1)
                return uname, f"https://t.me/{uname}"
            m = re.search(r"#\+([A-Za-z0-9_\-]+)", url)
            if m:
                return None, f"https://t.me/+{m.group(1)}"
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return None, None

    async def _extract_sender_from_bubble(self, msg):
        sender_id, sender_name = "", ""
        for attr in (
            "data-peer-id", "data-user-id", "data-sender-id"
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
            v = await chat_item.get_attribute("data-peer-id")
            if v:
                return str(v)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return ""

    async def _scrape_all_chats(
        self, effective_since: datetime | None = None
    ):
        try:
            url = (self._page.url or "").lower()
            if "/search" in url:
                await self._page.goto(
                    TELEGRAM_WEB_URL,
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(2)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        # اسکرول اولیه
        for _ in range(5):
            with contextlib.suppress(Exception):
                await self._page.mouse.wheel(0, 1500)
            await asyncio.sleep(0.5)

        # صبر برای لود کامل چت‌ها
        log_info("⏳ صبر برای لود کامل چت‌ها از سرور...")
        for _ in range(15):
            try:
                cnt = await self._page.locator(
                    "a.chatlist-chat[data-peer-id]"
                ).count()
                if cnt >= 5:
                    log_info(f"✅ {cnt} چت لود شد")
                    break
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
            await asyncio.sleep(1)

        chat_items = await self._find_chat_items()
        if not chat_items:
            log_warning("⚠️ هیچ چتی در لیست تلگرام پیدا نشد")
            with contextlib.suppress(Exception):
                await self.dump_dom()
            return

        self._last_scrape_stats["total_chats"] = len(chat_items)

        seen_pids: set[str] = set()
        unique_items: list[tuple] = []
        for it in chat_items:
            try:
                pid = await it.get_attribute("data-peer-id") or ""
                if not pid or pid in seen_pids:
                    continue
                seen_pids.add(pid)
                unique_items.append((pid, it))
            except Exception:
                continue

        self._last_scrape_stats["unique_chats"] = len(unique_items)
        log_info(
            f"🔍 {len(unique_items)} چت یکتا "
            f"(از {len(chat_items)})"
        )

        is_first = not self._first_run_done
        if is_first:
            log_info("🔍 حالت: بار اول — تاریخچه کامل")
        else:
            log_info("🔍 حالت: چک سریع")

        if self._max_chats > 0:
            unique_items = unique_items[: self._max_chats]

        for idx, (pid, item) in enumerate(unique_items):
            try:
                chat_name = await self._extract_item_title(item)
                if not chat_name:
                    chat_name = pid

                if _is_system_name(chat_name):
                    log_info(
                        f"   ⏭️ [{idx + 1}] سیستمی: {chat_name}"
                    )
                    self._last_scrape_stats["skipped_system"] += 1
                    continue

                try:
                    if (
                        self._targets.enabled(_PLATFORM)
                        and not self._targets.is_target(
                            chat_name, platform=_PLATFORM
                        )
                    ):
                        log_info(
                            f"   ⏭️ [{idx + 1}] غیرهدف: {chat_name}"
                        )
                        self._last_scrape_stats["skipped_target"] += 1
                        continue
                except Exception as e:
                    log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                log_info(
                    f"   ▶️ [{idx + 1}/{len(unique_items)}] {chat_name}"
                )

                clicked = False
                try:
                    locator = self._page.locator(
                        f'a.chatlist-chat[data-peer-id="{pid}"]'
                    ).first
                    cnt = await locator.count()
                    if cnt > 0:
                        await locator.click(timeout=5000, force=True)
                        clicked = True
                except Exception as e:
                    log_warning(f"   ⚠️ کلیک نشد: {e}")

                if not clicked:
                    log_warning(f"   ⚠️ [{idx + 1}] پیدا نشد: {chat_name}")
                    self._last_scrape_stats["failed"] += 1
                    continue

                await asyncio.sleep(self._chat_open_delay)

                try:
                    await asyncio.wait_for(
                        self._scrape_current_chat(
                            chat_name, pid,
                            effective_since=effective_since,
                        ),
                        timeout=120 if is_first else 60,
                    )
                    self._last_scrape_stats["processed"] += 1
                except asyncio.TimeoutError:
                    log_warning(
                        f"   ⏱️ timeout: {chat_name}"
                    )
                    self._last_scrape_stats["failed"] += 1
                    continue

                with contextlib.suppress(Exception):
                    await self._page.mouse.wheel(0, -200)

            except Exception as e:
                self._last_scrape_stats["failed"] += 1
                log_warning(f"   خطا در {pid}: {e}")
                continue

        log_success(
            f"🏁 [telegram:scrape] کامل | "
            f"پردازش={self._last_scrape_stats['processed']} | "
            f"سیستمی={self._last_scrape_stats['skipped_system']} | "
            f"غیرهدف={self._last_scrape_stats['skipped_target']} | "
            f"خطا={self._last_scrape_stats['failed']}"
        )

    async def _scrape_current_chat(
        self,
        chat_name: str,
        peer_id: str,
        effective_since: datetime | None = None,
    ) -> int:
        page = self._page
        chat_id = peer_id or chat_name

        chat_type = await self._detect_chat_type()
        chat_username, chat_link = await self._extract_chat_meta()

        if self._needs_backfill:
            await self._backfill_scroll()

        with contextlib.suppress(Exception):
            await page.wait_for_selector(
                MSG_SELECTOR,
                timeout=5000,
                state="attached",
            )

        messages = await page.query_selector_all(MSG_SELECTOR)
        if not messages:
            log_info(
                f"   ⚠️ [{chat_name}] هیچ پیامی پیدا نشد"
            )
            return 0

        log_info(
            f"   📨 {chat_name}: {len(messages)} پیام یافت"
        )

        limit = (
            max(self._messages_per_chat, 300)
            if self._needs_backfill
            else self._messages_per_chat
        )

        count = 0
        checked = 0
        skipped_old = 0
        skipped_dup = 0
        skipped_kw = 0
        skipped_invalid = 0

        _seen_in_this_chat: set[str] = set()

        for msg in messages[-limit:]:
            try:
                mid_raw = await msg.get_attribute("data-mid")
                if not mid_raw:
                    continue

                ts_raw = await msg.get_attribute("data-timestamp")
                msg_timestamp = None
                if ts_raw:
                    try:
                        ts_int = int(ts_raw)
                        if ts_int > 10_000_000_000:
                            msg_timestamp = datetime.fromtimestamp(  # noqa: DTZ006
                                ts_int / 1000
                            )
                        else:
                            msg_timestamp = datetime.fromtimestamp(  # noqa: DTZ006
                                ts_int
                            )
                    except (ValueError, OSError) as e:
                        log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                if effective_since and msg_timestamp:  # noqa: SIM102
                    if msg_timestamp < effective_since:
                        skipped_old += 1
                        continue

                text = (await msg.inner_text() or "").strip()
                if not text or len(text) < 2:
                    skipped_invalid += 1
                    continue

                checked += 1
                self._last_scrape_stats["messages_found"] += 1

                mid = hashlib.sha256(
                    f"{chat_id}:{mid_raw}".encode()
                ).hexdigest()[:32]

                if mid in _seen_in_this_chat:
                    skipped_dup += 1
                    continue
                _seen_in_this_chat.add(mid)

                if chat_type != "private":  # noqa: SIM102
                    if not self._kw.has_match(text):
                        skipped_kw += 1
                        self._last_scrape_stats[
                            "messages_skipped_kw"
                        ] += 1
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
                    sender_id, sender_name = "", chat_name
                else:
                    sender_id, sender_name = (
                        await self._extract_sender_from_bubble(msg)
                    )
                    if not sender_name:
                        sender_name = chat_name

                media_type, local_file_path = "text", None
                if self._kw.has_match(text):
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
                    except Exception as e:
                        log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

                final_ts = msg_timestamp or datetime.now()  # noqa: DTZ005

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

        self._last_scrape_stats["messages_skipped_old"] += skipped_old

        if count > 0:
            log_success(
                f"   📩 از {chat_name}: {count} پیام "
                f"(از {checked} چک‌شده)"
            )
        else:
            log_info(
                f"   ℹ️ {chat_name}: 0 پیام جدید "
                f"(checked={checked}، old={skipped_old}، "
                f"dup={skipped_dup}، kw={skipped_kw}، "
                f"invalid={skipped_invalid})"
            )

        return count

    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        log_info(
            f"⏭️ [telegram:send] غیرفعال (فقط‌خواندن) | "
            f"chat_id={chat_id}"
        )
        return False

    async def dump_dom(self, path: str | None = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "telegram_dom_dump.html"
        )
        try:
            if not self._page:
                return ""
            html = await self._page.content()

            def _write():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            await asyncio.to_thread(_write)
            log_info(f"DOM تلگرام در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

    async def disconnect(self) -> None:
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
        log_info("🔌 قطع ارتباط از تلگرام‌وب")

    async def check_health(self) -> bool:
        return self._connected
