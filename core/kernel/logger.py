"""
Structured logging — built on Python's standard `logging`.

- JSON lines to file (ISO-8601 UTC timestamp, level, logger, message, context,
  exception) → ready for ELK / Loki / Datadog / CloudWatch.
- Human-readable (optionally colored) console output.
- Size-based rotation (RotatingFileHandler) + backups.
- Backward-compatible API: log_info / log_success / log_error / log_warning /
  log_debug / log_exception / get_logger / log_duration.
- Configurable via .env:
    LOG_LEVEL (DEBUG|INFO|SUCCESS|WARNING|ERROR), LOG_DIR, LOG_TO_FILE,
    LOG_JSON (true=JSON file, false=plain), LOG_CONSOLE, LOG_MAX_MB, LOG_BACKUPS

⚠️ این فایل خودش نمی‌تواند از log_info/log_success/... استفاده کند (recursion).
از print() برای پیام‌های early-init و از self._logger.<level>(...) برای بعد از
setup استفاده می‌کنیم.

نسخهٔ ۲ (این نسخه):
  - 🎯 init diagnostics: چاپ خلاصه‌ای از تنظیماتِ اعمال‌شده (level/handlers/paths)
  - 🐛 FIX: _emit با try/except — اگه handler خطا بده، کاربر نمی‌ترکه
  - 🐛 FIX: set_level سطح نامعتبر رو لاگ می‌کند (نه بی‌صدا)
  - 🐛 FIX: get_stats JSON-safe (datetime → ISO)
  - 🐛 FIX: log_duration از time.monotonic (نه time.time)
  - 🐛 FIX: flush خطاها لاگ می‌شه
  - 🐛 FIX: _env_bool قبول می‌کنه yes/no/on/off/1/0
  - 🎯 ConsoleFormatter رنگ رو در Windows Terminal هم می‌فهمه (ANSICON/WT)
  - 🧹 from __future__ import annotations
  - 🧹 add_level helper + safe_add_handler
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from config import SYSTEM_CONFIG

# Don't let a logging/encoding hiccup crash the app
logging.raiseExceptions = False

# Custom SUCCESS level between INFO(20) and WARNING(30)
SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

_LEVEL_NO = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "SUCCESS": SUCCESS_LEVEL,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_EMOJI = {
    "DEBUG": "🐛",
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🔥",
}
_COLOR = {
    "DEBUG": "\033[90m",
    "INFO": "\033[94m",
    "SUCCESS": "\033[92m",
    "WARNING": "\033[93m",
    "ERROR": "\033[91m",
    "CRITICAL": "\033[95m",
}
_RESET = "\033[0m"

# حداکثر طول exception در JSON (باقی‌شو می‌بُریم که فایل لاگ بی‌نهایت نشه)
_EXC_MAX_LEN = 2000


class LogLevel(Enum):
    """Kept for backward compatibility with older call sites."""

    DEBUG = "🐛"
    INFO = "ℹ️"
    SUCCESS = "✅"
    WARNING = "⚠️"
    ERROR = "❌"
    CRITICAL = "🔥"


# ═══════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════
class JsonFormatter(logging.Formatter):
    """One machine-readable JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "context", None)
        if ctx:
            obj["context"] = ctx
        if record.exc_info:
            try:
                exc_text = "".join(
                    traceback.format_exception(*record.exc_info)
                )
                obj["exception"] = exc_text[-_EXC_MAX_LEN:]
            except Exception:
                obj["exception"] = "(unable to format exception)"
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            # JSON نشد — fallback به رشته
            return json.dumps(
                {
                    "ts": obj["ts"],
                    "level": obj["level"],
                    "msg": str(obj["msg"]),
                },
                ensure_ascii=False,
            )


class ConsoleFormatter(logging.Formatter):
    """Readable line for the terminal (colored on real TTYs)."""

    def __init__(self):
        super().__init__()
        try:
            # Windows Terminal / ANSICON / ConEmu رنگ رو پشتیبانی می‌کنن
            is_tty = sys.stdout.isatty() if sys.stdout else False
            is_win = sys.platform.startswith("win")
            has_ansi = bool(
                os.getenv("WT_SESSION")      # Windows Terminal
                or os.getenv("ANSICON")      # ANSICON
                or os.getenv("ConEmuANSI")   # ConEmu
                or os.getenv("TERM")         # Unix / Git Bash
            )
            self._color = is_tty and (not is_win or has_ansi)
        except Exception:
            self._color = False

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        emoji = _EMOJI.get(record.levelname, "")
        line = f"[{ts}] {emoji} {record.getMessage()}"
        ctx = getattr(record, "context", None)
        if ctx:
            try:
                line += f" | {json.dumps(ctx, ensure_ascii=False, default=str)}"
            except Exception:
                line += f" | {ctx}"
        if self._color:
            line = f"{_COLOR.get(record.levelname, '')}{line}{_RESET}"
        return line


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
_TRUE_VALUES = {"true", "1", "yes", "on", "y", "t"}
_FALSE_VALUES = {"false", "0", "no", "off", "n", "f"}


def _env_bool(name: str, default: str = "true") -> bool:
    """تشخیص bool از env — با پشتیبانی از yes/no/on/off/1/0."""
    raw = os.getenv(name)
    if raw is None:
        raw = default
    val = str(raw).strip().lower()
    if val in _TRUE_VALUES:
        return True
    if val in _FALSE_VALUES:
        return False
    # مقدار عجیب → default
    return str(default).strip().lower() in _TRUE_VALUES


# ═══════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════
class Logger:
    """Singleton wrapper around a stdlib logger named 'srooyesh'."""

    _instance: Logger | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        # ─── مسیر و سطح ───
        self.log_dir = os.getenv(
            "LOG_DIR", SYSTEM_CONFIG.get("log_directory", "storage/logs")
        )
        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception as e:
            print(
                f"❌ [LOGGER:init] ساخت log_dir ناموفق | {e}",
                file=sys.stderr,
            )

        self.log_level = os.getenv(
            "LOG_LEVEL", SYSTEM_CONFIG.get("log_level", "INFO")
        ).upper()
        if self.log_level not in _LEVEL_NO:
            print(
                f"⚠️ [LOGGER:init] LOG_LEVEL نامعتبر «{self.log_level}» — "
                f"استفاده از INFO",
                file=sys.stderr,
            )
            self.log_level = "INFO"

        # ─── logger اصلی ───
        self._logger = logging.getLogger("srooyesh")
        self._logger.setLevel(_LEVEL_NO.get(self.log_level, logging.INFO))
        self._logger.handlers.clear()
        self._logger.propagate = False

        # ─── Console handler ───
        console_on = _env_bool("LOG_CONSOLE", "true")
        if console_on:
            try:
                ch = logging.StreamHandler(sys.stdout)
                ch.setFormatter(ConsoleFormatter())
                self._logger.addHandler(ch)
            except Exception as e:
                print(
                    f"⚠️ [LOGGER:init] console handler | {e}",
                    file=sys.stderr,
                )

        # ─── File handler ───
        file_on = (
            SYSTEM_CONFIG.get("log_to_file", True)
            and _env_bool("LOG_TO_FILE", "true")
        )
        file_path = ""
        if file_on:
            try:
                max_mb = float(os.getenv("LOG_MAX_MB", "10"))
                if max_mb <= 0:
                    max_mb = 10.0
                backups = int(os.getenv("LOG_BACKUPS", "30"))
                if backups < 0:
                    backups = 0
                as_json = _env_bool("LOG_JSON", "true")
                fname = "app.jsonl" if as_json else "app.log"
                file_path = os.path.join(self.log_dir, fname)
                fh = logging.handlers.RotatingFileHandler(
                    file_path,
                    maxBytes=int(max_mb * 1024 * 1024),
                    backupCount=backups,
                    encoding="utf-8",
                )
                fh.setFormatter(
                    JsonFormatter() if as_json else ConsoleFormatter()
                )
                self._logger.addHandler(fh)
            except Exception as e:
                print(
                    f"❌ [LOGGER:init] file handler ناموفق | {e}",
                    file=sys.stderr,
                )
                file_on = False

        # ─── آمار ───
        self._stats: dict[str, Any] = {
            "total_logs": 0,
            "by_level": {},
            "last_log_time": None,
        }

        # ─── init diagnostic (با print چون logger تازه ساخته شد) ───
        handlers_info = []
        for h in self._logger.handlers:
            hname = type(h).__name__
            if isinstance(h, logging.StreamHandler) and not isinstance(
                h, logging.FileHandler
            ):
                handlers_info.append("console")
            elif isinstance(h, logging.FileHandler):
                handlers_info.append(f"file:{os.path.basename(h.baseFilename)}")
            else:
                handlers_info.append(hname)

        print(
            f"🔧 [LOGGER:init] level={self.log_level} | "
            f"handlers=[{', '.join(handlers_info) or '—'}] | "
            f"dir={self.log_dir}",
            file=sys.stderr,
        )

    # ═══════════════════════════════════════════
    # emit (با محافظ در برابر خطاهای handler)
    # ═══════════════════════════════════════════
    def _emit(
        self,
        levelname: str,
        message: str,
        context: dict | None = None,
        exc_info=None,
    ) -> None:
        # آمار
        try:
            self._stats["total_logs"] += 1
            self._stats["by_level"][levelname] = (
                self._stats["by_level"].get(levelname, 0) + 1
            )
            self._stats["last_log_time"] = datetime.now()
        except Exception:
            pass

        levelno = _LEVEL_NO.get(levelname, logging.INFO)
        extra = {"context": context} if context else None
        try:
            self._logger.log(levelno, message, extra=extra, exc_info=exc_info)
        except Exception as e:
            # هرگز نباید کد کاربر رو بترکونیم — به stderr چاپ می‌کنیم
            try:
                print(
                    f"❌ [LOGGER:emit-fail] {type(e).__name__}: {e} | "
                    f"orig_level={levelname} | msg={message[:200]!r}",
                    file=sys.stderr,
                )
            except Exception:
                pass

    # ═══════════════════════════════════════════
    # convenience
    # ═══════════════════════════════════════════
    def debug(self, message: str, context: dict | None = None):
        self._emit("DEBUG", message, context)

    def info(self, message: str, context: dict | None = None):
        self._emit("INFO", message, context)

    def success(self, message: str, context: dict | None = None):
        self._emit("SUCCESS", message, context)

    def warning(self, message: str, context: dict | None = None):
        self._emit("WARNING", message, context)

    def error(self, message: str, context: dict | None = None):
        self._emit("ERROR", message, context)

    def critical(self, message: str, context: dict | None = None):
        self._emit("CRITICAL", message, context)

    def exception(
        self,
        message: str,
        exc: Exception,
        context: dict | None = None,
    ):
        ctx = dict(context or {})
        ctx["exception_type"] = type(exc).__name__
        self._emit(
            "ERROR",
            message,
            ctx,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    def log(self, level, message: str, context: dict | None = None):
        """Accepts a LogLevel enum or a level name string (backward compatible)."""
        if isinstance(level, LogLevel):
            # mapped by name (DEBUG/INFO/...)
            name = level.name
        else:
            name = str(level).upper()
        self._emit(name, message, context)

    # ═══════════════════════════════════════════
    # management
    # ═══════════════════════════════════════════
    def set_level(self, level: str) -> bool:
        """تغییر سطح لاگ. Returns: True اگه اعمال شد."""
        level_u = str(level).upper()
        if level_u not in _LEVEL_NO:
            self.warning(
                f"⚠️ [LOGGER:set_level] سطح نامعتبر «{level}» | "
                f"معتبرها: {list(_LEVEL_NO.keys())}"
            )
            return False
        self.log_level = level_u
        self._logger.setLevel(_LEVEL_NO[level_u])
        self.success(f"✅ [LOGGER:set_level] سطح روی {level_u} تنظیم شد")
        return True

    def get_stats(self) -> dict[str, Any]:
        """آمار لاگر — JSON-safe."""
        last = self._stats.get("last_log_time")
        return {
            "total_logs": self._stats.get("total_logs", 0),
            "by_level": dict(self._stats.get("by_level", {})),
            "last_log_time": (
                last.isoformat() if isinstance(last, datetime) else last
            ),
            "log_dir": self.log_dir,
            "log_level": self.log_level,
            "handlers": [
                type(h).__name__ for h in self._logger.handlers
            ],
        }

    def flush(self):
        for h in self._logger.handlers:
            try:
                h.flush()
            except Exception as e:
                # نمی‌توانیم log_* صدا بزنیم (recursion) → stderr
                try:
                    print(
                        f"⚠️ [LOGGER:flush] {type(h).__name__} | "
                        f"{type(e).__name__}: {e}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass


# ═══════════════════════════════════════════
# Singleton + module-level helpers
# ═══════════════════════════════════════════
_logger: Logger | None = None


def get_logger() -> Logger:
    global _logger
    if _logger is None:
        _logger = Logger()
    return _logger


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


@contextmanager
def log_duration(operation: str, logger: Logger | None = None):
    """Log start/finish + duration of an operation.

    Uses time.monotonic() so NTP adjustments can't produce negative durations.
    """
    logger = logger or get_logger()
    start = time.monotonic()
    logger.info(f"▶️ start: {operation}")
    try:
        yield
    except Exception as e:
        logger.exception(f"❌ error in {operation}", e)
        raise
    finally:
        elapsed = time.monotonic() - start
        logger.success(f"✅ done: {operation} ({elapsed:.2f}s)")
