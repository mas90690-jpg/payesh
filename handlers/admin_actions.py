"""اجرای دستورهای مدیر — DB، گزارش، CRM، لاگ، پارامترها، ریاستارت."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime

from core.adapters.command_parser import (
    extract_value,
    parse_set_param,
    platform_in,
)
from core.kernel.logger import log_error, log_info, log_success, log_warning

# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
SAFE_PARAMS = {
    # ─── کلیدواژه‌ها ───
    "KEYWORDS_ONLY",

    # ─── poll intervals ───
    "EITAA_POLL_SECONDS", "BALE_POLL_SECONDS", "RUBIKA_POLL_SECONDS",
    "TELEGRAM_POLL_SECONDS", "WHATSAPP_POLL_SECONDS", "LINKEDIN_POLL_SECONDS",

    # ─── فعال/غیرفعال ───
    "WEB_ENABLED", "EITAA_ENABLED", "BALE_ENABLED", "RUBIKA_ENABLED",
    "TELEGRAM_ENABLED", "WHATSAPP_ENABLED", "LINKEDIN_ENABLED",

    # ─── عمومی ───
    "OBS_PORT", "LOG_LEVEL",
    "LINKEDIN_MONITOR_FEED",

    # ─── بله ───
    "BALE_MAX_CHATS",
    "BALE_MESSAGES_PER_CHAT",
    "BALE_BACKFILL_SCROLLS",
    "BALE_DELAY_MIN", "BALE_DELAY_MAX",
    "BALE_BETWEEN_MIN", "BALE_BETWEEN_MAX",
    "BALE_TYPING_MIN", "BALE_TYPING_MAX",
    "BALE_ADMIN_CHAT",
    "BALE_UA_SUFFIX",

    # ─── ایتا ───
    "EITAA_MAX_CHATS",
    "EITAA_MESSAGES_PER_CHAT",
    "EITAA_MSGS_PER_CHAT",  # سازگاری عقب‌رو
    "EITAA_BACKFILL_SCROLLS",
    "EITAA_CHAT_DELAY",

    # ─── واتساپ ───
    "WHATSAPP_MAX_CHATS",
    "WHATSAPP_MESSAGES_PER_CHAT",
    "WHATSAPP_MSGS_PER_CHAT",  # سازگاری عقب‌رو
    "WHATSAPP_BACKFILL_SCROLLS",
    "WHATSAPP_CHAT_DELAY",

    # ─── SafeBrain ───
    "SAFE_BRAIN_MAX_API_HOUR",
    "SAFE_BRAIN_AI_TIMEOUT_S",

    # ─── Backup ───
    "BACKUP_POKE_SECONDS",

    # ─── AI ───
    "AI_AVAILABLE_CACHE_S",

    # ─── Cleanup ───
    "CLEANUP_MIN_INTERVAL_HOURS",
    "CLEANUP_MAX_INTERVAL_HOURS",

    # ─── Report ───
    "REPORT_SQL_TIMEOUT_S",
    "REPORT_MISFIRE_GRACE_S",

    # ─── Monitor ───
    "MONITOR_ENABLED",
    "MONITOR_INTERVAL_SECONDS",
    "MONITOR_CPU_ALERT", "MONITOR_RAM_ALERT", "MONITOR_DISK_ALERT",

    # ─── Self-Monitor ───
    "SELF_MONITOR_ENABLED",
    "SELF_MONITOR_INTERVAL",
    "SELF_MONITOR_ERROR_THRESHOLD",
    "SELF_MONITOR_STALE_MINUTES",

    # ─── Keep ───
    "KEEP_MEDIA_DAYS", "KEEP_REPORTS_DAYS", "KEEP_MESSAGES_DAYS",
    "CLEANUP_INTERVAL_HOURS",

    # ─── اصلی ───
    "MAIN_BALE_WAIT_S",
    "LOGIN_INPUT_TIMEOUT_S",
}

# پارامترهای حساس — مقدارشان در پاسخ mask می‌شود
# ⚠️ این‌ها در SAFE_PARAMS نیستند (امکان تغییر از چت را ندارند)
_SENSITIVE_PARAMS = {
    "DEEPSEEK_API_KEY", "DB_PASSWORD", "LINKEDIN_PASSWORD",
    "BALE_PASSWORD", "EITAA_PASSWORD", "RUBIKA_PASSWORD",
    "MEMORY_API_KEY", "NOTIFY_TELEGRAM_TOKEN",
    "MAIN_SKIP_VENV_CHECK",  # امنیتی — نباید از چت تغییر کند
}

_PLATFORMS = ("eitaa", "bale", "rubika", "telegram",
              "whatsapp", "linkedin", "web")

# سقف خواندن لاگ (seek از انتها)
_LOG_TAIL_BYTES = 128 * 1024
_LOG_LINES_HARD_CAP = 200

# سقف نمایش مقدار در get_param
_PARAM_VALUE_MAX_LEN = 200

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_ACTION_MS = 5000
_SLOW_CRM_MS = 15000
_SLOW_REPORT_MS = 10000
_SLOW_AI_MS = 30000

# مدت اعتبار mask
_MASK_PREVIEW_LEN = 4


def _mask_secret(value: str) -> str:
    """مقدار حساس را mask می‌کند — فقط چند کاراکتر اول."""
    if not value or value == "(تنظیم‌نشده)":
        return value
    s = str(value)
    if len(s) <= _MASK_PREVIEW_LEN:
        return "•" * len(s)
    return s[:_MASK_PREVIEW_LEN] + "•" * min(len(s) - _MASK_PREVIEW_LEN, 12)


class AdminActions:
    """همهٔ اجراهای دستور — بدون گره به EventBus یا Handler."""

    def __init__(
        self,
        *,
        db,
        db_path: str,
        kw,
        ai,
        knowledge: str,
        keyword_admin,
        memory,
        root: str,
        platform: str,
        pid_file: str,
        get_requests_path: Callable[[], str],
        get_custom_kw_path: Callable[[], str],
    ):
        self.db = db
        self.db_path = db_path
        self.kw = kw
        self.ai = ai
        self.knowledge = knowledge
        self.keyword_admin = keyword_admin
        self.memory = memory
        self.root = root
        self.platform = platform
        self.pid_file = pid_file
        self._get_requests_path = get_requests_path
        self._get_custom_kw_path = get_custom_kw_path

        # 🟠 FIX: task reference برای restart (جلوگیری از GC)
        self._restart_task: asyncio.Task | None = None

    # ═══════════════════════════════════════
    # اجرا
    # ═══════════════════════════════════════
    async def execute(self, action: str, value, raw_text: str) -> str:
        t0 = time.monotonic()
        log_info(f"▶️ [ADMIN:exec] action={action} value={value!r}")

        try:
            result = await self._dispatch(action, value, raw_text)
        except Exception as e:
            log_error(
                f"❌ [ADMIN:exec] action={action} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطای غیرمنتظره: {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_ACTION_MS:
            log_warning(
                f"🐌 [ADMIN:exec] {action} کند | {elapsed_ms:.0f}ms"
            )
        return result

    async def _dispatch(self, action: str, value, raw_text: str) -> str:
        if action == "help":
            return self.help_text()
        if action == "status":
            return self.status()
        if action == "report":
            return await self.report_action(raw_text)
        if action == "crm":
            return await self.crm_action(raw_text)
        if action == "logs":
            return self.logs()
        if action == "keywords":
            ks = self.kw.keywords
            sample = "، ".join(ks[:15])
            return f"🔑 {len(ks)} کلیدواژه بارگذاری شده.\nنمونه: {sample}"
        if action == "add_keyword":
            kw = value or extract_value(raw_text)
            return self.keyword_admin.add(kw, self._get_custom_kw_path())
        if action == "remove_keyword":
            kw = value or extract_value(raw_text)
            return self.keyword_admin.remove(kw, self._get_custom_kw_path())
        if action == "health":
            return await self.health()
        if action == "lead_details":
            return await self.lead_details(value or "last")
        if action == "has_keyword":
            v = value or extract_value(raw_text)
            if not v:
                return "کدام کلیدواژه؟ داخل « » بنویس."
            found = bool(self.kw.has_match(v) or v in self.kw.keywords)
            log_info(f"🔍 [ADMIN:has_kw] v={v!r} | found={found}")
            return (
                f"{'✅ بله' if found else '❌ نه'}، "
                f"«{v}» {'هست' if found else 'نیست'}."
            )
        if action == "platform_status":
            return await self.platform_status(value)
        if action == "get_param":
            return self.get_param(value)
        if action == "set_param":
            return self.set_param(raw_text)
        if action == "restart":
            # 🟠 FIX: نگه‌داشتن reference به task (جلوگیری از GC)
            self._restart_task = asyncio.create_task(self._do_restart())
            log_warning("🔄 [ADMIN:restart] درخواست ریاستارت")
            return "🔄 در حال ریاستارت... چند ثانیه دیگر دوباره بالا می‌آیم."
        if action == "explain_logs":
            return await self.explain_logs()
        if action == "feature_request":
            return self.log_request(raw_text)
        # پیش‌فرض: سؤال آزاد از AI
        return await self.ask(raw_text)

    # ═══════════════════════════════════════
    # CRM / Report — در thread جدا (sync functions)
    # ═══════════════════════════════════════
    async def crm_action(self, raw_text: str) -> str:
        platform = platform_in((raw_text or "").lower())
        log_info(f"📤 [ADMIN:crm] platform={platform or 'all'}")

        try:
            from core.services.crm_export import export_crm
        except Exception as e:
            log_error(f"❌ [ADMIN:crm] import | {type(e).__name__}: {e}")
            return f"❌ خطا در بارگذاری CRM: {e}"

        # export_crm sync است — در thread جدا اجرا کن (بلاک event loop نشود)
        t0 = time.monotonic()
        try:
            res = await asyncio.to_thread(export_crm, 30, platform=platform)
        except TypeError:
            # امضای قدیمی (بدون platform)
            try:
                res = await asyncio.to_thread(export_crm, 30)
            except Exception as e:
                log_error(
                    f"❌ [ADMIN:crm] export fallback | "
                    f"{type(e).__name__}: {e}"
                )
                return f"❌ خطا در ساخت CRM: {e}"
        except Exception as e:
            log_error(f"❌ [ADMIN:crm] export | {type(e).__name__}: {e}")
            return f"❌ خطا در ساخت CRM: {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000

        if not res:
            log_warning("⚠️ [ADMIN:crm] خروجی خالی")
            return "⚠️ خروجی CRM ساخته نشد (شاید سرنخی نیست)."

        label = platform or "همه پلتفرم‌ها"
        path = res.get("xlsx") or res.get("csv") or res.get("json") or "?"
        log_success(
            f"✅ [ADMIN:crm] {res.get('count', 0)} سرنخ — {label} | "
            f"{elapsed_ms:.0f}ms"
        )
        if elapsed_ms > _SLOW_CRM_MS:
            log_warning(f"🐌 [ADMIN:crm] کند | {elapsed_ms:.0f}ms")

        return (
            f"📤 خروجی CRM آماده شد — {label}\n"
            f"🔢 تعداد: {res.get('count', 0)} سرنخ\n"
            f"📄 فایل: {path}"
        )

    async def report_action(self, raw_text: str) -> str:
        from core.services.daily_reporter import generate_executive_report
        platform = platform_in((raw_text or "").lower())
        log_info(f"📄 [ADMIN:report] platform={platform or 'all'}")

        t0 = time.monotonic()
        try:
            # sync → thread
            path = await asyncio.to_thread(
                generate_executive_report, platform=platform
            )
        except TypeError:
            try:
                path = await asyncio.to_thread(generate_executive_report)
            except Exception as e:
                log_error(
                    f"❌ [ADMIN:report] fallback | "
                    f"{type(e).__name__}: {e}"
                )
                return f"❌ خطا در ساخت گزارش: {e}"
        except Exception as e:
            log_error(f"❌ [ADMIN:report] {type(e).__name__}: {e}")
            return f"❌ خطا در ساخت گزارش: {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        label = platform or "همه پلتفرم‌ها"
        if elapsed_ms > _SLOW_REPORT_MS:
            log_warning(
                f"🐌 [ADMIN:report] کند | {elapsed_ms:.0f}ms"
            )

        if not path:
            log_warning("⚠️ [ADMIN:report] مسیر None برگشت")
            return f"⚠️ گزارش ساخته نشد (شاید سرنخی نیست) — {label}"

        log_success(
            f"✅ [ADMIN:report] {label} → {path} | {elapsed_ms:.0f}ms"
        )
        return f"📄 گزارش مدیریتی ساخته شد — {label}:\n{path}"

    # ═══════════════════════════════════════
    # Ask / memory
    # ═══════════════════════════════════════
    async def ask(self, question: str) -> str:
        try:
            from core.services.ai_assistant import PERSONA
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:ask] import PERSONA | "
                f"{type(e).__name__}: {e}"
            )
            return self.help_text()

        system = (
            PERSONA
            + "\n\n# دانشِ ربات:\n" + self.knowledge
            + "\n\n# وضعیتِ زندهٔ همین الان (از دیتابیس):\n"
            + self.live_context()
            + self.memory.recent()
        )
        t0 = time.monotonic()
        try:
            ans = await self.ai.chat(question, system=system)
        except Exception as e:
            log_error(f"❌ [ADMIN:ask] {type(e).__name__}: {e}")
            return (
                "هوش الان در دسترس نیست. خلاصهٔ قابلیت‌ها:\n"
                + self.help_text()
            )

        elapsed_ms = (time.monotonic() - t0) * 1000

        # AI در بعضی حالت‌ها پیام خطای متنی برمی‌گرداند (نه None)
        if (
            not ans
            or "در دسترس نیست" in ans
            or ans.startswith("⚠️")
        ):
            log_warning(
                f"⚠️ [ADMIN:ask] پاسخ نامعتبر | {elapsed_ms:.0f}ms"
            )
            return (
                "هوش الان در دسترس نیست. خلاصهٔ قابلیت‌ها:\n"
                + self.help_text()
            )

        if elapsed_ms > _SLOW_AI_MS:
            log_warning(f"🐌 [ADMIN:ask] کند | {elapsed_ms:.0f}ms")

        log_success(
            f"✅ [ADMIN:ask] پاسخ | {elapsed_ms:.0f}ms | len={len(ans)}"
        )
        self.memory.remember(question, ans)
        return ans

    # ═══════════════════════════════════════
    # آمار / وضعیت
    # ═══════════════════════════════════════
    def live_context(self) -> str:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            total = cur.execute(
                "SELECT COUNT(*) FROM messages WHERE keywords!=''"
            ).fetchone()[0]
            today = cur.execute(
                "SELECT COUNT(*) FROM messages WHERE keywords!='' "
                "AND date(timestamp)=date('now')"
            ).fetchone()[0]
            byp = cur.execute(
                "SELECT platform, COUNT(*) FROM messages "
                "WHERE keywords!='' GROUP BY platform"
            ).fetchall()
            last = cur.execute(
                "SELECT platform, chat_name, keywords, timestamp "
                "FROM messages WHERE keywords!='' "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()

            parts = [
                f"کل سرنخ‌ها: {total}",
                f"امروز: {today}",
                (
                    "به‌تفکیک: " + "، ".join(f"{p}={n}" for p, n in byp)
                    if byp else "به‌تفکیک: -"
                ),
            ]
            if last:
                parts.append(
                    f"آخرین سرنخ: {last[0]} / گروه {last[1]} / "
                    f"کلیدواژه {last[2]} / {last[3]}"
                )
            return "\n".join(parts)
        except Exception as e:
            log_warning(f"⚠️ [ADMIN:live_ctx] {type(e).__name__}: {e}")
            return "(فعلاً آماری در دسترس نیست)"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def status(self) -> str:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            total = cur.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            leads = cur.execute(
                "SELECT COUNT(*) FROM messages WHERE keywords!=''"
            ).fetchone()[0]
            hot = cur.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE opportunity_type='داغ'"
            ).fetchone()[0]
            rows = cur.execute(
                "SELECT platform, COUNT(*) FROM messages "
                "WHERE keywords!='' GROUP BY platform"
            ).fetchall()

            by = "، ".join(f"{p}: {n}" for p, n in rows) or "—"
            return (
                f"📊 وضعیت:\n"
                f"کل پیام: {total} | سرنخ: {leads} | داغ: {hot}\n"
                f"به تفکیک پلتفرم: {by}"
            )
        except Exception as e:
            log_error(f"❌ [ADMIN:status] {type(e).__name__}: {e}")
            return f"خطا در خواندن آمار: {e}"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    async def health(self) -> str:
        lines = ["🩺 سلامتِ کامل:"]
        for p in _PLATFORMS:
            try:
                last = await self.db.get_last_poll(p)
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:health] last_poll {p} | "
                    f"{type(e).__name__}: {e}"
                )
                last = None
            if not last:
                lines.append(f"• {p}: — (فعالیتی ثبت نشده)")
            else:
                mins = (datetime.now() - last).total_seconds() / 60
                mark = "✅" if mins < 30 else "⚠️"
                lines.append(
                    f"• {p}: {mark} آخرین فعالیت {int(mins)} دقیقه پیش"
                )
        try:
            import psutil
            lines.append(
                f"💻 CPU {psutil.cpu_percent(interval=0.3)}% | "
                f"RAM {psutil.virtual_memory().percent}%"
            )
        except ImportError:
            pass
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:health] psutil | {type(e).__name__}: {e}"
            )
        lines.append(self.status())
        return "\n".join(lines)

    # ═══════════════════════════════════════
    # سرنخ
    # ═══════════════════════════════════════
    async def lead_details(self, which: str) -> str:
        # sanitize ورودی
        which = (which or "last").lower()
        if which not in ("first", "last"):
            which = "last"
        order = "ASC" if which == "first" else "DESC"

        conn = None
        row = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = cur.execute(
                f"SELECT platform, chat_name, chat_link, keywords, "
                f"opportunity_type, opportunity_score, text, timestamp "
                f"FROM messages WHERE keywords!='' "
                f"ORDER BY timestamp {order} LIMIT 1"
            ).fetchone()
        except Exception as e:
            log_error(f"❌ [ADMIN:lead_details] {type(e).__name__}: {e}")
            return f"خطا در خواندن سرنخ: {e}"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        if not row:
            return "هنوز هیچ سرنخی ثبت نشده."

        label = "اولین" if which == "first" else "آخرین"
        detail = (
            f"🔎 {label} سرنخ:\n"
            f"📱 پلتفرم: {row['platform']}\n"
            f"👥 گروه: {row['chat_name']}\n"
            f"🔑 کلیدواژه: {row['keywords']}\n"
            f"🎯 {row['opportunity_type'] or '-'} "
            f"(امتیاز {row['opportunity_score'] or 0})\n"
            f"🕒 {row['timestamp']}\n"
            f"💬 {(row['text'] or '')[:300]}"
        )
        if row["chat_link"]:
            detail += f"\n🔗 {row['chat_link']}"

        # narration اختیاری — خطا نده
        narration = ""
        try:
            from core.services.ai_assistant import PERSONA
            t0 = time.monotonic()
            narration = await self.ai.chat(
                "این سرنخ را برای مدیر در یک جلسه توضیح بده: چه دیدی، "
                "چطور تشخیص دادی کلیدواژه دارد، و مراحلِ ثبتش چه بود. "
                "کوتاه و طبیعی.\n\n" + detail,
                system=PERSONA,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            # پاسخ نامعتبر AI → حذف
            if (
                narration
                and (
                    "در دسترس نیست" in narration
                    or narration.startswith("⚠️")
                )
            ):
                narration = ""
            if narration:
                log_info(
                    f"🗣️ [ADMIN:lead:narrate] {elapsed_ms:.0f}ms | "
                    f"len={len(narration)}"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:lead_details:narrate] "
                f"{type(e).__name__}: {e}"
            )
            narration = ""

        return detail + (("\n\n🗣️ " + narration) if narration else "")

    async def platform_status(self, platform) -> str:
        # sanitize
        p = (platform or "").lower() if isinstance(platform, str) else ""
        if not p or p not in _PLATFORMS:
            return self.status()

        try:
            last = await self.db.get_last_poll(p)
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:platform_status] {p} | "
                f"{type(e).__name__}: {e}"
            )
            last = None

        if not last:
            return (
                f"ℹ️ {p}: هنوز فعالیتی ثبت نشده "
                f"(شاید تازه شروع شده یا وصل نیست)."
            )

        mins = (datetime.now() - last).total_seconds() / 60
        if mins < 30:
            return (
                f"✅ {p}: فعال — آخرین فعالیت {int(mins)} دقیقه پیش."
            )
        return (
            f"⚠️ {p}: آخرین فعالیت {int(mins)} دقیقه پیش "
            f"(شاید وصل نیست)."
        )

    # ═══════════════════════════════════════
    # لاگ — با seek از انتها (نه کل فایل)
    # ═══════════════════════════════════════
    def logs(self, n: int = 12) -> str:
        # sanitize
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 12
        n = max(1, min(n, _LOG_LINES_HARD_CAP))

        path = os.path.join(self.root, "storage", "logs", "app.jsonl")
        if not os.path.exists(path):
            return "لاگی موجود نیست."

        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > _LOG_TAIL_BYTES:
                    f.seek(-_LOG_TAIL_BYTES, os.SEEK_END)
                    f.readline()  # خط ناقص اول را دور بریز
                data = f.read()
            lines = data.decode("utf-8", errors="ignore").splitlines()
        except Exception as e:
            log_error(f"❌ [ADMIN:logs] read | {type(e).__name__}: {e}")
            return f"خطا در خواندن لاگ: {e}"

        out = []
        for ln in lines[-n:]:
            try:
                o = json.loads(ln)
                lvl = o.get("level", "")
                msg = (o.get("msg", "") or "")[:90]
                out.append(f"[{lvl}] {msg}")
            except Exception:
                # خط ناقص/invalid — کوتاه کن
                out.append(ln.strip()[:90])
        return "🗒️ آخرین لاگ‌ها:\n" + "\n".join(out)

    async def explain_logs(self) -> str:
        logs = self.logs(15)
        prompt = (
            f"این لاگ‌های ربات را خیلی ساده به فارسی توضیح بده که "
            f"چه اتفاقی افتاده و اگر خطایی هست یعنی چه و چه باید کرد:\n\n"
            f"{logs}"
        )
        try:
            ans = await self.ai.chat(prompt)
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:explain_logs] {type(e).__name__}: {e}"
            )
            return logs

        # پاسخ نامعتبر → raw logs
        if (
            not ans
            or "در دسترس نیست" in ans
            or ans.startswith("⚠️")
        ):
            return logs
        return ans

    # ═══════════════════════════════════════
    # پارامترها — با mask حساس + atomic write
    # ═══════════════════════════════════════
    def get_param(self, name) -> str:
        if not name or not isinstance(name, str):
            return (
                f"این پارامتر مجاز نیست. "
                f"مجازها: {', '.join(sorted(SAFE_PARAMS))}"
            )
        key = name.upper()
        if key not in SAFE_PARAMS:
            return (
                f"این پارامتر مجاز نیست. "
                f"مجازها: {', '.join(sorted(SAFE_PARAMS))}"
            )

        raw = os.getenv(key, "(تنظیم‌نشده)")
        # 🎯 FIX: سقف طول مقدار
        if raw and len(str(raw)) > _PARAM_VALUE_MAX_LEN:
            raw = str(raw)[:_PARAM_VALUE_MAX_LEN] + "..."

        # اگه حساس بود، mask کن
        if key in _SENSITIVE_PARAMS:
            display = _mask_secret(raw)
        else:
            display = raw

        log_info(
            f"⚙️ [ADMIN:get_param] {key} "
            f"(masked={'yes' if key in _SENSITIVE_PARAMS else 'no'})"
        )
        return f"{key} = {display}"

    def set_param(self, raw_text: str) -> str:
        name, val = parse_set_param(raw_text)
        if not name:
            return "قالب: set PARAM=VALUE"
        name = name.upper()
        if name not in SAFE_PARAMS:
            log_warning(f"⚠️ [ADMIN:set_param] غیرمجاز | {name}")
            return (
                f"تغییرِ «{name}» مجاز نیست. "
                f"مجازها: {', '.join(sorted(SAFE_PARAMS))}"
            )

        # 🟠 FIX: چک بی‌فایده حذف شد
        # (چون SAFE_PARAMS و _SENSITIVE_PARAMS همپوشانی ندارند،
        #  پارامترهای حساس در SAFE_PARAMS نیستند → به چک دوم نیازی نیست)

        ok, err = self._update_env(name, val)
        if not ok:
            log_error(
                f"❌ [ADMIN:set_param] {name} | {err}"
            )
            return f"خطا در تغییر: {err}"

        log_success(f"✅ [ADMIN:set_param] {name}={val}")
        return (
            f"✅ {name}={val} ذخیره شد. "
            f"برای اعمالِ کامل، ریاستارت کن."
        )

    def _update_env(self, key: str, value: str) -> tuple[bool, str]:
        """نوشتن atomic در .env — جلوگیری از خرابی فایل."""
        env_path = os.path.join(self.root, ".env")
        tmp_path = env_path + ".tmp"

        try:
            lines: list[str] = []
            if os.path.exists(env_path):
                with open(env_path, encoding="utf-8") as f:
                    lines = f.readlines()

            found = False
            new_line = f"{key}={value}\n"
            for i, ln in enumerate(lines):
                if ln.strip().startswith(f"{key}="):
                    lines[i] = new_line
                    found = True
                    break
            if not found:
                # اطمینان از newline در آخر
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines.append(new_line)

            # atomic write: tmp + replace
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            os.replace(tmp_path, env_path)
            return True, ""
        except Exception as e:
            # cleanup tmp اگه موند
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return False, f"{type(e).__name__}: {e}"

    # ═══════════════════════════════════════
    # feature request / restart
    # ═══════════════════════════════════════
    def log_request(self, text: str) -> str:
        try:
            path = self._get_requests_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"ts": datetime.now().isoformat(), "request": text},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            log_info(f"📝 [ADMIN:request] ثبت شد | {text[:100]!r}")
            return (
                "📝 ثبت شد. این یک قابلیتِ جدید است که الان ربات ندارد؛ "
                "برای توسعه‌دهنده ثبتش کردم تا اضافه شود."
            )
        except Exception as e:
            log_error(f"❌ [ADMIN:request] {type(e).__name__}: {e}")
            return f"خطا در ثبتِ درخواست: {e}"

    async def _do_restart(self):
        """ری‌استارت با graceful shutdown — نه os._exit."""
        await asyncio.sleep(1)

        # تلاش برای shutdown تمیز
        try:
            from main import _signal_handler
            log_info("🔁 [ADMIN:restart] shutdown تمیز...")
            _signal_handler()
            # کمی صبر کن تا cleanup شروع شود (ولی نه تا کامل)
            await asyncio.sleep(3)
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:restart] shutdown تمیز نشد | "
                f"{type(e).__name__}: {e}"
            )

        # spawn پروسهٔ جدید
        try:
            kwargs = {"cwd": self.root}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_CONSOLE", 0
                )
            else:
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(
                [sys.executable, os.path.join(self.root, "main.py")],
                **kwargs,
            )
            try:
                with open(self.pid_file, "w") as f:
                    f.write(str(proc.pid))
                log_info(f"🔁 [ADMIN:restart] PID جدید={proc.pid}")
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:restart] PID ذخیره نشد | "
                    f"{type(e).__name__}: {e}"
                )
        except Exception as e:
            log_error(
                f"❌ [ADMIN:restart] Popen | {type(e).__name__}: {e}"
            )
            return

        # بعد از spawn، خروج از پروسهٔ قدیمی
        # (وقتی main() در finally خودش cleanup می‌کند)
        log_warning("🔄 [ADMIN:restart] خروج از پروسهٔ قدیمی")
        # 🎯 FIX: sys.exit در try/except
        try:
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:restart] sys.exit | "
                f"{type(e).__name__}: {e}"
            )
            os._exit(0)  # آخرین راه‌حل

    # ═══════════════════════════════════════
    # راهنما — شامل همهٔ actionهای موجود
    # ═══════════════════════════════════════
    def help_text(self) -> str:
        return (
            "🛡️ دستورها (متن آزاد هم می‌فهمم):\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📊 **اطلاعات:**\n"
            "• وضعیت / آمار / خلاصه\n"
            "• گزارش — `گزارش ایتا` برای یک پلتفرم خاص\n"
            "• CRM (اکسل) — `crm ایتا` برای یک پلتفرم خاص\n"
            "• لاگ / چند خطا\n"
            "• توضیح لاگ‌ها (explain_logs)\n"
            "• کلیدواژه‌ها / کلیدواژه‌های سفارشی\n"
            "• گروه‌های هدف / بهترین گروه\n"
            "• داغ‌ترین کلیدواژه\n"
            "• آخرین سرنخ / اولین سرنخ\n"
            "• `وضعیت ایتا` (platform_status)\n"
            "• `سلامت` (health)\n\n"
            "🔧 **مدیریت:**\n"
            "• کلیدواژه «...» را اضافه کن / حذف کن\n"
            "• کلیدواژه «...» را داری؟ (has_keyword)\n"
            "• لینکدین کار می‌کند؟ (یا هر پلتفرم)\n"
            "• `set PARAM=VALUE` — تنظیم پارامترهای مجاز\n"
            "• `get PARAM` — خواندن پارامتر\n"
            "• `restart` / `ری‌استارت`\n\n"
            "🧠 **یادگیری (هوش خودکار):**\n"
            "• یاد بگیر «سؤال» = «جواب»\n"
            "• یاد بگیر «سؤال» = SQL: SELECT ...\n"
            "• لیست candidate ها\n"
            "• تأیید <نام قانون>\n\n"
            "💡 **درخواست قابلیت جدید:**\n"
            "• «می‌خوام بتونی ...» → ثبت می‌شود\n\n"
            "🔒 دستورها فقط در چتِ خصوصی اجرا می‌شوند (نه در گروه)."
        )
