# -*- coding: utf-8 -*-
"""
Self-monitor — proactively detects RUNTIME problems and alerts admins on Bale.

Honest scope (per research): a local model cannot reliably find code bugs, but
rule-based watching of logs/metrics reliably catches runtime issues:
  - bursts of ERROR/CRITICAL in the logs
  - a platform that hasn't polled for too long (likely disconnected/stuck)
  - database health / growth problems
  - AI (Ollama) being down (so admin messages stay unanswered)
When a NEW problem appears it messages the admins (and optionally adds a short
AI suggestion). It de-dupes so it never spams. Light: reads a log tail every
few minutes; the AI is called only when there's actually a problem.
"""
import os
import json
import asyncio
from datetime import datetime, timedelta

from core.logger import log_info, log_warning, get_logger
from core import messenger_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(ROOT, "storage", "logs", "app.jsonl")
DB_PATH = os.path.join(ROOT, "storage", "database", "messages.db")


class SelfMonitor:
    def __init__(self, db=None):
        self.db = db
        from config import PLUGINS_CONFIG

        sm = PLUGINS_CONFIG.get("selfmonitor", {})
        self.enabled = sm.get("enabled", True)
        self.interval = int(sm.get("interval_seconds", 600))
        self.error_threshold = int(sm.get("error_threshold", 5))
        self.stale_minutes = int(sm.get("stale_minutes", 60))
        self.admin_ids = PLUGINS_CONFIG.get("adminhandler", {}).get("admin_ids", [])
        self.platform = PLUGINS_CONFIG.get("adminhandler", {}).get("platform", "bale")
        # کدام پلتفرم‌ها فعال‌اند (برای بررسیِ گیرکردن)
        self.watch = [
            p
            for p in (
                "eitaa",
                "bale",
                "rubika",
                "telegram",
                "whatsapp",
                "linkedin",
                "web",
            )
            if PLUGINS_CONFIG.get(f"{p}collector", {}).get("enabled", False)
            or p == "web"
            and PLUGINS_CONFIG.get("webcollector", {}).get("enabled", False)
        ]
        self._last_signature = None
        self._running = True
        self._prev_counts = {}  # platform -> تعداد سرنخِ آخرین دور (تشخیصِ گیرکردنِ بی‌صدای)
        self._last_check_counts = {}

    async def run_periodic(self):
        if not self.enabled:
            log_info("Self-monitor disabled")
            return
        log_info(f"🩺 Self-monitor started (every {self.interval}s)")
        # کمی صبر تا سیستم بالا بیاید
        await asyncio.sleep(min(60, self.interval))
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                log_warning(f"self-monitor error: {e}")
            await asyncio.sleep(self.interval)

    def _lead_counts(self) -> dict:
        """تعداد سرنخ (کلیدواژه‌دار) به‌تفکیک پلتفرم — برای تشخیصِ «گیرکرده ولی پول‌کننده»."""
        out = {}
        if not os.path.exists(DB_PATH):
            return out
        try:
            import sqlite3

            c = sqlite3.connect(DB_PATH)
            for p, n in c.execute(
                "SELECT platform, COUNT(*) FROM messages WHERE keywords!='' GROUP BY platform"
            ):
                out[p] = n
            c.close()
        except Exception:
            pass
        return out

    async def check_once(self):
        problems = []

        # ۱) خطاهای اخیرِ لاگ
        errs = self._recent_errors()
        if len(errs) >= self.error_threshold:
            sample = errs[-1].get("msg", "")[:120]
            problems.append(f"❗️ {len(errs)} خطا در لاگِ اخیر. نمونه: {sample}")

        # ۲) پلتفرمِ گیرکرده/قطع — آستانه نسبت به فاصلهٔ پولِ همان پلتفرم
        # (وب هر ۲۴ساعت پول می‌شود؛ نباید با ۶۰دقیقه «گیرکرده» تلقی شود).
        counts = self._lead_counts()
        for p in self.watch:
            try:
                last = await self.db.get_last_poll(p) if self.db else None
            except Exception:
                last = None
            if last:
                mins = (datetime.now() - last).total_seconds() / 60
                if mins > self._idle_limit(p):
                    problems.append(
                        f"⚠️ {p}: {int(mins)} دقیقه است فعالیتی ندارد (شاید قطع/گیرکرده)."
                    )
            else:
                # فعال است ولی هنوز هیچ پولی ثبت نشده
                problems.append(f"ℹ️ {p}: هنوز اولین پول ثبت نشده.")

        # ۳) دیتابیس: حجم و رشد غیرعادی
        db_prob = self._db_health()
        if db_prob:
            problems.append(db_prob)

        # ۴) هوش (Ollama): اگر مدیر جواب نمی‌گیرد، این را زود بفهمیم
        ai_prob = await self._ai_health()
        if ai_prob:
            problems.append(ai_prob)

        if not problems:
            self._last_signature = None
            return

        signature = " | ".join(problems)
        if signature == self._last_signature:
            return  # همان مشکلِ قبلی → دوباره هشدار نده (ضدِ اسپم)
        self._last_signature = signature

        text = "🩺 گزارشِ خودکارِ سلامت:\n" + "\n".join(problems)
        suggestion = await self._ai_suggestion(errs)
        if suggestion:
            text += f"\n\n💡 پیشنهاد: {suggestion}"
        await self._alert(text)
        log_warning("self-monitor alerted admins: " + signature)

    def _db_health(self) -> str:
        """بررسی حجم/قفلِ دیتابیس SQLite (سبک، بدونِ کوئری سنگین)."""
        if not os.path.exists(DB_PATH):
            return ""
        try:
            size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
            if size_mb > 500:
                return f"🗄️ دیتابیس {size_mb:.0f}MB شده — پاک‌سازی (cleanup) توصیه می‌شود."
            # فایلِ WAL بزرگ = تراکنش‌های باز/گیرکرده
            wal = DB_PATH + "-wal"
            if os.path.exists(wal) and os.path.getsize(wal) > 100 * 1024 * 1024:
                return f"🗄️ فایل WAL دیتابیس بزرگ شده ({(os.path.getsize(wal)/1048576):.0f}MB) — احتمالاً یک تراکنش گیر کرده."
        except Exception:
            pass
        return ""

    async def _ai_health(self) -> str:
        """اگر هوش (Ollama/API) پایین است و مدیر پیام می‌دهد جواب نمی‌گیرد."""
        try:
            from config import AI as AI_CONFIG

            if not AI_CONFIG.get("enabled", True):
                return ""
            from core.ai_assistant import get_ai_assistant

            ai = get_ai_assistant()
            if not await ai.available():
                return (
                    f"🤖 هوش (Ollama) در دسترس نیست — مدیر پیام بدهد جواب نمی‌گیرد. "
                    f"چک کن: {AI_CONFIG.get('url', 'http://localhost:11434')}"
                )
        except Exception:
            pass
        return ""

    def _idle_limit(self, platform: str) -> float:
        """حداکثر دقیقهٔ مجازِ بی‌فعالیتی = بیشینهٔ (stale_minutes، ۳× فاصلهٔ پولِ آن پلتفرم)."""
        from config import PLUGINS_CONFIG, SYSTEM_CONFIG

        if platform == "web":
            secs = SYSTEM_CONFIG.get("poll_interval_hours", 24) * 3600
        else:
            secs = PLUGINS_CONFIG.get(f"{platform}collector", {}).get(
                "poll_seconds", 120
            )
        return max(self.stale_minutes, (secs * 3) / 60)

    def _recent_errors(self):
        out = []
        if not os.path.exists(LOG_FILE):
            return out
        cutoff = datetime.now() - timedelta(seconds=self.interval)
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-400:]
            for ln in lines:
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if o.get("level") not in ("ERROR", "CRITICAL"):
                    continue
                ts = o.get("ts", "")
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(
                        tzinfo=None
                    )
                    if when < cutoff:
                        continue
                except Exception:
                    pass
                out.append(o)
        except Exception:
            pass
        return out

    async def _ai_suggestion(self, errs) -> str:
        if not errs:
            return ""
        try:
            from core.ai_assistant import get_ai_assistant

            ai = get_ai_assistant()
            if not await ai.available():
                return ""
            msgs = "\n".join(e.get("msg", "")[:150] for e in errs[-5:])
            return await ai.chat(
                f"این خطاهای ربات را در یک جملهٔ کوتاهِ فارسی خلاصه کن و یک پیشنهادِ عملی بده:\n{msgs}"
            )
        except Exception:
            return ""

    async def _alert(self, text: str):
        sender = messenger_registry.get_sender(self.platform)
        if not sender or not self.admin_ids:
            log_warning(
                "self-monitor: no sender/admins to alert (set ADMIN_IDS + connect Bale)"
            )
            return
        for aid in self.admin_ids:
            try:
                await sender(aid, "private", text)
            except Exception as e:
                log_warning(f"self-monitor alert to {aid} failed: {e}")

    def stop(self):
        self._running = False


async def start_self_monitor(db=None):
    sm = SelfMonitor(db)
    if sm.enabled:
        asyncio.create_task(sm.run_periodic())
    return sm
