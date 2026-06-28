# -*- coding: utf-8 -*-
"""
سیستم لاگ‌گیری پیشرفته - نسخه نهایی

قابلیت‌ها:
- ذخیره در فایل و کنسول
- چرخش خودکار فایل‌های لاگ بر اساس حجم
- سطوح مختلف لاگ (DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL)
- لاگ با context (اطلاعات اضافی)
- ذخیره در JSON برای آنالیز بعدی
- Singleton Pattern (یک نمونه در کل برنامه)
- Context manager برای اندازه‌گیری زمان اجرا
"""

import os
import sys
import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from config import SYSTEM_CONFIG


class LogLevel(Enum):
    """سطوح مختلف لاگ (عدد بالاتر = مهم‌تر)"""
    DEBUG = "🐛"
    INFO = "ℹ️"
    SUCCESS = "✅"
    WARNING = "⚠️"
    ERROR = "❌"
    CRITICAL = "🔥"


class Logger:
    """
    سیستم لاگ‌گیری پیشرفته
    
    استفاده:
        logger = Logger()
        logger.info("پیام معمولی")
        logger.success("عملیات موفق")
        logger.error("خطا", {"code": 500})
        logger.exception("خطا در تابع", e)
    
    تنظیمات در config.py:
        - log_level: سطح لاگ (DEBUG, INFO, WARNING, ERROR)
        - log_to_file: ذخیره در فایل (True/False)
        - max_log_file_mb: حداکثر حجم فایل لاگ (مگابایت)
        - max_log_files: تعداد فایل‌های لاگ نگهداری شود
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.log_dir = SYSTEM_CONFIG.get("log_directory", "storage/logs")
        self.log_to_file = SYSTEM_CONFIG.get("log_to_file", True)
        self.log_level = SYSTEM_CONFIG.get("log_level", "INFO")
        self.max_file_size_mb = SYSTEM_CONFIG.get("max_log_file_mb", 10)
        self.max_files = SYSTEM_CONFIG.get("max_log_files", 30)
        
        # ایجاد پوشه لاگ
        os.makedirs(self.log_dir, exist_ok=True)
        
        # سطوح لاگ (عدد بالاتر = مهم‌تر)
        self._level_values = {
            "DEBUG": 0,
            "INFO": 1,
            "SUCCESS": 1,
            "WARNING": 2,
            "ERROR": 3,
            "CRITICAL": 4
        }
        
        # رنگ‌ها برای ترمینال (فقط در لینوکس/مک)
        self._colors = {
            LogLevel.DEBUG: "\033[90m",     # خاکستری
            LogLevel.INFO: "\033[94m",      # آبی
            LogLevel.SUCCESS: "\033[92m",   # سبز
            LogLevel.WARNING: "\033[93m",   # زرد
            LogLevel.ERROR: "\033[91m",     # قرمز
            LogLevel.CRITICAL: "\033[95m"   # بنفش
        }
        self._reset_color = "\033[0m"
        
        # آمار لاگ‌ها
        self._stats = {
            "total_logs": 0,
            "by_level": {level.name: 0 for level in LogLevel},
            "last_log_time": None
        }
        
        # چرخش فایل در صورت نیاز
        self._rotate_if_needed()
    
    def _should_log(self, level: str) -> bool:
        """بررسی آیا این سطح باید لاگ شود"""
        current_value = self._level_values.get(self.log_level.upper(), 1)
        message_value = self._level_values.get(level, 1)
        return message_value >= current_value
    
    def _get_log_filename(self) -> str:
        """دریافت نام فایل لاگ بر اساس تاریخ"""
        return os.path.join(self.log_dir, f"{datetime.now().date()}.log")
    
    def _get_log_size(self) -> int:
        """دریافت حجم فایل لاگ فعلی"""
        log_file = self._get_log_filename()
        if os.path.exists(log_file):
            return os.path.getsize(log_file)
        return 0
    
    def _rotate_if_needed(self):
        """چرخش خودکار فایل لاگ در صورت بزرگ شدن"""
        log_file = self._get_log_filename()
        size_mb = self._get_log_size() / (1024 * 1024)
        
        if size_mb > self.max_file_size_mb:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = os.path.join(self.log_dir, f"log_{timestamp}.log")
            if os.path.exists(log_file):
                os.rename(log_file, new_name)
            self._cleanup_old_logs()
    
    def _cleanup_old_logs(self):
        """حذف فایل‌های لاگ قدیمی"""
        try:
            log_files = []
            for f in os.listdir(self.log_dir):
                if f.endswith('.log') and f != f"{datetime.now().date()}.log":
                    file_path = os.path.join(self.log_dir, f)
                    log_files.append((file_path, os.path.getmtime(file_path)))
            
            log_files.sort(key=lambda x: x[1])
            
            while len(log_files) > self.max_files:
                oldest_file, _ = log_files.pop(0)
                try:
                    os.remove(oldest_file)
                except:
                    pass
        except Exception:
            pass
    
    def _format_message(self, level: LogLevel, message: str, context: Optional[Dict] = None) -> str:
        """فرمت کردن پیام لاگ"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        base = f"[{timestamp}] {level.value} {message}"
        
        if context:
            context_str = json.dumps(context, ensure_ascii=False)
            base += f" | {context_str}"
        
        return base
    
    def _write_to_file(self, formatted_message: str):
        """نوشتن لاگ در فایل"""
        if not self.log_to_file:
            return
        
        try:
            log_file = self._get_log_filename()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(formatted_message + "\n")
            self._rotate_if_needed()
        except Exception:
            pass
    
    def _update_stats(self, level: str):
        """به‌روزرسانی آمار لاگ‌ها"""
        self._stats["total_logs"] += 1
        self._stats["by_level"][level] = self._stats["by_level"].get(level, 0) + 1
        self._stats["last_log_time"] = datetime.now()
    
    def log(self, level: LogLevel, message: str, context: Optional[Dict] = None):
        """لاگ کردن پیام با سطح مشخص"""
        level_name = level.name
        
        if not self._should_log(level_name):
            return
        
        formatted = self._format_message(level, message, context)
        
        # چاپ در کنسول (با رنگ در لینوکس/مک)
        if sys.platform == "win32":
            print(formatted)
        else:
            color = self._colors.get(level, "")
            print(f"{color}{formatted}{self._reset_color}")
        
        # ذخیره در فایل
        self._write_to_file(formatted)
        
        # به‌روزرسانی آمار
        self._update_stats(level_name)
    
    # ========== توابع راحت برای استفاده ==========
    
    def debug(self, message: str, context: Optional[Dict] = None):
        self.log(LogLevel.DEBUG, message, context)
    
    def info(self, message: str, context: Optional[Dict] = None):
        self.log(LogLevel.INFO, message, context)
    
    def success(self, message: str, context: Optional[Dict] = None):
        self.log(LogLevel.SUCCESS, message, context)
    
    def warning(self, message: str, context: Optional[Dict] = None):
        self.log(LogLevel.WARNING, message, context)
    
    def error(self, message: str, context: Optional[Dict] = None):
        self.log(LogLevel.ERROR, message, context)
    
    def critical(self, message: str, context: Optional[Dict] = None):
        self.log(LogLevel.CRITICAL, message, context)
    
    def exception(self, message: str, exc: Exception, context: Optional[Dict] = None):
        """لاگ کردن استثنا با جزئیات کامل"""
        ctx = context or {}
        ctx["exception"] = str(exc)
        ctx["exception_type"] = type(exc).__name__
        
        import traceback
        ctx["traceback"] = traceback.format_exc()[-1000:]
        
        self.error(message, ctx)
    
    # ========== مدیریت ==========
    
    def set_level(self, level: str):
        """تغییر سطح لاگ در زمان اجرا"""
        if level.upper() in self._level_values:
            self.log_level = level.upper()
            self.info(f"سطح لاگ به {self.log_level} تغییر کرد")
    
    def get_stats(self) -> Dict[str, Any]:
        """دریافت آمار لاگ‌ها"""
        return {
            **self._stats,
            "log_dir": self.log_dir,
            "current_log_file": self._get_log_filename(),
            "file_size_mb": round(self._get_log_size() / (1024 * 1024), 2),
            "log_level": self.log_level,
            "log_to_file": self.log_to_file
        }
    
    def flush(self):
        """اجبار برای ذخیره لاگ‌ها (در صورت نیاز)"""
        pass


# ==================================================
# نمونه Singleton برای استفاده آسان در کل پروژه
# ==================================================

_logger = None

def get_logger() -> Logger:
    """دریافت نمونه لاگر (Singleton)"""
    global _logger
    if _logger is None:
        _logger = Logger()
    return _logger


# ==================================================
# توابع سطح بالا (برای سازگاری با کد قدیمی)
# ==================================================

def log_info(msg: str):
    get_logger().info(msg)

def log_success(msg: str):
    get_logger().success(msg)

def log_error(msg: str):
    get_logger().error(msg)

def log_warning(msg: str):
    get_logger().warning(msg)

def log_debug(msg: str):
    get_logger().debug(msg)

def log_exception(msg: str, exc: Exception):
    get_logger().exception(msg, exc)


# ==================================================
# Context Manager برای لاگ کردن زمان اجرا
# ==================================================

from contextlib import contextmanager

@contextmanager
def log_duration(operation: str, logger: Logger = None):
    """
    لاگ کردن زمان اجرای یک عملیات
    
    استفاده:
        with log_duration("پردازش پیام"):
            await process_message()
    """
    if logger is None:
        logger = get_logger()
    
    start = datetime.now()
    logger.info(f"شروع: {operation}")
    
    try:
        yield
    except Exception as e:
        logger.exception(f"خطا در {operation}", e)
        raise
    finally:
        duration = (datetime.now() - start).total_seconds()
        logger.success(f"پایان: {operation} (مدت: {duration:.2f} ثانیه)")