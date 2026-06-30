# -*- coding: utf-8 -*-
"""
Structured logging — built on Python's standard `logging`.

- JSON lines to file (ISO-8601 UTC timestamp, level, logger, message, context,
  exception) → ready for ELK / Loki / Datadog / CloudWatch.
- Human-readable (optionally colored) console output.
- Size-based rotation (RotatingFileHandler) + backups.
- Backward-compatible API: log_info / log_success / log_error / log_warning /
  log_debug / log_exception / get_logger / log_duration  (used across the app).
- Configurable via .env:
    LOG_LEVEL (DEBUG|INFO|SUCCESS|WARNING|ERROR), LOG_DIR, LOG_TO_FILE,
    LOG_JSON (true=JSON file, false=plain), LOG_CONSOLE, LOG_MAX_MB, LOG_BACKUPS
"""
import os
import sys
import json
import time
import logging
import logging.handlers
import traceback
from enum import Enum
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any, Dict, Optional

from config import SYSTEM_CONFIG

# Don't let a logging/encoding hiccup crash the app
logging.raiseExceptions = False

# Custom SUCCESS level between INFO(20) and WARNING(30)
SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

_LEVEL_NO = {
    "DEBUG": logging.DEBUG, "INFO": logging.INFO, "SUCCESS": SUCCESS_LEVEL,
    "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL,
}
_EMOJI = {"DEBUG": "🐛", "INFO": "ℹ️", "SUCCESS": "✅",
          "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🔥"}
_COLOR = {"DEBUG": "\033[90m", "INFO": "\033[94m", "SUCCESS": "\033[92m",
          "WARNING": "\033[93m", "ERROR": "\033[91m", "CRITICAL": "\033[95m"}
_RESET = "\033[0m"


class LogLevel(Enum):
    """Kept for backward compatibility with older call sites."""
    DEBUG = "🐛"
    INFO = "ℹ️"
    SUCCESS = "✅"
    WARNING = "⚠️"
    ERROR = "❌"
    CRITICAL = "🔥"


class JsonFormatter(logging.Formatter):
    """One machine-readable JSON object per line."""
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "context", None)
        if ctx:
            obj["context"] = ctx
        if record.exc_info:
            obj["exception"] = "".join(traceback.format_exception(*record.exc_info))[-2000:]
        return json.dumps(obj, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Readable line for the terminal (colored on real TTYs)."""
    def __init__(self):
        super().__init__()
        try:
            self._color = (not sys.platform.startswith("win")) and sys.stdout.isatty()
        except Exception:
            self._color = False

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        emoji = _EMOJI.get(record.levelname, "")
        line = f"[{ts}] {emoji} {record.getMessage()}"
        ctx = getattr(record, "context", None)
        if ctx:
            line += f" | {json.dumps(ctx, ensure_ascii=False)}"
        if self._color:
            line = f"{_COLOR.get(record.levelname, '')}{line}{_RESET}"
        return line


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


class Logger:
    """Singleton wrapper around a stdlib logger named 'srooyesh'."""
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

        self.log_dir = os.getenv("LOG_DIR", SYSTEM_CONFIG.get("log_directory", "storage/logs"))
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_level = os.getenv("LOG_LEVEL", SYSTEM_CONFIG.get("log_level", "INFO")).upper()

        self._logger = logging.getLogger("srooyesh")
        self._logger.setLevel(_LEVEL_NO.get(self.log_level, logging.INFO))
        self._logger.handlers.clear()
        self._logger.propagate = False

        if _env_bool("LOG_CONSOLE", "true"):
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(ConsoleFormatter())
            self._logger.addHandler(ch)

        if SYSTEM_CONFIG.get("log_to_file", True) and _env_bool("LOG_TO_FILE", "true"):
            max_mb = float(os.getenv("LOG_MAX_MB", "10"))
            backups = int(os.getenv("LOG_BACKUPS", "30"))
            as_json = _env_bool("LOG_JSON", "true")
            fname = "app.jsonl" if as_json else "app.log"
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(self.log_dir, fname),
                maxBytes=int(max_mb * 1024 * 1024), backupCount=backups, encoding="utf-8",
            )
            fh.setFormatter(JsonFormatter() if as_json else ConsoleFormatter())
            self._logger.addHandler(fh)

        self._stats = {"total_logs": 0, "by_level": {}, "last_log_time": None}

    # ---- core ----
    def _emit(self, levelname: str, message: str, context: Optional[Dict] = None, exc_info=None):
        levelno = _LEVEL_NO.get(levelname, logging.INFO)
        self._stats["total_logs"] += 1
        self._stats["by_level"][levelname] = self._stats["by_level"].get(levelname, 0) + 1
        self._stats["last_log_time"] = datetime.now()
        extra = {"context": context} if context else None
        self._logger.log(levelno, message, extra=extra, exc_info=exc_info)

    # ---- convenience ----
    def debug(self, message: str, context: Optional[Dict] = None):
        self._emit("DEBUG", message, context)

    def info(self, message: str, context: Optional[Dict] = None):
        self._emit("INFO", message, context)

    def success(self, message: str, context: Optional[Dict] = None):
        self._emit("SUCCESS", message, context)

    def warning(self, message: str, context: Optional[Dict] = None):
        self._emit("WARNING", message, context)

    def error(self, message: str, context: Optional[Dict] = None):
        self._emit("ERROR", message, context)

    def critical(self, message: str, context: Optional[Dict] = None):
        self._emit("CRITICAL", message, context)

    def exception(self, message: str, exc: Exception, context: Optional[Dict] = None):
        ctx = dict(context or {})
        ctx["exception_type"] = type(exc).__name__
        self._emit("ERROR", message, ctx, exc_info=(type(exc), exc, exc.__traceback__))

    def log(self, level, message: str, context: Optional[Dict] = None):
        """Accepts a LogLevel enum or a level name string (backward compatible)."""
        name = level.name if isinstance(level, LogLevel) else str(level).upper()
        self._emit(name, message, context)

    # ---- management ----
    def set_level(self, level: str):
        level = level.upper()
        if level in _LEVEL_NO:
            self.log_level = level
            self._logger.setLevel(_LEVEL_NO[level])
            self.info(f"Log level set to {level}")

    def get_stats(self) -> Dict[str, Any]:
        return {**self._stats, "log_dir": self.log_dir, "log_level": self.log_level}

    def flush(self):
        for h in self._logger.handlers:
            try:
                h.flush()
            except Exception:
                pass


# ==================================================
# Singleton + module-level helpers (stable public API)
# ==================================================
_logger: Optional[Logger] = None


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
def log_duration(operation: str, logger: Logger = None):
    """Log start/finish + duration of an operation."""
    logger = logger or get_logger()
    start = time.time()
    logger.info(f"start: {operation}")
    try:
        yield
    except Exception as e:
        logger.exception(f"error in {operation}", e)
        raise
    finally:
        logger.success(f"done: {operation} ({time.time() - start:.2f}s)")
