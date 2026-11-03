# collectors/youtube.py
"""
کلکتور یوتیوب (YouTube) — نسخهٔ ۳.۱ (نهایی — فوق دقیق)

تغییرات نسخهٔ ۳.۱:
  - 🎯 FIX: force_no_since برای تست بدون فیلتر تاریخ
  - ✅ first_run_done + last_run_at
  - ✅ since واقعی (publish_date)
  - ✅ _last_scrape_stats تفکیکی
  - ✅ viewport 1920x1080
  - ✅ اسکرول هوشمندتر
  - ✅ لاگ کامل و شفاف
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
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

# ═══════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════
_PLATFORM = "youtube"
_MAX_SEEN = 100000

YOUTUBE_HOME = "https://www.youtube.com/"
YOUTUBE_SUBSCRIPTIONS = "https://www.youtube.com/feed/subscriptions"

LOGIN_MARKERS = [
    'a[href*="accounts.google.com/ServiceLogin"]',
    'yt-formatted-string:has-text("Sign in")',
    'tp-yt-paper-button:has-text("Sign in")',
]

LOGGEDIN_MARKERS = [
    "ytd-topbar-menu-button-renderer #avatar-btn",
    "button[aria-label*='Account' i]",
    "a[href*='/feed/subscriptions']",
    "ytd-mini-guide-entry-renderer",
]

_PROFILE_LOCK_FILES = [
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", ".parentlock",
]


class YouTubeCollector(BaseCollector):
    def __init__(
        self,
        headless: bool = False,
        poll_seconds: int = 1800,
        max_videos: int = 20,
        scroll_rounds: int = 8,
        delay_between: float = 2.5,
        **kwargs,
    ):
        super().__init__()
        self.headless = headless
        self._poll_seconds = int(poll_seconds)
        self._max_videos = int(max_videos)
        self._scroll_rounds = int(scroll_rounds)
        self._delay_between = float(delay_between)

        self._connected = False
        self._pw = None
        self._context = None
        self._page = None
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()
        self._targets = get_target_groups()
        self._page_lock = asyncio.Lock()
        self._last_scrape_stats: dict[str, int] = {}

        if not hasattr(YouTubeCollector, "_connect_lock"):
            YouTubeCollector._connect_lock = asyncio.Lock()

        from config import STORAGE_DIR

        self._user_data_dir = os.path.join(
            STORAGE_DIR, "sessions", "youtube_web"
        )
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "youtube")
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "youtube_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        # ─── state (first_run + last_run_at) ───
        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "youtube_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()
        self._last_run_at = self._load_last_run_at()

        log_info(
            f"🔧 [youtube:init] mpc={self._max_videos} | "
            f"bf={self._scroll_rounds} | "
            f"delay={self._delay_between}"
        )

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
    # state
    # ═══════════════════════════════════════════════
    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info("♻️ [youtube:state] بار اول انجام شده")
            else:
                log_info("🆕 [youtube:state] بار اول — تاریخچه کامل")
            return done
        except Exception:
            log_info("🆕 [youtube:state] بار اول — تاریخچه کامل")
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

    # ═══════════════════════════════════════════════
    # ضدتکراری
    # ═══════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} ویدیو دیده‌شدهٔ YouTube"
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
            log_warning(f"ذخیرهٔ seen یوتیوب: {e}")

    # ═══════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════
    async def connect(self, headless: bool | None = None) -> bool:
        from core.adapters.browser_lock import BROWSER_LOCK
        async with BROWSER_LOCK:
            return await self._connect_locked(headless)

    async def _connect_locked(
        self, headless: bool | None = None
    ) -> bool:
        if self._connected and self._context:
            log_info("ℹ️ [youtube:connect] از قبل متصل — رد")
            return True

        async with YouTubeCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning(
                    "⚠️ [youtube:connect] context قدیمی — بستن"
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

                # 🎯 anti-detection
                await self._apply_stealth()

                await self._page.goto(
                    YOUTUBE_HOME,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                for i in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        self._connected = True
                        self._set_status(ConnectionStatus.CONNECTED)
                        log_success(
                            f"✅ اتصال یوتیوب برقرار شد — "
                            f"بعد از {i + 1} تلاش"
                        )
                        return True

                await self.dump_dom()
                log_error(
                    "نشست یوتیوب تشخیص داده نشد. "
                    "`python login.py` (گزینهٔ ۸) را اجرا کن."
                )
                await self.disconnect()
                return False

            except Exception as e:
                log_error(f"خطا در اتصال یوتیوب: {e}")
                self._set_error(str(e))
                await self.disconnect()
                return False

    async def _apply_stealth(self):
        try:
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {name: 'Chrome PDF Plugin'},
                        {name: 'Chrome PDF Viewer'},
                        {name: 'Native Client'}
                    ]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };
            """)
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
            "--lang=en-US",
        ]
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

        await self._cleanup_profile_locks()

        launch_options: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": use_headless,
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": ua,
            "args": base_args,
            "locale": "en-US",
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
            log_info(f"مرورگر یوتیوب باز شد | منبع: {source}")
            return ctx
        except Exception as e:
            err = str(e)
            if (
                "already in use" in err
                or "existing browser" in err
                or "profile is already" in err
            ):
                log_warning("⚠️ پروفایل قفل — پاک‌سازی")
                self._force_kill_chrome()
                await asyncio.sleep(2)
                await self._cleanup_profile_locks()
                return (
                    await self._pw.chromium
                    .launch_persistent_context(**launch_options)
                )
            raise RuntimeError(
                f"هیچ مرورگری برای یوتیوب باز نشد: {e}"
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
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    # ═══════════════════════════════════════════════
    # رفتار انسانی
    # ═══════════════════════════════════════════════
    async def _human_delay(
        self, min_s: float = 0.5, max_s: float = 2.0
    ):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_scroll(self, amount: int = 1200):
        sign = 1 if amount > 0 else -1
        remaining = abs(amount)
        while remaining > 0:
            step = random.randint(80, 250)
            step = min(step, remaining)
            try:
                await self._page.mouse.wheel(0, sign * step)
            except Exception:
                break
            remaining -= step
            await self._human_delay(0.15, 0.4)

    # ═══════════════════════════════════════════════
    # fetch_messages (نسخهٔ ۳.۱ — با force_no_since)
    # ═══════════════════════════════════════════════
    async def fetch_messages(
        self,
        since: datetime | None = None,
        force_no_since: bool = False,
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._page:
            return []

        # ─── تعیین since نهایی ───
        effective_since = since
        if force_no_since:
            effective_since = None
            log_info(
                "🎯 [youtube:since] غیرفعال (force_no_since=True)"
            )
        elif effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(
                f"♻️ [youtube:since] از last_run_at: "
                f"{self._last_run_at.isoformat()}"
            )
        elif since:
            log_info(
                f"🎯 [youtube:since] از پارامتر since: "
                f"{since.isoformat()}"
            )

        # ─── ریست آمار ───
        self._last_scrape_stats = {
            "total_in_feed": 0,
            "new_videos": 0,
            "processed": 0,
            "skipped_seen": 0,
            "skipped_no_kw": 0,
            "skipped_old": 0,
            "skipped_invalid": 0,
            "stored": 0,
            "failed": 0,
        }

        async with self._page_lock:
            try:
                await self._scrape_subscriptions_v2(
                    effective_since=effective_since
                )
            except Exception as e:
                log_warning(f"subscriptions: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()

        if not self._first_run_done:
            self._first_run_at = datetime.now().isoformat()  # noqa: DTZ005
            self._first_run_done = True
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} ویدیو از یوتیوب")

        stats = self._last_scrape_stats
        log_info(
            f"📊 [youtube:stats] total={stats['total_in_feed']} | "
            f"new={stats['new_videos']} | "
            f"processed={stats['processed']} | "
            f"stored={stats['stored']} | "
            f"seen={stats['skipped_seen']} | "
            f"old={stats['skipped_old']} | "
            f"no-kw={stats['skipped_no_kw']} | "
            f"invalid={stats['skipped_invalid']} | "
            f"failed={stats['failed']}"
        )

        return new_msgs

    # ═══════════════════════════════════════════════
    # subscriptions v2
    # ═══════════════════════════════════════════════
    async def _scrape_subscriptions_v2(
        self, effective_since: datetime | None = None
    ):
        log_info("📺 [youtube] فید subscriptions...")
        try:
            await self._page.goto(
                YOUTUBE_SUBSCRIPTIONS,
                wait_until="domcontentloaded",
                timeout=60000,
            )
        except Exception as e:
            log_warning(f"goto subscriptions: {e}")
            return

        await asyncio.sleep(6)

        for _ in range(self._scroll_rounds):
            await self._human_scroll(2000)
            await asyncio.sleep(1.5)

        all_ids = await self._page.evaluate("""
            () => {
                const out = [];
                const seen = new Set();
                for (const c of document.querySelectorAll(
                    'ytd-rich-item-renderer'
                )) {
                    const a = c.querySelector(
                        'a[href*="watch?v="]'
                    );
                    if (a) {
                        const m = a.getAttribute('href')
                            .match(/v=([^&]+)/);
                        if (m && !seen.has(m[1])) {
                            seen.add(m[1]);
                            out.push(m[1]);
                        }
                    }
                }
                return out;
            }
        """)

        if not all_ids:
            log_warning("⚠️ هیچ video_id در subscriptions نیست")
            return

        self._last_scrape_stats["total_in_feed"] = len(all_ids)
        log_info(f"   📋 {len(all_ids)} video_id در فید")

        new_ids = []
        for v in all_ids:
            if v in self._seen_ids:
                self._last_scrape_stats["skipped_seen"] += 1
            else:
                new_ids.append(v)

        self._last_scrape_stats["new_videos"] = len(new_ids)
        log_info(
            f"   🆕 {len(new_ids)} جدید | "
            f"⏭️ {self._last_scrape_stats['skipped_seen']} دیده‌شده"
        )

        if not new_ids:
            return

        to_process = new_ids[: self._max_videos]
        log_info(f"   🎬 پردازش {len(to_process)} ویدیو")

        for vid in to_process:
            if await self._extract_and_queue(
                vid, effective_since=effective_since
            ):
                self._last_scrape_stats["stored"] += 1
            self._last_scrape_stats["processed"] += 1
            await asyncio.sleep(self._delay_between)

        log_info(
            f"   ✅ subscriptions: "
            f"{self._last_scrape_stats['stored']} ویدیو جدید"
        )

    # ═══════════════════════════════════════════════
    # استخراج کامل یک ویدیو
    # ═══════════════════════════════════════════════
    async def _extract_and_queue(
        self, video_id: str, effective_since: datetime | None = None
    ) -> bool:
        try:
            await self._page.goto(
                f"https://www.youtube.com/watch?v={video_id}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await asyncio.sleep(self._delay_between)

            data = await self._page.evaluate("""
                () => {
                    const r = window.ytInitialPlayerResponse;
                    if (!r || !r.videoDetails) return null;
                    const vd = r.videoDetails;
                    const md = r.microformat
                        ?.playerMicroformatRenderer;
                    return {
                        title: vd.title || '',
                        description: vd.shortDescription || '',
                        channel: vd.author || '',
                        channel_url: md?.ownerProfileUrl || '',
                        view_count: vd.viewCount || '',
                        publish_date: md?.publishDate || '',
                        upload_date: md?.uploadDate || '',
                        lang: vd.defaultAudioLanguage
                              || vd.defaultLanguage || '',
                    };
                }
            """)

            if not data or not data.get("title"):
                log_debug(f"   ⚠️ {video_id}: داده کافی نیست")
                self._last_scrape_stats["skipped_invalid"] += 1
                return False

            # ─── since filter ───
            if effective_since:
                pub = (
                    data.get("publish_date")
                    or data.get("upload_date")
                    or ""
                )
                if pub:
                    try:
                        pub_dt = datetime.fromisoformat(
                            pub.replace("Z", "+00:00")
                        )
                        if pub_dt.replace(tzinfo=None) < effective_since:
                            self._last_scrape_stats["skipped_old"] += 1
                            log_debug(
                                f"   ⏭️ قدیمی: {data['title'][:50]} "
                                f"({pub})"
                            )
                            return False
                    except Exception as e:
                        log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

            desc = data.get("description", "") or data["title"]
            full_text = (
                f"{data['title']}\n"
                f"{desc}\n"
                f"{data['channel']}"
            )

            # ─── فیلتر کلیدواژه ───
            if not self._kw.has_match(full_text):
                log_debug(
                    f"   ⏭️ بدون کلیدواژه: "
                    f"{data['title'][:50]}"
                )
                self._last_scrape_stats["skipped_no_kw"] += 1
                self._seen_ids.add(video_id)
                return False

            video_url = f"https://www.youtube.com/watch?v={video_id}"
            channel_url = data.get("channel_url", "")
            channel = data.get("channel", "")

            self._seen_ids.add(video_id)

            self._messages_queue.append({
                "id": video_id,
                "platform": _PLATFORM,
                "text": full_text,
                "chat_id": channel or "youtube",
                "chat_name": f"YouTube: {channel}",
                "chat_type": "youtube",
                "chat_username": channel or None,
                "chat_link": video_url,
                "user_id": "",
                "sender_name": channel or "YouTube",
                "timestamp": datetime.now(),  # noqa: DTZ005
                "media_type": "video",
                "file_id": None,
                "local_file_path": None,
                "reply_to": None,
                "video_url": video_url,
                "channel_url": channel_url,
                "view_count": data.get("view_count", ""),
                "lang_original": data.get("lang", ""),
                "description": desc,
            })

            log_info(
                f"   📩 {data['title'][:60]} | "
                f"کانال: {channel} | ✅"
            )
            return True

        except Exception as e:
            log_debug(f"   ❌ {video_id}: {type(e).__name__}: {e}")
            self._last_scrape_stats["failed"] += 1
            return False

    # ═══════════════════════════════════════════════
    # send_message — غیرفعال
    # ═══════════════════════════════════════════════
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        log_info(
            f"⏭️ [youtube:send] غیرفعال | chat_id={chat_id}"
        )
        return False

    # ═══════════════════════════════════════════════
    # dump_dom
    # ═══════════════════════════════════════════════
    async def dump_dom(self, path: str | None = None) -> str:
        path = path or os.path.join(
            self._user_data_dir, "youtube_dom_dump.html"
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
            log_info(f"DOM یوتیوب در {path} ذخیره شد")
            return path
        except Exception as e:
            log_warning(f"dump_dom ناموفق: {e}")
            return ""

    # ═══════════════════════════════════════════════
    # disconnect
    # ═══════════════════════════════════════════════
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
        log_info("🔌 قطع ارتباط از یوتیوب")

    async def check_health(self) -> bool:
        return self._connected
