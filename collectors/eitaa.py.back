# collectors/eitaa.py
"""
کلکتور ایتا (Eitaa) — نسخه v8.5 (دقیق + پایدار)

🎯 بهبودها نسبت به v8.4:
  - ✅ wait_for_selector بعد از کلیک روی چت (رفع مشکل «۱ پیام»)
  - ✅ sender_name درست برای گروه (فقط پیام‌های is-in)
  - ✅ skip پیام‌های media خالی (just-media/is-message-empty)
  - ✅ استخراج sender از data-peer-id والد اگر ممکن
  - ✅ chat_type دقیق‌تر (channel هم تشخیص داده می‌شه)
  - ✅ لاگ کامل با علت skip
  - ✅ حفظ همه امکانات v8.4 (backfill، _clean_text، ضدتکراری)
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

# ⚡ timeouts
_CHAT_TIMEOUT = 30
_SELECTOR_TIMEOUT_MS = 5000
_WAIT_FOR_MSG_MS = 1500
_MAX_SCROLL_ROUNDS = 30

# 🎭 تأخیرها
_DELAY_MIN = 0.2
_DELAY_MAX = 0.5
_CHAT_OPEN_DELAY_MIN = 1.2
_CHAT_OPEN_DELAY_MAX = 2.0

# 🎯 چت‌های سیستمی
_SYSTEM_NAMES = {
    "پیام‌های ذخیره شده", "ذخیره شده", "شما",
    "Saved Messages", "Drafts",
    "ایتا", "Eitaa",
}

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


def _clean_text(text: str) -> str:
    """پاک‌سازی متن پیام ایتا."""
    if not text:
        return ""

    # ① حذف PUA
    for ch in _PUA_CHARS:
        text = text.replace(ch, "")
    text = re.sub(r'[\ue000-\uf8ff]', '', text)

    lines = [ln.rstrip() for ln in text.split('\n')]

    # ② حذف خطوط انتهایی تزئینی
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
        poll_seconds: int = 180,
        max_chats: int = 15,
        chat_open_delay: float = 1.5,
        messages_per_chat: int = 50,
        backfill_scrolls: int = 3,
        **kwargs,
    ):
        super().__init__()
        self.phone = phone
        self.password = password
        self.headless = headless
        self._poll_seconds = int(poll_seconds)
        self._max_chats = int(max_chats)
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
        self._stealth_applied = False

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
        return False

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
        """
        🎯 استخراج فرستنده — فقط برای گروه.
        برای پیام‌های is-out → فرستنده خودمون، پس chat_name.
        برای پیام‌های is-in گروه → تلاش کن sender واقعی رو بگیر.
        """
        try:
            cls = await msg.get_attribute("class") or ""

            # پیام‌های خروجی (خودمون) → sender_name = chat_name
            if "is-out" in cls:
                return chat_name

            # پیام‌های خصوصی → sender_name = chat_name
            if "is-group" not in cls and "group" not in cls:
                return chat_name

            # پیام گروه ورودی → تلاش کن sender واقعی
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

    # ═══════════════════════════════════════════════
    # جمع‌آوری چت‌ها
    # ═══════════════════════════════════════════════
    async def _collect_chats(self) -> list[dict]:
        chats = []
        seen_keys = set()
        last_count = 0
        stable_rounds = 0

        for round_i in range(_MAX_SCROLL_ROUNDS):
            items = await self._page.query_selector_all(_CHAT_SELECTOR)
            for it in items:
                try:
                    pid = await self._extract_peer_id(it)
                    name = await self._extract_title(it)
                    if not name or name in _SYSTEM_NAMES:
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
            await self._human_delay(0.2, 0.4)

            cur = len(chats)
            if cur == last_count:
                stable_rounds += 1
                if stable_rounds >= 3:
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

        chats = await self._collect_chats()
        if not chats:
            return []

        if self._max_chats > 0:
            chats = chats[:self._max_chats]

        log_info(f"⚡ {len(chats)} چت (سریالی)")

        for idx, chat in enumerate(chats):
            try:
                await self._scrape_chat(chat, idx)
                await self._human_delay(0.3, 0.6)
            except Exception as e:
                log_warning(f"[{idx+1}] {chat.get('name')}: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        self._needs_backfill = False

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام از ایتا")

        return new_msgs

    # ═══════════════════════════════════════════════
    # اسکرپ یک چت
    # ═══════════════════════════════════════════════
    async def _scrape_chat(self, chat: dict, idx: int):
        peer_id = chat["peer_id"]
        name = chat["name"]

        if not name or name in _SYSTEM_NAMES:
            return

        try:
            if (self._targets.enabled(_PLATFORM) and
                not self._targets.is_target(name, platform=_PLATFORM)):
                log_debug(f"⏭️ [{idx+1}] غیرهدف: {name}")
                return
        except Exception:
            pass

        log_info(f"▶️ [{idx+1}] {name}")

        if not await self._click_chat(peer_id, name):
            log_warning(f"⏭️ [{idx+1}] کلیک نشد: {name}")
            return

        # 🎯 NEW: انتظار برای لود شدن پیام‌ها بعد از کلیک
        try:
            await self._page.wait_for_selector(
                ".bubbles .bubble",
                timeout=_WAIT_FOR_MSG_MS,
                state="attached",
            )
        except Exception:
            # timeout → احتمالاً چت خالیه یا هنوز لود نشده
            pass

        await self._human_delay(
            _CHAT_OPEN_DELAY_MIN, _CHAT_OPEN_DELAY_MAX
        )

        try:
            await asyncio.wait_for(
                self._scrape_current(name, peer_id),
                timeout=_CHAT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log_warning(f"⏱️ [{idx+1}] timeout: {name}")

    # ═══════════════════════════════════════════════
    # کلیک مطمئن روی چت
    # ═══════════════════════════════════════════════
    async def _click_chat(self, peer_id: str, name: str) -> bool:
        # ① سریع با data-peer-id
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

        # ② fallback: اسکرول تا پیدا شدن
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

    # ═══════════════════════════════════════════════
    # backfill scroll داخل چت
    # ═══════════════════════════════════════════════
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
    async def _scrape_current(self, name: str, peer_id: str):
        page = self._page
        chat_id = peer_id if peer_id else name

        await self._backfill_scroll()

        # ② پیدا کردن پیام‌ها
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
            return

        total_bubbles = len(messages)
        log_info(f"   📨 {name}: {total_bubbles} پیام یافت ({used_sel})")

        limit = (
            max(self._messages_per_chat, 200)
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

        for msg in messages[-limit:]:
            try:
                cls = await msg.get_attribute("class") or ""

                # ─── ① skip service/date ───
                if "service" in cls or "is-date" in cls:
                    skipped_service += 1
                    continue

                # ─── ② skip پیام‌های media خالی (عکس/ویدیو بدون کپشن) ───
                is_empty_media = any(
                    marker in cls for marker in _EMPTY_MEDIA_CLASSES
                )
                if is_empty_media:
                    skipped_empty_media += 1
                    continue

                # ─── ③ data-mid ───
                mid = await msg.get_attribute("data-mid") or ""

                # ─── ④ متن خام ───
                raw_text = (await msg.inner_text() or "").strip()
                if not raw_text:
                    continue

                # ─── ⑤ پاک‌سازی ───
                text = _clean_text(raw_text)
                if not text:
                    continue

                # 🎯 متن خیلی کوتاه (کمتر از ۳ کاراکتر) → skip
                if len(text.strip()) < 3:
                    skipped_short += 1
                    continue

                # 🎯 FIX: اگه data-mid نبود از hash استفاده کن
                if not mid:
                    mid = hashlib.sha256(
                        f"{chat_id}:{text}".encode("utf-8")
                    ).hexdigest()[:16]
                    skipped_nomid += 1

                uniq = f"{chat_id}:{mid}"
                if uniq in self._seen_ids:
                    skipped_seen += 1
                    continue
                self._seen_ids.add(uniq)

                # ─── ⑥ فیلتر کلیدواژه ───
                if not self._kw.has_match(text):
                    skipped_kw += 1
                    continue

                # ─── ⑦ sender (نسخه دقیق) ───
                sender = await self._extract_sender(msg, name)

                # ─── ⑧ chat_type ───
                chat_type = "private"
                if "is-group" in cls:
                    chat_type = "group"
                elif "is-channel" in cls or "channel" in cls.lower():
                    chat_type = "channel"

                # ─── ⑨ media ───
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
                    "chat_id": chat_id,
                    "chat_name": name,
                    "chat_type": chat_type,
                    "chat_username": None,
                    "chat_link": None,
                    "user_id": "",
                    "sender_name": sender,
                    "timestamp": datetime.now(),
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

        # 🎯 گزارش نهایی
        log_info(
            f"   📊 {name}: قبول={count} | "
            f"service={skipped_service} | "
            f"empty-media={skipped_empty_media} | "
            f"short={skipped_short} | "
            f"nomid→hash={skipped_nomid} | "
            f"seen={skipped_seen} | "
            f"kw-skip={skipped_kw}"
        )

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