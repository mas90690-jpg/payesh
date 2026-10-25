# collectors/telegram_web.py
"""
کلکتور تلگرام (Telegram) — کاربر عادی روی web.telegram.org

استراتژی: مثل بله/ایتا/واتساپ — مرورگر واقعی، نشست ذخیره‌شده،
بدون نیاز به api_id/api_hash.

ورود یک‌بار و دستی: `python login.py` → گزینهٔ ۴ (تلگرام)
نشست در storage/sessions/telegram_web/ ذخیره می‌شود.

نسخهٔ ۱:
  - مرورگر واقعی (Playwright + Chrome)
  - BROWSER_LOCK (استاندارد پروژه)
  - connect idempotent
  - ضدتکراری ماندگار روی دیسک
  - فیلتر کلیدواژه
  - پشتیبانی از شخصی + گروه + کانال
  - تشخیص chat_type از URL
  - دانلود عکس برای پیام‌های کلیدواژه‌دار
"""
from __future__ import annotations

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
_PLATFORM = "telegram"
TELEGRAM_WEB_URL = "https://web.telegram.org/k/"
_MAX_SEEN = 100000

# ─── انتخابگرها ───
LOGGEDIN_MARKERS = [
    ".chatlist",
    ".chat-list",
    "#column-left",
    ".LeftMainHeader",
    "input.input-search-input",
    '[contenteditable="true"].input-message-input',
]

CHATLIST_SELECTORS = [
    ".chatlist .chatlist-chat",
    ".chatlist a.chatlist-chat",
    "ul.chatlist > a",
    ".chatlist > a",
    "[data-peer-id]",
]

MSG_SELECTORS = [
    ".bubbles .bubble",
    ".bubble",
    ".message",
    "[data-mid]",
]

TITLE_SELECTORS = [
    ".chat-info .peer-title",
    ".chat-info .info .peer-title",
    ".topbar .peer-title",
    ".MiddleHeader .peer-title",
    ".info .peer-title",
    ".peer-title",
]

SENDER_NAME_SELECTORS = [
    ".peer-title",
    ".message-title",
    ".sender-title",
    ".sender-name",
]

_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


class TelegramWebCollector(BaseCollector):
    def __init__(
        self,
        phone: str = None,
        headless: bool = True,
        poll_seconds: int = 180,
        max_chats: int = 20,
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
        self._seen_ids: set[str] = set()
        self._targets = get_target_groups()
        self._kw = get_keyword_finder()
        self._page_lock = asyncio.Lock()

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
            log_info("ℹ️ [telegram:connect] از قبل متصل — رد")
            return True

        async with TelegramWebCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "⚠️ [telegram:connect] context قدیمی — بستن"
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
                            f"بعد از {i+1} تلاش"
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
            log_error(f"خطا در خواندن پیام تلگرام: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._needs_backfill = False
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از تلگرام")
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
                    "    '.bubbles, .scrollable-y, "
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
            for sel in CHATLIST_SELECTORS:
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
                            f"✅ [تلگرام] لیست چت با `{sel}` "
                            f"({len(visible)} آیتم) — "
                            f"تلاش {attempt+1}"
                        )
                        return visible, sel
                except Exception:
                    continue
            await asyncio.sleep(1)
        return [], ""

    async def _detect_chat_type(self) -> str:
        try:
            url = (self._page.url or "").lower()
            if "?channel=" in url or "/c/" in url:
                return "channel"
            if "?group=" in url or "/g/" in url:
                return "group"
            try:
                sub_el = await self._page.query_selector(
                    'text=/[Ss]ubscribers/'
                )
                if sub_el and await sub_el.is_visible():
                    return "channel"
            except Exception:
                pass
            try:
                mem_el = await self._page.query_selector(
                    'text=/[Mm]embers/'
                )
                if mem_el and await mem_el.is_visible():
                    return "group"
            except Exception:
                pass
        except Exception:
            pass
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
        except Exception:
            pass
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
            for attr in ("data-peer-id", "data-name"):
                v = await chat_item.get_attribute(attr)
                if v:
                    return str(v)
        except Exception:
            pass
        return "تلگرام"

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
        return "تلگرام"

    async def _scrape_all_chats(self):
        try:
            url = (self._page.url or "").lower()
            if "/search" in url:
                await self._page.goto(
                    TELEGRAM_WEB_URL,
                    wait_until="domcontentloaded",
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
            log_warning("⚠️ هیچ چتی در لیست تلگرام پیدا نشد")
            try:
                await self.dump_dom()
            except Exception:
                pass
            return

        log_info(f"🔍 {len(chat_items)} چت برای اسکرپ")

        chats_to_process = (
            chat_items
            if self._max_chats <= 0
            else chat_items[: self._max_chats]
        )

        processed = 0
        skipped_bot = 0
        skipped_target = 0
        failed = 0

        for idx, chat in enumerate(chats_to_process):
            try:
                chat_name = await self._extract_item_title(chat)

                if chat_name and chat_name != "تلگرام":
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
                    except Exception:
                        pass

                try:
                    await chat.click(timeout=5000, force=True)
                except Exception as e:
                    log_warning(
                        f"کلیک ناموفق روی چت {chat_name}: {e}"
                    )
                    failed += 1
                    continue

                await asyncio.sleep(self._chat_open_delay)
                await self._scrape_current_chat(chat_name)
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
            f"🏁 [telegram:scrape] کامل | پردازش={processed} | "
            f"غیرهدف={skipped_target} | بات={skipped_bot} | "
            f"خطا={failed}"
        )

    async def _scrape_current_chat(self, chat_name: str = None):
        page = self._page
        if not chat_name:
            chat_name = await self._get_open_chat_title()
        if not chat_name or chat_name == "تلگرام":
            return

        chat_type = await self._detect_chat_type()
        chat_username, chat_link = await self._extract_chat_meta()
        chat_id = chat_username or chat_name

        log_info(
            f"📖 اسکرپ چت تلگرام: {chat_name} "
            f"(نوع: {chat_type})"
        )

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
                    mid = hashlib.sha256(
                        f"{chat_id}:{text}".encode("utf-8")
                    ).hexdigest()[:16]

                if not mid or mid == "0" or not text:
                    continue

                checked += 1
                uniq = f"{chat_id}:{mid}"

                if (
                    chat_type != "private"
                    and not self._kw.has_match(text)
                ):
                    log_debug(f"⏭️ [kw] بدون کلیدواژه | {chat_name} | {text[:40]}")
                    continue

                if uniq in self._seen_ids:
                    log_debug(f"⏭️ [seen] تکراری | {chat_name} | {mid}")
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
                f"(از {checked} پیام)"
            )
        else:
            log_info(
                f"ℹ️ چت {chat_name}: هیچ پیام جدیدی "
                f"(از {checked} پیام)"
            )

    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        log_info(
            f"⏭️ [telegram:send] غیرفعال | chat_id={chat_id}"
        )
        return False

    async def dump_dom(self, path: str = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "telegram_dom_dump.html"
        )
        try:
            if not self._page:
                return ""
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            log_info(f"DOM تلگرام در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
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
        log_info("🔌 قطع ارتباط از تلگرام‌وب")

    async def check_health(self) -> bool:
        return self._connected