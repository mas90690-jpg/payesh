import asyncio
import re


FILES = [
    # core/brain
    "core/brain/main.py",
    "core/brain/constants.py",
    "core/brain/utils.py",
    "core/brain/learning.py",
    "core/brain/memory.py",
    "core/brain/mixin_agent.py",
    "core/brain/mixin_files.py",
    "core/brain/mixin_info.py",
    "core/brain/mixin_platforms.py",
    "core/brain/mixin_reports.py",
    "core/brain/mixin_system.py",
    "core/brain/tools_audit.py",
    "core/brain/tools_meta.py",
    "core/brain/__init__.py",
    # core/kernel
    "core/kernel/logger.py",
    "core/kernel/event_bus.py",
    "core/kernel/models.py",
    "core/kernel/console_compat.py",
    # core/app
    "core/app/orchestrator.py",
    "core/app/plugin_loader.py",
    # core/database
    "core/database/base.py",
    "core/database/sqlite.py",
    "core/database/postgres.py",
    "core/database/factory.py",
    # core/adapters
    "core/adapters/command_parser.py",
    "core/adapters/messenger_registry.py",
    "core/adapters/translator.py",
    "core/adapters/formula_manager.py",
    "core/adapters/access_manager.py",
    "core/adapters/web_media.py",
    "core/adapters/browser_lock.py",
    # core/manual
    "core/manual/brain.py",
    "core/manual/_brain_core.py",
    "core/manual/_brain_rules.py",
    "core/manual/_brain_keywords.py",
    "core/manual/_brain_learning.py",
    "core/manual/_brain_settings.py",
    "core/manual/data_api.py",
    "core/manual/rules.py",
    # core/safe_brain
    "core/safe_brain/main.py",
    "core/safe_brain/constants.py",
    "core/safe_brain/storage.py",
    # core/services
    "core/services/ai_assistant.py",
    "core/services/daily_reporter.py",
    "core/services/crm_export.py",
    "core/services/backup_service.py",
    "core/services/cleanup_service.py",
    # core/ops
    "core/ops/health_check.py",
    "core/ops/monitor.py",
    "core/ops/obs_server.py",
    "core/ops/self_monitor.py",
    # core/contracts
    "core/contracts/base_collector.py",
    "core/contracts/base_handler.py",
    "core/contracts/base_analyzer.py",
    # handlers
    "handlers/__init__.py",
    "handlers/_ai_utils.py",
    "handlers/admin_actions.py",
    "handlers/admin_handler.py",
    "handlers/admin_memory.py",
    "handlers/auto_subscribe.py",
    "handlers/keyword_admin.py",
    "handlers/storage_handler.py",
    # analyzers
    "analyzers/keyword_finder.py",
    "analyzers/lead_scorer.py",
    "analyzers/priority_finder.py",
    "analyzers/target_filter.py",
    # collectors
    "collectors/bale.py",
    "collectors/eitaa.py",
    "collectors/rubika.py",
    "collectors/telegram.py",
    "collectors/whatsapp.py",
    "collectors/linkedin.py",
    "collectors/instagram.py",
    "collectors/web.py",
]


def check_file(path):
    print("=" * 60)
    print("FILE:", path)
    print("=" * 60)
    try:
        with open(path, "rb") as f:
            raw = f.read(3)
        if raw == b"\xef\xbb\xbf":
            print("  BOM: YES (must remove)")
        else:
            print("  BOM: NO")
    except Exception as e:
        print("  BOM check error:", e)
        return
    try:
        s = open(path, encoding="utf-8").read()
        print("  len:", len(s))
        print("  lines:", s.count(chr(10)) + 1)
        first = s.split(chr(10))[0]
        print("  first:", repr(first[:60]))
    except Exception as e:
        print("  read error:", e)
        return
    try:
        import subprocess
        r = subprocess.run(
            ["python", "-m", "py_compile", path],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print("  syntax: OK")
        else:
            print("  syntax: ERROR")
            print("  ", r.stderr[:300])
    except Exception as e:
        print("  compile error:", e)


for f in FILES:
    check_file(f)