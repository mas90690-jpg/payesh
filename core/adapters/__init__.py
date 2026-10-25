"""core.adapters — آداپترها (دسترسی، فرمول، مترجم، ...)."""

# ─── access ───
from core.adapters.access_manager import (
    AccessManager,
    get_access_manager,
    reset_access_manager,
)

# ─── browser ───
from core.adapters.browser_lock import (
    BROWSER_LOCK,
    get_browser_lock,
    reset_browser_lock,
)
from core.adapters.browser_lock import (
    is_locked as browser_is_locked,
)

# ─── command ───
from core.adapters.command_parser import (
    extract_value,
    parse_command,
    parse_set_param,
    platform_in,
)

# ─── formula ───
from core.adapters.formula_manager import (
    FormulaManager,
    get_formula_manager,
    reset_formula_manager,
)

# ─── messenger ───
from core.adapters.messenger_registry import (
    get_registered_platforms,
    get_sender,
    is_registered,
    notify_admins,
    register_sender,
    unregister_sender,
)

# ─── translator ───
from core.adapters.translator import (
    Translator,
    get_translator,
    reset_translator,
    translate_text,
)

# ─── web_media ───
from core.adapters.web_media import (
    download_dom_image,
)

__all__ = [
    # browser
    "BROWSER_LOCK",
    # access
    "AccessManager",
    # formula
    "FormulaManager",
    # translator
    "Translator",
    "browser_is_locked",
    # web_media
    "download_dom_image",
    # command
    "extract_value",
    "get_access_manager",
    "get_browser_lock",
    "get_formula_manager",
    "get_registered_platforms",
    "get_sender",
    "get_translator",
    "is_registered",
    "notify_admins",
    "parse_command",
    "parse_set_param",
    "platform_in",
    # messenger
    "register_sender",
    "reset_access_manager",
    "reset_browser_lock",
    "reset_formula_manager",
    "reset_translator",
    "translate_text",
    "unregister_sender",
]