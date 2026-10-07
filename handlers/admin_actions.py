"""
اجرای دستورهای مدیر — DB، گزارش، CRM، لاگ، پارامترها، ریاستارت.

نسخهٔ ۹ (این نسخه — داده‌محور):
  - 🎯 همهٔ لیست‌ها و پیام‌ها از data/ می‌آید:
      * data/configs/params.json    → safe / sensitive / config_only
      * data/configs/messages.json  → پیام‌های خطا
      * data/templates/help.md      → متن راهنما
      * data/configs/platforms.json → ترجمهٔ پلتفرم
  - 🐛 FIX: `_get_safebrain_prefixes` حذف شد (بی‌استفاده بود).
  - 🐛 FIX: `ask()` حالا اول `live_context()` می‌دهد بعد `help_text()`.
  - 🐛 FIX: `live_context()` و `status()` و `platform_status()`
    اسم پلتفرم را فارسی نشان می‌دهند (از `_ai_utils.fa_platform`).
  - 🎯 `help_text()` حالا از `data/templates/help.md` می‌خواند.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۸.
"""
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

# 🎯 P0: استفاده از config برای top-level params
from config import PLATFORMS_ORDER

from core.adapters.command_parser import (
    extract_value,
    parse_set_param,
    platform_in,
)
from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 داده‌محور
from data import paths as data_paths
from data.loader import load_json, load_md

# 🎯 ترجمهٔ پلتفرم
from handlers._ai_utils import fa_platform


# ═══════════════════════════════════════════
# ثابت‌ها (فقط env-based — بدون دیتا)
# ═══════════════════════════════════════════

# پلتفرم‌ها از config
_PLATFORMS = PLATFORMS_ORDER

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

# پیش‌فرض‌های fallback (اگر data/ نبود)
_DEFAULT_HELP = "راهنما در دسترس نیست."
_DEFAULT_KEY_NOT_SET = "(تنظیم‌نشده)"
_DEFAULT_EMPTY_STATS = "(فعلاً آماری در دسترس نیست)"


# ═══════════════════════════════════════════
# helpers — داده‌محور
# ═══════════════════════════════════════════
def _get_params_data() -> dict:
    """🎯 بارگذاری params از access_control.json — با fallback امن.

    جایگزین params.json (که تکراری با access_control.json بود):
      - safe        ← access_control.settings.write
      - sensitive   ← access_control.settings.deny
      - config_only ← زیرمجموعه‌ای از deny (پارامترهای top-level config)
    """
    data = load_json("access_control")
    settings = data.get("settings", {})

    safe = set(settings.get("write", []) or [])
    sensitive = set(settings.get("deny", []) or [])

    # config_only = پارامترهای top-level config
    # (از deny استخراج می‌شوند — این‌ها نباید از env خونده بشن)
    _CONFIG_ONLY_KEYS = {
        "ADMIN_ENABLED", "ADMIN_IDS", "ADMIN_PLATFORM",
        "FULL_ACCESS", "USE_API_MEMORY",
        "AI_ENABLED", "AI_PRIORITY",
        "PLATFORMS_ACTIVE",
    }
    config_only = sensitive & _CONFIG_ONLY_KEYS

    return {
        "safe": safe,
        "sensitive": sensitive,
        "config_only": config_only,
    }
    
def _get_errors() -> dict:
    """بارگذاری errors.json — منبع یکتا."""
    return load_json("errors")


def _get_error_msg(key: str, default: str = "") -> str:
    """گرفتن پیام خطا از errors.json."""
    data = _get_errors()
    errors = data.get("errors", {})
    return errors.get(key, default)


def _mask_secret(value: str) -> str:
    """مقدار حساس را mask می‌کند — فقط چند کاراکتر اول."""
    not_set = _get_error_msg("key_not_set", _DEFAULT_KEY_NOT_SET)
    if not value or value == not_set:
        return value
    s = str(value)
    if len(s) <= _MASK_PREVIEW_LEN:
        return "•" * len(s)
    return s[:_MASK_PREVIEW_LEN] + "•" * min(
        len(s) - _MASK_PREVIEW_LEN, 12
    )


# ═══════════════════════════════════════════
# AdminActions
# ═══════════════════════════════════════════
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
        pid_file: str | None = None,
        get_requests_path: Callable[[], str] | None = None,
        get_custom_kw_path: Callable[[], str] | None = None,
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

        # 🎯 P0: fallback به data.paths اگر داده نشد
        self.pid_file = pid_file or str(data_paths.BOT_PID)
        self._get_requests_path = (
            get_requests_path or (lambda: str(data_paths.REQUESTS_FILE))
        )
        self._get_custom_kw_path = (
            get_custom_kw_path or (lambda: str(data_paths.KEYWORDS_CUSTOM))
        )

        self._restart_task: asyncio.Task | None = None

        # 🎯 لاگ init — خلاصه
        params = _get_params_data()
        log_info(
            f"⚙️ [ADMIN:init:actions] پارامترها | "
            f"safe={len(params['safe'])} | "
            f"sensitive={len(params['sensitive'])} | "
            f"config_only={len(params['config_only'])}"
        )

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
                return _get_error_msg(
                    "empty_keyword",
                    "کدام کلیدواژه؟ داخل « » بنویس.",
                )
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
            self._restart_task = asyncio.create_task(self._do_restart())
            log_warning("🔄 [ADMIN:restart] درخواست ریاستارت")
            return "🔄 در حال ریاستارت... چند ثانیه دیگر دوباره بالا می‌آیم."
        if action == "explain_logs":
            return await self.explain_logs()
        if action == "feature_request":
            return self.log_request(raw_text)
        return await self.ask(raw_text)

    # ═══════════════════════════════════════
    # CRM / Report
    # ═══════════════════════════════════════
    async def crm_action(self, raw_text: str) -> str:
        platform = platform_in((raw_text or "").lower())
        log_info(f"📤 [ADMIN:crm] platform={platform or 'all'}")

        try:
            from core.services.crm_export import export_crm
        except Exception as e:
            log_error(f"❌ [ADMIN:crm] import | {type(e).__name__}: {e}")
            return f"❌ خطا در بارگذاری CRM: {e}"

        t0 = time.monotonic()
        try:
            res = await asyncio.to_thread(export_crm, 30, platform=platform)
        except TypeError:
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

        label = fa_platform(platform) if platform else "همه پلتفرم‌ها"
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
        label = fa_platform(platform) if platform else "همه پلتفرم‌ها"
        if elapsed_ms > _SLOW_REPORT_MS:
            log_warning(f"🐌 [ADMIN:report] کند | {elapsed_ms:.0f}ms")

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
        """سؤال آزاد — با fallback چندلایه.

        اگر AI نبود → اول live_context() بعد help_text().
        """
        try:
            from core.services.ai_assistant import PERSONA
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:ask] import PERSONA | "
                f"{type(e).__name__}: {e}"
            )
            return self._fallback_reply()

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
            return self._fallback_reply()

        elapsed_ms = (time.monotonic() - t0) * 1000

        # ─── چک اعتبار ───
        if (
            not ans
            or "در دسترس نیست" in ans
            or ans.startswith("⚠️")
        ):
            log_warning(
                f"⚠️ [ADMIN:ask] پاسخ نامعتبر | {elapsed_ms:.0f}ms"
            )
            return self._fallback_reply()

        if elapsed_ms > _SLOW_AI_MS:
            log_warning(f"🐌 [ADMIN:ask] کند | {elapsed_ms:.0f}ms")

        log_success(
            f"✅ [ADMIN:ask] پاسخ | {elapsed_ms:.0f}ms | len={len(ans)}"
        )
        self.memory.remember(question, ans)
        return ans

    def _fallback_reply(self) -> str:
        """پاسخ fallback: اول آمار زنده، بعد راهنما."""
        return (
            "🤔 هوش الان در دسترس نیست — ولی آمار زنده:\n\n"
            + self.live_context()
            + "\n\n"
            + self.help_text()
        )

    # ═══════════════════════════════════════
    # آمار / وضعیت
    # ═══════════════════════════════════════
    def live_context(self) -> str:
        """آمار زنده — با اسم پلتفرم فارسی."""
        empty = _get_error_msg("empty_stats", _DEFAULT_EMPTY_STATS)
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

            # 🎯 اسم پلتفرم فارسی
            byp_fa = "، ".join(
                f"{fa_platform(p)}={n}" for p, n in byp
            )
            parts = [
                f"کل سرنخ‌ها: {total}",
                f"امروز: {today}",
                f"به‌تفکیک: {byp_fa}" if byp else "به‌تفکیک: -",
            ]
            if last:
                parts.append(
                    f"آخرین سرنخ: {fa_platform(last[0])} / "
                    f"گروه {last[1]} / "
                    f"کلیدواژه {last[2]} / {last[3]}"
                )
            return "\n".join(parts)
        except Exception as e:
            log_warning(f"⚠️ [ADMIN:live_ctx] {type(e).__name__}: {e}")
            return empty
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def status(self) -> str:
        """آمار کلی — با اسم پلتفرم فارسی."""
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

            # 🎯 اسم پلتفرم فارسی
            by = "، ".join(
                f"{fa_platform(p)}: {n}" for p, n in rows
            ) or "—"
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
        """سلامت کامل — با اسم پلتفرم فارسی."""
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
            p_fa = fa_platform(p)
            if not last:
                lines.append(f"• {p_fa}: — (فعالیتی ثبت نشده)")
            else:
                mins = (datetime.now() - last).total_seconds() / 60
                mark = "✅" if mins < 30 else "⚠️"
                lines.append(
                    f"• {p_fa}: {mark} آخرین فعالیت {int(mins)} دقیقه پیش"
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
                f"brands, matched_products, "
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
        try:
            brand_info = row["brands"] if "brands" in row.keys() else ""
        except Exception:
            brand_info = ""
        brand_line = f"🏢 برند: {brand_info}\n" if brand_info else ""
        detail = (
            f"🔎 {label} سرنخ:\n"
            f"📱 پلتفرم: {fa_platform(row['platform'])}\n"
            f"👥 گروه: {row['chat_name']}\n"
            f"🔑 کلیدواژه: {row['keywords']}\n"
            f"{brand_line}"
            f"🎯 {row['opportunity_type'] or '-'} "
            f"(امتیاز {row['opportunity_score'] or 0})\n"
            f"🕒 {row['timestamp']}\n"
            f"💬 {(row['text'] or '')[:300]}"
        )

        if row["chat_link"]:
            detail += f"\n🔗 {row['chat_link']}"

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
        p = (platform or "").lower() if isinstance(platform, str) else ""
        if not p or p not in _PLATFORMS:
            return self.status()

        p_fa = fa_platform(p)

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
                f"ℹ️ {p_fa}: هنوز فعالیتی ثبت نشده "
                f"(شاید تازه شروع شده یا وصل نیست)."
            )

        mins = (datetime.now() - last).total_seconds() / 60
        if mins < 30:
            return (
                f"✅ {p_fa}: فعال — آخرین فعالیت {int(mins)} دقیقه پیش."
            )
        return (
            f"⚠️ {p_fa}: آخرین فعالیت {int(mins)} دقیقه پیش "
            f"(شاید وصل نیست)."
        )

    # ═══════════════════════════════════════
    # لاگ
    # ═══════════════════════════════════════
    def logs(self, n: int = 12) -> str:
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 12
        n = max(1, min(n, _LOG_LINES_HARD_CAP))

        # 🎯 P0: از data.paths
        path = str(data_paths.LOG_FILE)
        if not os.path.exists(path):
            return "لاگی موجود نیست."

        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > _LOG_TAIL_BYTES:
                    f.seek(-_LOG_TAIL_BYTES, os.SEEK_END)
                    f.readline()
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

        if (
            not ans
            or "در دسترس نیست" in ans
            or ans.startswith("⚠️")
        ):
            return logs
        return ans

    # ═══════════════════════════════════════
    # پارامترها — 🎯 داده‌محور
    # ═══════════════════════════════════════
    def _read_config_only(self, key: str) -> str:
        """خواندن پارامترهای config-only از config."""
        not_set = _get_error_msg("key_not_set", _DEFAULT_KEY_NOT_SET)
        try:
            import config as _c
            mapping = {
                "ADMIN_ENABLED": lambda: _c.ADMIN_ENABLED,
                "ADMIN_PLATFORM": lambda: _c.ADMIN_PLATFORM,
                "ADMIN_IDS": lambda: ", ".join(_c.ADMIN_IDS) or "(خالی)",
                "AI_ENABLED": lambda: _c.AI_ENABLED,
                "AI_PRIORITY": lambda: _c.AI_PRIORITY,
                "FULL_ACCESS": lambda: _c.FULL_ACCESS,
                "PLATFORMS_ACTIVE": lambda: ", ".join(_c.PLATFORMS_ACTIVE) or "(هیچ)",
            }
            fn = mapping.get(key)
            if fn:
                return str(fn())
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:get_param:config] {key} | "
                f"{type(e).__name__}: {e}"
            )
        return not_set

    def get_param(self, name) -> str:
        """خواندن پارامتر — از env یا config.

        - config_only → از config
        - sensitive → از env با mask
        - safe → از env مستقیم
        """
        params = _get_params_data()
        safe = params["safe"]
        sensitive = params["sensitive"]
        config_only = params["config_only"]
        readable = safe | sensitive | config_only

        not_allowed = _get_error_msg(
            "param_not_allowed", "این پارامتر مجاز نیست."
        )

        if not name or not isinstance(name, str):
            return (
                f"{not_allowed} "
                f"مجازها: {', '.join(sorted(readable))}"
            )
        key = name.upper()
        if key not in readable:
            return (
                f"{not_allowed} "
                f"مجازها: {', '.join(sorted(readable))}"
            )

        # ─── config-only ───
        if key in config_only:
            raw = self._read_config_only(key)
            is_sensitive = key in ("ADMIN_IDS",)
            display = _mask_secret(raw) if is_sensitive else raw
            log_info(
                f"⚙️ [ADMIN:get_param] {key} "
                f"(source=config, masked={'yes' if is_sensitive else 'no'})"
            )
            return f"{key} = {display}"

        # ─── env-based ───
        not_set = _get_error_msg("key_not_set", _DEFAULT_KEY_NOT_SET)
        raw = os.getenv(key, not_set)
        if raw and len(str(raw)) > _PARAM_VALUE_MAX_LEN:
            raw = str(raw)[:_PARAM_VALUE_MAX_LEN] + "..."

        is_sensitive = key in sensitive
        display = _mask_secret(raw) if is_sensitive else raw

        log_info(
            f"⚙️ [ADMIN:get_param] {key} "
            f"(source=env, masked={'yes' if is_sensitive else 'no'})"
        )
        return f"{key} = {display}"

    def set_param(self, raw_text: str) -> str:
        params = _get_params_data()
        safe = params["safe"]

        name, val = parse_set_param(raw_text)
        if not name:
            return _get_error_msg(
                "set_param_format", "قالب: set PARAM=VALUE"
            )
        name = name.upper()
        if name not in safe:
            log_warning(f"⚠️ [ADMIN:set_param] غیرمجاز | {name}")
            return (
                f"تغییرِ «{name}» مجاز نیست. "
                f"مجازها: {', '.join(sorted(safe))}"
            )

        ok, err = self._update_env(name, val)
        if not ok:
            log_error(f"❌ [ADMIN:set_param] {name} | {err}")
            return f"خطا در تغییر: {err}"

        log_success(f"✅ [ADMIN:set_param] {name}={val}")
        return (
            f"✅ {name}={val} ذخیره شد. "
            f"برای اعمالِ کامل، ری‌استارت کن."
        )

    def _update_env(self, key: str, value: str) -> tuple[bool, str]:
        """نوشتن atomic در .env + fsync."""
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
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines.append(new_line)

            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except Exception:
                    pass

            os.replace(tmp_path, env_path)

            try:
                dir_fd = os.open(
                    os.path.dirname(env_path) or ".",
                    os.O_DIRECTORY,
                )
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass

            return True, ""
        except Exception as e:
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
        """ری‌استارت با graceful shutdown + flush کامل."""
        await asyncio.sleep(1)

        # ─── تلاش برای shutdown تمیز ───
        try:
            from main import _signal_handler
            log_info("🔁 [ADMIN:restart] shutdown تمیز...")
            _signal_handler()
            await asyncio.sleep(3)
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:restart] shutdown تمیز نشد | "
                f"{type(e).__name__}: {e}"
            )

        # ─── spawn پروسهٔ جدید ───
        spawned = False
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
            spawned = True
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

        log_warning("🔄 [ADMIN:restart] خروج از پروسهٔ قدیمی (os._exit)")

        if spawned:
            try:
                from core.kernel.logger import get_logger
                get_logger().flush()
            except Exception:
                pass

            try:
                sys.stdout.flush()
            except Exception:
                pass
            try:
                sys.stderr.flush()
            except Exception:
                pass

            os._exit(0)
        else:
            log_error("❌ [ADMIN:restart] spawn نشد — پروسهٔ قدیمی می‌ماند")

    # ═══════════════════════════════════════
    # راهنما — از data/templates/help.md
    # ═══════════════════════════════════════
    def help_text(self) -> str:
        """راهنما از data/templates/help.md.

        اگر فایل نبود → پیام پیش‌فرض.
        """
        return load_md("help", default=_DEFAULT_HELP)