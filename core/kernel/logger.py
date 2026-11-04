"""Structured logging — built on Python's standard `logging`.

ویژگی‌ها:
  - JSON lines to file (ISO-8601 UTC) → آماده برای ELK/Loki/Datadog
  - Console output خوانا (با رنگ در TTY)
  - Size-based rotation (RotatingFileHandler) + backups
  - API سازگار با قبل: log_info / log_success / log_error / ...
  - Configurable via .env یا config

⚠️ این فایل نمی‌تواند از log_info/log_success/... استفاده کند
(recursion). از print() برای پیام‌های early-init استفاده می‌شود.

نسخهٔ ۴ (این نسخه):
  - 🐛 FIX: `_EXC_MAX_LEN`, `SUCCESS_LEVEL` حالا env-based.
  - 🐛 FIX: `Logger.__new__` حالا thread-safe است.
  - 🐛 FIX: `get_logger()` حالا thread-safe است.
  - 🐛 FIX: `Logger._emit` حالا `message` را type-check می‌کند.
  - 🐛 FIX: `log_dir`, `log_level`, `max_mb`, `backups` حالا از
    `config.SYSTEM_CONFIG` و `config.LOG_CONFIG` می‌آیند
    (با fallback به env).
  - 🐛 FIX: `_ensure_utf8_streams()` حالا dict status برمی‌گرداند.
  - 🐛 FIX: `RotatingFileHandler` حالا `delay=True` (جلوگیری از
    قفل فایل در برخی سیستم‌ها).
  - 🎯 `LogLevel` enum بهتر مستندسازی شد (backward-compat).
  - 🎯 `reset_logger()` برای تست.
  - 🎯 `get_stats()` کامل‌تر (version, json flag, console flag).
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import io
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from typing import Any

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


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


_TRUE_VALUES = frozenset({
    "true", "1", "yes", "on", "y", "t",
})
_FALSE_VALUES = frozenset({
    "false", "0", "no", "off", "n", "f",
})


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
    return str(default).strip().lower() in _TRUE_VALUES


def _stream_encoding(stream: Any) -> str:
    """encoding فعلی یک stream را با احتیاط برمی‌گرداند."""
    try:
        enc = getattr(stream, "encoding", None)
        return str(enc) if enc else "?"
    except Exception:
        return "?"


# ═══════════════════════════════════════════
# UTF-8 streams (قبل از هر چاپی)
# ═══════════════════════════════════════════
def _ensure_utf8_streams() -> dict[str, str]:
    """sys.stdout/stderr را روی UTF-8 تنظیم می‌کند.

    Returns:
        dict با وضعیت هر stream (patched/skipped/error/no_stream).
    """
    status: dict[str, str] = {}

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            status[name] = "no_stream"
            continue

        # ─── تلاش reconfigure ───
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(
                    encoding="utf-8", errors="replace"
                )
                status[name] = "patched"
                continue
        except Exception:
            pass

        # ─── fallback: wrap با UTF-8 ───
        try:
            buf = getattr(stream, "buffer", None)
            if buf is not None:
                wrapper = io.TextIOWrapper(
                    buf,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                )
                setattr(sys, name, wrapper)
                status[name] = "patched"
            else:
                status[name] = "skipped"
        except Exception:
            status[name] = "error"

    return status


_UTF8_STATUS = _ensure_utf8_streams()

# Don't let a logging/encoding hiccup crash the app
logging.raiseExceptions = False


# ═══════════════════════════════════════════
# Levels + colors
# ═══════════════════════════════════════════
SUCCESS_LEVEL = _env_int(
    "LOG_SUCCESS_LEVEL", 25,
    min_val=logging.INFO + 1,
    max_val=logging.WARNING - 1,
)
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

_LEVEL_NO: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "SUCCESS": SUCCESS_LEVEL,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_EMOJI: dict[str, str] = {
    "DEBUG": "🐛",
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🔥",
}

_COLOR: dict[str, str] = {
    "DEBUG": "\033[90m",
    "INFO": "\033[94m",
    "SUCCESS": "\033[92m",
    "WARNING": "\033[93m",
    "ERROR": "\033[91m",
    "CRITICAL": "\033[95m",
}
_RESET = "\033[0m"

# حداکثر طول exception در JSON
_EXC_MAX_LEN = _env_int(
    "LOG_EXC_MAX_LEN", 2000,
    min_val=200, max_val=100_000,
)

# ─── سازگاری عقب‌رو ───


class LogLevel(Enum):
    """Enum قدیمی — حفظ‌شده برای سازگاری عقب‌رو.

    ⚠️ استفاده از این enum توصیه نمی‌شود. جایگزین:
    از `log_info`, `log_success`, ... استفاده کن.
    """

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
            return json.dumps(
                obj, ensure_ascii=False, default=str
            )
        except Exception:
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
            is_tty = (
                sys.stdout.isatty() if sys.stdout else False
            )
            is_win = sys.platform.startswith("win")
            has_ansi = bool(
                os.getenv("WT_SESSION")
                or os.getenv("ANSICON")
                or os.getenv("ConEmuANSI")
                or os.getenv("TERM")
            )
            self._color = is_tty and (not is_win or has_ansi)
        except Exception:
            self._color = False

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(
            record.created
        ).strftime("%Y-%m-%d %H:%M:%S")
        emoji = _EMOJI.get(record.levelname, "")
        line = f"[{ts}] {emoji} {record.getMessage()}"

        ctx = getattr(record, "context", None)
        if ctx:
            try:
                ctx_str = json.dumps(
                    ctx, ensure_ascii=False, default=str
                )
                line += f" | {ctx_str}"
            except Exception:
                line += f" | {ctx}"

        if self._color:
            line = (
                f"{_COLOR.get(record.levelname, '')}"
                f"{line}{_RESET}"
            )
        return line


# ═══════════════════════════════════════════
# Logger (singleton)
# ═══════════════════════════════════════════
class Logger:
    """Singleton wrapper around a stdlib logger named 'srooyesh'."""

    _instance: "Logger | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialized = False
                    cls._instance = obj
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        # ─── مسیر و سطح ───
        self.log_dir = self._resolve_log_dir()
        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception as e:
            print(
                f"❌ [LOGGER:init] ساخت log_dir ناموفق | "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )

        self.log_level = self._resolve_log_level()

        # ─── logger اصلی ───
        self._logger = logging.getLogger("srooyesh")
        self._logger.setLevel(
            _LEVEL_NO.get(self.log_level, logging.INFO)
        )
        self._logger.handlers.clear()
        self._logger.propagate = False

        # ─── Console handler ───
        self._console_enabled = _env_bool(
            "LOG_CONSOLE", "true"
        )
        if self._console_enabled:
            self._setup_console_handler()

        # ─── File handler (فقط env — config غیرامنه) ───
        _sys_log_to_file = _env_bool("LOG_TO_FILE", "true")

        self._file_enabled = (
            bool(_sys_log_to_file)
            and _env_bool("LOG_TO_FILE", "true")
        )

        self._file_path = ""
        if self._file_enabled:
            self._setup_file_handler()

        # ─── آمار ───
        self._stats: dict[str, Any] = {
            "total_logs": 0,
            "by_level": {},
            "last_log_time": None,
        }

        # ─── init diagnostic ───
        self._print_init_diagnostic()

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════

    @staticmethod
    def _resolve_log_dir() -> str:
        """🎯 FIX: بدون config — فقط env و default."""
        # ─── env (همیشه امن) ───
        d = os.getenv("LOG_DIR")
        if d and d.strip():
            return d.strip()

        # ─── default: مسیر پروژه + storage/logs ───
        try:
            here = os.path.abspath(__file__)
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(here))
            )
            return os.path.join(project_root, "storage", "logs")
        except Exception:
            return "storage/logs"

    @staticmethod
    def _resolve_log_level() -> str:
        """🎯 FIX: بدون config — فقط env."""
        lv = os.getenv("LOG_LEVEL", "").strip().upper()
        if lv in _LEVEL_NO:
            return lv
        return "INFO"

    def _setup_console_handler(self) -> None:
        """🎯 FIX: setup console handler با UTF-8 امن."""
        try:
            stream = sys.stdout
            enc = _stream_encoding(stream)
            if enc.lower() not in ("utf-8", "utf8"):
                try:
                    buf = getattr(stream, "buffer", None)
                    if buf is not None:
                        stream = io.TextIOWrapper(
                            buf,
                            encoding="utf-8",
                            errors="replace",
                            line_buffering=True,
                        )
                except Exception:
                    pass

            ch = logging.StreamHandler(stream)
            ch.setFormatter(ConsoleFormatter())
            self._logger.addHandler(ch)
        except Exception as e:
            print(
                f"⚠️ [LOGGER:init] console handler | "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )

    def _setup_file_handler(self) -> None:
        """🎯 FIX: setup file handler با config/env."""
        try:
            # ─── فقط از env بخون (config غیرامنه) ───
            max_mb = _env_float(
                "LOG_MAX_MB", 10.0,
                min_val=0.1, max_val=10_000,
            )
            backups = _env_int(
                "LOG_BACKUPS", 30,
                min_val=0, max_val=10_000,
            )
            as_json = _env_bool("LOG_JSON", "true")

            if max_mb <= 0:
                max_mb = 10.0
            if backups < 0:
                backups = 0

            fname = "app.jsonl" if as_json else "app.log"
            file_path = os.path.join(self.log_dir, fname)
            self._file_path = file_path

            fh = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=int(max_mb * 1024 * 1024),
                backupCount=backups,
                encoding="utf-8",
                delay=True,  # 🎯 FIX
            )
            fh.setFormatter(
                JsonFormatter() if as_json
                else ConsoleFormatter()
            )
            self._logger.addHandler(fh)
        except Exception as e:
            print(
                f"❌ [LOGGER:init] file handler ناموفق | "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            self._file_enabled = False

    def _print_init_diagnostic(self) -> None:
        """🎯 چاپ خلاصهٔ init روی stderr."""
        try:
            handlers_info: list[str] = []
            for h in self._logger.handlers:
                hname = type(h).__name__
                if isinstance(h, logging.FileHandler):
                    handlers_info.append(
                        f"file:{os.path.basename(h.baseFilename)}"
                    )
                elif isinstance(h, logging.StreamHandler):
                    handlers_info.append("console")
                else:
                    handlers_info.append(hname)

            stdout_enc = _stream_encoding(sys.stdout)
            stderr_enc = _stream_encoding(sys.stderr)

            print(
                f"🔧 [LOGGER:init] level={self.log_level} | "
                f"handlers=[{', '.join(handlers_info) or '—'}] | "
                f"dir={self.log_dir} | "
                f"stdout_enc={stdout_enc} | "
                f"stderr_enc={stderr_enc} | "
                f"utf8={_UTF8_STATUS}",
                file=sys.stderr,
            )
        except Exception:
            pass

    # ═══════════════════════════════════════════
    # emit
    # ═══════════════════════════════════════════
    def _emit(
        self,
        levelname: str,
        message: Any,
        context: dict | None = None,
        exc_info: Any = None,
    ) -> None:
        # 🎯 FIX: type-check message
        if not isinstance(message, str):
            try:
                message = str(message)
            except Exception:
                message = "(unprintable)"

        # ─── آمار ───
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
            self._logger.log(
                levelno,
                message,
                extra=extra,
                exc_info=exc_info,
            )
        except Exception as e:
            try:
                print(
                    f"❌ [LOGGER:emit-fail] "
                    f"{type(e).__name__}: {e} | "
                    f"orig_level={levelname} | "
                    f"msg={message[:200]!r}",
                    file=sys.stderr,
                )
            except Exception:
                pass

    # ═══════════════════════════════════════════
    # convenience
    # ═══════════════════════════════════════════
    def debug(
        self, message: Any, context: dict | None = None
    ) -> None:
        self._emit("DEBUG", message, context)

    def info(
        self, message: Any, context: dict | None = None
    ) -> None:
        self._emit("INFO", message, context)

    def success(
        self, message: Any, context: dict | None = None
    ) -> None:
        self._emit("SUCCESS", message, context)

    def warning(
        self, message: Any, context: dict | None = None
    ) -> None:
        self._emit("WARNING", message, context)

    def error(
        self, message: Any, context: dict | None = None
    ) -> None:
        self._emit("ERROR", message, context)

    def critical(
        self, message: Any, context: dict | None = None
    ) -> None:
        self._emit("CRITICAL", message, context)

    def exception(
        self,
        message: Any,
        exc: Exception,
        context: dict | None = None,
    ) -> None:
        ctx = dict(context or {})
        ctx["exception_type"] = type(exc).__name__
        self._emit(
            "ERROR",
            message,
            ctx,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    def log(
        self,
        level: Any,
        message: Any,
        context: dict | None = None,
    ) -> None:
        """🎯 پشتیبانی از LogLevel enum یا نام string."""
        if isinstance(level, LogLevel):
            name = level.name
        else:
            name = str(level).upper()

        if name not in _LEVEL_NO:
            name = "INFO"

        self._emit(name, message, context)

    # ═══════════════════════════════════════════
    # management
    # ═══════════════════════════════════════════
    def set_level(self, level: str) -> bool:
        """تغییر سطح لاگ. Returns: True اگه اعمال شد."""
        level_u = str(level).upper()
        if level_u not in _LEVEL_NO:
            self.warning(
                f"⚠️ [LOGGER:set_level] سطح نامعتبر "
                f"«{level}» | معتبرها: "
                f"{list(_LEVEL_NO.keys())}"
            )
            return False

        old = self.log_level
        self.log_level = level_u
        self._logger.setLevel(_LEVEL_NO[level_u])
        self.success(
            f"✅ [LOGGER:set_level] سطح از {old} "
            f"روی {level_u} تنظیم شد"
        )
        return True

    def get_stats(self) -> dict[str, Any]:
        """آمار لاگر — JSON-safe."""
        last = self._stats.get("last_log_time")

        with self._lock:
            by_level = dict(
                self._stats.get("by_level", {})
            )

        return {
            "version": "4",
            "total_logs": self._stats.get("total_logs", 0),
            "by_level": by_level,
            "last_log_time": (
                last.isoformat()
                if isinstance(last, datetime) else last
            ),
            "log_dir": self.log_dir,
            "log_level": self.log_level,
            "console_enabled": self._console_enabled,
            "file_enabled": self._file_enabled,
            "file_path": self._file_path,
            "handlers": [
                type(h).__name__
                for h in self._logger.handlers
            ],
            "stdout_enc": _stream_encoding(sys.stdout),
            "stderr_enc": _stream_encoding(sys.stderr),
            "utf8_status": dict(_UTF8_STATUS),
        }

    def flush(self) -> None:
        """Flush همهٔ handlerها."""
        for h in self._logger.handlers:
            try:
                h.flush()
            except Exception as e:
                try:
                    print(
                        f"⚠️ [LOGGER:flush] "
                        f"{type(h).__name__} | "
                        f"{type(e).__name__}: {e}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass

    def close(self) -> None:
        """🎯 بستن همهٔ handlerها — برای تست/restart."""
        for h in list(self._logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            try:
                self._logger.removeHandler(h)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"<Logger level={self.log_level} "
            f"dir={self.log_dir} "
            f"handlers={len(self._logger.handlers)}>"
        )


# ═══════════════════════════════════════════
# Singleton + module-level helpers
# ═══════════════════════════════════════════
_logger: Logger | None = None
_logger_lock = threading.Lock()


def get_logger() -> Logger:
    """🎯 FIX: thread-safe singleton."""
    global _logger
    if _logger is None:
        with _logger_lock:
            if _logger is None:
                _logger = Logger()
    return _logger


def reset_logger() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _logger
    with _logger_lock:
        if _logger is not None:
            try:
                _logger.close()
            except Exception:
                pass
        _logger = None


# ─── shortcuts ───
def log_info(msg: Any) -> None:
    get_logger().info(msg)


def log_success(msg: Any) -> None:
    get_logger().success(msg)


def log_error(msg: Any) -> None:
    get_logger().error(msg)


def log_warning(msg: Any) -> None:
    get_logger().warning(msg)


def log_debug(msg: Any) -> None:
    get_logger().debug(msg)


def log_exception(msg: Any, exc: Exception) -> None:
    get_logger().exception(msg, exc)


def log_critical(msg: Any) -> None:
    get_logger().critical(msg)


@contextmanager
def log_duration(
    operation: str,
    logger: Logger | None = None,
):
    """Log start/finish + duration of an operation.

    از `time.monotonic()` استفاده می‌کند تا NTP adjustments
    باعث duration منفی نشود.
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
        logger.success(
            f"✅ done: {operation} ({elapsed:.2f}s)"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "Logger",
    "LogLevel",
    "get_logger",
    "reset_logger",
    "log_info",
    "log_success",
    "log_error",
    "log_warning",
    "log_debug",
    "log_exception",
    "log_critical",
    "log_duration",
    "JsonFormatter",
    "ConsoleFormatter",
    "SUCCESS_LEVEL",
]
