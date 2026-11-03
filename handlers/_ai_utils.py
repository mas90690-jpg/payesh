"""
utility های مشترک AI برای handlers.

همهٔ داده از data/ می‌آید. کد فقط منطق است.

نسخهٔ ۲ (این نسخه):
  - 🎯 `fa_platform` حالا از `synonyms.json` می‌خونه (نه `platforms.json`)
  - 🐛 FIX: `platforms.json` حذف شد (تکراری با synonyms)
"""
from __future__ import annotations

from typing import Any

from data.loader import load_json


# ═══════════════════════════════════════════════════════════
# cache (برای جلوگیری از لود مکرر)
# ═══════════════════════════════════════════════════════════
_name_fa_cache: tuple[float, dict[str, str]] | None = None


def _get_name_fa() -> dict[str, str]:
    """🎯 ساخت name_fa از synonyms.json — با cache mtime.

    Returns:
        dict {platform_key: first_synonym}
        مثال: {"eitaa": "ایتا", "bale": "بله"}
    """
    global _name_fa_cache

    try:
        import os
        from data import paths as dp
        mtime = os.path.getmtime(str(dp.SYNONYMS))
    except Exception:
        mtime = 0.0

    if _name_fa_cache and _name_fa_cache[0] == mtime:
        return _name_fa_cache[1]

    try:
        syn = load_json("synonyms") or {}
        platforms = syn.get("platforms", {})
        if not isinstance(platforms, dict):
            platforms = {}

        result: dict[str, str] = {}
        for k, v in platforms.items():
            key = str(k).lower()
            if isinstance(v, list) and v:
                result[key] = str(v[0])
            else:
                result[key] = str(k)
                
    except Exception:
        result = {}

    _name_fa_cache = (mtime, result)
    return result
    

# ═══════════════════════════════════════════════════════════
# ترجمهٔ اسم پلتفرم → فارسی
# ═══════════════════════════════════════════════════════════
def fa_platform(p: Any) -> str:
    """ترجمهٔ اسم پلتفرم به فارسی.

    Args:
        p: نام پلتفرم (هر نوع — امن است).

    Returns:
        اسم فارسی اگر در synonyms.json بود، وگرنه خود p.
        اگر p خالی/None بود → "نامشخص".
    """
    if p is None:
        return "نامشخص"

    key = str(p).strip()
    if not key:
        return "نامشخص"

    name_fa = _get_name_fa()

    # ─── چک مستقیم ───
    if key in name_fa:
        return name_fa[key]

    # ─── چک case-insensitive ───
    key_low = key.lower()
    if key_low in name_fa:
        return name_fa[key_low]

    # ─── fallback: خودش ───
    return key


# ═══════════════════════════════════════════════════════════
# نام فارسی → کلید استاندارد
# ═══════════════════════════════════════════════════════════
def canonical_platform(name: Any) -> str:
    """نام فارسی یا انگلیسی را به کلید استاندارد تبدیل می‌کند.

    Args:
        name: نام پلتفرم (فارسی، انگلیسی، یا خالی).

    Returns:
        کلید استاندارد (مثل "bale", "eitaa") یا "bale" پیش‌فرض.
    """
    if not name:
        return "bale"
    key = str(name).strip().lower()
    if not key:
        return "bale"

    name_fa = _get_name_fa()
    if key in name_fa:
        return key

    # جستجوی معکوس: فارسی → کلید
    for k, v in name_fa.items():
        if str(v).strip() == str(name).strip():
            return k

    return key
 
# ═══════════════════════════════════════════════════════════
# cache markerهای bad (برای اعتبارسنجی AI)
# ═══════════════════════════════════════════════════════════
_bad_markers_cache: tuple[float, tuple, tuple] | None = None


def _get_bad_markers() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """cache ai_bad_prefixes + ai_bad_markers از messages.json."""
    global _bad_markers_cache

    try:
        import os
        from data import paths as dp
        mtime = os.path.getmtime(str(dp.MESSAGES))
    except Exception:
        mtime = 0.0

    if _bad_markers_cache and _bad_markers_cache[0] == mtime:
        return _bad_markers_cache[1], _bad_markers_cache[2]

    data = load_json("messages")
    prefixes = tuple(data.get("ai_bad_prefixes", []))
    markers = tuple(data.get("ai_bad_markers", []))
    _bad_markers_cache = (mtime, prefixes, markers)
    return prefixes, markers

    
# ═══════════════════════════════════════════════════════════
# اعتبارسنجی پاسخ AI
# ═══════════════════════════════════════════════════════════
def is_valid_ai_answer(ans: Any) -> bool:
    """آیا پاسخ AI معتبر است (نه پیام خطا)؟

    همهٔ markerها از messages.json می‌آید.
    """
    if ans is None:
        return False

    s = str(ans).strip()
    if not s:
        return False

    # ─── بارگذاری از cache ───
    bad_prefixes, bad_markers = _get_bad_markers()

    # ─── چک prefix ───
    for prefix in bad_prefixes:
        if prefix and s.startswith(prefix):
            return False

    # ─── چک marker ───
    for marker in bad_markers:
        if marker and marker in s:
            return False

    return True
    

# ═══════════════════════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════════════════════
__all__ = [
    "fa_platform",
    "canonical_platform",
    "is_valid_ai_answer",
]