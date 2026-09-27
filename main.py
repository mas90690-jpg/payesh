"""
main.py — نقطهٔ ورود ربات پایش سرنخ.

این فایل:
  ۱) بررسی venv
  ۲) تنظیم console و pycache
  ۳) ساخت DB + EventBus + PluginLoader
  ۴) راه‌اندازی SafeBrain + SystemMonitor + HealthChecker
  ۵) بارگذاری collectors و handlers
  ۶) راه‌اندازی obs server + self_monitor + orchestrator
  ۷) انتظار برای shutdown signal (Ctrl+C یا SIGTERM)
  ۸) cleanup کامل
  ۹) چاپ گزارش نهایی

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX: shutdown_cleanup حالا on_stop برای handlerها صدا می‌زند
    (قبلاً storage_handler.on_stop اجرا نمی‌شد → taskهای هشدار
    pending باقی می‌ماندند).
  - 🐛 FIX: handlers فقط یک‌بار discover می‌شوند و در main() ذخیره
    می‌شوند (نه در auto_subscribe دوباره).
  - 🎯 لاگ دقیق‌تر برای handler stop.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵.
"""
from __future__ import annotations
import sys, os
if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import asyncio
import os
import platform
import signal
import sys
import time
from typing import Any

# ─── تنظیم console قبل از هر print/import ───
from core.kernel.console_compat import patch_console

patch_console(verbose=False)


# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
_BOOT_BANNER_WIDTH = 60
_OBS_CLEANUP_TIMEOUT_S = 10.0
_ORCH_CLEANUP_TIMEOUT_S = 10.0
_HANDLER_STOP_TIMEOUT_S = 5.0
# _MAX_REPORT_* حذف شد — پایشگر فقط‌خواندنی

# ═══════════════════════════════════════════
# venv check
# ═══════════════════════════════════════════
def _is_venv() -> bool:
    """آیا در محیط مجازی هستیم؟"""
    try:
        return (sys.prefix != sys.base_prefix) or (
            os.environ.get("VIRTUAL_ENV") is not None
        )
    except Exception:
        return True  # امن: فرض کن venv هست


def _should_skip_venv_check() -> bool:
    """
    آیا بررسی venv نادیده گرفته شود؟

    🐛 FIX: حالا config.MAIN_CONFIG را چک می‌کند (hot-reload).
    (با fallback به os.environ برای امنیت بیشتر.)
    """
    # ─── اول از config (hot-reload) ───
    try:
        from config import MAIN_CONFIG
        if MAIN_CONFIG.get("skip_venv_check", False):
            return True
    except Exception:
        pass

    # ─── fallback: env مستقیم ───
    raw = os.environ.get("MAIN_SKIP_VENV_CHECK", "").strip().lower()
    return raw in ("true", "1", "yes", "on")


def _enforce_venv() -> None:
    """بررسی venv — با امکان override از env."""
    if _is_venv():
        return

    # 🐛 FIX: از config می‌خوانیم (نه os.environ)
    if _should_skip_venv_check():
        print(
            "⚠️ [MAIN:venv] اجرا بدون venv "
            "(MAIN_SKIP_VENV_CHECK=true)",
            file=sys.stderr,
        )
        return

    # پیام cross-platform
    is_win = platform.system().lower() == "windows"
    activate_cmd = (
        "venv\\Scripts\\activate"
        if is_win
        else "source venv/bin/activate"
    )

    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    print(
        "❌ این ربات فقط در محیط مجازی (venv) اجرا می‌شود.",
        file=sys.stderr,
    )
    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    print(file=sys.stderr)
    print("لطفاً ابتدا venv را فعال کنید:", file=sys.stderr)
    print(f"   {activate_cmd}", file=sys.stderr)
    print(file=sys.stderr)
    print("سپس دوباره اجرا کنید.", file=sys.stderr)
    print(
        "(یا با MAIN_SKIP_VENV_CHECK=true این بررسی را رد کنید)",
        file=sys.stderr,
    )
    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    sys.exit(1)


_enforce_venv()


# ═══════════════════════════════════════════
# pycache
# ═══════════════════════════════════════════
from config import (
    BRAIN_CONFIG,
    MAIN_CONFIG,
    MONITOR,
    PYCACHE_DIR,
)

try:
    os.makedirs(PYCACHE_DIR, exist_ok=True)
    sys.pycache_prefix = PYCACHE_DIR
except Exception as e:
    print(
        f"⚠️ [MAIN:pycache] {type(e).__name__}: {e}",
        file=sys.stderr,
    )


# ═══════════════════════════════════════════
# imports اصلی
# ═══════════════════════════════════════════
from core.app.orchestrator import Orchestrator
from core.app.plugin_loader import PluginLoader
from core.database.factory import create_database
from core.kernel.event_bus import EventBus
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.ops.health_check import HealthChecker
from core.ops.monitor import PerformanceMonitor, SystemMonitor

# ═══════════════════════════════════════════
# shutdown state
# ═══════════════════════════════════════════
# NOTE: shutdown_event در main() ساخته می‌شود (نه در import)
# تا با loop فعلی هم‌خوان باشد.
_shutdown_event: asyncio.Event | None = None
_shutdown_triggered = False
_kbd_interrupt_count = 0


def _get_shutdown_event() -> asyncio.Event:
    """shutdown_event را lazy می‌سازد (به loop فعلی گره‌خورده)."""
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def _reset_shutdown_event() -> None:
    """پاک‌سازی shutdown_event (برای تست‌ها)."""
    global _shutdown_event, _shutdown_triggered
    _shutdown_event = None
    _shutdown_triggered = False


def _signal_handler(signum=None, frame=None) -> None:
    """Signal handler — idempotent (دوبار اجرا نمی‌شود)."""
    global _shutdown_triggered, _kbd_interrupt_count

    # اگر دوباره صدا زده شد → کاربر عجله دارد
    if _shutdown_triggered:
        _kbd_interrupt_count += 1
        if _kbd_interrupt_count == 2:
            _safe_log_warning(
                "⚠️ [MAIN:signal] Ctrl+C دوباره — تلاش برای خروج سریع"
            )
        return

    _shutdown_triggered = True
    try:
        sig_name = (
            signal.Signals(signum).name
            if signum
            else "KeyboardInterrupt"
        )
    except Exception:
        sig_name = str(signum) if signum else "KeyboardInterrupt"

    _safe_log_warning(f"⚠️ [MAIN:signal] {sig_name} — شروع shutdown")

    # 🐛 FIX: مطمئن شو event موجود است (حتی قبل از logger init)
    try:
        ev = _get_shutdown_event()
        ev.set()
    except Exception as e:
        _safe_log_error(
            f"❌ [MAIN:signal] set | {type(e).__name__}: {e}"
        )


def _safe_log_warning(msg: str) -> None:
    """
    🐛 FIX: لاگ warning با fallback به stderr.

    اگر لاگر هنوز init نشده باشد (early init)، به stderr می‌نویسد.
    """
    try:
        log_warning(msg)
    except Exception:
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass


def _safe_log_error(msg: str) -> None:
    """لاگ error با fallback به stderr."""
    try:
        log_error(msg)
    except Exception:
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass


# ═══════════════════════════════════════════
# shutdown_cleanup
# ═══════════════════════════════════════════
async def _stop_handlers(handlers: list[Any]) -> None:
    """
    🐛 FIX: صدا زدن on_stop برای همهٔ handlerها.

    چرا مهم است؟
      storage_handler.on_stop برای cancel taskهای هشدار تعریف شده.
      قبلاً صدا زده نمی‌شد → taskهای pending باقی می‌ماندند.
    """
    if not handlers:
        return

    t0 = time.monotonic()
    stopped = 0
    failed = 0

    for handler in handlers:
        name = getattr(handler, "handler_name", "?")
        try:
            on_stop = getattr(handler, "on_stop", None)
            if on_stop is None:
                continue
            # on_stop ممکن است async یا sync باشد
            result = on_stop()
            if asyncio.iscoroutine(result):
                await asyncio.wait_for(
                    result, timeout=_HANDLER_STOP_TIMEOUT_S
                )
            stopped += 1
        except TimeoutError:
            failed += 1
            log_warning(
                f"   ⏰ handler.on_stop timeout | {name} | "
                f"{_HANDLER_STOP_TIMEOUT_S}s"
            )
        except Exception as e:
            failed += 1
            log_warning(
                f"   ⚠️ handler.on_stop | {name} | "
                f"{type(e).__name__}: {e}"
            )

    elapsed_ms = (time.monotonic() - t0) * 1000
    if stopped or failed:
        log_info(
            f"   ✔ handlers on_stop | stopped={stopped} "
            f"failed={failed} | {elapsed_ms:.0f}ms"
        )


async def shutdown_cleanup(
    db=None,
    system_monitor: SystemMonitor | None = None,
    orchestrator: Orchestrator | None = None,
    monitor_task: asyncio.Task | None = None,
    orchestrator_task: asyncio.Task | None = None,
    obs_runner=None,
    safe_brain=None,
    handlers: list[Any] | None = None,
) -> None:
    """Cleanup کامل — هر مرحله جدا در try/except."""
    t0 = time.monotonic()
    log_info("🛑 [MAIN:shutdown] شروع cleanup")

    # ۱) system monitor — هم stop_monitoring هم cancel task
    if system_monitor:
        try:
            await system_monitor.stop_monitoring()
            log_info("   ✔ system_monitor متوقف شد")
        except Exception as e:
            log_warning(
                f"   ⚠️ system_monitor | {type(e).__name__}: {e}"
            )

    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await asyncio.wait_for(monitor_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:
            log_warning(
                f"   ⚠️ monitor_task | {type(e).__name__}: {e}"
            )
        log_info("   ✔ monitor task cancel شد")

    # ۲) orchestrator stop + cancel task
    if orchestrator:
        try:
            await asyncio.wait_for(
                orchestrator.stop(),
                timeout=_ORCH_CLEANUP_TIMEOUT_S,
            )
            log_info("   ✔ orchestrator متوقف شد")
        except TimeoutError:
            log_warning(
                f"   ⏰ orchestrator timeout "
                f"({_ORCH_CLEANUP_TIMEOUT_S}s)"
            )
        except Exception as e:
            log_warning(
                f"   ⚠️ orchestrator | {type(e).__name__}: {e}"
            )

    if orchestrator_task and not orchestrator_task.done():
        orchestrator_task.cancel()
        try:
            await asyncio.wait_for(orchestrator_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:
            log_warning(
                f"   ⚠️ orchestrator_task | {type(e).__name__}: {e}"
            )
        log_info("   ✔ orchestrator task cancel شد")

    # ۳) handlers — 🐛 FIX: صدا زدن on_stop
    if handlers:
        try:
            await _stop_handlers(handlers)
        except Exception as e:
            log_warning(
                f"   ⚠️ handlers stop | {type(e).__name__}: {e}"
            )

    # ۴) obs runner
    if obs_runner:
        try:
            await asyncio.wait_for(
                obs_runner.cleanup(),
                timeout=_OBS_CLEANUP_TIMEOUT_S,
            )
            log_info("   ✔ obs_runner تمیز شد")
        except TimeoutError:
            log_warning(
                f"   ⏰ obs_runner timeout "
                f"({_OBS_CLEANUP_TIMEOUT_S}s)"
            )
        except Exception as e:
            log_warning(
                f"   ⚠️ obs_runner | {type(e).__name__}: {e}"
            )

    # ۵) SafeBrain.flush
    if safe_brain:
        try:
            safe_brain.flush()
            log_info("   ✔ SafeBrain flush شد")
        except Exception as e:
            log_warning(
                f"   ⚠️ SafeBrain flush | {type(e).__name__}: {e}"
            )

    # ۶) db — فقط یک‌بار، این‌جا
    if db:
        try:
            await db.close()
            log_info("   ✔ db بسته شد")
        except Exception as e:
            log_warning(f"   ⚠️ db | {type(e).__name__}: {e}")

    elapsed_ms = (time.monotonic() - t0) * 1000
    log_success(
        f"✅ [MAIN:shutdown] cleanup کامل | {elapsed_ms:.0f}ms"
    )


# _safe_read_report حذف شد — پایشگر فقط‌خواندنی

# _wait_for_bale_sender حذف شد — پایشگر فقط‌خواندنی

# _notify_restart_report حذف شد — پایشگر فقط‌خواندنی


# ═══════════════════════════════════════════
# main
# ═══════════════════════════════════════════
async def main() -> None:
    t_boot = time.monotonic()
    log_success("=" * _BOOT_BANNER_WIDTH)
    log_success("🚀 [MAIN:boot] Smart Lead Monitoring System")
    log_success("=" * _BOOT_BANNER_WIDTH)

    # ─── متغیرهای cleanup ───
    db = None
    system_monitor: SystemMonitor | None = None
    orchestrator: Orchestrator | None = None
    monitor_task: asyncio.Task | None = None
    orchestrator_task: asyncio.Task | None = None
    obs_runner = None
    perf_monitor: PerformanceMonitor | None = None
    safe_brain = None
    handlers: list[Any] = []  # 🐛 FIX: ذخیره برای on_stop

    try:
        # ─── ۱) DB ───
        t = time.monotonic()
        db = create_database()
        await db.init()
        log_success(
            f"✅ [MAIN:db] آماده | {time.monotonic() - t:.2f}s"
        )

        # ─── ۲) EventBus + PluginLoader ───
        event_bus = EventBus()
        plugin_loader = PluginLoader()
        plugin_loader.set_dependencies(db=db, scorer=None)

        # ─── ۳) SafeBrain ───
        from core.safe_brain import get_safe_brain
        t = time.monotonic()
        safe_brain = get_safe_brain(
            full_access=BRAIN_CONFIG.get("full_access", False),
            use_api_memory=BRAIN_CONFIG.get("use_api_memory", False),
            memory_api_url=BRAIN_CONFIG.get("memory_api_url", ""),
            memory_api_key=BRAIN_CONFIG.get("memory_api_key", ""),
        )
        log_success(
            f"✅ [MAIN:brain] SafeBrain آماده | "
            f"{time.monotonic() - t:.2f}s"
        )

        # ─── ۴) SystemMonitor + Perf + HealthChecker ───
        system_monitor = SystemMonitor()
        perf_monitor = PerformanceMonitor()
        boot_start = perf_monitor.start_operation("main", "boot_total")

        health_check = HealthChecker(db)

        if MONITOR.get("enabled", True):
            interval = MONITOR.get("interval_seconds", 30)
            monitor_task = asyncio.create_task(
                system_monitor.start_monitoring(
                    interval_seconds=interval
                ),
                name="system_monitor",
            )
            log_info(
                f"✅ [MAIN:monitor] SystemMonitor راه افتاد | "
                f"interval={interval}s"
            )

        # ─── ۵) collectors ───
        t = time.monotonic()
        collectors = plugin_loader.discover_collectors()
        if not collectors:
            log_error("❌ [MAIN:collectors] هیچ کلکتوری پیدا نشد!")
            return
        log_success(
            f"✅ [MAIN:collectors] {len(collectors)} کلکتور | "
            f"names={[c.platform_name for c in collectors]} | "
            f"{time.monotonic() - t:.2f}s"
        )

        for c in collectors:
            health_check.register_collector(c)

        # ─── ۶) obs server ───
        t = time.monotonic()
        from core.ops.obs_server import start_obs_server
        obs_runner = await start_obs_server(db, health_check, collectors)
        if obs_runner:
            log_success(
                f"✅ [MAIN:obs] server راه افتاد | "
                f"{time.monotonic() - t:.2f}s"
            )
        else:
            log_warning("⚠️ [MAIN:obs] راه نیافتاد")

        # ─── ۷) self monitor ───
        try:
            from core.ops.self_monitor import start_self_monitor
            await start_self_monitor(db)
        except Exception as e:
            log_warning(
                f"⚠️ [MAIN:self_monitor] {type(e).__name__}: {e}"
            )

        # ─── ۸) handlers — 🐛 FIX: کش کردن نتیجه
        t = time.monotonic()
        handlers = plugin_loader.discover_handlers()
        supported_events = [
            "message.received", "lead.saved", "error.occurred",
        ]
        subscribed = 0
        for handler in handlers:
            for event_name in supported_events:
                try:
                    if handler.can_handle(event_name):
                        event_bus.subscribe(
                            event_name, handler.handle
                        )
                        subscribed += 1
                except Exception as e:
                    log_warning(
                        f"⚠️ [MAIN:handlers] "
                        f"{handler.handler_name} → {event_name} | "
                        f"{type(e).__name__}: {e}"
                    )
        log_success(
            f"✅ [MAIN:handlers] {len(handlers)} هندلر | "
            f"subscribed={subscribed} | "
            f"{time.monotonic() - t:.2f}s"
        )

        # ─── ۹) orchestrator ───
        t = time.monotonic()
        orchestrator = Orchestrator(collectors, event_bus, db)
        orchestrator_task = asyncio.create_task(
            orchestrator.start(), name="orchestrator"
        )
        log_success(
            f"✅ [MAIN:orchestrator] شروع شد | "
            f"{time.monotonic() - t:.2f}s"
        )

        # ═══════════════════════════════════════
        # گزارش ری‌استارت — حذف شد (پایشگر فقط‌خواندنی)
        # ═══════════════════════════════════════
        log_info("ℹ️ [MAIN:report] گزارش ری‌استارت غیرفعال است")

        perf_monitor.end_operation("main", "boot_total", boot_start)
        total_boot_s = time.monotonic() - t_boot
        log_success(
            f"🎯 [MAIN:boot] کامل | total={total_boot_s:.2f}s"
        )
        log_info(
            "🔄 [MAIN:run] در حالت انتظار shutdown signal..."
        )

        # ═══════════════════════════════════════
        # انتظار shutdown
        # ═══════════════════════════════════════
        ev = _get_shutdown_event()
        try:
            await ev.wait()
        except KeyboardInterrupt:
            _signal_handler()
        except asyncio.CancelledError:
            log_info("🛑 [MAIN:run] main task cancel شد")

    except Exception as e:
        log_error(f"❌ [MAIN:main] {type(e).__name__}: {e}")
        import traceback
        for ln in traceback.format_exc().splitlines()[-5:]:
            log_error(f"   ↳ {ln}")

    finally:
        # ─── cleanup ───
        log_info("🛑 [MAIN:cleanup] شروع")
        try:
            await shutdown_cleanup(
                db=db,
                system_monitor=system_monitor,
                orchestrator=orchestrator,
                monitor_task=monitor_task,
                orchestrator_task=orchestrator_task,
                obs_runner=obs_runner,
                safe_brain=safe_brain,
                handlers=handlers,  # 🐛 FIX
            )
        except Exception as e:
            log_error(
                f"❌ [MAIN:cleanup] {type(e).__name__}: {e}"
            )

        # ─── گزارش نهایی ───
        log_success("=" * _BOOT_BANNER_WIDTH)
        log_success("📊 [MAIN:final] گزارش پایانی")
        log_success("=" * _BOOT_BANNER_WIDTH)

        if system_monitor:
            try:
                sys_report = system_monitor.get_report()
                system_dict = sys_report.get("system", {}) or {}

                def _val(
                    key: str, default: float = 0.0
                ) -> float:
                    m = system_dict.get(key)
                    if m and hasattr(m, "value"):
                        return float(m.value or 0)
                    if isinstance(m, dict) and "value" in m:
                        return float(m["value"] or 0)
                    return default

                log_info(
                    f"   🖥️ CPU: {_val('cpu'):.1f}% | "
                    f"RAM: {_val('ram'):.1f}% | "
                    f"Disk: {_val('disk'):.1f}% | "
                    f"Uptime: "
                    f"{system_dict.get('uptime_str', '?')}"
                )

                alerts = sys_report.get("alerts", [])
                if alerts:
                    log_warning(
                        f"   🚨 {len(alerts)} هشدار (آخرین ۳):"
                    )
                    for alert in alerts[-3:]:
                        try:
                            lvl = (
                                alert.level
                                if hasattr(alert, "level")
                                else alert.get("level", "?")
                            )
                            title = (
                                alert.title
                                if hasattr(alert, "title")
                                else alert.get("title", "?")
                            )
                            log_warning(f"      [{lvl}] {title}")
                        except Exception:
                            pass
            except Exception as e:
                log_warning(
                    f"⚠️ [MAIN:final] sys_report | "
                    f"{type(e).__name__}: {e}"
                )

        if perf_monitor:
            try:
                perf_stats = perf_monitor.get_stats()
                if perf_stats:
                    log_info("   ⚡ ماژول‌های فعال:")
                    for name, stats in list(perf_stats.items())[:5]:
                        avg = stats.get("avg_time", 0)
                        calls = stats.get("calls", 0)
                        log_info(
                            f"      {name}: {avg:.2f}s avg "
                            f"({calls} calls)"
                        )
                perf_monitor.show_slowest_operations(limit=3)
            except Exception as e:
                log_warning(
                    f"⚠️ [MAIN:final] perf | "
                    f"{type(e).__name__}: {e}"
                )

        log_success(
            "✅ [MAIN:final] برنامه به‌طور تمیز پایان یافت."
        )


# ═══════════════════════════════════════════
# runner
# ═══════════════════════════════════════════
def _run() -> None:
    """راه‌انداز — تفاوت Windows/Linux در signal handling."""
    global _kbd_interrupt_count

    if sys.platform == "win32":
        # Windows: SIGTERM پشتیبانی نمی‌شود در asyncio.
        # فقط Ctrl+C (KeyboardInterrupt) را می‌گیریم.
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            _kbd_interrupt_count += 1
            _signal_handler()
            log_info("🛑 [MAIN:win] KeyboardInterrupt")
        except Exception as e:
            log_error(
                f"❌ [MAIN:win] {type(e).__name__}: {e}"
            )
        finally:
            # 🎯 FIX: پاک‌سازی shutdown_event (مثل مسیر Linux)
            _reset_shutdown_event()
    else:
        # Linux/macOS: SIGINT + SIGTERM
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, lambda s=sig: _signal_handler(s)
                )
            except (NotImplementedError, RuntimeError) as e:
                log_warning(
                    f"⚠️ [MAIN:signal] "
                    f"add_signal_handler({sig}) | "
                    f"{type(e).__name__}: {e}"
                )

        try:
            loop.run_until_complete(main())
        except KeyboardInterrupt:
            _kbd_interrupt_count += 1
            _signal_handler()
        finally:
            # cancel همه taskهای باقی‌مانده
            try:
                pending = [
                    t for t in asyncio.all_tasks(loop)
                    if not t.done()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(
                            *pending, return_exceptions=True
                        )
                    )
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            # 🎯 FIX: پاک‌سازی shutdown_event
            _reset_shutdown_event()


if __name__ == "__main__":
    _run()
