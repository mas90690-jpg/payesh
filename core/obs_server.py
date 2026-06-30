# -*- coding: utf-8 -*-
"""
Lightweight observability HTTP server (aiohttp) — exposes:
    GET /health   → JSON health (200 healthy/degraded, 503 unhealthy)  [for uptime probes / k8s]
    GET /metrics  → Prometheus text exposition format                  [for Prometheus/Grafana]
    GET /         → tiny human status page

No extra dependency (aiohttp is already required; Prometheus text is rendered manually).
Enable/port via .env:  OBS_ENABLED (default true), OBS_HOST (0.0.0.0), OBS_PORT (8787)
"""
import os
import sqlite3
from datetime import datetime

from core.logger import log_success, log_error, log_warning

_START = datetime.now()


# ==================================================
# Pure helpers (testable without a server)
# ==================================================
def render_prometheus(counts: dict, sys_stats: dict, collectors_up: dict) -> str:
    """Render metrics in Prometheus text exposition format."""
    out = []

    def g(name, value, help_, typ="gauge"):
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {typ}")
        out.append(f"{name} {value}")

    g("app_messages_total", counts.get("messages", 0), "Total stored messages", "counter")
    g("app_leads_total", counts.get("leads", 0), "Total leads (keyword matches)", "counter")
    g("app_hot_leads_total", counts.get("hot", 0), "Hot (high-intent) leads", "counter")

    out.append("# HELP app_leads_by_platform Leads per platform")
    out.append("# TYPE app_leads_by_platform gauge")
    for p, n in (counts.get("by_platform") or {}).items():
        out.append(f'app_leads_by_platform{{platform="{p}"}} {n}')

    g("system_cpu_percent", round(sys_stats.get("cpu", 0), 1), "Process host CPU %")
    g("system_ram_percent", round(sys_stats.get("ram", 0), 1), "Host RAM %")
    g("app_uptime_seconds", int(sys_stats.get("uptime", 0)), "Uptime in seconds", "counter")

    out.append("# HELP collector_up Collector connected (1=up,0=down)")
    out.append("# TYPE collector_up gauge")
    for p, up in (collectors_up or {}).items():
        out.append(f'collector_up{{platform="{p}"}} {1 if up else 0}')

    return "\n".join(out) + "\n"


async def _gather_counts(db) -> dict:
    counts = {"messages": 0, "leads": 0, "hot": 0, "by_platform": {}}
    try:
        tc = await db.get_total_count()
        counts["messages"], counts["leads"] = tc.get("messages", 0), tc.get("leads", 0)
    except Exception:
        pass
    # richer numbers when SQLite (the default)
    path = getattr(db, "db_path", None)
    if path and os.path.exists(path):
        try:
            c = sqlite3.connect(path)
            cur = c.cursor()
            counts["hot"] = cur.execute(
                "SELECT COUNT(*) FROM messages WHERE opportunity_type='داغ'").fetchone()[0]
            for p, n in cur.execute(
                    "SELECT platform, COUNT(*) FROM messages WHERE keywords!='' GROUP BY platform"):
                counts["by_platform"][p] = n
            c.close()
        except Exception:
            pass
    return counts


def _sys_stats() -> dict:
    try:
        import psutil
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "uptime": (datetime.now() - _START).total_seconds(),
        }
    except Exception:
        return {"cpu": 0, "ram": 0, "uptime": (datetime.now() - _START).total_seconds()}


# ==================================================
# Server
# ==================================================
async def start_obs_server(db, health_checker=None, collectors=None):
    """Start the /health + /metrics server. Returns the aiohttp runner (or None)."""
    if os.getenv("OBS_ENABLED", "true").strip().lower() != "true":
        log_warning("Observability server disabled (OBS_ENABLED=false)")
        return None
    try:
        from aiohttp import web
    except ImportError:
        log_error("aiohttp not installed; observability server skipped")
        return None

    collectors = collectors or []
    host = os.getenv("OBS_HOST", "0.0.0.0")
    port = int(os.getenv("OBS_PORT", "8787"))

    async def health(request):
        if health_checker is not None:
            try:
                result = await health_checker.check_all()
                code = 200 if result.get("overall_status") in ("healthy", "degraded") else 503
                return web.json_response(result, status=code)
            except Exception as e:
                return web.json_response({"overall_status": "unhealthy", "error": str(e)}, status=503)
        # fallback: minimal liveness
        return web.json_response({"overall_status": "healthy", "uptime": _sys_stats()["uptime"]})

    async def metrics(request):
        counts = await _gather_counts(db)
        ups = {getattr(c, "platform_name", "?"): getattr(c, "is_connected", False) for c in collectors}
        text = render_prometheus(counts, _sys_stats(), ups)
        return web.Response(text=text, content_type="text/plain")

    async def index(request):
        counts = await _gather_counts(db)
        s = _sys_stats()
        body = (f"Srooyesh Lead-Monitor — OK\n"
                f"uptime: {int(s['uptime'])}s | cpu: {s['cpu']}% | ram: {s['ram']}%\n"
                f"messages: {counts['messages']} | leads: {counts['leads']} | hot: {counts['hot']}\n"
                f"endpoints: /health  /metrics\n")
        return web.Response(text=body, content_type="text/plain")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        log_success(f"Observability server on http://{host}:{port}  (/health, /metrics)")
    except Exception as e:
        log_error(f"Could not start observability server on :{port} — {e}")
        return None
    return runner
