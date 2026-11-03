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

نسخهٔ ۷ (این نسخه):
  - 🐛 FIX مهم: `safe_brain` حالا با `api_key` از `config.AI` ساخته
    می‌شود. این تضمین می‌کند که SafeBrain singleton با کلید کامل
    DeepSeek ساخته شود، نه بعداً که admin_handler صدا می‌زند.
  - 🐛 FIX: `_signal_handler` حالا idempotent است (دوبار اجرا نمی‌شود).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۶.
"""
from __future__ import annotations

import os
import sys
import time
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f'⚠️ {type(exc).__name__}: {exc}', file=sys.stderr)
import asyncio
import platform
import signal
from typing import Any

# ─── تنظیم console قبل از هر print/import ───
from core.kernel.console_compat import patch_console

patch_console(verbose=False)


# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
_BOOT_BANNER_WIDTH = 60
_OBS_CLEANUP_TIMEOUT_S = 10.0
_ORCH_CLEANUP_TIMEOUT_S = 60.0
_HANDLER_STOP_TIMEOUT_S = 5.0

# ═══════════════════════════════════════════
# 🔒 قفل اجرا: فقط venv + فقط یک نمونه
# ═══════════════════════════════════════════
import atexit
import tempfile
from pathlib import Path as _Path

_LOCK_FILE = _Path(tempfile.gettempdir()) / "payesh_bot.lock"
_LOCK_HANDLE = None


def _acquire_single_instance_lock() -> None:
    """قفل فایل — فقط یک نمونه ربات."""
    global _LOCK_HANDLE
    try:
        if os.name == "nt":
            import msvcrt
            _LOCK_HANDLE = open(_LOCK_FILE, "w")
            try:
                msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                # چک کن فایل قفل قدیمی‌ست (zombie از کرش قبلی)
                _try_clear_stale_lock()
                if _LOCK_HANDLE is not None:
                    try:
                        msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
                        return
                    except OSError:
                        pass
                print()
                print("⚠️ ربات از قبل در حال اجراست!")
                print(f"   اگر مطمئنی ربات اجرا نیست، این فایل رو پاک کن:")
                print(f"   {_LOCK_FILE}")
                print()
                if _LOCK_HANDLE is not None:
                    _LOCK_HANDLE.close()
                    _LOCK_HANDLE = None
                sys.exit(2)
        else:
            import fcntl
            _LOCK_HANDLE = open(_LOCK_FILE, "w")
            try:
                fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                _try_clear_stale_lock()
                if _LOCK_HANDLE is not None:
                    try:
                        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return
                    except OSError:
                        pass
                print()
                print("⚠️ ربات از قبل در حال اجراست!")
                print(f"   اگر مطمئنی ربات اجرا نیست، این فایل رو پاک کن:")
                print(f"   {_LOCK_FILE}")
                print()
                if _LOCK_HANDLE is not None:
                    _LOCK_HANDLE.close()
                    _LOCK_HANDLE = None
                sys.exit(2)
    except Exception as e:
        print(f"⚠️ قفل: {type(e).__name__}: {e} (ادامه می‌دم)")


def _try_clear_stale_lock() -> None:
    """اگر فایل قفل قدیمی‌ست (بیش از ۱ ساعت)، پاکش کن."""
    global _LOCK_HANDLE
    try:
        if not _LOCK_FILE.exists():
            return
        age = time.time() - _LOCK_FILE.stat().st_mtime
        if age > 3600:  # ۱ ساعت
            print(f"⚠️ فایل قفل قدیمی ({age/3600:.1f} ساعت) — پاک می‌شه")
            if _LOCK_HANDLE is not None:
                _LOCK_HANDLE.close()
                _LOCK_HANDLE = None
            _LOCK_FILE.unlink(missing_ok=True)
            _LOCK_HANDLE = open(_LOCK_FILE, "w")
    except Exception:
        pass


def _release_lock() -> None:
    """آزادسازی قفل هنگام خروج."""
    global _LOCK_HANDLE
    try:
        if _LOCK_HANDLE is not None:
            _LOCK_HANDLE.close()
            _LOCK_HANDLE = None
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


atexit.register(_release_lock)
_acquire_single_instance_lock()


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
        return True


def _enforce_venv() -> None:
    """بررسی venv — اجباری."""
    if _is_venv():
        return

    is_win = platform.system().lower() == "windows"
    activate_cmd = (
        "venv\\Scripts\\activate"
        if is_win
        else "source venv/bin/activate"
    )

    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    print("❌ این ربات فقط در محیط مجازی (venv) اجرا می‌شود.", file=sys.stderr)
    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    print(file=sys.stderr)
    print("لطفاً ابتدا venv را فعال کنید:", file=sys.stderr)
    print(f"   {activate_cmd}", file=sys.stderr)
    print(file=sys.stderr)
    print("سپس دوباره اجرا کنید.", file=sys.stderr)
    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    sys.exit(1)
    print("سپس دوباره اجرا کنید.", file=sys.stderr)
    print("=" * _BOOT_BANNER_WIDTH, file=sys.stderr)
    sys.exit(1)


_enforce_venv()   # ← این خط


# ═══════════════════════════════════════════
# pycache
# ═══════════════════════════════════════════
from config import (  # noqa: E402
    BRAIN_CONFIG,
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
import contextlib  # noqa: E402

from core.app.orchestrator import Orchestrator  # noqa: E402
from core.app.plugin_loader import PluginLoader  # noqa: E402
from core.database.factory import create_database  # noqa: E402
from core.kernel.event_bus import EventBus  # noqa: E402
from core.kernel.logger import log_debug, log_error, log_info, log_success, log_warning
from core.ops.health_check import HealthChecker  # noqa: E402
from core.ops.monitor import PerformanceMonitor, SystemMonitor  # noqa: E402
from core.services.cleanup_service import start_cleanup_service  # noqa: E402

# ═══════════════════════════════════════════
# shutdown state
# ═══════════════════════════════════════════
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


def _signal_handler(signum=None, frame=None) -> None:  # noqa: ARG001
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

    try:
        ev = _get_shutdown_event()
        ev.set()
    except Exception as e:
        _safe_log_error(
            f"❌ [MAIN:signal] set | {type(e).__name__}: {e}"
        )

    # 🆕 fallback: اگه ظرف ۳۰ ثانیه cleanup تموم نشد،
    #    پروسه‌های chrome مربوط به پروفایل‌های ما رو بکش
    import threading

    def _fallback_kill_chrome():
        threading.Event().wait(30.0)
        import os as _os
        import re as _re
        import subprocess
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='chrome.exe'",
                 "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="ignore",
                check=False,
            ).stdout
        except Exception:
            return

        sessions_dir = _os.path.normcase(
            r"F:\payesh plat\storage\sessions"
        )
        killed = 0
        for line in out.splitlines():
            if "--user-data-dir" not in line:
                continue
            if sessions_dir not in _os.path.normcase(line):
                continue
            m = _re.search(r"(\d+)\s*$", line.strip())
            if not m:
                continue
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", m.group(1), "/T"],
                    capture_output=True, timeout=5,
                    check=False,
                )
                killed += 1
            except Exception as exc:
                log_debug(f'⚠️ {type(exc).__name__}: {exc}')
        if killed:
            with contextlib.suppress(Exception):
                _safe_log_warning(
                    f"🧹 [MAIN:fallback] {killed} پروسه chrome "
                    f"بعد از timeout کشته شد"
                )

    with contextlib.suppress(Exception):
        threading.Thread(
            target=_fallback_kill_chrome,
            daemon=True,
            name="chrome_fallback_killer",
        ).start()


def _safe_log_warning(msg: str) -> None:
    """لاگ warning با fallback به stderr."""
    try:
        log_warning(msg)
    except Exception:
        with contextlib.suppress(Exception):
            print(msg, file=sys.stderr)


def _safe_log_error(msg: str) -> None:
    """لاگ error با fallback به stderr."""
    try:
        log_error(msg)
    except Exception:
        with contextlib.suppress(Exception):
            print(msg, file=sys.stderr)


def _extract_sys_value(
    system_dict: dict, key: str, default: float = 0.0
) -> float:
    """مقدار عددی رو از system_dict استخراج می‌کنه."""
    m = system_dict.get(key)
    if m and hasattr(m, "value"):
        return float(m.value or 0)
    if isinstance(m, dict) and "value" in m:
        return float(m["value"] or 0)
    return default


# ═══════════════════════════════════════════
# shutdown_cleanup
# ═══════════════════════════════════════════
async def _stop_handlers(handlers: list[Any]) -> None:
    """صدا زدن on_stop برای همهٔ handlerها."""
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
    cleanup_svc=None,
) -> None:
    """Cleanup کامل — هر مرحله جدا در try/except."""
    t0 = time.monotonic()
    log_info("🛑 [MAIN:shutdown] شروع cleanup")

    # ۱) system monitor
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
        except (TimeoutError, asyncio.CancelledError) as exc:
            log_debug(f'⚠️ {type(exc).__name__}: {exc}')
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
        except (TimeoutError, asyncio.CancelledError) as exc:
            log_debug(f'⚠️ {type(exc).__name__}: {exc}')
        except Exception as e:
            log_warning(
                f"   ⚠️ orchestrator_task | {type(e).__name__}: {e}"
            )
        log_info("   ✔ orchestrator task cancel شد")

    # ۳) handlers
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

    # ۵.۵) cleanup service
    if cleanup_svc:
        try:
            await cleanup_svc.shutdown()
            log_info("   ✔ cleanup_service متوقف شد")
        except Exception as e:
            log_warning(
                f"   ⚠️ cleanup_svc | {type(e).__name__}: {e}"
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
    handlers: list[Any] = []
    cleanup_svc = None

    try:
        # ─── ۱) DB ───
        t = time.monotonic()
        db = create_database()
        await db.init()
        log_success(
            f"✅ [MAIN:db] آماده | {time.monotonic() - t:.2f}s"
        )

        # ─── ۱.۵) cleanup خودکار ───
        t = time.monotonic()
        cleanup_svc = await start_cleanup_service(db)
        log_success(
            f"✅ [MAIN:cleanup] سرویس راه افتاد | "
            f"{time.monotonic() - t:.2f}s"
        )

        # ─── ۲) EventBus + PluginLoader ───
        event_bus = EventBus()
        plugin_loader = PluginLoader()
        plugin_loader.set_dependencies(db=db, scorer=None)

        # ═══════════════════════════════════════════
        # 🎯 FIX مهم نسخهٔ ۷: SafeBrain با api_key
        # ═══════════════════════════════════════════
        # قبلاً api_key پاس نمی‌شد → اگر SafeBrain.__init__
        # از config.AI نمی‌خواند، singleton بدون کلید ساخته
        # می‌شد و admin_handler که بعداً api_key می‌داد،
        # بی‌اثر می‌شد (چون singleton از قبل ساخته شده).
        #
        # حالا: api_key از config.AI (منبع یکتا) خوانده می‌شود
        # و صریحاً به get_safe_brain پاس داده می‌شود.
        # ═══════════════════════════════════════════
        from config import AI as _AI_CFG_MAIN
        from core.safe_brain import get_safe_brain

        t = time.monotonic()

        _api_key_main: str = (
            (_AI_CFG_MAIN or {}).get("deepseek_api_key")
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or ""
        )

        safe_brain = get_safe_brain(
            api_key=_api_key_main,
            full_access=BRAIN_CONFIG.get("full_access", False),
            use_api_memory=BRAIN_CONFIG.get("use_api_memory", False),
            memory_api_url=BRAIN_CONFIG.get("memory_api_url", ""),
            memory_api_key=BRAIN_CONFIG.get("memory_api_key", ""),
        )
        log_success(
            f"✅ [MAIN:brain] SafeBrain آماده | "
            f"api_key={'✅' if _api_key_main else '❌'} | "
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

        # ─── ۸) handlers — استفاده از AutoSubscriber ───
        t = time.monotonic()
        from handlers.auto_subscribe import AutoSubscriber
        handlers = plugin_loader.discover_handlers()
        auto_sub = AutoSubscriber(event_bus, plugin_loader, handlers=handlers)
        subscribed = await auto_sub.register_all()
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

        # 🌙 Consolidation (خواب مغز)
        try:
            from core.brain.consolidation import start_consolidation
            start_consolidation()
            log_info("✅ [MAIN:consolidation] خواب مغز راه افتاد")
        except Exception as e:
            log_warning(f"⚠️ [MAIN:consolidation] {type(e).__name__}: {e}")

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
            # 🐛 FIX: polling کوتاه — تا signal روی ویندوز به
            # handler برسد و loop گیر نکند.
            while not ev.is_set():
                try:
                    await asyncio.wait_for(
                        ev.wait(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue
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
                handlers=handlers,
                cleanup_svc=cleanup_svc,
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

                log_info(
                    f"   🖥️ CPU: "
                    f"{_extract_sys_value(system_dict, 'cpu'):.1f}% | "
                    f"RAM: "
                    f"{_extract_sys_value(system_dict, 'ram'):.1f}% | "
                    f"Disk: "
                    f"{_extract_sys_value(system_dict, 'disk'):.1f}% | "
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
                        except Exception as exc:
                            log_debug(f'⚠️ {type(exc).__name__}: {exc}')
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
        # 🐛 FIX: signal handler واقعی — تا Ctrl+C مستقیم به
        # asyncio.run نرسد و cleanup فرصت اجرا داشته باشد.
        try:
            signal.signal(signal.SIGINT, _signal_handler)
        except Exception as e:
            log_warning(
                f"⚠️ [MAIN:signal] signal() | "
                f"{type(e).__name__}: {e}"
            )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(main())
        except KeyboardInterrupt:
            _kbd_interrupt_count += 1
            _signal_handler()
            log_info("🛑 [MAIN:win] KeyboardInterrupt")
        except Exception as e:
            log_error(
                f"❌ [MAIN:win] {type(e).__name__}: {e}"
            )
        finally:
            try:
                pending = [
                    t for t in asyncio.all_tasks(loop)
                    if not t.done()
                ]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(
                            *pending, return_exceptions=True
                        )
                    )
            except Exception as exc:
                log_debug(f'⚠️ {type(exc).__name__}: {exc}')
            with contextlib.suppress(Exception):
                loop.close()
            _reset_shutdown_event()

    else:
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
            except Exception as exc:
                log_debug(f'⚠️ {type(exc).__name__}: {exc}')
            with contextlib.suppress(Exception):
                loop.close()
            _reset_shutdown_event()


if __name__ == "__main__":
    _run()
