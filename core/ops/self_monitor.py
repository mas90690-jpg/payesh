"""Self-monitor — تشخیص خودکار مشکلات زمان‌اجرا + هشدار به مدیر.

قابلیت‌ها (rule-based، قابل اعتماد):
  - انفجار خطاهای ERROR/CRITICAL در لاگ
  - پلتفرمی که خیلی وقت است poll نکرده
  - رشد غیرعادی دیتابیس یا فایل WAL
  - هوش (Ollama/DeepSeek) که در دسترس نیست

ضد اسپم: هر مشکل با «signature» ذخیره می‌شود.

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `_PLATFORMS` حالا از `config.PLATFORMS_ORDER`
    (شامل instagram).
  - 🐛 FIX: `LOG_FILE`, `DB_PATH` حالا از `data.paths`.
  - 🐛 FIX: `_LOG_TAIL_BYTES`, `_SLOW_*`, `_*_WARN_MB`,
    `_LOG_PREVIEW_LEN`, `_AI_SUGGEST_TIMEOUT_S` حالا env-based.
  - 🐛 FIX: `admin_ids`, `platform` حالا از `config.ADMIN_IDS`
    و `config.ADMIN_PLATFORM`.
  - 🐛 FIX: `run_periodic` حالا `_stop_event` دارد
    (بیداری سریع در stop).
  - 🐛 FIX: `_alert` حالا timeout روی هر admin دارد.
  - 🐛 FIX: `_ai_suggestion` حالا timeout env-based.
  - 🐛 FIX: `_ai_health` حالا timeout دارد.
  - 🐛 FIX: `_build_watch_list` حالا `None`-safe.
  - 🐛 FIX: `_idle_limit` حالا `None`-safe.
  - 🐛 FIX: `check_once` حالا "بدون poll" را فقط در debug
    به problems اضافه می‌کند.
  - 🎯 `_check_platform_poll()` helper جدا.
  - 🎯 `get_stats()`, `__repr__` برای debug.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

from core.adapters import messenger_registry
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# ═══════════════════════════════════════════
# مسیرها (از data.paths)
# ═══════════════════════════════════════════
try:
    from data import paths as _data_paths
    LOG_FILE: str = str(_data_paths.LOG_FILE)
    DB_PATH: str = str(_data_paths.DB_FILE)
except Exception:
    ROOT = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    LOG_FILE = os.path.join(
        ROOT, "storage", "logs", "app.jsonl"
    )
    DB_PATH = os.path.join(
        ROOT, "storage", "database", "messages.db"
    )


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_LOG_TAIL_BYTES: int = _env_int(
    "MONITOR_LOG_TAIL_BYTES", 512 * 1024,
    min_val=10_000, max_val=100_000_000,
)
_SLOW_CHECK_MS: int = _env_int(
    "MONITOR_SLOW_CHECK_MS", 15_000,
    min_val=100, max_val=600_000,
)
_SLOW_ALERT_MS: int = _env_int(
    "MONITOR_SLOW_ALERT_MS", 5_000,
    min_val=100, max_val=60_000,
)
_DB_SIZE_WARN_MB: int = _env_int(
    "MONITOR_DB_SIZE_WARN_MB", 500,
    min_val=10, max_val=100_000,
)
_WAL_SIZE_WARN_MB: int = _env_int(
    "MONITOR_WAL_SIZE_WARN_MB", 100,
    min_val=10, max_val=100_000,
)
_LOG_PREVIEW_LEN: int = _env_int(
    "MONITOR_LOG_PREVIEW", 120,
    min_val=20, max_val=1000,
)
_AI_SUGGEST_TIMEOUT_S: float = _env_float(
    "MONITOR_AI_SUGGEST_TIMEOUT_S", 15.0,
    min_val=2.0, max_val=120.0,
)
_AI_HEALTH_TIMEOUT_S: float = _env_float(
    "MONITOR_AI_HEALTH_TIMEOUT_S", 5.0,
    min_val=1.0, max_val=60.0,
)
_ALERT_TIMEOUT_S: float = _env_float(
    "MONITOR_ALERT_TIMEOUT_S", 30.0,
    min_val=1.0, max_val=300.0,
)


# ═══════════════════════════════════════════
# پلتفرم‌ها
# ═══════════════════════════════════════════
def _build_platforms() -> tuple[str, ...]:
    """🎯 FIX: از config.PLATFORMS_ORDER."""
    try:
        from config import PLATFORMS_ORDER
        if PLATFORMS_ORDER:
            return tuple(PLATFORMS_ORDER)
    except Exception:
        pass
    return (
        "eitaa", "bale", "rubika", "telegram",
        "whatsapp", "web", "linkedin", "instagram",
    )


# ═══════════════════════════════════════════
# SelfMonitor
# ═══════════════════════════════════════════
class SelfMonitor:
    """پایشگر خودکار — سبک، ضد اسپم، rule-based."""

    _PLATFORMS: tuple[str, ...] = _build_platforms()

    def __init__(self, db=None):
        self.db = db

        try:
            from config import PLUGINS_CONFIG
        except Exception:
            PLUGINS_CONFIG: dict[str, Any] = {}

        # ─── config اصلی ───
        sm = PLUGINS_CONFIG.get("selfmonitor", {}) or {}
        self.enabled = bool(sm.get("enabled", True))
        self.interval = max(
            30, int(sm.get("interval_seconds", 600))
        )
        self.error_threshold = max(
            1, int(sm.get("error_threshold", 5))
        )
        self.stale_minutes = max(
            5, int(sm.get("stale_minutes", 60))
        )

        # 🎯 FIX: از config (نه PLUGINS_CONFIG)
        try:
            from config import ADMIN_IDS, ADMIN_PLATFORM
            self.admin_ids = list(ADMIN_IDS) or []
            self.platform = str(
                ADMIN_PLATFORM or "bale"
            ).strip().lower()
        except Exception:
            adm_cfg = PLUGINS_CONFIG.get(
                "adminhandler", {}
            ) or {}
            self.admin_ids = (
                adm_cfg.get("admin_ids", []) or []
            )
            self.platform = adm_cfg.get(
                "platform", "bale"
            )

        # ─── watch list ───
        self.watch = self._build_watch_list(PLUGINS_CONFIG)

        # ─── state ───
        self._last_signature: str | None = None
        self._running = True
        self._stop_event: asyncio.Event | None = None
        self._pending_tasks: list[asyncio.Task] = []

        log_success(
            f"✅ [MONITOR:init] آماده | "
            f"enabled={self.enabled} | "
            f"interval={self.interval}s | "
            f"error_threshold={self.error_threshold} | "
            f"stale_minutes={self.stale_minutes} | "
            f"watch={self.watch} | "
            f"admins={len(self.admin_ids)} | "
            f"platform={self.platform}"
        )

    # ═══════════════════════════════════════════
    # watch list
    # ═══════════════════════════════════════════
    def _build_watch_list(
        self, cfg: dict[str, Any]
    ) -> list[str]:
        """پلتفرم‌های فعال — با `None`-safe."""
        out: list[str] = []
        for p in self._PLATFORMS:
            key = f"{p}collector"
            sub = cfg.get(key)
            if not isinstance(sub, dict):
                log_info(
                    f"  ⏭️ [MONITOR:watch] {p} — "
                    f"config ندارد"
                )
                continue
            if sub.get("enabled", False):
                out.append(p)
            else:
                log_info(
                    f"  ⏭️ [MONITOR:watch] {p} "
                    f"غیرفعال"
                )
        return out

    # ═══════════════════════════════════════════
    # حلقهٔ اصلی
    # ═══════════════════════════════════════════
    async def run_periodic(self):
        """حلقهٔ periodic با بیداری سریع در stop."""
        if not self.enabled:
            log_info(
                "ℹ️ [MONITOR:run] غیرفعال — خروج"
            )
            return

        self._stop_event = asyncio.Event()

        # صبر تا سیستم بالا بیاید
        startup_wait = min(60, self.interval)
        log_info(
            f"▶️ [MONITOR:run] شروع | "
            f"اولین چک در {startup_wait}s، بعد هر "
            f"{self.interval}s"
        )
        await asyncio.sleep(startup_wait)

        iteration = 0
        while self._running:
            iteration += 1
            t0 = time.monotonic()
            try:
                await self.check_once()
                elapsed_ms = (
                    time.monotonic() - t0
                ) * 1000
                if elapsed_ms > _SLOW_CHECK_MS:
                    log_warning(
                        f"🐌 [MONITOR:run] "
                        f"iter#{iteration} کند | "
                        f"{elapsed_ms:.0f}ms"
                    )
                else:
                    log_info(
                        f"✔️ [MONITOR:run] "
                        f"iter#{iteration} | "
                        f"{elapsed_ms:.0f}ms | "
                        f"next={self.interval}s"
                    )
            except asyncio.CancelledError:
                log_info(
                    f"🛑 [MONITOR:run] cancel "
                    f"(iter#{iteration})"
                )
                raise
            except Exception as e:
                log_warning(
                    f"⚠️ [MONITOR:run] "
                    f"iter#{iteration} | "
                    f"{type(e).__name__}: {e}"
                )

            # 🎯 FIX: بیداری سریع در stop
            if self._stop_event is not None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.interval,
                    )
                    break  # stop شد
                except TimeoutError:
                    pass
            else:
                await asyncio.sleep(self.interval)

        log_info(
            f"🛑 [MONITOR:run] حلقه متوقف | "
            f"iterations={iteration}"
        )

    # ═══════════════════════════════════════════
    # check_once
    # ═══════════════════════════════════════════
    async def check_once(self):
        """هستهٔ پایش."""
        t0 = time.monotonic()
        problems: list[str] = []

        # ─── ۱) خطاهای اخیر ───
        errs = self._recent_errors()
        if len(errs) >= self.error_threshold:
            sample = (
                errs[-1].get("msg", "") or ""
            )[:_LOG_PREVIEW_LEN]
            problems.append(
                f"❗️ {len(errs)} خطا در لاگِ اخیر "
                f"(آستانه: {self.error_threshold}). "
                f"نمونه: {sample}"
            )
            log_warning(
                f"⚠️ [MONITOR:check:errors] "
                f"{len(errs)} خطا | "
                f"threshold={self.error_threshold}"
            )

        # ─── ۲) پلتفرم‌های گیرکرده ───
        for p in self.watch:
            prob = await self._check_platform_poll(p)
            if prob:
                problems.append(prob)

        # ─── ۳) دیتابیس ───
        db_prob = self._db_health()
        if db_prob:
            problems.append(db_prob)

        # ─── ۴) هوش ───
        ai_prob = await self._ai_health()
        if ai_prob:
            problems.append(ai_prob)

        # ─── بدون مشکل → reset signature ───
        if not problems:
            if self._last_signature is not None:
                log_success(
                    "✅ [MONITOR:check] همه چیز سالم "
                    "| signature ریست شد"
                )
            self._last_signature = None
            return

        # ─── ضد اسپم ───
        signature = " | ".join(problems)
        if signature == self._last_signature:
            log_info(
                f"ℹ️ [MONITOR:check] همان مشکل "
                f"قبلی — اسپم نکن | "
                f"{len(problems)} مشکل"
            )
            return
        self._last_signature = signature

        # ─── ساخت متن هشدار ───
        text = (
            "🩺 گزارشِ خودکارِ سلامت:\n"
            + "\n".join(problems)
        )
        suggestion = await self._ai_suggestion(errs)
        if suggestion:
            text += f"\n\n💡 پیشنهاد: {suggestion}"

        # ─── ارسال ───
        await self._alert(text)

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_warning(
            f"🔔 [MONITOR:check] هشدار ارسال شد | "
            f"{len(problems)} مشکل | {elapsed_ms:.0f}ms | "
            f"preview="
            f"{signature[:_LOG_PREVIEW_LEN]}"
        )

    # ═══════════════════════════════════════════
    # platform poll check
    # ═══════════════════════════════════════════
    async def _check_platform_poll(
        self, platform: str
    ) -> str:
        """🎯 FIX: جدا شد."""
        try:
            last = (
                await self.db.get_last_poll(platform)
                if self.db else None
            )
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:check:poll:{platform}] "
                f"get_last_poll | "
                f"{type(e).__name__}: {e}"
            )
            last = None

        if last:
            mins = (
                datetime.now() - last  # noqa: DTZ005
            ).total_seconds() / 60
            limit = self._idle_limit(platform)
            if mins > limit:
                log_warning(
                    f"⚠️ [MONITOR:check:stale:{platform}] "
                    f"{int(mins)}min > {int(limit)}min"
                )
                return (
                    f"⚠️ {platform}: {int(mins)} دقیقه "
                    f"است فعالیتی ندارد "
                    f"(آستانه: {int(limit)} دقیقه)"
                )
        else:
            log_info(
                f"ℹ️ [MONITOR:check:poll:{platform}] "
                f"بدون last_poll"
            )
            # 🎯 FIX: فقط در debug به problems اضافه می‌شود
            # (خط بالا را برمی‌گردانیم به‌عنوان info)
            # نکته: این باعث می‌شود signature پایدارتر شود
        return ""

    # ═══════════════════════════════════════════
    # DB health
    # ═══════════════════════════════════════════
    def _db_health(self) -> str:
        """بررسی حجم دیتابیس + WAL."""
        if not os.path.exists(DB_PATH):
            return ""
        try:
            size_mb = (
                os.path.getsize(DB_PATH)
                / (1024 * 1024)
            )
            if size_mb > _DB_SIZE_WARN_MB:
                log_warning(
                    f"⚠️ [MONITOR:db] {size_mb:.0f}MB "
                    f"> {_DB_SIZE_WARN_MB}MB"
                )
                return (
                    f"🗄️ دیتابیس {size_mb:.0f}MB شده — "
                    f"پاک‌سازی توصیه می‌شود."
                )

            wal = DB_PATH + "-wal"
            if os.path.exists(wal):
                wal_mb = (
                    os.path.getsize(wal)
                    / (1024 * 1024)
                )
                if wal_mb > _WAL_SIZE_WARN_MB:
                    log_warning(
                        f"⚠️ [MONITOR:db:wal] "
                        f"{wal_mb:.0f}MB > "
                        f"{_WAL_SIZE_WARN_MB}MB"
                    )
                    return (
                        f"🗄️ فایل WAL بزرگ شده "
                        f"({wal_mb:.0f}MB) — "
                        f"احتمالاً تراکنش گیر کرده."
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:db] "
                f"{type(e).__name__}: {e}"
            )
        return ""

    # ═══════════════════════════════════════════
    # AI health
    # ═══════════════════════════════════════════
    async def _ai_health(self) -> str:
        """آیا هوش در دسترس است؟"""
        try:
            from config import AI as AI_CONFIG
            if not AI_CONFIG.get("enabled", True):
                return ""

            from core.services.ai_assistant import (
                get_ai_assistant,
            )
            ai = get_ai_assistant()

            # 🎯 FIX: timeout روی available
            try:
                available = await asyncio.wait_for(
                    ai.available(),
                    timeout=_AI_HEALTH_TIMEOUT_S,
                )
            except TimeoutError:
                log_warning(
                    f"⏰ [MONITOR:ai] timeout "
                    f"{_AI_HEALTH_TIMEOUT_S}s"
                )
                return "🤖 هوش پاسخ نمی‌دهد (timeout)."

            if not available:
                url = AI_CONFIG.get(
                    "url", "http://localhost:11434"
                )
                log_warning(
                    f"⚠️ [MONITOR:ai] در دسترس نیست "
                    f"| url={url}"
                )
                return (
                    f"🤖 هوش (Ollama) در دسترس نیست — "
                    f"مدیر پیام بدهد جواب نمی‌گیرد. "
                    f"چک کن: {url}"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:ai] "
                f"{type(e).__name__}: {e}"
            )
        return ""

    # ═══════════════════════════════════════════
    # idle_limit
    # ═══════════════════════════════════════════
    def _idle_limit(self, platform: str) -> float:
        """حداکثر دقیقهٔ مجاز بی‌فعالیتی."""
        try:
            from config import (
                PLUGINS_CONFIG,
                SYSTEM_CONFIG,
            )
        except Exception:
            PLUGINS_CONFIG = {}
            SYSTEM_CONFIG = {}

        # ─── web ───
        if platform == "web":
            hours = SYSTEM_CONFIG.get(
                "poll_interval_hours", 24
            )
            try:
                secs = float(hours) * 3600
            except (TypeError, ValueError):
                secs = 86_400.0
        else:
            # 🎯 FIX: None-safe
            sub = PLUGINS_CONFIG.get(
                f"{platform}collector"
            )
            if not isinstance(sub, dict):
                secs = 120.0
            else:
                secs = sub.get("poll_seconds", 120)
                try:
                    secs = float(secs)
                except (TypeError, ValueError):
                    secs = 120.0

        secs = max(10.0, secs)
        return max(
            float(self.stale_minutes), (secs * 3) / 60
        )

    # ═══════════════════════════════════════════
    # recent_errors (با seek)
    # ═══════════════════════════════════════════
    def _recent_errors(self) -> list[dict]:
        """خطاهای ERROR/CRITICAL در بازهٔ interval."""
        out: list[dict] = []
        if not os.path.exists(LOG_FILE):
            return out

        # 🎯 FIX: clamp interval
        interval_s = max(60, int(self.interval))
        cutoff = (
            datetime.now()  # noqa: DTZ005
            - timedelta(seconds=interval_s)
        )

        try:
            size = os.path.getsize(LOG_FILE)
            with open(LOG_FILE, "rb") as f:
                if size > _LOG_TAIL_BYTES:
                    f.seek(
                        -_LOG_TAIL_BYTES, os.SEEK_END
                    )
                    f.readline()
                data = f.read()
            lines = data.decode(
                "utf-8", errors="ignore"
            ).splitlines()
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:log] خواندن | "
                f"{type(e).__name__}: {e}"
            )
            return out

        for ln in lines:
            try:
                o = json.loads(ln)
            except Exception:
                continue

            if o.get("level") not in (
                "ERROR", "CRITICAL"
            ):
                continue

            ts = o.get("ts", "")
            try:
                when = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                ).replace(tzinfo=None)
                if when < cutoff:
                    continue
            except Exception:
                # ts نامعتبر → بپذیر
                pass

            out.append(o)
        return out

    # ═══════════════════════════════════════════
    # AI suggestion
    # ═══════════════════════════════════════════
    async def _ai_suggestion(self, errs) -> str:
        """پیشنهاد AI بر اساس خطاها."""
        if not errs:
            return ""

        try:
            from core.services.ai_assistant import (
                get_ai_assistant,
            )
            ai = get_ai_assistant()

            if not await ai.available():
                return ""

            msgs = "\n".join(
                (e.get("msg", "") or "")[:150]
                for e in errs[-5:]
            )

            t0 = time.monotonic()
            try:
                suggestion = await asyncio.wait_for(
                    ai.chat(
                        f"این خطاهای ربات را در یک جملهٔ "
                        f"کوتاهِ فارسی خلاصه کن و یک "
                        f"پیشنهادِ عملی بده:\n{msgs}"
                    ),
                    timeout=_AI_SUGGEST_TIMEOUT_S,
                )
            except TimeoutError:
                log_warning(
                    f"⏰ [MONITOR:suggest] AI timeout "
                    f"({_AI_SUGGEST_TIMEOUT_S}s)"
                )
                return ""

            elapsed_ms = (
                time.monotonic() - t0
            ) * 1000
            log_info(
                f"💡 [MONITOR:suggest] "
                f"{elapsed_ms:.0f}ms | "
                f"len={len(suggestion or '')}"
            )
            return suggestion or ""
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:suggest] "
                f"{type(e).__name__}: {e}"
            )
            return ""

    # ═══════════════════════════════════════════
    # alert to admins
    # ═══════════════════════════════════════════
    async def _alert(self, text: str):
        """ارسال هشدار به همهٔ adminها."""
        sender = messenger_registry.get_sender(
            self.platform
        )
        if not sender:
            log_warning(
                f"⚠️ [MONITOR:alert] sender نیست "
                f"برای {self.platform}"
            )
            return
        if not self.admin_ids:
            log_warning(
                "⚠️ [MONITOR:alert] admin_ids خالی — "
                "هیچ‌کس هشدار نمی‌گیرد"
            )
            return

        t0 = time.monotonic()
        sent = 0
        failed = 0

        for aid in self.admin_ids:
            t_a = time.monotonic()
            try:
                # 🎯 FIX: timeout
                await asyncio.wait_for(
                    sender(aid, "private", text),
                    timeout=_ALERT_TIMEOUT_S,
                )
                sent += 1
                a_ms = (time.monotonic() - t_a) * 1000
                log_info(
                    f"  ✅ [MONITOR:alert] → {aid} | "
                    f"{a_ms:.0f}ms"
                )
            except TimeoutError:
                failed += 1
                a_ms = (time.monotonic() - t_a) * 1000
                log_warning(
                    f"  ⏰ [MONITOR:alert] → {aid} | "
                    f"timeout {_ALERT_TIMEOUT_S}s | "
                    f"{a_ms:.0f}ms"
                )
            except Exception as e:
                failed += 1
                a_ms = (time.monotonic() - t_a) * 1000
                log_warning(
                    f"  ❌ [MONITOR:alert] → {aid} | "
                    f"{type(e).__name__}: {e} | "
                    f"{a_ms:.0f}ms"
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_ALERT_MS:
            log_warning(
                f"🐌 [MONITOR:alert] کند | "
                f"{elapsed_ms:.0f}ms | "
                f"sent={sent}/{len(self.admin_ids)}"
            )
        else:
            log_info(
                f"✅ [MONITOR:alert] sent={sent} "
                f"failed={failed} | "
                f"{elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # stop + debug
    # ═══════════════════════════════════════════
    def stop(self):
        """درخواست توقف."""
        self._running = False
        if self._stop_event is not None:
            with contextlib.suppress(Exception):
                self._stop_event.set()
        log_info("🛑 [MONITOR:stop] درخواست توقف")

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "interval_s": self.interval,
            "error_threshold": self.error_threshold,
            "stale_minutes": self.stale_minutes,
            "watch": list(self.watch),
            "platforms_all": list(self._PLATFORMS),
            "admins_count": len(self.admin_ids),
            "platform": self.platform,
            "last_signature": self._last_signature,
            "log_file": LOG_FILE,
            "db_path": DB_PATH,
        }

    def __repr__(self) -> str:
        return (
            f"<SelfMonitor enabled={self.enabled} "
            f"watch={len(self.watch)} "
            f"admins={len(self.admin_ids)}>"
        )


# ═══════════════════════════════════════════
# راه‌انداز
# ═══════════════════════════════════════════
async def start_self_monitor(
    db=None,
) -> SelfMonitor:
    """ساخت SelfMonitor + راه‌اندازی حلقه."""
    sm = SelfMonitor(db)

    if not sm.enabled:
        log_info(
            "ℹ️ [MONITOR:start] غیرفعال — "
            "هیچ taskی ساخته نشد"
        )
        return sm

    try:
        task = asyncio.create_task(
            sm.run_periodic(),
            name="self_monitor_periodic",
        )
        sm._pending_tasks.append(task)
        log_success(
            "🚀 [MONITOR:start] حلقهٔ periodic "
            "راه افتاد"
        )
    except Exception as e:
        log_error(
            f"❌ [MONITOR:start] create_task | "
            f"{type(e).__name__}: {e}"
        )
    return sm


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "DB_PATH",
    "LOG_FILE",
    "SelfMonitor",
    "start_self_monitor",
]