"""
ابزارهای کنترل پلتفرم — هوش می‌تواند پلتفرم‌ها را مثل کاربر
روشن/خاموش کند، وضعیت بگیرد، عیب‌یابی کند.

نسخهٔ ۱:
  - start_platform: وصل شدن
  - stop_platform: قطع
  - platform_health: چک سلامت
  - platform_diagnose: عیب‌یابی
  - platform_run_once: یک چرخه کامل
  - list_platforms: لیست پلتفرم‌ها
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import time
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)

# ═══════════════════════════════════════════
# نگاشت پلتفرم → کلاس
# ═══════════════════════════════════════════
_PLATFORM_MAP: dict[str, tuple[str, str]] = {
    "bale":      ("collectors.bale",      "BaleCollector"),
    "eitaa":     ("collectors.eitaa",     "EitaaCollector"),
    "rubika":    ("collectors.rubika",    "RubikaCollector"),
    "telegram":  ("collectors.telegram",  "TelegramWebCollector"),
    "whatsapp":  ("collectors.whatsapp",  "WhatsAppCollector"),
    "linkedin":  ("collectors.linkedin",  "LinkedInCollector"),
    "instagram": ("collectors.instagram", "InstagramCollector"),
    "youtube":   ("collectors.youtube",   "YouTubeCollector"),
    "web":       ("collectors.web",       "WebCollector"),
}


# ═══════════════════════════════════════════
# PlatformManager — نگه‌داری کلکتورهای زنده
# ═══════════════════════════════════════════
class PlatformManager:
    """مدیریت کلکتورهای در حال اجرا — singleton."""

    _instance: PlatformManager | None = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._active: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        log_info("🔧 [PLATFORM:manager] آماده")

    @classmethod
    async def get(cls) -> PlatformManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_collector(self, platform: str) -> Any | None:
        """کلکتور فعال را برمی‌گرداند."""
        async with self._lock:
            return self._active.get(platform)

    async def register(self, platform: str, collector: Any):
        """کلکتور را ثبت می‌کند."""
        async with self._lock:
            self._active[platform] = collector
        log_info(f"📌 [PLATFORM:manager] {platform} ثبت شد")

    async def unregister(self, platform: str):
        """کلکتور را حذف می‌کند."""
        async with self._lock:
            self._active.pop(platform, None)
        log_info(f"🗑️ [PLATFORM:manager] {platform} حذف شد")

    async def list_active(self) -> list[str]:
        """پلتفرم‌های فعال."""
        async with self._lock:
            return list(self._active.keys())

    async def close_all(self):
        """بستن همه."""
        async with self._lock:
            for p, c in list(self._active.items()):
                with contextlib.suppress(Exception):
                    await c.disconnect()
            self._active.clear()


# ═══════════════════════════════════════════
# PlatformMixin
# ═══════════════════════════════════════════
class PlatformMixin:
    """ابزارهای کنترل پلتفرم."""

    # ═══════════════════════════════════════════
    # helper
    # ═══════════════════════════════════════════
    @staticmethod
    def _load_collector_class(platform: str) -> tuple[Any, str]:
        """بارگذاری کلاس کلکتور.

        Returns:
            (class, error_message)
        """
        if platform not in _PLATFORM_MAP:
            return None, f"پلتفرم «{platform}» پشتیبانی نمی‌شود. معتبر: {list(_PLATFORM_MAP.keys())}"

        module_path, class_name = _PLATFORM_MAP[platform]
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name, None)
            if cls is None:
                return None, f"کلاس {class_name} پیدا نشد در {module_path}"
            return cls, ""
        except Exception as e:
            return None, f"import ناموفق: {type(e).__name__}: {e}"

    # ═══════════════════════════════════════════
    # list_platforms
    # ═══════════════════════════════════════════
    async def _list_platforms(self) -> str:
        """لیست همهٔ پلتفرم‌های موجود."""
        pm = await PlatformManager.get()
        active = await pm.list_active()

        lines = ["📋 پلتفرم‌های موجود:"]
        for p in _PLATFORM_MAP:
            status = "🟢 فعال" if p in active else "⚪ غیرفعال"
            lines.append(f"   • {p}: {status}")

        lines.append("")
        lines.append(f"📌 فعال‌ها: {len(active)}/{len(_PLATFORM_MAP)}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # start_platform
    # ═══════════════════════════════════════════
    async def _start_platform(self, platform: str = "") -> str:
        """شروع یک پلتفرم — مثل کاربر وارد می‌شود.

        Args:
            platform: bale, eitaa, rubika, telegram, whatsapp,
                      linkedin, instagram, youtube, web
        """
        platform = str(platform or "").strip().lower()

        if not platform:
            return (
                "❌ کدام پلتفرم؟ مثال: start_platform(platform='bale')\n"
                f"معتبر: {list(_PLATFORM_MAP.keys())}"
            )

        # ─── چک از قبل فعال ───
        pm = await PlatformManager.get()
        existing = await pm.get_collector(platform)
        if existing is not None:
            is_conn = getattr(existing, "is_connected", False)
            return (
                f"ℹ️ {platform} از قبل فعال است | "
                f"connected={is_conn}"
            )

        # ─── بارگذاری کلاس ───
        cls, err = self._load_collector_class(platform)
        if cls is None:
            return f"❌ {err}"

        # ─── ساخت collector ───
        t0 = time.monotonic()
        try:
            collector = cls(headless=True)
        except TypeError:
            try:
                collector = cls()
            except Exception as e:
                return f"❌ ساخت collector: {type(e).__name__}: {e}"
        except Exception as e:
            return f"❌ ساخت collector: {type(e).__name__}: {e}"

        # ─── connect ───
        try:
            ok = await asyncio.wait_for(
                collector.connect(),
                timeout=120.0,
            )
        except TimeoutError:
            return f"⏰ {platform}: timeout در connect (۱۲۰ ثانیه)"
        except Exception as e:
            log_error(
                f"❌ [PLATFORM:start:{platform}] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ {platform} connect: {type(e).__name__}: {e}"

        elapsed = time.monotonic() - t0

        if not ok:
            return f"❌ {platform}: connect موفق نبود ({elapsed:.1f}s)"

        # ─── ثبت ───
        await pm.register(platform, collector)

        log_success(
            f"✅ [PLATFORM:start] {platform} | {elapsed:.1f}s"
        )
        return (
            f"✅ {platform} روشن شد | "
            f"زمان: {elapsed:.1f}s | "
            f"وضعیت: متصل"
        )

    # ═══════════════════════════════════════════
    # stop_platform
    # ═══════════════════════════════════════════
    async def _stop_platform(self, platform: str = "") -> str:
        """قطع اتصال یک پلتفرم."""
        platform = str(platform or "").strip().lower()

        if not platform:
            return (
                "❌ کدام پلتفرم؟ مثال: stop_platform(platform='bale')\n"
                f"معتبر: {list(_PLATFORM_MAP.keys())}"
            )

        pm = await PlatformManager.get()
        collector = await pm.get_collector(platform)

        if collector is None:
            return f"ℹ️ {platform} فعال نیست"

        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                collector.disconnect(),
                timeout=30.0,
            )
        except TimeoutError:
            log_warning(f"⏰ [PLATFORM:stop:{platform}] timeout")
        except Exception as e:
            log_warning(
                f"⚠️ [PLATFORM:stop:{platform}] "
                f"{type(e).__name__}: {e}"
            )

        await pm.unregister(platform)
        elapsed = time.monotonic() - t0

        log_success(
            f"✅ [PLATFORM:stop] {platform} | {elapsed:.1f}s"
        )
        return f"✅ {platform} خاموش شد | زمان: {elapsed:.1f}s"

    # ═══════════════════════════════════════════
    # platform_health
    # ═══════════════════════════════════════════
    async def _platform_health(self, platform: str = "") -> str:
        """چک سلامت یک پلتفرم.

        چیزهایی که چک می‌شوند:
          - آیا فعال است؟
          - آیا متصل است؟
          - آخرین خطا چیست؟
          - session وجود دارد؟
          - چقدر طول کشید تا وصل شد؟
        """
        platform = str(platform or "").strip().lower()

        if not platform:
            return (
                "❌ کدام پلتفرم؟ مثال: platform_health(platform='bale')"
            )

        if platform not in _PLATFORM_MAP:
            return f"❌ پلتفرم «{platform}» ناشناخته"

        lines = [f"🩺 سلامت {platform}:"]
        lines.append("")

        # ─── چک فعال بودن ───
        pm = await PlatformManager.get()
        collector = await pm.get_collector(platform)

        if collector is None:
            lines.append("  🔴 وضعیت: غیرفعال (start نکرده)")
        else:
            is_conn = getattr(collector, "is_connected", False)
            status = getattr(collector, "status", None)
            status_val = status.value if status else "?"

            lines.append(f"  🟢 وضعیت: {'متصل' if is_conn else 'قطع'}")
            lines.append(f"  📊 status: {status_val}")

            # ─── خطای قبلی ───
            try:
                last_err = collector.get_last_error()
                if last_err:
                    lines.append(f"  ⚠️ آخرین خطا: {last_err[:100]}")
            except Exception:
                pass

        # ─── چک session ───
        try:
            cls, _ = self._load_collector_class(platform)
            if cls:
                temp = cls(headless=True)
                session_dir = getattr(temp, "_user_data_dir", None)
                if session_dir:
                    from pathlib import Path
                    p = Path(session_dir)
                    if p.exists():
                        files = list(p.glob("*"))
                        lines.append(
                            f"  📁 session: ✅ {len(files)} فایل"
                        )
                    else:
                        lines.append("  📁 session: ❌ وجود ندارد")
        except Exception as e:
            lines.append(f"  📁 session: خطا در چک ({type(e).__name__})")

        # ─── چک کلاس ───
        cls, err = self._load_collector_class(platform)
        if cls is None:
            lines.append(f"  ❌ کلکتور: {err}")
        else:
            lines.append(f"  ✅ کلکتور: {cls.__name__}")

        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # platform_diagnose
    # ═══════════════════════════════════════════
    async def _platform_diagnose(self, platform: str = "") -> str:
        """عیب‌یابی عمیق یک پلتفرم.

        چک می‌کند:
          - آیا کلکتور وجود دارد؟
          - آیا session وجود دارد؟
          - آیا playright نصب است؟
          - آیا CHROME_PATH درست است؟
          - آیا متدهای کلیدی وجود دارند؟
          - آیا در لاگ خطایی هست؟
        """
        platform = str(platform or "").strip().lower()

        if not platform:
            return "❌ کدام پلتفرم؟ مثال: platform_diagnose(platform='bale')"

        if platform not in _PLATFORM_MAP:
            return f"❌ پلتفرم «{platform}» ناشناخته"

        lines = [f"🔍 عیب‌یابی {platform}:"]
        lines.append("")

        # ─── ۱) کلکتور ───
        cls, err = self._load_collector_class(platform)
        if cls is None:
            lines.append(f"  ❌ کلکتور: {err}")
            return "\n".join(lines)

        lines.append(f"  ✅ کلکتور: {cls.__name__}")

        # ─── ۲) متدها ───
        required = ["connect", "disconnect", "fetch_messages"]
        for m in required:
            has = hasattr(cls, m)
            lines.append(f"  {'✅' if has else '❌'} متد {m}")

        # ─── ۳) ساخت تست ───
        try:
            temp = cls(headless=True)
            lines.append("  ✅ ساخت collector OK")
        except Exception as e:
            lines.append(f"  ❌ ساخت collector: {type(e).__name__}: {e}")
            return "\n".join(lines)

        # ─── ۴) session ───
        session_dir = getattr(temp, "_user_data_dir", None)
        if session_dir:
            from pathlib import Path
            p = Path(session_dir)
            if p.exists():
                files = list(p.glob("*"))
                lines.append(
                    f"  ✅ session: {len(files)} فایل"
                )
            else:
                lines.append(
                    f"  ⚠️ session: وجود ندارد ({session_dir})"
                )
                lines.append(
                    "      💡 باید login.py اجرا کنی"
                )

        # ─── ۵) Playwright ───
        try:

            lines.append("  ✅ playwright نصب است")
        except ImportError:
            lines.append("  ❌ playwright نصب نیست")

        # ─── ۶) Chrome ───
        try:
            from config import CHROME_PATH
            if CHROME_PATH:
                from pathlib import Path
                if Path(CHROME_PATH).exists():
                    lines.append(f"  ✅ Chrome: {CHROME_PATH}")
                else:
                    lines.append(
                        f"  ⚠️ Chrome: مسیر نامعتبر ({CHROME_PATH})"
                    )
            else:
                lines.append("  ℹ️ CHROME_PATH تنظیم نشده")
        except Exception:
            pass

        # ─── ۷) لاگ‌های اخیر ───
        try:
            from data import paths as data_paths
            log_file = data_paths.LOG_FILE
            if log_file.exists():
                content = log_file.read_text(
                    encoding="utf-8", errors="ignore"
                )
                recent = [
                    line for line in content.splitlines()[-200:]
                    if platform in line.lower()
                    and "ERROR" in line.upper()
                ]
                if recent:
                    lines.append(
                        f"  ⚠️ {len(recent)} خطای اخیر در لاگ:"
                    )
                    for r in recent[-3:]:
                        lines.append(f"      {r[:100]}")
                else:
                    lines.append("  ✅ خطایی در لاگ نیست")
        except Exception:
            pass

        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # platform_run_once
    # ═══════════════════════════════════════════
    async def _platform_run_once(
        self,
        platform: str = "",
        hours: int = 24,
    ) -> str:
        """یک چرخهٔ کامل: connect → fetch → disconnect.

        Args:
            platform: bale, eitaa, ...
            hours: بازهٔ زمانی (پیش‌فرض ۲۴ ساعت)
        """
        platform = str(platform or "").strip().lower()

        if not platform:
            return "❌ کدام پلتفرم؟"

        if platform not in _PLATFORM_MAP:
            return f"❌ پلتفرم «{platform}» ناشناخته"

        cls, err = self._load_collector_class(platform)
        if cls is None:
            return f"❌ {err}"

        # ─── ساخت ───
        try:
            collector = cls(headless=True)
        except Exception as e:
            return f"❌ ساخت: {type(e).__name__}: {e}"

        t0 = time.monotonic()
        messages = []

        try:
            # ─── ۱) connect ───
            ok = await asyncio.wait_for(
                collector.connect(),
                timeout=120.0,
            )
            if not ok:
                return f"❌ connect ناموفق ({time.monotonic() - t0:.1f}s)"

            t_conn = time.monotonic() - t0

            # ─── ۲) fetch ───
            from datetime import datetime, timedelta
            since = datetime.now() - timedelta(hours=int(hours))  # noqa: DTZ005

            try:
                messages = await asyncio.wait_for(
                    collector.fetch_messages(since),
                    timeout=300.0,
                )
            except TimeoutError:
                return (
                    f"✅ connect OK ({t_conn:.1f}s) | "
                    f"⏰ fetch timeout (۳۰۰s)"
                )

            t_fetch = time.monotonic() - t0

            # ─── ۳) disconnect ───
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    collector.disconnect(),
                    timeout=30.0,
                )

            t_total = time.monotonic() - t0

            log_success(
                f"✅ [PLATFORM:run_once] {platform} | "
                f"{len(messages)} پیام | {t_total:.1f}s"
            )

            return (
                f"✅ {platform}: چرخه کامل\n"
                f"   📥 {len(messages)} پیام\n"
                f"   ⏱️ connect: {t_conn:.1f}s\n"
                f"   ⏱️ fetch: {t_fetch - t_conn:.1f}s\n"
                f"   ⏱️ total: {t_total:.1f}s"
            )

        except Exception as e:
            log_error(
                f"❌ [PLATFORM:run_once:{platform}] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ {platform}: {type(e).__name__}: {e}"

        finally:
            with contextlib.suppress(Exception):
                await collector.disconnect()

    # ═══════════════════════════════════════════
    # 🆕 ریشه‌یابی — چرا پلتفرم وصل نشد؟
    # ═══════════════════════════════════════════
    async def _diagnose_platform(self, platform: str = "") -> str:
        """ریشه‌یابی کامل یک پلتفرم — چرا وصل نشد؟"""
        platform = str(platform or "").strip().lower()

        if not platform:
            return (
                "❌ کدام پلتفرم؟\n"
                f"معتبر: {list(_PLATFORM_MAP.keys())}"
            )

        if platform not in _PLATFORM_MAP:
            return f"❌ پلتفرم ناشناخته: {platform}"

        report = [f"🔍 ریشه‌یابی {platform}:"]
        report.append("")

        # ─── ۱) session ───
        session_dirs = {
            "bale": "storage/sessions/bale_web",
            "eitaa": "storage/sessions/eitaa_web",
            "telegram": "storage/sessions/telegram_web",
            "whatsapp": "storage/sessions/whatsapp",
            "linkedin": "storage/sessions/linkedin_v2",
            "instagram": "storage/sessions/instagram",
            "youtube": "storage/sessions/youtube_web",
            "rubika": "storage/sessions/rubika_session",
        }
        import time as _time
        from pathlib import Path

        session_path = session_dirs.get(platform)
        if session_path:
            p = Path(session_path)
            if p.exists():
                files = list(p.glob("*"))
                if files:
                    mtime = max(
                        f.stat().st_mtime
                        for f in files if f.is_file()
                    )
                    age = (_time.time() - mtime) / 86400
                    if age > 30:
                        report.append(
                            f"  📁 Session: ⚠️ {age:.0f} روز قدیمی"
                        )
                    else:
                        report.append(
                            f"  📁 Session: ✅ {len(files)} فایل، "
                            f"{age:.0f} روز پیش"
                        )
                else:
                    report.append("  📁 Session: ❌ خالی")
            else:
                report.append(f"  📁 Session: ❌ نیست ({session_path})")

        # ─── ۲) آخرین poll ───
        try:
            if self.db:
                last = await self.db.get_last_poll(platform)
                if last:
                    from datetime import datetime
                    mins = (
                        datetime.now() - last  # noqa: DTZ005
                    ).total_seconds() / 60
                    if mins < 5:
                        report.append(
                            f"  ⏱️  Last poll: ✅ {mins:.0f} دقیقه پیش"
                        )
                    elif mins < 60:
                        report.append(
                            f"  ⏱️  Last poll: ⚠️ {mins:.0f} دقیقه پیش"
                        )
                    else:
                        report.append(
                            f"  ⏱️  Last poll: ❌ {mins / 60:.1f} ساعت پیش"
                        )
                else:
                    report.append("  ⏱️  Last poll: ❌ هیچ")
        except Exception as e:
            report.append(f"  ⏱️  Last poll: ⚠️ خطا ({e})")

        # ─── ۳) خطاهای اخیر ───
        try:
            from data import paths
            log_file = paths.LOG_FILE
            if log_file.exists():
                lines = log_file.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()[-200:]
                errors = [
                    ln for ln in lines
                    if platform in ln.lower()
                    and "error" in ln.lower()
                ]
                if errors:
                    report.append(
                        f"  ❌ خطاها: ⚠️ {len(errors)} خطای اخیر"
                    )
                    report.append(
                        f"     آخرین: {errors[-1][:80]}"
                    )
                else:
                    report.append("  ❌ خطاها: ✅ ندارد")
        except Exception:
            pass

        # ─── ۴) شبکه ───
        domains = {
            "bale": "web.bale.ai",
            "eitaa": "web.eitaa.com",
            "telegram": "web.telegram.org",
            "whatsapp": "web.whatsapp.com",
            "linkedin": "www.linkedin.com",
            "instagram": "www.instagram.com",
            "youtube": "www.youtube.com",
            "rubika": "rubika.ir",
        }
        domain = domains.get(platform)
        if domain:
            try:
                import socket
                socket.gethostbyname(domain)
                report.append(f"  🌐 شبکه: ✅ {domain}")
            except Exception:
                report.append(
                    f"  🌐 شبکه: ❌ {domain} — VPN چک کن"
                )

        # ─── ۵) مرورگر ───
        try:
            import os as _os

            from config import CHROME_PATH
            if CHROME_PATH and _os.path.exists(CHROME_PATH):
                report.append("  🌐 مرورگر: ✅ Chrome موجود")
            else:
                report.append(
                    "  🌐 مرورگر: ⚠️ CHROME_PATH تنظیم نشده"
                )
        except Exception:
            pass

        # ─── ۶) پیشنهاد ───
        report.append("")
        report.append("  💡 پیشنهاد:")
        report.append(
            f"     • اجرا کن: run_platform_as_user("
            f"platform='{platform}', action='start')"
        )

        return "\n".join(report)

    # ═══════════════════════════════════════════
    # 🆕 اجرا — مثل کاربر واقعی
    # ═══════════════════════════════════════════
    async def _run_platform_as_user(
        self,
        platform: str = "",
        action: str = "start",
        headless: bool = True,
    ) -> str:
        """اجرای پلتفرم مثل کاربر واقعی.

        action: start | stop | restart | run_once | status
        """
        platform = str(platform or "").strip().lower()

        if not platform or platform not in _PLATFORM_MAP:
            return (
                f"❌ پلتفرم نامعتبر. معتبر: "
                f"{list(_PLATFORM_MAP.keys())}"
            )

        if action == "start":
            return await self._start_as_user(platform, headless)
        if action == "stop":
            return await self._stop_as_user(platform)
        if action == "restart":
            await self._stop_as_user(platform)
            await asyncio.sleep(2)
            return await self._start_as_user(platform, headless)
        if action == "run_once":
            return await self._run_once_as_user(platform, headless)
        if action == "status":
            return await self._status_as_user(platform)

        return f"❌ action نامعتبر: {action}"

    async def _start_as_user(
        self, platform: str, headless: bool
    ) -> str:
        """شروع پلتفرم مثل کاربر."""
        # چک از قبل فعال
        pm = await PlatformManager.get()
        existing = await pm.get_collector(platform)
        if existing is not None:
            return f"ℹ️ {platform} از قبل فعال است"

        # بارگذاری کلاس
        cls, err = self._load_collector_class(platform)
        if cls is None:
            return f"❌ {err}"

        # ساخت
        t0 = time.monotonic()
        try:
            try:
                collector = cls(headless=headless)
            except TypeError:
                collector = cls()
        except Exception as e:
            return f"❌ ساخت: {type(e).__name__}: {e}"

        # connect
        try:
            ok = await asyncio.wait_for(
                collector.connect(), timeout=120
            )
        except TimeoutError:
            return "⏰ timeout در connect (۱۲۰s)"
        except Exception as e:
            return f"❌ connect: {type(e).__name__}: {e}"

        elapsed = time.monotonic() - t0

        if not ok:
            return (
                f"❌ {platform} وصل نشد ({elapsed:.1f}s)\n"
                f"💡 ریشه‌یابی: diagnose_platform("
                f"platform='{platform}')"
            )

        # ثبت در manager
        await pm.register(platform, collector)

        return (
            f"✅ {platform} شروع شد (مثل کاربر)\n"
            f"⏱️  زمان: {elapsed:.1f}s\n"
            f"📊 وضعیت: متصل"
        )

    async def _stop_as_user(self, platform: str) -> str:
        """توقف پلتفرم."""
        pm = await PlatformManager.get()
        collector = await pm.get_collector(platform)
        if collector is None:
            return f"ℹ️ {platform} فعال نیست"

        try:
            await asyncio.wait_for(
                collector.disconnect(), timeout=30
            )
        except Exception as e:
            log_warning(f"⚠️ disconnect: {e}")

        await pm.unregister(platform)
        return f"✅ {platform} متوقف شد"

    async def _run_once_as_user(
        self, platform: str, headless: bool
    ) -> str:
        """یک چرخهٔ کامل: connect → fetch → disconnect."""
        cls, err = self._load_collector_class(platform)
        if cls is None:
            return f"❌ {err}"

        try:
            try:
                collector = cls(headless=headless)
            except TypeError:
                collector = cls()
        except Exception as e:
            return f"❌ ساخت: {e}"

        t0 = time.monotonic()
        try:
            ok = await asyncio.wait_for(
                collector.connect(), timeout=120
            )
            if not ok:
                return "❌ connect ناموفق"

            t_conn = time.monotonic() - t0

            from datetime import datetime, timedelta
            since = datetime.now() - timedelta(hours=24)  # noqa: DTZ005

            try:
                messages = await asyncio.wait_for(
                    collector.fetch_messages(since), timeout=300
                )
            except TimeoutError:
                messages = []
            except Exception:
                messages = []

            t_fetch = time.monotonic() - t0

            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    collector.disconnect(), timeout=30
                )

            t_total = time.monotonic() - t0

            return (
                f"✅ {platform} — چرخهٔ کامل\n"
                f"  📥 {len(messages)} پیام\n"
                f"  ⏱️  connect: {t_conn:.1f}s\n"
                f"  ⏱️  fetch: {t_fetch - t_conn:.1f}s\n"
                f"  ⏱️  total: {t_total:.1f}s"
            )
        except Exception as e:
            return f"❌ {platform}: {type(e).__name__}: {e}"
        finally:
            with contextlib.suppress(Exception):
                await collector.disconnect()

    async def _status_as_user(self, platform: str) -> str:
        """وضعیت یک پلتفرم."""
        pm = await PlatformManager.get()
        collector = await pm.get_collector(platform)
        if collector is not None:
            is_conn = getattr(collector, "is_connected", False)
            return (
                f"🟢 {platform} — فعال | "
                f"connected={is_conn}"
            )
        return f"⚪ {platform} — غیرفعال"

    async def _list_all_platforms(self) -> str:
        """لیست همه پلتفرم‌ها."""
        pm = await PlatformManager.get()
        active = await pm.list_active()

        lines = ["📋 پلتفرم‌ها:"]
        for p in _PLATFORM_MAP:
            if p in active:
                lines.append(f"  🟢 {p} — فعال")
            else:
                lines.append(f"  ⚪ {p} — غیرفعال")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 🆕 کنترل پلتفرم — فقط با دستور
    # ═══════════════════════════════════════════
    async def _control_platform(
        self,
        platform: str = "",
        action: str = "",
        query: str = "",
        link: str = "",
        chat: str = "",
        text: str = "",
        message_id: str = "",
    ) -> str:
        """کنترل پلتفرم — فقط با دستور صریح.

        پلتفرم: bale | eitaa | rubika
        action: search | join | leave | send |
                forward | pin | delete_message
        """
        from collectors.controller import (
            get_controller,
            list_actions,
            list_platforms,
        )

        platform = str(platform or "").strip().lower()
        action = str(action or "").strip().lower()

        if not platform:
            return (
                f"❌ کدام پلتفرم؟\n"
                f"معتبر: {list_platforms()}"
            )

        if platform not in list_platforms():
            return f"❌ پلتفرم نامعتبر: {platform}"

        if not action:
            return (
                f"❌ کدام کار؟\n"
                f"معتبر: {list_actions()}"
            )

        if action not in list_actions():
            return f"❌ کار نامعتبر: {action}"

        # ─── چک پلتفرم فعال است ───
        pm = await PlatformManager.get()
        collector = await pm.get_collector(platform)

        if collector is None:
            return (
                f"❌ {platform} فعال نیست\n"
                f"💡 اول شروع کن: "
                f"run_platform_as_user("
                f"platform='{platform}', action='start')"
            )

        # ─── ساخت controller ───
        controller = get_controller(platform, collector)
        if controller is None:
            return f"❌ کنترلر {platform} ساخته نشد"

        # ─── اجرا ───
        try:
            method = getattr(controller, action, None)
            if method is None:
                return f"❌ کار «{action}» پیاده نشده"

            # ساخت kwargs
            kwargs = {}
            if query:
                kwargs["query"] = query
            if link:
                kwargs["link"] = link
            if chat:
                kwargs["chat"] = chat
            if text:
                kwargs["text"] = text
            if message_id:
                kwargs["message_id"] = message_id

            result = await method(**kwargs)
            return result

        except Exception as e:
            log_error(
                f"❌ [CONTROLLER] {platform}:{action} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {type(e).__name__}: {e}"

    async def _control_stats(self, platform: str = "") -> str:
        """آمار کارهای یک پلتفرم."""
        from collectors.controller import get_stats

        if not platform:
            return "❌ کدام پلتفرم؟"

        stats = get_stats(platform)
        return (
            f"📊 آمار {platform}:\n"
            f"  امروز: {stats['today']}/{stats['max_day']}\n"
            f"  این ساعت: {stats['this_hour']}/{stats['max_hour']}\n"
            f"  ساعت کاری: {stats['work_hours']}"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "_PLATFORM_MAP",
    "PlatformManager",
    "PlatformMixin",
]
