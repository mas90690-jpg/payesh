# core/manual/_brain_settings.py
"""
مدیریت تنظیمات + پلتفرم‌ها.

این ماژول:
  - handle_settings_command (parse)
  - show_platform_status, show_one_platform
  - toggle_platform, change_poll
  - set_setting (نوشتن در .env با fsync)

نسخهٔ ۸ (این نسخه):
  - 🎯 استفاده از config.PLATFORMS_ORDER (شامل instagram).
  - 🐛 FIX: `_set_setting` حالا fsync می‌زند.
  - ✅ حفظ همهٔ رفتار قبلی.
"""

from __future__ import annotations

import os
import re

from config import PLATFORMS_ORDER
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.manual._brain_core import PLATFORM_FA, ROOT


# ═══════════════════════════════════════════════════════════
# BrainSettingsMixin
# ═══════════════════════════════════════════════════════════
class BrainSettingsMixin:
    """Mixin تنظیمات + پلتفرم‌ها."""

    def _handle_settings_command(self, text: str) -> str | None:
        """مدیریت تنظیمات و پلتفرم‌ها — بدون AI."""
        low = self._normalize(text)

        if any(
            w in low
            for w in [
                "وضعیت پلتفرم",
                "پلتفرم‌ها",
                "همه پلتفرم",
            ]
        ):
            return self._show_platform_status()

        _AMBIGUOUS = {"بله", "وب", "سایت", "web"}

        for p_fa, p_code in PLATFORM_FA.items():
            if p_fa in _AMBIGUOUS:
                pattern = (
                    r"(?:پلتفرم|سرویس|بات)\s+" +
                    re.escape(p_fa) +
                    r"([\s،.:؛!?؟]|$)")
            else:
                pattern = r"(^|[\s،.:؛!?؟])" + \
                    re.escape(p_fa) + r"([\s،.:؛!?؟]|$)"
            if not re.search(pattern, low):
                continue
            if any(
                w in low
                for w in [
                    "خاموش",
                    "ببند",
                    "غیرفعال",
                    "disable",
                    "off",
                ]
            ):
                return self._toggle_platform(p_code, False)
            if any(
                w in low
                for w in [
                    "روشن",
                    "فعال",
                    "باز کن",
                    "enable",
                    "on",
                ]
            ):
                return self._toggle_platform(p_code, True)
            m = re.search(r"(\d+)\s*ثانیه", low)
            if m and any(
                w in low
                for w in [
                    "poll",
                    "پول",
                    "فاصله",
                    "زمان",
                ]
            ):
                return self._change_poll(p_code, int(m.group(1)))
            if any(
                w in low
                for w in [
                    "وضعیت",
                    "کار",
                    "وصل",
                    "فعال",
                ]
            ):
                return self._show_one_platform(p_code)

        if "تنظیم" in low or "ست کن" in low or "بذار" in low:
            m = re.search(r"([A-Z_]{3,})\s*[=:]\s*(\S+)", text)
            if m:
                return self._set_setting(m.group(1), m.group(2))

        if "دسترسی" in low or "access" in low:
            if self._access_available:
                return self._access_mgr.get_summary()
            return "⚠️ access_manager در دسترس نیست."

        return None

    def _show_platform_status(self) -> str:
        """🎯 FIX: از config.PLATFORMS_ORDER (شامل instagram)."""
        try:
            from config import PLUGINS_CONFIG

            lines = ["📊 وضعیت پلتفرم‌ها:"]
            for p in PLATFORMS_ORDER:
                cfg = PLUGINS_CONFIG.get(f"{p}collector", {})
                enabled = cfg.get("enabled", False)
                poll = cfg.get("poll_seconds", "-")
                mark = "✅ روشن" if enabled else "❌ خاموش"
                lines.append(f"   • {p}: {mark} | poll: {poll}s")
            return "\n".join(lines)
        except Exception as e:
            log_warning(f"⚠️ [MBRAIN:settings:show] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    def _show_one_platform(self, p_code: str) -> str:
        try:
            from config import PLUGINS_CONFIG

            cfg = PLUGINS_CONFIG.get(f"{p_code}collector", {})
            enabled = cfg.get("enabled", False)
            poll = cfg.get("poll_seconds", "-")
            mark = "✅ روشن" if enabled else "❌ خاموش"
            return f"📱 {p_code}: {mark} | poll: {poll}s"
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:settings:one] {p_code} | {
                    type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    def _toggle_platform(self, p_code: str, enabled: bool) -> str:
        if self._access_available:
            ok, reason = self._access_mgr.can_toggle_platform(p_code)
            if not ok:
                log_warning(f"⛔ [MBRAIN:platform] {p_code} | {reason}")
                return f"⛔ {reason}"
        return self._set_setting(
            f"{p_code.upper()}_ENABLED",
            "true" if enabled else "false",
        )

    def _change_poll(self, p_code: str, seconds: int) -> str:
        if seconds < 10 or seconds > 86400:
            return "⛔ مقدار باید بین ۱۰ تا ۸۶۴۰۰ ثانیه باشه."
        if self._access_available:
            ok, reason = self._access_mgr.can_toggle_platform(p_code)
            if not ok:
                return f"⛔ {reason}"
        return self._set_setting(
            f"{p_code.upper()}_POLL_SECONDS", str(seconds))

    def _set_setting(self, key: str, value: str) -> str:
        """🎯 FIX: نوشتن در .env با fsync."""
        key_u = key.upper()
        if self._access_available:
            ok, reason = self._access_mgr.can_write_setting(key_u)
            if not ok:
                log_warning(f"⛔ [MBRAIN:setting] {key_u} | {reason}")
                return f"⛔ {reason}"

        try:
            env_path = ROOT / ".env"
            lines: list[str] = []
            if env_path.exists():
                with open(str(env_path), encoding="utf-8") as f:
                    lines = f.readlines()

            found = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith(f"{key_u}="):
                    lines[i] = f"{key_u}={value}\n"
                    found = True
                    break
            if not found:
                lines.append(f"{key_u}={value}\n")

            tmp = env_path.with_suffix(".tmp")
            # 🎯 FIX: fsync
            with open(str(tmp), "w", encoding="utf-8") as f:
                f.writelines(lines)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass
            tmp.replace(env_path)

            # fsync دایرکتوری (POSIX)
            try:
                parent = str(env_path.parent)
                dir_fd = os.open(parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass

            log_success(
                f"💾 [MBRAIN:setting] {key_u}={value} | {
                    'به‌روز' if found else 'جدید'}")
            return f"✅ {key_u}={value} ذخیره شد.\n(برای اعمال، ری‌استارت کن.)"
        except Exception as e:
            log_error(f"❌ [MBRAIN:setting] {key_u} | {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"
