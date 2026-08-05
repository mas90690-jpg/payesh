# core/web_media.py
"""
دانلود عکس/فایل از داخل DOM مرورگر (برای کلکتورهای مرورگری: ایتا، واتساپ).

تصاویر در web.eitaa/web.whatsapp معمولاً blob: یا data: هستند و با دانلود مستقیم
گرفته نمی‌شوند؛ این تابع داخل بافتِ خود صفحه fetch می‌کند و به base64 تبدیل و ذخیره می‌کند.
"""
import os
import base64
from datetime import datetime
from core.logger import log_success, log_warning

# اسکریپت داخل مرورگر: یک URL (blob/data/http) را می‌گیرد و base64 برمی‌گرداند
_FETCH_B64_JS = """
async (url) => {
    try {
        const resp = await fetch(url);
        const blob = await resp.blob();
        return await new Promise((resolve) => {
            const fr = new FileReader();
            fr.onloadend = () => resolve(fr.result.split(',')[1] || null);
            fr.onerror = () => resolve(null);
            fr.readAsDataURL(blob);
        });
    } catch (e) { return null; }
}
"""


async def download_dom_image(page, element, media_dir: str, prefix: str):
    """
    اولین عکس داخل element را دانلود و ذخیره می‌کند.
    خروجی: (media_type, local_path) یا (None, None) اگر عکسی نبود/نشد.
    """
    try:
        img = await element.query_selector("img[src]")
        if not img:
            return None, None
        src = await img.get_attribute("src")
        if not src or src.startswith("data:image/svg"):  # آیکون‌های svg را رد کن
            return None, None

        b64 = await page.evaluate(_FETCH_B64_JS, src)
        if not b64:
            return None, None

        os.makedirs(media_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        path = os.path.join(media_dir, f"{prefix}_{stamp}.jpg")
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        if os.path.getsize(path) > 0:
            log_success(f"📁 عکس {prefix} ذخیره شد: {os.path.basename(path)}")
            return "photo", path
        os.remove(path)
    except Exception as e:
        log_warning(f"دانلود عکس {prefix} ناموفق: {e}")
    return None, None
