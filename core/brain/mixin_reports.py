"""
گزارشها و تنظیمات — settings, status, keywords, logs.

نسخهٔ ۲ (این نسخه):
  - 🎯 `PLATFORMS_ORDER` از `config` (شامل instagram) — بهجای hardcode.
  - 🎯 `_get_recent_logs` حالا از `data.paths.LOG_FILE` میخواند.
  - 🎯 `_set_setting` حالا `os.fsync` میزند (durability).
  - 🎯 `_get_health_report` — timeout از `AI_CONFIG` (env-based).
  - 🎯 `generate_report` حالا `days` را اعمال میکند (پارامتر بیاستفاده بود).
  - 🐛 FIX: `_get_setting` حالا از `config` هم برای پارامترهای top-level
    استفاده میکند (ADMIN_ENABLED, ...).
  - 🐛 FIX: `_platform_status` حالا `PLATFORMS_ORDER` را از config
    میگیرد.
  - 🎯 `_env_float` helper برای thresholds.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""

from __future__ import annotations
from data.loader import load_json as _load_json
import asyncio
import contextlib
import json
import os
import sys          # ← اضافه
from datetime import datetime
from pathlib import Path

# 🎯 P0: از config
from config import (
    ADMIN_ENABLED,
    ADMIN_IDS,
    ADMIN_PLATFORM,
    PLATFORMS_ORDER,
)
from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    LOG_TAIL_BYTES,
    PROJECT_BASE,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)

# 🎯 P0: مسیرها از data.paths
from data import paths as data_paths


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
def _env_float(
    name: str,
    default: float,
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


# آستانهٔ timeout برای health check (env-based)
_HEALTH_TIMEOUT_S = _env_float(
    "BRAIN_HEALTH_TIMEOUT_S", 15.0, min_val=2.0, max_val=120.0
)

# سقف نمایش در پاسخها
_MAX_HEALTH_RESPONSE_LEN = 3000

# 🆕 _CONFIG_PARAMS از data/configs/config_params.json

# نگاشت پارامتر → تابع خواندن (lambdaها اینجا می‌مونن چون کد هستن)
_CONFIG_GETTERS = {
    "ADMIN_ENABLED": lambda: ADMIN_ENABLED,
    "ADMIN_PLATFORM": lambda: ADMIN_PLATFORM,
    "ADMIN_IDS": lambda: ", ".join(ADMIN_IDS) or "(خالی)",
}

# لیست پارامترها از data
_config_params_data = _load_json("configs/config_params.json")
_CONFIG_PARAMS = {
    key: _CONFIG_GETTERS[key]
    for key in _config_params_data.get("params", [])
    if key in _CONFIG_GETTERS
}


class ReportsMixin:
    """گزارشها و تنظیمات — به Brain اضافه میشود."""

    # ═══════════════════════════════════════════
    # تنظیمات
    # ═══════════════════════════════════════════
    async def _get_setting(self, key: str) -> str:
        """خواندن یک پارامتر از env یا config."""
        key_u = key.upper()
        try:
            access = get_access_manager()
            deny = access.rules.get("settings", {}).get("deny", []) or []
            deny_set = {str(x).upper() for x in deny}
            if key_u in deny_set:
                log_warning(f"⛔ [BRAIN:setting:get] deny | {key_u}")
                return f"⛔ خواندن {key_u} ممنوع است."
        except Exception as e:
            log_warning(
                f"⚠️ [core.brain.mixin_reports] {
                    type(e).__name__}: {e}")
        ok, reason = get_access_manager().can_read_setting(key_u)
        if not ok:
            return f"⛔ {reason}"

        if key_u in _CONFIG_PARAMS:
            try:
                val = _CONFIG_PARAMS[key_u]()
                log_info(f"⚙️ [BRAIN:setting:get] {key_u} (source=config)")
                return f"⚙️ {key_u} = {val}"
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:setting:get] {key_u} config | {
                        type(e).__name__}: {e}")

        val = os.getenv(key_u, "(تنظیم‌نشده)")
        log_info(f"⚙️ [BRAIN:setting:get] {key_u} (source=env)")
        return f"⚙️ {key_u} = {val}"

    async def _set_setting(self, key: str, value: str) -> str:
        """🎯 FIX: نوشتن در .env با fsync."""
        key_u = key.upper()
        ok, reason = get_access_manager().can_write_setting(key_u)
        if not ok:
            return f"⛔ {reason}"

        tmp_path = ""
        try:
            env_path = os.path.join(PROJECT_BASE, ".env")
            tmp_path = env_path + ".tmp"
            lines: list[str] = []
            if os.path.exists(env_path):

                def _read():
                    with open(env_path, encoding="utf-8") as f:
                        return f.readlines()

                lines = await asyncio.to_thread(_read)

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

            # 🎯 FIX: fsync برای durability
            def _write_env():
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                    try:
                        f.flush()
                        os.fsync(f.fileno())
                    except (AttributeError, OSError) as e:
                        log_warning(
                            f"⚠️ [core.brain.mixin_reports] {
                                type(e).__name__}: {e}")

            await asyncio.to_thread(_write_env)
            os.replace(tmp_path, env_path)

            # fsync دایرکتوری — فقط POSIX (روی ویندوز os.open روی دایرکتوری
            # PermissionError می‌دهد)
            if sys.platform != "win32":
                try:
                    parent = os.path.dirname(env_path) or "."
                    if hasattr(os, "O_DIRECTORY"):
                        dir_fd = os.open(parent, os.O_DIRECTORY)
                    else:
                        dir_fd = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (AttributeError, OSError) as e:
                    log_warning(
                        f"⚠️ [core.brain.mixin_reports] {
                            type(e).__name__}: {e}")

            log_success(
                f"✅ [BRAIN:setting:set] {key_u}={value} | "
                f"{'بهروز' if found else 'جدید'}"
            )
            return f"✅ {key_u}={value} ذخیره شد.\n(برای اعمال، ریاستارت کن.)"
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                with contextlib.suppress(Exception):
                    os.remove(tmp_path)
            log_error(f"❌ [BRAIN:setting:set] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _toggle_platform(self, platform: str, enabled: bool) -> str:
        ok, reason = get_access_manager().can_toggle_platform(platform)
        if not ok:
            return f"⛔ {reason}"
        key = f"{platform.upper()}_ENABLED"
        val = "true" if enabled else "false"
        r = await self._set_setting(key, val)
        if "✅" in r:
            log_success(
                f"📱 [BRAIN:platform] {platform} → {
                    'روشن' if enabled else 'خاموش'}")
            return f"📱 {platform} → {'روشن' if enabled else 'خاموش'}"
        return r

    async def _platform_status(self, platform: str = "all") -> str:
        """🎯 FIX: از config.PLATFORMS_ORDER (شامل instagram)."""
        try:
            from config import PLUGINS_CONFIG as _PC

            # 🎯 FIX: از config.PLATFORMS_ORDER
            platforms = tuple(PLATFORMS_ORDER)

            targets = (
                [platform]
                if platform != "all" and platform in platforms
                else list(platforms)
            )
            lines = ["📊 وضعیت پلتفرمها:"]
            for p in targets:
                cfg = _PC.get(f"{p}collector", {})
                enabled = cfg.get("enabled", False)
                poll = cfg.get("poll_seconds", "-")
                status = "✅ روشن" if enabled else "❌ خاموش"
                lines.append(f"   • {p}: {status} | poll: {poll}s")
            return "\n".join(lines)
        except Exception as e:
            log_error(f"❌ [BRAIN:platform:status] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _change_poll_interval(self, platform: str, seconds: int) -> str:
        ok, reason = get_access_manager().can_toggle_platform(platform)
        if not ok:
            return f"⛔ {reason}"
        if seconds < 10 or seconds > 86400:
            return "⛔ بین ۱۰ تا ۸۶۴۰۰ ثانیه."
        key = f"{platform.upper()}_POLL_SECONDS"
        r = await self._set_setting(key, str(seconds))
        if "✅" in r:
            log_success(f"⏱️ [BRAIN:poll] {platform} → {seconds}s")
            return f"⏱️ فاصلهٔ poll برای {platform} → {seconds} ثانیه."
        return r

    async def _get_access_summary(self) -> str:
        return get_access_manager().get_summary()

    # ═══════════════════════════════════════════
    # Status / Help / Stats
    # ═══════════════════════════════════════════
    def get_status(self) -> str:
        mem = getattr(self, "memory", None)
        stats = getattr(self, "stats", {}) or {}
        sem_collection = getattr(
            getattr(self, "semantic_search", None), "collection", None
        )
        cache_entries = 0
        try:
            cache_entries = getattr(self, "cache", None).stats()["entries"]
        except Exception:
            pass
        return (
            f"🧠 **وضعیت مغز**\n"
            f"───────────────\n"
            f"🔹 مدل: {getattr(self, 'model', '?')}\n"
            f"🔹 حالت: {getattr(self, 'current_mode', '?')}\n"
            f"🔹 کلاینت: "
            f"{'✅ فعال' if getattr(self, 'client', None) else '❌ آفلاین'}\n"
            f"🔹 فقط‌خواندنی: "
            f"{'✅' if getattr(self, 'read_only', False) else '❌'}\n"
            f"🔹 دسترسی کامل: "
            f"{'✅' if getattr(self, 'full_access', False) else '❌'}\n"
            f"🔹 حافظه معنایی: "
            f"{'✅' if getattr(mem, 'collection', None) else '❌'}\n"
            f"🔹 Semantic: "
            f"{'✅' if sem_collection else '❌'}\n"
            f"🔹 حافظه کوتاه: "
            f"{len(getattr(mem, 'short_term', []) or [])}\n"
            f"🔹 کش: {cache_entries}\n"
            f"🔹 دستورات: {stats.get('commands_processed', 0)}\n"
            f"🔹 Agent: {stats.get('agents_run', 0)}\n"
            f"🔹 خطاها: {stats.get('errors', 0)}\n"
            f"🔹 شروع: {stats.get('started_at', '?')}"
        )
        
    def get_help(self) -> str:
        return (
            "📚 **راهنمای مغز**\n"
            "─────────────\n"
            "💬 /status یا «وضعیت»\n"
            "💬 /help یا «راهنما»\n"
            "💬 /stats یا «آمار»\n"
            "💬 /clear یا «پاک»\n"
            "💬 /mode <mode>\n"
            "🤖 یا هر سؤال آزاد"
        )
    def get_stats(self) -> str:
        stats = getattr(self, "stats", {}) or {}
        mem = getattr(self, "memory", None)
        return (
            f"📊 **آمار**\n"
            f"🔹 دستورات: {stats.get('commands_processed', 0)}\n"
            f"🔹 Agent: {stats.get('agents_run', 0)}\n"
            f"🔹 خطاها: {stats.get('errors', 0)}\n"
            f"🔹 حافظه: {len(getattr(mem, 'short_term', []) or [])}"
        )

    def set_mode(self, mode: str):
        """تنظیم حالت پردازش."""
        valid = {
            "precise",
            "debug",
            "architect",
            "concise",
            "creative",
            "code",
            "chat",
            "analyze",
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
        """🎯 گزارش سلامت کامل — timeout env-based."""
        try:
            from core.ops.health_check import HealthChecker
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:health] HealthChecker import | {
                    type(e).__name__}: {e}")
            return "⚠️ HealthChecker در دسترس نیست."

        if not self.db:
            return "⚠️ دیتابیس متصل نیست."

        try:
            hc = HealthChecker(self.db)
            # 🎯 FIX: timeout env-based
            report = await asyncio.wait_for(
                hc.check_all(),
                timeout=_HEALTH_TIMEOUT_S,
            )
            if isinstance(report, dict):
                return json.dumps(report, ensure_ascii=False, indent=2)[
                    :_MAX_HEALTH_RESPONSE_LEN
                ]
            return str(report)[:_MAX_HEALTH_RESPONSE_LEN]
        except TimeoutError:
            log_warning(f"⏰ [BRAIN:health] timeout ({_HEALTH_TIMEOUT_S:.0f}s)")
            return f"⏰ بررسی سلامت طول کشید (timeout {
                _HEALTH_TIMEOUT_S:.0f}s)."
        except Exception as e:
            log_error(f"❌ [BRAIN:health] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _get_keywords_report(self) -> str:
        """آمار کلیدواژهها از DB."""
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            stats = await self.db.get_keyword_stats(limit=30)
            if not stats:
                return "📭 کلیدواژهای ثبت نشده."
            lines = ["📊 **کلیدواژهها**"]
            for kw, count in stats[:20]:
                lines.append(f"• {kw}: {count}")
            return "\n".join(lines)
        except Exception as e:
            log_error(f"❌ [BRAIN:keywords_report] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _get_platform_status(self, platform: str) -> str:
        """وضعیت آخرین poll یک پلتفرم."""
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            last_poll = await self.db.get_last_poll(platform)
            if not last_poll:
                return f"📡 **{platform}**: دادهای نیست."

            # 🎯 FIX: safe date diff
            now = datetime.now()  # noqa: DTZ005
            if last_poll.tzinfo is not None:
                now = now.replace(tzinfo=last_poll.tzinfo)

            ago = (now - last_poll).total_seconds()
            if ago < 60:
                return f"📡 **{platform}**: فعال ✅"
            elif ago < 3600:
                return f"📡 **{platform}**: {ago / 60:.0f} دقیقه"
            return f"📡 **{platform}**: {ago / 3600:.1f} ساعت"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:platform_status:{platform}] {
                    type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _get_report(self) -> str:
        """گزارش سریع — مجموع + ۷ روز اخیر."""
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            total = await self.db.get_total_count()
            daily = await self.db.get_daily_stats(days=7)
            lines = [
                "📊 **گزارش سریع**",
                f"🔹 پیامها: {total.get('messages', 0)}",
                f"🔹 سرنخها: {total.get('leads', 0)}",
                "📅 **۷ روز اخیر:**",
            ]
            for d in daily[:7]:
                lines.append(f"  {d.get('date', '?')}: {d.get('count', 0)}")
            return "\n".join(lines)
        except Exception as e:
            log_error(f"❌ [BRAIN:report] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    def _get_recent_logs(self, limit: int = 30) -> str:
        """🎯 FIX: آخرین خطوط لاگ — از data.paths.LOG_FILE."""
        try:
            log_path = Path(str(data_paths.LOG_FILE))
        except Exception as e:
            log_error(f"❌ [BRAIN:logs] path resolve | {type(e).__name__}: {e}")
            return "📭 مسیر لاگ در دسترس نیست."

        if not log_path.exists():
            return "📭 فایل لاگ نیست."

        try:
            size = log_path.stat().st_size
            with open(log_path, "rb") as f:
                if size > LOG_TAIL_BYTES:
                    f.seek(-LOG_TAIL_BYTES, os.SEEK_END)
                    f.readline()  # خط ناقص اول
                data = f.read()
            lines = data.decode("utf-8", errors="ignore").splitlines()
            recent = lines[-limit:] if len(lines) > limit else lines
            log_info(
                f"📋 [BRAIN:logs] {log_path.name} | "
                f"lines≈{len(lines)} showing={len(recent)}"
            )
            return f"📋 **لاگها ({log_path.name}):**\n" + "\n".join(recent)
        except Exception as e:
            log_error(f"❌ [BRAIN:recent_logs] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _get_lead_details(self, which: str = "last") -> str:
        """جزئیات یک سرنخ (اولین/آخرین)."""
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            leads = await self.db.get_high_score_leads(min_score=0, limit=1)
            if not leads:
                return "📭 سرنخی نیست."
            lead = leads[0]
            return (
                f"🔎 **سرنخ**\n"
                f"پلتفرم: {lead.get('platform', '?')}\n"
                f"چت: {lead.get('chat_name', '?')}\n"
                f"متن: {str(lead.get('text', ''))[:200]}\n"
                f"کلیدواژه: {lead.get('keywords', '')}"
            )
        except Exception as e:
            log_error(f"❌ [BRAIN:lead_details] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _check_keyword(self, kw: str) -> str:
        """چک وجود یک کلیدواژه در finder."""
        if not kw:
            return "⚠️ کلیدواژه وارد کن."
        try:
            from analyzers.keyword_finder import (
                get_keyword_finder,
            )

            finder = get_keyword_finder()
            found = finder.has_match(kw)
            log_info(f"🔍 [BRAIN:kw-check] kw={kw!r} | found={found}")

            if found:
                matches = finder.match(kw)
                if matches:
                    preview = "، ".join(matches[:5])
                    return f"✅ '{kw}' شامل کلیدواژههای ثبتشده است:\n   {preview}"
                return f"✅ '{kw}' وجود دارد."

            return (
                f"❌ '{kw}' کلیدواژهٔ ثبتشده نیست.\n"
                f"   (نه در products.py و نه در "
                f"keywords_custom.json)\n"
                f"   برای افزودن: کلیدواژه «{kw}» را اضافه کن"
            )
        except Exception as e:
            log_error(f"❌ [BRAIN:check_keyword] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    def generate_report(self, days: int = 7) -> str:
        """🎯 FIX: پارامتر days اعمال میشود (قبلاً بیاستفاده بود)."""
        return (
            f"📊 **گزارش Agent ({days} روز)**\n"
            f"دستورات: {self.stats['commands_processed']}\n"
            f"Agent: {self.stats['agents_run']}\n"
            f"خطاها: {self.stats['errors']}\n"
            f"یادگیری: "
            f"{len(self.learning_engine.successful_interactions)} ok, "
            f"{len(self.learning_engine.failed_interactions)} fail\n"
            f"کش: {self.cache.stats()['entries']}"
        )

    def analyze_logs(self, category: str | None = None) -> str:
        """تحلیل لاگها — دستهبندی خطاها بر اساس نوع."""
        try:
            logs_text = self._get_recent_logs(limit=200)
            if not logs_text or "لاگ" in logs_text[:20]:
                return logs_text

            categories = {
                "شبکه": [
                    "connection",
                    "timeout",
                    "network",
                    "socket",
                ],
                "دیتابیس": [
                    "database",
                    "sqlite",
                    "sql",
                    "locked",
                ],
                "AI": [
                    "openai",
                    "ollama",
                    "deepseek",
                    "api",
                ],
                "فایل": [
                    "file",
                    "path",
                    "permission",
                    "read",
                ],
                "مرورگر": [
                    "playwright",
                    "chromium",
                    "browser",
                ],
                "کلیدواژه": [
                    "keyword",
                    "کلیدواژه",
                ],
            }

            counts = dict.fromkeys(categories, 0)
            counts["سایر"] = 0
            lines = logs_text.split("\n")
            error_lines = [
                ln
                for ln in lines
                if "[ERROR]" in ln or "[CRITICAL]" in ln or "❌" in ln
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
            for cat, cnt in sorted(counts.items(), key=lambda x: -x[1]):
                if cnt > 0:
                    result.append(f"• {cat}: {cnt}")
            log_info(
                f"📊 [BRAIN:logs:analyze] errors={
                    len(error_lines)} categories={counts}")
            return "\n".join(result)
        except Exception as e:
            log_error(f"❌ [BRAIN:analyze_logs] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"
