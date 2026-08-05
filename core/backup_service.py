# core/backup_service.py
"""
سیستم پشتیبان‌گیری هوشمند - نسخه نهایی
"""

import os
import json
import time
import shutil
import sqlite3
import asyncio
import zipfile
from datetime import datetime
from typing import List, Optional
from config import BASE_DIR, STORAGE_DIR, DB_DIR
from core.logger import log_info, log_success, log_error, log_warning

# ==================================================
# تنظیمات پیش‌فرض (قابل تنظیم از env)
# ==================================================
DAILY_BACKUP_HOURS = int(os.environ.get("DAILY_BACKUP_HOURS", 24))
KEEP_DAILY_BACKUPS = int(os.environ.get("KEEP_DAILY_BACKUPS", 30))
KEEP_FULL_BACKUPS = int(os.environ.get("KEEP_FULL_BACKUPS", 10))
FULL_BACKUP_DAYS = int(os.environ.get("FULL_BACKUP_DAYS", 7))

# مسیرها
DB_PATH = os.path.join(DB_DIR, "messages.db")
BACKUP_DIR = os.path.join(STORAGE_DIR, "backups")


class BackupService:
    """سرویس پشتیبان‌گیری خودکار"""

    def __init__(self):
        self.daily_dir = os.path.join(BACKUP_DIR, "daily")
        self.full_dir = os.path.join(BACKUP_DIR, "full")
        self.hash_file = os.path.join(BACKUP_DIR, "hashes.json")
        self.last_backup_file = os.path.join(BACKUP_DIR, "last_backup.txt")
        self.last_full_backup_file = os.path.join(BACKUP_DIR, "last_full_backup.txt")
        self.stats_file = os.path.join(BACKUP_DIR, "stats.json")
        self._running = True

    def init(self) -> None:
        """راه‌اندازی پوشه‌های پشتیبان"""
        os.makedirs(BACKUP_DIR, exist_ok=True)
        os.makedirs(self.daily_dir, exist_ok=True)
        os.makedirs(self.full_dir, exist_ok=True)

        if not os.path.exists(self.hash_file):
            self._save_hashes({})

        if not os.path.exists(self.stats_file):
            self._save_stats({})

        log_success("✅ سیستم پشتیبان‌گیری راه‌اندازی شد")

    def _save_stats(self, stats: dict) -> None:
        with open(self.stats_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False, default=str)

    def _load_stats(self) -> dict:
        if os.path.exists(self.stats_file):
            with open(self.stats_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _update_stats(self, backup_type: str, backup_path: str, size: int) -> None:
        stats = self._load_stats()
        if backup_type not in stats:
            stats[backup_type] = []
        stats[backup_type].append(
            {"path": backup_path, "size": size, "date": datetime.now().isoformat()}
        )
        stats[backup_type] = stats[backup_type][-50:]
        self._save_stats(stats)

    def _get_file_hash(self, filepath: str) -> Optional[str]:
        try:
            stat = os.stat(filepath)
            return f"{stat.st_mtime:.6f}_{stat.st_size}"
        except Exception as e:
            log_error(f"خطا در هش کردن {filepath}: {e}")
            return None

    def _load_hashes(self) -> dict:
        if os.path.exists(self.hash_file):
            with open(self.hash_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_hashes(self, hashes: dict) -> None:
        with open(self.hash_file, "w", encoding="utf-8") as f:
            json.dump(hashes, f, indent=2, ensure_ascii=False)

    def _get_code_files(self) -> List[str]:
        """دریافت لیست فایل‌های کد (بدون فایل‌های موقتی)"""
        ignore_dirs = {
            "__pycache__",
            "backups",
            "storage",
            "data",
            ".git",
            "venv",
            "reports",
            "logs",
            "cache",
            "sessions",
            "media",
        }
        ignore_extensions = {
            ".pyc",
            ".db",
            ".log",
            ".bale",
            ".zip",
            ".7z",
            ".rar",
            ".pid",
        }
        ignore_files = {
            "hashes.json",
            "last_backup.txt",
            "last_full_backup.txt",
            "bot.pid",
            "stats.json",
            "products_list.txt",
            "messages.txt",
        }

        code_files = []
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                if file in ignore_files:
                    continue
                if any(file.endswith(ext) for ext in ignore_extensions):
                    continue
                if file.endswith(
                    (
                        ".py",
                        ".json",
                        ".ini",
                        ".txt",
                        ".env",
                        ".bat",
                        ".md",
                        ".yml",
                        ".yaml",
                    )
                ):
                    rel = os.path.relpath(os.path.join(root, file), BASE_DIR)
                    code_files.append(rel)

        return sorted(set(code_files))

    def _is_db_valid(self) -> bool:
        """بررسی سلامت دیتابیس"""
        if not os.path.exists(DB_PATH):
            return False
        try:
            conn = sqlite3.connect(DB_PATH)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            return result[0] == "ok"
        except Exception:
            return False

    def _get_db_size(self) -> int:
        return os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

    def _compress_folder(self, source_dir: str, zip_path: str) -> bool:
        try:
            file_count = 0
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(source_dir):
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, source_dir)
                        zf.write(full_path, arcname)
                        file_count += 1
            return True
        except Exception as e:
            log_error(f"خطا در فشرده‌سازی: {e}")
            return False

    async def backup_daily(self) -> Optional[str]:
        """پشتیبان روزانه دیتابیس"""
        if not os.path.exists(DB_PATH):
            log_warning("فایل دیتابیس یافت نشد")
            return None

        if not self._is_db_valid():
            log_error("دیتابیس خراب است، پشتیبان‌گیری انجام نشد")
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_folder = os.path.join(self.daily_dir, f"db_backup_{timestamp}")
        os.makedirs(backup_folder, exist_ok=True)

        shutil.copy2(DB_PATH, os.path.join(backup_folder, "messages.db"))

        # گزارش
        report_path = os.path.join(backup_folder, "INFO.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"پشتیبان روزانه دیتابیس - {timestamp}\n")
            f.write(f"حجم: {self._get_db_size():,} bytes\n")
            f.write(f"تاریخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        zip_path = os.path.join(self.daily_dir, f"db_backup_{timestamp}.zip")
        if self._compress_folder(backup_folder, zip_path):
            shutil.rmtree(backup_folder, ignore_errors=True)
            self._update_stats("daily", zip_path, os.path.getsize(zip_path))
            log_success(f"پشتیبان روزانه: {os.path.basename(zip_path)}")
            return zip_path

        return None

    async def backup_full(self) -> Optional[str]:
        """پشتیبان کامل (کد + دیتابیس)"""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_folder = os.path.join(self.full_dir, f"full_backup_{timestamp}")
        os.makedirs(backup_folder, exist_ok=True)

        # کپی دیتابیس
        if os.path.exists(DB_PATH) and self._is_db_valid():
            shutil.copy2(DB_PATH, os.path.join(backup_folder, "messages.db"))

        # کپی فایل‌های کد
        file_count = 0
        for filepath in self._get_code_files():
            src = os.path.join(BASE_DIR, filepath)
            if not os.path.exists(src):
                continue
            dest = os.path.join(backup_folder, filepath)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            file_count += 1

        # گزارش
        report = os.path.join(backup_folder, "INFO.txt")
        with open(report, "w", encoding="utf-8") as f:
            f.write(f"پشتیبان کامل - {timestamp}\n")
            f.write(f"تعداد فایل‌های کد: {file_count}\n")
            f.write(f"حجم دیتابیس: {self._get_db_size():,} bytes\n")

        zip_path = os.path.join(self.full_dir, f"full_backup_{timestamp}.zip")
        if self._compress_folder(backup_folder, zip_path):
            shutil.rmtree(backup_folder, ignore_errors=True)
            self._update_stats("full", zip_path, os.path.getsize(zip_path))
            log_success(f"پشتیبان کامل: {os.path.basename(zip_path)}")
            return zip_path

        return None

    def check_code_changes(self) -> List[str]:
        """بررسی تغییرات کدها"""
        hashes = self._load_hashes()
        changes = []
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

        for filepath in list(hashes.keys()):
            if filepath not in current_files:
                changes.append(f"حذف: {filepath}")
                del hashes[filepath]

        for filepath in current_files:
            full = os.path.join(BASE_DIR, filepath)
            if os.path.exists(full):
                hashes[filepath] = self._get_file_hash(full)

        self._save_hashes(hashes)
        return changes

    def _should_backup_daily(self) -> bool:
        if not os.path.exists(self.last_backup_file):
            return True
        with open(self.last_backup_file, "r") as f:
            last = float(f.read().strip())
        return (time.time() - last) >= (DAILY_BACKUP_HOURS * 3600)

    def _mark_daily_done(self) -> None:
        with open(self.last_backup_file, "w") as f:
            f.write(str(time.time()))

    def _should_backup_full(self) -> bool:
        if not os.path.exists(self.last_full_backup_file):
            return True
        with open(self.last_full_backup_file, "r") as f:
            last = float(f.read().strip())
        return (time.time() - last) >= (FULL_BACKUP_DAYS * 24 * 3600)

    def _mark_full_done(self) -> None:
        with open(self.last_full_backup_file, "w") as f:
            f.write(str(time.time()))

    def _cleanup_old(self) -> None:
        """حذف بکاپ‌های قدیمی"""
        # پشتیبان‌های روزانه
        daily_files = sorted(
            [f for f in os.listdir(self.daily_dir) if f.endswith(".zip")]
        )
        while len(daily_files) > KEEP_DAILY_BACKUPS:
            oldest = daily_files.pop(0)
            os.remove(os.path.join(self.daily_dir, oldest))
            log_info(f"حذف بکاپ قدیمی: {oldest}")

        # پشتیبان‌های کامل
        full_files = sorted(
            [f for f in os.listdir(self.full_dir) if f.endswith(".zip")]
        )
        while len(full_files) > KEEP_FULL_BACKUPS:
            oldest = full_files.pop(0)
            os.remove(os.path.join(self.full_dir, oldest))
            log_info(f"حذف بکاپ کامل قدیمی: {oldest}")

    async def run_periodic(self) -> None:
        """اجرای دوره‌ای پشتیبان‌گیری"""
        log_info("سرویس پشتیبان‌گیری دوره‌ای شروع شد")
        while self._running:
            try:
                changes = self.check_code_changes()
                if changes and self._should_backup_full():
                    log_info(f"تغییرات detected: {len(changes)} فایل")
                    if await self.backup_full():
                        self._mark_full_done()

                if self._should_backup_daily():
                    if await self.backup_daily():
                        self._mark_daily_done()

                self._cleanup_old()
            except Exception as e:
                log_error(f"خطا در پشتیبان‌گیری: {e}")

            await asyncio.sleep(3600)  # هر ساعت

    def stop(self):
        self._running = False

    async def manual_full(self) -> Optional[str]:
        return await self.backup_full()

    async def manual_daily(self) -> Optional[str]:
        return await self.backup_daily()

    def get_stats(self) -> dict:
        return self._load_stats()


async def start_backup_service():
    """راه‌اندازی سرویس پشتیبان‌گیری"""
    backup = BackupService()
    backup.init()
    asyncio.create_task(backup.run_periodic())
    log_success("سرویس پشتیبان‌گیری راه‌اندازی شد")
    return backup
