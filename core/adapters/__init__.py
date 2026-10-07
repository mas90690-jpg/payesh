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
    register_sender,
    unregister_sender,
    get_sender,
    is_registered,
    get_registered_platforms,
    notify_admins,
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
    # access
    "AccessManager",
    "get_access_manager",
    "reset_access_manager",
    # browser
    "BROWSER_LOCK",
    "get_browser_lock",
    "reset_browser_lock",
    "browser_is_locked",
    # command
    "extract_value",
    "parse_command",
    "parse_set_param",
    "platform_in",
    # formula
    "FormulaManager",
    "get_formula_manager",
    "reset_formula_manager",
    # messenger
    "register_sender",
    "unregister_sender",
    "get_sender",
    "is_registered",
    "get_registered_platforms",
    "notify_admins",
    # translator
    "Translator",
    "get_translator",
    "reset_translator",
    "translate_text",
    # web_media
    "download_dom_image",
]