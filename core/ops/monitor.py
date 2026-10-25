"""core/ops/monitor.py — مانیتورینگ منابع سیستم + عملکرد ماژول‌ها.

دو کلاس:
  1) SystemMonitor      → CPU/RAM/Disk/Network/Process + هشدار
  2) PerformanceMonitor → اندازه‌گیری زمان اجرای عملیات

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX: حذف `HealthStatus` از `__all__` (کلاس تعریف‌نشده بود).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psutil

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _safe_float(value: Any, default: float) -> float:
    """🎯 FIX: تبدیل امن به float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_METRICS_MAX = _env_int(
    "MONITOR_METRICS_MAX", 1000,
    min_val=10, max_val=100_000,
)
_ALERTS_MAX = _env_int(
    "MONITOR_ALERTS_MAX", 100,
    min_val=10, max_val=10_000,
)
_STATUS_EVERY_N_CYCLES = _env_int(
    "MONITOR_STATUS_EVERY_N", 12,
    min_val=1, max_val=1000,
)
_ALERT_COOLDOWN_SECONDS = _env_int(
    "MONITOR_ALERT_COOLDOWN_S", 300,
    min_val=10, max_val=86_400,
)
_SLOW_OP_SECONDS = _env_float(
    "MONITOR_SLOW_OP_S", 5.0,
    min_val=0.1, max_val=600.0,
)
_LOG_PREVIEW_LEN = _env_int(
    "MONITOR_LOG_PREVIEW", 60,
    min_val=20, max_val=500,
)

# ─── سقف cycles برای لاگ کندی ───
_SLOW_COLLECT_MS = _env_int(
    "MONITOR_SLOW_COLLECT_MS", 1000,
    min_val=100, max_val=60_000,
)


# ═══════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════
@dataclass
class Metric:
    """یک metric."""

    name: str
    value: float
    unit: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": (
                round(self.value, 3)
                if isinstance(self.value, float)
                else self.value
            ),
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Metric {self.name}={self.value}{self.unit}>"


_VALID_ALERT_LEVELS = frozenset({
    "critical", "warning", "info",
})


@dataclass
class Alert:
    """یک هشدار."""

    level: str
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """🎯 FIX: validate level."""
        if self.level not in _VALID_ALERT_LEVELS:
            self.level = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Alert [{self.level}] {self.title}>"


# ═══════════════════════════════════════════
# SystemMonitor
# ═══════════════════════════════════════════
class SystemMonitor:
    """مانیتورینگ منابع سیستم با هشدارهای آستانه‌ای + cooldown."""

    def __init__(self):
        self.start_time = datetime.now()  # noqa: DTZ005
        self.metrics_history: list[Metric] = []
        self.alerts: list[Alert] = []
        self._monitoring = False
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

        # 🎯 FIX: lock برای alert cooldowns
        self._alert_cooldowns: dict[str, float] = {}
        self._alert_lock = threading.RLock()

        log_info(
            f"🔧 [SYSMON:init] آماده | "
            f"cooldown={_ALERT_COOLDOWN_SECONDS}s | "
            f"status_every={_STATUS_EVERY_N_CYCLES} cycles | "
            f"metrics_max={_METRICS_MAX}"
        )

    # ═══════════════════════════════════════════
    # حلقهٔ اصلی
    # ═══════════════════════════════════════════
    async def start_monitoring(
        self, interval_seconds: int = 5
    ):
        """شروع حلقهٔ پایش — بلاک می‌شود تا stop."""
        if self._monitoring:
            log_warning(
                "⚠️ [SYSMON:start] قبلاً در حال اجراست"
            )
            return

        self._monitoring = True
        self._stop_event = asyncio.Event()

        try:
            interval = max(1, int(interval_seconds))
        except (TypeError, ValueError):
            interval = 5

        log_success(
            f"✅ [SYSMON:start] شروع | interval={interval}s"
        )

        cycle = 0
        while self._monitoring:
            cycle += 1
            t0 = time.monotonic()
            try:
                await self.collect_metrics()
                await self.check_alerts()

                elapsed_ms = (
                    time.monotonic() - t0
                ) * 1000
                log_info(
                    f"✔️ [SYSMON:cycle] #{cycle} | "
                    f"{elapsed_ms:.0f}ms | "
                    f"metrics_total="
                    f"{len(self.metrics_history)} "
                    f"alerts_total={len(self.alerts)}"
                )

                if cycle % _STATUS_EVERY_N_CYCLES == 0:
                    await self.show_status()

            except asyncio.CancelledError:
                log_info(
                    f"🛑 [SYSMON:cycle] cancel "
                    f"(cycle#{cycle})"
                )
                raise
            except Exception as e:
                log_error(
                    f"❌ [SYSMON:cycle] #{cycle} | "
                    f"{type(e).__name__}: {e}"
                )

            # 🎯 FIX: بیداری سریع در stop
            if self._stop_event is not None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=interval,
                    )
                    break  # stop شد
                except TimeoutError:
                    pass
            else:
                await asyncio.sleep(interval)

        log_info(
            f"🛑 [SYSMON:start] حلقه متوقف | "
            f"total_cycles={cycle}"
        )

    # ═══════════════════════════════════════════
    # collect_metrics
    # ═══════════════════════════════════════════
    async def collect_metrics(self):
        """جمع‌آوری همه metricها."""
        t0 = time.monotonic()

        # ─── CPU ───
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            self._add_metric(
                "cpu_usage", cpu_percent, "%"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] cpu | "
                f"{type(e).__name__}: {e}"
            )

        # ─── RAM ───
        try:
            mem = psutil.virtual_memory()
            self._add_metric("ram_usage", mem.percent, "%")
            self._add_metric(
                "ram_used", mem.used / (1024**3), "GB"
            )
            self._add_metric(
                "ram_available",
                mem.available / (1024**3),
                "GB",
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] ram | "
                f"{type(e).__name__}: {e}"
            )

        # ─── Disk ───
        try:
            disk_path = "C:\\" if os.name == "nt" else "/"
            disk = psutil.disk_usage(disk_path)
            self._add_metric(
                "disk_usage", disk.percent, "%"
            )
            self._add_metric(
                "disk_used", disk.used / (1024**3), "GB"
            )
            self._add_metric(
                "disk_free", disk.free / (1024**3), "GB"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] disk | "
                f"{type(e).__name__}: {e}"
            )

        # ─── Network ───
        try:
            net_io = psutil.net_io_counters()
            self._add_metric(
                "network_sent",
                net_io.bytes_sent / (1024**2),
                "MB",
            )
            self._add_metric(
                "network_recv",
                net_io.bytes_recv / (1024**2),
                "MB",
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] net | "
                f"{type(e).__name__}: {e}"
            )

        # ─── Process ───
        try:
            proc = psutil.Process()
            self._add_metric(
                "process_cpu", proc.cpu_percent(), "%"
            )
            self._add_metric(
                "process_memory",
                proc.memory_info().rss / (1024**2),
                "MB",
            )
            self._add_metric(
                "process_threads",
                proc.num_threads(),
                "count",
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] process | "
                f"{type(e).__name__}: {e}"
            )

        # ─── Uptime ───
        try:
            uptime = (
                datetime.now() - self.start_time  # noqa: DTZ005
            ).total_seconds()
            self._add_metric(
                "uptime", uptime, "seconds"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] uptime | "
                f"{type(e).__name__}: {e}"
            )

        # ─── Temperature (اختیاری) ───
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for sensor, entries in temps.items():
                    for entry in entries:
                        self._add_metric(
                            f"temp_{sensor}",
                            entry.current,
                            "°C",
                        )
        except (
            AttributeError,
            NotImplementedError,
        ):
            pass
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] temps | "
                f"{type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_COLLECT_MS:
            log_warning(
                f"🐌 [SYSMON:collect] کند | "
                f"{elapsed_ms:.0f}ms"
            )

    def _add_metric(
        self, name: str, value: Any, unit: str
    ):
        """🎯 FIX: type-check value."""
        try:
            # type-check
            if value is None:
                return
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                return

            self.metrics_history.append(
                Metric(name, fvalue, unit)
            )
            if len(self.metrics_history) > _METRICS_MAX:
                self.metrics_history = (
                    self.metrics_history[-_METRICS_MAX:]
                )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:metric] {name} | "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # check_alerts
    # ═══════════════════════════════════════════
    async def check_alerts(self):
        """بررسی آستانه‌ها + cooldown."""
        # 🎯 FIX: مقادیر پیش‌فرض منطقی
        cpu_th = 95.0
        ram_th = 92.0
        disk_th = 92.0

        try:
            from config import MONITOR
            if isinstance(MONITOR, dict):
                cpu_th = _safe_float(
                    MONITOR.get("cpu_alert"), cpu_th
                )
                ram_th = _safe_float(
                    MONITOR.get("ram_alert"), ram_th
                )
                disk_th = _safe_float(
                    MONITOR.get("disk_alert"), disk_th
                )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:thresholds] "
                f"{type(e).__name__}: {e} — "
                f"پیش‌فرض [{cpu_th}, {ram_th}, {disk_th}]"
            )

        # ─── CPU ───
        cpu = self.get_current_metric("cpu_usage")
        if cpu and cpu.value > cpu_th:
            self._add_alert_with_cooldown(
                key="cpu_high",
                level="critical",
                title="High CPU Usage",
                message=(
                    f"CPU: {cpu.value:.1f}% "
                    f"(threshold {cpu_th}%)"
                ),
            )

        # ─── RAM ───
        ram = self.get_current_metric("ram_usage")
        if ram and ram.value > ram_th:
            self._add_alert_with_cooldown(
                key="ram_high",
                level="critical",
                title="High RAM Usage",
                message=(
                    f"RAM: {ram.value:.1f}% "
                    f"(threshold {ram_th}%)"
                ),
            )

        # ─── Disk ───
        disk = self.get_current_metric("disk_usage")
        if disk and disk.value > disk_th:
            self._add_alert_with_cooldown(
                key="disk_high",
                level="critical",
                title="Disk Space Critical",
                message=(
                    f"Disk: {disk.value:.1f}% "
                    f"(threshold {disk_th}%)"
                ),
            )

    # ═══════════════════════════════════════════
    # alert با cooldown
    # ═══════════════════════════════════════════
    def _add_alert_with_cooldown(
        self,
        key: str,
        level: str,
        title: str,
        message: str,
    ):
        """alert با cooldown (key خالی → بدون cooldown)."""
        # 🎯 FIX: چک key خالی
        if not key:
            log_warning(
                f"⚠️ [SYSMON:alert] کلید خالی — "
                f"بدون cooldown | {title}"
            )
            self._add_alert(level, title, message)
            return

        now = time.monotonic()

        # 🎯 FIX: lock
        with self._alert_lock:
            last = self._alert_cooldowns.get(key, 0)
            if now - last < _ALERT_COOLDOWN_SECONDS:
                remaining = (
                    _ALERT_COOLDOWN_SECONDS - (now - last)
                )
                log_info(
                    f"⏭️ [SYSMON:alert] {key} در cooldown "
                    f"| {remaining:.0f}s تا انتشار مجدد"
                )
                return

            self._alert_cooldowns[key] = now

        self._add_alert(level, title, message)

    def _add_alert(
        self, level: str, title: str, message: str
    ):
        """ثبت یک alert."""
        try:
            alert = Alert(level, title, message)
            self.alerts.append(alert)
            if len(self.alerts) > _ALERTS_MAX:
                self.alerts = self.alerts[-_ALERTS_MAX:]

            if level == "critical":
                log_error(
                    f"🚨 [SYSMON:alert] {title} | {message}"
                )
            elif level == "warning":
                log_warning(
                    f"⚠️ [SYSMON:alert] {title} | {message}"
                )
            else:
                log_info(
                    f"ℹ️ [SYSMON:alert] {title} | {message}"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:alert] "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # get_current_metric
    # ═══════════════════════════════════════════
    def get_current_metric(
        self, name: str
    ) -> Metric | None:
        """آخرین مقدار یک metric."""
        for m in reversed(self.metrics_history):
            if m.name == name:
                return m
        return None

    # ═══════════════════════════════════════════
    # show_status
    # ═══════════════════════════════════════════
    async def show_status(self):
        """لاگ وضعیت سیستم."""
        cpu = self.get_current_metric("cpu_usage")
        ram = self.get_current_metric("ram_usage")
        disk = self.get_current_metric("disk_usage")
        proc_mem = self.get_current_metric(
            "process_memory"
        )

        def _fmt(
            m: Metric | None, default: float = 0.0
        ) -> float:
            return m.value if m else default

        cpu_icon = (
            "🟢" if _fmt(cpu) < 60
            else "🟡" if _fmt(cpu) < 80
            else "🔴"
        )
        ram_icon = (
            "🟢" if _fmt(ram) < 70
            else "🟡" if _fmt(ram) < 90
            else "🔴"
        )

        log_info(
            f"📊 [SYSMON:status] "
            f"{cpu_icon} CPU={_fmt(cpu):.1f}% | "
            f"{ram_icon} RAM={_fmt(ram):.1f}% | "
            f"💾 Disk={_fmt(disk):.1f}% | "
            f"📦 ProcMem={_fmt(proc_mem):.0f}MB | "
            f"⏰ Uptime={self._uptime_str()}"
        )

        # alertهای 60 ثانیه اخیر
        now = datetime.now()  # noqa: DTZ005
        recent = [
            a for a in self.alerts
            if (now - a.timestamp).total_seconds() < 60
        ]
        if recent:
            log_warning(
                f"🚨 [SYSMON:status] "
                f"{len(recent)} هشدار در ۶۰ ثانیه اخیر"
            )

    def _uptime_str(self) -> str:
        """فرمت uptime."""
        uptime = self.get_current_metric("uptime")
        if not uptime:
            return "0s"
        try:
            seconds = int(uptime.value)
        except (TypeError, ValueError):
            return "0s"
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    # ═══════════════════════════════════════════
    # get_report
    # ═══════════════════════════════════════════
    def get_report(self) -> dict[str, Any]:
        """گزارش کامل وضعیت — JSON-safe."""

        def _metric_dict(
            name: str,
        ) -> dict[str, Any] | None:
            m = self.get_current_metric(name)
            return m.to_dict() if m else None

        uptime_metric = self.get_current_metric(
            "uptime"
        )
        try:
            uptime_seconds = (
                int(uptime_metric.value)
                if uptime_metric else 0
            )
        except (TypeError, ValueError):
            uptime_seconds = 0

        return {
            "system": {
                "cpu": _metric_dict("cpu_usage"),
                "ram": _metric_dict("ram_usage"),
                "disk": _metric_dict("disk_usage"),
                "uptime_str": self._uptime_str(),
                "uptime_seconds": uptime_seconds,
            },
            "process": {
                "cpu": _metric_dict("process_cpu"),
                "memory": _metric_dict("process_memory"),
                "threads": _metric_dict(
                    "process_threads"
                ),
            },
            "alerts": [
                a.to_dict() for a in self.alerts[-10:]
            ],
            "total_metrics": len(self.metrics_history),
            "total_alerts": len(self.alerts),
            "monitoring": self._monitoring,
        }

    # ═══════════════════════════════════════════
    # stop + debug
    # ═══════════════════════════════════════════
    async def stop_monitoring(self):
        """درخواست توقف."""
        self._monitoring = False
        if self._stop_event is not None:
            with contextlib.suppress(Exception):
                self._stop_event.set()
        log_info(
            f"🛑 [SYSMON:stop] درخواست توقف | "
            f"metrics={len(self.metrics_history)} "
            f"alerts={len(self.alerts)}"
        )

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        with self._alert_lock:
            cooldowns = dict(self._alert_cooldowns)

        return {
            "monitoring": self._monitoring,
            "metrics_count": len(self.metrics_history),
            "alerts_count": len(self.alerts),
            "cooldowns_count": len(cooldowns),
            "cooldown_keys": list(cooldowns.keys()),
            "uptime_s": (
                datetime.now() - self.start_time  # noqa: DTZ005
            ).total_seconds(),
            "uptime_str": self._uptime_str(),
            "thresholds_s": {
                "cooldown": _ALERT_COOLDOWN_SECONDS,
                "metrics_max": _METRICS_MAX,
                "alerts_max": _ALERTS_MAX,
            },
        }

    def __repr__(self) -> str:
        return (
            f"<SystemMonitor "
            f"metrics={len(self.metrics_history)} "
            f"alerts={len(self.alerts)} "
            f"monitoring={self._monitoring}>"
        )


# ═══════════════════════════════════════════
# PerformanceMonitor
# ═══════════════════════════════════════════
class PerformanceMonitor:
    """اندازه‌گیری زمان اجرای عملیات — per (module, operation)."""

    def __init__(self):
        self.module_stats: dict[
            str, dict[str, Any]
        ] = defaultdict(lambda: {
            "calls": 0,
            "total_time": 0.0,
            "errors": 0,
            "last_call": None,
            "avg_time": 0.0,
            "max_time": 0.0,
        })
        log_info("🔧 [PERF:init] آماده")

    # ═══════════════════════════════════════════
    # start / end
    # ═══════════════════════════════════════════
    def start_operation(
        self, module_name: str, operation: str
    ) -> float:
        """شروع اندازه‌گیری — monotonic timestamp."""
        key = f"{module_name}.{operation}"
        try:
            stats = self.module_stats[key]
            stats["calls"] += 1
            stats["last_call"] = (
                datetime.now().isoformat()  # noqa: DTZ005
            )
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:start] {key} | "
                f"{type(e).__name__}: {e}"
            )
        return time.monotonic()

    def end_operation(
        self,
        module_name: str,
        operation: str,
        start_time: float,
        error: bool = False,
    ) -> float:
        """پایان اندازه‌گیری — elapsed (ثانیه)."""
        key = f"{module_name}.{operation}"
        elapsed = time.monotonic() - start_time

        try:
            stats = self.module_stats[key]
            stats["total_time"] += elapsed
            stats["avg_time"] = (
                stats["total_time"] / stats["calls"]
                if stats["calls"] else 0.0
            )
            stats["max_time"] = max(
                stats["max_time"], elapsed
            )
            if error:
                stats["errors"] += 1

            if elapsed > _SLOW_OP_SECONDS:
                log_warning(
                    f"🐌 [PERF:slow] {key} | "
                    f"{elapsed:.2f}s | "
                    f"calls={stats['calls']} "
                    f"avg={stats['avg_time']:.2f}s"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:end] {key} | "
                f"{type(e).__name__}: {e}"
            )
        return elapsed

    # ═══════════════════════════════════════════
    # stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """dict خام stats."""
        return dict(self.module_stats)

    def get_slowest(
        self, limit: int = 5
    ) -> list[tuple[str, dict[str, Any]]]:
        """🎯 برگرداندن N عملیات کندترین (نه لاگ)."""
        try:
            return sorted(
                self.module_stats.items(),
                key=lambda x: x[1].get(
                    "avg_time", 0.0
                ),
                reverse=True,
            )[:limit]
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:slowest] "
                f"{type(e).__name__}: {e}"
            )
            return []

    def show_slowest_operations(self, limit: int = 5):
        """لاگ N عملیات کندترین."""
        try:
            sorted_items = self.get_slowest(limit)
            if not sorted_items:
                log_info(
                    "ℹ️ [PERF:slowest] هیچ داده‌ای نیست"
                )
                return

            lines = []
            for name, stats in sorted_items:
                lines.append(
                    f"{name}: "
                    f"avg={stats.get('avg_time', 0.0):.2f}s "
                    f"max={stats.get('max_time', 0.0):.2f}s "
                    f"({stats.get('calls', 0)} calls, "
                    f"{stats.get('errors', 0)} err)"
                )
            log_info(
                "🐌 [PERF:slowest] " + " | ".join(lines)
            )
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:slowest] "
                f"{type(e).__name__}: {e}"
            )

    def reset_stats(self) -> int:
        """🎯 پاک‌کردن stats — برای تست."""
        n = len(self.module_stats)
        self.module_stats.clear()
        log_info(
            f"🗑️ [PERF:reset] {n} module پاک شد"
        )
        return n

    def get_module_count(self) -> int:
        """تعداد ماژول‌های ثبت‌شده."""
        return len(self.module_stats)

    def __repr__(self) -> str:
        return (
            f"<PerformanceMonitor "
            f"modules={len(self.module_stats)}>"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "Alert",
    "Metric",
    "PerformanceMonitor",
    "SystemMonitor",
]