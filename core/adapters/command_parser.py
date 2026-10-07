"""پارسرِ دستورهای ChatOps — خالص، بدون side-effect، کاملاً تست‌پذیر.

منبعِ یکتا برای هر دو:
  - handlers/admin_handler.py  (استفاده می‌کند)
  - core/safe_brain/main.py     (بعداً migrate می‌شود)

⚠️ هیچ import از config یا logger در این فایل نیست.

نسخهٔ ۳ (این نسخه):
  - 🎯 کلمات کلیدی و نگاشت پلتفرم‌ها از
    `data/configs/commands.json` می‌آیند (تمرکز داده‌ها).
  - 🎯 `_load_commands_from_data()` — با fallback امن.
  - 🎯 `reload_commands()` — برای hot-reload.
  - 🎯 `get_commands_source()` — file یا fallback.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.

نسخهٔ ۲ (قبلی):
  - 🐛 FIX: `_PLATFORM_MAP` حالا `instagram` + اختصارات دارد.
  - 🐛 FIX: `platform_in` حالا با word-boundary چک می‌کند.
  - 🐛 FIX: `_VALUE_RE` پشتیبانی از «» و "" و '' و تک‌کاراکتری.
  - 🐛 FIX: `parse_command` priority صریح دارد.
  - 🐛 FIX: `_SET_PARAM_RE` value با quote را می‌فهمد.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import re


# ═══════════════════════════════════════════
# 🆕 کلمات کلیدی + پلتفرم‌ها (از data)
# ═══════════════════════════════════════════

# ─── Fallback: نگاشت پلتفرم ───
_FALLBACK_PLATFORM_MAP: dict[str, str] = {
    "ایتا": "eitaa",
    "ایتاوب": "eitaa",
    "بله": "bale",
    "بله‌وب": "bale",
    "روبیکا": "rubika",
    "تلگرام": "telegram",
    "واتساپ": "whatsapp",
    "واتس‌اپ": "whatsapp",
    "واتس اپ": "whatsapp",
    "لینکدین": "linkedin",
    "وب": "web",
    "سایت": "web",
    "اینستاگرام": "instagram",
    "اینستا": "instagram",
    "اینستگرام": "instagram",
    "eitaa": "eitaa",
    "bale": "bale",
    "rubika": "rubika",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "linkedin": "linkedin",
    "web": "web",
    "instagram": "instagram",
    "tg": "telegram",
    "wa": "whatsapp",
    "li": "linkedin",
    "insta": "instagram",
    "ig": "instagram",
}

# ─── Fallback: کلمات کلیدی ───
_FALLBACK_WORDS: dict[str, tuple[str, ...]] = {
    "help": (
        "help", "راهنما", "کمک", "دستورات", "منو",
        "/help", "/راهنما",
    ),
    "restart": (
        "restart", "ری‌استارت", "ری استارت", "ریستارت",
        "راه‌اندازی مجدد", "راه اندازی مجدد", "/restart",
    ),
    "remove": (
        "حذف", "remove", "پاک", "delete",
        "بردار", "پاک کن",
    ),
    "add": (
        "اضافه", "اضاف", "add", "insert",
        "بیفزا", "بذار", "ثبت",
    ),
    "has": (
        "دار", "هست", "has", "exist",
        "داری", "دارد",
    ),
    "lead": (
        "سرنخ", "سرنخ‌ها", "سرنخ ها",
        "پایش", "lead", "leads",
    ),
    "lead_detail": (
        "جزئیات", "اولین", "آخرین",
        "detail", "first", "last",
        "نشون", "نشان", "بگو",
    ),
    "health": (
        "سلامت", "health",
        "چک کامل", "تندرستی",
        "همه پلتفرم", "وضعیت کامل",
    ),
    "want": (
        "می‌خوام", "میخوام", "می‌خواهم", "میخواهم",
        "کاش", "اضافه کن", "اضاف کن",
    ),
    "cap": (
        "بتون", "بتونی", "قابلیت", "feature", "توانایی",
    ),
    "keyword": (
        "کلیدواژه", "کلید واژه", "کلیدواژه‌ها",
        "کلید واژه ها", "keyword", "keywords",
    ),
    "report": ("گزارش", "report", "reports"),
    "crm": ("crm", "سی آر ام", "خروجی"),
    "log": ("لاگ", "log", "logs"),
    "log_explain": (
        "چیه", "یعنی", "توضیح", "معنی",
        "explain", "چرا", "دلیل",
    ),
    "status": (
        "وضعیت", "آمار", "اوضاع", "حال",
        "status", "stat",
    ),
    "platform_status": (
        "کار", "کار می‌کنه", "کار میکنه",
        "وصل", "وصل هست", "working", "status",
        "?", "؟", "فعال", "فعاله",
    ),
}

_COMMANDS_LOADED_FROM_FILE: bool = False


def _validate_platforms(data: any) -> dict[str, str]:
    """اعتبارسنجی + پاک‌سازی platform map."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if (
            isinstance(k, str) and k.strip()
            and isinstance(v, str) and v.strip()
        ):
            out[k.strip()] = v.strip().lower()
    return out


def _validate_words(data: any) -> dict[str, tuple[str, ...]]:
    """اعتبارسنجی + پاک‌سازی words."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for k, v in data.items():
        if (
            isinstance(k, str) and k.strip()
            and isinstance(v, list) and v
        ):
            items = tuple(
                str(x).strip() for x in v
                if isinstance(x, str) and x.strip()
            )
            if items:
                out[k.strip()] = items
    return out


def _load_commands_from_data() -> tuple[
    dict[str, str], dict[str, tuple[str, ...]]
]:
    """🎯 خواندن پلتفرم‌ها + کلمات کلیدی از data — با fallback امن.

    Returns:
        (platform_map, words_dict)
    """
    global _COMMANDS_LOADED_FROM_FILE

    try:
        from data.loader import load_json
        data = load_json("commands", default={})

        if not isinstance(data, dict):
            _COMMANDS_LOADED_FROM_FILE = False
            return dict(_FALLBACK_PLATFORM_MAP), dict(_FALLBACK_WORDS)

        platforms = _validate_platforms(data.get("platforms"))
        words = _validate_words(data.get("words"))

        if not platforms or not words:
            _COMMANDS_LOADED_FROM_FILE = False
            return dict(_FALLBACK_PLATFORM_MAP), dict(_FALLBACK_WORDS)

        _COMMANDS_LOADED_FROM_FILE = True
        return platforms, words

    except Exception:
        _COMMANDS_LOADED_FROM_FILE = False
        return dict(_FALLBACK_PLATFORM_MAP), dict(_FALLBACK_WORDS)


# ─── لود اولیه ───
_PLATFORM_MAP, _WORDS = _load_commands_from_data()


# ─── کلمات کلیدی (پس از لود) ───
_HELP_WORDS: tuple[str, ...] = _WORDS.get(
    "help", _FALLBACK_WORDS["help"]
)
_RESTART_WORDS: tuple[str, ...] = _WORDS.get(
    "restart", _FALLBACK_WORDS["restart"]
)
_REMOVE_WORDS: tuple[str, ...] = _WORDS.get(
    "remove", _FALLBACK_WORDS["remove"]
)
_ADD_WORDS: tuple[str, ...] = _WORDS.get(
    "add", _FALLBACK_WORDS["add"]
)
_HAS_WORDS: tuple[str, ...] = _WORDS.get(
    "has", _FALLBACK_WORDS["has"]
)
_LEAD_WORDS: tuple[str, ...] = _WORDS.get(
    "lead", _FALLBACK_WORDS["lead"]
)
_LEAD_DETAIL_WORDS: tuple[str, ...] = _WORDS.get(
    "lead_detail", _FALLBACK_WORDS["lead_detail"]
)
_HEALTH_WORDS: tuple[str, ...] = _WORDS.get(
    "health", _FALLBACK_WORDS["health"]
)
_WANT_WORDS: tuple[str, ...] = _WORDS.get(
    "want", _FALLBACK_WORDS["want"]
)
_CAP_WORDS: tuple[str, ...] = _WORDS.get(
    "cap", _FALLBACK_WORDS["cap"]
)
_KEYWORD_WORDS: tuple[str, ...] = _WORDS.get(
    "keyword", _FALLBACK_WORDS["keyword"]
)
_REPORT_WORDS: tuple[str, ...] = _WORDS.get(
    "report", _FALLBACK_WORDS["report"]
)
_CRM_WORDS: tuple[str, ...] = _WORDS.get(
    "crm", _FALLBACK_WORDS["crm"]
)
_LOG_WORDS: tuple[str, ...] = _WORDS.get(
    "log", _FALLBACK_WORDS["log"]
)
_LOG_EXPLAIN_WORDS: tuple[str, ...] = _WORDS.get(
    "log_explain", _FALLBACK_WORDS["log_explain"]
)
_STATUS_WORDS: tuple[str, ...] = _WORDS.get(
    "status", _FALLBACK_WORDS["status"]
)
_PLATFORM_STATUS_WORDS: tuple[str, ...] = _WORDS.get(
    "platform_status", _FALLBACK_WORDS["platform_status"]
)


def reload_commands() -> None:
    """🎯 hot-reload کلمات کلیدی + پلتفرم‌ها."""
    global _PLATFORM_MAP, _WORDS
    global _HELP_WORDS, _RESTART_WORDS, _REMOVE_WORDS
    global _ADD_WORDS, _HAS_WORDS, _LEAD_WORDS
    global _LEAD_DETAIL_WORDS, _HEALTH_WORDS
    global _WANT_WORDS, _CAP_WORDS, _KEYWORD_WORDS
    global _REPORT_WORDS, _CRM_WORDS, _LOG_WORDS
    global _LOG_EXPLAIN_WORDS, _STATUS_WORDS
    global _PLATFORM_STATUS_WORDS

    try:
        from data.loader import clear_cache
        clear_cache()
    except Exception:
        pass

    _PLATFORM_MAP, _WORDS = _load_commands_from_data()

    _HELP_WORDS = _WORDS.get("help", _FALLBACK_WORDS["help"])
    _RESTART_WORDS = _WORDS.get(
        "restart", _FALLBACK_WORDS["restart"]
    )
    _REMOVE_WORDS = _WORDS.get(
        "remove", _FALLBACK_WORDS["remove"]
    )
    _ADD_WORDS = _WORDS.get("add", _FALLBACK_WORDS["add"])
    _HAS_WORDS = _WORDS.get("has", _FALLBACK_WORDS["has"])
    _LEAD_WORDS = _WORDS.get("lead", _FALLBACK_WORDS["lead"])
    _LEAD_DETAIL_WORDS = _WORDS.get(
        "lead_detail", _FALLBACK_WORDS["lead_detail"]
    )
    _HEALTH_WORDS = _WORDS.get(
        "health", _FALLBACK_WORDS["health"]
    )
    _WANT_WORDS = _WORDS.get("want", _FALLBACK_WORDS["want"])
    _CAP_WORDS = _WORDS.get("cap", _FALLBACK_WORDS["cap"])
    _KEYWORD_WORDS = _WORDS.get(
        "keyword", _FALLBACK_WORDS["keyword"]
    )
    _REPORT_WORDS = _WORDS.get(
        "report", _FALLBACK_WORDS["report"]
    )
    _CRM_WORDS = _WORDS.get("crm", _FALLBACK_WORDS["crm"])
    _LOG_WORDS = _WORDS.get("log", _FALLBACK_WORDS["log"])
    _LOG_EXPLAIN_WORDS = _WORDS.get(
        "log_explain", _FALLBACK_WORDS["log_explain"]
    )
    _STATUS_WORDS = _WORDS.get(
        "status", _FALLBACK_WORDS["status"]
    )
    _PLATFORM_STATUS_WORDS = _WORDS.get(
        "platform_status", _FALLBACK_WORDS["platform_status"]
    )


def get_commands_source() -> str:
    """🎯 منبع کلمات: file یا fallback."""
    return (
        "file" if _COMMANDS_LOADED_FROM_FILE
        else "fallback"
    )


# ═══════════════════════════════════════════
# Regex ها
# ═══════════════════════════════════════════
_VALUE_RE = re.compile(
    r"[«\"']([^«»\"']*)[»\"']"
)

_SET_PARAM_RE = re.compile(
    r"([A-Z][A-Z0-9_]{2,})\s*[=:]\s*"
    r"(?:\"([^\"]+)\"|'([^']+)'|(\S+))"
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _has_word(text_low: str, word: str) -> bool:
    """آیا `word` به‌عنوان کلمهٔ مستقل در متن هست؟"""
    if not text_low or not word:
        return False

    w = word.lower()

    # ─── ASCII کوتاه: word-boundary ───
    if w.isascii() and w.isalnum() and len(w) <= 4:
        return bool(re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])",
            text_low,
        ))

    # ─── بقیه: substring ───
    return w in text_low


def _any_word(
    text_low: str, words: tuple[str, ...]
) -> bool:
    """آیا هر یک از کلمات در متن هست؟"""
    if not words:
        return False
    return any(_has_word(text_low, w) for w in words)


# ═══════════════════════════════════════════
# توابع کمکی عمومی
# ═══════════════════════════════════════════
def extract_value(text: str) -> str | None:
    """مقدار داخل « » یا " " یا ' ' را برمی‌گرداند."""
    if not text:
        return None
    m = _VALUE_RE.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    return val if val else None


def platform_in(low: str) -> str | None:
    """نام پلتفرم را در متن پیدا می‌کند (canonical)."""
    if not low or not _PLATFORM_MAP:
        return None

    sorted_words = sorted(
        _PLATFORM_MAP.items(),
        key=lambda x: -len(x[0]),
    )

    for word, canon in sorted_words:
        if _has_word(low, word):
            return canon
    return None


# ═══════════════════════════════════════════
# پارسرِ اصلی دستور
# ═══════════════════════════════════════════
def parse_command(
    text: str,
) -> tuple[str | None, str | None]:
    """متن → (action, value). خالص."""
    if not text:
        return None, None

    low = text.lower()
    val = extract_value(text)

    # ─── 1. restart ───
    if _any_word(low, _RESTART_WORDS):
        return "restart", None

    # ─── 2. help ───
    if _any_word(low, _HELP_WORDS):
        return "help", None

    # ─── 3. feature_request ───
    if (
        _any_word(low, _WANT_WORDS)
        and _any_word(low, _CAP_WORDS)
        and not _any_word(low, _KEYWORD_WORDS)
    ):
        return "feature_request", None

    # ─── 4. lead_details ───
    if (
        _any_word(low, _LEAD_WORDS)
        and _any_word(low, _LEAD_DETAIL_WORDS)
    ):
        which = (
            "first"
            if ("اولین" in low or "first" in low)
            else "last"
        )
        return "lead_details", which

    # ─── 5. has_keyword ───
    if (
        _any_word(low, _HAS_WORDS)
        and _any_word(low, _KEYWORD_WORDS)
    ):
        return "has_keyword", val

    # ─── 6. add_keyword ───
    if (
        _any_word(low, _ADD_WORDS)
        and _any_word(low, _KEYWORD_WORDS)
    ):
        return "add_keyword", val

    # ─── 7. remove_keyword ───
    if (
        _any_word(low, _REMOVE_WORDS)
        and _any_word(low, _KEYWORD_WORDS)
    ):
        return "remove_keyword", val

    # ─── 8. keywords (لیست) ───
    if _any_word(low, _KEYWORD_WORDS):
        return "keywords", None

    # ─── 9. health ───
    if _any_word(low, _HEALTH_WORDS):
        return "health", None

    # ─── 10. report ───
    if _any_word(low, _REPORT_WORDS):
        return "report", None

    # ─── 11. crm ───
    if _any_word(low, _CRM_WORDS):
        return "crm", None

    # ─── 12. explain_logs ───
    if (
        _any_word(low, _LOG_WORDS)
        and _any_word(low, _LOG_EXPLAIN_WORDS)
    ):
        return "explain_logs", None

    # ─── 13. logs ───
    if _any_word(low, _LOG_WORDS):
        return "logs", None

    # ─── 14. platform_status ───
    p = platform_in(low)
    if p and _any_word(low, _PLATFORM_STATUS_WORDS):
        return "platform_status", p

    # ─── 15. status ───
    if _any_word(low, _STATUS_WORDS) and not p:
        return "status", None

    # ─── ناشناخته ───
    return None, val


# ═══════════════════════════════════════════
# پارسرِ set PARAM=VALUE
# ═══════════════════════════════════════════
def parse_set_param(
    text: str,
) -> tuple[str | None, str | None]:
    """پارسرِ `set PARAM=VALUE` → (NAME, value)."""
    if not text:
        return None, None

    m = _SET_PARAM_RE.search(text)
    if not m:
        return None, None

    name = m.group(1).upper()
    value = (
        m.group(2)
        or m.group(3)
        or m.group(4)
        or ""
    )
    return name, value


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "extract_value",
    "platform_in",
    "parse_command",
    "parse_set_param",
    "reload_commands",
    "get_commands_source",
    "_PLATFORM_MAP",
]