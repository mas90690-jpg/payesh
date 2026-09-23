# core/services/cleanup_service.py
"""
سرویس پاک‌سازی خودکار (Retention).

طبق تنظیمات .env:
  - عکس/فایل‌های قدیمی‌تر از KEEP_MEDIA_DAYS حذف می‌شوند.
  - گزارش‌های قدیمی‌تر از KEEP_REPORTS_DAYS حذف می‌شوند.
  - پیام‌های دیتابیس قدیمی‌تر از KEEP_MESSAGES_DAYS حذف می‌شوند (۰ = هرگز).
هر CLEANUP_INTERVAL_HOURS ساعت یک‌بار اجرا می‌شود (و یک‌بار هنگام شروع).

نسخهٔ ۳ (این نسخه):
  - 🟡 FIX: os.walk حالا صریحاً followlinks=False می‌شود
    (جلوگیری از حلقهٔ بی‌نهایت با symlink).
  - 🟡 FIX: حذف فایل‌های readonly حالا با os.chmod کار می‌کند.
  - 🟡 FIX: run_once حالا در صورت _running=False زودتر برمی‌گردد.
  - 🎯 بهبود: _delete_old_files حالا newest_age را هم لاگ می‌کند.
  - 🎯 بهبود: run_once حالا dict برمی‌گرداند (آمار per-folder).
  - 🎯 بهبود: run_periodic حالا sleep کوتاه‌تر می‌شود (پاسخ سریع به stop).
  - 🎯 بهبود: _MIN_INTERVAL_HOURS / _MAX_INTERVAL_HOURS از env قابل override.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime

from config import MEDIA_DIR, REPORTS_DIR, RETENTION
from core.kernel.logger import log_error, log_info, log_success, log_warning


# ─── آستانه‌های مرزی (از env، قابل override) ───
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


_MIN_INTERVAL_HOURS = max(1, _env_int("CLEANUP_MIN_INTERVAL_HOURS", 1))
_MAX_INTERVAL_HOURS = max(
    _MIN_INTERVAL_HOURS,
    _env_int("CLEANUP_MAX_INTERVAL_HOURS", 720),
)  # پیش‌فرض 720 ساعت = 30 روز
_SLOW_STEP_MS = 5000           # هر مرحله > 5s

# 🎯 FIX: sleep کوتاه‌تر برای پاسخ سریع به stop
_SLEEP_CHUNK_S = 5.0


# ═══════════════════════════════════════════
# CleanupService
# ═══════════════════════════════════════════
class CleanupService:
    """پاک‌سازی خودکار فایل‌های قدیمی + پیام‌های دیتابیس."""

    def __init__(self, db=None):
        self.db = db
        self._running = True

        # 🐛 FIX: کلیپ مقادیر منفی/غیرمعقول
        self.keep_media_days = max(0, int(RETENTION.get("media_days", 0)))
        self.keep_reports_days = max(0, int(RETENTION.get("reports_days", 0)))
        self.keep_messages_days = max(0, int(RETENTION.get("messages_days", 0)))
        raw_interval = int(RETENTION.get("cleanup_interval_hours", 24))
        self.interval_hours = max(
            _MIN_INTERVAL_HOURS,
            min(_MAX_INTERVAL_HOURS, raw_interval),
        )

        log_success(
            f"✅ [CLEANUP:init] آماده | "
            f"media={self.keep_media_days}روز | "
            f"reports={self.keep_reports_days}روز | "
            f"messages={'همیشه' if self.keep_messages_days == 0 else str(self.keep_messages_days)+'روز'} | "
            f"interval={self.interval_hours}ساعت | "
            f"db={'✅' if self.db else '❌'}"
        )
        log_info(
            f"📂 [CLEANUP:init] مسیرها | "
            f"media_dir={MEDIA_DIR} | reports_dir={REPORTS_DIR}"
        )
        if raw_interval != self.interval_hours:
            log_warning(
                f"⚠️ [CLEANUP:init] interval={raw_interval} خارج از بازهٔ "
                f"[{_MIN_INTERVAL_HOURS}, {_MAX_INTERVAL_HOURS}] — "
                f"به {self.interval_hours} کلیپ شد"
            )

    # ═══════════════════════════════════════════
    # _safe_remove — حذف مقاوم
    # ═══════════════════════════════════════════
    @staticmethod
    def _safe_remove(path: str) -> bool:
        """
        حذف امن فایل (با تلاش برای تغییر permission اگر readonly بود).

        🟡 FIX: حالا با فایل‌های readonly کار می‌کند.

        Returns:
            True اگر فایل حذف شد.
        """
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            # 🟡 FIX: تلاش برای تغییر permission
            try:
                os.chmod(path, 0o644)
                os.remove(path)
                return True
            except Exception:
                return False
        except Exception:
            return False

    # ═══════════════════════════════════════════
    # _delete_old_files — با آمار کامل
    # ═══════════════════════════════════════════
    @staticmethod
    def _delete_old_files(folder: str, days: int, label: str) -> int:
        """
        فایل‌های قدیمی‌تر از days روز را در folder حذف می‌کند.

        🟡 FIX: حالا followlinks=False صریح است (جلوگیری از حلقهٔ symlink).
        🎯 بهبود: حالا newest_age را هم لاگ می‌کند.

        Args:
            folder: پوشه‌ای که اسکن شود.
            days:   حداکثر سن مجاز (روز). 0 = هیچ‌چیز حذف نکن.
            label:  برچسب فارسی برای لاگ ("فایل رسانه" یا "گزارش").

        Returns:
            تعداد فایل‌های حذف‌شده.
        """
        # 🐛 FIX: کلیپ منفی
        if days <= 0:
            log_info(f"ℹ️ [CLEANUP:files:{label}] days={days} ≤ 0 — رد شد")
            return 0

        if not os.path.isdir(folder):
            log_warning(
                f"⚠️ [CLEANUP:files:{label}] پوشه نیست: {folder}"
            )
            return 0

        t0 = time.monotonic()
        # 🧹 cutoff یک‌بار
        cutoff = time.time() - days * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff)

        scanned = 0
        removed = 0
        errors = 0
        newest_mtime = 0.0

        # 🟡 FIX: followlinks=False
        for root, _, files in os.walk(folder, followlinks=False):
            for name in files:
                path = os.path.join(root, name)
                scanned += 1
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > newest_mtime:
                        newest_mtime = mtime

                    if mtime < cutoff:
                        # 🟡 FIX: از _safe_remove استفاده می‌کنیم
                        if CleanupService._safe_remove(path):
                            removed += 1
                except FileNotFoundError:
                    # فایل بین اسکن و حذف توسط کس دیگه‌ای پاک شد
                    continue
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        log_warning(
                            f"⚠️ [CLEANUP:files:{label}] "
                            f"{type(e).__name__}: {e} | {path}"
                        )

        elapsed_ms = (time.monotonic() - t0) * 1000

        # 🎯 FIX: newest_age محاسبه
        newest_age_days = (
            (time.time() - newest_mtime) / 86400
            if newest_mtime > 0 else 0.0
        )

        # لاگ خلاصه
        if removed > 0 or errors > 0:
            log_success(
                f"🧹 [CLEANUP:files:{label}] {removed} حذف شد از "
                f"{scanned} اسکن‌شده | قدیمی‌تر از {days} روز "
                f"(قبل از {cutoff_dt:%Y-%m-%d}) | "
                f"newest_age={newest_age_days:.1f}d | "
                f"errors={errors} | {elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"ℹ️ [CLEANUP:files:{label}] چیزی حذف نشد | "
                f"{scanned} فایل اسکن شد | "
                f"newest_age={newest_age_days:.1f}d | "
                f"{elapsed_ms:.0f}ms"
            )

        if elapsed_ms > _SLOW_STEP_MS:
            log_warning(
                f"🐌 [CLEANUP:files:{label}] کند | {elapsed_ms:.0f}ms | "
                f"scanned={scanned}"
            )
        return removed

    # ═══════════════════════════════════════════
    # run_once — یک دور کامل
    # ═══════════════════════════════════════════
    async def run_once(self) -> dict[str, int]:
        """
        یک دور پاک‌سازی کامل — فایل‌ها + دیتابیس.

        🟡 FIX: حالا در صورت _running=False زودتر برمی‌گردد.
        🎯 بهبود: حالا dict آمار برمی‌گرداند.

        Returns:
            {"media": N, "reports": M, "db": K}
        """
        t_all = time.monotonic()
        log_info("▶️ [CLEANUP:run] شروع دور کامل")
        stats: dict[str, int] = {"media": 0, "reports": 0, "db": 0}

        # 🟡 FIX: چک _running قبل از شروع
        if not self._running:
            log_info("ℹ️ [CLEANUP:run] _running=False — رد شد")
            return stats

        # ─── فایل‌ها ───
        try:
            t_step = time.monotonic()
            stats["media"] = self._delete_old_files(
                MEDIA_DIR, self.keep_media_days, "فایل رسانه"
            )
            step_ms = (time.monotonic() - t_step) * 1000
            log_info(f"   ✔ media | {step_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ [CLEANUP:run:media] {type(e).__name__}: {e}"
            )

        # 🟡 FIX: چک _running بین مراحل
        if not self._running:
            log_info("ℹ️ [CLEANUP:run] _running=False بین مراحل — توقف")
            return stats

        try:
            t_step = time.monotonic()
            stats["reports"] = self._delete_old_files(
                REPORTS_DIR, self.keep_reports_days, "گزارش"
            )
            step_ms = (time.monotonic() - t_step) * 1000
            log_info(f"   ✔ reports | {step_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ [CLEANUP:run:reports] {type(e).__name__}: {e}"
            )

        # ─── دیتابیس ───
        if self.db and self.keep_messages_days > 0:
            try:
                t_step = time.monotonic()
                await self.db.delete_old_messages(self.keep_messages_days)
                step_ms = (time.monotonic() - t_step) * 1000
                stats["db"] = self.keep_messages_days  # فقط برای لاگ
                log_success(
                    f"🧹 [CLEANUP:run:db] پیام‌های قدیمی‌تر از "
                    f"{self.keep_messages_days} روز حذف شد | "
                    f"{step_ms:.0f}ms"
                )
            except Exception as e:
                log_error(
                    f"❌ [CLEANUP:run:db] {type(e).__name__}: {e}"
                )
        elif not self.db:
            log_info("ℹ️ [CLEANUP:run:db] db ندارد — رد شد")
        else:
            log_info(
                f"ℹ️ [CLEANUP:run:db] keep_messages_days="
                f"{self.keep_messages_days} — پیام‌ها حذف نمی‌شوند"
            )

        elapsed_ms = (time.monotonic() - t_all) * 1000
        log_success(
            f"🏁 [CLEANUP:run] کامل | {elapsed_ms:.0f}ms | "
            f"stats={stats}"
        )
        return stats

    # ═══════════════════════════════════════════
    # run_periodic
    # ═══════════════════════════════════════════
    async def run_periodic(self):
        """
        حلقهٔ periodic.

        🎯 FIX: sleep به chunk تقسیم می‌شود (پاسخ سریع به stop).
        """
        log_info(
            f"▶️ [CLEANUP:periodic] شروع | هر {self.interval_hours}ساعت"
        )
        iteration = 0
        total_sleep_s = self.interval_hours * 3600

        while self._running:
            iteration += 1
            t0 = time.monotonic()
            try:
                await self.run_once()
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"✔️ [CLEANUP:periodic] iter#{iteration} | "
                    f"{elapsed_ms:.0f}ms | next={self.interval_hours}h"
                )
            except asyncio.CancelledError:
                log_info(
                    f"🛑 [CLEANUP:periodic] cancel (iter#{iteration})"
                )
                raise
            except Exception as e:
                log_error(
                    f"❌ [CLEANUP:periodic] iter#{iteration} | "
                    f"{type(e).__name__}: {e}"
                )

            # 🎯 FIX: sleep تکه‌تکه برای پاسخ سریع به stop
            remaining_s = total_sleep_s
            while remaining_s > 0 and self._running:
                chunk = min(_SLEEP_CHUNK_S, remaining_s)
                try:
                    await asyncio.sleep(chunk)
                except asyncio.CancelledError:
                    log_info(
                        f"🛑 [CLEANUP:periodic] cancel در sleep "
                        f"(iter#{iteration})"
                    )
                    raise
                remaining_s -= chunk

        log_info(
            f"🛑 [CLEANUP:periodic] حلقه متوقف شد | "
            f"iterations={iteration}"
        )

    def stop(self):
        """درخواست توقف — حلقه در sleep سریع‌تر متوقف می‌شود."""
        self._running = False
        log_info("🛑 [CLEANUP:stop] درخواست توقف")


# ═══════════════════════════════════════════
# راه‌انداز
# ═══════════════════════════════════════════
async def start_cleanup_service(db=None) -> CleanupService:
    """ساخت CleanupService و راه‌اندازی حلقهٔ periodic در پس‌زمینه."""
    log_info("🚀 [CLEANUP:start] راه‌اندازی...")
    try:
        svc = CleanupService(db)
    except Exception as e:
        log_error(
            f"❌ [CLEANUP:start] ساخت ناموفق | {type(e).__name__}: {e}"
        )
        raise

    try:
        asyncio.create_task(svc.run_periodic())
        log_success(
            f"✅ [CLEANUP:start] حلقه راه افتاد | "
            f"media={svc.keep_media_days}روز | "
            f"reports={svc.keep_reports_days}روز | "
            f"messages={'همیشه' if svc.keep_messages_days == 0 else str(svc.keep_messages_days)+'روز'} | "
            f"interval={svc.interval_hours}ساعت"
        )
    except Exception as e:
        log_error(
            f"❌ [CLEANUP:start] create_task | "
            f"{type(e).__name__}: {e}"
        )
    return svc
