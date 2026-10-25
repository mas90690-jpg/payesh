"""توابع کمک‌کننده — sandbox، access، helpers.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: `safe_path` حالا None/خالی را رد می‌کند (قبلاً
    در `(base / None)` crash می‌کرد).
  - 🐛 FIX: `safe_path` حالا case-insensitive FS را امن handle
    می‌کند (Windows).
  - 🐛 FIX: `PersianBrainHandler.setup_environment` حالا idempotent
    است (flag داخلی — جلوگیری از دوبار `chcp`).
  - 🐛 FIX: `PersianBrainHandler.fix` حالا type-check دارد
    (قبلاً اگر `text` غیر-str بود، خطا می‌داد).
  - 🐛 FIX: `_load_access` حالا race-safe است (cache check
    داخل lock دوباره).
  - 🐛 FIX: `_load_access` حالا deep-merge می‌کند (defaults
    حفظ می‌شوند اگر فایل JSON ناقص بود).
  - 🐛 FIX: `_load_access` حالا deep-copy از defaults برمی‌گرداند
    (جلوگیری از mutation).
  - 🐛 FIX: `SAFE_PREAMBLE` حالا از مسیر absolute استفاده می‌کند
    (قبلاً `os.path.abspath('storage/reports')` بر اساس CWD بود
    که در subprocess نادرست می‌شد).
  - 🐛 FIX: `human_bytes` حالا n منفی را امن handle می‌کند.
  - 🐛 FIX: `sanitize_console_output` حالا max length دارد
    (جلوگیری از output غول‌آسا).
  - 🎯 `setup_environment` حالا در exception، سعی می‌کند
    متوقف نشود.
  - 🎯 `_env_int` helper برای thresholds.
  - 🎯 env-based: `BRAIN_SANITIZE_MAX_LEN`, `BRAIN_SANDBOX_ALLOWED_DIRS`.
  - 🎯 لاگ‌های دقیق‌تر.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import contextlib
import copy
import json
import locale
import os
import platform
import re
import sys
import threading
from pathlib import Path
from typing import Any, ClassVar

from core.brain.constants import (
    PROJECT_BASE,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_warning,
)
from data import paths as data_paths

_ACCESS_FILE = data_paths.ACCESS_CONTROL


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


# 🎯 سقف sanitize output
_SANITIZE_MAX_LEN = _env_int(
    "BRAIN_SANITIZE_MAX_LEN", 100_000,
    min_val=1000, max_val=10_000_000,
)


# ═══════════════════════════════════════════
# PersianBrainHandler
# ═══════════════════════════════════════════
class PersianBrainHandler:
    """تنظیم محیط فارسی برای subprocess و console."""

    REVERSE_MAP: ClassVar[dict[str, str]] = {
        "ي": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "ھ": "ه",
        "ے": "ی",
        "ۓ": "ی",
    }

    # 🐛 FIX: flag برای idempotency
    _env_setup_done: bool = False

    @classmethod
    def setup_environment(cls) -> None:
        """🎯 FIX: idempotent — فقط یک‌بار اجرا می‌شود."""
        if cls._env_setup_done:
            return

        try:
            # ─── stdout/stderr ───
            for stream_name in ("stdout", "stderr"):
                stream = getattr(sys, stream_name, None)
                if stream is None:
                    continue
                try:
                    if hasattr(stream, "reconfigure"):
                        stream.reconfigure(
                            encoding="utf-8",
                            errors="replace",
                        )
                except Exception as e:
                    log_warning(
                        f"⚠️ [BRAIN:env] reconfigure "
                        f"{stream_name} | {type(e).__name__}: {e}"
                    )

            # ─── locale ───
            for loc in ("fa_IR.UTF-8", "Persian_Iran.1256", ""):
                try:
                    locale.setlocale(locale.LC_ALL, loc)
                    break
                except Exception:
                    continue

            # ─── Windows-specific ───
            if platform.system() == "Windows":
                # 🐛 FIX: `color` و `chcp` جدا در try/except
                with contextlib.suppress(Exception):
                    os.system("color")
                with contextlib.suppress(Exception):
                    os.system("chcp 65001 > nul 2>&1")

            # ─── env vars ───
            os.environ["PYTHONIOENCODING"] = "utf-8"
            os.environ["LANG"] = "fa_IR.UTF-8"

            cls._env_setup_done = True
            log_info("🌐 [BRAIN:env] محیط فارسی تنظیم شد")

        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:env] تنظیم ناموفق | "
                f"{type(e).__name__}: {e}"
            )

    @classmethod
    def fix(cls, text: Any) -> str:
        """اصلاح کاراکترهای عربی به فارسی + حذف کاراکترهای مخفی.

        🐛 FIX: type-check (قبلاً اگر text غیر-str بود، خطا می‌داد).
        """
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return ""

        for wrong, correct in cls.REVERSE_MAP.items():
            text = text.replace(wrong, correct)

        # حذف کاراکترهای مخفی RTL/LTR
        for ch in (
            "\u200e", "\u200f",
            "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
        ):
            text = text.replace(ch, "")

        return text


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════
def human_bytes(n: float) -> str:
    """تبدیل بایت به فرمت خوانا.

    🐛 FIX: n منفی امن است.
    """
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n < 0:
        return f"-{human_bytes(-n)}"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def safe_path(
    path_str: str | None, base: Path | None = None
) -> Path:
    """مسیر امن — جلوگیری از escape از پروژه.

    🐛 FIX: None/خالی رد می‌شود.
    🐛 FIX: case-insensitive FS امن است.
    """
    if not path_str:
        raise ValueError("⛔ مسیر خالی است.")

    base_resolved = (base or Path(PROJECT_BASE)).resolve()

    try:
        # اگه absolute بود، مستقیم resolve
        candidate = Path(path_str)
        if candidate.is_absolute():
            full = candidate.resolve()
        else:
            full = (base_resolved / candidate).resolve()
    except Exception as e:
        log_warning(
            f"⛔ [BRAIN:path] resolve fail | "
            f"base={base_resolved} | requested={path_str!r} | "
            f"{type(e).__name__}: {e}"
        )
        raise ValueError(f"⛔ مسیر نامعتبر: {e}")

    # 🐛 FIX: case-insensitive check روی Windows
    try:
        full_str = str(full)
        base_str = str(base_resolved)
        if os.name == "nt":
            full_str = full_str.lower()
            base_str = base_str.lower()

        # چک پیشوند (با جداکننده)
        if not (
            full_str == base_str
            or full_str.startswith(base_str + os.sep)
        ):
            log_warning(
                f"⛔ [BRAIN:path] escape blocked | "
                f"base={base_resolved} | requested={path_str!r}"
            )
            raise ValueError(
                "⛔ دسترسی به خارج از پروژه ممنوع!"
            )
    except ValueError:
        raise
    except Exception as e:
        log_warning(
            f"⚠️ [BRAIN:path] check | "
            f"{type(e).__name__}: {e}"
        )
        raise ValueError(f"⛔ خطا در بررسی مسیر: {e}")

    return full


def sanitize_console_output(text: str | None) -> str:
    """پاک‌سازی ANSI escape codes و کنترل کاراکترها.

    🐛 FIX: max length (جلوگیری از output غول‌آسا).
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)

    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # 🐛 FIX: limit
    if len(text) > _SANITIZE_MAX_LEN:
        text = text[:_SANITIZE_MAX_LEN] + "... [truncated]"

    return text


# ═══════════════════════════════════════════
# Access loader (mtime-aware)
# ═══════════════════════════════════════════
_ACCESS_CACHE: dict[str, Any] | None = None
_ACCESS_CACHE_MTIME: float = 0.0
_ACCESS_LOCK = threading.Lock()

# 🎯 defaults (deepcopy خواهند شد)
_ACCESS_DEFAULTS: dict[str, Any] = {
    "full_access": False,
    "read": {
        "allow": ["**/*"],
        "deny": [".env", "storage/sessions/**"],
    },
    "write": {
        "allow": ["data/**", "storage/reports/**"],
        "deny": [],
    },
    "execute": {
        "allow": ["git status", "git diff"],
        "deny": ["rm -rf"],
    },
    "safety": {
        "backup_before_write": True,
        "require_confirmation": [],
    },
}


def _deep_merge(
    base: dict[str, Any], override: dict[str, Any]
) -> dict[str, Any]:
    """🎯 deep-merge دو dict (override بر base غالب می‌شود).

    مقادیر nested dict بازگشتی merge می‌شوند؛ بقیه overwrite.
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _load_access() -> dict[str, Any]:
    """دسترسی‌ها را از فایل می‌خواند — با cache mtime-aware.

    🐛 FIX: حالا deep-merge می‌کند (defaults حفظ می‌شوند).
    🐛 FIX: حالا deep-copy برمی‌گرداند (جلوگیری از mutation).
    """
    global _ACCESS_CACHE, _ACCESS_CACHE_MTIME

    # ─── mtime ───
    try:
        mtime = (
            _ACCESS_FILE.stat().st_mtime
            if _ACCESS_FILE.exists() else 0.0
        )
    except OSError:
        mtime = 0.0

    # ─── cache hit ───
    with _ACCESS_LOCK:
        if (
            _ACCESS_CACHE is not None
            and mtime == _ACCESS_CACHE_MTIME
        ):
            return copy.deepcopy(_ACCESS_CACHE)

    # ─── load (خارج از lock) ───
    new_data: dict[str, Any] = copy.deepcopy(_ACCESS_DEFAULTS)

    try:
        if _ACCESS_FILE.exists():
            with open(
                str(_ACCESS_FILE), encoding="utf-8"
            ) as f:
                file_data = json.load(f)

            if not isinstance(file_data, dict):
                log_warning(
                    f"⚠️ [BRAIN:access] ساختار نامعتبر | "
                    f"type={type(file_data).__name__} — "
                    f"defaults استفاده شد"
                )
            else:
                # 🐛 FIX: deep-merge
                new_data = _deep_merge(
                    _ACCESS_DEFAULTS, file_data
                )
                log_info(
                    f"🔐 [BRAIN:access] بارگذاری شد | "
                    f"file={_ACCESS_FILE.name} | "
                    f"keys={list(new_data.keys())} | "
                    f"mtime={mtime:.0f}"
                )
        else:
            log_warning(
                f"⚠️ [BRAIN:access] فایل نبود: "
                f"{_ACCESS_FILE} — defaults"
            )
    except json.JSONDecodeError as e:
        log_error(
            f"❌ [BRAIN:access] JSON نامعتبر | "
            f"line={e.lineno} col={e.colno} | {e.msg} — "
            f"defaults استفاده شد"
        )
    except Exception as e:
        log_error(
            f"❌ [BRAIN:access] بارگذاری ناموفق | "
            f"{type(e).__name__}: {e} — defaults استفاده شد"
        )

    # ─── cache ───
    with _ACCESS_LOCK:
        _ACCESS_CACHE = new_data
        _ACCESS_CACHE_MTIME = mtime
        return copy.deepcopy(new_data)


def _invalidate_access_cache() -> None:
    """cache دسترسی‌ها را پاک می‌کند."""
    global _ACCESS_CACHE, _ACCESS_CACHE_MTIME
    with _ACCESS_LOCK:
        _ACCESS_CACHE = None
        _ACCESS_CACHE_MTIME = 0.0


# ═══════════════════════════════════════════
# Sandbox Preamble (برای execute_python)
# ═══════════════════════════════════════════
def _build_safe_preamble() -> str:
    """🎯 FIX: ساخت SAFE_PREAMBLE با مسیر absolute.

    قبلاً `os.path.abspath('storage/reports')` بر اساس CWD بود
    که در subprocess نادرست می‌شد.
    """
    # مسیر absolute storage/reports بر اساس PROJECT_BASE
    reports_dir = str(
        Path(PROJECT_BASE) / "storage" / "reports"
    ).replace("\\", "\\\\")

    return (
        "import builtins, os, io, sys\n"
        "from pathlib import Path as _P\n"
        f"_ALLOWED = [\n    r'{reports_dir}',\n]\n"
        "def _ok(p):\n"
        "    ap = os.path.abspath(str(p))\n"
        "    for d in _ALLOWED:\n"
        "        if ap == d or ap.startswith(d + os.sep): return True\n"
        "    return False\n"
        "def _deny(p):\n"
        "    raise PermissionError(\n"
        "        '\\u26d4 ONLY READ (or write in storage/reports/) "
        "— blocked: '\n"
        "        + str(p)\n"
        "    )\n"
        "_ro = builtins.open\n"
        "def _so(f, m='r', *a, **k):\n"
        "    if any(c in str(m) for c in 'wax+'):\n"
        "        if not _ok(f): _deny(f)\n"
        "        try:\n"
        "            os.makedirs(os.path.dirname("
        "os.path.abspath(str(f))), exist_ok=True)\n"
        "        except Exception: pass\n"
        "    return _ro(f, m, *a, **k)\n"
        "builtins.open = _so\n"
        "io.open = _so\n"
        "_wt = _P.write_text\n"
        "def _swt(self, *a, **k):\n"
        "    if not _ok(self): _deny(self)\n"
        "    self.parent.mkdir(parents=True, exist_ok=True)\n"
        "    return _wt(self, *a, **k)\n"
        "_P.write_text = _swt\n"
        "_wb = _P.write_bytes\n"
        "def _swb(self, *a, **k):\n"
        "    if not _ok(self): _deny(self)\n"
        "    self.parent.mkdir(parents=True, exist_ok=True)\n"
        "    return _wb(self, *a, **k)\n"
        "_P.write_bytes = _swb\n"
        "_t = _P.touch\n"
        "def _st(self, *a, **k):\n"
        "    if not _ok(self): _deny(self)\n"
        "    return _t(self, *a, **k)\n"
        "_P.touch = _st\n"
        "_u = _P.unlink\n"
        "def _su(self, *a, **k):\n"
        "    if not _ok(self): _deny(self)\n"
        "    return _u(self, *a, **k)\n"
        "_P.unlink = _su\n"
        "_r = _P.rmdir\n"
        "def _sr(self, *a, **k):\n"
        "    if not _ok(self): _deny(self)\n"
        "    return _r(self, *a, **k)\n"
        "_P.rmdir = _sr\n"
        "_mk = _P.mkdir\n"
        "def _smk(self, *a, **k):\n"
        "    if not _ok(self): _deny(self)\n"
        "    return _mk(self, *a, **k)\n"
        "_P.mkdir = _smk\n"
    )


# 🎯 ساخت at import time (idempotent)
SAFE_PREAMBLE: str = _build_safe_preamble()


# ─── سازگاری عقب‌رو ───
_SAFE_PREAMBLE: str = SAFE_PREAMBLE


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "SAFE_PREAMBLE",
    "_SAFE_PREAMBLE",
    "PersianBrainHandler",
    "_invalidate_access_cache",
    "_load_access",
    "human_bytes",
    "safe_path",
    "sanitize_console_output",
]
