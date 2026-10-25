"""سیستم پشتیبان‌گیری هوشمند.

قابلیت‌ها:
  - backup_daily: کپی + zip دیتابیس (سبک، برای حفظ روزانه)
  - backup_full:  کپی کد + دیتابیس در یک zip (snapshot کامل)
  - check_code_changes: مقایسه hash (mtime+size) → تغییرات
  - run_periodic: حلقهٔ hourly که خودش تصمیم می‌گیرد
  - cleanup: چرخش بکاپ‌های قدیمی

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX: `DB_PATH` حالا از `data.paths.DB_FILE` می‌آید.
  - 🐛 FIX: `BACKUP_DIR` حالا از `data.paths.BACKUPS_DIR`.
  - 🐛 FIX: `_IGNORE_EXTENSIONS` + `.db-journal`.
  - 🐛 FIX: `DAILY_BACKUP_HOURS`, `KEEP_*`, `FULL_BACKUP_DAYS`,
    `POKE_INTERVAL_SECONDS` حالا با `_env_int` (sanitize).
  - 🐛 FIX: `_SLOW_BACKUP_MS`, `_SLOW_HASH_MS` حالا env-based.
  - 🐛 FIX: `_save_stats`, `_save_hashes`, `_mark_*_done` حالا
    atomic + fsync + cleanup tmp.
  - 🐛 FIX: `_get_code_files` حالا `_IGNORE_FILES` را قبل از
    `_is_sensitive_filename` چک می‌کند (پرفورمنس).
  - 🐛 FIX: `_compress_folder` حالا `zipfile.BadZipFile` را
    handle می‌کند.
  - 🐛 FIX: `_should_backup_daily` / `_should_backup_full` حالا
    clamp مقادیر منفی.
  - 🐛 FIX: `_cleanup_old` حالا `min=1` برای keep.
  - 🐛 FIX: `_is_db_valid` حالا timeout دارد.
  - 🐛 FIX: `run_periodic` حالا `_stop_event` دارد
    (بیداری سریع در stop).
  - 🐛 FIX: `start_backup_service` حالا reference task را
    نگه می‌دارد.
  - 🎯 `BACKUP_TYPE_DAILY`, `BACKUP_TYPE_FULL` constants.
  - 🎯 `get_stats()` کامل‌تر.
  - 🎯 `__repr__` برای BackupService.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import threading
import time
import zipfile
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


# ═══════════════════════════════════════════
# مسیرها (از data.paths — منبع یکتا)
# ═══════════════════════════════════════════
try:
    from data import paths as _data_paths
    DB_PATH: str = str(_data_paths.DB_FILE)
    BACKUP_DIR: str = str(_data_paths.BACKUPS_DIR)
except Exception:
    from config import DB_DIR, STORAGE_DIR
    DB_PATH = os.path.join(str(DB_DIR), "messages.db")
    BACKUP_DIR = os.path.join(str(STORAGE_DIR), "backups")

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )


# ═══════════════════════════════════════════
# تنظیمات (env-based)
# ═══════════════════════════════════════════
DAILY_BACKUP_HOURS: int = _env_int(
    "DAILY_BACKUP_HOURS", 24,
    min_val=1, max_val=720,
)
KEEP_DAILY_BACKUPS: int = _env_int(
    "KEEP_DAILY_BACKUPS", 30,
    min_val=1, max_val=1000,
)
KEEP_FULL_BACKUPS: int = _env_int(
    "KEEP_FULL_BACKUPS", 10,
    min_val=1, max_val=100,
)
FULL_BACKUP_DAYS: int = _env_int(
    "FULL_BACKUP_DAYS", 7,
    min_val=1, max_val=365,
)
POKE_INTERVAL_SECONDS: int = _env_int(
    "BACKUP_POKE_SECONDS", 3600,
    min_val=60, max_val=86400,
)

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_BACKUP_MS: int = _env_int(
    "BACKUP_SLOW_MS", 30_000,
    min_val=1000, max_val=600_000,
)
_SLOW_HASH_MS: int = _env_int(
    "BACKUP_SLOW_HASH_MS", 5_000,
    min_val=100, max_val=60_000,
)
_DB_INTEGRITY_TIMEOUT_S: float = 30.0

# 🎯 نوع بکاپ
BACKUP_TYPE_DAILY = "daily"
BACKUP_TYPE_FULL = "full"


# ═══════════════════════════════════════════
# لیست‌های امنیتی (فایل‌های حساس)
# ═══════════════════════════════════════════
_IGNORE_DIRS: frozenset[str] = frozenset({
    "__pycache__", "backups", "storage", "data", ".git",
    "venv", ".venv", "env", ".env",
    "reports", "logs", "cache", "sessions", "media",
    "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".idea", ".vscode",
})

_IGNORE_EXTENSIONS: frozenset[str] = frozenset({
    # باینری Python
    ".pyc", ".pyo", ".pyd",
    # دیتابیس
    ".db", ".db-wal", ".db-shm", ".db-journal",
    ".sqlite", ".sqlite3",
    # لاگ/zip/pid
    ".log", ".bale", ".zip", ".7z", ".rar", ".pid",
    # 🔴 کلید/گواهی
    ".key", ".pem", ".crt", ".p12", ".pfx",
    ".jks", ".keystore",
})

_IGNORE_FILES: frozenset[str] = frozenset({
    # فایل‌های خود backup
    "hashes.json", "last_backup.txt",
    "last_full_backup.txt", "bot.pid",
    "stats.json", "products_list.txt", "messages.txt",
    # 🔴 فایل‌های حساس دقیق
    ".env",
    "secrets.json", "credentials.json", "auth.json",
    ".git-credentials", ".netrc", "id_rsa", "id_dsa",
    "known_hosts", "config.json",
    ".pgpass",  # 🔴 جدید
})

# 🔴 stems حساس
_SENSITIVE_STEMS: tuple[str, ...] = (
    ".env",
    "secrets",
    "secret",
    "credentials",
    "private",
    "auth",
    "token",
    "api_key",
    "apikey",
    ".pgpass",  # 🔴 جدید
    "id_rsa",   # 🔴 جدید
    "id_dsa",   # 🔴 جدید
)

# 🔴 پسوندهای «نمونه» — مجاز
_ALLOWED_SAMPLE_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".sample",
    ".template",
    ".dist",
    ".default",
)


def _is_sensitive_filename(name: str) -> bool:
    """آیا نام فایل حساس است؟ (نباید در backup کپی شود)

    منطق:
      ۱) فایل نمونه (ends with .example/.sample/...) → False
      ۲) نام دقیقاً یکی از stemها → True
      ۳) با `{stem}.` شروع → True
      ۴) با `{stem}_` شروع → True
      ۵) وگرنه → False
    """
    if not name:
        return False

    name_low = name.lower()

    # ─── ۱) استثنا: فایل‌های نمونه ───
    for suffix in _ALLOWED_SAMPLE_SUFFIXES:
        if name_low.endswith(suffix):
            return False

    # ─── ۲ تا ۴) چک stemها ───
    for stem in _SENSITIVE_STEMS:
        if name_low == stem:
            return True
        if name_low.startswith(stem + "."):
            return True
        if name_low.startswith(stem + "_"):
            return True

    return False


# ═══════════════════════════════════════════
# helpers (atomic write)
# ═══════════════════════════════════════════
def _atomic_write_text(
    path: str,
    content: str,
    encoding: str = "utf-8",
) -> bool:
    """🎯 FIX: نوشتن atomic + fsync + cleanup tmp."""
    tmp = path + ".tmp"

    try:
        parent = os.path.dirname(path)
        if parent:
            with contextlib.suppress(Exception):
                os.makedirs(parent, exist_ok=True)

        with open(tmp, "w", encoding=encoding) as f:
            f.write(content)
            try:
                f.flush()
                os.fsync(f.fileno())
            except (AttributeError, OSError):
                pass

        os.replace(tmp, path)

        # dir fsync (POSIX)
        if parent:
            try:
                dir_fd = os.open(parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass

        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _atomic_write_json(path: str, data: Any) -> bool:
    """🎯 FIX: نوشتن atomic JSON."""
    try:
        content = json.dumps(
            data, indent=2, ensure_ascii=False, default=str
        )
    except Exception as e:
        log_warning(
            f"⚠️ [BACKUP:json] dumps | {type(e).__name__}: {e}"
        )
        return False
    return _atomic_write_text(path, content)


# ═══════════════════════════════════════════
# BackupService
# ═══════════════════════════════════════════
class BackupService:
    """سرویس پشتیبان‌گیری خودکار."""

    def __init__(self):
        self.daily_dir = os.path.join(
            BACKUP_DIR, BACKUP_TYPE_DAILY
        )
        self.full_dir = os.path.join(
            BACKUP_DIR, BACKUP_TYPE_FULL
        )
        self.hash_file = os.path.join(
            BACKUP_DIR, "hashes.json"
        )
        self.last_backup_file = os.path.join(
            BACKUP_DIR, "last_backup.txt"
        )
        self.last_full_backup_file = os.path.join(
            BACKUP_DIR, "last_full_backup.txt"
        )
        self.stats_file = os.path.join(
            BACKUP_DIR, "stats.json"
        )

        self._running = True
        self._stop_event: asyncio.Event | None = None
        self._pending_tasks: list[asyncio.Task] = []
        self._lock = threading.Lock()

        log_info(
            f"🔧 [BACKUP:init] آماده | "
            f"daily_h={DAILY_BACKUP_HOURS} "
            f"keep_daily={KEEP_DAILY_BACKUPS} | "
            f"full_days={FULL_BACKUP_DAYS} "
            f"keep_full={KEEP_FULL_BACKUPS} | "
            f"poke={POKE_INTERVAL_SECONDS}s | "
            f"DB={DB_PATH}"
        )

    # ═══════════════════════════════════════════
    # init
    # ═══════════════════════════════════════════
    def init(self) -> None:
        """ساخت پوشه‌ها + فایل‌های اولیه."""
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            os.makedirs(self.daily_dir, exist_ok=True)
            os.makedirs(self.full_dir, exist_ok=True)
            if not os.path.exists(self.hash_file):
                self._save_hashes({})
            if not os.path.exists(self.stats_file):
                self._save_stats({})
            log_success(
                f"✅ [BACKUP:init] آماده | "
                f"daily_dir={self.daily_dir} | "
                f"full_dir={self.full_dir}"
            )
        except Exception as e:
            log_error(
                f"❌ [BACKUP:init] {type(e).__name__}: {e}"
            )
            raise

    # ═══════════════════════════════════════════
    # stats / hashes (atomic)
    # ═══════════════════════════════════════════
    def _save_stats(self, stats: dict[str, Any]) -> None:
        ok = _atomic_write_json(self.stats_file, stats)
        if not ok:
            log_warning(
                "⚠️ [BACKUP:stats] ذخیره ناموفق"
            )

    def _load_stats(self) -> dict[str, Any]:
        if not os.path.exists(self.stats_file):
            return {}
        try:
            with open(self.stats_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BACKUP:stats] JSON نامعتبر | "
                f"line={e.lineno} — {{}}"
            )
            return {}
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:stats] خواندن | "
                f"{type(e).__name__}: {e}"
            )
            return {}

    def _update_stats(
        self,
        backup_type: str,
        backup_path: str,
        size: int,
    ) -> None:
        try:
            stats = self._load_stats()
            stats.setdefault(backup_type, []).append({
                "path": backup_path,
                "size": size,
                "date": datetime.now().isoformat(),  # noqa: DTZ005
            })
            stats[backup_type] = stats[backup_type][-50:]
            self._save_stats(stats)
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:stats] update | "
                f"{type(e).__name__}: {e}"
            )

    def _get_file_hash(self, filepath: str) -> str | None:
        try:
            stat = os.stat(filepath)
            return f"{stat.st_mtime:.6f}_{stat.st_size}"
        except FileNotFoundError:
            return None
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:hash] {filepath} | "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _load_hashes(self) -> dict[str, str]:
        if not os.path.exists(self.hash_file):
            return {}
        try:
            with open(self.hash_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BACKUP:hashes] JSON نامعتبر | "
                f"line={e.lineno} — {{}}"
            )
            return {}
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:hashes] خواندن | "
                f"{type(e).__name__}: {e}"
            )
            return {}

    def _save_hashes(self, hashes: dict[str, str]) -> None:
        ok = _atomic_write_json(self.hash_file, hashes)
        if not ok:
            log_warning(
                "⚠️ [BACKUP:hashes] ذخیره ناموفق"
            )

    # ═══════════════════════════════════════════
    # فایل‌های کد (با گارد امنیتی)
    # ═══════════════════════════════════════════
    def _get_code_files(self) -> list[str]:
        """لیست فایل‌های کد برای backup کامل.

        امنیت:
          - فایل‌های حساس (`_is_sensitive_filename`) رد می‌شوند.
          - فایل‌های نمونه مجاز هستند.
          - دایرکتوری‌های حساس رد می‌شوند.
        """
        code_files: list[str] = []
        skipped_security = 0
        skipped_ext = 0
        skipped_name = 0

        try:
            for root, dirs, files in os.walk(BASE_DIR):
                # فیلتر دایرکتوری
                dirs[:] = [
                    d for d in dirs if d not in _IGNORE_DIRS
                ]

                for file in files:
                    # ─── چک نام دقیق (سریع‌تر) ───
                    if file in _IGNORE_FILES:
                        skipped_name += 1
                        continue

                    # ─── چک stem حساس ───
                    if _is_sensitive_filename(file):
                        skipped_security += 1
                        continue

                    # ─── چک پسوند ───
                    if any(
                        file.endswith(ext)
                        for ext in _IGNORE_EXTENSIONS
                    ):
                        skipped_ext += 1
                        continue

                    # ─── فقط فایل‌های مجاز ───
                    if file.endswith((
                        ".py", ".json", ".ini", ".txt",
                        ".bat", ".md", ".yml", ".yaml",
                    )):
                        rel = os.path.relpath(
                            os.path.join(root, file),
                            BASE_DIR,
                        )
                        code_files.append(rel)

            if skipped_security or skipped_name or skipped_ext:
                log_info(
                    f"🔒 [BACKUP:code] skip | "
                    f"security={skipped_security} "
                    f"name={skipped_name} "
                    f"ext={skipped_ext} | "
                    f"kept={len(code_files)}"
                )
        except Exception as e:
            log_error(
                f"❌ [BACKUP:code] walk | "
                f"{type(e).__name__}: {e}"
            )
        return sorted(set(code_files))

    # ═══════════════════════════════════════════
    # DB valid + size
    # ═══════════════════════════════════════════
    def _is_db_valid(self) -> bool:
        """بررسی سلامت دیتابیس با PRAGMA integrity_check."""
        if not os.path.exists(DB_PATH):
            log_warning(
                f"⚠️ [BACKUP:db] فایل نیست: {DB_PATH}"
            )
            return False

        conn = None
        try:
            conn = sqlite3.connect(
                DB_PATH, timeout=_DB_INTEGRITY_TIMEOUT_S
            )
            result = conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()
            ok = result and result[0] == "ok"
            if not ok:
                log_error(
                    f"❌ [BACKUP:db] integrity_check | "
                    f"result={result!r}"
                )
            return bool(ok)
        except Exception as e:
            log_error(
                f"❌ [BACKUP:db] integrity | "
                f"{type(e).__name__}: {e}"
            )
            return False
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    def _get_db_size(self) -> int:
        return (
            os.path.getsize(DB_PATH)
            if os.path.exists(DB_PATH) else 0
        )

    # ═══════════════════════════════════════════
    # compress
    # ═══════════════════════════════════════════
    def _compress_folder(
        self, source_dir: str, zip_path: str
    ) -> bool:
        """فشرده‌سازی پوشه در zip."""
        t0 = time.monotonic()
        file_count = 0
        total_bytes = 0

        try:
            with zipfile.ZipFile(
                zip_path, "w", zipfile.ZIP_DEFLATED
            ) as zf:
                for root, _, files in os.walk(source_dir):
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(
                            full_path, source_dir
                        )
                        try:
                            zf.write(full_path, arcname)
                            file_count += 1
                            total_bytes += os.path.getsize(
                                full_path
                            )
                        except FileNotFoundError:
                            continue
                        except Exception as e:
                            log_warning(
                                f"⚠️ [BACKUP:compress] {file} | "
                                f"{type(e).__name__}: {e}"
                            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            zip_size = (
                os.path.getsize(zip_path)
                if os.path.exists(zip_path) else 0
            )
            ratio = (
                (zip_size / total_bytes) if total_bytes else 0
            )
            log_success(
                f"🗜️ [BACKUP:compress] {file_count} فایل | "
                f"raw={total_bytes/1024:.1f}KB → "
                f"zip={zip_size/1024:.1f}KB "
                f"(ratio={ratio:.2f}) | {elapsed_ms:.0f}ms"
            )
            return True
        except zipfile.BadZipFile as e:
            log_error(
                f"❌ [BACKUP:compress] BadZipFile | {e} | "
                f"source={source_dir}"
            )
            return False
        except Exception as e:
            log_error(
                f"❌ [BACKUP:compress] "
                f"{type(e).__name__}: {e} | "
                f"source={source_dir}"
            )
            return False

    # ═══════════════════════════════════════════
    # backup_daily
    # ═══════════════════════════════════════════
    async def backup_daily(self) -> str | None:
        """پشتیبان روزانه دیتابیس."""
        t_all = time.monotonic()
        log_info("▶️ [BACKUP:daily] شروع")

        if not os.path.exists(DB_PATH):
            log_warning(
                f"⚠️ [BACKUP:daily] فایل DB نیست: {DB_PATH}"
            )
            return None

        if not self._is_db_valid():
            log_error(
                "❌ [BACKUP:daily] DB خراب — رد شد"
            )
            return None

        timestamp = datetime.now().strftime(  # noqa: DTZ005
            "%Y-%m-%d_%H-%M-%S_%f"
        )
        backup_folder = os.path.join(
            self.daily_dir, f"db_backup_{timestamp}"
        )

        try:
            os.makedirs(backup_folder, exist_ok=True)
            src_size = self._get_db_size()

            # ─── کپی ───
            t_copy = time.monotonic()
            shutil.copy2(
                DB_PATH,
                os.path.join(backup_folder, "messages.db"),
            )
            copy_ms = (time.monotonic() - t_copy) * 1000

            # ─── INFO ───
            info_content = (
                f"پشتیبان روزانه دیتابیس - {timestamp}\n"
                f"حجم: {src_size:,} bytes\n"
                f"تاریخ: "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"  # noqa: DTZ005
            )
            _atomic_write_text(
                os.path.join(backup_folder, "INFO.txt"),
                info_content,
            )

            # ─── zip ───
            zip_path = os.path.join(
                self.daily_dir, f"db_backup_{timestamp}.zip"
            )
            if not self._compress_folder(
                backup_folder, zip_path
            ):
                return None

            shutil.rmtree(backup_folder, ignore_errors=True)

            zip_size = os.path.getsize(zip_path)
            self._update_stats(
                BACKUP_TYPE_DAILY, zip_path, zip_size
            )

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [BACKUP:daily] "
                f"{os.path.basename(zip_path)} | "
                f"src={src_size/1024:.1f}KB → "
                f"zip={zip_size/1024:.1f}KB | "
                f"copy={copy_ms:.0f}ms total={elapsed_ms:.0f}ms"
            )
            if elapsed_ms > _SLOW_BACKUP_MS:
                log_warning(
                    f"🐌 [BACKUP:daily] کند | "
                    f"{elapsed_ms:.0f}ms"
                )
            return zip_path

        except Exception as e:
            log_error(
                f"❌ [BACKUP:daily] "
                f"{type(e).__name__}: {e}"
            )
            with contextlib.suppress(Exception):
                shutil.rmtree(
                    backup_folder, ignore_errors=True
                )
            return None

    # ═══════════════════════════════════════════
    # backup_full
    # ═══════════════════════════════════════════
    async def backup_full(self) -> str | None:
        """پشتیبان کامل (کد + دیتابیس)."""
        t_all = time.monotonic()
        log_info("▶️ [BACKUP:full] شروع")

        timestamp = datetime.now().strftime(  # noqa: DTZ005
            "%Y-%m-%d_%H-%M-%S_%f"
        )
        backup_folder = os.path.join(
            self.full_dir, f"full_backup_{timestamp}"
        )

        try:
            os.makedirs(backup_folder, exist_ok=True)

            # ─── DB ───
            db_copied = False
            if (
                os.path.exists(DB_PATH)
                and self._is_db_valid()
            ):
                try:
                    shutil.copy2(
                        DB_PATH,
                        os.path.join(
                            backup_folder, "messages.db"
                        ),
                    )
                    db_copied = True
                except Exception as e:
                    log_warning(
                        f"⚠️ [BACKUP:full:db] "
                        f"{type(e).__name__}: {e}"
                    )

            # ─── فایل‌های کد ───
            t_code = time.monotonic()
            code_files = self._get_code_files()
            file_count = 0
            copy_errors = 0

            for filepath in code_files:
                src = os.path.join(BASE_DIR, filepath)
                if not os.path.exists(src):
                    continue
                dest = os.path.join(
                    backup_folder, filepath
                )
                try:
                    os.makedirs(
                        os.path.dirname(dest),
                        exist_ok=True,
                    )
                    shutil.copy2(src, dest)
                    file_count += 1
                except Exception as e:
                    copy_errors += 1
                    if copy_errors <= 3:
                        log_warning(
                            f"⚠️ [BACKUP:full:code] "
                            f"{filepath} | "
                            f"{type(e).__name__}: {e}"
                        )
            code_ms = (time.monotonic() - t_code) * 1000

            # ─── INFO ───
            info_content = (
                f"پشتیبان کامل - {timestamp}\n"
                f"تعداد فایل‌های کد: {file_count}\n"
                f"DB کپی‌شده: "
                f"{'✅' if db_copied else '❌'}\n"
                f"حجم دیتابیس: "
                f"{self._get_db_size():,} bytes\n"
            )
            _atomic_write_text(
                os.path.join(backup_folder, "INFO.txt"),
                info_content,
            )

            # ─── zip ───
            zip_path = os.path.join(
                self.full_dir, f"full_backup_{timestamp}.zip"
            )
            if not self._compress_folder(
                backup_folder, zip_path
            ):
                return None

            shutil.rmtree(backup_folder, ignore_errors=True)

            zip_size = os.path.getsize(zip_path)
            self._update_stats(
                BACKUP_TYPE_FULL, zip_path, zip_size
            )

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [BACKUP:full] "
                f"{os.path.basename(zip_path)} | "
                f"files={file_count} (err={copy_errors}) | "
                f"db={'✅' if db_copied else '❌'} | "
                f"code={code_ms:.0f}ms "
                f"total={elapsed_ms:.0f}ms | "
                f"zip={zip_size/1024:.1f}KB"
            )
            if elapsed_ms > _SLOW_BACKUP_MS:
                log_warning(
                    f"🐌 [BACKUP:full] کند | "
                    f"{elapsed_ms:.0f}ms"
                )
            return zip_path

        except Exception as e:
            log_error(
                f"❌ [BACKUP:full] "
                f"{type(e).__name__}: {e}"
            )
            with contextlib.suppress(Exception):
                shutil.rmtree(
                    backup_folder, ignore_errors=True
                )
            return None

    # ═══════════════════════════════════════════
    # check_code_changes
    # ═══════════════════════════════════════════
    def check_code_changes(self) -> list[str]:
        """مقایسه hash فایل‌ها → تغییرات."""
        t0 = time.monotonic()
        hashes = self._load_hashes()
        changes: list[str] = []
        current_files = self._get_code_files()

        for filepath in current_files:
            full = os.path.join(BASE_DIR, filepath)
            if not os.path.exists(full):
                continue
            new_hash = self._get_file_hash(full)
            old_hash = hashes.get(filepath)
            if old_hash is None:
                changes.append(f"جدید: {filepath}")
            elif new_hash != old_hash:
                changes.append(f"تغییر: {filepath}")

        # فایل‌های حذف‌شده
        for filepath in list(hashes.keys()):
            if filepath not in current_files:
                changes.append(f"حذف: {filepath}")
                del hashes[filepath]

        # به‌روزرسانی
        for filepath in current_files:
            full = os.path.join(BASE_DIR, filepath)
            if os.path.exists(full):
                h = self._get_file_hash(full)
                if h:
                    hashes[filepath] = h

        self._save_hashes(hashes)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if changes:
            preview = ", ".join(
                c[:60] for c in changes[:3]
            )
            more = (
                f" و {len(changes) - 3} دیگر"
                if len(changes) > 3 else ""
            )
            log_info(
                f"🔍 [BACKUP:changes] {len(changes)} تغییر "
                f"از {len(current_files)} فایل | "
                f"{preview}{more} | {elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"🔍 [BACKUP:changes] بدون تغییر | "
                f"{len(current_files)} فایل چک شد | "
                f"{elapsed_ms:.0f}ms"
            )
        if elapsed_ms > _SLOW_HASH_MS:
            log_warning(
                f"🐌 [BACKUP:changes] کند | "
                f"{elapsed_ms:.0f}ms"
            )
        return changes

    # ═══════════════════════════════════════════
    # should_backup
    # ═══════════════════════════════════════════
    def _should_backup_daily(self) -> bool:
        if not os.path.exists(self.last_backup_file):
            return True
        try:
            with open(self.last_backup_file) as f:
                last = float(f.read().strip())
            elapsed_h = (time.time() - last) / 3600
            return elapsed_h >= DAILY_BACKUP_HOURS
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:should_daily] "
                f"{type(e).__name__}: {e} — پیش‌فرض True"
            )
            return True

    def _mark_daily_done(self) -> None:
        ok = _atomic_write_text(
            self.last_backup_file, str(time.time())
        )
        if not ok:
            log_warning(
                "⚠️ [BACKUP:mark_daily] ذخیره ناموفق"
            )

    def _should_backup_full(self) -> bool:
        if not os.path.exists(self.last_full_backup_file):
            return True
        try:
            with open(self.last_full_backup_file) as f:
                last = float(f.read().strip())
            elapsed_days = (time.time() - last) / 86400
            return elapsed_days >= FULL_BACKUP_DAYS
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:should_full] "
                f"{type(e).__name__}: {e} — پیش‌فرض True"
            )
            return True

    def _mark_full_done(self) -> None:
        ok = _atomic_write_text(
            self.last_full_backup_file, str(time.time())
        )
        if not ok:
            log_warning(
                "⚠️ [BACKUP:mark_full] ذخیره ناموفق"
            )

    # ═══════════════════════════════════════════
    # cleanup
    # ═══════════════════════════════════════════
    def _cleanup_old(self) -> None:
        """حذف بکاپ‌های اضافی (چرخش)."""
        t0 = time.monotonic()
        removed_daily: list[str] = []
        removed_full: list[str] = []

        # 🎯 min=1
        keep_daily = max(1, KEEP_DAILY_BACKUPS)
        keep_full = max(1, KEEP_FULL_BACKUPS)

        try:
            daily = sorted(
                f for f in os.listdir(self.daily_dir)
                if f.endswith(".zip")
            )
            while len(daily) > keep_daily:
                oldest = daily.pop(0)
                p = os.path.join(self.daily_dir, oldest)
                try:
                    os.remove(p)
                    removed_daily.append(oldest)
                except FileNotFoundError:
                    continue
                except Exception as e:
                    log_warning(
                        f"⚠️ [BACKUP:cleanup:daily] "
                        f"{oldest} | "
                        f"{type(e).__name__}: {e}"
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:cleanup:daily] "
                f"{type(e).__name__}: {e}"
            )

        try:
            full = sorted(
                f for f in os.listdir(self.full_dir)
                if f.endswith(".zip")
            )
            while len(full) > keep_full:
                oldest = full.pop(0)
                p = os.path.join(self.full_dir, oldest)
                try:
                    os.remove(p)
                    removed_full.append(oldest)
                except FileNotFoundError:
                    continue
                except Exception as e:
                    log_warning(
                        f"⚠️ [BACKUP:cleanup:full] "
                        f"{oldest} | "
                        f"{type(e).__name__}: {e}"
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:cleanup:full] "
                f"{type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if removed_daily or removed_full:
            log_info(
                f"🧹 [BACKUP:cleanup] "
                f"daily={len(removed_daily)} "
                f"full={len(removed_full)} | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"ℹ️ [BACKUP:cleanup] چیزی برای حذف نبود | "
                f"{elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # run_periodic
    # ═══════════════════════════════════════════
    async def run_periodic(self) -> None:
        """حلقهٔ periodic با بیداری سریع در stop."""
        self._stop_event = asyncio.Event()

        log_info(
            f"▶️ [BACKUP:periodic] شروع | "
            f"هر {POKE_INTERVAL_SECONDS}s"
        )
        iteration = 0

        while self._running:
            iteration += 1
            t0 = time.monotonic()
            try:
                changes = self.check_code_changes()

                if changes and self._should_backup_full():
                    log_info(
                        f"📦 [BACKUP:periodic] "
                        f"{len(changes)} تغییر — "
                        f"شروع backup کامل"
                    )
                    if await self.backup_full():
                        self._mark_full_done()
                elif changes:
                    log_info(
                        f"ℹ️ [BACKUP:periodic] "
                        f"{len(changes)} تغییر ولی "
                        f"هنوز زمان backup کامل نشده "
                        f"(هر {FULL_BACKUP_DAYS}روز)"
                    )

                if self._should_backup_daily():
                    log_info(
                        "📦 [BACKUP:periodic] "
                        "شروع backup روزانه"
                    )
                    if await self.backup_daily():
                        self._mark_daily_done()
                else:
                    log_info(
                        f"ℹ️ [BACKUP:periodic] backup روزانه "
                        f"هنوز لازم نیست "
                        f"(هر {DAILY_BACKUP_HOURS}h)"
                    )

                self._cleanup_old()

                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"✔️ [BACKUP:periodic] "
                    f"iter#{iteration} | {elapsed_ms:.0f}ms | "
                    f"next={POKE_INTERVAL_SECONDS}s"
                )

            except asyncio.CancelledError:
                log_info(
                    f"🛑 [BACKUP:periodic] cancel "
                    f"(iter#{iteration})"
                )
                raise
            except Exception as e:
                log_error(
                    f"❌ [BACKUP:periodic] "
                    f"iter#{iteration} | "
                    f"{type(e).__name__}: {e}"
                )

            # ─── sleep با بیداری سریع ───
            if self._stop_event is not None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=POKE_INTERVAL_SECONDS,
                    )
                    break  # stop شد
                except TimeoutError:
                    pass
            else:
                await asyncio.sleep(POKE_INTERVAL_SECONDS)

        log_info(
            f"🛑 [BACKUP:periodic] حلقه متوقف شد | "
            f"iterations={iteration}"
        )

    def stop(self) -> None:
        """توقف حلقه."""
        self._running = False
        if self._stop_event is not None:
            with contextlib.suppress(Exception):
                self._stop_event.set()
        log_info("🛑 [BACKUP:stop] درخواست توقف")

    # ═══════════════════════════════════════════
    # manual triggers
    # ═══════════════════════════════════════════
    async def manual_full(self) -> str | None:
        log_info(
            "🔧 [BACKUP:manual] full — درخواست دستی"
        )
        return await self.backup_full()

    async def manual_daily(self) -> str | None:
        log_info(
            "🔧 [BACKUP:manual] daily — درخواست دستی"
        )
        return await self.backup_daily()

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        stats = self._load_stats()
        hashes = self._load_hashes()

        # شمارش فایل‌های بکاپ
        daily_count = 0
        full_count = 0
        with contextlib.suppress(Exception):
            daily_count = len([
                f for f in os.listdir(self.daily_dir)
                if f.endswith(".zip")
            ])
        with contextlib.suppress(Exception):
            full_count = len([
                f for f in os.listdir(self.full_dir)
                if f.endswith(".zip")
            ])

        return {
            "running": self._running,
            "db_path": DB_PATH,
            "backup_dir": BACKUP_DIR,
            "daily_count": daily_count,
            "full_count": full_count,
            "keep_daily": KEEP_DAILY_BACKUPS,
            "keep_full": KEEP_FULL_BACKUPS,
            "daily_hours": DAILY_BACKUP_HOURS,
            "full_days": FULL_BACKUP_DAYS,
            "poke_seconds": POKE_INTERVAL_SECONDS,
            "hashes_count": len(hashes),
            "sensitive_stems": list(_SENSITIVE_STEMS),
            "stats_keys": list(stats.keys()),
        }

    def __repr__(self) -> str:
        return (
            f"<BackupService daily={KEEP_DAILY_BACKUPS} "
            f"full={KEEP_FULL_BACKUPS} "
            f"poke={POKE_INTERVAL_SECONDS}s>"
        )


# ═══════════════════════════════════════════
# راه‌انداز
# ═══════════════════════════════════════════
async def start_backup_service() -> BackupService:
    """ساخت BackupService + راه‌اندازی حلقهٔ periodic."""
    log_info("🚀 [BACKUP:start] راه‌اندازی...")

    try:
        backup = BackupService()
        backup.init()
    except Exception as e:
        log_error(
            f"❌ [BACKUP:start] ساخت ناموفق | "
            f"{type(e).__name__}: {e}"
        )
        raise

    try:
        # 🎯 FIX: نگه‌داری reference
        task = asyncio.create_task(
            backup.run_periodic(),
            name="backup_periodic",
        )
        backup._pending_tasks.append(task)
        log_success(
            f"✅ [BACKUP:start] حلقه راه افتاد | "
            f"daily={DAILY_BACKUP_HOURS}h | "
            f"full={FULL_BACKUP_DAYS}d | "
            f"keep(daily/full)={KEEP_DAILY_BACKUPS}/"
            f"{KEEP_FULL_BACKUPS}"
        )
    except Exception as e:
        log_error(
            f"❌ [BACKUP:start] create_task | "
            f"{type(e).__name__}: {e}"
        )
    return backup


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "BACKUP_DIR",
    "BACKUP_TYPE_DAILY",
    "BACKUP_TYPE_FULL",
    "DAILY_BACKUP_HOURS",
    "DB_PATH",
    "FULL_BACKUP_DAYS",
    "KEEP_DAILY_BACKUPS",
    "KEEP_FULL_BACKUPS",
    "POKE_INTERVAL_SECONDS",
    "BackupService",
    "_is_sensitive_filename",
    "start_backup_service",
]