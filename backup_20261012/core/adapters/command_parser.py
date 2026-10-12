"""پارسرِ دستورهای ChatOps — خالص، بدون side-effect، کاملاً تست‌پذیر.

نسخهٔ ۵ (این نسخه):
  - 🎯 start_platform اضافه شد.
  - 🎯 search_platform و join_group (از نسخهٔ ۴).
  - 🎯 _detect_platform برای تشخیص پلتفرم‌های فارسی.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۳.
"""
from __future__ import annotations

import re


# ═══════════════════════════════════════════
# Fallback: نگاشت پلتفرم
# ═══════════════════════════════════════════
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

# ─── تشخیص مستقیم پلتفرم (fallback) ───
_DIRECT_PLATFORM_WORDS: dict[str, str] = {
    "بله": "bale",
    "ایتا": "eitaa",
    "روبیکا": "rubika",
    "تلگرام": "telegram",
    "واتساپ": "whatsapp",
    "لینکدین": "linkedin",
    "اینستاگرام": "instagram",
    "یوتیوب": "youtube",
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
    "search_platform": (
        "سرچ", "جستجو", "بگرد", "پیدا کن", "search",
    ),
    "join_group": (
        "عضو", "عضویت", "جوین", "join",
        "وارد شو", "عضو شو",
    ),
    "start_platform": (
        "روشن", "شروع", "استارت", "start",
        "باز کن", "وصل شو",
    ),
}

_COMMANDS_LOADED_FROM_FILE: bool = False


# ═══════════════════════════════════════════
# اعتبارسنجی
# ═══════════════════════════════════════════
def _validate_platforms(data) -> dict[str, str]:
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


def _validate_words(data) -> dict[str, tuple[str, ...]]:
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


# ═══════════════════════════════════════════
# لود از synonyms + commands
# ═══════════════════════════════════════════
def _load_platforms_from_synonyms() -> dict[str, str]:
    """🎯 ساخت platform_map از synonyms.json.

    فرمت synonyms.json:
        {"platforms": {"eitaa": ["ایتا", "eitaa"], "bale": ["بله", "bale"]}}

    فرمت خروجی:
        {"ایتا": "eitaa", "eitaa": "eitaa", "بله": "bale", "bale": "bale"}
    """
    try:
        from data.loader import load_json
        data = load_json("synonyms", default={}, force=True)
        platforms = data.get("platforms", {})
        if not isinstance(platforms, dict):
            return {}

        out: dict[str, str] = {}
        for canon, synonyms in platforms.items():
            if not isinstance(canon, str) or not canon.strip():
                continue
            canon_clean = canon.strip().lower()
            if not isinstance(synonyms, list):
                continue
            for syn in synonyms:
                if isinstance(syn, str) and syn.strip():
                    out[syn.strip()] = canon_clean
            # خود canon رو هم اضافه کن
            out[canon_clean] = canon_clean

        return out
    except Exception:
        return {}


def _load_commands_from_data() -> tuple[
    dict[str, str], dict[str, tuple[str, ...]]
]:
    """🎯 خواندن پلتفرم‌ها از synonyms + کلمات از commands."""
    global _COMMANDS_LOADED_FROM_FILE

    # ─── ۱) platforms از synonyms.json ───
    platforms = _load_platforms_from_synonyms()
    if not platforms:
        platforms = dict(_FALLBACK_PLATFORM_MAP)

    # ─── ۲) words از commands.json ───
    try:
        from data.loader import load_json
        data = load_json("commands", default={}, force=True)

        if not isinstance(data, dict):
            _COMMANDS_LOADED_FROM_FILE = False
            return platforms, dict(_FALLBACK_WORDS)

        words = _validate_words(data.get("words"))

        if not words:
            _COMMANDS_LOADED_FROM_FILE = False
            return platforms, dict(_FALLBACK_WORDS)

        _COMMANDS_LOADED_FROM_FILE = True
        return platforms, words

    except Exception:
        _COMMANDS_LOADED_FROM_FILE = False
        return platforms, dict(_FALLBACK_WORDS)


# ─── لود اولیه ───
_PLATFORM_MAP, _WORDS = _load_commands_from_data()


# ═══════════════════════════════════════════
# کلمات کلیدی
# ═══════════════════════════════════════════
def _get_words(key: str) -> tuple[str, ...]:
    """گرفتن کلمات با fallback."""
    return _WORDS.get(key, _FALLBACK_WORDS.get(key, ()))


_HELP_WORDS = _get_words("help")
_RESTART_WORDS = _get_words("restart")
_REMOVE_WORDS = _get_words("remove")
_ADD_WORDS = _get_words("add")
_HAS_WORDS = _get_words("has")
_LEAD_WORDS = _get_words("lead")
_LEAD_DETAIL_WORDS = _get_words("lead_detail")
_HEALTH_WORDS = _get_words("health")
_WANT_WORDS = _get_words("want")
_CAP_WORDS = _get_words("cap")
_KEYWORD_WORDS = _get_words("keyword")
_REPORT_WORDS = _get_words("report")
_CRM_WORDS = _get_words("crm")
_LOG_WORDS = _get_words("log")
_LOG_EXPLAIN_WORDS = _get_words("log_explain")
_STATUS_WORDS = _get_words("status")
_PLATFORM_STATUS_WORDS = _get_words("platform_status")
_SEARCH_PLATFORM_WORDS = _get_words("search_platform")
_JOIN_GROUP_WORDS = _get_words("join_group")
_START_PLATFORM_WORDS = _get_words("start_platform")


def reload_commands() -> None:
    """🎯 hot-reload کلمات کلیدی."""
    global _PLATFORM_MAP, _WORDS
    global _HELP_WORDS, _RESTART_WORDS, _REMOVE_WORDS
    global _ADD_WORDS, _HAS_WORDS, _LEAD_WORDS
    global _LEAD_DETAIL_WORDS, _HEALTH_WORDS
    global _WANT_WORDS, _CAP_WORDS, _KEYWORD_WORDS
    global _REPORT_WORDS, _CRM_WORDS, _LOG_WORDS
    global _LOG_EXPLAIN_WORDS, _STATUS_WORDS
    global _PLATFORM_STATUS_WORDS
    global _SEARCH_PLATFORM_WORDS, _JOIN_GROUP_WORDS, _START_PLATFORM_WORDS

    try:
        from data.loader import clear_cache
        clear_cache()
    except Exception:
        pass

    _PLATFORM_MAP, _WORDS = _load_commands_from_data()

    _HELP_WORDS = _get_words("help")
    _RESTART_WORDS = _get_words("restart")
    _REMOVE_WORDS = _get_words("remove")
    _ADD_WORDS = _get_words("add")
    _HAS_WORDS = _get_words("has")
    _LEAD_WORDS = _get_words("lead")
    _LEAD_DETAIL_WORDS = _get_words("lead_detail")
    _HEALTH_WORDS = _get_words("health")
    _WANT_WORDS = _get_words("want")
    _CAP_WORDS = _get_words("cap")
    _KEYWORD_WORDS = _get_words("keyword")
    _REPORT_WORDS = _get_words("report")
    _CRM_WORDS = _get_words("crm")
    _LOG_WORDS = _get_words("log")
    _LOG_EXPLAIN_WORDS = _get_words("log_explain")
    _STATUS_WORDS = _get_words("status")
    _PLATFORM_STATUS_WORDS = _get_words("platform_status")
    _SEARCH_PLATFORM_WORDS = _get_words("search_platform")
    _JOIN_GROUP_WORDS = _get_words("join_group")
    _START_PLATFORM_WORDS = _get_words("start_platform")


def get_commands_source() -> str:
    """🎯 منبع کلمات: file یا fallback."""
    return (
        "file" if _COMMANDS_LOADED_FROM_FILE
        else "fallback"
    )


# ═══════════════════════════════════════════
# Regex ها
# ═══════════════════════════════════════════
_VALUE_RE = re.compile(r"«([^»]*)»|\"([^\"]*)\"|'([^']*)'")

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
    word_chars = (
        r"a-zA-Z0-9_\u0621-\u063A"
        r"\u0641-\u064A\u0660-\u0669\u200c"
    )

    pattern = (
        rf"(?<![{word_chars}])"
        rf"{re.escape(w)}"
        rf"(?![{word_chars}])"
    )
    return bool(re.search(pattern, text_low))


def _any_word(text_low: str, words: tuple[str, ...]) -> bool:
    """آیا هر یک از کلمات در متن هست؟"""
    if not words:
        return False
    return any(_has_word(text_low, w) for w in words)


def _detect_platform(text_low: str) -> str | None:
    """🎯 تشخیص مستقیم پلتفرم از متن."""
    for word, canon in _DIRECT_PLATFORM_WORDS.items():
        if _has_word(text_low, word):
            return canon
    return None


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
    val = (m.group(1) or m.group(2) or m.group(3) or "").strip()
    return val if val else None


def platform_in(low: str) -> str | None:
    """نام پلتفرم را در متن پیدا می‌کند."""
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
def parse_command(text: str) -> tuple[str | None, str | None]:
    """متن → (action, value)."""
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

    # ─── 14.5: platform_status با وضعیت صریح ───
    if p and _any_word(low, _STATUS_WORDS):
        return "platform_status", p

    # ═══════════════════════════════════════════
    # 🆕 14.6: search_platform
    # ═══════════════════════════════════════════
    if _any_word(low, _SEARCH_PLATFORM_WORDS):
        # ─── اول پلتفرم رو از متن اصلی تشخیص بده ───
        p_found = platform_in(low) or _detect_platform(low)

        # ─── query: مقدار داخل « » یا کل متن ───
        query = val or text

        # ─── پاک‌سازی: کلمات دستوری (سرچ، بگرد، ...) ───
        for w in _SEARCH_PLATFORM_WORDS:
            query = re.sub(
                rf"(?<!\w){re.escape(w)}(?!\w)",
                "",
                query,
                flags=re.IGNORECASE,
            )

        # ─── پاک‌سازی: پلتفرم ───
        for word, _ in _PLATFORM_MAP.items():
            query = re.sub(
                rf"(?<!\w){re.escape(word)}(?!\w)",
                "",
                query,
                flags=re.IGNORECASE,
            )

        # ─── پاک‌سازی: کلمات ربطی ───
        filler_words = (
            "کن", "بکن", "تو", "در", "از",
            "روی", "برای", "برام", "واسه", "واسه‌ام",
        )
        for fw in filler_words:
            query = re.sub(
                rf"(?<!\w){re.escape(fw)}(?!\w)",
                "",
                query,
                flags=re.IGNORECASE,
            )

        query = query.strip()
        # حذف فاصله‌های اضافی
        query = re.sub(r"\s+", " ", query).strip()

        if query:
            if p_found:
                return "search_platform", f"{query}|{p_found}"
            return "search_platform", query

    # ═══════════════════════════════════════════
    # 🆕 14.7: join_group
    # ═══════════════════════════════════════════
    if _any_word(low, _JOIN_GROUP_WORDS):
        # لینک رو از متن استخراج کن
        link_match = re.search(
            r"https?://[^\s]+", text
        )
        if link_match:
            link = link_match.group(0)
            p_found = platform_in(low)
            if p_found:
                return "join_group", f"{link}|{p_found}"
            return "join_group", link

    # ═══════════════════════════════════════════
    # 🆕 14.8: start_platform
    # ═══════════════════════════════════════════
    if _any_word(low, _START_PLATFORM_WORDS):
        p_found = platform_in(low) or _detect_platform(low)
        if p_found:
            return "start_platform", p_found

    # ─── 15. status ───
    p = platform_in(low)
    if _any_word(low, _STATUS_WORDS) and not p:
        return "status", None

    # ─── ناشناخته ───
    return None, val


# ═══════════════════════════════════════════
# پارسرِ set PARAM=VALUE
# ═══════════════════════════════════════════
def parse_set_param(text: str) -> tuple[str | None, str | None]:
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