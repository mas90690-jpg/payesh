# collectors/youtube.py
"""
کلکتور یوتیوب (YouTube) — نسخهٔ ۴.۰ (بازنویسی کامل)

تغییرات v4.0:
  - 🎯 FIX: _first_run_at در connect ست می‌شود (نه در _save_state)
  - 🎯 FIX: _save_state حالا in-memory را هم آپدیت می‌کند
  - 🎯 FIX: سلکتورها چندگانه (ytd-rich-item-renderer + ...)
  - 🎯 FIX: استخراج video_id از DOM به‌جای ytInitialPlayerResponse
              (ytInitialPlayerResponse در watch گاهی null است)
  - 🎯 FIX: fallback برای description خالی
  - 🎯 FIX: _disconnect ایمن‌تر
  - 🎯 NEW: اسکرول تدریجی تا پیدا شدن ویدیو (نه تعداد ثابت)
  - 🎯 NEW: لاگ کامل و شفاف در هر مرحله
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
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
YOUTUBE_WATCH = "https://www.youtube.com/watch?v={vid}"

# ─── مارکرهای ورود/عدم‌ورود ───
LOGIN_MARKERS = [
    'a[href*="accounts.google.com/ServiceLogin"]',
    'yt-formatted-string:has-text("Sign in")',
    'tp-yt-paper-button:has-text("Sign in")',
    'a[href*="accounts.google.com/SignOutOptions"]',
]

LOGGEDIN_MARKERS = [
    "ytd-topbar-menu-button-renderer #avatar-btn",
    "button[aria-label*='Account' i]",
    "a[href*='/feed/subscriptions']",
    "ytd-mini-guide-entry-renderer",
    "#avatar-btn",
]

# ─── سلکتورهای کارت ویدیو در فید ───
# یوتیوب A/B test دارد؛ هر سه را امتحان می‌کنیم
VIDEO_CARD_SELECTORS = [
    "ytd-rich-item-renderer",
    "ytd-grid-video-renderer",
    "ytd-video-renderer",
    "ytd-compact-video-renderer",
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

        # state (in-memory)
        self._first_run_done = False
        self._first_run_at: str | None = None
        self._last_run_at: datetime | None = None

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

        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "youtube_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)

        # بارگذاری
        self._load_seen()
        self._load_state()

        log_info(
            f"🔧 [youtube:init] max_videos={self._max_videos} | "
            f"scroll_rounds={self._scroll_rounds} | "
            f"delay={self._delay_between}s | "
            f"first_run_done={self._first_run_done}"
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

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ═══════════════════════════════════════════════
    # state — بارگذاری
    # ═══════════════════════════════════════════════
    def _load_state(self):
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            self._first_run_done = bool(d.get("first_run_done", False))
            self._first_run_at = d.get("first_run_at")
            last_run = d.get("last_run_at")
            if last_run:
                try:
                    self._last_run_at = datetime.fromisoformat(last_run)
                except (ValueError, TypeError):
                    self._last_run_at = None
            if self._first_run_done:
                log_info(
                    f"♻️ [youtube:state] بار اول انجام شده | "
                    f"last_run={self._last_run_at}"
                )
            else:
                log_info("🆕 [youtube:state] بار اول — تاریخچه کامل")
        except FileNotFoundError:
            log_info("🆕 [youtube:state] بار اول — تاریخچه کامل")
        except Exception as e:
            log_warning(f"⚠️ load_state: {type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════
    # state — ذخیره
    # ═══════════════════════════════════════════════
    def _save_state(self):
        try:
            now = datetime.now()  # noqa: DTZ005
            data = {
                "first_run_done": True,
                "first_run_at": self._first_run_at or now.isoformat(),
                "last_run_at": now.isoformat(),
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 🎯 FIX: in-memory هم آپدیت شود
            self._first_run_done = True
            if not self._first_run_at:
                self._first_run_at = now.isoformat()
            self._last_run_at = now
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
                log_info(
                    f"♻️ {len(self._seen_ids)} ویدیو دیده‌شده بارگذاری شد"
                )
        except FileNotFoundError:
            self._seen_ids = set()
        except Exception as e:
            log_warning(f"⚠️ load_seen: {type(e).__name__}: {e}")
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
            log_warning(f"ذخیره seen ناموفق: {e}")

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
            log_info("ℹ️ [youtube:connect] از قبل متصل")
            return True

        async with YouTubeCollector._connect_lock:
            if self._connected and self._context:
                return True

            if self._context:
                log_warning("⚠️ context قدیمی — بستن")
                with contextlib.suppress(Exception):
                    await self._context.close()
                self._context = None
                self._page = None

            try:
                if async_playwright is None:
                    log_error("playwright نصب نیست: pip install playwright")
                    return False

                use_headless = (
                    self.headless if headless is None else headless
                )

                await self._cleanup_profile_locks()

                log_info("🌐 [youtube:connect] راه‌اندازی مرورگر...")
                self._pw = await async_playwright().start()
                self._context = await self._launch_context(use_headless)
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )

                await self._apply_stealth()

                log_info("🌐 [youtube:connect] رفتن به youtube.com...")
                try:
                    await self._page.goto(
                        YOUTUBE_HOME,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                except Exception as e:
                    err = str(e)
                    log_error(f"❌ goto شکست خورد: {err[:120]}")
                    if "ERR_SSL_PROTOCOL_ERROR" in err:
                        log_warning("🔒 SSL error → VPN/Proxy")
                    elif "ERR_NAME_NOT_RESOLVED" in err:
                        log_warning("🌐 DNS error → VPN")
                    elif "ERR_CONNECTION_TIMED_OUT" in err:
                        log_warning("⏱️ Timeout → VPN")
                    self._set_error(f"goto_failed: {err[:200]}")
                    await self.disconnect()
                    return False

                # ─── انتظار پویا برای لاگین (تا ۴۰ ثانیه) ───
                log_info("⏳ [youtube:connect] انتظار لاگین (تا ۴۰s)...")
                login_confirmed = False
                for attempt in range(20):
                    await asyncio.sleep(2)
                    if await self._is_logged_in():
                        login_confirmed = True
                        log_success(
                            f"✅ لاگین تشخیص داده شد "
                            f"(بعد از {attempt + 1} تلاش)"
                        )
                        break
                    if attempt % 5 == 4:
                        log_info(
                            f"   ⏳ [youtube:connect] {attempt + 1}/20..."
                        )

                if login_confirmed:
                    self._connected = True
                    self._set_status(ConnectionStatus.CONNECTED)
                    # 🎯 FIX: _first_run_at را همین‌جا ست کن
                    if not self._first_run_at:
                        self._first_run_at = (
                            datetime.now().isoformat()  # noqa: DTZ005
                        )
                    log_success("✅ اتصال یوتیوب برقرار شد")
                    return True

                # ─── تشخیص دلیل شکست ───
                url = self._page.url or ""
                title = ""
                with contextlib.suppress(Exception):
                    title = await self._page.title()

                log_error("❌ لاگین تشخیص داده نشد")
                log_info(f"   URL: {url[:100]}")
                log_info(f"   Title: {title[:80]}")

                if "youtube.com" not in url:
                    log_error("   → صفحه در یوتیوب نیست (شبکه/VPN)")
                else:
                    qr_found = False
                    for sel in LOGIN_MARKERS:
                        try:
                            el = await self._page.query_selector(sel)
                            if el and await el.is_visible():
                                qr_found = True
                                break
                        except Exception:
                            continue
                    if qr_found:
                        log_error(
                            "   → صفحه Sign in نمایش داده شده "
                            "(نشست معتبر نیست)"
                        )
                        log_warning(
                            "   راه‌حل: python login.py → گزینهٔ ۸ (YouTube)"
                        )
                    else:
                        log_error(
                            "   → هیچ مارکر لاگینی پیدا نشد "
                            "(DOM تغییر کرده؟)"
                        )

                with contextlib.suppress(Exception):
                    await self.dump_dom()

                await self.disconnect()
                return False

            except Exception as e:
                log_error(
                    f"خطا در اتصال یوتیوب: "
                    f"{type(e).__name__}: {e}"
                )
                self._set_error(str(e))
                await self.disconnect()
                return False

    # ═══════════════════════════════════════════════
    # launch
    # ═══════════════════════════════════════════════
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
            log_info(f"مرورگر یوتیوب باز شد | {source}")
            return ctx
        except Exception as e:
            err = str(e)
            needs_cleanup = (
                "already in use" in err
                or "existing browser" in err
                or "profile is already" in err
                or "has been closed" in err
                or "exitCode=21" in err
                or "Target page" in err
                or "TargetClosedError" in err
            )
            if needs_cleanup:
                log_warning(
                    f"⚠️ پروفایل قفل — پاک‌سازی | {err[:80]}"
                )
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
                [
                    "taskkill", "/F", "/FI",
                    "WINDOWTITLE eq *youtube_web*",
                ],
                capture_output=True, timeout=5, check=False,
            )
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

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
            log_debug(f"⚠️ stealth: {type(e).__name__}: {e}")

    async def _is_logged_in(self) -> bool:
        try:
            if not self._page:
                return False
            # ۱) Sign in → not logged
            for sel in LOGIN_MARKERS:
                try:
                    el = await self._page.query_selector(sel)
                    if el and await el.is_visible():
                        return False
                except Exception:
                    continue
            # ۲) آواتار/لینک subscriptions → logged
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
    # fetch_messages
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
            log_info("🎯 [youtube:since] غیرفعال (force_no_since)")
        elif effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(
                f"♻️ [youtube:since] از last_run_at: "
                f"{self._last_run_at.isoformat()}"
            )
        elif since:
            log_info(
                f"🎯 [youtube:since] از پارامتر: "
                f"{since.isoformat()}"
            )

        # ─── ریست آمار ───
        self._last_scrape_stats = {
            "total_in_feed": 0,
            "new_videos": 0,
            "processed": 0,
            "stored": 0,
            "skipped_seen": 0,
            "skipped_old": 0,
            "skipped_no_kw": 0,
            "skipped_invalid": 0,
            "failed": 0,
        }

        async with self._page_lock:
            try:
                await self._scrape_subscriptions(
                    effective_since=effective_since
                )
            except Exception as e:
                log_error(f"subscriptions: {type(e).__name__}: {e}")

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

        # 🎯 FIX: seen اول، بعد state
        self._save_seen()
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} ویدیو از یوتیوب")

        s = self._last_scrape_stats
        log_info(
            f"📊 [youtube:stats] total={s['total_in_feed']} | "
            f"new={s['new_videos']} | "
            f"processed={s['processed']} | "
            f"stored={s['stored']} | "
            f"seen={s['skipped_seen']} | "
            f"old={s['skipped_old']} | "
            f"no-kw={s['skipped_no_kw']} | "
            f"invalid={s['skipped_invalid']} | "
            f"failed={s['failed']}"
        )

        return new_msgs

    # ═══════════════════════════════════════════════
    # scrape subscriptions
    # ═══════════════════════════════════════════════
    async def _scrape_subscriptions(
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

        # ─── صبر اولیه ───
        log_info("⏳ صبر اولیه ۵ ثانیه...")
        await asyncio.sleep(5)

        # ─── اسکرول تدریجی تا پیدا شدن ویدیو ───
        log_info(f"🖱️ اسکرول تدریجی (تا {self._scroll_rounds} دور)...")
        last_count = 0
        stable_rounds = 0

        for round_i in range(self._scroll_rounds):
            await self._human_scroll(2000)
            await asyncio.sleep(1.5)

            # شمارش تعداد کارت‌ها
            total_cards = 0
            for sel in VIDEO_CARD_SELECTORS:
                try:
                    cnt = await self._page.locator(sel).count()
                    total_cards += cnt
                except Exception:
                    pass

            if total_cards > last_count:
                last_count = total_cards
                stable_rounds = 0
                log_info(
                    f"   ↕️ {round_i + 1}/{self._scroll_rounds} | "
                    f"cards={total_cards}"
                )
            else:
                stable_rounds += 1
                if stable_rounds >= 2 and total_cards > 0:
                    log_info(
                        f"   ⏹️ پایدار در {total_cards} کارت"
                    )
                    break

        # ─── استخراج video_id ───
        log_info("🔍 استخراج video_id از DOM...")
        all_ids = await self._extract_video_ids_from_dom()

        if not all_ids:
            log_warning("⚠️ هیچ video_id در subscriptions نیست")
            with contextlib.suppress(Exception):
                await self.dump_dom()
            return

        self._last_scrape_stats["total_in_feed"] = len(all_ids)
        log_info(f"   📋 {len(all_ids)} video_id پیدا شد")

        # ─── فیلتر seen ───
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

        # ─── پردازش ───
        to_process = new_ids[: self._max_videos]
        log_info(f"   🎬 پردازش {len(to_process)} ویدیو")

        for i, vid in enumerate(to_process, 1):
            log_info(f"   ▶️ [{i}/{len(to_process)}] {vid}")
            ok = await self._extract_and_queue(
                vid, effective_since=effective_since
            )
            if ok:
                self._last_scrape_stats["stored"] += 1
            self._last_scrape_stats["processed"] += 1

            if i < len(to_process):
                await asyncio.sleep(self._delay_between)

        log_info(
            f"   ✅ subscriptions: "
            f"{self._last_scrape_stats['stored']} ویدیو جدید"
        )

    async def _extract_video_ids_from_dom(self) -> list[str]:
        """استخراج video_id از DOM با هر سلکتور کارت."""
        script = """
        () => {
            const out = [];
            const seen = new Set();
            const selectors = [
                'ytd-rich-item-renderer',
                'ytd-grid-video-renderer',
                'ytd-video-renderer',
                'ytd-compact-video-renderer',
            ];
            for (const sel of selectors) {
                for (const c of document.querySelectorAll(sel)) {
                    const a = c.querySelector('a[href*="watch?v="]');
                    if (!a) continue;
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/[?&]v=([A-Za-z0-9_-]{6,})/);
                    if (m && !seen.has(m[1])) {
                        seen.add(m[1]);
                        out.push(m[1]);
                    }
                }
            }
            return out;
        }
        """
        try:
            result = await self._page.evaluate(script)
            if isinstance(result, list):
                return [str(v) for v in result if v]
            return []
        except Exception as e:
            log_warning(f"استخراج video_id ناموفق: {e}")
            return []

    # ═══════════════════════════════════════════════
    # استخراج جزئیات یک ویدیو
    # ═══════════════════════════════════════════════
    async def _extract_and_queue(
        self, video_id: str, effective_since: datetime | None = None
    ) -> bool:
        try:
            url = YOUTUBE_WATCH.format(vid=video_id)
            await self._page.goto(
                url, wait_until="domcontentloaded", timeout=60000
            )
            await asyncio.sleep(self._delay_between)

            data = await self._page.evaluate("""
                () => {
                    const out = {
                        title: '',
                        description: '',
                        channel: '',
                        publish_date: '',
                        view_count: '',
                    };
                    // ۱) ytInitialPlayerResponse
                    const r = window.ytInitialPlayerResponse;
                    if (r && r.videoDetails) {
                        const vd = r.videoDetails;
                        out.title = vd.title || '';
                        out.description = vd.shortDescription || '';
                        out.channel = vd.author || '';
                        out.view_count = vd.viewCount || '';
                        const md = r.microformat
                            ?.playerMicroformatRenderer;
                        if (md) {
                            out.publish_date =
                                md.publishDate || md.uploadDate || '';
                        }
                    }
                    // ۲) fallback: DOM
                    if (!out.title) {
                        const t = document.querySelector(
                            'h1.ytd-watch-metadata yt-formatted-string, '
                            + 'h1.title'
                        );
                        if (t) out.title = (t.innerText || '').trim();
                    }
                    if (!out.channel) {
                        const ch = document.querySelector(
                            'ytd-channel-name a, '
                            + '#channel-name a'
                        );
                        if (ch) {
                            out.channel = (ch.innerText || '').trim();
                        }
                    }
                    return out;
                }
            """)

            if not data or not data.get("title"):
                log_warning(
                    f"   ⚠️ {video_id}: عنوان پیدا نشد — skip"
                )
                self._last_scrape_stats["skipped_invalid"] += 1
                return False

            # ─── since filter ───
            if effective_since:
                pub = data.get("publish_date") or ""
                if pub:
                    try:
                        pub_dt = datetime.fromisoformat(
                            pub.replace("Z", "+00:00")
                        )
                        if pub_dt.replace(tzinfo=None) < effective_since:
                            self._last_scrape_stats["skipped_old"] += 1
                            log_debug(
                                f"   ⏭️ قدیمی: "
                                f"{data['title'][:50]} ({pub})"
                            )
                            return False
                    except Exception as e:
                        log_debug(
                            f"⚠️ parse publish_date: "
                            f"{type(e).__name__}: {e}"
                        )

            desc = data.get("description") or data["title"]
            full_text = (
                f"{data['title']}\n"
                f"{desc}\n"
                f"{data.get('channel', '')}"
            )

            # ─── فیلتر کلیدواژه (فقط اگر KEYWORDS_ONLY=true) ───
            _keywords_only = os.getenv(
                "KEYWORDS_ONLY", "true"
            ).strip().lower() in ("true", "1", "yes", "on")
            if _keywords_only and not self._kw.has_match(full_text):
                log_info(
                    f"   ⏭️ بدون کلیدواژه: "
                    f"{data['title'][:50]}"
                )
                self._last_scrape_stats["skipped_no_kw"] += 1
                self._seen_ids.add(video_id)
                return False

            channel = data.get("channel", "") or "YouTube"

            self._seen_ids.add(video_id)

            self._messages_queue.append({
                "id": video_id,
                "platform": _PLATFORM,
                "text": full_text[:5000],
                "chat_id": channel,
                "chat_name": f"YouTube: {channel}",
                "chat_type": "youtube",
                "chat_username": channel,
                "chat_link": url,
                "user_id": "",
                "sender_name": channel,
                "timestamp": datetime.now(),  # noqa: DTZ005
                "media_type": "video",
                "file_id": None,
                "local_file_path": None,
                "reply_to": None,
            })

            log_success(
                f"   📩 {data['title'][:60]} | "
                f"کانال: {channel} | ✅"
            )
            return True

        except Exception as e:
            log_warning(
                f"   ❌ {video_id}: {type(e).__name__}: {e}"
            )
            self._last_scrape_stats["failed"] += 1
            return False

    # ═══════════════════════════════════════════════
    # send_message — غیرفعال
    # ═══════════════════════════════════════════════
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        log_info(f"⏭️ [youtube:send] غیرفعال | chat_id={chat_id}")
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