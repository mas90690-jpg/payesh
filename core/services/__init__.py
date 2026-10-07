"""core.services — سرویس‌ها (AI, Backup, Cleanup, CRM, Report)."""

from core.services.ai_assistant import (
    AIAssistant,
    get_ai_assistant,
    reset_ai_assistant,
)
from core.services.backup_service import (
    BackupService,
    start_backup_service,
)
from core.services.cleanup_service import (
    CleanupService,
    start_cleanup_service,
)
from core.services.crm_export import (
    export_crm,
    CRM_HEADERS,
)
from core.services.daily_reporter import (
    DailyReporter,
    start_daily_reporter,
    generate_executive_report,
    generate_detailed_report,
)

__all__ = [
    # AI
    "AIAssistant", "get_ai_assistant", "reset_ai_assistant",
    # Backup
    "BackupService", "start_backup_service",
    # Cleanup
    "CleanupService", "start_cleanup_service",
    # CRM
    "export_crm", "CRM_HEADERS",
    # Reporter
    "DailyReporter", "start_daily_reporter",
    "generate_executive_report", "generate_detailed_report",
]