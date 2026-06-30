# -*- coding: utf-8 -*-
"""
ChatOps admin handler — manage the bot from a messenger (Bale by default).

Security model (so it can NEVER damage the bot):
- Only users whose id is in ADMIN_IDS may issue commands; others are ignored.
- Only PRIVATE chats are treated as commands (group messages are never commands).
- ONLY a fixed whitelist of safe actions runs. No shell, no eval, no arbitrary code.
- Free text is understood by the local AI (Ollama) for ACTION routing only; the
  VALUES (keyword/param) are extracted from the raw text by rules (more reliable
  than a small model echoing Persian). If AI is down, rule parser still works.

Pairing: if ADMIN_IDS is empty, the bot logs the sender id of any private message
so you can copy it into ADMIN_IDS (and it tells the sender their id).
"""
import os
import sys
import json
import asyncio
import sqlite3
import subprocess
from datetime import datetime, timedelta

from core.base_handler import BaseHandler
from core.logger import get_logger, log_info, log_warning
from analyzers.keyword_finder import get_keyword_finder
from core.ai_assistant import get_ai_assistant
from core import messenger_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUSTOM_KW = os.path.join(ROOT, "data", "keywords_custom.json")
PID_FILE = os.path.join(ROOT, "storage", "bot.pid")
KNOWLEDGE = os.path.join(ROOT, "data", "bot_knowledge.md")
REQUESTS = os.path.join(ROOT, "storage", "requests.jsonl")
MEMORY = os.path.join(ROOT, "storage", "ai_memory.jsonl")   # حافظهٔ سبکِ گفتگو (capped)
MEMORY_MAX = 200                                            # سقفِ خطوط (کم‌حجم می‌ماند)

# Parameters the admin is allowed to read/change (safe whitelist; everything else refused)
SAFE_PARAMS = {
    "KEYWORDS_ONLY", "EITAA_POLL_SECONDS", "WHATSAPP_POLL_SECONDS",
    "LINKEDIN_POLL_SECONDS", "LINKEDIN_MONITOR_FEED", "WEB_ENABLED",
    "EITAA_ENABLED", "BALE_ENABLED", "RUBIKA_ENABLED", "TELEGRAM_ENABLED",
    "WHATSAPP_ENABLED", "LINKEDIN_ENABLED", "OBS_PORT", "LOG_LEVEL",
}
_PLATFORM_MAP = {
    "ایتا": "eitaa", "بله": "bale", "روبیکا": "rubika", "تلگرام": "telegram",
    "واتساپ": "whatsapp", "واتس‌اپ": "whatsapp", "لینکدین": "linkedin", "وب": "web",
    "eitaa": "eitaa", "bale": "bale", "rubika": "rubika", "telegram": "telegram",
    "whatsapp": "whatsapp", "linkedin": "linkedin", "web": "web",
}


class AdminHandler(BaseHandler):
    def __init__(self, db, scorer=None, **kwargs):
        self.db = db
        self.kw = get_keyword_finder()
        self.ai = get_ai_assistant()
        from config import PLUGINS_CONFIG
        cfg = PLUGINS_CONFIG.get("adminhandler", {})
        self.enabled = cfg.get("enabled", True)
        self.admin_ids = set(str(x) for x in cfg.get("admin_ids", []))
        self.platform = cfg.get("platform", "bale")
        self.db_path = getattr(db, "db_path", os.path.join(ROOT, "storage", "database", "messages.db"))
        self._knowledge = ""
        try:
            with open(KNOWLEDGE, "r", encoding="utf-8") as f:
                self._knowledge = f.read()
        except Exception:
            pass

    @property
    def handler_name(self):
        return "admin_handler"

    def can_handle(self, event_name):
        return self.enabled and event_name == "message.received"

    async def handle(self, event):
        raw = event.data if isinstance(event.data, dict) else {}
        if raw.get("platform") != self.platform:
            return
        # فقط چتِ خصوصی = دستور (پیامِ گروه هرگز دستور نیست)
        if (raw.get("chat_type") or "") not in ("private", "user", ""):
            return
        sender = str(raw.get("user_id") or "").strip()
        text = (raw.get("text") or raw.get("caption") or "").strip()
        if not text:
            return

        # حالتِ pairing: هنوز مدیری ثبت نشده → آی‌دی را اعلام کن
        if not self.admin_ids:
            log_warning(f"🔑 ADMIN PAIRING — platform={self.platform} user_id={sender} | "
                        f"این آی‌دی را در .env بگذار: ADMIN_IDS={sender}")
            await self._reply(raw, f"👋 آی‌دیِ تو: {sender}\nآن را در .env بگذار:\nADMIN_IDS={sender}\n"
                                   f"بعد ربات را ری‌استارت کن تا مدیر شوی.")
            return

        if sender not in self.admin_ids:
            return  # کاربرِ غیرمجاز → نادیده

        log_info(f"🛡️ admin command from {sender}: {text[:80]}")
        action, value = self._rule_parse(text)
        if action is None:
            intent = await self.ai.parse_intent(text)
            action = intent.get("action", "unknown")
            value = value or (intent.get("args", {}) or {}).get("value")
        try:
            reply = await self._execute(action, value, text)
        except Exception as e:
            reply = f"❌ خطا در اجرای دستور: {e}"
        await self._reply(raw, reply)

    # ==================================================
    # parsing
    # ==================================================
    @staticmethod
    def _extract_value(text: str):
        import re
        m = re.search(r"[«\"']([^»\"']+)[»\"']", text)   # «...» or "..." or '...'
        return m.group(1).strip() if m else None

    def _platform_in(self, low: str):
        for word, canon in _PLATFORM_MAP.items():
            if word in low:
                return canon
        return None

    def _rule_parse(self, text: str):
        low = text.lower()
        val = self._extract_value(text)
        if any(w in low for w in ["help", "راهنما", "کمک", "دستورات", "منو"]):
            return "help", None
        if any(w in low for w in ["restart", "ری‌استارت", "ریستارت", "راه‌اندازی مجدد", "ری استارت"]):
            return "restart", None
        if (("حذف" in low or "remove" in low or "پاک" in low or "delete" in low)
                and ("کلیدواژه" in low or "keyword" in low or val)):
            return "remove_keyword", val
        if ("اضافه" in low or "add" in low) and ("کلیدواژه" in low or "keyword" in low or val):
            return "add_keyword", val
        if (("دار" in low or "هست" in low or "has" in low or "exist" in low)
                and ("کلیدواژه" in low or "keyword" in low)):
            return "has_keyword", val
        # جزئیاتِ سرنخ (برای رونمایی): «اولین/آخرین سرنخ را با جزئیات بگو»
        if ("سرنخ" in low or "پایش" in low or "lead" in low) and any(w in low for w in ["جزئیات", "اولین", "آخرین", "detail", "first", "last", "نشون", "بگو"]):
            which = "first" if ("اولین" in low or "first" in low) else "last"
            return "lead_details", which
        # سلامتِ کاملِ ۰-۱۰۰
        if any(w in low for w in ["سلامت", "health", "چک کامل", "تندرستی", "همه پلتفرم", "وضعیت کامل"]):
            return "health", None
        # درخواستِ قابلیتِ جدید: فقط وقتی فعلِ «خواستن» + اشارهٔ «قابلیت/توانایی» باشد
        # (تا سؤالِ «چه قابلیت‌هایی داری؟» اشتباه گرفته نشود — آن سؤال است نه درخواست).
        _want = any(w in low for w in ["میخوام", "می‌خوام", "میخواهم", "می‌خواهم", "کاش", "اضافه کن", "اضاف کن"])
        _cap = any(w in low for w in ["بتون", "قابلیت", "feature", "توانایی"])
        if _want and _cap and "کلیدواژه" not in low:
            return "feature_request", None
        if any(w in low for w in ["کلیدواژه", "keyword"]):
            return "keywords", None
        if any(w in low for w in ["گزارش", "report"]):
            return "report", None
        if any(w in low for w in ["crm", "سی آر ام", "خروجی"]):
            return "crm", None
        # «این لاگ چیه/یعنی چی/توضیح بده» → توضیح؛ صرفِ «لاگ» → نمایش
        if ("لاگ" in low or "log" in low) and any(w in low for w in ["چیه", "یعنی", "توضیح", "معنی", "explain", "چرا"]):
            return "explain_logs", None
        if any(w in low for w in ["لاگ", "log"]):
            return "logs", None
        if (any(w in low for w in ["وضعیت", "آمار", "اوضاع", "حال", "چطور",
                                   "سرنخ", "lead", "status", "stat"])
                and not self._platform_in(low)):
            return "status", None
        p = self._platform_in(low)
        if p and any(w in low for w in ["کار", "وصل", "working", "status", "?", "؟", "فعال"]):
            return "platform_status", p
        return None, val

    # ==================================================
    # safe actions (whitelist) → reply text
    # ==================================================
    async def _execute(self, action: str, value, raw_text: str) -> str:
        if action == "help":
            return self._help()
        if action == "status":
            return self._status()
        if action == "report":
            from core.daily_reporter import generate_executive_report
            path = generate_executive_report()
            return f"📄 گزارش مدیریتی ساخته شد:\n{path}"
        if action == "crm":
            from core.crm_export import export_crm
            res = export_crm(30)
            return (f"📤 خروجی CRM آماده شد ({res.get('count', 0)} سرنخ):\n"
                    f"{res.get('xlsx') or res.get('csv')}")
        if action == "logs":
            return self._logs()
        if action == "keywords":
            ks = self.kw.keywords
            sample = "، ".join(ks[:15])
            return f"🔑 {len(ks)} کلیدواژه بارگذاری شده.\nنمونه: {sample}"
        if action == "add_keyword":
            return self._add_keyword(value or self._extract_value(raw_text))
        if action == "remove_keyword":
            return self._remove_keyword(value or self._extract_value(raw_text))
        if action == "health":
            return await self._health()
        if action == "lead_details":
            return await self._lead_details(value or "last")
        if action == "has_keyword":
            v = value or self._extract_value(raw_text)
            if not v:
                return "کدام کلیدواژه؟ داخل « » بنویس."
            found = bool(self.kw.has_match(v) or v in self.kw.keywords)
            return f"{'✅ بله' if found else '❌ نه'}، «{v}» {'هست' if found else 'نیست'}."
        if action == "platform_status":
            return await self._platform_status(value)
        if action == "get_param":
            return self._get_param(value)
        if action == "set_param":
            return self._set_param(raw_text)
        if action == "restart":
            asyncio.create_task(self._do_restart())
            return "🔄 در حال ری‌استارت... چند ثانیه دیگر دوباره بالا می‌آیم."
        if action == "explain_logs":
            return await self._explain_logs()
        if action == "feature_request":
            return self._log_request(raw_text)
        # هر چیز دیگری = سؤالِ آزاد → هوش با کمکِ سندِ دانش جواب می‌دهد
        return await self._ask(raw_text)

    async def _ask(self, question: str) -> str:
        """پاسخ به سؤالِ آزاد — هویتِ ربات + سندِ دانش + حافظهٔ زندهٔ دیتابیس + گفتگوی اخیر."""
        from core.ai_assistant import PERSONA
        system = (PERSONA + "\n\n# دانشِ ربات:\n" + self._knowledge
                  + "\n\n# وضعیتِ زندهٔ همین الان (از دیتابیس):\n" + self._live_context()
                  + self._recent_memory())
        ans = await self.ai.chat(question, system=system)
        if not ans:
            return ("هوش الان در دسترس نیست. خلاصهٔ قابلیت‌ها:\n" + self._help())
        self._remember(question, ans)   # حافظهٔ سبک (capped) — کم‌کم باهوش‌تر می‌شود
        return ans

    def _recent_memory(self, n: int = 6) -> str:
        """چند گفتگوی اخیرِ مدیر (حافظهٔ سبک). کم‌حجم چون فقط N خطِ آخر خوانده می‌شود."""
        if not os.path.exists(MEMORY):
            return ""
        try:
            with open(MEMORY, "r", encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            items = []
            for ln in lines:
                try:
                    o = json.loads(ln)
                    items.append(f"س: {o.get('q','')[:120]}\nج: {o.get('a','')[:160]}")
                except Exception:
                    pass
            return ("\n\n# گفتگوی اخیر (حافظه):\n" + "\n".join(items)) if items else ""
        except Exception:
            return ""

    def _remember(self, q: str, a: str):
        try:
            os.makedirs(os.path.dirname(MEMORY), exist_ok=True)
            with open(MEMORY, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(), "q": q, "a": a},
                                   ensure_ascii=False) + "\n")
            # سقفِ حجم: فقط آخرین MEMORY_MAX خط نگه داشته شود
            with open(MEMORY, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > MEMORY_MAX:
                with open(MEMORY, "w", encoding="utf-8") as f:
                    f.writelines(lines[-MEMORY_MAX:])
        except Exception:
            pass

    def _live_context(self) -> str:
        """خلاصهٔ کوتاه و زندهٔ وضعیت از دیتابیس (حافظه = همان دیتابیس؛ سبک، هر بار تازه).
        با بزرگ‌شدنِ داده‌ها سنگین نمی‌شود چون فقط چند عددِ خلاصه و آخرین سرنخ را می‌خواند."""
        try:
            c = sqlite3.connect(self.db_path)
            cur = c.cursor()
            total = cur.execute("SELECT COUNT(*) FROM messages WHERE keywords!=''").fetchone()[0]
            today = cur.execute("SELECT COUNT(*) FROM messages WHERE keywords!='' AND date(timestamp)=date('now')").fetchone()[0]
            byp = cur.execute("SELECT platform, COUNT(*) FROM messages WHERE keywords!='' GROUP BY platform").fetchall()
            last = cur.execute("SELECT platform, chat_name, keywords, timestamp FROM messages WHERE keywords!='' ORDER BY timestamp DESC LIMIT 1").fetchone()
            c.close()
            parts = [f"کل سرنخ‌ها: {total}", f"امروز: {today}",
                     "به‌تفکیک: " + "، ".join(f"{p}={n}" for p, n in byp) if byp else "به‌تفکیک: -"]
            if last:
                parts.append(f"آخرین سرنخ: {last[0]} / گروه {last[1]} / کلیدواژه {last[2]} / {last[3]}")
            return "\n".join(parts)
        except Exception:
            return "(فعلاً آماری در دسترس نیست)"

    async def _explain_logs(self) -> str:
        logs = self._logs(15)
        prompt = (f"این لاگ‌های ربات را خیلی ساده به فارسی توضیح بده که چه اتفاقی افتاده و "
                  f"اگر خطایی هست یعنی چه و چه باید کرد:\n\n{logs}")
        ans = await self.ai.chat(prompt)
        return ans or logs

    def _log_request(self, text: str) -> str:
        try:
            os.makedirs(os.path.dirname(REQUESTS), exist_ok=True)
            with open(REQUESTS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(), "request": text},
                                   ensure_ascii=False) + "\n")
            return ("📝 ثبت شد. این یک قابلیتِ جدید است که الان ربات ندارد؛ برای توسعه‌دهنده "
                    "ثبتش کردم تا اضافه شود.")
        except Exception as e:
            return f"خطا در ثبتِ درخواست: {e}"

    def _help(self) -> str:
        return ("🛡️ دستورها (متن آزاد هم می‌فهمم):\n"
                "• وضعیت / آمار\n• گزارش\n• CRM (اکسل)\n• لاگ\n"
                "• کلیدواژه‌ها\n• کلیدواژه «...» را اضافه کن\n• کلیدواژه «...» را داری؟\n"
                "• لینکدین کار می‌کند؟ (یا هر پلتفرم)\n• ری‌استارت")

    def _status(self) -> str:
        try:
            c = sqlite3.connect(self.db_path)
            cur = c.cursor()
            total = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            leads = cur.execute("SELECT COUNT(*) FROM messages WHERE keywords!=''").fetchone()[0]
            hot = cur.execute("SELECT COUNT(*) FROM messages WHERE opportunity_type='داغ'").fetchone()[0]
            rows = cur.execute("SELECT platform, COUNT(*) FROM messages WHERE keywords!='' GROUP BY platform").fetchall()
            c.close()
            by = "، ".join(f"{p}: {n}" for p, n in rows) or "—"
            return (f"📊 وضعیت:\nکل پیام: {total} | سرنخ: {leads} | داغ: {hot}\n"
                    f"به تفکیک پلتفرم: {by}")
        except Exception as e:
            return f"خطا در خواندن آمار: {e}"

    def _logs(self, n: int = 12) -> str:
        path = os.path.join(ROOT, "storage", "logs", "app.jsonl")
        if not os.path.exists(path):
            return "لاگی موجود نیست."
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            out = []
            for ln in lines:
                try:
                    o = json.loads(ln)
                    out.append(f"[{o.get('level','')}] {o.get('msg','')[:90]}")
                except Exception:
                    out.append(ln.strip()[:90])
            return "🗒️ آخرین لاگ‌ها:\n" + "\n".join(out)
        except Exception as e:
            return f"خطا در خواندن لاگ: {e}"

    def _add_keyword(self, value) -> str:
        if not value:
            return "کلیدواژه را داخل « » بنویس، مثلاً: کلیدواژه «کود مایع» را اضافه کن"
        try:
            data = []
            if os.path.exists(CUSTOM_KW):
                with open(CUSTOM_KW, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list):
                data = []
            if value in data:
                return f"«{value}» از قبل هست."
            data.append(value)
            with open(CUSTOM_KW, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return f"✅ «{value}» اضافه شد (تا چند ثانیهٔ دیگر خودکار فعال می‌شود، بدون ری‌استارت)."
        except Exception as e:
            return f"خطا در افزودن کلیدواژه: {e}"

    def _remove_keyword(self, value) -> str:
        if not value:
            return "کدام کلیدواژه را حذف کنم؟ داخل « » بنویس."
        try:
            data = []
            if os.path.exists(CUSTOM_KW):
                with open(CUSTOM_KW, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list) or value not in data:
                return (f"«{value}» در کلیدواژه‌های افزودنی نیست. "
                        f"(فقط کلیدواژه‌هایی که از همین‌جا اضافه شده‌اند قابلِ حذف‌اند، نه محصولاتِ اصلی.)")
            data = [k for k in data if k != value]
            with open(CUSTOM_KW, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return f"🗑️ «{value}» حذف شد (چند ثانیهٔ دیگر خودکار اعمال می‌شود)."
        except Exception as e:
            return f"خطا در حذفِ کلیدواژه: {e}"

    async def _health(self) -> str:
        """سلامتِ کاملِ ۰-۱۰۰: همهٔ پلتفرم‌ها + سیستم + شمارش."""
        lines = ["🩺 سلامتِ کامل:"]
        for p in ("eitaa", "bale", "rubika", "telegram", "whatsapp", "linkedin", "web"):
            try:
                last = await self.db.get_last_poll(p)
            except Exception:
                last = None
            if not last:
                lines.append(f"• {p}: — (فعالیتی ثبت نشده)")
            else:
                mins = (datetime.now() - last).total_seconds() / 60
                mark = "✅" if mins < 30 else "⚠️"
                lines.append(f"• {p}: {mark} آخرین فعالیت {int(mins)} دقیقه پیش")
        try:
            import psutil
            lines.append(f"💻 CPU {psutil.cpu_percent(interval=0.3)}% | RAM {psutil.virtual_memory().percent}%")
        except Exception:
            pass
        lines.append(self._status())
        return "\n".join(lines)

    async def _lead_details(self, which: str) -> str:
        """جزئیاتِ اولین/آخرین سرنخ + توضیحِ نحوهٔ ثبت (برای رونمایی به مدیر)."""
        order = "ASC" if which == "first" else "DESC"
        try:
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            row = c.execute(
                f"SELECT platform, chat_name, chat_link, keywords, opportunity_type, "
                f"opportunity_score, text, timestamp FROM messages WHERE keywords!='' "
                f"ORDER BY timestamp {order} LIMIT 1").fetchone()
            c.close()
        except Exception as e:
            return f"خطا در خواندن سرنخ: {e}"
        if not row:
            return "هنوز هیچ سرنخی ثبت نشده."
        label = "اولین" if which == "first" else "آخرین"
        detail = (f"🔎 {label} سرنخ:\n"
                  f"📱 پلتفرم: {row['platform']}\n"
                  f"👥 گروه: {row['chat_name']}\n"
                  f"🔑 کلیدواژه: {row['keywords']}\n"
                  f"🎯 {row['opportunity_type'] or '-'} (امتیاز {row['opportunity_score'] or 0})\n"
                  f"🕒 {row['timestamp']}\n"
                  f"💬 {(row['text'] or '')[:300]}")
        if row["chat_link"]:
            detail += f"\n🔗 {row['chat_link']}"
        # توضیحِ طبیعیِ نحوهٔ ثبت (با زبانِ خودِ ربات) اگر هوش در دسترس باشد
        from core.ai_assistant import PERSONA
        narration = await self.ai.chat(
            "این سرنخ را برای مدیر در یک جلسه توضیح بده: چه دیدی، چطور تشخیص دادی کلیدواژه دارد، "
            "و مراحلِ ثبتش چه بود (خواندن پیام → تطبیق کلیدواژه → امتیازدهی → ذخیره در دیتابیس). کوتاه و طبیعی.\n\n"
            + detail, system=PERSONA)
        return detail + (("\n\n🗣️ " + narration) if narration else "")

    async def _platform_status(self, platform) -> str:
        # اگر مدل نامِ پلتفرمِ معتبری نداد، به‌جای جوابِ بی‌معنی، وضعیتِ کلی را بده
        if platform not in _PLATFORM_MAP.values():
            return self._status()
        try:
            last = await self.db.get_last_poll(platform)
        except Exception:
            last = None
        if not last:
            return f"ℹ️ {platform}: هنوز فعالیتی ثبت نشده (شاید تازه شروع شده یا وصل نیست)."
        mins = (datetime.now() - last).total_seconds() / 60
        if mins < 30:
            return f"✅ {platform}: فعال — آخرین فعالیت {int(mins)} دقیقه پیش."
        return f"⚠️ {platform}: آخرین فعالیت {int(mins)} دقیقه پیش (شاید وصل نیست)."

    def _get_param(self, name) -> str:
        if not name or name.upper() not in SAFE_PARAMS:
            return f"این پارامتر مجاز نیست. مجازها: {', '.join(sorted(SAFE_PARAMS))}"
        return f"{name.upper()} = {os.getenv(name.upper(), '(تنظیم‌نشده)')}"

    def _set_param(self, raw_text: str) -> str:
        import re
        m = re.search(r"([A-Z_]{3,})\s*[=:]\s*(\S+)", raw_text)
        if not m:
            return "قالب: set PARAM=VALUE"
        name, val = m.group(1).upper(), m.group(2)
        if name not in SAFE_PARAMS:
            return f"تغییرِ «{name}» مجاز نیست. مجازها: {', '.join(sorted(SAFE_PARAMS))}"
        try:
            self._update_env(name, val)
            return f"✅ {name}={val} ذخیره شد. برای اعمالِ کامل، ری‌استارت کن."
        except Exception as e:
            return f"خطا در تغییر: {e}"

    @staticmethod
    def _update_env(key: str, value: str):
        env_path = os.path.join(ROOT, ".env")
        lines, found = [], False
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        for i, ln in enumerate(lines):
            if ln.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    async def _do_restart(self):
        await asyncio.sleep(1)
        try:
            kwargs = {"cwd": ROOT}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")], **kwargs)
            try:
                with open(PID_FILE, "w") as f:
                    f.write(str(proc.pid))
            except Exception:
                pass
        finally:
            os._exit(0)

    async def _reply(self, raw: dict, text: str):
        sender = messenger_registry.get_sender(self.platform)
        if not sender:
            log_warning(f"admin reply skipped — no sender registered for {self.platform}")
            return
        try:
            await sender(raw.get("chat_id"), raw.get("chat_type"), text)
        except Exception as e:
            log_warning(f"admin reply failed: {e}")
