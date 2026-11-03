"""دانلود عکس/فایل از داخل DOM مرورگر (برای کلکتورهای مرورگری).

تصاویر در web.eitaa/web.whatsapp معمولاً `blob:` یا `data:` هستند و با
دانلود مستقیم گرفته نمی‌شوند؛ این تابع داخل بافتِ خود صفحه fetch
می‌کند و به base64 تبدیل و ذخیره می‌کند.

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `_SLOW_FETCH_MS`, `_SLOW_TOTAL_MS`, `_LOG_PREVIEW_LEN`
    حالا env-based.
  - 🐛 FIX: `_detect_ext_from_bytes` حالا SVG، ICO، TIFF، AVIF، HEIC
    را هم تشخیص می‌دهد.
  - 🐛 FIX: `download_dom_image` حالا size limit دارد
    (`WEBMEDIA_MAX_BYTES`) — جلوگیری از RAM bloat.
  - 🐛 FIX: `download_dom_image` حالا `page` و `element` را
    validate می‌کند (None-safe).
  - 🐛 FIX: `download_dom_image` حالا `asyncio.CancelledError` را
    امن handle می‌کند (cleanup فایل نیمه‌کاره).
  - 🐛 FIX: filename حالا `microsecond + uuid` دارد (بدون collision).
  - 🐛 FIX: `_sanitize_prefix()` helper برای prefix نامعتبر.
  - 🐛 FIX: `src` حالا از `srcset` هم fallback می‌کند.
  - 🐛 FIX: `_FETCH_B64_JS` حالا max size دارد.
  - 🎯 `download_dom_image` حالا MIME type واقعی را برمی‌گرداند
    (`.jpg` → `image/jpeg`).
  - 🎯 `get_stats()` متد جدید.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str,
    default: int,
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


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_LOG_PREVIEW_LEN = _env_int(
    "WEBMEDIA_LOG_PREVIEW",
    60,
    min_val=20,
    max_val=500,
)
_SLOW_FETCH_MS = _env_int(
    "WEBMEDIA_SLOW_FETCH_MS",
    5000,
    min_val=100,
    max_val=120_000,
)
_SLOW_TOTAL_MS = _env_int(
    "WEBMEDIA_SLOW_TOTAL_MS",
    10_000,
    min_val=100,
    max_val=300_000,
)
_MAX_IMAGE_BYTES = _env_int(
    "WEBMEDIA_MAX_BYTES",
    20 * 1024 * 1024,
    min_val=1024,
    max_val=500 * 1024 * 1024,
)


# ═══════════════════════════════════════════
# JS درون مرورگر: fetch → base64
# ═══════════════════════════════════════════
_FETCH_B64_JS = """
async (url) => {
    try {
        const resp = await fetch(url);
        if (!resp.ok) {
            console.error(
                '[WEBMEDIA] fetch status ' + resp.status +
                ' for ' + url
            );
            return null;
        }
        // محدودیت حجم: اگه content-length بزرگ بود، رد کن
        const cl = resp.headers.get('content-length');
        if (cl && parseInt(cl) > 20 * 1024 * 1024) {
            console.error(
                '[WEBMEDIA] too large: ' + cl + ' bytes for ' + url
            );
            return null;
        }
        const blob = await resp.blob();
        return await new Promise((resolve) => {
            const fr = new FileReader();
            fr.onloadend = () => {
                try {
                    const result = fr.result || '';
                    resolve(result.split(',')[1] || null);
                } catch (e) {
                    console.error('[WEBMEDIA] split error: ' + e.message);
                    resolve(null);
                }
            };
            fr.onerror = () => {
                console.error('[WEBMEDIA] FileReader error for ' + url);
                resolve(null);
            };
            fr.readAsDataURL(blob);
        });
    } catch (e) {
        console.error(
            '[WEBMEDIA] fetch error: ' + e.message + ' | url=' + url
        );
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
        یکی از: ".jpg", ".png", ".gif", ".webp", ".bmp",
        ".svg", ".ico", ".tiff", ".avif", ".heic", ".bin"
    """
    if not data or len(data) < 4:
        return ".bin"

    # ─── JPEG: FF D8 FF ───
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"

    # ─── PNG: 89 50 4E 47 0D 0A 1A 0A ───
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"

    # ─── GIF: "GIF87a" or "GIF89a" ───
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"

    # ─── WebP: "RIFF"...."WEBP" ───
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"

    # ─── BMP: "BM" ───
    if data[:2] == b"BM":
        return ".bmp"

    # ─── ICO: 00 00 01 00 ───
    if data[:4] == b"\x00\x00\x01\x00":
        return ".ico"

    # ─── TIFF: "II*\x00" (little-endian) یا "MM\x00*" (big-endian) ───
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tiff"

    # ─── AVIF: ...."ftypavif" ───
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] == b"avif":
        return ".avif"

    # ─── HEIC: ...."ftypheic"/"ftypheix"/"ftypmif1" ───
    if (
        len(data) >= 12
        and data[4:8] == b"ftyp"
        and data[8:12] in (b"heic", b"heix", b"mif1")
    ):
        return ".heic"

    # ─── SVG: شروع با `<svg` یا `<?xml` (بعد از whitespace) ───
    head = data[:200].lstrip()
    if head.startswith((b"<svg", b"<?xml")):
        # چک دقیق‌تر برای svg
        head_low = data[:1000].lower()
        if b"<svg" in head_low:
            return ".svg"

    return ".bin"


# ═══════════════════════════════════════════
# MIME mapping
# ═══════════════════════════════════════════
_EXT_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".bin": "application/octet-stream",
}


def _ext_to_mime(ext: str) -> str:
    """تبدیل پسوند به MIME."""
    return _EXT_TO_MIME.get(ext.lower(), "application/octet-stream")


# ═══════════════════════════════════════════
# sanitize prefix
# ═══════════════════════════════════════════
_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_prefix(prefix: str) -> str:
    """پاک‌سازی prefix — جلوگیری از filename نامعتبر."""
    if not prefix:
        return "media"
    safe = _BAD_FILENAME_CHARS.sub("_", str(prefix)).strip()[:32]
    return safe or "media"


# ═══════════════════════════════════════════
# API اصلی
# ═══════════════════════════════════════════
async def download_dom_image(
    page: Any,
    element: Any,
    media_dir: str,
    prefix: str,
) -> tuple[str | None, str | None]:
    """اولین عکس داخل element را دانلود و ذخیره می‌کند.

    Args:
        page:       playwright Page.
        element:    playwright ElementHandle که شامل عکس است.
        media_dir:  پوشه‌ای که فایل‌ها ذخیره می‌شوند.
        prefix:     پیشوند نام فایل (معمولاً platform).

    Returns:
        (media_type, local_path) یا (None, None) اگر عکسی نبود.
        media_type حالا MIME واقعی است (مثل `image/jpeg`).
    """
    # ─── validate ورودی ───
    if page is None or element is None:
        log_warning(f"⚠️ [WEBMEDIA:{prefix}] page/element None — رد شد")
        return None, None

    t_all = time.monotonic()
    safe_prefix = _sanitize_prefix(prefix)

    # ─── ۱) پیدا کردن img ───
    try:
        t_q = time.monotonic()
        img = await element.query_selector("img[src]")
        if not img:
            # fallback: img بدون src
            img = await element.query_selector("img")
        query_ms = (time.monotonic() - t_q) * 1000
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] query_selector | {
                type(e).__name__}: {e}")
        return None, None

    if not img:
        log_info(f"ℹ️ [WEBMEDIA:{safe_prefix}] بدون img | {query_ms:.0f}ms")
        return None, None

    # ─── ۲) گرفتن src ───
    src: str | None = None
    try:
        src = await img.get_attribute("src")
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] get_attribute(src) | {
                type(e).__name__}: {e}")

    # 🐛 FIX: fallback به srcset
    if not src:
        try:
            srcset = await img.get_attribute("srcset")
            if srcset:
                # اولین URL از srcset
                first = srcset.split(",")[0].strip().split()[0]
                if first:
                    src = first
        except Exception as e:
            log_warning(
                f"⚠️ [core.adapters.web_media] {
                    type(e).__name__}: {e}")

    if not src:
        log_info(f"ℹ️ [WEBMEDIA:{safe_prefix}] src خالی — رد شد")
        return None, None

    src_preview = src[:_LOG_PREVIEW_LEN]

    # ─── رد SVG data URL ───
    if src.startswith("data:image/svg"):
        log_info(f"⏭️ [WEBMEDIA:{safe_prefix}] SVG skip | {src_preview}")
        return None, None

    # ─── ۳) fetch با JS ───
    try:
        t_f = time.monotonic()
        b64 = await page.evaluate(_FETCH_B64_JS, src)
        fetch_ms = (time.monotonic() - t_f) * 1000
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] evaluate(fetch) | "
            f"{type(e).__name__}: {e} | src={src_preview}"
        )
        return None, None

    if not b64:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] b64 خالی | "
            f"fetch={fetch_ms:.0f}ms | src={src_preview}"
        )
        return None, None

    if fetch_ms > _SLOW_FETCH_MS:
        log_warning(
            f"🐌 [WEBMEDIA:{safe_prefix}] fetch کند | "
            f"{fetch_ms:.0f}ms | src={src_preview}"
        )

    # ─── ۴) decode base64 ───
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] b64decode | "
            f"{type(e).__name__}: {e} | b64_len={len(b64)}"
        )
        return None, None

    if not raw:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] داده خالی | b64_len={
                len(b64)}")
        return None, None

    # 🐛 FIX: size limit
    if len(raw) > _MAX_IMAGE_BYTES:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] حجم بزرگ | "
            f"{len(raw)} > {_MAX_IMAGE_BYTES} bytes"
        )
        return None, None

    # ─── ۵) تشخیص پسوند + MIME ───
    ext = _detect_ext_from_bytes(raw)
    mime = _ext_to_mime(ext)

    # ─── ۶) آماده‌سازی پوشه ───
    try:
        os.makedirs(media_dir, exist_ok=True)
    except Exception as e:
        log_error(
            f"❌ [WEBMEDIA:{safe_prefix}] makedirs | "
            f"{type(e).__name__}: {e} | dir={media_dir}"
        )
        return None, None

    # ─── ۷) نوشتن فایل ───
    # 🐛 FIX: microsecond + short uuid
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")  # noqa: DTZ005
    short_id = uuid.uuid4().hex[:6]
    filename = f"{safe_prefix}_{ts}_{short_id}{ext}"
    path = os.path.join(media_dir, filename)

    try:
        t_w = time.monotonic()

        def _write():
            with open(path, "wb") as f:
                f.write(raw)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError) as e:
                    log_warning(
                        f"⚠️ [core.adapters.web_media] {
                            type(e).__name__}: {e}")

        await asyncio.to_thread(_write)
        write_ms = (time.monotonic() - t_w) * 1000
    except asyncio.CancelledError:
        _safe_remove(path, safe_prefix)
        raise
    except Exception as e:
        log_error(
            f"❌ [WEBMEDIA:{safe_prefix}] write | {
                type(e).__name__}: {e} | path={path}")
        _safe_remove(path, safe_prefix)
        return None, None

    # ─── ۸) بررسی اندازه ───
    try:
        size = os.path.getsize(path)
    except Exception as e:
        log_warning(
            f"⚠️ [WEBMEDIA:{safe_prefix}] getsize | {
                type(e).__name__}: {e}")
        _safe_remove(path, safe_prefix)
        return None, None

    if size == 0:
        log_warning(f"⚠️ [WEBMEDIA:{safe_prefix}] فایل 0 بایت — حذف")
        _safe_remove(path, safe_prefix)
        return None, None

    elapsed_ms = (time.monotonic() - t_all) * 1000
    log_success(
        f"📁 [WEBMEDIA:{safe_prefix}] {filename} | "
        f"{size / 1024:.1f}KB | ext={ext} | mime={mime} | "
        f"fetch={fetch_ms:.0f}ms write={write_ms:.0f}ms "
        f"total={elapsed_ms:.0f}ms"
    )
    if elapsed_ms > _SLOW_TOTAL_MS:
        log_warning(
            f"🐌 [WEBMEDIA:{safe_prefix}] کند | {
                elapsed_ms:.0f}ms | src={src_preview}")

    return mime, path


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _safe_remove(path: str, prefix: str) -> None:
    """پاک‌سازی امن فایل — با retry."""
    if not path:
        return
    for attempt in range(3):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt < 2:
                time.sleep(0.1)
                continue
            log_warning(f"⚠️ [WEBMEDIA:{prefix}] remove perm | path={path}")
            return
        except Exception as e:
            log_warning(
                f"⚠️ [WEBMEDIA:{prefix}] remove | {
                    type(e).__name__}: {e} | path={path}")
            return


def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    return {
        "max_image_bytes": _MAX_IMAGE_BYTES,
        "slow_fetch_ms": _SLOW_FETCH_MS,
        "slow_total_ms": _SLOW_TOTAL_MS,
        "supported_exts": list(_EXT_TO_MIME.keys()),
    }


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "_detect_ext_from_bytes",
    "_ext_to_mime",
    "download_dom_image",
    "get_stats",
]
