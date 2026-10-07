"""core.kernel — هستهٔ سیستم."""

# ─── logger ───
from core.kernel.logger import (
    log_info,
    log_success,
    log_error,
    log_warning,
    log_debug,
    log_exception,
    log_critical,
    log_duration,
    get_logger,
    reset_logger,
)

# ─── console ───
from core.kernel.console_compat import (
    patch_console,
    restore_console,
    is_patched,
    get_status,
)

# ─── event bus ───
from core.kernel.event_bus import (
    EventBus,
    Event,
    EventPriority,
)

__all__ = [
    # logger
    "log_info",
    "log_success",
    "log_error",
    "log_warning",
    "log_debug",
    "log_exception",
    "log_critical",
    "log_duration",
    "get_logger",
    "reset_logger",
    # console
    "patch_console",
    "restore_console",
    "is_patched",
    "get_status",
    # event bus
    "EventBus",
    "Event",
    "EventPriority",
]