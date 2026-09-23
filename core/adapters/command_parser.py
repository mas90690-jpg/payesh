"""
پارسرِ دستورهای ChatOps — خالص، بدون side-effect، کاملاً تست‌پذیر.

منبعِ یکتا برای هر دو:
  - handlers/admin_handler.py  (استفاده می‌کند)
  - core/safe_brain.py          (بعداً migrate می‌شود)

⚠️ هیچ import از config یا logger در این فایل نیست.
"""
from __future__ import annotations

import re

# ═══════════════════════════════════════════
# نگاشت پلتفرم (فارسی → canonical)
# ═══════════════════════════════════════════
_PLATFORM_MAP: dict[str, str] = {
    # فارسی
    "ایتا": "eitaa",
    "بله": "bale",
    "روبیکا": "rubika",
    "تلگرام": "telegram",
    "واتساپ": "whatsapp",
    "واتس‌اپ": "whatsapp",
    "لینکدین": "linkedin",
    "وب": "web",
    "سایت": "web",
    # انگلیسی
    "eitaa": "eitaa",
    "bale": "bale",
    "rubika": "rubika",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "linkedin": "linkedin",
    "web": "web",
}

# ═══════════════════════════════════════════
# Regex ها
# ═══════════════════════════════════════════
# مقدار داخل « » یا " " یا ' '
_VALUE_RE = re.compile(r"[«\"']([^»\"']+)[»\"']")

# set PARAM=VALUE یا set PARAM:VALUE
_SET_PARAM_RE = re.compile(r"([A-Z_]{3,})\s*[=:]\s*(\S+)")

# ═══════════════════════════════════════════
# لیست کلمات کلیدی (dedup شده)
# ═══════════════════════════════════════════
_HELP_WORDS = ("help", "راهنما", "کمک", "دستورات", "منو")

_RESTART_WORDS = (
    "restart", "ریاستارت", "ریستارت",
    "راه‌اندازی مجدد", "ری استارت",
)

_REMOVE_WORDS = ("حذف", "remove", "پاک", "delete")

_ADD_WORDS = ("اضافه", "add")

_HAS_WORDS = ("دار", "هست", "has", "exist")

_LEAD_WORDS = ("سرنخ", "پایش", "lead")

_LEAD_DETAIL_WORDS = (
    "جزئیات", "اولین", "آخرین",
    "detail", "first", "last", "نشون", "بگو",
)

_HEALTH_WORDS = (
    "سلامت", "health", "چک کامل",
    "تندرستی", "همه پلتفرم", "وضعیت کامل",
)

_WANT_WORDS = (
    "می‌خوام", "میخوام", "می‌خواهم", "میخواهم",
    "کاش", "اضافه کن", "اضاف کن",
)

_CAP_WORDS = ("بتون", "قابلیت", "feature", "توانایی")

_KEYWORD_WORDS = ("کلیدواژه", "کلید واژه", "keyword")

_REPORT_WORDS = ("گزارش", "report")

_CRM_WORDS = ("crm", "سی آر ام", "خروجی")

_LOG_WORDS = ("لاگ", "log")

_LOG_EXPLAIN_WORDS = (
    "چیه", "یعنی", "توضیح", "معنی", "explain", "چرا",
)

_STATUS_WORDS = (
    "وضعیت", "آمار", "اوضاع", "حال",
    "سرنخ", "lead", "status", "stat",
)

_PLATFORM_STATUS_WORDS = (
    "کار", "وصل", "working", "status", "?", "؟", "فعال",
)


# ═══════════════════════════════════════════
# توابع کمکی
# ═══════════════════════════════════════════
def extract_value(text: str) -> str | None:
    """مقدار داخل « » یا " " یا ' ' را برمی‌گرداند."""
    if not text:
        return None
    m = _VALUE_RE.search(text)
    return m.group(1).strip() if m else None


def platform_in(low: str) -> str | None:
    """نام پلتفرم را در متن پیدا می‌کند (canonical).

    Args:
        low: متن lowercase شده.

    Returns:
        نام canonical پلتفرم (eitaa/bale/...) یا None.
    """
    if not low:
        return None
    for word, canon in _PLATFORM_MAP.items():
        if word in low:
            return canon
    return None


# ═══════════════════════════════════════════
# پارسرِ اصلی دستور
# ═══════════════════════════════════════════
def parse_command(text: str) -> tuple[str | None, str | None]:
    """متن → (action, value). خالص.

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

    # ─── help ───
    if any(w in low for w in _HELP_WORDS):
        return "help", None

    # ─── restart ───
    if any(w in low for w in _RESTART_WORDS):
        return "restart", None

    # ─── remove_keyword ───
    if (
        any(w in low for w in _REMOVE_WORDS)
        and ("کلیدواژه" in low or "keyword" in low or val)
    ):
        return "remove_keyword", val

    # ─── add_keyword ───
    if (
        any(w in low for w in _ADD_WORDS)
        and ("کلیدواژه" in low or "keyword" in low or val)
    ):
        return "add_keyword", val

    # ─── has_keyword ───
    if (
        any(w in low for w in _HAS_WORDS)
        and ("کلیدواژه" in low or "keyword" in low)
    ):
        return "has_keyword", val

    # ─── lead_details ───
    if (
        any(w in low for w in _LEAD_WORDS)
        and any(w in low for w in _LEAD_DETAIL_WORDS)
    ):
        which = "first" if ("اولین" in low or "first" in low) else "last"
        return "lead_details", which

    # ─── health ───
    if any(w in low for w in _HEALTH_WORDS):
        return "health", None

    # ─── feature_request ───
    if (
        any(w in low for w in _WANT_WORDS)
        and any(w in low for w in _CAP_WORDS)
        and "کلیدواژه" not in low
    ):
        return "feature_request", None

    # ─── keywords (فهرست) ───
    if any(w in low for w in _KEYWORD_WORDS):
        return "keywords", None

    # ─── report ───
    if any(w in low for w in _REPORT_WORDS):
        return "report", None

    # ─── crm ───
    if any(w in low for w in _CRM_WORDS):
        return "crm", None

    # ─── explain_logs ───
    if (
        any(w in low for w in _LOG_WORDS)
        and any(w in low for w in _LOG_EXPLAIN_WORDS)
    ):
        return "explain_logs", None

    # ─── logs ───
    if any(w in low for w in _LOG_WORDS):
        return "logs", None

    # ─── status ─── (فقط اگر پلتفرم مشخصی در متن نباشد)
    if any(w in low for w in _STATUS_WORDS) and not platform_in(low):
        return "status", None

    # ─── platform_status ───
    p = platform_in(low)
    if p and any(w in low for w in _PLATFORM_STATUS_WORDS):
        return "platform_status", p

    # ─── ناشناخته ───
    return None, val


# ═══════════════════════════════════════════
# پارسرِ set PARAM=VALUE
# ═══════════════════════════════════════════
def parse_set_param(text: str) -> tuple[str | None, str | None]:
    """پارسرِ `set PARAM=VALUE` → (NAME, value).

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
    return m.group(1).upper(), m.group(2)