"""توابع کمک‌کننده — sandbox، access، helpers."""
from __future__ import annotations

import json
import locale
import os
import platform
import re
import sys
import threading
from pathlib import Path

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
# PersianBrainHandler
# ═══════════════════════════════════════════
class PersianBrainHandler:
    """تنظیم محیط فارسی برای subprocess و console."""

    REVERSE_MAP = {
        "ي": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "ھ": "ه",
        "ے": "ی",
        "ۓ": "ی",
    }

    @staticmethod
    def setup_environment():
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
                sys.stderr.reconfigure(encoding="utf-8")
            try:
                locale.setlocale(locale.LC_ALL, "fa_IR.UTF-8")
            except Exception:
                try:
                    locale.setlocale(
                        locale.LC_ALL, "Persian_Iran.1256"
                    )
                except Exception:
                    locale.setlocale(locale.LC_ALL, "")
            if platform.system() == "Windows":
                os.system("color")
                os.system("chcp 65001 > nul 2>&1")
            os.environ["PYTHONIOENCODING"] = "utf-8"
            os.environ["LANG"] = "fa_IR.UTF-8"
            log_info("🌐 [BRAIN:env] محیط فارسی تنظیم شد")
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:env] تنظیم ناموفق | "
                f"{type(e).__name__}: {e}"
            )

    @staticmethod
    def fix(text: str) -> str:
        """اصلاح کاراکترهای عربی به فارسی + حذف کاراکترهای مخفی."""
        if not text:
            return text
        for wrong, correct in PersianBrainHandler.REVERSE_MAP.items():
            text = text.replace(wrong, correct)
        text = (
            text.replace("\u200e", "")
            .replace("\u200f", "")
            .replace("\u202b", "")
            .replace("\u202e", "")
        )
        return text


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════
def human_bytes(n: int) -> str:
    """تبدیل بایت به فرمت خوانا."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def safe_path(path_str: str, base: Path | None = None) -> Path:
    """مسیر امن — جلوگیری از escape از پروژه."""
    base = (base or Path(PROJECT_BASE)).resolve()
    full = (base / path_str).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        log_warning(
            f"⛔ [BRAIN:path] escape blocked | "
            f"base={base} | requested={path_str!r}"
        )
        raise ValueError("⛔ دسترسی به خارج از پروژه ممنوع!")
    return full


def sanitize_console_output(text: str) -> str:
    """پاک‌سازی ANSI escape codes و کنترل کاراکترها."""
    if not text:
        return text
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


# ═══════════════════════════════════════════
# Access loader (mtime-aware)
# ═══════════════════════════════════════════
_ACCESS_CACHE: dict | None = None
_ACCESS_CACHE_MTIME: float = 0.0
_ACCESS_LOCK = threading.Lock()


def _load_access() -> dict:
    """دسترسی‌ها را از فایل می‌خواند — با cache mtime-aware."""
    global _ACCESS_CACHE, _ACCESS_CACHE_MTIME

    try:
        mtime = (
            _ACCESS_FILE.stat().st_mtime
            if _ACCESS_FILE.exists() else 0.0
        )
    except OSError:
        mtime = 0.0

    with _ACCESS_LOCK:
        if _ACCESS_CACHE is not None and mtime == _ACCESS_CACHE_MTIME:
            return _ACCESS_CACHE

    defaults = {
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

    new_data = defaults
    try:
        if _ACCESS_FILE.exists():
            with open(_ACCESS_FILE, encoding="utf-8") as f:
                new_data = json.load(f)
            log_info(
                f"🔐 [BRAIN:access] بارگذاری شد | "
                f"file={_ACCESS_FILE.name} | "
                f"keys={list(new_data.keys())} | mtime={mtime:.0f}"
            )
        else:
            log_warning(
                f"⚠️ [BRAIN:access] فایل نبود: {_ACCESS_FILE}"
            )
    except json.JSONDecodeError as e:
        log_error(
            f"❌ [BRAIN:access] JSON نامعتبر | "
            f"line={e.lineno} col={e.colno} | {e.msg}"
        )
    except Exception as e:
        log_error(
            f"❌ [BRAIN:access] بارگذاری ناموفق | "
            f"{type(e).__name__}: {e}"
        )

    with _ACCESS_LOCK:
        _ACCESS_CACHE = new_data
        _ACCESS_CACHE_MTIME = mtime
    return new_data


def _invalidate_access_cache() -> None:
    """cache دسترسی‌ها را پاک می‌کند."""
    global _ACCESS_CACHE, _ACCESS_CACHE_MTIME
    with _ACCESS_LOCK:
        _ACCESS_CACHE = None
        _ACCESS_CACHE_MTIME = 0.0


# ═══════════════════════════════════════════
# Sandbox Preamble (برای execute_python)
# ═══════════════════════════════════════════
SAFE_PREAMBLE = (
    "import builtins, os, io, sys\n"
    "from pathlib import Path as _P\n"
    "_ALLOWED = [\n"
    "    os.path.abspath('storage/reports'),\n"
    "]\n"
    "def _ok(p):\n"
    "    ap = os.path.abspath(str(p))\n"
    "    for d in _ALLOWED:\n"
    "        if ap == d or ap.startswith(d + os.sep): return True\n"
    "    return False\n"
    "def _deny(p):\n"
    "    raise PermissionError(\n"
    "        '\\u26d4 ONLY READ (or write in storage/reports/) — blocked: '\n"
    "        + str(p)\n"
    "    )\n"
    "_ro = builtins.open\n"
    "def _so(f, m='r', *a, **k):\n"
    "    if any(c in str(m) for c in 'wax+'):\n"
    "        if not _ok(f): _deny(f)\n"
    "        try:\n"
    "            os.makedirs(os.path.dirname(os.path.abspath(str(f))), exist_ok=True)\n"
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


# سازگاری با نام قبلی
_SAFE_PREAMBLE = SAFE_PREAMBLE
