# core/services/backup_service.py
"""
سیستم پشتیبان‌گیری هوشمند.

قابلیت‌ها:
  - backup_daily: کپی + zip دیتابیس (سبک، برای حفظ روزانه)
  - backup_full:  کپی کد + دیتابیس در یک zip (برای snapshot کامل)
  - check_code_changes: مقایسه hash (mtime+size) فایل‌ها → تشخیص تغییر
  - run_periodic: حلقهٔ hourly که خودش تصمیم می‌گیرد چه زمانی backup بزند
  - cleanup: چرخش بکاپ‌های قدیمی

تنظیمات از .env:
  DAILY_BACKUP_HOURS   (پیش‌فرض 24)  → فاصلهٔ backup روزانه
  KEEP_DAILY_BACKUPS   (پیش‌فرض 30)  → حداکثر بکاپ روزانه نگه‌داشته‌شده
  KEEP_FULL_BACKUPS    (پیش‌فرض 10)  → حداکثر بکاپ کامل
  FULL_BACKUP_DAYS     (پیش‌فرض 7)   → فاصلهٔ backup کامل (روز)
  BACKUP_POKE_SECONDS  (پیش‌فرض 3600) → فاصلهٔ حلقهٔ بررسی (ثانیه)

نسخهٔ ۵ (این نسخه):
  - 🔴 FIX امنیتی مهم: چک فایل‌های حساس حالا با تابع
    `_is_sensitive_filename()` انجام می‌شود (به‌جای prefix با نقطه).

    باگ قبلی: `_IGNORE_PREFIXES` شامل `"secrets."` و `"credentials."`
    بود (با نقطه). ولی این فایل‌ها از backup رد نمی‌شدند:
      ✗ secrets_backup.json    (با _ نه .)
      ✗ credentials_old.json   (با _ نه .)
      ✗ auth_token.txt         (اصلاً پوشش نداشت)
      ✗ token.json             (اصلاً پوشش نداشت)
      ✗ api_key.txt            (اصلاً پوشش نداشت)
    حالا همه پوشش داده می‌شوند.

  - 🎯 استثنا: فایل‌های نمونه (.example, .sample, .template) مجاز
    هستند (چون معمولاً حاوی secret نیستند و برای backup کامل مفیدن).
    مثال: `.env.example` کپی می‌شود ✅

  - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime

from config import BASE_DIR, DB_DIR, STORAGE_DIR
from core.kernel.logger import log_error, log_info, log_success, log_warning

# ═══════════════════════════════════════════
# تنظیمات (از env)
# ═══════════════════════════════════════════
DAILY_BACKUP_HOURS = max(1, int(os.environ.get("DAILY_BACKUP_HOURS", 24)))
KEEP_DAILY_BACKUPS = max(1, int(os.environ.get("KEEP_DAILY_BACKUPS", 30)))
KEEP_FULL_BACKUPS = max(1, int(os.environ.get("KEEP_FULL_BACKUPS", 10)))
FULL_BACKUP_DAYS = max(1, int(os.environ.get("FULL_BACKUP_DAYS", 7)))

# فاصلهٔ حلقهٔ periodic (برای تست‌ها قابل override)
POKE_INTERVAL_SECONDS = int(os.environ.get("BACKUP_POKE_SECONDS", 3600))

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_BACKUP_MS = 30_000
_SLOW_HASH_MS = 5_000

# مسیرها
DB_PATH = os.path.join(DB_DIR, "messages.db")
BACKUP_DIR = os.path.join(STORAGE_DIR, "backups")


# ═══════════════════════════════════════════
# 🔴 لیست‌های امنیتی (فایل‌های حساس)
# ═══════════════════════════════════════════
_IGNORE_DIRS: frozenset = frozenset({
    "__pycache__", "backups", "storage", "data", ".git",
    "venv", ".venv", "env", ".env",
    "reports", "logs", "cache", "sessions", "media",
    "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".idea", ".vscode",
})

_IGNORE_EXTENSIONS: frozenset = frozenset({
    ".pyc", ".pyo", ".pyd",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".log", ".bale", ".zip", ".7z", ".rar", ".pid",
    # 🔴 پسوندهای حساس (کلید/گواهی)
    ".key", ".pem", ".crt", ".p12", ".pfx", ".jks", ".keystore",
})

_IGNORE_FILES: frozenset = frozenset({
    # فایل‌های خود backup
    "hashes.json", "last_backup.txt", "last_full_backup.txt",
    "bot.pid", "stats.json", "products_list.txt", "messages.txt",
    # 🔴 فایل‌های حساس دقیق
    ".env",
    "secrets.json", "credentials.json", "auth.json",
    ".git-credentials", ".netrc", "id_rsa", "id_dsa",
    "known_hosts", "config.json",
})

# 🔴 «stems» حساس — چک با تابع _is_sensitive_filename
# هر stem: نام دقیق، یا با `.` یا با `_` ادامه می‌یابد
#
# مثال‌هایی که با این لیست پوشش داده می‌شوند:
#   `.env`               → مطابقت دقیق
#   `.env.local`         → `.env.` prefix
#   `.env_backup`        → `.env_` prefix
#   `secrets.json`       → `secrets.` prefix
#   `secrets_backup.json`→ `secrets_` prefix
#   `credentials_old.json`→ `credentials_` prefix
#   `secret_key.txt`     → `secret_` prefix
#   `private_config.json`→ `private_` prefix
#   `auth_token.txt`     → `auth_` prefix
#   `token.json`         → `token.` prefix
#   `api_key.txt`        → `api_key.` prefix
#   `apikey.txt`         → `apikey.` prefix
_SENSITIVE_STEMS: tuple = (
    ".env",
    "secrets",
    "secret",
    "credentials",
    "private",
    "auth",
    "token",
    "api_key",
    "apikey",
)

# 🔴 پسوندهای فایل‌های «نمونه» — مجاز هستند
# (این‌ها حاوی secret واقعی نیستند، فقط قالب هستن)
# مثال: `.env.example`, `secrets.sample`, `config.template`
_ALLOWED_SAMPLE_SUFFIXES: tuple = (
    ".example",
    ".sample",
    ".template",
    ".dist",
    ".default",
)


def _is_sensitive_filename(name: str) -> bool:
    """
    آیا نام فایل حساس است؟ (نباید در backup کپی شود)

    🔴 FIX نسخهٔ ۵: این تابع جایگزین منطق قدیمیِ prefix-based شد.

    منطق:
      ۱) اگه فایل نمونه باشد (ends with .example/.sample/...) → False
      ۲) اگه نام دقیقاً یکی از stemها باشد → True
      ۳) اگه با `{stem}.` شروع شود → True
      ۴) اگه با `{stem}_` شروع شود → True
      ۵) وگرنه → False

    Args:
        name: نام فایل (بدون مسیر).

    Returns:
        True اگه حساس است (نباید backup شود).
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
# BackupService
# ═══════════════════════════════════════════
class BackupService:
    """سرویس پشتیبان‌گیری خودکار."""

    def __init__(self):
        self.daily_dir = os.path.join(BACKUP_DIR, "daily")
        self.full_dir = os.path.join(BACKUP_DIR, "full")
        self.hash_file = os.path.join(BACKUP_DIR, "hashes.json")
        self.last_backup_file = os.path.join(BACKUP_DIR, "last_backup.txt")
        self.last_full_backup_file = os.path.join(
            BACKUP_DIR, "last_full_backup.txt"
        )
        self.stats_file = os.path.join(BACKUP_DIR, "stats.json")
        self._running = True

        log_info(
            f"🔧 [BACKUP:init] BackupService ساخته شد | "
            f"daily_h={DAILY_BACKUP_HOURS} keep_daily={KEEP_DAILY_BACKUPS} | "
            f"full_days={FULL_BACKUP_DAYS} keep_full={KEEP_FULL_BACKUPS} | "
            f"poke={POKE_INTERVAL_SECONDS}s | "
            f"DB={DB_PATH}"
        )

    # ═══════════════════════════════════════════
    # init
    # ═══════════════════════════════════════════
    def init(self) -> None:
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
                f"daily_dir={self.daily_dir} | full_dir={self.full_dir}"
            )
        except Exception as e:
            log_error(
                f"❌ [BACKUP:init] {type(e).__name__}: {e}"
            )
            raise

    # ═══════════════════════════════════════════
    # stats / hashes (با error handling)
    # ═══════════════════════════════════════════
    def _save_stats(self, stats: dict) -> None:
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:stats] ذخیره | {type(e).__name__}: {e}"
            )

    def _load_stats(self) -> dict:
        if not os.path.exists(self.stats_file):
            return {}
        try:
            with open(self.stats_file, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BACKUP:stats] JSON نامعتبر | "
                f"line={e.lineno} | {e.msg} — {{}} استفاده می‌شود"
            )
            return {}
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:stats] خواندن | {type(e).__name__}: {e}"
            )
            return {}

    def _update_stats(
        self, backup_type: str, backup_path: str, size: int
    ) -> None:
        try:
            stats = self._load_stats()
            stats.setdefault(backup_type, []).append({
                "path": backup_path,
                "size": size,
                "date": datetime.now().isoformat(),
            })
            stats[backup_type] = stats[backup_type][-50:]
            self._save_stats(stats)
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:stats] update | {type(e).__name__}: {e}"
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

    def _load_hashes(self) -> dict:
        if not os.path.exists(self.hash_file):
            return {}
        try:
            with open(self.hash_file, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BACKUP:hashes] JSON نامعتبر | "
                f"line={e.lineno} — {{}} استفاده می‌شود"
            )
            return {}
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:hashes] خواندن | {type(e).__name__}: {e}"
            )
            return {}

    def _save_hashes(self, hashes: dict) -> None:
        try:
            with open(self.hash_file, "w", encoding="utf-8") as f:
                json.dump(hashes, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:hashes] ذخیره | {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # 🔴 فایل‌های کد (با گارد امنیتی)
    # ═══════════════════════════════════════════
    def _get_code_files(self) -> list[str]:
        """
        لیست فایل‌های کد که باید در backup کامل کپی شوند.

        🔴 امنیت (نسخهٔ ۵):
          - فایل‌های حساس (با `_is_sensitive_filename`) رد می‌شوند.
            این شامل: `.env.*`, `secrets*`, `credentials*`,
            `secret*`, `private*`, `auth*`, `token*`, `api_key*`
          - فایل‌های نمونه (.example, .sample, ...) مجاز هستند.
          - دایرکتوری‌های حساس (venv, .git, storage, ...) رد می‌شوند.
        """
        code_files: list[str] = []
        skipped_security = 0
        skipped_ext = 0
        skipped_name = 0

        try:
            for root, dirs, files in os.walk(BASE_DIR):
                # 🔴 فیلتر دایرکتوری‌های حساس
                dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]

                for file in files:
                    # 🔴 چک stemهای حساس (نسخهٔ ۵ — تابع)
                    if _is_sensitive_filename(file):
                        skipped_security += 1
                        continue

                    # چک نام فایل حساس (exact)
                    if file in _IGNORE_FILES:
                        skipped_name += 1
                        continue

                    # چک پسوند حساس
                    if any(
                        file.endswith(ext)
                        for ext in _IGNORE_EXTENSIONS
                    ):
                        skipped_ext += 1
                        continue

                    # فقط فایل‌های مجاز
                    if file.endswith((
                        ".py", ".json", ".ini", ".txt",
                        ".bat", ".md", ".yml", ".yaml",
                    )):
                        rel = os.path.relpath(
                            os.path.join(root, file), BASE_DIR
                        )
                        code_files.append(rel)

            # لاگ خلاصه (فقط اگر فایلی skip شده)
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
                f"❌ [BACKUP:code] walk | {type(e).__name__}: {e}"
            )
        return sorted(set(code_files))

    # ═══════════════════════════════════════════
    # DB valid + size
    # ═══════════════════════════════════════════
    def _is_db_valid(self) -> bool:
        """بررسی سلامت دیتابیس با PRAGMA integrity_check."""
        if not os.path.exists(DB_PATH):
            log_warning(f"⚠️ [BACKUP:db] فایل نیست: {DB_PATH}")
            return False
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                result = conn.execute("PRAGMA integrity_check").fetchone()
            finally:
                conn.close()
            ok = result and result[0] == "ok"
            if not ok:
                log_error(
                    f"❌ [BACKUP:db] integrity_check | result={result!r}"
                )
            return bool(ok)
        except Exception as e:
            log_error(
                f"❌ [BACKUP:db] integrity | {type(e).__name__}: {e}"
            )
            return False

    def _get_db_size(self) -> int:
        return os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

    # ═══════════════════════════════════════════
    # compress
    # ═══════════════════════════════════════════
    def _compress_folder(self, source_dir: str, zip_path: str) -> bool:
        """فشرده‌سازی یک پوشه در zip. Returns: True/False."""
        t0 = time.monotonic()
        file_count = 0
        total_bytes = 0
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(source_dir):
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, source_dir)
                        try:
                            zf.write(full_path, arcname)
                            file_count += 1
                            total_bytes += os.path.getsize(full_path)
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
            ratio = (zip_size / total_bytes) if total_bytes else 0
            log_success(
                f"🗜️ [BACKUP:compress] {file_count} فایل | "
                f"raw={total_bytes/1024:.1f}KB → "
                f"zip={zip_size/1024:.1f}KB "
                f"(ratio={ratio:.2f}) | {elapsed_ms:.0f}ms"
            )
            return True
        except Exception as e:
            log_error(
                f"❌ [BACKUP:compress] {type(e).__name__}: {e} | "
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
            log_warning(f"⚠️ [BACKUP:daily] فایل DB نیست: {DB_PATH}")
            return None
        if not self._is_db_valid():
            log_error("❌ [BACKUP:daily] DB خراب — پشتیبان‌گیری رد شد")
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_folder = os.path.join(
            self.daily_dir, f"db_backup_{timestamp}"
        )

        try:
            os.makedirs(backup_folder, exist_ok=True)
            src_size = self._get_db_size()

            t_copy = time.monotonic()
            shutil.copy2(DB_PATH, os.path.join(backup_folder, "messages.db"))
            copy_ms = (time.monotonic() - t_copy) * 1000

            # گزارش داخل پوشه
            report_path = os.path.join(backup_folder, "INFO.txt")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"پشتیبان روزانه دیتابیس - {timestamp}\n")
                f.write(f"حجم: {src_size:,} bytes\n")
                f.write(
                    f"تاریخ: "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )

            zip_path = os.path.join(
                self.daily_dir, f"db_backup_{timestamp}.zip"
            )
            if not self._compress_folder(backup_folder, zip_path):
                return None

            shutil.rmtree(backup_folder, ignore_errors=True)

            zip_size = os.path.getsize(zip_path)
            self._update_stats("daily", zip_path, zip_size)

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [BACKUP:daily] {os.path.basename(zip_path)} | "
                f"src={src_size/1024:.1f}KB → zip={zip_size/1024:.1f}KB | "
                f"copy={copy_ms:.0f}ms total={elapsed_ms:.0f}ms"
            )
            if elapsed_ms > _SLOW_BACKUP_MS:
                log_warning(
                    f"🐌 [BACKUP:daily] کند | {elapsed_ms:.0f}ms"
                )
            return zip_path

        except Exception as e:
            log_error(
                f"❌ [BACKUP:daily] {type(e).__name__}: {e}"
            )
            try:
                shutil.rmtree(backup_folder, ignore_errors=True)
            except Exception:
                pass
            return None

    # ═══════════════════════════════════════════
    # backup_full
    # ═══════════════════════════════════════════
    async def backup_full(self) -> str | None:
        """پشتیبان کامل (کد + دیتابیس)."""
        t_all = time.monotonic()
        log_info("▶️ [BACKUP:full] شروع")

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_folder = os.path.join(
            self.full_dir, f"full_backup_{timestamp}"
        )

        try:
            os.makedirs(backup_folder, exist_ok=True)

            # ─── DB ───
            db_copied = False
            if os.path.exists(DB_PATH) and self._is_db_valid():
                try:
                    shutil.copy2(
                        DB_PATH,
                        os.path.join(backup_folder, "messages.db"),
                    )
                    db_copied = True
                except Exception as e:
                    log_warning(
                        f"⚠️ [BACKUP:full:db] {type(e).__name__}: {e}"
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
                dest = os.path.join(backup_folder, filepath)
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)
                    file_count += 1
                except Exception as e:
                    copy_errors += 1
                    if copy_errors <= 3:
                        log_warning(
                            f"⚠️ [BACKUP:full:code] {filepath} | "
                            f"{type(e).__name__}: {e}"
                        )
            code_ms = (time.monotonic() - t_code) * 1000

            # ─── گزارش ───
            report = os.path.join(backup_folder, "INFO.txt")
            with open(report, "w", encoding="utf-8") as f:
                f.write(f"پشتیبان کامل - {timestamp}\n")
                f.write(f"تعداد فایل‌های کد: {file_count}\n")
                f.write(f"DB کپی‌شده: {'✅' if db_copied else '❌'}\n")
                f.write(f"حجم دیتابیس: {self._get_db_size():,} bytes\n")

            # ─── zip ───
            zip_path = os.path.join(
                self.full_dir, f"full_backup_{timestamp}.zip"
            )
            if not self._compress_folder(backup_folder, zip_path):
                return None
            shutil.rmtree(backup_folder, ignore_errors=True)

            zip_size = os.path.getsize(zip_path)
            self._update_stats("full", zip_path, zip_size)

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [BACKUP:full] {os.path.basename(zip_path)} | "
                f"files={file_count} (err={copy_errors}) | "
                f"db={'✅' if db_copied else '❌'} | "
                f"code={code_ms:.0f}ms total={elapsed_ms:.0f}ms | "
                f"zip={zip_size/1024:.1f}KB"
            )
            if elapsed_ms > _SLOW_BACKUP_MS:
                log_warning(
                    f"🐌 [BACKUP:full] کند | {elapsed_ms:.0f}ms"
                )
            return zip_path

        except Exception as e:
            log_error(
                f"❌ [BACKUP:full] {type(e).__name__}: {e}"
            )
            try:
                shutil.rmtree(backup_folder, ignore_errors=True)
            except Exception:
                pass
            return None

    # ═══════════════════════════════════════════
    # check_code_changes
    # ═══════════════════════════════════════════
    def check_code_changes(self) -> list[str]:
        """مقایسه hash فایل‌ها → لیست تغییرات."""
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

        # به‌روزرسانی hashes
        for filepath in current_files:
            full = os.path.join(BASE_DIR, filepath)
            if os.path.exists(full):
                h = self._get_file_hash(full)
                if h:
                    hashes[filepath] = h

        self._save_hashes(hashes)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if changes:
            preview = ", ".join(c[:60] for c in changes[:3])
            more = f" و {len(changes) - 3} دیگر" if len(changes) > 3 else ""
            log_info(
                f"🔍 [BACKUP:changes] {len(changes)} تغییر از "
                f"{len(current_files)} فایل | {preview}{more} | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"🔍 [BACKUP:changes] بدون تغییر | "
                f"{len(current_files)} فایل چک شد | {elapsed_ms:.0f}ms"
            )
        if elapsed_ms > _SLOW_HASH_MS:
            log_warning(
                f"🐌 [BACKUP:changes] کند | {elapsed_ms:.0f}ms"
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
                f"⚠️ [BACKUP:should_daily] {type(e).__name__}: {e} — "
                f"پیش‌فرض True"
            )
            return True

    def _mark_daily_done(self) -> None:
        try:
            with open(self.last_backup_file, "w") as f:
                f.write(str(time.time()))
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:mark_daily] {type(e).__name__}: {e}"
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
                f"⚠️ [BACKUP:should_full] {type(e).__name__}: {e} — "
                f"پیش‌فرض True"
            )
            return True

    def _mark_full_done(self) -> None:
        try:
            with open(self.last_full_backup_file, "w") as f:
                f.write(str(time.time()))
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:mark_full] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # cleanup
    # ═══════════════════════════════════════════
    def _cleanup_old(self) -> None:
        """حذف بکاپ‌های اضافی (چرخش)."""
        t0 = time.monotonic()
        removed_daily: list[str] = []
        removed_full: list[str] = []

        try:
            daily = sorted(
                f for f in os.listdir(self.daily_dir) if f.endswith(".zip")
            )
            while len(daily) > KEEP_DAILY_BACKUPS:
                oldest = daily.pop(0)
                p = os.path.join(self.daily_dir, oldest)
                try:
                    os.remove(p)
                    removed_daily.append(oldest)
                except FileNotFoundError:
                    continue
                except Exception as e:
                    log_warning(
                        f"⚠️ [BACKUP:cleanup:daily] {oldest} | "
                        f"{type(e).__name__}: {e}"
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:cleanup:daily] {type(e).__name__}: {e}"
            )

        try:
            full = sorted(
                f for f in os.listdir(self.full_dir) if f.endswith(".zip")
            )
            while len(full) > KEEP_FULL_BACKUPS:
                oldest = full.pop(0)
                p = os.path.join(self.full_dir, oldest)
                try:
                    os.remove(p)
                    removed_full.append(oldest)
                except FileNotFoundError:
                    continue
                except Exception as e:
                    log_warning(
                        f"⚠️ [BACKUP:cleanup:full] {oldest} | "
                        f"{type(e).__name__}: {e}"
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [BACKUP:cleanup:full] {type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if removed_daily or removed_full:
            log_info(
                f"🧹 [BACKUP:cleanup] daily={len(removed_daily)} "
                f"full={len(removed_full)} | {elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"ℹ️ [BACKUP:cleanup] چیزی برای حذف نبود | {elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # run_periodic
    # ═══════════════════════════════════════════
    async def run_periodic(self) -> None:
        log_info(
            f"▶️ [BACKUP:periodic] شروع | هر {POKE_INTERVAL_SECONDS}s"
        )
        iteration = 0
        while self._running:
            iteration += 1
            t0 = time.monotonic()
            try:
                changes = self.check_code_changes()

                if changes and self._should_backup_full():
                    log_info(
                        f"📦 [BACKUP:periodic] {len(changes)} تغییر — "
                        f"شروع backup کامل"
                    )
                    if await self.backup_full():
                        self._mark_full_done()
                elif changes:
                    log_info(
                        f"ℹ️ [BACKUP:periodic] {len(changes)} تغییر ولی "
                        f"هنوز زمان backup کامل نشده "
                        f"(هر {FULL_BACKUP_DAYS}روز)"
                    )

                if self._should_backup_daily():
                    log_info("📦 [BACKUP:periodic] شروع backup روزانه")
                    if await self.backup_daily():
                        self._mark_daily_done()
                else:
                    log_info(
                        f"ℹ️ [BACKUP:periodic] backup روزانه هنوز لازم نیست "
                        f"(هر {DAILY_BACKUP_HOURS}h)"
                    )

                self._cleanup_old()

                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"✔️ [BACKUP:periodic] iter#{iteration} | "
                    f"{elapsed_ms:.0f}ms | next={POKE_INTERVAL_SECONDS}s"
                )

            except asyncio.CancelledError:
                log_info(
                    f"🛑 [BACKUP:periodic] cancel (iter#{iteration})"
                )
                raise
            except Exception as e:
                log_error(
                    f"❌ [BACKUP:periodic] iter#{iteration} | "
                    f"{type(e).__name__}: {e}"
                )

            await asyncio.sleep(POKE_INTERVAL_SECONDS)

    def stop(self):
        self._running = False
        log_info("🛑 [BACKUP:stop] درخواست توقف")

    # ═══════════════════════════════════════════
    # manual triggers
    # ═══════════════════════════════════════════
    async def manual_full(self) -> str | None:
        log_info("🔧 [BACKUP:manual] full — درخواست دستی")
        return await self.backup_full()

    async def manual_daily(self) -> str | None:
        log_info("🔧 [BACKUP:manual] daily — درخواست دستی")
        return await self.backup_daily()

    def get_stats(self) -> dict:
        return self._load_stats()


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
        asyncio.create_task(backup.run_periodic())
        log_success(
            f"✅ [BACKUP:start] حلقه راه افتاد | "
            f"daily={DAILY_BACKUP_HOURS}h | "
            f"full={FULL_BACKUP_DAYS}d | "
            f"keep(daily/full)={KEEP_DAILY_BACKUPS}/{KEEP_FULL_BACKUPS}"
        )
    except Exception as e:
        log_error(
            f"❌ [BACKUP:start] create_task | "
            f"{type(e).__name__}: {e}"
        )
    return backup