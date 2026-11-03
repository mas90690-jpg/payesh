"""core.kernel — هستهٔ سیستم."""

# ─── logger ───
# ─── console ───
from core.kernel.console_compat import (
    get_status,
    is_patched,
    patch_console,
    restore_console,
)

# ─── event bus ───
from core.kernel.event_bus import (
    Event,
    EventBus,
    EventPriority,
)
from core.kernel.logger import (
    get_logger,
    log_critical,
    log_debug,
    log_duration,
    log_error,
    log_exception,
    log_info,
    log_success,
    log_warning,
    reset_logger,
)

__all__ = [
    "Event",
    # event bus
    "EventBus",
    "EventPriority",
    "get_logger",
    "get_status",
    "is_patched",
    "log_critical",
    "log_debug",
    "log_duration",
    "log_error",
    "log_exception",
    # logger
    "log_info",
    "log_success",
    "log_warning",
    # console
    "patch_console",
    "reset_logger",
    "restore_console",
]
