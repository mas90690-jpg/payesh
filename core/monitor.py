# -*- coding: utf-8 -*-
import time
import psutil
import asyncio
from datetime import datetime
from typing import Dict, List, Any
from dataclasses import dataclass, field
from collections import defaultdict
from core.logger import log_info, log_success, log_error, log_warning

@dataclass
class Metric:
    name: str
    value: float
    unit: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class Alert:
    level: str
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

class SystemMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.metrics_history: List[Metric] = []
        self.alerts: List[Alert] = []
        self._monitoring = False

    async def start_monitoring(self, interval_seconds: int = 5):
        self._monitoring = True
        log_success(f"System monitoring started (every {interval_seconds}s)")
        while self._monitoring:
            await self.collect_metrics()
            await self.check_alerts()
            await asyncio.sleep(interval_seconds)

    async def collect_metrics(self):
        # interval=None یعنی غیرمسدودکننده (حلقهٔ async را ۱ ثانیه بلاک نمی‌کند)
        cpu_percent = psutil.cpu_percent(interval=None)
        self._add_metric("cpu_usage", cpu_percent, "%")

        mem = psutil.virtual_memory()
        self._add_metric("ram_usage", mem.percent, "%")
        self._add_metric("ram_used", mem.used / (1024**3), "GB")
        self._add_metric("ram_available", mem.available / (1024**3), "GB")

        disk = psutil.disk_usage('/')
        self._add_metric("disk_usage", disk.percent, "%")
        self._add_metric("disk_used", disk.used / (1024**3), "GB")
        self._add_metric("disk_free", disk.free / (1024**3), "GB")

        net_io = psutil.net_io_counters()
        self._add_metric("network_sent", net_io.bytes_sent / (1024**2), "MB")
        self._add_metric("network_recv", net_io.bytes_recv / (1024**2), "MB")

        proc = psutil.Process()
        self._add_metric("process_cpu", proc.cpu_percent(), "%")
        self._add_metric("process_memory", proc.memory_info().rss / (1024**2), "MB")
        self._add_metric("process_threads", proc.num_threads(), "count")

        uptime = (datetime.now() - self.start_time).total_seconds()
        self._add_metric("uptime", uptime, "seconds")

        try:
            temps = psutil.sensors_temperatures()
            for sensor, entries in temps.items():
                for entry in entries:
                    self._add_metric(f"temp_{sensor}", entry.current, "°C")
        except Exception:
            pass

        if len(self.metrics_history) % 6 == 0:
            await self.show_status()

    def _add_metric(self, name: str, value: float, unit: str):
        self.metrics_history.append(Metric(name, value, unit))
        if len(self.metrics_history) > 1000:
            self.metrics_history = self.metrics_history[-1000:]

    async def check_alerts(self):
        # آستانه‌ها از config (قابل تنظیم در .env) — فقط هشدارهای واقعاً مهم، بدون نویز
        try:
            from config import MONITOR
            cpu_th = MONITOR["cpu_alert"]
            ram_th = MONITOR["ram_alert"]
            disk_th = MONITOR["disk_alert"]
        except Exception:
            cpu_th, ram_th, disk_th = 95, 92, 92

        cpu = self.get_current_metric("cpu_usage")
        if cpu and cpu.value > cpu_th:
            self._add_alert("critical", "High CPU Usage", f"CPU: {cpu.value}%")

        ram = self.get_current_metric("ram_usage")
        if ram and ram.value > ram_th:
            self._add_alert("critical", "High RAM Usage", f"RAM: {ram.value}%")

        disk = self.get_current_metric("disk_usage")
        if disk and disk.value > disk_th:
            self._add_alert("critical", "Disk Space Critical", f"Disk: {disk.value}%")

    def _add_alert(self, level: str, title: str, message: str):
        alert = Alert(level, title, message)
        self.alerts.append(alert)
        if level == "critical":
            log_error(f"🚨 {title}: {message}")
        elif level == "warning":
            log_warning(f"⚠️ {title}: {message}")
        else:
            log_info(f"ℹ️ {title}: {message}")
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-100:]

    def get_current_metric(self, name: str):
        for m in reversed(self.metrics_history):
            if m.name == name:
                return m
        return None

    async def show_status(self):
        cpu = self.get_current_metric("cpu_usage")
        ram = self.get_current_metric("ram_usage")
        disk = self.get_current_metric("disk_usage")
        proc_mem = self.get_current_metric("process_memory")

        cpu_icon = "🟢" if cpu and cpu.value < 60 else "🟡" if cpu and cpu.value < 80 else "🔴"
        ram_icon = "🟢" if ram and ram.value < 70 else "🟡" if ram and ram.value < 90 else "🔴"

        print(f"\n📊 [{datetime.now().strftime('%H:%M:%S')}] System Status:")
        print(f"   {cpu_icon} CPU: {cpu.value if cpu else 0}%  |  {ram_icon} RAM: {ram.value if ram else 0}%")
        print(f"   💾 Disk: {disk.value if disk else 0}%  |  📦 Process Memory: {proc_mem.value if proc_mem else 0}MB")
        print(f"   ⏰ Uptime: {self._uptime_str()}")

        recent = [a for a in self.alerts if (datetime.now() - a.timestamp).seconds < 60]
        if recent:
            print(f"   🚨 {len(recent)} new alert(s)")

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
        else:
            return f"{secs}s"

    def get_report(self) -> Dict[str, Any]:
        return {
            "system": {
                "cpu": self.get_current_metric("cpu_usage"),
                "ram": self.get_current_metric("ram_usage"),
                "disk": self.get_current_metric("disk_usage"),
                "uptime": self._uptime_str()
            },
            "process": {
                "cpu": self.get_current_metric("process_cpu"),
                "memory": self.get_current_metric("process_memory"),
                "threads": self.get_current_metric("process_threads")
            },
            "alerts": self.alerts[-10:],
            "total_metrics": len(self.metrics_history),
            "total_alerts": len(self.alerts)
        }

    async def stop_monitoring(self):
        self._monitoring = False
        log_info("System monitoring stopped")

class PerformanceMonitor:
    def __init__(self):
        self.module_stats: Dict[str, Dict] = defaultdict(lambda: {
            "calls": 0,
            "total_time": 0.0,
            "errors": 0,
            "last_call": None,
            "avg_time": 0.0
        })

    def start_operation(self, module_name: str, operation: str) -> float:
        key = f"{module_name}.{operation}"
        self.module_stats[key]["calls"] += 1
        self.module_stats[key]["last_call"] = datetime.now()
        return time.time()

    def end_operation(self, module_name: str, operation: str, start_time: float, error: bool = False) -> float:
        key = f"{module_name}.{operation}"
        elapsed = time.time() - start_time
        stats = self.module_stats[key]
        stats["total_time"] += elapsed
        stats["avg_time"] = stats["total_time"] / stats["calls"]
        if error:
            stats["errors"] += 1
        if elapsed > 5:
            log_warning(f"⏱️ Slow operation: {key} took {elapsed:.2f}s")
        return elapsed

    def get_stats(self) -> Dict:
        return dict(self.module_stats)

    def show_slowest_operations(self, limit: int = 5):
        sorted_items = sorted(
            self.module_stats.items(),
            key=lambda x: x[1]["avg_time"],
            reverse=True
        )[:limit]
        if sorted_items:
            print("\n🐌 Slowest operations:")
            for name, stats in sorted_items:
                print(f"   {name}: {stats['avg_time']:.2f}s avg ({stats['calls']} calls)")