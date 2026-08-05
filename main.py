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
    return (sys.prefix != sys.base_prefix) or (
        os.environ.get("VIRTUAL_ENV") is not None
    )


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
from core.logger import log_info, log_success, log_error, log_warning

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
    print("\n" + "=" * 70)
    print("🚀 Smart Lead Monitoring System - Final Version")
    print("=" * 70)

    db = create_database()
    await db.init()

    event_bus = EventBus()
    plugin_loader = PluginLoader()

    # ========== تنظیم وابستگی‌های مورد نیاز برای پلاگین‌ها ==========
    plugin_loader.set_dependencies(db=db, scorer=None)

    # ========== راه‌اندازی مغز هوشمند ==========
    from core.safe_brain import get_safe_brain
    from config import BRAIN_CONFIG

    get_safe_brain(
        full_access=BRAIN_CONFIG.get("full_access", False),
        use_api_memory=BRAIN_CONFIG.get("use_api_memory", False),
        memory_api_url=BRAIN_CONFIG.get("memory_api_url", ""),
        memory_api_key=BRAIN_CONFIG.get("memory_api_key", ""),
    )
    log_success("🧠 مغز هوشمند (SafeBrain) با تنظیمات جدید راه‌اندازی شد")

    from config import MONITOR

    system_monitor = SystemMonitor()
    perf_monitor = PerformanceMonitor()
    health_check = HealthChecker(db)

    if MONITOR.get("enabled", True):
        asyncio.create_task(
            system_monitor.start_monitoring(
                interval_seconds=MONITOR.get("interval_seconds", 30)
            )
        )
    await health_check.check_all()

    # ========== بارگذاری خودکار کلکتورها ==========
    collectors = plugin_loader.discover_collectors()
    if not collectors:
        log_error("No collectors found!")
        await shutdown_cleanup(db, system_monitor, None)
        return

    for _c in collectors:
        health_check.register_collector(_c)
    from core.obs_server import start_obs_server

    obs_runner = await start_obs_server(db, health_check, collectors)

    # پایشگرِ خودکار
    from core.self_monitor import start_self_monitor

    await start_self_monitor(db)

    # ========== بارگذاری و ثبت خودکار هندلرها ==========
    handlers = plugin_loader.discover_handlers()
    supported_events = ["message.received", "lead.saved", "error.occurred"]

    for handler in handlers:
        for event_name in supported_events:
            if handler.can_handle(event_name):
                event_bus.subscribe(event_name, handler.handle)
                log_success(
                    f"Handler '{handler.handler_name}' subscribed to '{event_name}'"
                )

    # ========== راه‌اندازی Orchestrator ==========
    orchestrator = Orchestrator(collectors, event_bus, db)
    orchestrator_task = asyncio.create_task(orchestrator.start())

    # ========== منتظر اتصال بله و ارسال گزارش ==========
    # صبر می‌کنیم تا بله متصل شود
    await asyncio.sleep(10)  # ۱۰ ثانیه صبر برای اطمینان از اتصال
    
    try:
        from core.daily_reporter import generate_executive_report
        from core import messenger_registry
        
        report_path = generate_executive_report()
        with open(report_path, 'r', encoding='utf-8') as f:
            report_text = f.read()
        
        # بررسی اینکه sender ثبت شده باشد
        if messenger_registry.get_sender("bale"):
            await messenger_registry.notify_admins(
                f"🔄 **ربات ری‌استارت شد!**\n\n"
                f"📊 **گزارش خلاصه:**\n{report_text[:2000]}"
            )
            log_success("✅ گزارش ری‌استارت به مدیر ارسال شد")
        else:
            log_warning("⚠️ سندر بله ثبت نشده، گزارش ارسال نشد")
    except Exception as e:
        log_error(f"⚠️ ارسال گزارش ری‌استارت失敗: {e}")

    # ========== منتظر سیگنال خاموشی ==========
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

    print("\n" + "=" * 70)
    print("📊 Final Monitoring Report (before exit)")
    print("=" * 70)

    sys_report = system_monitor.get_report()
    print(f"\n🖥️ System Status:")
    print(
        f"   CPU: {sys_report['system']['cpu'].value if sys_report['system']['cpu'] else 0}%"
    )
    print(
        f"   RAM: {sys_report['system']['ram'].value if sys_report['system']['ram'] else 0}%"
    )
    print(
        f"   Disk: {sys_report['system']['disk'].value if sys_report['system']['disk'] else 0}%"
    )
    print(f"   Uptime: {sys_report['system']['uptime']}")

    perf_stats = perf_monitor.get_stats()
    if perf_stats:
        print(f"\n⚡ Module Performance:")
        for name, stats in list(perf_stats.items())[:5]:
            print(f"   {name}: {stats['avg_time']:.2f}s avg ({stats['calls']} calls)")

    perf_monitor.show_slowest_operations()

    if sys_report["alerts"]:
        print(f"\n🚨 Recent Alerts:")
        for alert in sys_report["alerts"][-3:]:
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