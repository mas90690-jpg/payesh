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
_name_fa_cache: dict[str, str] | None = None


def _get_name_fa() -> dict[str, str]:
    """🎯 ساخت name_fa از synonyms.json — با cache.

    Returns:
        dict {platform_key: first_synonym}
        مثال: {"eitaa": "ایتا", "bale": "بله"}
    """
    global _name_fa_cache
    if _name_fa_cache is not None:
        return _name_fa_cache

    try:
        syn = load_json("synonyms") or {}
        platforms = syn.get("platforms", {})
        if not isinstance(platforms, dict):
            platforms = {}

        _name_fa_cache = {
            str(k).lower(): (
                str(v[0]) if isinstance(v, list) and v else str(k)
            )
            for k, v in platforms.items()
        }
    except Exception:
        _name_fa_cache = {}

    return _name_fa_cache


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

    # ─── بارگذاری از data ───
    data = load_json("messages")
    bad_prefixes = tuple(data.get("ai_bad_prefixes", []))
    bad_markers = tuple(data.get("ai_bad_markers", []))

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
    "is_valid_ai_answer",
]