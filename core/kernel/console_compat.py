"""
Windows console compatibility shim.

مشکل: وقتی code page فعال cp437/cp1252 است ولی کد متن فارسی/ایموجی چاپ
می‌کند، UnicodeEncodeError رخ می‌دهد → crash. این ماژول stdout/stderr را
به UTF-8 با errors='replace' تغییر می‌دهد تا هرگز crash نشود.

نحوه استفاده:
    from core.kernel.console_compat import patch_console
    patch_console()  # قبل از هر print/import که چاپ می‌کند

⚠️ این فایل MUST-BE-SAFE است:
  - نمی‌تواند از core.kernel.logger استفاده کند (circular import)
  - نمی‌تواند از config import کند (شاید config خودش پیش‌نیاز داشته باشد)
  - هر خطای این فایل نباید پروژه را بترکاند
  - در حالت عادی بی‌صدا کار می‌کند؛ فقط در خطا یا verbose پیام می‌دهد

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: خطاها دیگر بی‌صدا نیستند (به sys.__stderr__ چاپ می‌شوند)
  - 🎯 patch_console() حالا dict برمی‌گرداند:
      {"stdout": "patched"|"skipped"|"error", "stderr": ..., "encoding": "utf-8"}
  - 🎯 گزینهٔ verbose برای چاپ نتیجه (برای دیباگ)
  - 🎯 flush قبل از reconfigure (اگه چیزی buffer شده، از دست نره)
  - 🧹 حذف import مرده (IO)
  - 🧹 from __future__ import annotations
  - 🧹 type hints دقیق
"""
from __future__ import annotations

import sys
from typing import Any

# نام encoding و errors انتخابی — قابل تغییر در آینده
_UTF8 = "utf-8"
_ERRORS = "replace"


def _safe_stderr_write(msg: str) -> None:
    """نوشتن روی stderr اصلی (sys.__stderr__) — بی‌خطر.

    حتی اگه stderr یوزر خراب باشه، این از sys.__stderr__ استفاده می‌کند که
    همیشه توسط Python تعریف شده.
    """
    for stream in (getattr(sys, "__stderr__", None), sys.stderr):
        if stream is None:
            continue
        try:
            stream.write(msg + "\n")
            try:
                stream.flush()
            except Exception:
                pass
            return
        except Exception:
            continue


def _patch_stream(stream: Any | None, name: str) -> str:
    """پچ یک استریم به UTF-8.

    Args:
        stream: آبجکت استریم (sys.stdout / sys.stderr).
        name:   اسم برای پیام‌های خطا.

    Returns:
        "patched" | "skipped" | "error" | "no_stream"
    """
    if stream is None:
        return "no_stream"

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return "skipped"

    # flush قبل از تغییر (تا داده‌های buffer شده با encoding قدیمی از دست نره)
    try:
        if hasattr(stream, "flush"):
            stream.flush()
    except Exception:
        pass

    try:
        reconfigure(encoding=_UTF8, errors=_ERRORS)
        return "patched"
    except Exception as e:
        _safe_stderr_write(
            f"⚠️ [CONSOLE] patch {name} ناموفق | "
            f"{type(e).__name__}: {e}"
        )
        return "error"


def patch_console(verbose: bool = False) -> dict[str, str]:
    """stdout/stderr را به UTF-8 تغییر می‌دهد تا ایموجی/فارسی هرگز crash نکند.

    Args:
        verbose: اگه True، نتیجه را روی stderr چاپ می‌کند.

    Returns:
        dict با کلیدهای "stdout", "stderr", "encoding", "errors".
        هر مقدار یکی از: "patched" | "skipped" | "error" | "no_stream".

    Safe on all platforms. No-op when the stream already handles Unicode.
    """
    result = {
        "stdout": _patch_stream(sys.stdout, "stdout"),
        "stderr": _patch_stream(sys.stderr, "stderr"),
        "encoding": _UTF8,
        "errors": _ERRORS,
    }

    if verbose:
        _safe_stderr_write(
            f"🔧 [CONSOLE] patch_console → "
            f"stdout={result['stdout']} stderr={result['stderr']} "
            f"({_UTF8}/{_ERRORS})"
        )
    return result


# ═══════════════════════════════════════════
# اجرای مستقیم — برای تست
# ═══════════════════════════════════════════
if __name__ == "__main__":
    r = patch_console(verbose=True)
    print(f"✅ نتیجه: {r}")
    # تست فارسی و ایموجی
    print("🧪 تست: سلام — کود مایع — یاراترا ✅❌⚠️")
