# core/manual/_brain_rules.py
"""
تطبیق قوانین + اجرای action + callableها.

این ماژول:
  - match_against, match_exact, match_fuzzy
  - trigger_exists
  - execute_action (sql_scalar, sql_row, sql_rows, text, template, callable)
  - call_builtin (همهٔ handlerهای rules.json)
  - format_lead

نسخهٔ ۹ (این نسخه):
  - 🎯 FIX: `_call_builtin` از CC=62 به CC≈3 کاهش یافت.
    با استفاده از dispatch table به ۲۲ متد کوچک تقسیم شد.
  - ✅ حفظ همهٔ رفتار قبلی.
"""
from __future__ import annotations

import time
from datetime import datetime
from difflib import SequenceMatcher

from core.kernel.logger import log_warning

from core.manual._brain_core import (
    FUZZY_THRESHOLD,
    SLOW_ACTION_MS,
)


# ═══════════════════════════════════════════════════════════
# dispatch table برای _call_builtin
# ═══════════════════════════════════════════════════════════
_BUILTIN_HANDLERS: dict[str, str] = {
    "keyword_count": "_builtin_keyword_count",
    "keyword_list": "_builtin_keyword_list",
    "custom_keyword_list": "_builtin_custom_keyword_list",
    "target_group_list": "_builtin_target_group_list",
    "today_by_platform": "_builtin_today_by_platform",
    "platform_status": "_builtin_platform_status",
    "top_keywords": "_builtin_top_keywords",
    "top_groups": "_builtin_top_groups",
    "last_lead": "_builtin_last_lead",
    "first_lead": "_builtin_first_lead",
    "recent_logs": "_builtin_recent_logs",
    "error_count": "_builtin_error_count",
    "recent_reports": "_builtin_recent_reports",
    "system_snapshot": "_builtin_system_snapshot",
    "compare_days": "_builtin_compare_days",
    "today_hourly": "_builtin_today_hourly",
    "hot_recent": "_builtin_hot_recent",
    "high_score": "_builtin_high_score",
    "silent_groups": "_builtin_silent_groups",
    "by_platform": "_builtin_by_platform",
    "top_platform": "_builtin_top_platform",
    "top_group": "_builtin_top_group",
}


# ═══════════════════════════════════════════════════════════
# BrainRulesMixin
# ═══════════════════════════════════════════════════════════
class BrainRulesMixin:
    """Mixin تطبیق قوانین + اجرا + callables."""

    # ═══════════════════════════════════════════
    # تطبیق
    # ═══════════════════════════════════════════
    def _match_against(
        self, rules: list[dict], low: str
    ) -> tuple[dict | None, int]:
        best_rule = None
        best_score = 0
        for rule in rules:
            for trigger in rule.get("triggers", []):
                score = 0
                if isinstance(trigger, str):
                    if self._normalize(trigger) in low:
                        score = len(trigger)
                elif isinstance(trigger, list):
                    if all(
                        self._normalize(w) in low for w in trigger
                    ):
                        score = sum(len(w) for w in trigger)
                if score > best_score:
                    best_score = score
                    best_rule = rule
        return best_rule, best_score

    def _match_exact(
        self, text: str
    ) -> tuple[dict | None, int, str]:
        low = self._normalize(text)
        rule, score = self._match_against(self.core_rules, low)
        if rule:
            return rule, score, "core"
        rule, score = self._match_against(self.learned_rules, low)
        if rule:
            return rule, score, "learned"
        return None, 0, ""

    def _match_fuzzy(self, text: str) -> dict | None:
        low = self._normalize(text)
        for _layer, rules in (
            ("core", self.core_rules),
            ("learned", self.learned_rules),
        ):
            best_rule = None
            best_sim = FUZZY_THRESHOLD
            for rule in rules:
                for trigger in rule.get("triggers", []):
                    if not isinstance(trigger, str):
                        continue
                    t = self._normalize(trigger)
                    if len(t) < 4:
                        continue
                    sim = SequenceMatcher(None, t, low).ratio()
                    if sim > best_sim:
                        best_sim = sim
                        best_rule = rule
            if best_rule:
                return best_rule
        return None

    def _trigger_exists(self, trigger: str) -> bool:
        low = self._normalize(trigger)
        if not low:
            return True
        for rules in (
            self.core_rules, self.learned_rules, self._candidates
        ):
            for rule in rules:
                for t in rule.get("triggers", []):
                    if (
                        isinstance(t, str)
                        and self._normalize(t) == low
                    ):
                        return True
        return False

    # ═══════════════════════════════════════════
    # اجرای action
    # ═══════════════════════════════════════════
    def _execute_action(
        self, action: dict, text: str
    ) -> str | None:
        if not isinstance(action, dict):
            return None
        t = action.get("type")
        t0 = time.monotonic()
        result: str | None = None

        try:
            if t == "sql_scalar":
                v = self.api.scalar(
                    action["sql"], tuple(action.get("params", []))
                )
                tmpl = action.get("template", "{v}")
                result = tmpl.format(v=v)
            elif t == "sql_row":
                row = self.api.row(
                    action["sql"], tuple(action.get("params", []))
                )
                if not row:
                    result = action.get(
                        "empty", "📭 نتیجه‌ای یافت نشد"
                    )
                else:
                    try:
                        result = action.get("template", "").format(
                            **row
                        )
                    except KeyError as e:
                        result = f"⚠️ ستون ناموجود: {e}"
            elif t == "sql_rows":
                rows = self.api.sql(
                    action["sql"], tuple(action.get("params", []))
                )
                if not rows:
                    result = action.get(
                        "empty", "📭 نتیجه‌ای یافت نشد"
                    )
                else:
                    tmpl = action.get("row_template", "• {0}")
                    out = [action.get("header", "")]
                    for r in rows:
                        try:
                            out.append(tmpl.format(**r))
                        except Exception:
                            out.append(
                                "• " + " | ".join(
                                    str(v) for v in r.values()
                                )
                            )
                    result = "\n".join(s for s in out if s)
            elif t == "text":
                if action.get("text"):
                    result = action["text"]
                else:
                    text_key = action.get("text_key")
                    result = (
                        self._get_template(text_key)
                        if text_key else ""
                    )
            elif t == "text_key":
                key = action.get("key") or action.get("text_key")
                result = self._get_template(key) if key else ""
            elif t == "template":
                ctx = {
                    "leads_total": self.api.leads_total(),
                    "leads_today": self.api.leads_today(),
                    "leads_yesterday": self.api.leads_yesterday(),
                    "leads_last_7d": self.api.leads_last_n_days(7),
                    "leads_last_30d": self.api.leads_last_n_days(30),
                    "hot_total": self.api.hot_total(),
                    "hot_today": self.api.hot_today(),
                    "keyword_count": self.api.keyword_count(),
                    "custom_kw_count": len(
                        self.api.custom_keywords()
                    ),
                    "target_group_count": len(
                        self.api.target_groups()
                    ),
                    "errors_recent": self.api.error_count_recent(),
                }
                result = action.get("template", "").format(**ctx)
            elif t == "callable":
                fn = action.get("function", "")
                result = self._call_builtin(fn, text)
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:exec:{t}] "
                f"{type(e).__name__}: {e}"
            )
            return f"⚠️ خطا: {e}"
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > SLOW_ACTION_MS:
                log_warning(
                    f"🐌 [MBRAIN:exec:{t}] کند | "
                    f"{elapsed_ms:.0f}ms"
                )

        return result

    # ═══════════════════════════════════════════
    # callableهای builtin — dispatch
    # ═══════════════════════════════════════════
    def _call_builtin(self, fn: str, text: str) -> str | None:
        """🎯 FIX: dispatch table به‌جای ۳۰ تا if زنجیره‌ای.

        CC قبلاً ۶۲ بود. الان هر handler تابع جدا با CC پایین.
        """
        name = _BUILTIN_HANDLERS.get(fn)
        if not name:
            return None
        handler = getattr(self, name, None)
        if not handler:
            log_warning(
                f"⚠️ [MBRAIN:builtin] handler ناشناخته: {fn}"
            )
            return None
        p = self._extract_platform(text)
        return handler(text, p)

    # ═══════════════════════════════════════════
    # handlerهای builtin — هر کدام تابع مستقل
    # ═══════════════════════════════════════════
    def _builtin_keyword_count(
        self, text: str, p: str
    ) -> str:
        return f"🔑 تعداد کلیدواژه‌ها: {self.api.keyword_count()}"

    def _builtin_keyword_list(
        self, text: str, p: str
    ) -> str | None:
        return self._handle_kw_list(
            text, self._normalize(text), []
        )

    def _builtin_custom_keyword_list(
        self, text: str, p: str
    ) -> str:
        kws = self.api.custom_keywords()
        if not kws:
            return "📭 هیچ کلیدواژهٔ سفارشی‌ای ثبت نشده."
        return (
            f"🔧 {len(kws)} کلیدواژهٔ سفارشی:\n"
            + "\n".join(f"• {k}" for k in kws[:30])
        )

    def _builtin_target_group_list(
        self, text: str, p: str
    ) -> str:
        gs = self.api.target_groups()
        if not gs:
            return "📭 هیچ گروه هدفی تعریف نشده (همه قبولند)."
        return (
            f"🎯 {len(gs)} گروه هدف:\n"
            + "\n".join(f"• {g}" for g in gs[:30])
        )

    def _builtin_today_by_platform(
        self, text: str, p: str
    ) -> str:
        rows = self.api.sql(
            "SELECT platform, COUNT(*) as n FROM messages "
            "WHERE keywords!='' AND date(timestamp)=date('now') "
            "GROUP BY platform ORDER BY n DESC"
        )
        if not rows:
            return "📭 امروز سرنخی نیست."
        if p:
            for r in rows:
                if r["platform"] == p:
                    return f"📊 امروز {p}: {r['n']} سرنخ"
            return f"📊 امروز {p}: 0 سرنخ"
        lines = ["📊 امروز به تفکیک:"]
        for r in rows:
            lines.append(f"   • {r['platform']}: {r['n']}")
        return "\n".join(lines)

    def _builtin_platform_status(
        self, text: str, p: str
    ) -> str:
        if not p:
            return "ℹ️ کدام پلتفرم؟ مثال: «ایتا کار می‌کند؟»"
        last = self.api.platform_last_poll(p)
        if not last:
            return f"ℹ️ {p}: هنوز فعالیتی ثبت نشده."
        mins = (datetime.now() - last).total_seconds() / 60
        if mins < 30:
            return f"✅ {p}: فعال — {int(mins)} دقیقه پیش"
        return f"⚠️ {p}: آخرین فعالیت {int(mins)} دقیقه پیش"

    def _builtin_top_keywords(
        self, text: str, p: str
    ) -> str:
        rows = self.api.top_keywords(10, 30)
        if not rows:
            return "📭 کلیدواژه‌ای در ۳۰ روز اخیر نیست."
        return (
            "🔥 پرتکرارترین کلیدواژه‌ها (۳۰ روز):\n"
            + "\n".join(
                f"   • {r['keyword']}: {r['count']}"
                for r in rows
            )
        )

    def _builtin_top_groups(
        self, text: str, p: str
    ) -> str:
        rows = self.api.top_groups(10, 30)
        if not rows:
            return "📭 گروهی در ۳۰ روز اخیر نیست."
        return (
            "🏆 پرسرنخ‌ترین گروه‌ها (۳۰ روز):\n"
            + "\n".join(
                f"   • {r['chat_name']} ({r['platform']}): "
                f"{r['n']}"
                for r in rows
            )
        )

    def _builtin_last_lead(
        self, text: str, p: str
    ) -> str:
        r = self.api.last_lead(platform=p)
        if not r:
            if p:
                return f"📭 هنوز سرنخی از {p} نیست."
            return "📭 هنوز سرنخی نیست."
        label = f"آخرین ({p})" if p else "آخرین"
        return self._format_lead(r, label)

    def _builtin_first_lead(
        self, text: str, p: str
    ) -> str:
        r = self.api.first_lead(platform=p)
        if not r:
            if p:
                return f"📭 هنوز سرنخی از {p} نیست."
            return "📭 هنوز سرنخی نیست."
        label = f"اولین سرنخ {p}" if p else "اولین سرنخ"
        return self._format_lead(r, label)

    def _builtin_recent_logs(
        self, text: str, p: str
    ) -> str:
        logs = self.api.recent_logs(10)
        if not logs:
            return "📭 لاگی نیست."
        return "🗒️ آخرین لاگ‌ها:\n" + "\n".join(
            f"[{entry.get('level', '')}] {entry.get('msg', '')[:90]}"
            for entry in logs
        )

    def _builtin_error_count(
        self, text: str, p: str
    ) -> str:
        return (
            f"⚠️ تعداد خطا در لاگِ اخیر: "
            f"{self.api.error_count_recent()}"
        )

    def _builtin_recent_reports(
        self, text: str, p: str
    ) -> str:
        rs = self.api.recent_reports(10)
        if not rs:
            return "📭 گزارشی نیست."
        return "📁 آخرین گزارش‌ها:\n" + "\n".join(
            f"   • {r['name']} ({r['size_kb']:.1f}KB)"
            for r in rs
        )

    def _builtin_system_snapshot(
        self, text: str, p: str
    ) -> str:
        s = self.api.system_snapshot()
        lines = [
            "📊 خلاصهٔ سیستم:",
            f"   • کل سرنخ‌ها: {s['leads_total']}",
            f"   • امروز: {s['leads_today']} | "
            f"دیروز: {s['leads_yesterday']}",
            f"   • ۷ روز: {s['leads_last_7d']} | "
            f"۳۰ روز: {s['leads_last_30d']}",
            f"   • داغ: {s['hot_total']} "
            f"(امروز: {s['hot_today']})",
            f"   • کلیدواژه‌ها: {s['keyword_count']} | "
            f"سفارشی: {s['custom_kw_count']}",
            f"   • گروه هدف: {s['target_group_count']}",
            f"   • خطاهای اخیر: {s['errors_recent']}",
        ]
        if s["by_platform"]:
            lines.append("   • به تفکیک پلتفرم:")
            for r in s["by_platform"]:
                lines.append(f"       {r['platform']}: {r['n']}")
        return "\n".join(lines)

    def _builtin_compare_days(
        self, text: str, p: str
    ) -> str:
        d = self.api.compare_today_yesterday()
        arrow = (
            "📈" if d["diff"] > 0
            else ("📉" if d["diff"] < 0 else "➡️")
        )
        return (
            f"📊 مقایسه:\n"
            f"   امروز: {d['today']}\n"
            f"   دیروز: {d['yesterday']}\n"
            f"   {arrow} اختلاف: {d['diff']:+d}"
        )

    def _builtin_today_hourly(
        self, text: str, p: str
    ) -> str:
        rows = self.api.leads_by_hour_today()
        if not rows:
            return "📭 امروز هنوز سرنخی نیست."
        return "🕐 امروز به تفکیک ساعت:\n" + "\n".join(
            f"   • {r['hour']}:00 → {r['n']}" for r in rows
        )

    def _builtin_hot_recent(
        self, text: str, p: str
    ) -> str:
        rows = self.api.recent_hot_leads(5)
        if not rows:
            return "📭 سرنخ داغی نیست."
        return "🔥 ۵ سرنخ داغ اخیر:\n" + "\n".join(
            f"   • [{r['platform']}] {r['chat_name']} "
            f"(امتیاز {r['opportunity_score']})\n"
            f"     {r['text'][:80]}..."
            for r in rows
        )

    def _builtin_high_score(
        self, text: str, p: str
    ) -> str:
        rows = self.api.high_score_leads(50, 10)
        if not rows:
            return "📭 سرنخ با امتیاز بالا نیست."
        return "⭐ ۱۰ سرنخ قوی:\n" + "\n".join(
            f"   • [{r['platform']}] {r['chat_name']} | "
            f"{r['keywords']} | {r['opportunity_score']}"
            for r in rows
        )

    def _builtin_silent_groups(
        self, text: str, p: str
    ) -> str:
        rows = self.api.silent_groups(7)
        if not rows:
            return "✅ همهٔ گروه‌ها فعالن."
        return (
            f"😴 {len(rows)} گروه ساکت (۷ روز اخیر):\n"
            + "\n".join(
                f"   • {r['chat_name']} "
                f"(آخرین: {r['last_seen'][:10]})"
                for r in rows
            )
        )

    def _builtin_by_platform(
        self, text: str, p: str
    ) -> str:
        rows = self.api.sql(
            "SELECT platform, COUNT(*) as n FROM messages "
            "WHERE keywords!='' GROUP BY platform "
            "ORDER BY n DESC"
        )
        if not rows:
            return "📭 هنوز سرنخی ثبت نشده."
        lines = ["📊 تفکیک پلتفرم:"]
        for r in rows:
            lines.append(
                f"   • {r['platform']}: {r['n']} سرنخ"
            )
        total = sum(r["n"] for r in rows)
        lines.append(f"\n🔑 کل: {total} سرنخ")
        return "\n".join(lines)

    def _builtin_top_platform(
        self, text: str, p: str
    ) -> str:
        r = self.api.row(
            "SELECT platform, COUNT(*) as n FROM messages "
            "WHERE keywords!='' GROUP BY platform "
            "ORDER BY n DESC LIMIT 1"
        )
        if not r:
            return "📭 هنوز سرنخی ثبت نشده."
        return (
            f"🏆 پرسرنخ‌ترین پلتفرم: {r['platform']} "
            f"({r['n']} سرنخ)"
        )

    def _builtin_top_group(
        self, text: str, p: str
    ) -> str:
        r = self.api.row(
            "SELECT chat_name, platform, COUNT(*) as n "
            "FROM messages WHERE keywords!='' "
            "GROUP BY chat_name, platform "
            "ORDER BY n DESC LIMIT 1"
        )
        if not r:
            return "📭 هنوز سرنخی ثبت نشده."
        return (
            f"🏆 پرسرنخ‌ترین گروه:\n"
            f"   👥 {r['chat_name']} ({r['platform']})\n"
            f"   🔑 {r['n']} سرنخ"
        )

    # ═══════════════════════════════════════════
    # format helpers
    # ═══════════════════════════════════════════
    def _format_lead(self, r: dict, label: str) -> str:
        return (
            f"🔎 {label} سرنخ:\n"
            f"📱 پلتفرم: {r.get('platform', '?')}\n"
            f"👥 گروه: {r.get('chat_name', '?')}\n"
            f"🔑 کلیدواژه: {r.get('keywords', '?')}\n"
            f"🎯 {r.get('opportunity_type', '-')} "
            f"(امتیاز {r.get('opportunity_score', 0)})\n"
            f"🕒 {r.get('timestamp', '?')}\n"
            f"💬 {(r.get('text') or '')[:300]}"
            + (
                f"\n🔗 {r['chat_link']}"
                if r.get("chat_link") else ""
            )
        )
