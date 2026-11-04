"""پکیج core.ops — عملیات زیرساختی (Health, Monitor, OBS, Self-Monitor).

اجزا:
  - HealthChecker, HealthStatus, HealthCheckResult, run_health_check
  - SystemMonitor, PerformanceMonitor, Metric, Alert
  - start_obs_server, render_prometheus, server_info
  - SelfMonitor, start_self_monitor

نحوهٔ استفاده:
    from core.ops import HealthChecker, SystemMonitor
    from core.ops import start_obs_server, start_self_monitor

نسخهٔ ۱ (این نسخه):
  - 🎯 `__all__` کامل با همهٔ اجزای ops.
  - 🐛 FIX: محتوای نامعتبر قبلی (`0`) حذف شد.
  - 🎯 import با try/except برای هر ماژول (یک خطا کل پکیج
    را نترکاند).
  - 🎯 docstring کامل با مثال.
"""
from __future__ import annotations


# ═══════════════════════════════════════════
# Health Check
# ═══════════════════════════════════════════
try:
    from core.ops.health_check import (
        HealthChecker,
        HealthCheckResult,
        HealthStatus,
        run_health_check,
    )
except Exception as e:
    import sys
    try:
        print(
            f"⚠️ [OPS:init] import health_check | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
    except Exception:
        pass
    HealthChecker = None  # type: ignore
    HealthCheckResult = None  # type: ignore
    HealthStatus = None  # type: ignore
    run_health_check = None  # type: ignore


# ═══════════════════════════════════════════
# Monitor
# ═══════════════════════════════════════════
try:
    from core.ops.monitor import (
        Alert,
        Metric,
        PerformanceMonitor,
        SystemMonitor,
    )
except Exception as e:
    import sys
    try:
        print(
            f"⚠️ [OPS:init] import monitor | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
    except Exception:
        pass
    Alert = None  # type: ignore
    Metric = None  # type: ignore
    PerformanceMonitor = None  # type: ignore
    SystemMonitor = None  # type: ignore


# ═══════════════════════════════════════════
# OBS Server
# ═══════════════════════════════════════════
try:
    from core.ops.obs_server import (
        render_prometheus,
        server_info,
        start_obs_server,
    )
except Exception as e:
    import sys
    try:
        print(
            f"⚠️ [OPS:init] import obs_server | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
    except Exception:
        pass
    render_prometheus = None  # type: ignore
    server_info = None  # type: ignore
    start_obs_server = None  # type: ignore


# ═══════════════════════════════════════════
# Self-Monitor
# ═══════════════════════════════════════════
try:
    from core.ops.self_monitor import (
        SelfMonitor,
        start_self_monitor,
    )
except Exception as e:
    import sys
    try:
        print(
            f"⚠️ [OPS:init] import self_monitor | "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
    except Exception:
        pass
    SelfMonitor = None  # type: ignore
    start_self_monitor = None  # type: ignore


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    # Health Check
    "HealthChecker",
    "HealthStatus",
    "HealthCheckResult",
    "run_health_check",
    # Monitor
    "SystemMonitor",
    "PerformanceMonitor",
    "Metric",
    "Alert",
    # OBS Server
    "start_obs_server",
    "render_prometheus",
    "server_info",
    # Self-Monitor
    "SelfMonitor",
    "start_self_monitor",
]
