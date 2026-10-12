"""سرویس پاک‌سازی خودکار (Retention + Caches).

طبق تنظیمات `config.RETENTION`:
  - عکس/فایل‌های قدیمی‌تر از `media_days` حذف می‌شوند.
  - گزارش‌های قدیمی‌تر از `reports_days` حذف می‌شوند.
  - پیام‌های دیتابیس قدیمی‌تر از `messages_days` حذف می‌شوند
    (۰ = هرگز).
  - کش‌های AI (SafeBrain + Brain) و pycache هم پاک می‌شوند.

هر `interval_hours` ساعت یک‌بار اجرا می‌شود (و یک‌بار هنگام شروع).

نسخهٔ ۵ (این نسخه):
  - 🐛 FIX: `MEDIA_DIR`, `REPORTS_DIR`, `STORAGE_DIR` از
    `data.paths` می‌آیند.
  - 🐛 FIX: `_CACHE_DIRS`, `_CACHE_FILES` از `data.paths`.
  - 🐛 FIX: `_SLOW_STEP_MS`, `_SLEEP_CHUNK_S` env-based.
  - 🐛 FIX: `_env_int` حالا `min_val`/`max_val` دارد.
  - 🐛 FIX: `_resolve_retention_value()` helper (type-safe).
  - 🐛 FIX: `run_once` — `stats["db"]` حالا تعداد پیام‌های
    حذف‌شده است (نه days).
  - 🐛 FIX: `run_periodic` حالا `_stop_event` دارد.
  - 🐛 FIX: `start_cleanup_service` حالا reference task نگه
    می‌دارد.
  - 🐛 FIX: `_cleanup_caches` حالا `normcase` برای skip.
  - 🎯 `CLEANUP_TYPE_MEDIA`, `CLEANUP_TYPE_REPORTS` constants.
  - 🎯 `get_stats()`, `__repr__`, `is_running()`.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import datetime
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _resolve_retention_value(
    value: Any, default: int = 0
) -> int:
    """🎯 FIX: نوع امن از RETENTION dict."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════
# مسیرها (از data.paths — منبع یکتا)
# ═══════════════════════════════════════════
try:
    from data import paths as _data_paths
    MEDIA_DIR: str = str(_data_paths.MEDIA_DIR)
    REPORTS_DIR: str = str(_data_paths.REPORTS_DIR)
    STORAGE_DIR: str = str(_data_paths.STORAGE_DIR)
except Exception:
    from config import (
        MEDIA_DIR as _CFG_MEDIA,
        REPORTS_DIR as _CFG_REPORTS,
        STORAGE_DIR as _CFG_STORAGE,
    )
    MEDIA_DIR = str(_CFG_MEDIA)
    REPORTS_DIR = str(_CFG_REPORTS)
    STORAGE_DIR = str(_CFG_STORAGE)


# ─── RETENTION از config ───
try:
    from config import RETENTION
except Exception:
    RETENTION: dict[str, Any] = {}


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_MIN_INTERVAL_HOURS = max(
    1,
    _env_int("CLEANUP_MIN_INTERVAL_HOURS", 1, min_val=1),
)
_MAX_INTERVAL_HOURS = max(
    _MIN_INTERVAL_HOURS,
    _env_int(
        "CLEANUP_MAX_INTERVAL_HOURS", 720,
        min_val=_MIN_INTERVAL_HOURS,
    ),
)
_SLOW_STEP_MS = _env_int(
    "CLEANUP_SLOW_STEP_MS", 5000,
    min_val=100, max_val=600_000,
)
_SLEEP_CHUNK_S = _env_int(
    "CLEANUP_SLEEP_CHUNK_S", 5,
    min_val=1, max_val=60,
)

# 🎯 نوع cleanup
CLEANUP_TYPE_MEDIA = "media"
CLEANUP_TYPE_REPORTS = "reports"


# ═══════════════════════════════════════════
# پوشه‌ها/فایل‌های cache
# ═══════════════════════════════════════════
def _build_cache_dirs() -> list[str]:
    """🎯 FIX: پوشه‌های cache از data.paths."""
    dirs: list[str] = []
    try:
        dirs.extend([
            str(_data_paths.SAFE_BRAIN_CACHE_DIR),
            str(_data_paths.SAFE_BRAIN_MEMORY_DIR),
            str(_data_paths.BRAIN_CACHE_DIR),
            str(_data_paths.BRAIN_MEMORY_DIR),
            str(_data_paths.PYCACHE_DIR),
        ])
    except Exception:
        dirs.extend([
            os.path.join(STORAGE_DIR, "safe_brain", "cache"),
            os.path.join(STORAGE_DIR, "safe_brain", "memory"),
            os.path.join(STORAGE_DIR, "brain", "cache"),
            os.path.join(STORAGE_DIR, "brain", "memory"),
            os.path.join(STORAGE_DIR, "pycache"),
        ])
    # فیلتر تکراری‌ها
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _build_cache_files() -> list[str]:
    """🎯 FIX: فایل‌های cache از data.paths."""
    files: list[str] = []
    try:
        files.extend([
            str(_data_paths.REPORT_CACHE),
            str(_data_paths.LEARNED_PATTERNS),
            str(_data_paths.BRAIN_PLANS),
        ])
    except Exception:
        files.extend([
            os.path.join(STORAGE_DIR, "database", "report_cache.json"),
            os.path.join(STORAGE_DIR, "brain", "learned_patterns.json"),
            os.path.join(STORAGE_DIR, "brain", "plans.json"),
        ])
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


_CACHE_DIRS: list[str] = _build_cache_dirs()
_CACHE_FILES: list[str] = _build_cache_files()

# 🎯 پوشه‌هایی که در scan __pycache__ نباید بره داخلشون
_SKIP_DIRS_FOR_PYCACHE: tuple[str, ...] = (
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
        self._stop_event: asyncio.Event | None = None
        self._pending_tasks: list[asyncio.Task] = []

        # 🐛 FIX: type-safe
        self.keep_media_days = _resolve_retention_value(
            RETENTION.get("media_days", 0), 0
        )
        self.keep_reports_days = _resolve_retention_value(
            RETENTION.get("reports_days", 0), 0
        )
        self.keep_messages_days = _resolve_retention_value(
            RETENTION.get("messages_days", 0), 0
        )

        raw_interval = _resolve_retention_value(
            RETENTION.get("cleanup_interval_hours", 24),
            24,
        )
        self.interval_hours = max(
            _MIN_INTERVAL_HOURS,
            min(_MAX_INTERVAL_HOURS, raw_interval),
        )

        log_success(
            f"✅ [CLEANUP:init] آماده | "
            f"media={self.keep_media_days}روز | "
            f"reports={self.keep_reports_days}روز | "
            f"messages="
            f"{'همیشه' if self.keep_messages_days == 0 else str(self.keep_messages_days)+'روز'} | "
            f"interval={self.interval_hours}ساعت | "
            f"caches={len(_CACHE_DIRS)}dirs+"
            f"{len(_CACHE_FILES)}files | "
            f"db={'✅' if self.db else '❌'}"
        )
        log_info(
            f"📂 [CLEANUP:init] مسیرها | "
            f"media_dir={MEDIA_DIR} | "
            f"reports_dir={REPORTS_DIR}"
        )
        if raw_interval != self.interval_hours:
            log_warning(
                f"⚠️ [CLEANUP:init] interval={raw_interval} "
                f"خارج از بازهٔ "
                f"[{_MIN_INTERVAL_HOURS}, {_MAX_INTERVAL_HOURS}] — "
                f"به {self.interval_hours} کلیپ شد"
            )

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    @property
    def is_running(self) -> bool:
        """🎯 آیا سرویس در حال اجراست؟"""
        return self._running

    @staticmethod
    def _safe_remove(path: str) -> bool:
        """حذف امن فایل (با تلاش برای chmod اگر readonly بود)."""
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

    @staticmethod
    def _dir_size_kb(path: str) -> float:
        """حجم پوشه به کیلوبایت (بدون symlink loop)."""
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

    @staticmethod
    def _should_skip_pycache_dir(root: str) -> bool:
        """🎯 FIX: چک case-insensitive."""
        root_norm = os.path.normcase(root).replace("\\", "/")
        for sd in _SKIP_DIRS_FOR_PYCACHE:
            sd_norm = os.path.normcase(sd).replace("\\", "/")
            if sd_norm in root_norm:
                return True
        return False

    # ═══════════════════════════════════════════
    # _delete_old_files
    # ═══════════════════════════════════════════
    @staticmethod
    def _delete_old_files(
        folder: str, days: int, label: str
    ) -> int:
        """حذف فایل‌های قدیمی‌تر از `days` روز."""
        # 🐛 FIX: کلیپ منفی
        if days <= 0:
            log_info(
                f"ℹ️ [CLEANUP:files:{label}] "
                f"days={days} ≤ 0 — رد شد"
            )
            return 0

        if not os.path.isdir(folder):
            log_warning(
                f"⚠️ [CLEANUP:files:{label}] "
                f"پوشه نیست: {folder}"
            )
            return 0

        t0 = time.monotonic()
        cutoff = time.time() - days * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff)

        scanned = 0
        removed = 0
        errors = 0
        newest_mtime = 0.0

        for root, _, files in os.walk(
            folder, followlinks=False
        ):
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
                f"🧹 [CLEANUP:files:{label}] {removed} حذف "
                f"از {scanned} اسکن‌شده | "
                f"قدیمی‌تر از {days} روز "
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
                f"🐌 [CLEANUP:files:{label}] کند | "
                f"{elapsed_ms:.0f}ms | scanned={scanned}"
            )
        return removed

    # ═══════════════════════════════════════════
    # _cleanup_caches
    # ═══════════════════════════════════════════
    @staticmethod
    def _cleanup_caches() -> dict[str, Any]:
        """پاک‌سازی کش‌های AI + pycache.

        Returns:
            {
                "ai_files": N,
                "ai_dirs": M,
                "pycache_storage": K,
                "pycache_global": L,
                "freed_kb": float,
                "errors": E,
            }
        """
        t0 = time.monotonic()
        log_info("▶️ [CLEANUP:caches] شروع")

        stats: dict[str, Any] = {
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
                                f"{type(e).__name__}: {e} | "
                                f"{path}"
                            )
                    elif os.path.isdir(path):
                        try:
                            if not os.listdir(path):
                                os.rmdir(path)
                                stats["ai_dirs"] += 1
                        except Exception:
                            pass
            except Exception as e:
                stats["errors"] += 1
                log_warning(
                    f"⚠️ [CLEANUP:caches] "
                    f"{type(e).__name__}: {e} | {d}"
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
        pycache_storage = os.path.join(
            STORAGE_DIR, "pycache"
        )
        if os.path.isdir(pycache_storage):
            try:
                for name in os.listdir(pycache_storage):
                    path = os.path.join(
                        pycache_storage, name
                    )
                    if os.path.isdir(path):
                        try:
                            size = (
                                CleanupService._dir_size_kb(
                                    path
                                )
                            )
                            shutil.rmtree(
                                path, ignore_errors=True
                            )
                            stats["pycache_storage"] += 1
                            stats["freed_kb"] += size
                        except Exception as e:
                            stats["errors"] += 1
                            log_warning(
                                f"⚠️ [CLEANUP:caches] "
                                f"{type(e).__name__}: {e} | "
                                f"{path}"
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
                    f"{type(e).__name__}: {e} | "
                    f"{pycache_storage}"
                )

        # ─── ۴) پاک __pycache__ تو کل پروژه ───
        project_root = os.path.dirname(
            os.path.normpath(STORAGE_DIR)
        )

        for root, dirs, _ in os.walk(
            project_root, followlinks=False
        ):
            # 🎯 FIX: case-insensitive skip
            if CleanupService._should_skip_pycache_dir(
                root
            ):
                dirs[:] = []
                continue

            if "__pycache__" in dirs:
                pyc_path = os.path.join(
                    root, "__pycache__"
                )
                try:
                    size = CleanupService._dir_size_kb(
                        pyc_path
                    )
                    shutil.rmtree(
                        pyc_path, ignore_errors=True
                    )
                    stats["pycache_global"] += 1
                    stats["freed_kb"] += size
                except Exception:
                    pass
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
                f"🐌 [CLEANUP:caches] کند | "
                f"{elapsed_ms:.0f}ms"
            )

        return stats

    # ═══════════════════════════════════════════
    # run_once
    # ═══════════════════════════════════════════
    async def run_once(self) -> dict[str, int]:
        """یک دور پاک‌سازی کامل."""
        t_all = time.monotonic()
        log_info("▶️ [CLEANUP:run] شروع دور کامل")

        stats: dict[str, int] = {
            "media": 0,
            "reports": 0,
            "db": 0,
            "cache_files": 0,
            "pycache_dirs": 0,
        }

        if not self._running:
            log_info(
                "ℹ️ [CLEANUP:run] _running=False — رد شد"
            )
            return stats

        # ─── ۱) media ───
        try:
            t_step = time.monotonic()
            stats["media"] = self._delete_old_files(
                MEDIA_DIR, self.keep_media_days,
                CLEANUP_TYPE_MEDIA,
            )
            step_ms = (time.monotonic() - t_step) * 1000
            log_info(f"   ✔ media | {step_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ [CLEANUP:run:media] "
                f"{type(e).__name__}: {e}"
            )

        if not self._running:
            log_info(
                "ℹ️ [CLEANUP:run] _running=False — توقف"
            )
            return stats

        # ─── ۲) reports ───
        try:
            t_step = time.monotonic()
            stats["reports"] = self._delete_old_files(
                REPORTS_DIR, self.keep_reports_days,
                CLEANUP_TYPE_REPORTS,
            )
            step_ms = (time.monotonic() - t_step) * 1000
            log_info(f"   ✔ reports | {step_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ [CLEANUP:run:reports] "
                f"{type(e).__name__}: {e}"
            )

        if not self._running:
            log_info(
                "ℹ️ [CLEANUP:run] _running=False — توقف"
            )
            return stats

        # ─── ۳) db ───
        if self.db and self.keep_messages_days > 0:
            try:
                t_step = time.monotonic()
                # 🎯 FIX: تلاش برای گرفتن return value
                result = await self.db.delete_old_messages(
                    self.keep_messages_days
                )
                step_ms = (time.monotonic() - t_step) * 1000

                # اگر db تعداد برگرداند، استفاده کن
                if isinstance(result, int):
                    stats["db"] = result
                else:
                    stats["db"] = self.keep_messages_days

                log_success(
                    f"🧹 [CLEANUP:run:db] پیام‌های "
                    f"قدیمی‌تر از "
                    f"{self.keep_messages_days} روز | "
                    f"result={result} | {step_ms:.0f}ms"
                )
            except Exception as e:
                log_error(
                    f"❌ [CLEANUP:run:db] "
                    f"{type(e).__name__}: {e}"
                )
        elif not self.db:
            log_info("ℹ️ [CLEANUP:run:db] db ندارد")
        else:
            log_info(
                f"ℹ️ [CLEANUP:run:db] "
                f"keep_messages_days="
                f"{self.keep_messages_days} — "
                f"پیام‌ها حذف نمی‌شوند"
            )

        if not self._running:
            log_info(
                "ℹ️ [CLEANUP:run] _running=False — توقف"
            )
            return stats

        # ─── ۴) caches ───
        try:
            t_step = time.monotonic()
            cache_stats = self._cleanup_caches()
            step_ms = (time.monotonic() - t_step) * 1000
            stats["cache_files"] = cache_stats.get(
                "ai_files", 0
            )
            stats["pycache_dirs"] = (
                cache_stats.get("pycache_storage", 0)
                + cache_stats.get("pycache_global", 0)
            )
            log_info(f"   ✔ caches | {step_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ [CLEANUP:run:caches] "
                f"{type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t_all) * 1000
        log_success(
            f"🏁 [CLEANUP:run] کامل | "
            f"{elapsed_ms:.0f}ms | stats={stats}"
        )
        return stats

    # ═══════════════════════════════════════════
    # run_periodic
    # ═══════════════════════════════════════════
    async def run_periodic(self) -> None:
        """حلقهٔ periodic با بیداری سریع در stop."""
        self._stop_event = asyncio.Event()

        log_info(
            f"▶️ [CLEANUP:periodic] شروع | "
            f"هر {self.interval_hours}ساعت"
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
                    f"✔️ [CLEANUP:periodic] "
                    f"iter#{iteration} | {elapsed_ms:.0f}ms | "
                    f"next={self.interval_hours}h"
                )
            except asyncio.CancelledError:
                log_info(
                    f"🛑 [CLEANUP:periodic] cancel "
                    f"(iter#{iteration})"
                )
                raise
            except Exception as e:
                log_error(
                    f"❌ [CLEANUP:periodic] "
                    f"iter#{iteration} | "
                    f"{type(e).__name__}: {e}"
                )

            # ─── sleep تکه‌تکه ───
            remaining_s = total_sleep_s
            while remaining_s > 0 and self._running:
                chunk = min(_SLEEP_CHUNK_S, remaining_s)

                # 🎯 FIX: بیداری سریع در stop
                if self._stop_event is not None:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=chunk,
                        )
                        # stop شد
                        remaining_s = 0
                        break
                    except TimeoutError:
                        pass
                else:
                    try:
                        await asyncio.sleep(chunk)
                    except asyncio.CancelledError:
                        log_info(
                            f"🛑 [CLEANUP:periodic] "
                            f"cancel در sleep "
                            f"(iter#{iteration})"
                        )
                        raise

                remaining_s -= chunk

        log_info(
            f"🛑 [CLEANUP:periodic] حلقه متوقف شد | "
            f"iterations={iteration}"
        )

    # ═══════════════════════════════════════════
    # manual
    # ═══════════════════════════════════════════
    def cleanup_caches(self) -> dict[str, Any]:
        """پاک‌سازی کش‌ها — برای صدا زدن از پنل."""
        return self._cleanup_caches()

    def stop(self) -> None:
        """درخواست توقف."""
        self._running = False
        if self._stop_event is not None:
            try:
                self._stop_event.set()
            except Exception:
                pass
        log_info("🛑 [CLEANUP:stop] درخواست توقف")

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "running": self._running,
            "media_dir": MEDIA_DIR,
            "reports_dir": REPORTS_DIR,
            "storage_dir": STORAGE_DIR,
            "keep_media_days": self.keep_media_days,
            "keep_reports_days": self.keep_reports_days,
            "keep_messages_days": self.keep_messages_days,
            "interval_hours": self.interval_hours,
            "cache_dirs_count": len(_CACHE_DIRS),
            "cache_files_count": len(_CACHE_FILES),
            "db_connected": self.db is not None,
        }

    def __repr__(self) -> str:
        return (
            f"<CleanupService "
            f"media={self.keep_media_days}d "
            f"reports={self.keep_reports_days}d "
            f"messages={self.keep_messages_days}d "
            f"interval={self.interval_hours}h>"
        )


# ═══════════════════════════════════════════
# راه‌انداز
# ═══════════════════════════════════════════
async def start_cleanup_service(db=None) -> CleanupService:
    """ساخت CleanupService + راه‌اندازی حلقه در پس‌زمینه."""
    log_info("🚀 [CLEANUP:start] راه‌اندازی...")

    try:
        svc = CleanupService(db)
    except Exception as e:
        log_error(
            f"❌ [CLEANUP:start] ساخت ناموفق | "
            f"{type(e).__name__}: {e}"
        )
        raise

    try:
        # 🎯 FIX: نگه‌داری reference
        task = asyncio.create_task(
            svc.run_periodic(),
            name="cleanup_periodic",
        )
        svc._pending_tasks.append(task)
        log_success(
            f"✅ [CLEANUP:start] حلقه راه افتاد | "
            f"media={svc.keep_media_days}روز | "
            f"reports={svc.keep_reports_days}روز | "
            f"messages="
            f"{'همیشه' if svc.keep_messages_days == 0 else str(svc.keep_messages_days)+'روز'} | "
            f"interval={svc.interval_hours}ساعت"
        )
    except Exception as e:
        log_error(
            f"❌ [CLEANUP:start] create_task | "
            f"{type(e).__name__}: {e}"
        )
    return svc


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "CleanupService",
    "start_cleanup_service",
    "MEDIA_DIR",
    "REPORTS_DIR",
    "STORAGE_DIR",
    "CLEANUP_TYPE_MEDIA",
    "CLEANUP_TYPE_REPORTS",
]