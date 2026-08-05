# core/cleanup_service.py
"""
سرویس پاک‌سازی خودکار (Retention).

طبق تنظیمات .env:
- عکس/فایل‌های قدیمی‌تر از KEEP_MEDIA_DAYS حذف می‌شوند.
- گزارش‌های قدیمی‌تر از KEEP_REPORTS_DAYS حذف می‌شوند.
- پیام‌های دیتابیس قدیمی‌تر از KEEP_MESSAGES_DAYS حذف می‌شوند (۰ = هرگز).
هر CLEANUP_INTERVAL_HOURS ساعت یک‌بار اجرا می‌شود (و یک‌بار هنگام شروع).
"""
import os
import time
import asyncio
from config import MEDIA_DIR, REPORTS_DIR, RETENTION
from core.logger import log_info, log_success, log_error


class CleanupService:
    def __init__(self, db=None):
        self.db = db
        self._running = True
        self.keep_media_days = RETENTION["media_days"]
        self.keep_reports_days = RETENTION["reports_days"]
        self.keep_messages_days = RETENTION["messages_days"]
        self.interval_hours = max(1, RETENTION["cleanup_interval_hours"])

    @staticmethod
    def _delete_old_files(folder: str, days: int, label: str) -> int:
        """فایل‌های قدیمی‌تر از days روز را در folder حذف می‌کند (۰ = هیچ)."""
        if days <= 0 or not os.path.isdir(folder):
            return 0
        cutoff = time.time() - days * 86400
        removed = 0
        for root, _, files in os.walk(folder):
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except Exception:
                    pass
        if removed:
            log_success(f"🧹 {removed} {label} قدیمی‌تر از {days} روز حذف شد")
        return removed

    async def run_once(self):
        """یک دور پاک‌سازی کامل."""
        self._delete_old_files(MEDIA_DIR, self.keep_media_days, "فایل رسانه")
        self._delete_old_files(REPORTS_DIR, self.keep_reports_days, "گزارش")
        if self.db and self.keep_messages_days > 0:
            try:
                await self.db.delete_old_messages(self.keep_messages_days)
                log_success(
                    f"🧹 پیام‌های قدیمی‌تر از {self.keep_messages_days} روز از دیتابیس حذف شد"
                )
            except Exception as e:
                log_error(f"خطا در حذف پیام‌های قدیمی دیتابیس: {e}")

    async def run_periodic(self):
        log_info("سرویس پاک‌سازی خودکار شروع شد")
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                log_error(f"خطا در پاک‌سازی: {e}")
            await asyncio.sleep(self.interval_hours * 3600)

    def stop(self):
        self._running = False


async def start_cleanup_service(db=None):
    svc = CleanupService(db)
    asyncio.create_task(svc.run_periodic())
    log_success(
        f"🧹 پاک‌سازی خودکار فعال شد "
        f"(رسانه: {svc.keep_media_days}روز، گزارش: {svc.keep_reports_days}روز، "
        f"پیام‌ها: {'همیشه' if svc.keep_messages_days == 0 else str(svc.keep_messages_days)+'روز'})"
    )
    return svc
