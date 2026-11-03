"""smart_controller.py — re-export از controller.py.

⚠️ این فایل فقط برای سازگاری نگه داشته شده.
همه چیز در controller.py تعریف شده.
"""
from core.kernel.logger import log_info
from .controller import (
    PLATFORM_SELECTORS,
    SmartController,
    get_controller,
    get_smart_controller,
    list_actions,
    list_platforms,
    list_supported_platforms,
)

log_info(f'✅ [MODULE] {__name__}')

# alias
list_actions = list_actions
list_supported_platforms = list_supported_platforms


__all__ = [
    "PLATFORM_SELECTORS",
    "SmartController",
    "get_controller",
    "get_smart_controller",
    "list_actions",
    "list_platforms",
    "list_supported_platforms",
]
