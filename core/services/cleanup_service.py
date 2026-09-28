# core/services/cleanup_service.py
"""
سرویس پاک‌سازی خودکار (Retention + Caches).

طبق تنظیمات .env:
  - عکس/فایل‌های قدیمی‌تر از KEEP_MEDIA_DAYS حذف می‌شوند.
  - گزارش‌های قدیمی‌تر از KEEP_REPORTS_DAYS حذف می‌شوند.
  - پیام‌های دیتابیس قدیمی‌تر از KEEP_MESSAGES_DAYS حذف می‌شوند (۰ = هرگز).
  - کش‌های AI (SafeBrain + Brain) و pycache هم پاک می‌شوند.
هر CLEANUP_INTERVAL_HOURS ساعت یک‌بار اجرا می‌شود (و یک‌بار هنگام شروع).

نسخهٔ ۴ (این نسخه):
  - ⭐ NEW: پاک‌سازی کش‌های AI (SafeBrain cache/memory، Brain cache)
  - ⭐ NEW: پاک‌سازی pycache (هم storage/pycache، هم __pycache__)
  - ⭐ NEW: پاک‌سازی report_cache.json
  - ⭐ NEW: متد _cleanup_caches جدا برای قابلیت صدا زدن مستقل
  - 🎯 cleanup_interval_hours از env با کلیپ
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳:
      * followlinks=False
      * _safe_remove برای readonly
      * _running check
      * newest_age در لاگ
      * sleep تکه‌تکه
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import datetime

from config import MEDIA_DIR, REPORTS_DIR, RETENTION, STORAGE_DIR
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
_SLEEP_CHUNK_S = 5.0           # sleep تکه‌تکه

# ─── پوشه‌هایی که cleanup_caches باید پاک کند ───
_CACHE_DIRS = [
    os.path.join(STORAGE_DIR, "safe_brain", "cache"),
    os.path.join(STORAGE_DIR, "safe_brain", "memory"),
    os.path.join(STORAGE_DIR, "brain", "cache"),
    os.path.join(STORAGE_DIR, "brain", "memory"),
    os.path.join(STORAGE_DIR, "pycache"),
]

# ─── فایل‌های تک که باید پاک شوند ───
_CACHE_FILES = [
    os.path.join(STORAGE_DIR, "database", "report_cache.json"),
    os.path.join(STORAGE_DIR, "brain", "learned_patterns.json"),
    os.path.join(STORAGE_DIR, "brain", "plans.json"),
]

# ─── پوشه‌هایی که در scan __pycache__ نباید بره داخلشون ───
_SKIP_DIRS_FOR_PYCACHE = (
    "venv", ".venv", "env", ".env",
    ".git", "node_modules",
    os.path.join("storage", "sessions"),
)


# ═══════════════════════════════════════════
# CleanupService
# ═══════════════════════════════════════════
class CleanupService:
    """پاک‌سازی خودکار فایل‌های قدیمی + پیام‌های دیتابیس + کش‌ها."""

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
            f"caches={len(_CACHE_DIRS)}dirs+{len(_CACHE_FILES)}files | "
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
        """حذف امن فایل (با تلاش برای تغییر permission اگر readonly بود)."""
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            try:
                os.chmod(path, 0o644)
                os.remove(path)
                return True
            except Exception:
                return False
        except Exception:
            return False

    # ═══════════════════════════════════════════
    # _dir_size_kb — حجم پوشه (برای آمار)
    # ═══════════════════════════════════════════
    @staticmethod
    def _dir_size_kb(path: str) -> float:
        """حجم یه پوشه به کیلوبایت."""
        total = 0
        try:
            for root, _, files in os.walk(
                path, followlinks=False
            ):
                for f in files:
                    try:
                        total += os.path.getsize(
                            os.path.join(root, f)
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        return total / 1024

    # ═══════════════════════════════════════════
    # _delete_old_files
    # ═══════════════════════════════════════════
    @staticmethod
    def _delete_old_files(folder: str, days: int, label: str) -> int:
        """
        فایل‌های قدیمی‌تر از days روز را در folder حذف می‌کند.

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
        cutoff = time.time() - days * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff)

        scanned = 0
        removed = 0
        errors = 0
        newest_mtime = 0.0

        for root, _, files in os.walk(folder, followlinks=False):
            for name in files:
                path = os.path.join(root, name)
                scanned += 1
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > newest_mtime:
                        newest_mtime = mtime

                    if mtime < cutoff:
                        if CleanupService._safe_remove(path):
                            removed += 1
                except FileNotFoundError:
                    continue
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        log_warning(
                            f"⚠️ [CLEANUP:files:{label}] "
                            f"{type(e).__name__}: {e} | {path}"
                        )

        elapsed_ms = (time.monotonic() - t0) * 1000

        newest_age_days = (
            (time.time() - newest_mtime) / 86400
            if newest_mtime > 0 else 0.0
        )

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
    # ⭐ NEW: _cleanup_caches — پاک‌سازی کش‌ها
    # ═══════════════════════════════════════════
    @staticmethod
    def _cleanup_caches() -> dict[str, int]:
        """
        پاک‌سازی کش‌های AI + pycache.

        Returns:
            {
                "ai_files": N,           # فایل‌های cache حذف‌شده
                "ai_dirs": M,            # پوشه‌های خالی حذف‌شده
                "pycache_storage": K,    # پوشه‌های storage/pycache
                "pycache_global": L,     # پوشه‌های __pycache__
                "freed_kb": float,       # حجم آزادشده
                "errors": E,
            }
        """
        t0 = time.monotonic()
        log_info("▶️ [CLEANUP:caches] شروع پاک‌سازی کش‌ها")

        stats = {
            "ai_files": 0,
            "ai_dirs": 0,
            "pycache_storage": 0,
            "pycache_global": 0,
            "freed_kb": 0.0,
            "errors": 0,
        }

        # ─── ۱) پاک فایل‌های AI ───
        for d in _CACHE_DIRS:
            if not os.path.isdir(d):
                continue

            try:
                for name in os.listdir(d):
                    path = os.path.join(d, name)
                    if os.path.isfile(path):
                        try:
                            size = os.path.getsize(path)
                            os.remove(path)
                            stats["ai_files"] += 1
                            stats["freed_kb"] += size / 1024
                        except Exception as e:
                            stats["errors"] += 1
                            log_warning(
                                f"⚠️ [CLEANUP:caches] "
                                f"{type(e).__name__}: {e} | {path}"
                            )
                    elif os.path.isdir(path):
                        # پوشه‌های خالی رو پاک کن
                        try:
                            if not os.listdir(path):
                                os.rmdir(path)
                                stats["ai_dirs"] += 1
                        except Exception:
                            pass
            except Exception as e:
                stats["errors"] += 1
                log_warning(
                    f"⚠️ [CLEANUP:caches] {type(e).__name__}: {e} | {d}"
                )

        # ─── ۲) پاک فایل‌های تک AI ───
        for f in _CACHE_FILES:
            if os.path.isfile(f):
                try:
                    size = os.path.getsize(f)
                    os.remove(f)
                    stats["ai_files"] += 1
                    stats["freed_kb"] += size / 1024
                except Exception as e:
                    stats["errors"] += 1
                    log_warning(
                        f"⚠️ [CLEANUP:caches] "
                        f"{type(e).__name__}: {e} | {f}"
                    )

        # ─── ۳) پاک storage/pycache ───
        pycache_storage = os.path.join(STORAGE_DIR, "pycache")
        if os.path.isdir(pycache_storage):
            try:
                for name in os.listdir(pycache_storage):
                    path = os.path.join(pycache_storage, name)
                    if os.path.isdir(path):
                        try:
                            size = CleanupService._dir_size_kb(path)
                            shutil.rmtree(path, ignore_errors=True)
                            stats["pycache_storage"] += 1
                            stats["freed_kb"] += size
                        except Exception as e:
                            stats["errors"] += 1
                            log_warning(
                                f"⚠️ [CLEANUP:caches] "
                                f"{type(e).__name__}: {e} | {path}"
                            )
                    elif os.path.isfile(path):
                        try:
                            size = os.path.getsize(path)
                            os.remove(path)
                            stats["ai_files"] += 1
                            stats["freed_kb"] += size / 1024
                        except Exception:
                            pass
            except Exception as e:
                stats["errors"] += 1
                log_warning(
                    f"⚠️ [CLEANUP:caches] "
                    f"{type(e).__name__}: {e} | {pycache_storage}"
                )

        # ─── ۴) پاک __pycache__ تو کل پروژه ───
        # NOTE: از STORAGE_DIR.parent استفاده می‌کنیم (پروژه root)
        project_root = os.path.dirname(STORAGE_DIR)

        for root, dirs, _ in os.walk(
            project_root, followlinks=False
        ):
            # ─── چک skip ───
            skip = False
            for sd in _SKIP_DIRS_FOR_PYCACHE:
                # نرمال‌سازی مسیر
                if sd in root.replace("\\", "/"):
                    skip = True
                    break
            if skip:
                dirs[:] = []   # ادامه نده
                continue

            # ─── پاک __pycache__ ───
            if "__pycache__" in dirs:
                pyc_path = os.path.join(root, "__pycache__")
                try:
                    size = CleanupService._dir_size_kb(pyc_path)
                    shutil.rmtree(pyc_path, ignore_errors=True)
                    stats["pycache_global"] += 1
                    stats["freed_kb"] += size
                except Exception:
                    pass
                # از لیست dirs حذف کن
                try:
                    dirs.remove("__pycache__")
                except ValueError:
                    pass

        elapsed_ms = (time.monotonic() - t0) * 1000

        log_success(
            f"🧹 [CLEANUP:caches] کامل | "
            f"ai_files={stats['ai_files']} | "
            f"ai_dirs={stats['ai_dirs']} | "
            f"pycache_storage={stats['pycache_storage']} | "
            f"__pycache__={stats['pycache_global']} | "
            f"freed={stats['freed_kb']:.1f}KB | "
            f"errors={stats['errors']} | "
            f"{elapsed_ms:.0f}ms"
        )

        if elapsed_ms > _SLOW_STEP_MS:
            log_warning(
                f"🐌 [CLEANUP:caches] کند | {elapsed_ms:.0f}ms"
            )

        return stats

    # ═══════════════════════════════════════════
    # run_once — یک دور کامل
    # ═══════════════════════════════════════════
    async def run_once(self) -> dict[str, int]:
        """
        یک دور پاک‌سازی کامل:
          - فایل‌های رسانه
          - گزارش‌ها
          - پیام‌های DB
          - کش‌های AI + pycache

        Returns:
            {
                "media": N,
                "reports": M,
                "db": K,
                "cache_files": P,
                "pycache_dirs": Q,
            }
        """
        t_all = time.monotonic()
        log_info("▶️ [CLEANUP:run] شروع دور کامل")
        stats: dict[str, int] = {
            "media": 0,
            "reports": 0,
            "db": 0,
            "cache_files": 0,
            "pycache_dirs": 0,
        }

        # 🟡 FIX: چک _running قبل از شروع
        if not self._running:
            log_info("ℹ️ [CLEANUP:run] _running=False — رد شد")
            return stats

        # ═══════════════════════════════════════
        # ۱) عکس/فایل‌های رسانه
        # ═══════════════════════════════════════
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

        if not self._running:
            log_info("ℹ️ [CLEANUP:run] _running=False بین مراحل — توقف")
            return stats

        # ═══════════════════════════════════════
        # ۲) گزارش‌ها
        # ═══════════════════════════════════════
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

        if not self._running:
            log_info("ℹ️ [CLEANUP:run] _running=False بین مراحل — توقف")
            return stats

        # ═══════════════════════════════════════
        # ۳) پیام‌های قدیمی DB
        # ═══════════════════════════════════════
        if self.db and self.keep_messages_days > 0:
            try:
                t_step = time.monotonic()
                await self.db.delete_old_messages(self.keep_messages_days)
                step_ms = (time.monotonic() - t_step) * 1000
                stats["db"] = self.keep_messages_days
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

        if not self._running:
            log_info("ℹ️ [CLEANUP:run] _running=False بین مراحل — توقف")
            return stats

        # ═══════════════════════════════════════
        # ۴) ⭐ NEW: کش‌های AI + pycache
        # ═══════════════════════════════════════
        try:
            t_step = time.monotonic()
            cache_stats = self._cleanup_caches()
            step_ms = (time.monotonic() - t_step) * 1000
            stats["cache_files"] = cache_stats.get("ai_files", 0)
            stats["pycache_dirs"] = (
                cache_stats.get("pycache_storage", 0)
                + cache_stats.get("pycache_global", 0)
            )
            log_info(f"   ✔ caches | {step_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ [CLEANUP:run:caches] {type(e).__name__}: {e}"
            )

        # ═══════════════════════════════════════
        # خلاصه
        # ═══════════════════════════════════════
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
        """حلقهٔ periodic — هر interval_hours یه بار run_once."""
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

            # sleep تکه‌تکه برای پاسخ سریع به stop
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

    # ═══════════════════════════════════════════
    # ⭐ NEW: cleanup_caches — برای صدا زدن مستقل از پنل
    # ═══════════════════════════════════════════
    def cleanup_caches(self) -> dict[str, int]:
        """پاک‌سازی کش‌ها — برای صدا زدن از پنل."""
        return self._cleanup_caches()

    def stop(self):
        """درخواست توقف."""
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
            f"interval={svc.interval_hours}ساعت | "
            f"caches=✅"
        )
    except Exception as e:
        log_error(
            f"❌ [CLEANUP:start] create_task | "
            f"{type(e).__name__}: {e}"
        )
    return svc