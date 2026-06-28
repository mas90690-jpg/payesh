# -*- coding: utf-8 -*-
import asyncio
import aiohttp
import psutil
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from core.logger import log_info, log_success, log_error, log_warning


class HealthStatus(Enum):
    """وضعیت سلامت"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """نتیجه یک چک سلامت"""
    component: str
    status: HealthStatus
    message: str = ""
    response_time: float = 0
    details: Dict[str, Any] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "response_time": self.response_time,
            "details": self.details,
            "last_check": self.last_check.isoformat()
        }


class HealthChecker:
    """
    سیستم بررسی سلامت (Health Check)
    
    قابلیت‌ها:
    - بررسی سلامت دیتابیس
    - بررسی سلامت کلکتورها
    - بررسی سلامت سیستم (CPU, RAM, Disk)
    - بررسی سلامت API‌های خارجی
    - گزارش وضعیت کلی
    """
    
    def __init__(self, db=None):
        self.db = db
        self.results: Dict[str, HealthCheckResult] = {}
        self._collectors: List = []
        self._external_apis: Dict[str, str] = {}
        
    def register_collector(self, collector) -> None:
        """ثبت کلکتور برای بررسی سلامت"""
        self._collectors.append(collector)
        log_info(f"کلکتور {collector.platform_name} برای بررسی سلامت ثبت شد")
    
    def register_external_api(self, name: str, url: str) -> None:
        """ثبت API خارجی برای بررسی"""
        self._external_apis[name] = url
    
    async def check_all(self) -> Dict[str, Any]:
        """
        بررسی همه اجزا
        
        Returns:
            Dict: وضعیت کلی و جزئیات هر بخش
        """
        log_info("🏥 شروع چک کامل سلامت سیستم...")
        
        start_time = datetime.now()
        
        # بررسی دیتابیس
        db_result = await self.check_database()
        self.results["database"] = db_result
        
        # بررسی کلکتورها
        for collector in self._collectors:
            collector_result = await self.check_collector(collector)
            self.results[f"collector_{collector.platform_name}"] = collector_result
        
        # بررسی سیستم
        system_result = self.check_system()
        self.results["system"] = system_result
        
        # بررسی APIهای خارجی
        for api_name, api_url in self._external_apis.items():
            api_result = await self.check_external_api(api_name, api_url)
            self.results[f"api_{api_name}"] = api_result
        
        total_time = (datetime.now() - start_time).total_seconds()
        
        # تعیین وضعیت کلی
        overall_status = self._get_overall_status()
        
        log_success(f"✅ چک سلامت کامل شد در {total_time:.2f} ثانیه - وضعیت: {overall_status.value}")
        
        return {
            "overall_status": overall_status.value,
            "timestamp": datetime.now().isoformat(),
            "total_time": total_time,
            "components": {name: r.to_dict() for name, r in self.results.items()}
        }
    
    async def check_database(self) -> HealthCheckResult:
        """بررسی سلامت دیتابیس"""
        start_time = datetime.now()
        
        if not self.db:
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNKNOWN,
                message="دیتابیس ثبت نشده است"
            )
        
        try:
            # اجرای یک query ساده
            result = await self.db.execute("SELECT 1")
            
            # بررسی حجم دیتابیس
            details = {}
            if hasattr(self.db, 'db_path') and os.path.exists(self.db.db_path):
                size = os.path.getsize(self.db.db_path)
                details["size_mb"] = round(size / (1024 * 1024), 2)
            
            response_time = (datetime.now() - start_time).total_seconds()
            
            return HealthCheckResult(
                component="database",
                status=HealthStatus.HEALTHY,
                message="دیتابیس سالم است",
                response_time=response_time,
                details=details
            )
            
        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds()
            log_error(f"❌ خطا در دیتابیس: {e}")
            
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا در اتصال دیتابیس: {e}",
                response_time=response_time
            )
    
    async def check_collector(self, collector) -> HealthCheckResult:
        """بررسی سلامت یک کلکتور"""
        start_time = datetime.now()
        platform = getattr(collector, 'platform_name', 'unknown')
        
        try:
            # بررسی متد health_check اگر وجود داشته باشد
            if hasattr(collector, 'check_health'):
                result = await collector.check_health()
            elif hasattr(collector, 'is_connected'):
                result = collector.is_connected
            else:
                result = getattr(collector, '_connected', False)
            
            response_time = (datetime.now() - start_time).total_seconds()
            
            status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
            
            return HealthCheckResult(
                component=f"collector_{platform}",
                status=status,
                message=f"اتصال {'برقرار' if result else 'برقرار نیست'}",
                response_time=response_time
            )
            
        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds()
            log_error(f"❌ خطا در کلکتور {platform}: {e}")
            
            return HealthCheckResult(
                component=f"collector_{platform}",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا: {e}",
                response_time=response_time
            )
    
    def check_system(self) -> HealthCheckResult:
        """بررسی سلامت سیستم (CPU, RAM, Disk)"""
        details = {}
        status = HealthStatus.HEALTHY
        messages = []
        
        # بررسی CPU
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            details["cpu_percent"] = cpu_percent
            if cpu_percent > 80:
                status = HealthStatus.DEGRADED
                messages.append(f"CPU بالا: {cpu_percent}%")
            elif cpu_percent > 90:
                status = HealthStatus.UNHEALTHY
                messages.append(f"CPU بحرانی: {cpu_percent}%")
        except Exception as e:
            details["cpu_error"] = str(e)
        
        # بررسی RAM
        try:
            mem = psutil.virtual_memory()
            details["ram_percent"] = mem.percent
            details["ram_available_mb"] = round(mem.available / (1024 * 1024), 2)
            if mem.percent > 85:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"RAM بالا: {mem.percent}%")
        except Exception as e:
            details["ram_error"] = str(e)
        
        # بررسی Disk
        try:
            disk = psutil.disk_usage('/')
            details["disk_percent"] = disk.percent
            details["disk_free_gb"] = round(disk.free / (1024 ** 3), 2)
            if disk.percent > 85:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                messages.append(f"فضای دیسک کم: {disk.percent}%")
        except Exception as e:
            details["disk_error"] = str(e)
        
        return HealthCheckResult(
            component="system",
            status=status,
            message=", ".join(messages) if messages else "سیستم سالم است",
            details=details
        )
    
    async def check_external_api(self, name: str, url: str, timeout: int = 10) -> HealthCheckResult:
        """بررسی سلامت یک API خارجی"""
        start_time = datetime.now()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as response:
                    response_time = (datetime.now() - start_time).total_seconds()
                    
                    if response.status == 200:
                        return HealthCheckResult(
                            component=f"api_{name}",
                            status=HealthStatus.HEALTHY,
                            message=f"API پاسخ می‌دهد (status {response.status})",
                            response_time=response_time
                        )
                    else:
                        return HealthCheckResult(
                            component=f"api_{name}",
                            status=HealthStatus.DEGRADED,
                            message=f"API پاسخ می‌دهد اما status {response.status}",
                            response_time=response_time
                        )
                        
        except asyncio.TimeoutError:
            response_time = (datetime.now() - start_time).total_seconds()
            return HealthCheckResult(
                component=f"api_{name}",
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout بعد از {timeout} ثانیه",
                response_time=response_time
            )
        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds()
            return HealthCheckResult(
                component=f"api_{name}",
                status=HealthStatus.UNHEALTHY,
                message=f"خطا: {e}",
                response_time=response_time
            )
    
    def _get_overall_status(self) -> HealthStatus:
        """تعیین وضعیت کلی بر اساس نتایج اجزا"""
        if not self.results:
            return HealthStatus.UNKNOWN
        
        unhealthy_count = sum(1 for r in self.results.values() if r.status == HealthStatus.UNHEALTHY)
        degraded_count = sum(1 for r in self.results.values() if r.status == HealthStatus.DEGRADED)
        
        if unhealthy_count > 0:
            return HealthStatus.UNHEALTHY
        elif degraded_count > 0:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    def get_summary(self) -> Dict[str, Any]:
        """دریافت خلاصه وضعیت سلامت"""
        if not self.results:
            return {"status": "not_checked", "components": {}}
        
        return {
            "status": self._get_overall_status().value,
            "last_check": datetime.now().isoformat(),
            "components": {
                name: {
                    "status": r.status.value,
                    "message": r.message,
                    "response_time": r.response_time
                }
                for name, r in self.results.items()
            }
        }
    
    async def health_check_endpoint(self) -> Dict[str, Any]:
        """
        Endpoint برای API سلامت (برای استفاده در وب سرویس)
        """
        result = await self.check_all()
        return {
            "status": "ok" if result["overall_status"] == "healthy" else "degraded",
            "timestamp": datetime.now().isoformat(),
            "details": result
        }


# تابع کمکی برای استفاده در main.py
async def run_health_check(db, collectors=None):
    """اجرای سریع چک سلامت"""
    checker = HealthChecker(db)
    
    if collectors:
        for collector in collectors:
            checker.register_collector(collector)
    
    return await checker.check_all()