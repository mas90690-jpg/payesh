"""
core/ops/health_check.py — سیستم بررسی سلامت (Health Check)

قابلیتها:
  - بررسی سلامت دیتابیس (اتصال واقعی + حجم فایل)
  - بررسی سلامت کلکتورها (check_health یا is_connected) با timeout
  - بررسی سلامت سیستم (CPU, RAM, Disk) با سه سطح
  - بررسی سلامت APIهای خارجی (HTTP status code)
  - گزارش وضعیت کلی (بدترین وضعیت برنده است)

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: check_collector حالا timeout ۱۰ ثانیه دارد
    (قبلاً اگه collector.check_health() hang میکرد،
     کل health check قفل میشد).
  - ✅ نسخهٔ ۲ هم حفظ شده
"""
from __future__ import annotations

import asyncio
import os
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

# ─── آستانههای هشدار/بحرانی (یکجا، قابل تنظیم از env) ───
_CPU_DEGRADED = float(os.getenv("HEALTH_CPU_DEGRADED", "80"))
_CPU_UNHEALTHY = float(os.getenv("HEALTH_CPU_UNHEALTHY", "90"))
_RAM_DEGRADED = float(os.getenv("HEALTH_RAM_DEGRADED", "85"))
_RAM_UNHEALTHY = float(os.getenv("HEALTH_RAM_UNHEALTHY", "95"))
_DISK_DEGRADED = float(os.getenv("HEALTH_DISK_DEGRADED", "85"))
_DISK_UNHEALTHY = float(os.getenv("HEALTH_DISK_UNHEALTHY", "95"))

# تایماوت پیشفرض برای چک APIهای خارجی
_API_DEFAULT_TIMEOUT = 10

# 🐛 FIX: timeout برای check_health کلکتورها
_COLLECTOR_HEALTH_TIMEOUT = float(
    os.getenv("HEALTH_COLLECTOR_TIMEOUT", "10")
)


class HealthStatus(Enum):
    """وضعیت سلامت — از بهترین به بدترین."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """نتیجهٔ یک چک سلامت."""

    component: str
    status: HealthStatus
    message: str = ""
    response_time: float = 0
    details: dict[str, Any] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "response_time": self.response_time,
            "details": self.details,
            "last_check": self.last_check.isoformat(),
        }


class HealthChecker:
    """سیستم بررسی سلامت — همهٔ اجزا بهصورت یکجا."""

    def __init__(self, db=None):
        self.db = db
        self.results: dict[str, HealthCheckResult] = {}
        self._collectors: list = []
        self._external_apis: dict[str, str] = {}
        log_info(
            f"🏥 [HEALTH] نمونه ساخته شد | "
            f"thresholds: CPU>{_CPU_DEGRADED}/{_CPU_UNHEALTHY} "
            f"RAM>{_RAM_DEGRADED}/{_RAM_UNHEALTHY} "
            f"Disk>{_DISK_DEGRADED}/{_DISK_UNHEALTHY} | "
            f"collector_timeout={_COLLECTOR_HEALTH_TIMEOUT}s"
        )

    # ═══════════════════════════════════════════
    # ثبت
    # ═══════════════════════════════════════════
    def register_collector(self, collector) -> None:
        """ثبت کلکتور برای بررسی سلامت (ضدتکراری)."""
        # ضدتکراری بر اساس platform_name
        name = getattr(collector, "platform_name", "unknown")
        if any(
            getattr(c, "platform_name", None) == name
            for c in self._collectors
        ):
            log_warning(
                f"⚠️ [HEALTH] کلکتور {name} قبلاً ثبت شده "
                f"— نادیده گرفته شد"
            )
            return
        self._collectors.append(collector)
        log_info(
            f"📝 [HEALTH] کلکتور {name} ثبت شد "
            f"(مجموع: {len(self._collectors)})"
        )

    def register_external_api(self, name: str, url: str) -> None:
        """ثبت API خارجی برای بررسی."""
        self._external_apis[name] = url
        log_info(
            f"📝 [HEALTH] API خارجی «{name}» ثبت شد → {url}"
        )

    # ═══════════════════════════════════════════
    # اجرای همهٔ چکها
    # ═══════════════════════════════════════════
    async def check_all(self) -> dict[str, Any]:
        """بررسی همهٔ اجزا — نتایج در self.results ذخیره میشوند."""
        log_info(
            f"🏥 [HEALTH] شروع چک کامل | "
            f"collectors={len(self._collectors)} "
            f"apis={len(self._external_apis)} "
            f"db={'✅' if self.db else '—'}"
        )
        start = datetime.now()

        # ریست نتایج قبلی (جلوگیری از رشد و آلودگی بین دورها)
        self.results = {}

        # ۱) دیتابیس
        try:
            self.results["database"] = await self.check_database()
        except Exception as e:
            log_error(
                f"❌ [HEALTH:db] چک دیتابیس استثنا داد: {e}"
            )
            self.results["database"] = HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"استثنا در چک دیتابیس: {e}",
            )

        # ۲) کلکتورها
        for collector in self._collectors:
            name = getattr(collector, "platform_name", "unknown")
            try:
                self.results[f"collector_{name}"] = (
                    await self.check_collector(collector)
                )
            except Exception as e:
                log_error(
                    f"❌ [HEALTH:collector:{name}] استثنا: {e}"
                )
                self.results[f"collector_{name}"] = HealthCheckResult(
                    component=f"collector_{name}",
                    status=HealthStatus.UNHEALTHY,
                    message=f"استثنا: {e}",
                )

        # ۳) سیستم
        try:
            self.results["system"] = await self.check_system()
        except Exception as e:
            log_error(f"❌ [HEALTH:system] استثنا: {e}")
            self.results["system"] = HealthCheckResult(
                component="system",
                status=HealthStatus.UNKNOWN,
                message=f"استثنا در چک سیستم: {e}",
            )

        # ۴) APIهای خارجی
        for name, url in self._external_apis.items():
            try:
                self.results[f"api_{name}"] = (
                    await self.check_external_api(name, url)
                )
            except Exception as e:
                log_error(
                    f"❌ [HEALTH:api:{name}] استثنا: {e}"
                )
                self.results[f"api_{name}"] = HealthCheckResult(
                    component=f"api_{name}",
                    status=HealthStatus.UNHEALTHY,
                    message=f"استثنا: {e}",
                )

        total_ms = (datetime.now() - start).total_seconds() * 1000
        overall = self._get_overall_status()

        # شمارش هر وضعیت (برای لاگ دقیق)
        counts = {
            "healthy": 0,
            "degraded": 0,
            "unhealthy": 0,
            "unknown": 0,
        }
        for r in self.results.values():
            counts[r.status.value] = counts.get(r.status.value, 0) + 1

        icon = {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.DEGRADED: "⚠️",
            HealthStatus.UNHEALTHY: "🔴",
            HealthStatus.UNKNOWN: "❓",
        }.get(overall, "❓")

        msg = (
            f"{icon} [HEALTH] چک کامل شد در {total_ms:.0f}ms | "
            f"وضعیت: {overall.value} | "
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
        """بررسی سلامت دیتابیس — اتصال واقعی + حجم فایل."""
        start = datetime.now()

        if not self.db:
            log_warning("⚠️ [HEALTH:db] دیتابیس ثبت نشده")
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNKNOWN,
                message="دیتابیس ثبت نشده است",
            )

        try:
            # اجرای یک کوئری ساده و بررسی نتیجه
            result = await self.db.execute("SELECT 1")
            row = await result.fetchone()

            # اگه نتیجه خالی بود، یعنی دیتابیس پاسخ نمیده
            if not row:
                response_ms = (
                    datetime.now() - start
                ).total_seconds() * 1000
                log_warning(
                    f"⚠️ [HEALTH:db] پاسخ خالی از SELECT 1 "
                    f"({response_ms:.0f}ms)"
                )
                return HealthCheckResult(
                    component="database",
                    status=HealthStatus.DEGRADED,
                    message="دیتابیس پاسخ خالی داد (شاید قفل/بازیابی)",
                    response_time=response_ms / 1000,
                )

            # بررسی حجم فایل
            details: dict[str, Any] = {"probe": "ok"}
            if (
                hasattr(self.db, "db_path")
                and os.path.exists(self.db.db_path)
            ):
                size_bytes = os.path.getsize(self.db.db_path)
                details["size_mb"] = round(
                    size_bytes / (1024 * 1024), 2
                )

            response_ms = (
                datetime.now() - start
            ).total_seconds() * 1000
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

        except Exception as e:
            response_ms = (
                datetime.now() - start
            ).total_seconds() * 1000
            log_error(
                f"❌ [HEALTH:db] خطا | {response_ms:.0f}ms | "
                f"{type(e).__name__}: {e}"
            )
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا در اتصال دیتابیس: {e}",
                response_time=response_ms / 1000,
            )

    # ═══════════════════════════════════════════
    # چک کلکتور
    # ═══════════════════════════════════════════
    async def check_collector(self, collector) -> HealthCheckResult:
        """
        بررسی سلامت یک کلکتور — اول check_health، بعد is_connected.

        🐛 FIX: حالا timeout ۱۰ ثانیه دارد (قبلاً اگه check_health
        hang میکرد، کل health check قفل میشد).
        """
        start = datetime.now()
        platform = getattr(collector, "platform_name", "unknown")

        try:
            # اولویت ۱: متد check_health (با timeout)
            if hasattr(collector, "check_health"):
                try:
                    result = await asyncio.wait_for(
                        collector.check_health(),
                        timeout=_COLLECTOR_HEALTH_TIMEOUT,
                    )
                    source = "check_health()"
                except TimeoutError:
                    response_ms = (
                        datetime.now() - start
                    ).total_seconds() * 1000
                    log_warning(
                        f"⏰ [HEALTH:collector:{platform}] "
                        f"timeout ({_COLLECTOR_HEALTH_TIMEOUT}s) | "
                        f"{response_ms:.0f}ms"
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
            elif hasattr(collector, "is_connected"):
                result = collector.is_connected
                source = "is_connected"
            else:
                result = getattr(collector, "_connected", False)
                source = "_connected"

            response_ms = (
                datetime.now() - start
            ).total_seconds() * 1000
            status = (
                HealthStatus.HEALTHY if result
                else HealthStatus.UNHEALTHY
            )
            icon = "✅" if result else "🔴"

            if result:
                log_success(
                    f"{icon} [HEALTH:collector:{platform}] متصل | "
                    f"{response_ms:.0f}ms | via {source}"
                )
            else:
                log_warning(
                    f"{icon} [HEALTH:collector:{platform}] قطع | "
                    f"via {source}"
                )

            return HealthCheckResult(
                component=f"collector_{platform}",
                status=status,
                message=(
                    f"اتصال {'برقرار' if result else 'برقرار نیست'}"
                ),
                response_time=response_ms / 1000,
                details={"source": source},
            )

        except Exception as e:
            response_ms = (
                datetime.now() - start
            ).total_seconds() * 1000
            log_error(
                f"❌ [HEALTH:collector:{platform}] خطا | "
                f"{response_ms:.0f}ms | {type(e).__name__}: {e}"
            )
            return HealthCheckResult(
                component=f"collector_{platform}",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا: {e}",
                response_time=response_ms / 1000,
            )

    # ═══════════════════════════════════════════
    # چک سیستم (CPU/RAM/Disk)
    # ═══════════════════════════════════════════
    async def check_system(self) -> HealthCheckResult:
        """بررسی سلامت سیستم — CPU non-blocking + RAM + Disk با ۳ سطح."""
        details: dict[str, Any] = {}
        status = HealthStatus.HEALTHY
        messages: list[str] = []

        # ─── CPU (non-blocking) ───
        try:
            cpu = await asyncio.to_thread(psutil.cpu_percent, 1.0)
            cpu = float(cpu)
            details["cpu_percent"] = cpu

            # ترتیب مهم: اول بحرانی، بعد هشدار
            if cpu > _CPU_UNHEALTHY:
                status = HealthStatus.UNHEALTHY
                messages.append(f"CPU بحرانی: {cpu}%")
                log_error(
                    f"🔴 [HEALTH:sys:cpu] بحرانی: {cpu:.1f}% "
                    f"(>{_CPU_UNHEALTHY})"
                )
            elif cpu > _CPU_DEGRADED:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"CPU بالا: {cpu}%")
                log_warning(
                    f"⚠️ [HEALTH:sys:cpu] بالا: {cpu:.1f}% "
                    f"(>{_CPU_DEGRADED})"
                )
            else:
                log_info(
                    f"✅ [HEALTH:sys:cpu] {cpu:.1f}% (طبیعی)"
                )
        except Exception as e:
            details["cpu_error"] = str(e)
            log_warning(
                f"⚠️ [HEALTH:sys:cpu] اندازهگیری ناموفق: {e}"
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
                messages.append(f"RAM بحرانی: {mem.percent}%")
                log_error(
                    f"🔴 [HEALTH:sys:ram] بحرانی: {mem.percent}% "
                    f"(>{_RAM_UNHEALTHY}) | "
                    f"free={details['ram_available_mb']}MB"
                )
            elif mem.percent > _RAM_DEGRADED:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"RAM بالا: {mem.percent}%")
                log_warning(
                    f"⚠️ [HEALTH:sys:ram] بالا: {mem.percent}% "
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
                f"⚠️ [HEALTH:sys:ram] اندازهگیری ناموفق: {e}"
            )

        # ─── Disk ───
        try:
            disk_path = "C:\\" if os.name == "nt" else "/"
            disk = psutil.disk_usage(disk_path)
            details["disk_percent"] = disk.percent
            details["disk_free_gb"] = round(
                disk.free / (1024**3), 2
            )

            if disk.percent > _DISK_UNHEALTHY:
                status = HealthStatus.UNHEALTHY
                messages.append(
                    f"فضای دیسک بحرانی: {disk.percent}%"
                )
                log_error(
                    f"🔴 [HEALTH:sys:disk] بحرانی: "
                    f"{disk.percent}% (>{_DISK_UNHEALTHY}) | "
                    f"free={details['disk_free_gb']}GB"
                )
            elif disk.percent > _DISK_DEGRADED:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"فضای دیسک کم: {disk.percent}%")
                log_warning(
                    f"⚠️ [HEALTH:sys:disk] بالا: {disk.percent}% "
                    f"(>{_DISK_DEGRADED}) | "
                    f"free={details['disk_free_gb']}GB"
                )
            else:
                log_info(
                    f"✅ [HEALTH:sys:disk] {disk.percent}% | "
                    f"free={details['disk_free_gb']}GB"
                )
        except Exception as e:
            details["disk_error"] = str(e)
            log_warning(
                f"⚠️ [HEALTH:sys:disk] اندازهگیری ناموفق: {e}"
            )

        # خلاصه
        message = (
            ", ".join(messages) if messages else "سیستم سالم است"
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
        self, name: str, url: str, timeout: int = _API_DEFAULT_TIMEOUT
    ) -> HealthCheckResult:
        """بررسی سلامت یک API خارجی — با timeout مشخص."""
        start = datetime.now()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=timeout
                ) as response:
                    response_ms = (
                        datetime.now() - start
                    ).total_seconds() * 1000
                    if response.status == 200:
                        log_success(
                            f"✅ [HEALTH:api:{name}] 200 | "
                            f"{response_ms:.0f}ms"
                        )
                        return HealthCheckResult(
                            component=f"api_{name}",
                            status=HealthStatus.HEALTHY,
                            message=(
                                f"API پاسخ میدهد "
                                f"(status {response.status})"
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
                            f"API پاسخ میدهد اما "
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
                datetime.now() - start
            ).total_seconds() * 1000
            log_warning(
                f"⏰ [HEALTH:api:{name}] Timeout بعد از {timeout}s"
            )
            return HealthCheckResult(
                component=f"api_{name}",
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout بعد از {timeout} ثانیه",
                response_time=response_ms / 1000,
            )
        except Exception as e:
            response_ms = (
                datetime.now() - start
            ).total_seconds() * 1000
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
            return {"status": "not_checked", "components": {}}
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

    async def health_check_endpoint(self) -> dict[str, Any]:
        """Endpoint وب (برای /health)."""
        result = await self.check_all()
        overall = result["overall_status"]
        return {
            "status": overall,
            "timestamp": datetime.now().isoformat(),
            "details": result,
        }


# ═══════════════════════════════════════════
# تابع کمکی
# ═══════════════════════════════════════════
async def run_health_check(
    db, collectors=None
) -> dict[str, Any]:
    """اجرای سریع چک سلامت — برای استفاده در main.py و تست."""
    checker = HealthChecker(db)
    if collectors:
        for collector in collectors:
            checker.register_collector(collector)
    return await checker.check_all()
