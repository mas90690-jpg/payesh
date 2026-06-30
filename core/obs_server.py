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

# صفحهٔ چتِ محلی (Persian/RTL) — کاملاً آفلاین؛ با هوشِ لوکال و دادهٔ ربات حرف می‌زنی
_CHAT_HTML = """<!DOCTYPE html><html dir="rtl" lang="fa"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>چت با ربات</title><style>
body{font-family:Tahoma,Arial;background:#0f172a;color:#e2e8f0;margin:0;display:flex;flex-direction:column;height:100vh}
#h{background:#1e293b;padding:12px 16px;font-weight:bold;border-bottom:2px solid #27ae60}
#m{flex:1;overflow-y:auto;padding:16px}
.b{max-width:80%;padding:10px 14px;border-radius:12px;margin:8px 0;white-space:pre-wrap;line-height:1.7}
.u{background:#2563eb;margin-right:auto}
.a{background:#1e293b;margin-left:auto;border:1px solid #334155}
#f{display:flex;padding:12px;background:#1e293b;gap:8px}
#t{flex:1;padding:12px;border-radius:10px;border:none;font-size:15px;font-family:inherit}
#s{padding:12px 20px;border:none;border-radius:10px;background:#27ae60;color:#fff;cursor:pointer;font-size:15px}
</style></head><body>
<div id="h">🤖 چت با ربات پایش سرنخ (لوکال — بدونِ نت)</div>
<div id="m"></div>
<form id="f"><input id="t" placeholder="بنویس... مثلاً: وضعیت، سلامت، آخرین سرنخ، یا هر سؤال" autocomplete="off">
<button id="s">ارسال</button></form>
<script>
const m=document.getElementById('m'),t=document.getElementById('t'),f=document.getElementById('f');
function add(txt,cls){const d=document.createElement('div');d.className='b '+cls;d.textContent=txt;m.appendChild(d);m.scrollTop=m.scrollHeight;return d;}
add('سلام مدیر! آماده‌ام. هر چیزی بپرس.','a');
f.onsubmit=async(e)=>{e.preventDefault();const q=t.value.trim();if(!q)return;add(q,'u');t.value='';
const w=add('...','a');
try{const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:q})});
const j=await r.json();w.textContent=j.reply||'(بدون جواب)';}
catch(err){w.textContent='❌ خطا در ارتباط';}};
</script></body></html>"""


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
                f"endpoints: /health  /metrics  /chat (چت با هوشِ لوکال)\n")
        return web.Response(text=body, content_type="text/plain")

    # چتِ محلیِ هوش (روی لپ‌تاپ، بدونِ نت/بله) — همیشه روی http://localhost:PORT/chat
    _admin = {"h": None}

    def _get_admin():
        if _admin["h"] is None:
            from handlers.admin_handler import AdminHandler
            _admin["h"] = AdminHandler(db)
        return _admin["h"]

    async def chat_page(request):
        return web.Response(text=_CHAT_HTML, content_type="text/html")

    async def chat_api(request):
        try:
            data = await request.json()
            reply = await _get_admin().process_text(data.get("message", ""))
        except Exception as e:
            reply = f"❌ خطا: {e}"
        return web.json_response({"reply": reply})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/chat", chat_page)
    app.router.add_post("/chat", chat_api)

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
