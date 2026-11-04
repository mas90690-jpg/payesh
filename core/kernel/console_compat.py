"""Windows console compatibility shim.

مشکل: وقتی code page فعال cp437/cp1252 است ولی کد متن فارسی/ایموجی
چاپ می‌کند، `UnicodeEncodeError` رخ می‌دهد → crash. این ماژول
stdout/stderr را به UTF-8 با errors='replace' تغییر می‌دهد تا هرگز
crash نشود.

نحوه استفاده:
    from core.kernel.console_compat import patch_console
    patch_console()  # قبل از هر print/import که چاپ می‌کند

⚠️ این فایل MUST-BE-SAFE است:
  - نمی‌تواند از core.kernel.logger استفاده کند (circular import)
  - نمی‌تواند از config import کند (شاید config پیش‌نیاز داشته باشد)
  - هر خطای این فایل نباید پروژه را بترکاند
  - در حالت عادی بی‌صدا کار می‌کند؛ فقط در خطا یا verbose پیام می‌دهد

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `_patch_stream` حالا fallback به `io.TextIOWrapper`
    دارد اگر `reconfigure` نبود (نه فقط `skipped`).
  - 🐛 FIX: `patch_console` حالا idempotent است (flag داخلی).
  - 🐛 FIX: اگر stream از قبل UTF-8 باشد، skip می‌شود (سریع‌تر).
  - 🐛 FIX: `errors` قابل تنظیم از env (`CONSOLE_ERRORS`).
  - 🎯 `restore_console()` — بازگرداندن streams اصلی (برای تست).
  - 🎯 `is_patched()` helper.
  - 🎯 `get_status()` — وضعیت فعلی.
  - 🎯 `force=True` option برای اجبار به patch.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
"""
from __future__ import annotations

import io
import os
import sys
from typing import Any


# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
_UTF8 = "utf-8"
_ERRORS = (
    os.getenv("CONSOLE_ERRORS", "replace").strip()
    or "replace"
)

# ─── state ───
_patched_streams: dict[str, Any] = {}   # {"stdout": <new>, "stderr": <new>}
_original_streams: dict[str, Any] = {}  # {"stdout": <orig>, ...}
_is_patched: bool = False


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _safe_stderr_write(msg: str) -> None:
    """نوشتن روی stderr اصلی (`sys.__stderr__`) — بی‌خطر.

    حتی اگر stderr یوزر خراب باشد، این از `sys.__stderr__` استفاده
    می‌کند که همیشه توسط Python تعریف شده.
    """
    msg_with_newline = msg + "\n"
    for stream in (
        getattr(sys, "__stderr__", None),
        sys.stderr,
    ):
        if stream is None:
            continue
        try:
            stream.write(msg_with_newline)
            try:
                stream.flush()
            except Exception:
                pass
            return
        except Exception:
            continue


def _current_encoding(stream: Any) -> str:
    """🎯 encoding فعلی stream."""
    if stream is None:
        return ""
    try:
        enc = getattr(stream, "encoding", None)
        return str(enc).lower() if enc else ""
    except Exception:
        return ""


def _is_utf8(stream: Any) -> bool:
    """🎯 آیا stream از قبل UTF-8 است؟"""
    enc = _current_encoding(stream)
    return enc in ("utf-8", "utf8")


def _wrap_with_utf8(stream: Any) -> Any:
    """🎯 FIX: wrap کردن stream با TextIOWrapper UTF-8.

    Returns:
        wrapper امن یا None اگر ممکن نبود.
    """
    if stream is None:
        return None
    try:
        buf = getattr(stream, "buffer", None)
        if buf is None:
            return None
        return io.TextIOWrapper(
            buf,
            encoding=_UTF8,
            errors=_ERRORS,
            line_buffering=True,
            write_through=True,
        )
    except Exception:
        return None


def _patch_stream(
    stream: Any, name: str, force: bool = False
) -> str:
    """پچ یک استریم به UTF-8.

    Args:
        stream: آبجکت استریم (sys.stdout / sys.stderr).
        name:   اسم برای پیام‌های خطا.
        force:  اجبار به patch (حتی اگر از قبل UTF-8 باشد).

    Returns:
        "already_utf8" | "patched" | "wrapped" | "skipped" | "error"
        | "no_stream"
    """
    if stream is None:
        return "no_stream"

    # 🎯 FIX: اگر از قبل UTF-8 است، skip
    if not force and _is_utf8(stream):
        return "already_utf8"

    # ─── flush قبل از تغییر ───
    try:
        if hasattr(stream, "flush"):
            stream.flush()
    except Exception:
        pass

    # ─── try reconfigure ───
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(
                encoding=_UTF8,
                errors=_ERRORS,
            )
            return "patched"
        except Exception as e:
            _safe_stderr_write(
                f"⚠️ [CONSOLE] reconfigure {name} | "
                f"{type(e).__name__}: {e}"
            )
            # ادامه به fallback

    # ─── fallback: wrap ───
    wrapped = _wrap_with_utf8(stream)
    if wrapped is not None:
        try:
            setattr(sys, name, wrapped)
            return "wrapped"
        except Exception as e:
            _safe_stderr_write(
                f"⚠️ [CONSOLE] wrap {name} | "
                f"{type(e).__name__}: {e}"
            )
            return "error"

    return "skipped"


# ═══════════════════════════════════════════
# API اصلی
# ═══════════════════════════════════════════
def patch_console(
    verbose: bool = False,
    force: bool = False,
) -> dict[str, str]:
    """stdout/stderr را به UTF-8 تغییر می‌دهد.

    تا ایموجی/فارسی هرگز crash نکند.

    Args:
        verbose: اگه True، نتیجه را روی stderr چاپ می‌کند.
        force:   اگه True، حتی اگر از قبل UTF-8 باشد، patch می‌کند.

    Returns:
        dict با کلیدهای "stdout", "stderr", "encoding", "errors",
        "idempotent".
    """
    global _is_patched

    # 🎯 FIX: idempotency
    if _is_patched and not force:
        result = {
            "stdout": "already_patched",
            "stderr": "already_patched",
            "encoding": _UTF8,
            "errors": _ERRORS,
            "idempotent": "true",
        }
        if verbose:
            _safe_stderr_write(
                "🔧 [CONSOLE] patch_console → "
                "(قبلاً patch شده)"
            )
        return result

    # ─── ذخیرهٔ streams اصلی ───
    if not _original_streams:
        try:
            _original_streams["stdout"] = sys.stdout
            _original_streams["stderr"] = sys.stderr
        except Exception:
            pass

    # ─── patch ───
    stdout_status = _patch_stream(
        sys.stdout, "stdout", force=force
    )
    stderr_status = _patch_stream(
        sys.stderr, "stderr", force=force
    )

    _is_patched = True

    result = {
        "stdout": stdout_status,
        "stderr": stderr_status,
        "encoding": _UTF8,
        "errors": _ERRORS,
        "idempotent": "false",
    }

    if verbose:
        _safe_stderr_write(
            f"🔧 [CONSOLE] patch_console → "
            f"stdout={stdout_status} "
            f"stderr={stderr_status} "
            f"({_UTF8}/{_ERRORS})"
        )
    return result


def restore_console() -> dict[str, str]:
    """🎯 بازگرداندن stdout/stderr به حالت قبل.

    فقط برای تست — در runtime صدا نزن.

    Returns:
        dict با وضعیت بازگردانی.
    """
    global _is_patched

    result: dict[str, str] = {}

    for name in ("stdout", "stderr"):
        original = _original_streams.get(name)
        if original is None:
            result[name] = "no_original"
            continue
        try:
            setattr(sys, name, original)
            result[name] = "restored"
        except Exception as e:
            result[name] = f"error:{type(e).__name__}"

    _is_patched = False
    return result


def is_patched() -> bool:
    """🎯 آیا console در این session patch شده است؟"""
    return _is_patched


def get_status() -> dict[str, Any]:
    """🎯 وضعیت فعلی streams — برای debug."""
    return {
        "patched": _is_patched,
        "stdout_encoding": _current_encoding(sys.stdout),
        "stderr_encoding": _current_encoding(sys.stderr),
        "target_encoding": _UTF8,
        "errors_policy": _ERRORS,
        "platform": sys.platform,
        "has_original_stdout": (
            "stdout" in _original_streams
        ),
        "has_original_stderr": (
            "stderr" in _original_streams
        ),
    }


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "patch_console",
    "restore_console",
    "is_patched",
    "get_status",
]


# ═══════════════════════════════════════════
# اجرای مستقیم — برای تست
# ═══════════════════════════════════════════
if __name__ == "__main__":
    r = patch_console(verbose=True, force=True)
    print(f"✅ نتیجه: {r}")
    print(f"📊 وضعیت: {get_status()}")
    # تست فارسی و ایموجی
    print("🧪 تست: سلام — کود مایع — یاراترا ✅❌⚠️")
