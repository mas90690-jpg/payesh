"""تعریف ابزارهای Brain — metadata (بدون پیاده‌سازی)."""
from __future__ import annotations

ALLOWED_TOOLS: dict[str, dict] = {
    "read_file": {"risk": "low", "require_confirm": False},
    "list_directory": {"risk": "low", "require_confirm": False},
    "git_status": {"risk": "low", "require_confirm": False},
    "git_diff": {"risk": "low", "require_confirm": False},
    "git_log": {"risk": "low", "require_confirm": False},
    "get_system_info": {"risk": "low", "require_confirm": False},
    "list_processes": {"risk": "low", "require_confirm": False},
    "ping_host": {"risk": "low", "require_confirm": False},
    "analyze_code": {"risk": "low", "require_confirm": False},
    "semantic_search": {"risk": "low", "require_confirm": False},
    "grep_search": {"risk": "low", "require_confirm": False},
    "query_database": {"risk": "low", "require_confirm": False},
    "run_tests": {"risk": "low", "require_confirm": False},
    "clipboard_get": {"risk": "low", "require_confirm": False},
    "browse_web": {"risk": "low", "require_confirm": False},
    "web_search": {"risk": "low", "require_confirm": False},
    "get_project_context": {"risk": "low", "require_confirm": False},
    "system_dashboard": {"risk": "low", "require_confirm": False},
    "zip_archive": {"risk": "low", "require_confirm": False},
    "write_file": {"risk": "medium", "require_confirm": True},
    "edit_file": {"risk": "medium", "require_confirm": True},
    "delete_file": {"risk": "high", "require_confirm": True},
    "move_file": {"risk": "medium", "require_confirm": True},
    "git_commit": {"risk": "medium", "require_confirm": True},
    "git_branch": {"risk": "medium", "require_confirm": True},
    "kill_process": {"risk": "high", "require_confirm": True},
    "run_tests_coverage": {"risk": "low", "require_confirm": False},
    "analyze_security": {"risk": "low", "require_confirm": False},
    "backup_project": {"risk": "low", "require_confirm": False},
    "execute_command": {"risk": "high", "require_confirm": True},
    "execute_python": {"risk": "high", "require_confirm": True},
    "fix_bug": {"risk": "medium", "require_confirm": True},
    "generate_tests": {"risk": "low", "require_confirm": False},
    "code_review": {"risk": "low", "require_confirm": False},
    "plan_task": {"risk": "low", "require_confirm": False},
    "get_setting": {"risk": "low", "require_confirm": False},
    "set_setting": {"risk": "medium", "require_confirm": True},
    "toggle_platform": {"risk": "medium", "require_confirm": True},
    "platform_status": {"risk": "low", "require_confirm": False},
    "change_poll_interval": {
        "risk": "medium",
        "require_confirm": True,
    },
    "get_access_summary": {"risk": "low", "require_confirm": False},
}


ALLOWED_DIRECT_COMMANDS: dict[str, str] = {
    "سلام": "greeting",
    "خوبی": "greeting",
    "وضعیت": "status",
    "گزارش": "report",
    "help": "help",
    "راهنما": "help",
    "کمک": "help",
}


def _td(
    name: str, desc: str, props: dict, required: list[str]
) -> dict:
    """ابزار OpenAI function-calling."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


TOOL_DEFINITIONS: list[dict] = [
    _td(
        "read_file", "خواندن کامل یک فایل (تا ۵ مگ)",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "write_file",
        "نوشتن یا بازنویسی یک فایل (نیاز به تأیید)",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
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
        "list_directory", "لیست محتویات یک پوشه",
        {"path": {"type": "string", "default": "."}}, [],
    ),
    _td(
        "delete_file",
        "حذف امن یک فایل با بکاپ (نیاز به تأیید)",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "move_file", "جابجایی/تغییر نام (نیاز به تأیید)",
        {
            "source": {"type": "string"},
            "destination": {"type": "string"},
        },
        ["source", "destination"],
    ),
    _td("git_status", "نمایش وضعیت Git", {}, []),
    _td(
        "git_diff", "نمایش تغییرات Git",
        {"path": {"type": "string"}}, [],
    ),
    _td(
        "git_commit", "ثبت commit (نیاز به تأیید)",
        {"message": {"type": "string"}}, ["message"],
    ),
    _td(
        "git_log", "نمایش تاریخچه",
        {"n": {"type": "integer", "default": 10}}, [],
    ),
    _td(
        "git_branch", "ایجاد/تغییر برنچ",
        {
            "name": {"type": "string"},
            "action": {"type": "string", "enum": ["create", "switch"]},
        },
        ["name"],
    ),
    _td(
        "execute_command", "اجرای دستور سیستمی (نیاز به تأیید)",
        {"command": {"type": "string"}}, ["command"],
    ),
    _td("get_system_info", "اطلاعات سیستم", {}, []),
    _td(
        "list_processes", "لیست پردازه‌ها",
        {"limit": {"type": "integer", "default": 20}}, [],
    ),
    _td(
        "kill_process", "پایان دادن به پردازه",
        {"pid": {"type": "integer"}}, ["pid"],
    ),
    _td(
        "ping_host", "تست اتصال به هاست",
        {"host": {"type": "string"}}, ["host"],
    ),
    _td("run_tests", "اجرای تست‌ها", {"path": {"type": "string"}}, []),
    _td(
        "execute_python", "اجرای پایتون در sandbox",
        {"code": {"type": "string"}}, ["code"],
    ),
    _td(
        "analyze_code", "تحلیل استاتیک با AST",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "semantic_search", "جستجوی معنایی",
        {
            "query": {"type": "string"},
            "n_results": {"type": "integer", "default": 5},
        },
        ["query"],
    ),
    _td(
        "grep_search", "جستجو با regex",
        {
            "pattern": {"type": "string"},
            "file_pattern": {"type": "string", "default": "*.py"},
        },
        ["pattern"],
    ),
    _td(
        "query_database", "کوئری SELECT",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
        ["query"],
    ),
    _td(
        "browse_web", "خواندن متن یک صفحه",
        {"url": {"type": "string"}}, ["url"],
    ),
    _td("clipboard_get", "خواندن کلیپ‌بورد", {}, []),
    _td(
        "zip_archive", "فشرده‌سازی/باز کردن ZIP",
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
    _td(
        "web_search", "جستجوی وب",
        {
            "query": {"type": "string"},
            "num_results": {"type": "integer", "default": 5},
        },
        ["query"],
    ),
    _td(
        "generate_tests", "تولید تست خودکار",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "code_review", "بازبینی کد",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "fix_bug", "رفع خودکار باگ",
        {
            "path": {"type": "string"},
            "error": {"type": "string"},
        },
        ["path", "error"],
    ),
    _td(
        "plan_task", "برنامه‌ریزی چندمرحله‌ای",
        {"task": {"type": "string"}}, ["task"],
    ),
    _td("get_project_context", "اطلاعات پروژه", {}, []),
    _td("system_dashboard", "داشبورد سیستم", {}, []),
    _td(
        "run_tests_coverage", "تست با پوشش کد",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "analyze_security", "بررسی امنیت با bandit",
        {"path": {"type": "string"}}, ["path"],
    ),
    _td(
        "backup_project", "بکاپ کامل پروژه",
        {
            "destination": {
                "type": "string",
                "description": "نام فایل خروجی",
            }
        },
        [],
    ),
    _td(
        "get_setting", "خواندن تنظیمات از .env",
        {"key": {"type": "string"}}, ["key"],
    ),
    _td(
        "set_setting", "تغییر تنظیمات",
        {
            "key": {"type": "string"},
            "value": {"type": "string"},
        },
        ["key", "value"],
    ),
    _td(
        "toggle_platform", "روشن/خاموش کردن پلتفرم",
        {
            "platform": {"type": "string"},
            "enabled": {"type": "boolean"},
        },
        ["platform", "enabled"],
    ),
    _td(
        "platform_status", "وضعیت پلتفرم",
        {"platform": {"type": "string", "default": "all"}}, [],
    ),
    _td(
        "change_poll_interval", "تغییر فاصلهٔ poll",
        {
            "platform": {"type": "string"},
            "seconds": {"type": "integer"},
        },
        ["platform", "seconds"],
    ),
    _td("get_access_summary", "خلاصهٔ دسترسی‌ها", {}, []),
]


def build_tool_definitions() -> list[dict]:
    """برگرداندن کپی از TOOL_DEFINITIONS."""
    return list(TOOL_DEFINITIONS)
