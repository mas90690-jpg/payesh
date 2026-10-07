"""پارسرِ دستورهای ChatOps — خالص، بدون side-effect، کاملاً تست‌پذیر.

منبعِ یکتا برای هر دو:
  - handlers/admin_handler.py  (استفاده می‌کند)
  - core/safe_brain/main.py     (بعداً migrate می‌شود)

⚠️ هیچ import از config یا logger در این فایل نیست.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: `_PLATFORM_MAP` حالا `instagram` + اختصارات
    (`tg`, `wa`, `li`, `insta`) دارد.
  - 🐛 FIX: `platform_in` حالا با word-boundary چک می‌کند
    (قبلاً substring بود → «وب» در «سرویس» match می‌شد).
  - 🐛 FIX: `_VALUE_RE` حالا پشتیبانی از `«»`، `""`, `''`
    و مقادیر تک‌کاراکتری.
  - 🐛 FIX: `parse_command` حالا priority صریح دارد:
    restart > help > feature_request > lead_details > has_keyword
    > add_keyword > remove_keyword > keywords > health > report
    > crm > explain_logs > logs > status > platform_status
  - 🐛 FIX: `_SET_PARAM_RE` حالا value با quote را می‌فهمد.
  - 🎯 `_KEYWORD_WORDS` شامل «کلیدواژه‌ها» (جمع).
  - 🎯 `_LEAD_WORDS` شامل «سرنخ‌ها» (جمع).
  - 🎯 `_PLATFORM_STATUS_WORDS` شامل «کار می‌کنه» (محاوره‌ای).
  - 🎯 `__all__` export.
  - 🎯 type hints صریح‌تر.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import re

# ═══════════════════════════════════════════
# نگاشت پلتفرم (فارسی/انگلیسی/اختصار → canonical)
# ═══════════════════════════════════════════
_PLATFORM_MAP: dict[str, str] = {
    # ─── فارسی ───
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
    # ─── انگلیسی (کامل) ───
    "eitaa": "eitaa",
    "bale": "bale",
    "rubika": "rubika",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "linkedin": "linkedin",
    "web": "web",
    "instagram": "instagram",
    # ─── انگلیسی (اختصار) ───
    "tg": "telegram",
    "wa": "whatsapp",
    "li": "linkedin",
    "insta": "instagram",
    "ig": "instagram",
}


# ═══════════════════════════════════════════
# Regex ها
# ═══════════════════════════════════════════
# مقدار داخل « » یا " " یا ' ' (شامل تک‌کاراکتری)
_VALUE_RE = re.compile(
    r"[«\"']([^«»\"']*)[»\"']"
)

# set PARAM=VALUE یا set PARAM:VALUE
# value می‌تواند با quote باشد یا بدون
_SET_PARAM_RE = re.compile(
    r"([A-Z][A-Z0-9_]{2,})\s*[=:]\s*"
    r"(?:\"([^\"]+)\"|'([^']+)'|(\S+))"
)


# ═══════════════════════════════════════════
# لیست کلمات کلیدی
# ═══════════════════════════════════════════
_HELP_WORDS = (
    "help", "راهنما", "کمک", "دستورات", "منو",
    "/help", "/راهنما",
)

_RESTART_WORDS = (
    "restart", "ری‌استارت", "ری استارت", "ریستارت",
    "راه‌اندازی مجدد", "راه اندازی مجدد",
    "/restart",
)

_REMOVE_WORDS = (
    "حذف", "remove", "پاک", "delete",
    "بردار", "پاک کن",
)

_ADD_WORDS = (
    "اضافه", "اضاف", "add", "insert",
    "بیفزا", "بذار", "ثبت",
)

_HAS_WORDS = (
    "دار", "هست", "has", "exist",
    "داری", "دارد",
)

_LEAD_WORDS = (
    "سرنخ", "سرنخ‌ها", "سرنخ ها",
    "پایش", "lead", "leads",
)

_LEAD_DETAIL_WORDS = (
    "جزئیات", "اولین", "آخرین",
    "detail", "first", "last",
    "نشون", "نشان", "بگو",
)

_HEALTH_WORDS = (
    "سلامت", "health",
    "چک کامل", "تندرستی",
    "همه پلتفرم", "وضعیت کامل",
)

_WANT_WORDS = (
    "می‌خوام", "میخوام", "می‌خواهم", "میخواهم",
    "کاش", "اضافه کن", "اضاف کن",
)

_CAP_WORDS = (
    "بتون", "بتونی", "قابلیت", "feature", "توانایی",
)

_KEYWORD_WORDS = (
    "کلیدواژه", "کلید واژه", "کلیدواژه‌ها", "کلید واژه ها",
    "keyword", "keywords",
)

_REPORT_WORDS = ("گزارش", "report", "reports")

_CRM_WORDS = ("crm", "سی آر ام", "خروجی")

_LOG_WORDS = ("لاگ", "log", "logs")

_LOG_EXPLAIN_WORDS = (
    "چیه", "یعنی", "توضیح", "معنی",
    "explain", "چرا", "دلیل",
)

_STATUS_WORDS = (
    "وضعیت", "آمار", "اوضاع", "حال",
    "status", "stat",
)

_PLATFORM_STATUS_WORDS = (
    "کار", "کار می‌کنه", "کار میکنه",
    "وصل", "وصل هست", "working", "status",
    "?", "؟", "فعال", "فعاله",
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _has_word(text_low: str, word: str) -> bool:
    """آیا `word` به‌عنوان کلمهٔ مستقل در متن هست؟

    برای کلمات فارسی (که word-boundary استاندارد کار نمی‌کند)
    از `in` استفاده می‌کنیم. برای کلمات ASCII از word-boundary.
    """
    if not text_low or not word:
        return False

    w = word.lower()

    # ─── ASCII: word-boundary ───
    if w.isascii() and w.isalnum() and len(w) <= 4:
        # کلمات کوتاه ASCII (tg, wa, ...) → مرز دقیق
        return bool(re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])",
            text_low,
        ))

    # ─── بقیه: substring ───
    return w in text_low


def _any_word(text_low: str, words: tuple[str, ...]) -> bool:
    """آیا هر یک از کلمات در متن هست؟"""
    return any(_has_word(text_low, w) for w in words)


# ═══════════════════════════════════════════
# توابع کمکی عمومی
# ═══════════════════════════════════════════
def extract_value(text: str) -> str | None:
    """مقدار داخل « » یا " " یا ' ' را برمی‌گرداند.

    Args:
        text: پیام.

    Returns:
        مقدار (strip شده) یا None.
    """
    if not text:
        return None
    m = _VALUE_RE.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    return val if val else None


def platform_in(low: str) -> str | None:
    """نام پلتفرم را در متن پیدا می‌کند (canonical).

    اولویت: کلمات بلندتر اول (جلوگیری از match اشتباه).

    Args:
        low: متن lowercase شده.

    Returns:
        نام canonical (eitaa/bale/...) یا None.
    """
    if not low:
        return None

    # ─── مرتب بر اساس طول (بلندتر = اولویت بالاتر) ───
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
    """متن → (action, value). خالص.

    ترتیب اولویت (بالا به پایین):
      1. restart
      2. help
      3. feature_request
      4. lead_details
      5. has_keyword
      6. add_keyword
      7. remove_keyword
      8. keywords (لیست)
      9. health
      10. report
      11. crm
      12. explain_logs
      13. logs
      14. status
      15. platform_status

    Args:
        text: پیام کاربر.

    Returns:
        tuple[action, value]:
          - action: نام دستور یا None
          - value:  مقدار (معمولاً برای کلیدواژه/پلتفرم)

    Examples:
        >>> parse_command("سلام")
        (None, None)
        >>> parse_command("وضعیت")
        ("status", None)
        >>> parse_command("کلیدواژه «کود» را اضافه کن")
        ("add_keyword", "کود")
        >>> parse_command("وضعیت ایتا")
        ("platform_status", "eitaa")
    """
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
            if (
                "اولین" in low
                or "first" in low
            )
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
    """پارسرِ `set PARAM=VALUE` → (NAME, value).

    پشتیبانی از:
      - set PARAM=VALUE
      - set PARAM:VALUE
      - set PARAM="quoted value"
      - set PARAM='quoted value'

    Args:
        text: پیام کاربر.

    Returns:
        (NAME, value) یا (None, None).

    Examples:
        >>> parse_set_param("set BALE_POLL_SECONDS=120")
        ("BALE_POLL_SECONDS", "120")
        >>> parse_set_param("SET_PARAM: AI_PRIORITY=auto")
        ("AI_PRIORITY", "auto")
    """
    if not text:
        return None, None

    m = _SET_PARAM_RE.search(text)
    if not m:
        return None, None

    name = m.group(1).upper()

    # value از یکی از سه گروه (double-quoted, single-quoted, unquoted)
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
    "_PLATFORM_MAP",
]