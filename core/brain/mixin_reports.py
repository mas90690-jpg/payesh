"""گزارش‌ها و تنظیمات — settings, status, keywords, logs."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    LOG_TAIL_BYTES,
    LOGS_DIR,
    PROJECT_BASE,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


class ReportsMixin:
    """گزارش‌ها و تنظیمات — به Brain اضافه می‌شود."""

    # ═══════════════════════════════════════════
    # تنظیمات
    # ═══════════════════════════════════════════
    async def _get_setting(self, key: str) -> str:
        ok, reason = get_access_manager().can_read_setting(
            key.upper()
        )
        if not ok:
            return f"⛔ {reason}"
        val = os.getenv(key.upper(), "(تنظیم‌نشده)")
        log_info(f"⚙️ [BRAIN:setting:get] {key.upper()}")
        return f"⚙️ {key.upper()} = {val}"

    async def _set_setting(self, key: str, value: str) -> str:
        key_u = key.upper()
        ok, reason = get_access_manager().can_write_setting(key_u)
        if not ok:
            return f"⛔ {reason}"
        tmp_path = ""
        try:
            env_path = os.path.join(PROJECT_BASE, ".env")
            tmp_path = env_path + ".tmp"
            lines = []
            if os.path.exists(env_path):
                with open(env_path, encoding="utf-8") as f:
                    lines = f.readlines()
            found = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith(f"{key_u}="):
                    lines[i] = f"{key_u}={value}\n"
                    found = True
                    break
            if not found:
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines.append(f"{key_u}={value}\n")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            os.replace(tmp_path, env_path)
            log_success(f"✅ [BRAIN:setting:set] {key_u}={value}")
            return (
                f"✅ {key_u}={value} ذخیره شد.\n"
                f"(برای اعمال، ری‌استارت کن.)"
            )
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            log_error(
                f"❌ [BRAIN:setting:set] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _toggle_platform(
        self, platform: str, enabled: bool
    ) -> str:
        ok, reason = get_access_manager().can_toggle_platform(
            platform
        )
        if not ok:
            return f"⛔ {reason}"
        key = f"{platform.upper()}_ENABLED"
        val = "true" if enabled else "false"
        r = await self._set_setting(key, val)
        if "✅" in r:
            log_success(
                f"📱 [BRAIN:platform] {platform} → "
                f"{'روشن' if enabled else 'خاموش'}"
            )
            return (
                f"📱 {platform} → "
                f"{'روشن' if enabled else 'خاموش'}"
            )
        return r

    async def _platform_status(
        self, platform: str = "all"
    ) -> str:
        try:
            from config import PLUGINS_CONFIG as _PC

            platforms = (
                "eitaa", "bale", "rubika", "telegram",
                "whatsapp", "web", "linkedin",
            )
            targets = (
                [platform] if platform != "all"
                else list(platforms)
            )
            lines = ["📊 وضعیت پلتفرم‌ها:"]
            for p in targets:
                if p not in platforms:
                    continue
                cfg = _PC.get(f"{p}collector", {})
                enabled = cfg.get("enabled", False)
                poll = cfg.get("poll_seconds", "-")
                status = "✅ روشن" if enabled else "❌ خاموش"
                lines.append(
                    f"   • {p}: {status} | poll: {poll}s"
                )
            return "\n".join(lines)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:platform:status] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _change_poll_interval(
        self, platform: str, seconds: int
    ) -> str:
        ok, reason = get_access_manager().can_toggle_platform(
            platform
        )
        if not ok:
            return f"⛔ {reason}"
        if seconds < 10 or seconds > 86400:
            return "⛔ بین ۱۰ تا ۸۶۴۰۰ ثانیه."
        key = f"{platform.upper()}_POLL_SECONDS"
        r = await self._set_setting(key, str(seconds))
        if "✅" in r:
            log_success(
                f"⏱️ [BRAIN:poll] {platform} → {seconds}s"
            )
            return (
                f"⏱️ فاصلهٔ poll برای {platform} → "
                f"{seconds} ثانیه."
            )
        return r

    async def _get_access_summary(self) -> str:
        return get_access_manager().get_summary()

    # ═══════════════════════════════════════════
    # Status / Help / Stats
    # ═══════════════════════════════════════════
    def get_status(self) -> str:
        mem = self.memory
        return (
            f"🧠 **وضعیت مغز**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔹 مدل: {self.model}\n"
            f"🔹 حالت: {self.current_mode}\n"
            f"🔹 کلاینت: "
            f"{'✅ فعال' if self.client else '❌ آفلاین'}\n"
            f"🔹 فقط‌خواندنی: "
            f"{'✅' if self.read_only else '❌'}\n"
            f"🔹 دسترسی کامل: "
            f"{'✅' if self.full_access else '❌'}\n"
            f"🔹 حافظه معنایی: "
            f"{'✅' if mem.collection else '❌'}\n"
            f"🔹 Semantic: "
            f"{'✅' if self.semantic_search.collection else '❌'}\n"
            f"🔹 حافظه کوتاه: {len(mem.short_term)}\n"
            f"🔹 کش: {self.cache.stats()['entries']}\n"
            f"🔹 دستورات: {self.stats['commands_processed']}\n"
            f"🔹 Agent: {self.stats['agents_run']}\n"
            f"🔹 خطاها: {self.stats['errors']}\n"
            f"🔹 شروع: {self.stats['started_at']}"
        )

    def get_help(self) -> str:
        return (
            "📚 **راهنمای مغز**\n"
            "━━━━━━━━━━━━━\n"
            "💬 /status یا «وضعیت»\n"
            "💬 /help یا «راهنما»\n"
            "💬 /stats یا «آمار»\n"
            "💬 /clear یا «پاک»\n"
            "💬 /mode <mode>\n"
            "🤖 یا هر سؤال آزاد"
        )

    def get_stats(self) -> str:
        return (
            f"📊 **آمار**\n"
            f"🔹 دستورات: {self.stats['commands_processed']}\n"
            f"🔹 Agent: {self.stats['agents_run']}\n"
            f"🔹 خطاها: {self.stats['errors']}\n"
            f"🔹 حافظه: {len(self.memory.short_term)}"
        )

    def set_mode(self, mode: str):
        valid = {
            "precise", "debug", "architect", "concise",
            "creative", "code", "chat", "analyze",
        }
        if mode in valid:
            self.current_mode = mode
            log_success(f"✅ [BRAIN:mode] → {mode}")
        else:
            log_warning(f"⚠️ [BRAIN:mode] نامعتبر: {mode}")

    # ═══════════════════════════════════════════
    # Reports
    # ═══════════════════════════════════════════
    async def _get_health_report(self) -> str:
        """گزارش سلامت کامل از HealthChecker (timeout 15s)."""
        try:
            from core.ops.health_check import HealthChecker
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:health] HealthChecker import | "
                f"{type(e).__name__}: {e}"
            )
            return "⚠️ HealthChecker در دسترس نیست."

        if not self.db:
            return "⚠️ دیتابیس متصل نیست."

        try:
            hc = HealthChecker(self.db)
            report = await asyncio.wait_for(
                hc.check_all(),
                timeout=15.0,
            )
            if isinstance(report, dict):
                return json.dumps(
                    report, ensure_ascii=False, indent=2
                )[:3000]
            return str(report)[:3000]
        except TimeoutError:
            log_warning("⏰ [BRAIN:health] timeout (15s)")
            return "⏰ بررسی سلامت طول کشید (timeout 15s)."
        except Exception as e:
            log_error(
                f"❌ [BRAIN:health] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _get_keywords_report(self) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            stats = await self.db.get_keyword_stats(limit=30)
            if not stats:
                return "📭 کلیدواژه‌ای ثبت نشده."
            lines = ["📊 **کلیدواژه‌ها**"]
            for kw, count in stats[:20]:
                lines.append(f"• {kw}: {count}")
            return "\n".join(lines)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:keywords_report] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _get_platform_status(self, platform: str) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            last_poll = await self.db.get_last_poll(platform)
            if not last_poll:
                return f"📡 **{platform}**: داده‌ای نیست."
            ago = (datetime.now() - last_poll).total_seconds()
            if ago < 60:
                return f"📡 **{platform}**: فعال ✅"
            elif ago < 3600:
                return f"📡 **{platform}**: {ago/60:.0f} دقیقه"
            return f"📡 **{platform}**: {ago/3600:.1f} ساعت"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:platform_status:{platform}] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _get_report(self) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            total = await self.db.get_total_count()
            daily = await self.db.get_daily_stats(days=7)
            lines = [
                "📊 **گزارش سریع**",
                f"🔹 پیام‌ها: {total.get('messages', 0)}",
                f"🔹 سرنخ‌ها: {total.get('leads', 0)}",
                "📅 **۷ روز اخیر:**",
            ]
            for d in daily[:7]:
                lines.append(
                    f"  {d.get('date', '?')}: "
                    f"{d.get('count', 0)}"
                )
            return "\n".join(lines)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:report] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    def _get_recent_logs(self, limit: int = 30) -> str:
        """آخرین خطوط لاگ — با seek از انتها (نه کل فایل در RAM)."""
        log_dir = Path(LOGS_DIR)
        if not log_dir.exists():
            return "📭 پوشه لاگ نیست."
        logs = (
            list(log_dir.glob("*.log"))
            + list(log_dir.glob("*.txt"))
            + list(log_dir.glob("*.jsonl"))
        )
        if not logs:
            return "📭 فایل لاگ نیست."
        latest = max(logs, key=lambda p: p.stat().st_mtime)
        try:
            size = latest.stat().st_size
            with open(latest, "rb") as f:
                if size > LOG_TAIL_BYTES:
                    f.seek(-LOG_TAIL_BYTES, os.SEEK_END)
                    f.readline()
                data = f.read()
            lines = data.decode(
                "utf-8", errors="ignore"
            ).splitlines()
            recent = lines[-limit:] if len(lines) > limit else lines
            log_info(
                f"📋 [BRAIN:logs] {latest.name} | "
                f"lines≈{len(lines)} showing={len(recent)}"
            )
            return (
                f"📋 **لاگ‌ها ({latest.name}):**\n"
                + "\n".join(recent)
            )
        except Exception as e:
            log_error(
                f"❌ [BRAIN:recent_logs] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _get_lead_details(
        self, which: str = "last"
    ) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            leads = await self.db.get_high_score_leads(
                min_score=0, limit=1
            )
            if not leads:
                return "📭 سرنخی نیست."
            lead = leads[0]
            return (
                f"📌 **سرنخ**\n"
                f"پلتفرم: {lead.get('platform', '?')}\n"
                f"چت: {lead.get('chat_name', '?')}\n"
                f"متن: {str(lead.get('text', ''))[:200]}\n"
                f"کلیدواژه: {lead.get('keywords', '')}"
            )
        except Exception as e:
            log_error(
                f"❌ [BRAIN:lead_details] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _check_keyword(self, kw: str) -> str:
        if not kw:
            return "⚠️ کلیدواژه وارد کن."
        try:
            from analyzers.keyword_finder import (
                get_keyword_finder,
            )

            finder = get_keyword_finder()
            found = finder.has_match(kw)
            log_info(
                f"🔍 [BRAIN:kw-check] kw={kw!r} | found={found}"
            )
            if found:
                return f"✅ '{kw}' وجود دارد."
            return f"❌ '{kw}' یافت نشد."
        except Exception as e:
            log_error(
                f"❌ [BRAIN:check_keyword] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    def generate_report(self, days: int = 7) -> str:
        return (
            f"📊 **گزارش Agent**\n"
            f"دستورات: {self.stats['commands_processed']}\n"
            f"Agent: {self.stats['agents_run']}\n"
            f"خطاها: {self.stats['errors']}\n"
            f"یادگیری: "
            f"{len(self.learning_engine.successful_interactions)} ok, "
            f"{len(self.learning_engine.failed_interactions)} fail\n"
            f"کش: {self.cache.stats()['entries']}"
        )

    def analyze_logs(self, category: str = None) -> str:
        """تحلیل لاگ‌ها — دسته‌بندی خطاها بر اساس نوع."""
        try:
            logs_text = self._get_recent_logs(limit=200)
            if not logs_text or "لاگ" in logs_text[:20]:
                return logs_text

            categories = {
                "شبکه": [
                    "connection", "timeout", "network", "socket",
                ],
                "دیتابیس": [
                    "database", "sqlite", "sql", "locked",
                ],
                "AI": [
                    "openai", "ollama", "deepseek", "api",
                ],
                "فایل": [
                    "file", "path", "permission", "read",
                ],
                "مرورگر": [
                    "playwright", "chromium", "browser",
                ],
                "کلیدواژه": [
                    "keyword", "کلیدواژه",
                ],
            }

            counts = dict.fromkeys(categories, 0)
            counts["سایر"] = 0
            lines = logs_text.split("\n")
            error_lines = [
                ln for ln in lines
                if "[ERROR]" in ln
                or "[CRITICAL]" in ln
                or "❌" in ln
            ]
            for ln in error_lines:
                ln_low = ln.lower()
                matched = False
                for cat, keywords in categories.items():
                    if any(kw in ln_low for kw in keywords):
                        counts[cat] += 1
                        matched = True
                        break
                if not matched:
                    counts["سایر"] += 1
            result = [f"📋 کل: {len(lines)} خطا: {len(error_lines)}"]
            for cat, cnt in sorted(
                counts.items(), key=lambda x: -x[1]
            ):
                if cnt > 0:
                    result.append(f"• {cat}: {cnt}")
            log_info(
                f"📊 [BRAIN:logs:analyze] "
                f"errors={len(error_lines)} categories={counts}"
            )
            return "\n".join(result)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:analyze_logs] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"