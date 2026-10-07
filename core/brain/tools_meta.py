"""
تعریف ابزارهای Brain — metadata (بدون پیاده‌سازی).

نسخهٔ ۴ (این نسخه):
  - 🎯 NEW: `audit_code` — بررسی کد با pyflakes + flake8.
  - 🎯 NEW: `audit_file` — بررسی یک فایل خاص.
  - 🎯 NEW: `fix_fstrings` — درست کردن f-string های بی‌placeholder.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.

نسخهٔ ۳ (قبلی):
  - 🎯 NEW: `append_knowledge` — افزودن به bot_knowledge.md.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.

نسخهٔ ۲ (قبلی):
  - 🎯 `ALLOWED_DIRECT_COMMANDS` حالا به `DIRECT_COMMANDS` تغییر نام.
  - 🎯 `ALLOWED_TOOLS` و `DIRECT_COMMANDS` حالا
    `MappingProxyType` (immutable).
  - 🐛 FIX: `build_tool_definitions()` حالا `deepcopy`.
  - 🎯 `get_tool_info(name)` متد جدید.
  - 🎯 `get_tools_by_risk(risk)` متد جدید.
  - 🎯 `_td` helper حالا `returns` اختیاری هم می‌گیرد.
"""
from __future__ import annotations

import copy
from types import MappingProxyType
from typing import Any


# ═══════════════════════════════════════════════════════════
# سطوح ریسک
# ═══════════════════════════════════════════════════════════
_RISK_LEVELS = frozenset({"low", "medium", "high"})


def _tool(
    risk: str = "low",
    require_confirm: bool = False,
) -> dict:
    """ساخت dict متادیتای یک ابزار."""
    if risk not in _RISK_LEVELS:
        risk = "low"
    return {
        "risk": risk,
        "require_confirm": bool(require_confirm),
    }


# ═══════════════════════════════════════════════════════════
# ابزارهای مجاز (name → metadata)
# ═══════════════════════════════════════════════════════════
_ALLOWED_TOOLS_RAW: dict[str, dict] = {
    # ─── فایل (low) ───
    "read_file": _tool("low"),
    "list_directory": _tool("low"),
    "analyze_code": _tool("low"),
    "semantic_search": _tool("low"),
    "grep_search": _tool("low"),
    "get_project_context": _tool("low"),
    "system_dashboard": _tool("low"),
    "get_system_info": _tool("low"),
    "list_processes": _tool("low"),
    "clipboard_get": _tool("low"),
    "browse_web": _tool("low"),
    "web_search": _tool("low"),
    "ping_host": _tool("low"),
    "zip_archive": _tool("low"),

    # ─── Git (low) ───
    "git_status": _tool("low"),
    "git_diff": _tool("low"),
    "git_log": _tool("low"),

    # ─── تست (low) ───
    "run_tests": _tool("low"),
    "run_tests_coverage": _tool("low"),
    "analyze_security": _tool("low"),

    # ─── دیتابیس (low — SELECT فقط) ───
    "query_database": _tool("low"),

    # ─── settings (low — خواندن) ───
    "get_setting": _tool("low"),
    "get_access_summary": _tool("low"),
    "platform_status": _tool("low"),

    # ─── AI helper (low) ───
    "generate_tests": _tool("low"),
    "code_review": _tool("low"),
    "plan_task": _tool("low"),

    # ─── backup (low — فقط zip) ───
    "backup_project": _tool("low"),

    # ─── 🎯 NEW: audit (low — فقط بررسی، بدون تغییر) ───
    "audit_code": _tool("low"),
    "audit_file": _tool("low"),

    # ─── فایل (medium — write) ───
    "write_file": _tool("medium", True),
    "edit_file": _tool("medium", True),
    "move_file": _tool("medium", True),
    "fix_bug": _tool("medium", True),

    # ─── 🎯 NEW: append_knowledge (medium — بدون تأیید) ───
    "append_knowledge": _tool("medium", False),

    # ─── 🎯 NEW: fix_fstrings (medium — تغییر فایل، نیاز به تأیید) ───
    "fix_fstrings": _tool("medium", True),

    # ─── Git (medium) ───
    "git_commit": _tool("medium", True),
    "git_branch": _tool("medium", True),

    # ─── settings (medium) ───
    "set_setting": _tool("medium", True),
    "toggle_platform": _tool("medium", True),
    "change_poll_interval": _tool("medium", True),

    # ─── فایل (high — delete) ───
    "delete_file": _tool("high", True),
    "kill_process": _tool("high", True),

    # ─── اجرا (high) ───
    "execute_command": _tool("high", True),
    "execute_python": _tool("high", True),
}


# 🎯 immutable export
ALLOWED_TOOLS: MappingProxyType = MappingProxyType(
    _ALLOWED_TOOLS_RAW
)


# ═══════════════════════════════════════════════════════════
# دستورات مستقیم (بدون AI)
# ═══════════════════════════════════════════════════════════
_DIRECT_COMMANDS_RAW: dict[str, str] = {
    "سلام": "greeting",
    "خوبی": "greeting",
    "درود": "greeting",
    "وضعیت": "status",
    "آمار": "status",
    "گزارش": "report",
    "help": "help",
    "راهنما": "help",
    "کمک": "help",
}


# 🎯 immutable export
DIRECT_COMMANDS: MappingProxyType = MappingProxyType(
    _DIRECT_COMMANDS_RAW
)

# ⚠️ Backward-compat: نام قدیمی حفظ شد
ALLOWED_DIRECT_COMMANDS: MappingProxyType = DIRECT_COMMANDS


# ═══════════════════════════════════════════════════════════
# helper ساخت tool definition
# ═══════════════════════════════════════════════════════════
def _td(
    name: str,
    desc: str,
    props: dict[str, Any],
    required: list[str],
    returns: str | None = None,
) -> dict:
    """ساخت tool definition مطابق OpenAI function-calling.

    Args:
        name: نام ابزار.
        desc: توضیح فارسی.
        props: dict از پارامترها → {type, description?, default?, enum?}.
        required: لیست پارامترهای الزامی.
        returns: توضیح خروجی (اختیاری، به description اضافه می‌شود).

    Returns:
        dict مطابق فرمت OpenAI tools.
    """
    if returns:
        desc = f"{desc}\nخروجی: {returns}"

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(required),
            },
        },
    }


# ═══════════════════════════════════════════════════════════
# TOOL_DEFINITIONS
# ═══════════════════════════════════════════════════════════
TOOL_DEFINITIONS: list[dict] = [
    # ─── فایل (read) ───
    _td(
        "read_file",
        "خواندن کامل یک فایل (تا ۲۰ مگابایت)",
        {"path": {"type": "string", "description": "مسیر فایل"}},
        ["path"],
        returns="متن فایل یا پیام خطا",
    ),
    _td(
        "write_file",
        "نوشتن یا بازنویسی یک فایل (نیاز به تأیید). "
        "⚠️ روی فایل‌های دانشی (bot_knowledge.md, persona.md, "
        "system.md) قفل است — از append_knowledge استفاده کن.",
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        ["path", "content"],
        returns="✅ موفق / ❌ خطا",
    ),
    _td(
        "edit_file",
        "ویرایش جراحی در یک فایل (نیاز به تأیید)",
        {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        ["path", "old_text", "new_text"],
    ),
    _td(
        "list_directory",
        "لیست محتویات یک پوشه",
        {"path": {"type": "string", "default": "."}},
        [],
    ),
    _td(
        "delete_file",
        "حذف امن یک فایل با بکاپ (نیاز به تأیید)",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _td(
        "move_file",
        "جابجایی/تغییر نام (نیاز به تأیید)",
        {
            "source": {"type": "string"},
            "destination": {"type": "string"},
        },
        ["source", "destination"],
    ),

    # ─── 🎯 NEW: append_knowledge ───
    _td(
        "append_knowledge",
        "افزودن دانش جدید به data/prompts/bot_knowledge.md — "
        "فقط append، هرگز overwrite نمی‌کند، بکاپ خودکار. "
        "برای یادگیری خودکار استفاده کن.",
        {
            "content": {
                "type": "string",
                "description": (
                    "متن دانشی که باید اضافه شود "
                    "(حداکثر ۵۰۰۰ کاراکتر)"
                ),
            },
            "section": {
                "type": "string",
                "description": (
                    "عنوان بخش — پیش‌فرض: «یاد گرفته‌شده‌ها»"
                ),
                "default": "یاد گرفته‌شده‌ها",
            },
        },
        ["content"],
        returns="✅ موفق / ℹ️ تکراری / ❌ خطا",
    ),

    # ─── 🎯 NEW: audit_code ───
    _td(
        "audit_code",
        "بررسی کامل کد پایتون با pyflakes + flake8 (+ vulture + bandit). "
        "پیدا کردن: import اضافه، متغیر استفاده‌نشده، "
        "اسم تعریف‌نشده، f-string بی‌placeholder، غلط تایپی، "
        "مشکلات منطقی. خروجی: dict ساختاریافته با دسته‌بندی.",
        {
            "paths": {
                "type": "string",
                "description": (
                    "مسیرها با فاصله "
                    "(پیش‌فرض: core/ handlers/ collectors/ "
                    "analyzers/ main.py)"
                ),
                "default": (
                    "core/ handlers/ collectors/ "
                    "analyzers/ main.py"
                ),
            },
            "include_flake8": {
                "type": "boolean",
                "description": "بررسی با flake8 (پیش‌فرض True)",
                "default": True,
            },
            "include_vulture": {
                "type": "boolean",
                "description": "بررسی کد بلااستفاده (کندتر)",
                "default": False,
            },
            "include_bandit": {
                "type": "boolean",
                "description": "بررسی امنیت (کندتر)",
                "default": False,
            },
        },
        [],
        returns="dict با summary + details + raw_output",
    ),

    # ─── 🎯 NEW: audit_file ───
    _td(
        "audit_file",
        "بررسی یک فایل پایتون خاص با pyflakes + flake8.",
        {
            "file_path": {
                "type": "string",
                "description": (
                    "مسیر فایل (مثلاً: core/brain/main.py)"
                ),
            },
            "include_flake8": {
                "type": "boolean",
                "default": True,
            },
        },
        ["file_path"],
        returns="dict با summary + details",
    ),

    # ─── 🎯 NEW: fix_fstrings ───
    _td(
        "fix_fstrings",
        "درست کردن f-string های بی‌placeholder (f\"...\" → \"...\"). "
        "⚠️ فایل‌ها را تغییر می‌دهد — قبلش بکاپ بگیر. "
        "با dry_run=True فقط نشان می‌دهد.",
        {
            "paths": {
                "type": "string",
                "description": "مسیرها با فاصله",
                "default": (
                    "core/ handlers/ collectors/ "
                    "analyzers/ main.py"
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "اگه True، فقط نشون بده (پیش‌فرض)"
                ),
                "default": True,
            },
        },
        [],
        returns="dict با files_fixed + fixes",
    ),

    # ─── Git ───
    _td("git_status", "نمایش وضعیت Git", {}, []),
    _td(
        "git_diff",
        "نمایش تغییرات Git",
        {"path": {"type": "string"}},
        [],
    ),
    _td(
        "git_commit",
        "ثبت commit (نیاز به تأیید)",
        {"message": {"type": "string"}},
        ["message"],
    ),
    _td(
        "git_log",
        "نمایش تاریخچه",
        {"n": {"type": "integer", "default": 10}},
        [],
    ),
    _td(
        "git_branch",
        "ایجاد/تغییر برنچ",
        {
            "name": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["create", "switch"],
            },
        },
        ["name"],
    ),

    # ─── اجرا ───
    _td(
        "execute_command",
        "اجرای دستور سیستمی (نیاز به تأیید)",
        {"command": {"type": "string"}},
        ["command"],
    ),
    _td(
        "execute_python",
        "اجرای پایتون در sandbox (نیاز به تأیید)",
        {"code": {"type": "string"}},
        ["code"],
    ),

    # ─── سیستم ───
    _td("get_system_info", "اطلاعات سیستم", {}, []),
    _td(
        "list_processes",
        "لیست پردازه‌ها",
        {"limit": {"type": "integer", "default": 20}},
        [],
    ),
    _td(
        "kill_process",
        "پایان دادن به پردازه (نیاز به تأیید)",
        {"pid": {"type": "integer"}},
        ["pid"],
    ),
    _td(
        "ping_host",
        "تست اتصال به هاست",
        {"host": {"type": "string"}},
        ["host"],
    ),
    _td(
        "system_dashboard",
        "داشبورد سیستم (CPU/RAM/Disk)",
        {},
        [],
    ),

    # ─── تست ───
    _td(
        "run_tests",
        "اجرای تست‌ها",
        {"path": {"type": "string"}},
        [],
    ),
    _td(
        "run_tests_coverage",
        "تست با پوشش کد",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _td(
        "analyze_security",
        "بررسی امنیت با bandit",
        {"path": {"type": "string"}},
        ["path"],
    ),

    # ─── تحلیل ───
    _td(
        "analyze_code",
        "تحلیل استاتیک با AST",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _td(
        "semantic_search",
        "جستجوی معنایی",
        {
            "query": {"type": "string"},
            "n_results": {"type": "integer", "default": 5},
        },
        ["query"],
    ),
    _td(
        "grep_search",
        "جستجو با regex",
        {
            "pattern": {"type": "string"},
            "file_pattern": {
                "type": "string",
                "default": "*.py",
            },
        },
        ["pattern"],
    ),

    # ─── دیتابیس ───
    _td(
        "query_database",
        "کوئری SELECT",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
        ["query"],
    ),

    # ─── وب ───
    _td(
        "browse_web",
        "خواندن متن یک صفحه",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _td(
        "web_search",
        "جستجوی وب",
        {
            "query": {"type": "string"},
            "num_results": {"type": "integer", "default": 5},
        },
        ["query"],
    ),

    # ─── clipboard ───
    _td("clipboard_get", "خواندن کلیپ‌بورد", {}, []),

    # ─── zip ───
    _td(
        "zip_archive",
        "فشرده‌سازی/باز کردن ZIP",
        {
            "source": {"type": "string"},
            "dest": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["compress", "extract"],
                "default": "compress",
            },
        },
        ["source"],
    ),

    # ─── AI helpers ───
    _td(
        "generate_tests",
        "تولید تست خودکار با AI",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _td(
        "code_review",
        "بازبینی کد با AI",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _td(
        "fix_bug",
        "رفع خودکار باگ (نیاز به تأیید)",
        {
            "path": {"type": "string"},
            "error": {"type": "string"},
        },
        ["path", "error"],
    ),
    _td(
        "plan_task",
        "برنامه‌ریزی چندمرحله‌ای",
        {"task": {"type": "string"}},
        ["task"],
    ),

    # ─── پروژه ───
    _td("get_project_context", "اطلاعات پروژه", {}, []),
    _td(
        "backup_project",
        "بکاپ کامل پروژه (zip)",
        {
            "destination": {
                "type": "string",
                "description": "نام فایل خروجی",
            }
        },
        [],
    ),

    # ─── تنظیمات ───
    _td(
        "get_setting",
        "خواندن تنظیمات از .env یا config",
        {"key": {"type": "string"}},
        ["key"],
    ),
    _td(
        "set_setting",
        "تغییر تنظیمات (نیاز به تأیید)",
        {
            "key": {"type": "string"},
            "value": {"type": "string"},
        },
        ["key", "value"],
    ),
    _td(
        "toggle_platform",
        "روشن/خاموش کردن پلتفرم (نیاز به تأیید)",
        {
            "platform": {"type": "string"},
            "enabled": {"type": "boolean"},
        },
        ["platform", "enabled"],
    ),
    _td(
        "platform_status",
        "وضعیت پلتفرم",
        {
            "platform": {
                "type": "string",
                "default": "all",
            }
        },
        [],
    ),
    _td(
        "change_poll_interval",
        "تغییر فاصلهٔ poll (نیاز به تأیید)",
        {
            "platform": {"type": "string"},
            "seconds": {"type": "integer"},
        },
        ["platform", "seconds"],
    ),
    _td("get_access_summary", "خلاصهٔ دسترسی‌ها", {}, []),
]


# ═══════════════════════════════════════════════════════════
# API عمومی
# ═══════════════════════════════════════════════════════════
def build_tool_definitions() -> list[dict]:
    """🎯 FIX: برگرداندن deep-copy از TOOL_DEFINITIONS."""
    return copy.deepcopy(TOOL_DEFINITIONS)


def get_tool_info(name: str) -> dict | None:
    """🎯 متادیتای یک ابزار یا None."""
    if not name:
        return None
    info = ALLOWED_TOOLS.get(name)
    return dict(info) if info else None


def get_tools_by_risk(risk: str) -> list[str]:
    """🎯 ابزارهای یک سطح ریسک مشخص."""
    if not risk:
        return []
    risk_low = str(risk).lower()
    if risk_low not in _RISK_LEVELS:
        return []
    return sorted(
        name for name, info in ALLOWED_TOOLS.items()
        if info.get("risk") == risk_low
    )


def get_tools_requiring_confirm() -> list[str]:
    """🎯 ابزارهایی که نیاز به تأیید دارند."""
    return sorted(
        name for name, info in ALLOWED_TOOLS.items()
        if info.get("require_confirm")
    )


def get_tool_count() -> int:
    """تعداد ابزارهای مجاز."""
    return len(ALLOWED_TOOLS)


def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت ابزارها — برای debug."""
    by_risk: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    for info in ALLOWED_TOOLS.values():
        r = info.get("risk", "low")
        if r in by_risk:
            by_risk[r] += 1

    return {
        "total_tools": len(ALLOWED_TOOLS),
        "by_risk": by_risk,
        "require_confirm": len(get_tools_requiring_confirm()),
        "tool_definitions": len(TOOL_DEFINITIONS),
        "direct_commands": len(DIRECT_COMMANDS),
    }