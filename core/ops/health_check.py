"""core/ops/health_check.py — سیستم بررسی سلامت (Health Check).

قابلیت‌ها:
  - بررسی سلامت دیتابیس (اتصال واقعی + حجم فایل)
  - بررسی سلامت کلکتورها (check_health یا is_connected) با timeout
  - بررسی سلامت سیستم (CPU, RAM, Disk) با سه سطح
  - بررسی سلامت APIهای خارجی (HTTP status code)
  - گزارش وضعیت کلی (بدترین وضعیت برنده است)

نسخهٔ ۴ (این نسخه):
  - 🐛 FIX: همهٔ thresholdها حالا با `_env_float` (sanitize).
  - 🐛 FIX: `_API_DEFAULT_TIMEOUT` حالا env-based
    (`HEALTH_API_TIMEOUT_S`).
  - 🐛 FIX: `_COLLECTOR_HEALTH_TIMEOUT` با `_env_float`.
  - 🐛 FIX: `check_all` حالا از `time.monotonic()` استفاده می‌کند
    (مقاوم به تغییر ساعت).
  - 🐛 FIX: `check_all` حالا `_check_lock` دارد (race-safe).
  - 🐛 FIX: `check_database` حالا `asyncio.wait_for` دارد
    (جلوگیری از hang).
  - 🐛 FIX: `check_external_api` حالا `aiohttp.ClientTimeout`
    صریح می‌سازد.
  - 🐛 FIX: `HealthCheckResult.to_dict()` حالا deep-copy از
    `details` می‌دهد.
  - 🎯 `HealthCheckResult.is_healthy()` helper.
  - 🎯 `HealthCheckResult.to_prometheus()` برای obs_server.
  - 🎯 `reset_results()` متد جدید.
  - 🎯 `get_stats()` متد جدید.
  - 🎯 env-based: `HEALTH_API_TIMEOUT_S`, `HEALTH_DB_TIMEOUT_S`,
    `HEALTH_CPU_SAMPLE_S`.
  - 🎯 `__repr__` برای HealthChecker.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import asyncio
import copy
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import aiohttp
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


# ═══════════════════════════════════════════
# آستانه‌های هشدار (env-based)
# ═══════════════════════════════════════════
_CPU_DEGRADED = _env_float(
    "HEALTH_CPU_DEGRADED", 80.0, min_val=1.0, max_val=100.0
)
_CPU_UNHEALTHY = _env_float(
    "HEALTH_CPU_UNHEALTHY", 90.0, min_val=1.0, max_val=100.0
)
_RAM_DEGRADED = _env_float(
    "HEALTH_RAM_DEGRADED", 85.0, min_val=1.0, max_val=100.0
)
_RAM_UNHEALTHY = _env_float(
    "HEALTH_RAM_UNHEALTHY", 95.0, min_val=1.0, max_val=100.0
)
_DISK_DEGRADED = _env_float(
    "HEALTH_DISK_DEGRADED", 85.0, min_val=1.0, max_val=100.0
)
_DISK_UNHEALTHY = _env_float(
    "HEALTH_DISK_UNHEALTHY", 95.0, min_val=1.0, max_val=100.0
)

# ─── timeoutها ───
_API_DEFAULT_TIMEOUT = _env_float(
    "HEALTH_API_TIMEOUT_S", 10.0,
    min_val=1.0, max_val=300.0,
)
_COLLECTOR_HEALTH_TIMEOUT = _env_float(
    "HEALTH_COLLECTOR_TIMEOUT", 10.0,
    min_val=1.0, max_val=300.0,
)
_DB_TIMEOUT = _env_float(
    "HEALTH_DB_TIMEOUT_S", 5.0,
    min_val=0.5, max_val=60.0,
)
_CPU_SAMPLE_S = _env_float(
    "HEALTH_CPU_SAMPLE_S", 0.5,
    min_val=0.0, max_val=5.0,
)


# ═══════════════════════════════════════════
# HealthStatus
# ═══════════════════════════════════════════
class HealthStatus(Enum):
    """وضعیت سلامت — از بهترین به بدترین."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    @property
    def order(self) -> int:
        """🎯 ترتیب عددی — برای مقایسه."""
        return {
            HealthStatus.HEALTHY: 0,
            HealthStatus.DEGRADED: 1,
            HealthStatus.UNKNOWN: 2,
            HealthStatus.UNHEALTHY: 3,
        }[self]


# ═══════════════════════════════════════════
# HealthCheckResult
# ═══════════════════════════════════════════
@dataclass
class HealthCheckResult:
    """نتیجهٔ یک چک سلامت."""

    component: str
    status: HealthStatus
    message: str = ""
    response_time: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.now)

    def is_healthy(self) -> bool:
        """🎯 آیا این component سالم است؟"""
        return self.status == HealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        """🎯 FIX: deep-copy از details."""
        try:
            details_copy = copy.deepcopy(self.details)
        except Exception:
            details_copy = dict(self.details or {})

        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "response_time": self.response_time,
            "details": details_copy,
            "last_check": (
                self.last_check.isoformat()
                if isinstance(self.last_check, datetime)
                else str(self.last_check)
            ),
        }

    def to_prometheus(self) -> str:
        """🎯 خروجی Prometheus text."""
        status_val = {
            HealthStatus.HEALTHY: 1,
            HealthStatus.DEGRADED: 0.5,
            HealthStatus.UNHEALTHY: 0,
            HealthStatus.UNKNOWN: -1,
        }.get(self.status, -1)

        # escape برای label
        comp = str(self.component).replace('"', '\\"')

        lines = [
            f'health_status{{component="{comp}"}} {status_val}',
            f'health_response_time{{component="{comp}"}} '
            f"{self.response_time}",
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════
# HealthChecker
# ═══════════════════════════════════════════
class HealthChecker:
    """سیستم بررسی سلامت — همهٔ اجزا به‌صورت یکجا."""

    def __init__(self, db=None):
        self.db = db
        self.results: dict[str, HealthCheckResult] = {}
        self._collectors: list[Any] = []
        self._external_apis: dict[str, str] = {}
        self._check_lock = asyncio.Lock()

        log_info(
            f"🏥 [HEALTH:init] نمونه ساخته شد | "
            f"thresholds: "
            f"CPU>{_CPU_DEGRADED}/{_CPU_UNHEALTHY} "
            f"RAM>{_RAM_DEGRADED}/{_RAM_UNHEALTHY} "
            f"Disk>{_DISK_DEGRADED}/{_DISK_UNHEALTHY} | "
            f"collector_timeout={_COLLECTOR_HEALTH_TIMEOUT}s | "
            f"db_timeout={_DB_TIMEOUT}s | "
            f"api_timeout={_API_DEFAULT_TIMEOUT}s"
        )

    # ═══════════════════════════════════════════
    # ثبت
    # ═══════════════════════════════════════════
    def register_collector(self, collector) -> None:
        """ثبت کلکتور برای بررسی سلامت (ضدتکراری)."""
        name = getattr(collector, "platform_name", "unknown")

        if any(
            getattr(c, "platform_name", None) == name
            for c in self._collectors
        ):
            log_warning(
                f"⚠️ [HEALTH:register] کلکتور {name} "
                f"قبلاً ثبت شده — نادیده گرفته شد"
            )
            return

        self._collectors.append(collector)
        log_info(
            f"📝 [HEALTH:register] کلکتور {name} ثبت شد "
            f"(مجموع: {len(self._collectors)})"
        )

    def register_external_api(
        self, name: str, url: str
    ) -> None:
        """ثبت API خارجی برای بررسی."""
        if not name or not url:
            log_warning(
                "⚠️ [HEALTH:register_api] name/url خالی — رد"
            )
            return
        self._external_apis[name] = url
        log_info(
            f"📝 [HEALTH:register_api] «{name}» → {url}"
        )

    # ═══════════════════════════════════════════
    # اجرای همهٔ چک‌ها
    # ═══════════════════════════════════════════
    async def check_all(self) -> dict[str, Any]:
        """بررسی همهٔ اجزا — نتایج در `self.results` ذخیره می‌شوند."""
        # 🎯 FIX: lock برای جلوگیری از race
        async with self._check_lock:
            return await self._check_all_inner()

    async def _check_all_inner(self) -> dict[str, Any]:
        """قلب `check_all` (بدون lock)."""
        log_info(
            f"🏥 [HEALTH:check_all] شروع | "
            f"collectors={len(self._collectors)} "
            f"apis={len(self._external_apis)} "
            f"db={'✅' if self.db else '—'}"
        )

        # 🎯 FIX: monotonic
        t_start = time.monotonic()

        # ریست نتایج قبلی
        self.results = {}

        # ─── ۱) دیتابیس ───
        try:
            self.results["database"] = (
                await self.check_database()
            )
        except Exception as e:
            log_error(
                f"❌ [HEALTH:check_all:db] "
                f"{type(e).__name__}: {e}"
            )
            self.results["database"] = HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"استثنا: {e}",
            )

        # ─── ۲) کلکتورها ───
        for collector in self._collectors:
            name = getattr(
                collector, "platform_name", "unknown"
            )
            try:
                self.results[f"collector_{name}"] = (
                    await self.check_collector(collector)
                )
            except Exception as e:
                log_error(
                    f"❌ [HEALTH:check_all:collector:{name}] "
                    f"{type(e).__name__}: {e}"
                )
                self.results[f"collector_{name}"] = (
                    HealthCheckResult(
                        component=f"collector_{name}",
                        status=HealthStatus.UNHEALTHY,
                        message=f"استثنا: {e}",
                    )
                )

        # ─── ۳) سیستم ───
        try:
            self.results["system"] = await self.check_system()
        except Exception as e:
            log_error(
                f"❌ [HEALTH:check_all:system] "
                f"{type(e).__name__}: {e}"
            )
            self.results["system"] = HealthCheckResult(
                component="system",
                status=HealthStatus.UNKNOWN,
                message=f"استثنا: {e}",
            )

        # ─── ۴) APIهای خارجی ───
        for name, url in self._external_apis.items():
            try:
                self.results[f"api_{name}"] = (
                    await self.check_external_api(name, url)
                )
            except Exception as e:
                log_error(
                    f"❌ [HEALTH:check_all:api:{name}] "
                    f"{type(e).__name__}: {e}"
                )
                self.results[f"api_{name}"] = (
                    HealthCheckResult(
                        component=f"api_{name}",
                        status=HealthStatus.UNHEALTHY,
                        message=f"استثنا: {e}",
                    )
                )

        # ─── خلاصه ───
        total_ms = (time.monotonic() - t_start) * 1000
        overall = self._get_overall_status()

        counts = {
            "healthy": 0,
            "degraded": 0,
            "unhealthy": 0,
            "unknown": 0,
        }
        for r in self.results.values():
            key = r.status.value
            counts[key] = counts.get(key, 0) + 1

        icon = {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.DEGRADED: "⚠️",
            HealthStatus.UNHEALTHY: "🔴",
            HealthStatus.UNKNOWN: "❓",
        }.get(overall, "❓")

        msg = (
            f"{icon} [HEALTH:check_all] {total_ms:.0f}ms | "
            f"{overall.value} | "
            f"✅{counts['healthy']} ⚠️{counts['degraded']} "
            f"🔴{counts['unhealthy']} ❓{counts['unknown']}"
        )
        if overall == HealthStatus.HEALTHY:
            log_success(msg)
        elif overall == HealthStatus.UNHEALTHY:
            log_error(msg)
        else:
            log_warning(msg)

        return {
            "overall_status": overall.value,
            "timestamp": datetime.now().isoformat(),
            "total_time": total_ms / 1000,
            "components": {
                name: r.to_dict()
                for name, r in self.results.items()
            },
        }

    # ═══════════════════════════════════════════
    # چک دیتابیس
    # ═══════════════════════════════════════════
    async def check_database(self) -> HealthCheckResult:
        """بررسی سلامت دیتابیس — اتصال واقعی + حجم."""
        t_start = time.monotonic()

        if not self.db:
            log_warning(
                "⚠️ [HEALTH:db] دیتابیس ثبت نشده"
            )
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNKNOWN,
                message="دیتابیس ثبت نشده است",
            )

        try:
            # 🎯 FIX: timeout
            async def _probe():
                result = await self.db.execute("SELECT 1")
                return await result.fetchone()

            row = await asyncio.wait_for(
                _probe(), timeout=_DB_TIMEOUT
            )

            response_ms = (
                time.monotonic() - t_start
            ) * 1000

            # ─── پاسخ خالی ───
            if not row:
                log_warning(
                    f"⚠️ [HEALTH:db] پاسخ خالی | "
                    f"{response_ms:.0f}ms"
                )
                return HealthCheckResult(
                    component="database",
                    status=HealthStatus.DEGRADED,
                    message=(
                        "دیتابیس پاسخ خالی داد "
                        "(شاید قفل/بازیابی)"
                    ),
                    response_time=response_ms / 1000,
                )

            # ─── حجم فایل ───
            details: dict[str, Any] = {"probe": "ok"}
            try:
                db_path = getattr(self.db, "db_path", None)
                if db_path and os.path.exists(db_path):
                    size_bytes = os.path.getsize(db_path)
                    details["size_mb"] = round(
                        size_bytes / (1024 * 1024), 2
                    )
            except Exception:
                pass

            size_info = (
                f" size={details.get('size_mb', '?')}MB"
                if "size_mb" in details else ""
            )
            log_success(
                f"✅ [HEALTH:db] سالم | "
                f"{response_ms:.0f}ms{size_info}"
            )

            return HealthCheckResult(
                component="database",
                status=HealthStatus.HEALTHY,
                message="دیتابیس سالم است",
                response_time=response_ms / 1000,
                details=details,
            )

        except TimeoutError:
            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            log_warning(
                f"⏰ [HEALTH:db] timeout {_DB_TIMEOUT}s"
            )
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"timeout در اتصال ({_DB_TIMEOUT}s)",
                response_time=response_ms / 1000,
            )
        except Exception as e:
            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            log_error(
                f"❌ [HEALTH:db] خطا | {response_ms:.0f}ms | "
                f"{type(e).__name__}: {e}"
            )
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا: {e}",
                response_time=response_ms / 1000,
            )

    # ═══════════════════════════════════════════
    # چک کلکتور
    # ═══════════════════════════════════════════
    async def check_collector(
        self, collector
    ) -> HealthCheckResult:
        """بررسی سلامت یک کلکتور با timeout."""
        t_start = time.monotonic()
        platform = getattr(
            collector, "platform_name", "unknown"
        )

        try:
            # ─── check_health ───
            if hasattr(collector, "check_health"):
                try:
                    result = await asyncio.wait_for(
                        collector.check_health(),
                        timeout=_COLLECTOR_HEALTH_TIMEOUT,
                    )
                    source = "check_health()"
                except TimeoutError:
                    response_ms = (
                        time.monotonic() - t_start
                    ) * 1000
                    log_warning(
                        f"⏰ [HEALTH:collector:{platform}] "
                        f"timeout "
                        f"({_COLLECTOR_HEALTH_TIMEOUT}s)"
                    )
                    return HealthCheckResult(
                        component=f"collector_{platform}",
                        status=HealthStatus.UNHEALTHY,
                        message=(
                            f"timeout در check_health "
                            f"({_COLLECTOR_HEALTH_TIMEOUT}s)"
                        ),
                        response_time=response_ms / 1000,
                        details={"source": "timeout"},
                    )

            # ─── is_connected ───
            elif hasattr(collector, "is_connected"):
                try:
                    result = collector.is_connected
                except Exception:
                    result = getattr(
                        collector, "_connected", False
                    )
                source = "is_connected"

            # ─── _connected ───
            else:
                result = getattr(
                    collector, "_connected", False
                )
                source = "_connected"

            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            status = (
                HealthStatus.HEALTHY if result
                else HealthStatus.UNHEALTHY
            )

            if result:
                log_success(
                    f"✅ [HEALTH:collector:{platform}] "
                    f"متصل | {response_ms:.0f}ms | "
                    f"via {source}"
                )
            else:
                log_warning(
                    f"🔴 [HEALTH:collector:{platform}] "
                    f"قطع | via {source}"
                )

            return HealthCheckResult(
                component=f"collector_{platform}",
                status=status,
                message=(
                    f"اتصال "
                    f"{'برقرار' if result else 'برقرار نیست'}"
                ),
                response_time=response_ms / 1000,
                details={"source": source},
            )

        except Exception as e:
            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            log_error(
                f"❌ [HEALTH:collector:{platform}] "
                f"{response_ms:.0f}ms | "
                f"{type(e).__name__}: {e}"
            )
            return HealthCheckResult(
                component=f"collector_{platform}",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا: {e}",
                response_time=response_ms / 1000,
            )

    # ═══════════════════════════════════════════
    # چک سیستم
    # ═══════════════════════════════════════════
    async def check_system(self) -> HealthCheckResult:
        """بررسی CPU/RAM/Disk با ۳ سطح."""
        details: dict[str, Any] = {}
        status = HealthStatus.HEALTHY
        messages: list[str] = []

        # ─── CPU ───
        try:
            cpu = await asyncio.to_thread(
                psutil.cpu_percent, _CPU_SAMPLE_S
            )
            cpu = float(cpu)
            details["cpu_percent"] = cpu

            if cpu > _CPU_UNHEALTHY:
                status = HealthStatus.UNHEALTHY
                messages.append(f"CPU بحرانی: {cpu}%")
                log_error(
                    f"🔴 [HEALTH:sys:cpu] {cpu:.1f}% "
                    f"(>{_CPU_UNHEALTHY})"
                )
            elif cpu > _CPU_DEGRADED:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"CPU بالا: {cpu}%")
                log_warning(
                    f"⚠️ [HEALTH:sys:cpu] {cpu:.1f}% "
                    f"(>{_CPU_DEGRADED})"
                )
            else:
                log_info(
                    f"✅ [HEALTH:sys:cpu] {cpu:.1f}%"
                )
        except Exception as e:
            details["cpu_error"] = str(e)
            log_warning(
                f"⚠️ [HEALTH:sys:cpu] "
                f"{type(e).__name__}: {e}"
            )

        # ─── RAM ───
        try:
            mem = psutil.virtual_memory()
            details["ram_percent"] = mem.percent
            details["ram_available_mb"] = round(
                mem.available / (1024 * 1024), 2
            )

            if mem.percent > _RAM_UNHEALTHY:
                status = HealthStatus.UNHEALTHY
                messages.append(
                    f"RAM بحرانی: {mem.percent}%"
                )
                log_error(
                    f"🔴 [HEALTH:sys:ram] {mem.percent}% "
                    f"(>{_RAM_UNHEALTHY}) | "
                    f"free={details['ram_available_mb']}MB"
                )
            elif mem.percent > _RAM_DEGRADED:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"RAM بالا: {mem.percent}%")
                log_warning(
                    f"⚠️ [HEALTH:sys:ram] {mem.percent}% "
                    f"(>{_RAM_DEGRADED}) | "
                    f"free={details['ram_available_mb']}MB"
                )
            else:
                log_info(
                    f"✅ [HEALTH:sys:ram] {mem.percent}% | "
                    f"free={details['ram_available_mb']}MB"
                )
        except Exception as e:
            details["ram_error"] = str(e)
            log_warning(
                f"⚠️ [HEALTH:sys:ram] "
                f"{type(e).__name__}: {e}"
            )

        # ─── Disk ───
        try:
            disk_path = "C:\\" if os.name == "nt" else "/"
            disk = psutil.disk_usage(disk_path)
            details["disk_percent"] = disk.percent
            details["disk_free_gb"] = round(
                disk.free / (1024 ** 3), 2
            )

            if disk.percent > _DISK_UNHEALTHY:
                status = HealthStatus.UNHEALTHY
                messages.append(
                    f"دیسک بحرانی: {disk.percent}%"
                )
                log_error(
                    f"🔴 [HEALTH:sys:disk] "
                    f"{disk.percent}% "
                    f"(>{_DISK_UNHEALTHY}) | "
                    f"free={details['disk_free_gb']}GB"
                )
            elif disk.percent > _DISK_DEGRADED:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(
                    f"دیسک بالا: {disk.percent}%"
                )
                log_warning(
                    f"⚠️ [HEALTH:sys:disk] "
                    f"{disk.percent}% "
                    f"(>{_DISK_DEGRADED}) | "
                    f"free={details['disk_free_gb']}GB"
                )
            else:
                log_info(
                    f"✅ [HEALTH:sys:disk] "
                    f"{disk.percent}% | "
                    f"free={details['disk_free_gb']}GB"
                )
        except Exception as e:
            details["disk_error"] = str(e)
            log_warning(
                f"⚠️ [HEALTH:sys:disk] "
                f"{type(e).__name__}: {e}"
            )

        message = (
            ", ".join(messages)
            if messages else "سیستم سالم است"
        )
        if status == HealthStatus.HEALTHY:
            log_success("✅ [HEALTH:sys] همه چیز سالم")

        return HealthCheckResult(
            component="system",
            status=status,
            message=message,
            details=details,
        )

    # ═══════════════════════════════════════════
    # چک API خارجی
    # ═══════════════════════════════════════════
    async def check_external_api(
        self,
        name: str,
        url: str,
        timeout: float | None = None,
    ) -> HealthCheckResult:
        """بررسی سلامت یک API خارجی."""
        t_start = time.monotonic()
        use_timeout = (
            timeout if timeout is not None
            else _API_DEFAULT_TIMEOUT
        )

        try:
            # 🎯 FIX: ClientTimeout صریح
            client_timeout = aiohttp.ClientTimeout(
                total=use_timeout
            )

            async with aiohttp.ClientSession(
                timeout=client_timeout
            ) as session:
                async with session.get(url) as response:
                    response_ms = (
                        time.monotonic() - t_start
                    ) * 1000

                    if response.status == 200:
                        log_success(
                            f"✅ [HEALTH:api:{name}] 200 | "
                            f"{response_ms:.0f}ms"
                        )
                        return HealthCheckResult(
                            component=f"api_{name}",
                            status=HealthStatus.HEALTHY,
                            message=(
                                f"API پاسخ می‌دهد "
                                f"({response.status})"
                            ),
                            response_time=response_ms / 1000,
                            details={
                                "status_code": response.status,
                                "url": url,
                            },
                        )

                    log_warning(
                        f"⚠️ [HEALTH:api:{name}] "
                        f"status={response.status} | "
                        f"{response_ms:.0f}ms"
                    )
                    return HealthCheckResult(
                        component=f"api_{name}",
                        status=HealthStatus.DEGRADED,
                        message=(
                            f"API پاسخ می‌دهد اما "
                            f"status {response.status}"
                        ),
                        response_time=response_ms / 1000,
                        details={
                            "status_code": response.status,
                            "url": url,
                        },
                    )

        except TimeoutError:
            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            log_warning(
                f"⏰ [HEALTH:api:{name}] "
                f"timeout {use_timeout}s"
            )
            return HealthCheckResult(
                component=f"api_{name}",
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout ({use_timeout}s)",
                response_time=response_ms / 1000,
            )
        except aiohttp.ClientError as e:
            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            log_warning(
                f"⚠️ [HEALTH:api:{name}] "
                f"ClientError: {e} | "
                f"{response_ms:.0f}ms"
            )
            return HealthCheckResult(
                component=f"api_{name}",
                status=HealthStatus.UNHEALTHY,
                message=f"ClientError: {e}",
                response_time=response_ms / 1000,
            )
        except Exception as e:
            response_ms = (
                time.monotonic() - t_start
            ) * 1000
            log_error(
                f"❌ [HEALTH:api:{name}] "
                f"{type(e).__name__}: {e} | "
                f"{response_ms:.0f}ms"
            )
            return HealthCheckResult(
                component=f"api_{name}",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا: {e}",
                response_time=response_ms / 1000,
            )

    # ═══════════════════════════════════════════
    # وضعیت کلی + خلاصه
    # ═══════════════════════════════════════════
    def _get_overall_status(self) -> HealthStatus:
        """بدترین وضعیت برنده است."""
        if not self.results:
            return HealthStatus.UNKNOWN
        if any(
            r.status == HealthStatus.UNHEALTHY
            for r in self.results.values()
        ):
            return HealthStatus.UNHEALTHY
        if any(
            r.status == HealthStatus.DEGRADED
            for r in self.results.values()
        ):
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    def get_summary(self) -> dict[str, Any]:
        """خلاصهٔ سبک برای پاسخ سریع."""
        if not self.results:
            return {
                "status": "not_checked",
                "components": {},
            }
        return {
            "status": self._get_overall_status().value,
            "last_check": datetime.now().isoformat(),
            "components": {
                name: {
                    "status": r.status.value,
                    "message": r.message,
                    "response_time": r.response_time,
                }
                for name, r in self.results.items()
            },
        }

    async def health_check_endpoint(
        self,
    ) -> dict[str, Any]:
        """Endpoint وب (برای `/health`)."""
        result = await self.check_all()
        overall = result["overall_status"]
        return {
            "status": overall,
            "timestamp": datetime.now().isoformat(),
            "details": result,
        }

    # ═══════════════════════════════════════════
    # management
    # ═══════════════════════════════════════════
    def reset_results(self) -> int:
        """🎯 پاک کردن نتایج قبلی — برای تست."""
        n = len(self.results)
        self.results = {}
        log_info(
            f"🗑️ [HEALTH:reset] {n} نتیجه پاک شد"
        )
        return n

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "collectors_count": len(self._collectors),
            "collector_names": [
                getattr(c, "platform_name", "?")
                for c in self._collectors
            ],
            "external_apis_count": len(
                self._external_apis
            ),
            "external_api_names": list(
                self._external_apis.keys()
            ),
            "results_count": len(self.results),
            "db_connected": self.db is not None,
            "thresholds": {
                "cpu_degraded": _CPU_DEGRADED,
                "cpu_unhealthy": _CPU_UNHEALTHY,
                "ram_degraded": _RAM_DEGRADED,
                "ram_unhealthy": _RAM_UNHEALTHY,
                "disk_degraded": _DISK_DEGRADED,
                "disk_unhealthy": _DISK_UNHEALTHY,
            },
            "timeouts": {
                "api": _API_DEFAULT_TIMEOUT,
                "collector": _COLLECTOR_HEALTH_TIMEOUT,
                "db": _DB_TIMEOUT,
            },
        }

    def __repr__(self) -> str:
        return (
            f"<HealthChecker "
            f"collectors={len(self._collectors)} "
            f"apis={len(self._external_apis)} "
            f"db={'✅' if self.db else '—'}>"
        )


# ═══════════════════════════════════════════
# تابع کمکی
# ═══════════════════════════════════════════
async def run_health_check(
    db,
    collectors: list[Any] | None = None,
) -> dict[str, Any]:
    """اجرای سریع چک سلامت."""
    checker = HealthChecker(db)
    if collectors:
        for c in collectors:
            checker.register_collector(c)
    return await checker.check_all()


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "HealthChecker",
    "HealthStatus",
    "HealthCheckResult",
    "run_health_check",
]