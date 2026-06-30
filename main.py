# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import signal


# ==================================================
# بررسی محیط مجازی (VENV)
# ==================================================
def is_venv() -> bool:
    """بررسی می‌کند که آیا در محیط مجازی هستیم یا خیر."""
    return (sys.prefix != sys.base_prefix) or (os.environ.get("VIRTUAL_ENV") is not None)


if not is_venv():
    print("=" * 60)
    print("❌ این ربات فقط در محیط مجازی (venv) اجرا می‌شود.")
    print("=" * 60)
    print()
    print("لطفاً ابتدا venv را فعال کنید:")
    print("   venv\\Scripts\\activate")
    print()
    print("سپس دوباره اجرا کنید.")
    print("=" * 60)
    sys.exit(1)
# ==================================================


from config import PYCACHE_DIR

os.makedirs(PYCACHE_DIR, exist_ok=True)
os.environ["PYTHONPYCACHEPREFIX"] = PYCACHE_DIR

from core.orchestrator import Orchestrator
from core.event_bus import EventBus
from core.database import create_database
from core.plugin_loader import PluginLoader
from core.monitor import SystemMonitor, PerformanceMonitor
from core.health_check import HealthChecker
from core.logger import log_info, log_success, log_error

shutdown_event = asyncio.Event()

def signal_handler():
    print("\n⚠️ Shutdown signal received, cleaning up...")
    shutdown_event.set()

async def shutdown_cleanup(db, system_monitor, orchestrator):
    log_info("Shutting down...")
    if system_monitor:
        await system_monitor.stop_monitoring()
    if orchestrator:
        await orchestrator.stop()
    if db:
        await db.close()
    log_success("Cleanup completed.")

async def main():
    print("\n" + "="*70)
    print("🚀 Smart Lead Monitoring System - Final Version")
    print("="*70)

    db = create_database()
    await db.init()

    event_bus = EventBus()
    plugin_loader = PluginLoader()

    # ========== تنظیم وابستگی‌های مورد نیاز برای پلاگین‌ها ==========
    # هر پلاگینی که در __init__ خود پارامتری مثل db، scorer، ... داشته باشد،
    # این مقادیر به آن تزریق می‌شود.
    plugin_loader.set_dependencies(db=db, scorer=None)

    from config import MONITOR
    system_monitor = SystemMonitor()
    perf_monitor = PerformanceMonitor()
    health_check = HealthChecker(db)

    if MONITOR.get("enabled", True):
        asyncio.create_task(system_monitor.start_monitoring(
            interval_seconds=MONITOR.get("interval_seconds", 30)
        ))
    await health_check.check_all()

    # ========== بارگذاری خودکار کلکتورها ==========
    collectors = plugin_loader.discover_collectors()
    if not collectors:
        log_error("No collectors found!")
        await shutdown_cleanup(db, system_monitor, None)
        return

    # ثبت کلکتورها برای health-check + اجرای سرورِ observability (/health و /metrics)
    for _c in collectors:
        health_check.register_collector(_c)
    from core.obs_server import start_obs_server
    obs_runner = await start_obs_server(db, health_check, collectors)

    # ========== بارگذاری و ثبت خودکار هندلرها ==========
    handlers = plugin_loader.discover_handlers()
    # لیست رویدادهایی که سیستم پشتیبانی می‌کند
    supported_events = ["message.received", "lead.saved", "error.occurred"]

    for handler in handlers:
        for event_name in supported_events:
            if handler.can_handle(event_name):
                event_bus.subscribe(event_name, handler.handle)
                log_success(f"Handler '{handler.handler_name}' subscribed to '{event_name}'")

    # ========== راه‌اندازی سرویس پشتیبان‌گیری ==========
    from core.backup_service import start_backup_service
    await start_backup_service()

    from core.cleanup_service import start_cleanup_service
    await start_cleanup_service(db)

    from core.daily_reporter import start_daily_reporter
    await start_daily_reporter(db)
    
    orchestrator = Orchestrator(collectors, event_bus, db)
    orchestrator_task = asyncio.create_task(orchestrator.start())

    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        signal_handler()

    orchestrator_task.cancel()
    try:
        await orchestrator_task
    except asyncio.CancelledError:
        pass
    if obs_runner:
        try:
            await obs_runner.cleanup()
        except Exception:
            pass
    await shutdown_cleanup(db, system_monitor, orchestrator)

    print("\n" + "="*70)
    print("📊 Final Monitoring Report (before exit)")
    print("="*70)

    sys_report = system_monitor.get_report()
    print(f"\n🖥️ System Status:")
    print(f"   CPU: {sys_report['system']['cpu'].value if sys_report['system']['cpu'] else 0}%")
    print(f"   RAM: {sys_report['system']['ram'].value if sys_report['system']['ram'] else 0}%")
    print(f"   Disk: {sys_report['system']['disk'].value if sys_report['system']['disk'] else 0}%")
    print(f"   Uptime: {sys_report['system']['uptime']}")

    perf_stats = perf_monitor.get_stats()
    if perf_stats:
        print(f"\n⚡ Module Performance:")
        for name, stats in list(perf_stats.items())[:5]:
            print(f"   {name}: {stats['avg_time']:.2f}s avg ({stats['calls']} calls)")

    perf_monitor.show_slowest_operations()

    if sys_report['alerts']:
        print(f"\n🚨 Recent Alerts:")
        for alert in sys_report['alerts'][-3:]:
            print(f"   {alert.level}: {alert.title} - {alert.message}")

    log_success("Program finished cleanly.")

if __name__ == "__main__":
    if sys.platform == "win32":
        async def main_wrapper():
            try:
                await main()
            except KeyboardInterrupt:
                signal_handler()
        asyncio.run(main_wrapper())
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
        try:
            loop.run_until_complete(main())
        except KeyboardInterrupt:
            signal_handler()