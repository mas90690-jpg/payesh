"""
core/ops/monitor.py — مانیتورینگ منابع سیستم + عملکرد ماژولها.

دو کلاس:
  1) SystemMonitor      → CPU/RAM/Disk/Network/Process + هشدارهای آستانهای
  2) PerformanceMonitor → اندازهگیری زمان اجرای عملیات در ماژولها

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX بحرانی: در get_report، uptime دوبار صدا زده میشد
    (O(n) اضافه روی ۱۰۰۰ آیتم). حالا یکبار میگیریم.
  - ✅ نسخهٔ ۲ هم حفظ شده
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psutil

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── ثابتها ───
_METRICS_MAX = 1000
_ALERTS_MAX = 100
_STATUS_EVERY_N_CYCLES = 12      # هر ۱۲ سیکل (۱ دقیقه اگه ۵ثانیهای)
_ALERT_COOLDOWN_SECONDS = 300    # ۵ دقیقه — هر alert در این بازه فقط یکبار
_SLOW_OP_SECONDS = 5.0
_LOG_PREVIEW_LEN = 60


# ═══════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════
@dataclass
class Metric:
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


@dataclass
class Alert:
    level: str
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }


# ═══════════════════════════════════════════
# SystemMonitor
# ═══════════════════════════════════════════
class SystemMonitor:
    """مانیتورینگ منابع سیستم با هشدارهای آستانهای + cooldown."""

    def __init__(self):
        self.start_time = datetime.now()
        self.metrics_history: list[Metric] = []
        self.alerts: list[Alert] = []
        self._monitoring = False
        self._task: asyncio.Task | None = None
        # cooldown: {alert_key: last_emitted_timestamp}
        self._alert_cooldowns: dict[str, float] = {}

        log_info(
            f"🔧 [SYSMON:init] آماده | "
            f"cooldown={_ALERT_COOLDOWN_SECONDS}s | "
            f"status_every={_STATUS_EVERY_N_CYCLES} cycles"
        )

    # ═══════════════════════════════════════════
    # حلقهٔ اصلی
    # ═══════════════════════════════════════════
    async def start_monitoring(self, interval_seconds: int = 5):
        """شروع حلقهٔ پایش — بلاک میشود تا stop_monitoring."""
        if self._monitoring:
            log_warning("⚠️ [SYSMON:start] قبلاً در حال اجراست")
            return

        self._monitoring = True
        interval = max(1, int(interval_seconds))
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

                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"✔️ [SYSMON:cycle] #{cycle} | {elapsed_ms:.0f}ms | "
                    f"metrics_total={len(self.metrics_history)} "
                    f"alerts_total={len(self.alerts)}"
                )

                # status دورهای
                if cycle % _STATUS_EVERY_N_CYCLES == 0:
                    await self.show_status()

            except asyncio.CancelledError:
                log_info(
                    f"🛑 [SYSMON:cycle] cancel (cycle#{cycle})"
                )
                raise
            except Exception as e:
                log_error(
                    f"❌ [SYSMON:cycle] #{cycle} | "
                    f"{type(e).__name__}: {e}"
                )

            await asyncio.sleep(interval)

        log_info(
            f"🛑 [SYSMON:start] حلقه متوقف | total_cycles={cycle}"
        )

    # ═══════════════════════════════════════════
    # collect_metrics
    # ═══════════════════════════════════════════
    async def collect_metrics(self):
        """جمعآوری همه metricها — sync psutil ولی سریع."""
        t0 = time.monotonic()

        # CPU (non-blocking با interval=None)
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            self._add_metric("cpu_usage", cpu_percent, "%")
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] cpu | {type(e).__name__}: {e}"
            )

        # RAM
        try:
            mem = psutil.virtual_memory()
            self._add_metric("ram_usage", mem.percent, "%")
            self._add_metric("ram_used", mem.used / (1024**3), "GB")
            self._add_metric(
                "ram_available", mem.available / (1024**3), "GB"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] ram | {type(e).__name__}: {e}"
            )

        # Disk
        try:
            # 🐛 FIX: تشخیص تمیز cross-platform
            disk_path = "C:\\" if os.name == "nt" else "/"
            disk = psutil.disk_usage(disk_path)
            self._add_metric("disk_usage", disk.percent, "%")
            self._add_metric("disk_used", disk.used / (1024**3), "GB")
            self._add_metric("disk_free", disk.free / (1024**3), "GB")
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] disk | {type(e).__name__}: {e}"
            )

        # Network
        try:
            net_io = psutil.net_io_counters()
            self._add_metric(
                "network_sent", net_io.bytes_sent / (1024**2), "MB"
            )
            self._add_metric(
                "network_recv", net_io.bytes_recv / (1024**2), "MB"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] net | {type(e).__name__}: {e}"
            )

        # Process
        try:
            proc = psutil.Process()
            self._add_metric("process_cpu", proc.cpu_percent(), "%")
            self._add_metric(
                "process_memory",
                proc.memory_info().rss / (1024**2),
                "MB",
            )
            self._add_metric(
                "process_threads", proc.num_threads(), "count"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] process | {type(e).__name__}: {e}"
            )

        # Uptime
        uptime = (datetime.now() - self.start_time).total_seconds()
        self._add_metric("uptime", uptime, "seconds")

        # Temperature (اختیاری)
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for sensor, entries in temps.items():
                    for entry in entries:
                        self._add_metric(
                            f"temp_{sensor}", entry.current, "°C"
                        )
        except (AttributeError, NotImplementedError):
            # Windows/macOS معمولاً sensors_temperatures ندارن
            pass
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:collect] temps | {type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > 1000:
            log_warning(
                f"🐌 [SYSMON:collect] کند | {elapsed_ms:.0f}ms"
            )

    def _add_metric(self, name: str, value: float, unit: str):
        try:
            self.metrics_history.append(Metric(name, value, unit))
            if len(self.metrics_history) > _METRICS_MAX:
                self.metrics_history = self.metrics_history[-_METRICS_MAX:]
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:metric] {name} | {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # check_alerts
    # ═══════════════════════════════════════════
    async def check_alerts(self):
        """بررسی آستانهها + اعمال cooldown برای جلوگیری از اسپم."""
        # thresholds از config
        cpu_th, ram_th, disk_th = 95, 92, 92
        try:
            from config import MONITOR
            cpu_th = MONITOR.get("cpu_alert", 95)
            ram_th = MONITOR.get("ram_alert", 92)
            disk_th = MONITOR.get("disk_alert", 92)
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:thresholds] config | "
                f"{type(e).__name__}: {e} — پیشفرض [95, 92, 92]"
            )

        # CPU
        cpu = self.get_current_metric("cpu_usage")
        if cpu and cpu.value > cpu_th:
            self._add_alert_with_cooldown(
                key="cpu_high",
                level="critical",
                title="High CPU Usage",
                message=f"CPU: {cpu.value:.1f}% (threshold {cpu_th}%)",
            )

        # RAM
        ram = self.get_current_metric("ram_usage")
        if ram and ram.value > ram_th:
            self._add_alert_with_cooldown(
                key="ram_high",
                level="critical",
                title="High RAM Usage",
                message=f"RAM: {ram.value:.1f}% (threshold {ram_th}%)",
            )

        # Disk
        disk = self.get_current_metric("disk_usage")
        if disk and disk.value > disk_th:
            self._add_alert_with_cooldown(
                key="disk_high",
                level="critical",
                title="Disk Space Critical",
                message=(
                    f"Disk: {disk.value:.1f}% (threshold {disk_th}%)"
                ),
            )

    # ═══════════════════════════════════════════
    # alert با cooldown
    # ═══════════════════════════════════════════
    def _add_alert_with_cooldown(
        self, key: str, level: str, title: str, message: str
    ):
        """alert فقط اگه از آخرین بار cooldown گذشته باشه."""
        now = time.monotonic()
        last = self._alert_cooldowns.get(key, 0)
        if now - last < _ALERT_COOLDOWN_SECONDS:
            remaining = _ALERT_COOLDOWN_SECONDS - (now - last)
            log_info(
                f"⏭️ [SYSMON:alert] {key} در cooldown | "
                f"{remaining:.0f}s تا انتشار مجدد"
            )
            return

        self._alert_cooldowns[key] = now
        self._add_alert(level, title, message)

    def _add_alert(self, level: str, title: str, message: str):
        try:
            alert = Alert(level, title, message)
            self.alerts.append(alert)
            if len(self.alerts) > _ALERTS_MAX:
                self.alerts = self.alerts[-_ALERTS_MAX:]

            if level == "critical":
                log_error(f"🚨 [SYSMON:alert] {title} | {message}")
            elif level == "warning":
                log_warning(f"⚠️ [SYSMON:alert] {title} | {message}")
            else:
                log_info(f"ℹ️ [SYSMON:alert] {title} | {message}")
        except Exception as e:
            log_warning(
                f"⚠️ [SYSMON:alert] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # get_current_metric (O(n) ولی n≤1000 — ok)
    # ═══════════════════════════════════════════
    def get_current_metric(self, name: str) -> Metric | None:
        for m in reversed(self.metrics_history):
            if m.name == name:
                return m
        return None

    # ═══════════════════════════════════════════
    # show_status — لاگ بهجای print
    # ═══════════════════════════════════════════
    async def show_status(self):
        cpu = self.get_current_metric("cpu_usage")
        ram = self.get_current_metric("ram_usage")
        disk = self.get_current_metric("disk_usage")
        proc_mem = self.get_current_metric("process_memory")

        def _fmt(m: Metric | None, default: float = 0.0) -> float:
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
        now = datetime.now()
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
        uptime = self.get_current_metric("uptime")
        if not uptime:
            return "0s"
        seconds = int(uptime.value)
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    # ═══════════════════════════════════════════
    # get_report — dict قابل serialize
    # ═══════════════════════════════════════════
    def get_report(self) -> dict[str, Any]:
        """
        گزارش کامل وضعیت — dict قابل JSON-serialize.

        🐛 FIX: قبلاً uptime دوبار صدا زده میشد (O(n) اضافی).
        حالا یهبار میگیریم.
        """
        def _metric_dict(name: str) -> dict[str, Any] | None:
            m = self.get_current_metric(name)
            return m.to_dict() if m else None

        # 🐛 FIX: uptime رو یهبار بگیر
        uptime_metric = self.get_current_metric("uptime")
        uptime_seconds = (
            int(uptime_metric.value) if uptime_metric else 0
        )

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
                "threads": _metric_dict("process_threads"),
            },
            "alerts": [a.to_dict() for a in self.alerts[-10:]],
            "total_metrics": len(self.metrics_history),
            "total_alerts": len(self.alerts),
        }

    # ═══════════════════════════════════════════
    # stop
    # ═══════════════════════════════════════════
    async def stop_monitoring(self):
        self._monitoring = False
        log_info(
            f"🛑 [SYSMON:stop] درخواست توقف | "
            f"metrics={len(self.metrics_history)} "
            f"alerts={len(self.alerts)}"
        )


# ═══════════════════════════════════════════
# PerformanceMonitor
# ═══════════════════════════════════════════
class PerformanceMonitor:
    """اندازهگیری زمان اجرای عملیات — per (module, operation)."""

    def __init__(self):
        self.module_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "total_time": 0.0,
                "errors": 0,
                "last_call": None,
                "avg_time": 0.0,
                "max_time": 0.0,
            }
        )
        log_info("🔧 [PERF:init] آماده")

    def start_operation(
        self, module_name: str, operation: str
    ) -> float:
        """شروع اندازهگیری — timestamp برمیگردونه.

        از time.monotonic() استفاده میکنه (مقاوم به تغییر ساعت).
        """
        key = f"{module_name}.{operation}"
        try:
            stats = self.module_stats[key]
            stats["calls"] += 1
            stats["last_call"] = datetime.now().isoformat()
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:start] {key} | {type(e).__name__}: {e}"
            )
        return time.monotonic()

    def end_operation(
        self,
        module_name: str,
        operation: str,
        start_time: float,
        error: bool = False,
    ) -> float:
        """پایان اندازهگیری — زمان سپریشده رو برمیگردونه (ثانیه)."""
        key = f"{module_name}.{operation}"
        elapsed = time.monotonic() - start_time
        try:
            stats = self.module_stats[key]
            stats["total_time"] += elapsed
            stats["avg_time"] = (
                stats["total_time"] / stats["calls"]
                if stats["calls"] else 0.0
            )
            stats["max_time"] = max(stats["max_time"], elapsed)
            if error:
                stats["errors"] += 1

            if elapsed > _SLOW_OP_SECONDS:
                log_warning(
                    f"🐌 [PERF:slow] {key} | {elapsed:.2f}s | "
                    f"calls={stats['calls']} "
                    f"avg={stats['avg_time']:.2f}s"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:end] {key} | {type(e).__name__}: {e}"
            )
        return elapsed

    def get_stats(self) -> dict:
        return dict(self.module_stats)

    def show_slowest_operations(self, limit: int = 5):
        """لاگ ۵ عملیات کندترین — بهجای print."""
        try:
            sorted_items = sorted(
                self.module_stats.items(),
                key=lambda x: x[1]["avg_time"],
                reverse=True,
            )[:limit]
            if not sorted_items:
                log_info("ℹ️ [PERF:slowest] هیچ دادهای نیست")
                return
            lines = []
            for name, stats in sorted_items:
                lines.append(
                    f"{name}: avg={stats['avg_time']:.2f}s "
                    f"max={stats['max_time']:.2f}s "
                    f"({stats['calls']} calls, "
                    f"{stats['errors']} err)"
                )
            log_info("🐌 [PERF:slowest] " + " | ".join(lines))
        except Exception as e:
            log_warning(
                f"⚠️ [PERF:slowest] {type(e).__name__}: {e}"
            )
