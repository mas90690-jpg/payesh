"""
Self-monitor — تشخیص خودکار مشکلات زمان‌اجرا + هشدار به مدیر.

قابلیت‌ها (rule-based، قابل اعتماد):
  - انفجار خطاهای ERROR/CRITICAL در لاگ
  - پلتفرمی که خیلی وقته poll نکرده (احتمال قطع/گیرکردن)
  - رشد غیرعادی دیتابیس یا فایل WAL
  - هوش (Ollama/DeepSeek) که در دسترس نیست

ضد اسپم: هر مشکل با «signature» ذخیره می‌شه؛ اگه تکرار بشه، دوباره هشدار
نمی‌ده. AI فقط وقتی صدا زده می‌شه که واقعاً مشکل جدیدی هست.

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [MONITOR:...]:
      * run_periodic: شروع + هر iteration با زمان
      * check_once: تعداد problems + signature preview + زمان کل
      * هر چک: نتیجه دقیق (pass/fail با آستانه)
      * alert: per-admin delivery + timing
      * خطاها: type(e).__name__ + context
  - 🐛 FIX: comprehension watch ساده و واضح شد (webcollector جداگانه)
  - 🐛 FIX: _recent_errors با seek از انتهای فایل (نه کل فایل در RAM)
  - 🐛 FIX: except: pass بی‌صدا → همیشه با type(e).__name__ لاگ می‌شود
  - 🐛 FIX: _idle_limit حالا حداقلِ stale_minutes را تضمین می‌کند (کلیپ منفی)
  - 🐛 FIX: error_threshold حالا حداقل 1 است (0 → 1)
  - 🧹 حذف کد مرده (_prev_counts, _last_check_counts)
  - 🧹 start_self_monitor حالا در حالت disabled هم لاگ می‌کند
  - 🧹 آستانه‌های هشدار در ثابت‌های بالای فایل
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

from core.adapters import messenger_registry
from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── مسیرها ───
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(ROOT, "storage", "logs", "app.jsonl")
DB_PATH = os.path.join(ROOT, "storage", "database", "messages.db")

# ─── ثابت‌ها ───
_LOG_TAIL_BYTES = 512 * 1024     # 512KB از انتهای لاگ کافی است
_SLOW_CHECK_MS = 15_000          # check_once > 15s
_SLOW_ALERT_MS = 5_000           # _alert > 5s
_DB_SIZE_WARN_MB = 500
_WAL_SIZE_WARN_MB = 100
_LOG_PREVIEW_LEN = 120


class SelfMonitor:
    """پایشگر خودکار — سبک، ضد اسپم، rule-based."""

    # پلتفرم‌های شناخته‌شده (برای ترتیب و لاگ پایدار)
    _PLATFORMS = (
        "eitaa", "bale", "rubika", "telegram",
        "whatsapp", "linkedin", "web",
    )

    def __init__(self, db=None):
        self.db = db
        from config import PLUGINS_CONFIG

        sm = PLUGINS_CONFIG.get("selfmonitor", {})
        self.enabled = bool(sm.get("enabled", True))
        self.interval = max(30, int(sm.get("interval_seconds", 600)))
        # 🐛 FIX: حداقل 1 — قبلاً 0 یا منفی هم قبول بود
        self.error_threshold = max(1, int(sm.get("error_threshold", 5)))
        self.stale_minutes = max(5, int(sm.get("stale_minutes", 60)))

        adm_cfg = PLUGINS_CONFIG.get("adminhandler", {})
        self.admin_ids = adm_cfg.get("admin_ids", []) or []
        self.platform = adm_cfg.get("platform", "bale")

        # 🐛 FIX: comprehension واضح — هر پلتفرم کلید config خودش را دارد
        # (web → webcollector، بقیه → {platform}collector)
        # نکته: «وب» همین الان با webcollector کار می‌کند، پس نیازی به
        # شرط جداگانه نیست.
        self.watch = self._build_watch_list(PLUGINS_CONFIG)

        self._last_signature: str | None = None
        self._running = True

        log_success(
            f"✅ [MONITOR:init] آماده | enabled={self.enabled} | "
            f"interval={self.interval}s | "
            f"error_threshold={self.error_threshold} | "
            f"stale_minutes={self.stale_minutes} | "
            f"watch={self.watch} | "
            f"admins={len(self.admin_ids)} | platform={self.platform}"
        )

    # ═══════════════════════════════════════════
    # ساخت watch list
    # ═══════════════════════════════════════════
    def _build_watch_list(self, cfg: dict[str, Any]) -> list[str]:
        """پلتفرم‌های فعال — برای بررسیِ «گیرکردن».

        برای هر پلتفرم، کلید config آن `{p}collector` است. پلتفرم «web»
        با کلید «webcollector» هم‌خوانی دارد.
        """
        out: list[str] = []
        for p in self._PLATFORMS:
            key = f"{p}collector"
            enabled = cfg.get(key, {}).get("enabled", False)
            if enabled:
                out.append(p)
            else:
                log_info(f"  ⏭️ [MONITOR:watch] {p} غیرفعال ({key}.enabled=False)")
        return out

    # ═══════════════════════════════════════════
    # حلقهٔ اصلی
    # ═══════════════════════════════════════════
    async def run_periodic(self):
        if not self.enabled:
            log_info("ℹ️ [MONITOR:run] غیرفعال — خروج")
            return

        # صبر تا سیستم بالا بیاد
        startup_wait = min(60, self.interval)
        log_info(
            f"▶️ [MONITOR:run] شروع | "
            f"اولین چک در {startup_wait}s، بعد هر {self.interval}s"
        )
        await asyncio.sleep(startup_wait)

        iteration = 0
        while self._running:
            iteration += 1
            t0 = time.monotonic()
            try:
                await self.check_once()
                elapsed_ms = (time.monotonic() - t0) * 1000
                if elapsed_ms > _SLOW_CHECK_MS:
                    log_warning(
                        f"🐌 [MONITOR:run] iter#{iteration} کند | "
                        f"{elapsed_ms:.0f}ms"
                    )
                else:
                    log_info(
                        f"✔️ [MONITOR:run] iter#{iteration} | "
                        f"{elapsed_ms:.0f}ms | next={self.interval}s"
                    )
            except asyncio.CancelledError:
                log_info(f"🛑 [MONITOR:run] cancel (iter#{iteration})")
                raise
            except Exception as e:
                log_warning(
                    f"⚠️ [MONITOR:run] iter#{iteration} | "
                    f"{type(e).__name__}: {e}"
                )
            await asyncio.sleep(self.interval)

    # ═══════════════════════════════════════════
    # check_once — هستهٔ پایش
    # ═══════════════════════════════════════════
    async def check_once(self):
        t0 = time.monotonic()
        problems: list[str] = []

        # ۱) خطاهای اخیر لاگ
        errs = self._recent_errors()
        if len(errs) >= self.error_threshold:
            sample = (errs[-1].get("msg", "") or "")[:_LOG_PREVIEW_LEN]
            problems.append(
                f"❗️ {len(errs)} خطا در لاگِ اخیر "
                f"(آستانه: {self.error_threshold}). نمونه: {sample}"
            )
            log_warning(
                f"⚠️ [MONITOR:check:errors] {len(errs)} خطا | "
                f"threshold={self.error_threshold}"
            )

        # ۲) پلتفرم‌های گیرکرده
        for p in self.watch:
            try:
                last = await self.db.get_last_poll(p) if self.db else None
            except Exception as e:
                log_warning(
                    f"⚠️ [MONITOR:check:poll:{p}] get_last_poll | "
                    f"{type(e).__name__}: {e}"
                )
                last = None

            if last:
                mins = (datetime.now() - last).total_seconds() / 60
                limit = self._idle_limit(p)
                if mins > limit:
                    problems.append(
                        f"⚠️ {p}: {int(mins)} دقیقه است فعالیتی ندارد "
                        f"(آستانه: {int(limit)} دقیقه)"
                    )
                    log_warning(
                        f"⚠️ [MONITOR:check:stale:{p}] {int(mins)}min > "
                        f"{int(limit)}min"
                    )
            else:
                problems.append(f"ℹ️ {p}: هنوز اولین poll ثبت نشده.")
                log_info(f"ℹ️ [MONITOR:check:poll:{p}] بدون last_poll")

        # ۳) دیتابیس
        db_prob = self._db_health()
        if db_prob:
            problems.append(db_prob)

        # ۴) هوش
        ai_prob = await self._ai_health()
        if ai_prob:
            problems.append(ai_prob)

        # بدون مشکل → reset signature
        if not problems:
            if self._last_signature is not None:
                log_success("✅ [MONITOR:check] همه چیز سالم | signature ریست شد")
            self._last_signature = None
            return

        # ضد اسپم: اگه signature عوض نشده → هشدار نده
        signature = " | ".join(problems)
        if signature == self._last_signature:
            log_info(
                f"ℹ️ [MONITOR:check] همان مشکل قبلی — اسپم نکن | "
                f"{len(problems)} مشکل"
            )
            return
        self._last_signature = signature

        # ساخت متن هشدار
        text = "🩺 گزارشِ خودکارِ سلامت:\n" + "\n".join(problems)
        suggestion = await self._ai_suggestion(errs)
        if suggestion:
            text += f"\n\n💡 پیشنهاد: {suggestion}"

        # ارسال
        await self._alert(text)

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_warning(
            f"🔔 [MONITOR:check] هشدار ارسال شد | "
            f"{len(problems)} مشکل | {elapsed_ms:.0f}ms | "
            f"preview={signature[:_LOG_PREVIEW_LEN]}"
        )

    # ═══════════════════════════════════════════
    # DB health
    # ═══════════════════════════════════════════
    def _db_health(self) -> str:
        """بررسی حجم دیتابیس + WAL."""
        if not os.path.exists(DB_PATH):
            return ""
        try:
            size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
            if size_mb > _DB_SIZE_WARN_MB:
                log_warning(
                    f"⚠️ [MONITOR:db] {size_mb:.0f}MB > "
                    f"{_DB_SIZE_WARN_MB}MB"
                )
                return (
                    f"🗄️ دیتابیس {size_mb:.0f}MB شده — "
                    f"پاک‌سازی (cleanup) توصیه می‌شود."
                )

            wal = DB_PATH + "-wal"
            if os.path.exists(wal):
                wal_mb = os.path.getsize(wal) / (1024 * 1024)
                if wal_mb > _WAL_SIZE_WARN_MB:
                    log_warning(
                        f"⚠️ [MONITOR:db:wal] {wal_mb:.0f}MB > "
                        f"{_WAL_SIZE_WARN_MB}MB"
                    )
                    return (
                        f"🗄️ فایل WAL دیتابیس بزرگ شده ({wal_mb:.0f}MB) — "
                        f"احتمالاً یک تراکنش گیر کرده."
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:db] {type(e).__name__}: {e}"
            )
        return ""

    # ═══════════════════════════════════════════
    # AI health
    # ═══════════════════════════════════════════
    async def _ai_health(self) -> str:
        """آیا هوش (Ollama/DeepSeek) در دسترس است؟"""
        try:
            from config import AI as AI_CONFIG
            if not AI_CONFIG.get("enabled", True):
                return ""
            from core.services.ai_assistant import get_ai_assistant
            ai = get_ai_assistant()
            available = await ai.available()
            if not available:
                url = AI_CONFIG.get("url", "http://localhost:11434")
                log_warning(
                    f"⚠️ [MONITOR:ai] در دسترس نیست | url={url}"
                )
                return (
                    f"🤖 هوش (Ollama) در دسترس نیست — "
                    f"مدیر پیام بدهد جواب نمی‌گیرد. چک کن: {url}"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:ai] {type(e).__name__}: {e}"
            )
        return ""

    # ═══════════════════════════════════════════
    # idle_limit
    # ═══════════════════════════════════════════
    def _idle_limit(self, platform: str) -> float:
        """حداکثر دقیقهٔ مجاز بی‌فعالیتی = max(stale_minutes, 3×poll).

        🐛 FIX: مقدار منفی کلیپ می‌شود.
        """
        from config import PLUGINS_CONFIG, SYSTEM_CONFIG

        if platform == "web":
            secs = SYSTEM_CONFIG.get("poll_interval_hours", 24) * 3600
        else:
            secs = PLUGINS_CONFIG.get(
                f"{platform}collector", {}
            ).get("poll_seconds", 120)

        # کلیپ به مقدار معقول
        try:
            secs = max(10, float(secs))
        except (TypeError, ValueError):
            secs = 120.0

        return max(float(self.stale_minutes), (secs * 3) / 60)

    # ═══════════════════════════════════════════
    # recent_errors (با seek از انتها)
    # ═══════════════════════════════════════════
    def _recent_errors(self) -> list[dict]:
        """خطاهای ERROR/CRITICAL در بازهٔ self.interval اخیر.

        🐛 FIX: فقط 512KB آخر فایل خوانده می‌شود (نه کل فایل).
        """
        out: list[dict] = []
        if not os.path.exists(LOG_FILE):
            return out

        cutoff = datetime.now() - timedelta(seconds=self.interval)
        try:
            size = os.path.getsize(LOG_FILE)
            with open(LOG_FILE, "rb") as f:
                if size > _LOG_TAIL_BYTES:
                    f.seek(-_LOG_TAIL_BYTES, os.SEEK_END)
                    f.readline()  # خط ناقص اول را دور بریز
                data = f.read()
            lines = data.decode("utf-8", errors="ignore").splitlines()
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:log] خواندن | {type(e).__name__}: {e}"
            )
            return out

        for ln in lines:
            try:
                o = json.loads(ln)
            except Exception:
                continue  # خط ناقص/نامعتبر
            if o.get("level") not in ("ERROR", "CRITICAL"):
                continue
            ts = o.get("ts", "")
            try:
                when = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                ).replace(tzinfo=None)
                if when < cutoff:
                    continue
            except Exception:
                # اگه ts نامعتبر بود، بپذیر (بدون قطع کردن)
                pass
            out.append(o)
        return out

    # ═══════════════════════════════════════════
    # AI suggestion
    # ═══════════════════════════════════════════
    async def _ai_suggestion(self, errs) -> str:
        if not errs:
            return ""
        try:
            from core.services.ai_assistant import get_ai_assistant
            ai = get_ai_assistant()
            if not await ai.available():
                return ""
            msgs = "\n".join(
                (e.get("msg", "") or "")[:150] for e in errs[-5:]
            )
            t0 = time.monotonic()
            try:
                suggestion = await asyncio.wait_for(
                    ai.chat(
                        f"این خطاهای ربات را در یک جملهٔ کوتاهِ فارسی "
                        f"خلاصه کن و یک پیشنهادِ عملی بده:\n{msgs}"
                    ),
                    timeout=15,
                )
            except TimeoutError:
                log_warning("⏰ [MONITOR:suggest] AI timeout (15s)")
                return ""
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"💡 [MONITOR:suggest] {elapsed_ms:.0f}ms | "
                f"len={len(suggestion or '')}"
            )
            return suggestion or ""
        except Exception as e:
            log_warning(
                f"⚠️ [MONITOR:suggest] {type(e).__name__}: {e}"
            )
            return ""

    # ═══════════════════════════════════════════
    # alert to admins
    # ═══════════════════════════════════════════
    async def _alert(self, text: str):
        sender = messenger_registry.get_sender(self.platform)
        if not sender:
            log_warning(
                f"⚠️ [MONITOR:alert] sender نیست برای {self.platform}"
            )
            return
        if not self.admin_ids:
            log_warning(
                "⚠️ [MONITOR:alert] admin_ids خالی — هیچ‌کس هشدار نمی‌گیرد"
            )
            return

        t0 = time.monotonic()
        sent = 0
        failed = 0
        for aid in self.admin_ids:
            t_a = time.monotonic()
            try:
                await sender(aid, "private", text)
                sent += 1
                a_ms = (time.monotonic() - t_a) * 1000
                log_info(
                    f"  ✅ [MONITOR:alert] → {aid} | {a_ms:.0f}ms"
                )
            except Exception as e:
                failed += 1
                a_ms = (time.monotonic() - t_a) * 1000
                log_warning(
                    f"  ❌ [MONITOR:alert] → {aid} | "
                    f"{type(e).__name__}: {e} | {a_ms:.0f}ms"
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_ALERT_MS:
            log_warning(
                f"🐌 [MONITOR:alert] کند | {elapsed_ms:.0f}ms | "
                f"sent={sent}/{len(self.admin_ids)}"
            )
        else:
            log_info(
                f"✅ [MONITOR:alert] sent={sent} failed={failed} | "
                f"{elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # stop
    # ═══════════════════════════════════════════
    def stop(self):
        self._running = False
        log_info("🛑 [MONITOR:stop] درخواست توقف")


# ═══════════════════════════════════════════
# تابع راه‌انداز
# ═══════════════════════════════════════════
async def start_self_monitor(db=None) -> SelfMonitor:
    """ساخت SelfMonitor و راه‌اندازی حلقهٔ periodic در پس‌زمینه."""
    sm = SelfMonitor(db)
    if sm.enabled:
        try:
            asyncio.create_task(sm.run_periodic())
            log_success("🚀 [MONITOR:start] حلقهٔ periodic راه افتاد")
        except Exception as e:
            log_error(
                f"❌ [MONITOR:start] create_task | "
                f"{type(e).__name__}: {e}"
            )
    else:
        log_info("ℹ️ [MONITOR:start] غیرفعال — هیچ taskی ساخته نشد")
    return sm
