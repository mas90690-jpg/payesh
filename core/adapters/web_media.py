# core/web_media.py
"""
دانلود عکس/فایل از داخل DOM مرورگر (برای کلکتورهای مرورگری: ایتا، واتساپ).

تصاویر در web.eitaa/web.whatsapp معمولاً blob: یا data: هستند و با دانلود
مستقیم گرفته نمی‌شوند؛ این تابع داخل بافتِ خود صفحه fetch می‌کند و به base64
تبدیل و ذخیره می‌کند.

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [WEBMEDIA:...]:
      * هر مرحله با زمان (query / fetch / decode / write)
      * خطاها با type(e).__name__ + URL context
      * هر skip با دلیل صریح
  - 🐛 FIX: پسوند فایل از magic bytes تشخیص داده می‌شود (jpg/png/gif/webp)
  - 🐛 FIX: base64 decode با validation — اگه invalid باشه، فایل نیمه‌کاره
    پاک می‌شود
  - 🐛 FIX: os.makedirs و os.remove امن‌سازی شدند
  - 🎯 JS مرورگر حالا خطا را در console.error هم لاگ می‌کند (برای دیباگ)
  - 🎯 آستانه‌های هشدار برای کندی (5s fetch، 10s total)
  - 🧹 type hints و from __future__ import annotations
"""
from __future__ import annotations

import base64
import os
import time
from datetime import datetime

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── ثابت‌ها ───
_LOG_PREVIEW_LEN = 60
_SLOW_FETCH_MS = 5000
_SLOW_TOTAL_MS = 10000


# ═══════════════════════════════════════════
# JS درون مرورگر: fetch → base64
# ═══════════════════════════════════════════
_FETCH_B64_JS = """
async (url) => {
    try {
        const resp = await fetch(url);
        if (!resp.ok) {
            console.error('[WEBMEDIA] fetch status ' + resp.status + ' for ' + url);
            return null;
        }
        const blob = await resp.blob();
        return await new Promise((resolve) => {
            const fr = new FileReader();
            fr.onloadend = () => resolve(fr.result.split(',')[1] || null);
            fr.onerror = () => {
                console.error('[WEBMEDIA] FileReader error for ' + url);
                resolve(null);
            };
            fr.readAsDataURL(blob);
        });
    } catch (e) {
        console.error('[WEBMEDIA] fetch error: ' + e.message + ' | url=' + url);
        return null;
    }
}
"""


# ═══════════════════════════════════════════
# MIME detection از magic bytes
# ═══════════════════════════════════════════
def _detect_ext_from_bytes(data: bytes) -> str:
    """تشخیص پسوند فایل از magic bytes.

    Returns:
        ".jpg" | ".png" | ".gif" | ".webp" | ".bin"
    """
    if not data or len(data) < 4:
        return ".bin"

    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"

    # GIF: "GIF87a" or "GIF89a"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"

    # WebP: "RIFF"...."WEBP"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"

    # BMP: "BM"
    if data[:2] == b"BM":
        return ".bmp"

    return ".bin"


# ═══════════════════════════════════════════
# API اصلی
# ═══════════════════════════════════════════
async def download_dom_image(
    page,
    element,
    media_dir: str,
    prefix: str,
) -> tuple[str | None, str | None]:
    """اولین عکس داخل element را دانلود و ذخیره می‌کند.

    Args:
        page:       playwright Page.
        element:    playwright ElementHandle که شامل عکس است.
        media_dir:  پوشه‌ای که فایل‌ها ذخیره می‌شوند.
        prefix:     پیشوند نام فایل (معمولاً platform، مثل "eitaa").

    Returns:
        (media_type, local_path) یا (None, None) اگر عکسی نبود/نشد.
        media_type همیشه "photo" است (فقط برای سازگاری).
    """
    t_all = time.monotonic()

    # ─── ۱) پیدا کردن img ───
    try:
        t_q = time.monotonic()
        img = await element.query_selector("img[src]")
        query_ms = (time.monotonic() - t_q) * 1000
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] query_selector | "
            f"{type(e).__name__}: {e}"
        )
        return None, None

    if not img:
        log_info(
            f"ℹ️ [WEBMEDIA:{prefix}] بدون img | {query_ms:.0f}ms"
        )
        return None, None

    # ─── ۲) گرفتن src ───
    try:
        src = await img.get_attribute("src")
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] get_attribute(src) | "
            f"{type(e).__name__}: {e}"
        )
        return None, None

    if not src:
        log_info(f"ℹ️ [WEBMEDIA:{prefix}] src خالی — رد شد")
        return None, None

    src_preview = src[:_LOG_PREVIEW_LEN]

    # SVG را رد کن (آیکون‌ها)
    if src.startswith("data:image/svg"):
        log_info(
            f"⏭️ [WEBMEDIA:{prefix}] SVG skip | {src_preview}"
        )
        return None, None

    # ─── ۳) fetch با JS (از داخل مرورگر) ───
    try:
        t_f = time.monotonic()
        b64 = await page.evaluate(_FETCH_B64_JS, src)
        fetch_ms = (time.monotonic() - t_f) * 1000
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] evaluate(fetch) | "
            f"{type(e).__name__}: {e} | src={src_preview}"
        )
        return None, None

    if not b64:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] b64 خالی | fetch={fetch_ms:.0f}ms | "
            f"src={src_preview}"
        )
        return None, None

    if fetch_ms > _SLOW_FETCH_MS:
        log_warning(
            f"🐌 [WEBMEDIA:{prefix}] fetch کند | {fetch_ms:.0f}ms | "
            f"src={src_preview}"
        )

    # ─── ۴) decode base64 ───
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] b64decode | "
            f"{type(e).__name__}: {e} | b64_len={len(b64)}"
        )
        return None, None

    if not raw:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] داده خالی بعد از decode | "
            f"b64_len={len(b64)}"
        )
        return None, None

    # ─── ۵) تشخیص پسوند ───
    ext = _detect_ext_from_bytes(raw)

    # ─── ۶) آماده‌سازی پوشه ───
    try:
        os.makedirs(media_dir, exist_ok=True)
    except Exception as e:
        log_error(
            f"❌ [WEBMEDIA:{prefix}] makedirs | "
            f"{type(e).__name__}: {e} | dir={media_dir}"
        )
        return None, None

    # ─── ۷) نوشتن فایل ───
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{prefix}_{stamp}{ext}"
    path = os.path.join(media_dir, filename)

    try:
        t_w = time.monotonic()
        with open(path, "wb") as f:
            f.write(raw)
        write_ms = (time.monotonic() - t_w) * 1000
    except Exception as e:
        log_error(
            f"❌ [WEBMEDIA:{prefix}] write | "
            f"{type(e).__name__}: {e} | path={path}"
        )
        # تلاش برای پاک‌سازی فایل نیمه‌کاره
        _safe_remove(path, prefix)
        return None, None

    # ─── ۸) بررسی اندازه ───
    try:
        size = os.path.getsize(path)
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] getsize | {type(e).__name__}: {e}"
        )
        _safe_remove(path, prefix)
        return None, None

    if size == 0:
        log_warning(f"⚠️ [WEBMEDIA:{prefix}] فایل 0 بایت — حذف شد")
        _safe_remove(path, prefix)
        return None, None

    elapsed_ms = (time.monotonic() - t_all) * 1000
    log_success(
        f"📁 [WEBMEDIA:{prefix}] {filename} | "
        f"{size/1024:.1f}KB | ext={ext} | "
        f"fetch={fetch_ms:.0f}ms write={write_ms:.0f}ms "
        f"total={elapsed_ms:.0f}ms"
    )
    if elapsed_ms > _SLOW_TOTAL_MS:
        log_warning(
            f"🐌 [WEBMEDIA:{prefix}] کند | {elapsed_ms:.0f}ms | "
            f"src={src_preview}"
        )

    return "photo", path


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _safe_remove(path: str, prefix: str) -> None:
    """پاک‌سازی امن فایل (بی‌صدا شکست نخورد)."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{prefix}] remove | "
            f"{type(e).__name__}: {e} | path={path}"
        )
