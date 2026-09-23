"""
Observability HTTP server — سبک، بدون وابستگی اضافه (aiohttp موجود است).

Endpointها:
    GET  /health   → JSON health (200 healthy/degraded, 503 unhealthy)
    GET  /metrics  → Prometheus text exposition
    GET  /         → صفحهٔ وضعیت انسانی
    GET  /chat     → چتِ محلی با هوش (بدون نیاز به اینترنت/بله)
    POST /chat     → JSON {message} → {reply}

تنظیمات از .env:
    OBS_ENABLED (default true), OBS_HOST (0.0.0.0), OBS_PORT (8787)

نسخهٔ ۵ (این نسخه):
  - 🐛 FIX بحرانی: _periodic_cleanup دیگر سرور را ۲ دقیقه بلوکه نمی‌کند.
    (قبلاً `lambda app: asyncio.create_task(...)` یک Task برمی‌گرداند
    که aiohttp در on_startup منتظر آن می‌ماند → سرور دیر بالا می‌آمد.)
    حالا تسک به‌درستی در پس‌زمینه اجرا می‌شود و مرجعش نگه داشته می‌شود.
  - 🐛 FIX: تسک پس‌زمینه در on_cleanup لغو می‌شود (بدون نشت).
  - 🎯 لاگ timing برای زمان راه‌اندازی سرور.
  - (به‌همراه تمام FIXهای نسخهٔ ۴)
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── ثابت‌ها ───
_SERVER_START = datetime.now()

_PLATFORM_ORDER = (
    "eitaa", "bale", "rubika", "telegram",
    "whatsapp", "web", "linkedin",
)

_LOG_PREVIEW_LEN = 80

# ─── سقف‌ها ───
def _safe_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default

def _safe_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default

_MAX_CHAT_LEN = _safe_int("OBS_CHAT_MAX_LEN", 2000)
_MAX_BODY_BYTES = _safe_int("OBS_MAX_BODY_BYTES", 10000)
_RATE_LIMIT_WINDOW_S = _safe_int("OBS_RATE_WINDOW", 60)
_RATE_LIMIT_MAX = _safe_int("OBS_RATE_MAX", 30)  # per-IP per-window

# ─── cache intervals ───
_STATS_CACHE_S = _safe_float("OBS_STATS_CACHE_S", 10.0)
_SYS_CACHE_S = _safe_float("OBS_SYS_CACHE_S", 5.0)
_HEALTH_CACHE_S = _safe_float("OBS_HEALTH_CACHE_S", 30.0)
_COUNTS_TIMEOUT_S = _safe_float("OBS_COUNTS_TIMEOUT_S", 5.0)

# فاصلهٔ پاک‌سازی bucketهای rate limiter (ثانیه)
_RATE_CLEANUP_INTERVAL_S = 300.0


# ═══════════════════════════════════════════
# HTML صفحهٔ چت
# ═══════════════════════════════════════════
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


# ═══════════════════════════════════════════
# Prometheus renderer
# ═══════════════════════════════════════════
def _prom_escape(value: str) -> str:
    """escape مقدار label برای Prometheus."""
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace("\n", "\\n")
    s = s.replace('"', '\\"')
    return s


def _prom_metric_value(value: Any) -> str:
    """مقدار عددی امن برای Prometheus (NaN/Inf نه)."""
    try:
        f = float(value)
        if f != f:  # NaN
            return "0"
        if f == float("inf") or f == float("-inf"):
            return "0"
        return str(f)
    except (TypeError, ValueError):
        return "0"


def render_prometheus(
    counts: dict, sys_stats: dict, collectors_up: dict
) -> str:
    """Prometheus text exposition — label escape + عدد امن."""
    out: list[str] = []

    def g(name: str, value: Any, help_: str, typ: str = "gauge") -> None:
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {typ}")
        out.append(f"{name} {_prom_metric_value(value)}")

    def g_labeled(
        name: str, value: Any, labels: dict[str, str],
        help_: str, typ: str = "gauge",
    ) -> None:
        label_str = ",".join(
            f'{k}="{_prom_escape(v)}"' for k, v in labels.items()
        )
        out.append(f"{name}{{{label_str}}} {_prom_metric_value(value)}")

    g(
        "app_messages_total", counts.get("messages", 0),
        "Total stored messages", "counter",
    )
    g(
        "app_leads_total", counts.get("leads", 0),
        "Total leads (keyword matches)", "counter",
    )
    g(
        "app_hot_leads_total", counts.get("hot", 0),
        "Hot (high-intent) leads", "counter",
    )

    # per-platform (all-time)
    by_platform = counts.get("by_platform") or {}
    out.append(
        "# HELP app_leads_by_platform Leads per platform (all-time)"
    )
    out.append("# TYPE app_leads_by_platform gauge")
    for p in _PLATFORM_ORDER:
        g_labeled(
            "app_leads_by_platform", by_platform.get(p, 0),
            {"platform": p}, "",
        )

    # per-platform (last 24h)
    by_24h = counts.get("by_platform_24h") or {}
    out.append(
        "# HELP app_leads_by_platform_last_24h "
        "Leads per platform (last 24h)"
    )
    out.append("# TYPE app_leads_by_platform_last_24h gauge")
    for p in _PLATFORM_ORDER:
        g_labeled(
            "app_leads_by_platform_last_24h", by_24h.get(p, 0),
            {"platform": p}, "",
        )

    g(
        "system_cpu_percent",
        round(float(sys_stats.get("cpu", 0) or 0), 1),
        "Process host CPU %",
    )
    g(
        "system_ram_percent",
        round(float(sys_stats.get("ram", 0) or 0), 1),
        "Host RAM %",
    )
    g(
        "app_uptime_seconds", int(sys_stats.get("uptime", 0) or 0),
        "Uptime in seconds", "counter",
    )

    # collector_up
    ups = collectors_up or {}
    out.append(
        "# HELP collector_up Collector connected (1=up,0=down)"
    )
    out.append("# TYPE collector_up gauge")
    for p in _PLATFORM_ORDER:
        g_labeled(
            "collector_up", 1 if ups.get(p) else 0,
            {"platform": p}, "",
        )

    return "\n".join(out) + "\n"


# ═══════════════════════════════════════════
# cache helpers (thread-safe)
# ═══════════════════════════════════════════
class _TTLCache:
    """کش ساده با TTL — thread-safe."""

    def __init__(self, ttl_s: float):
        self.ttl = ttl_s
        self._value: Any = None
        self._time: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> Any | None:
        with self._lock:
            if (
                self._value is not None
                and (time.monotonic() - self._time) < self.ttl
            ):
                return self._value
        return None

    def set(self, value: Any) -> None:
        with self._lock:
            self._value = value
            self._time = time.monotonic()

    def invalidate(self) -> None:
        with self._lock:
            self._value = None
            self._time = 0.0


_counts_cache = _TTLCache(_STATS_CACHE_S)
_sys_cache = _TTLCache(_SYS_CACHE_S)
_health_cache = _TTLCache(_HEALTH_CACHE_S)


# ═══════════════════════════════════════════
# آمار
# ═══════════════════════════════════════════
async def _gather_counts(db) -> dict:
    """آمار برای متریک‌ها — با cache + timeout + لاگ خطا."""
    cached = _counts_cache.get()
    if cached is not None:
        return cached

    try:
        counts = await asyncio.wait_for(
            _gather_counts_inner(db),
            timeout=_COUNTS_TIMEOUT_S,
        )
        _counts_cache.set(counts)
        return counts
    except TimeoutError:
        log_warning(
            f"⏰ [OBS:counts] timeout ({_COUNTS_TIMEOUT_S}s) — "
            f"برگشت مقادیر پایه"
        )
    except Exception as e:
        log_warning(
            f"⚠️ [OBS:counts] {type(e).__name__}: {e}"
        )

    return {
        "messages": 0,
        "leads": 0,
        "hot": 0,
        "by_platform": dict.fromkeys(_PLATFORM_ORDER, 0),
        "by_platform_24h": dict.fromkeys(_PLATFORM_ORDER, 0),
    }


async def _gather_counts_inner(db) -> dict:
    """قلب _gather_counts (بدون cache/timeout)."""
    counts: dict[str, Any] = {
        "messages": 0,
        "leads": 0,
        "hot": 0,
        "by_platform": dict.fromkeys(_PLATFORM_ORDER, 0),
        "by_platform_24h": dict.fromkeys(_PLATFORM_ORDER, 0),
    }

    # ─── مسیر سریع: db.get_total_count ───
    try:
        tc = await db.get_total_count()
        counts["messages"] = int(tc.get("messages", 0) or 0)
        counts["leads"] = int(tc.get("leads", 0) or 0)
    except Exception as e:
        log_warning(
            f"⚠️ [OBS:counts] get_total_count | "
            f"{type(e).__name__}: {e}"
        )

    # ─── مسیر SQLite ───
    path = getattr(db, "db_path", None)
    if not path or not os.path.exists(path):
        log_info(
            "ℹ️ [OBS:counts] SQLite مسیر نیست — فقط مقادیر پایه"
        )
        return counts

    # اجرای SQL در thread جدا (جلوگیری از بلاک event loop)
    try:
        counts = await asyncio.to_thread(
            _gather_counts_sqlite, path, counts
        )
    except Exception as e:
        log_warning(
            f"⚠️ [OBS:counts] sqlite thread | "
            f"{type(e).__name__}: {e}"
        )

    return counts


def _gather_counts_sqlite(path: str, counts: dict) -> dict:
    """کوئری‌های SQLite (sync، در thread)."""
    try:
        c = sqlite3.connect(path, timeout=3.0)
        try:
            cur = c.cursor()

            # hot
            try:
                counts["hot"] = int(
                    cur.execute(
                        "SELECT COUNT(*) FROM messages "
                        "WHERE opportunity_type='داغ'"
                    ).fetchone()[0] or 0
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [OBS:counts:hot] {type(e).__name__}: {e}"
                )

            # per-platform all-time
            try:
                for p, n in cur.execute(
                    "SELECT platform, COUNT(*) FROM messages "
                    "WHERE keywords!='' GROUP BY platform"
                ):
                    if p in counts["by_platform"]:
                        counts["by_platform"][p] = int(n)
            except Exception as e:
                log_warning(
                    f"⚠️ [OBS:counts:by_platform] "
                    f"{type(e).__name__}: {e}"
                )

            # per-platform last 24h
            try:
                since = (
                    datetime.now() - timedelta(hours=24)
                ).strftime("%Y-%m-%d %H:%M:%S")
                for p, n in cur.execute(
                    "SELECT platform, COUNT(*) FROM messages "
                    "WHERE keywords!='' AND timestamp >= ? "
                    "GROUP BY platform",
                    (since,),
                ):
                    if p in counts["by_platform_24h"]:
                        counts["by_platform_24h"][p] = int(n)
            except Exception as e:
                log_warning(
                    f"⚠️ [OBS:counts:by_24h] "
                    f"{type(e).__name__}: {e}"
                )
        finally:
            c.close()
    except Exception as e:
        log_warning(
            f"⚠️ [OBS:counts] connect | {type(e).__name__}: {e} | "
            f"path={path}"
        )
    return counts


def _sys_stats() -> dict:
    """CPU/RAM/uptime — با cache 5s."""
    cached = _sys_cache.get()
    if cached is not None:
        return cached

    stats: dict[str, Any] = {
        "cpu": 0,
        "ram": 0,
        "uptime": (datetime.now() - _SERVER_START).total_seconds(),
    }
    try:
        import psutil
        stats["cpu"] = psutil.cpu_percent(interval=None)
        stats["ram"] = psutil.virtual_memory().percent
    except ImportError:
        log_warning(
            "⚠️ [OBS:sys] psutil نصب نیست — CPU/RAM صفر"
        )
    except Exception as e:
        log_warning(f"⚠️ [OBS:sys] {type(e).__name__}: {e}")

    _sys_cache.set(stats)
    return stats


# ═══════════════════════════════════════════
# Rate limiter (per-IP)
# ═══════════════════════════════════════════
class _RateLimiter:
    """rate limiter ساده per-IP با پنجرهٔ ثابت."""

    def __init__(self, max_req: int, window_s: int):
        self.max_req = max_req
        self.window_s = window_s
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str) -> tuple[bool, int]:
        """(allowed, remaining)."""
        now = time.monotonic()
        with self._lock:
            last_time, count = self._buckets.get(ip, (0.0, 0))
            if now - last_time >= self.window_s:
                # reset window
                self._buckets[ip] = (now, 1)
                return True, self.max_req - 1
            if count >= self.max_req:
                return False, 0
            self._buckets[ip] = (last_time, count + 1)
            return True, self.max_req - count - 1

    def cleanup(self) -> None:
        """پاک‌سازی bucketهای قدیمی."""
        now = time.monotonic()
        with self._lock:
            self._buckets = {
                ip: (t, c)
                for ip, (t, c) in self._buckets.items()
                if now - t < self.window_s * 2
            }


_rate_limiter = _RateLimiter(_RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW_S)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _sanitize_message(text: str) -> str:
    """پاک‌سازی پیام ورودی از null bytes + کنترل کاراکترها."""
    if not text:
        return ""
    # حذف null + control (به‌جز newline/tab)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # سقف طول
    if len(text) > _MAX_CHAT_LEN:
        text = text[:_MAX_CHAT_LEN]
    return text.strip()


def _get_client_ip(request) -> str:
    """IP مشتری — با احترام به X-Forwarded-For."""
    try:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        return request.remote or "unknown"
    except Exception:
        return "unknown"


# ═══════════════════════════════════════════
# Server
# ═══════════════════════════════════════════
async def start_obs_server(
    db,
    health_checker=None,
    collectors: list | None = None,
):
    """راه‌اندازی سرور observability — runner برمی‌گرداند (یا None)."""
    t_start = time.monotonic()

    enabled = (
        os.getenv("OBS_ENABLED", "true").strip().lower() == "true"
    )
    if not enabled:
        log_warning(
            "⚠️ [OBS:init] غیرفعال (OBS_ENABLED=false) — "
            "هیچ سروری راه نمی‌افتد"
        )
        return None

    try:
        from aiohttp import web
    except ImportError as e:
        log_error(f"❌ [OBS:init] aiohttp نصب نیست | {e}")
        return None

    collectors = collectors or []
    host = os.getenv("OBS_HOST", "0.0.0.0")

    raw_port = os.getenv("OBS_PORT", "8787")
    try:
        port = int(raw_port)
    except (ValueError, TypeError):
        log_warning(
            f"⚠️ [OBS:init] OBS_PORT نامعتبر | raw={raw_port!r} — "
            f"استفاده از 8787"
        )
        port = 8787

    log_info(
        f"▶️ [OBS:init] شروع | host={host} port={port} | "
        f"collectors={len(collectors)} | "
        f"health_checker={'✅' if health_checker else '—'} | "
        f"rate_limit={_RATE_LIMIT_MAX}/{_RATE_LIMIT_WINDOW_S}s"
    )

    # ─── نگاشت collector → is_connected ───
    def _collector_map() -> dict:
        m = dict.fromkeys(_PLATFORM_ORDER, False)
        for c in collectors:
            name = getattr(c, "platform_name", None)
            if name:
                m[name] = bool(getattr(c, "is_connected", False))
        return m

    # ─── AdminHandler (lazy, thread-safe, db-aware) ───
    _admin_lock = asyncio.Lock()
    _admin_state: dict[str, Any] = {"h": None, "db_id": None}

    async def _get_admin():
        current_db_id = id(db)
        async with _admin_lock:
            if (
                _admin_state["h"] is None
                or _admin_state["db_id"] != current_db_id
            ):
                from handlers.admin_handler import AdminHandler
                log_info(
                    f"🏗️ [OBS:chat] ساخت AdminHandler "
                    f"(db_id={current_db_id})"
                )
                _admin_state["h"] = AdminHandler(db)
                _admin_state["db_id"] = current_db_id
            return _admin_state["h"]

    # ═══════════════════════════════════════════
    # Handlers
    # ═══════════════════════════════════════════
    async def health(request):
        t0 = time.monotonic()

        # cache
        cached = _health_cache.get()
        if cached is not None:
            result, code = cached
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"🌐 [OBS:/health] {code} (cache) | "
                f"{elapsed_ms:.0f}ms"
            )
            return web.json_response(result, status=code)

        if health_checker is not None:
            try:
                result = await health_checker.check_all()
                overall = result.get("overall_status", "unknown")
                code = (
                    200 if overall in ("healthy", "degraded") else 503
                )
                _health_cache.set((result, code))
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"🌐 [OBS:/health] {code} {overall} | "
                    f"{elapsed_ms:.0f}ms"
                )
                return web.json_response(result, status=code)
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_error(
                    f"❌ [OBS:/health] health_checker | "
                    f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
                )
                return web.json_response(
                    {"overall_status": "unhealthy", "error": str(e)},
                    status=503,
                )

        # fallback: minimal + DB check
        db_ok = True
        try:
            if db is not None:
                await asyncio.wait_for(
                    db.execute("SELECT 1"),
                    timeout=2.0,
                )
        except Exception as e:
            db_ok = False
            log_warning(
                f"⚠️ [OBS:/health] db check | "
                f"{type(e).__name__}: {e}"
            )

        s = _sys_stats()
        overall = "healthy" if db_ok else "degraded"
        code = 200 if db_ok else 503
        result = {
            "overall_status": overall,
            "uptime": s["uptime"],
            "db_ok": db_ok,
        }
        _health_cache.set((result, code))
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"🌐 [OBS:/health] {code} {overall} (fallback) | "
            f"{elapsed_ms:.0f}ms"
        )
        return web.json_response(result, status=code)

    async def metrics(request):
        t0 = time.monotonic()
        try:
            counts = await _gather_counts(db)
            text = render_prometheus(
                counts, _sys_stats(), _collector_map()
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"🌐 [OBS:/metrics] 200 | {elapsed_ms:.0f}ms | "
                f"leads={counts.get('leads', 0)} "
                f"messages={counts.get('messages', 0)}"
            )
            return web.Response(
                text=text, content_type="text/plain"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [OBS:/metrics] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms"
            )
            return web.Response(
                text=f"# error: {e}\n",
                content_type="text/plain",
                status=500,
            )

    async def index(request):
        t0 = time.monotonic()
        try:
            counts = await _gather_counts(db)
            s = _sys_stats()
            by = "، ".join(
                f"{p}={counts['by_platform'].get(p, 0)}"
                for p in _PLATFORM_ORDER
            )
            body = (
                f"Srooyesh Lead-Monitor — OK\n"
                f"uptime: {int(s['uptime'])}s | "
                f"cpu: {s['cpu']}% | ram: {s['ram']}%\n"
                f"messages: {counts['messages']} | "
                f"leads: {counts['leads']} | hot: {counts['hot']}\n"
                f"by platform: {by}\n"
                f"endpoints: /health  /metrics  /chat\n"
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"🌐 [OBS:/] 200 | {elapsed_ms:.0f}ms | "
                f"uptime={int(s['uptime'])}s"
            )
            return web.Response(text=body, content_type="text/plain")
        except Exception as e:
            log_error(f"❌ [OBS:/] {type(e).__name__}: {e}")
            return web.Response(
                text=f"error: {e}",
                content_type="text/plain",
                status=500,
            )

    async def chat_page(request):
        log_info("🌐 [OBS:/chat] 200 HTML")
        return web.Response(
            text=_CHAT_HTML,
            content_type="text/html",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Security-Policy": (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def chat_api(request):
        t0 = time.monotonic()

        # ─── rate limit ───
        client_ip = _get_client_ip(request)
        allowed, remaining = _rate_limiter.check(client_ip)
        if not allowed:
            log_warning(
                f"⛔ [OBS:/chat:api] rate limit | ip={client_ip}"
            )
            return web.json_response(
                {"reply": "⏳ درخواست‌های زیادی — کمی صبر کن."},
                status=429,
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW_S)},
            )

        from config import AI_ENABLED
        if not AI_ENABLED:
            log_warning(
                "⚠️ [OBS:/chat:api] AI_ENABLED=false — رد شد"
            )
            return web.json_response({
                "reply": (
                    "🧠 هوش فعلاً خاموش است "
                    "(در .env بزن AI_ENABLED=true)."
                )
            })

        # ─── Content-Length check ───
        try:
            cl = request.content_length
            if cl is not None and cl > _MAX_BODY_BYTES:
                log_warning(
                    f"⛔ [OBS:/chat:api] body بزرگ | "
                    f"cl={cl} > {_MAX_BODY_BYTES}"
                )
                return web.json_response(
                    {"reply": "❌ درخواست بزرگ است."},
                    status=413,
                )
        except Exception:
            pass

        # ─── parse JSON ───
        try:
            data = await request.json()
            message = _sanitize_message(
                data.get("message") or ""
            )
        except Exception as e:
            log_warning(
                f"⚠️ [OBS:/chat:api] json نامعتبر | "
                f"{type(e).__name__}: {e}"
            )
            return web.json_response(
                {"reply": "❌ درخواست نامعتبر (بدنه باید JSON باشد)."},
                status=400,
            )

        if not message:
            return web.json_response({"reply": "📝 پیام خالی."})

        # ─── اجرا ───
        try:
            admin = await _get_admin()
            reply = await admin.process_text(message)
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_success(
                f"✅ [OBS:/chat:api] {elapsed_ms:.0f}ms | "
                f"ip={client_ip} | "
                f"in={message[:_LOG_PREVIEW_LEN]!r} | "
                f"out_len={len(reply or '')}"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [OBS:/chat:api] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms | ip={client_ip}"
            )
            # traceback کامل فقط در DEBUG
            if os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
                for line in traceback.format_exc().splitlines()[-10:]:
                    log_error(f"   ↳ {line}")
            reply = "❌ خطای داخلی در پردازش پیام."

        return web.json_response(
            {"reply": reply},
            headers={
                "X-RateLimit-Remaining": str(max(0, remaining)),
            },
        )

    # ═══════════════════════════════════════════
    # ساخت و راه‌اندازی
    # ═══════════════════════════════════════════
    app = web.Application(client_max_size=_MAX_BODY_BYTES)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/chat", chat_page)
    app.router.add_post("/chat", chat_api)

    # ─── cleanup دوره‌ای rate limiter (پس‌زمینه، بدون بلاک کردن startup) ───
    #
    # ⚠️ BUG-FIX: قبلاً این‌جا از
    #     lambda app: asyncio.create_task(_periodic_cleanup(app))
    # استفاده می‌شد. aiohttp در on_startup مقدار برگشتی را
    # await می‌کند؛ چون lambda یک Task برمی‌گرداند، aiohttp منتظر
    # تمام شدن آن Task می‌ماند و چون Task تا ابد در حلقهٔ while
    # است، سرور تا چند دقیقه بالا نمی‌آمد.
    #
    # راه‌حل: تابع async که تسک را می‌سازد و بلافاصله return می‌کند
    # (Task در پس‌زمینه می‌ماند و مرجعش در app نگه داشته می‌شود).
    async def _start_periodic_cleanup(app_):
        async def _runner():
            try:
                while True:
                    await asyncio.sleep(_RATE_CLEANUP_INTERVAL_S)
                    try:
                        _rate_limiter.cleanup()
                    except Exception as e:
                        log_warning(
                            f"⚠️ [OBS:cleanup] "
                            f"{type(e).__name__}: {e}"
                        )
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(_runner(), name="obs_rate_cleanup")
        app_["_obs_cleanup_task"] = task
        log_info(
            f"🧹 [OBS:init] تسک پاک‌سازی rate limiter راه افتاد | "
            f"هر {_RATE_CLEANUP_INTERVAL_S:.0f}s"
        )

    async def _stop_periodic_cleanup(app_):
        task = app_.get("_obs_cleanup_task")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    app.on_startup.append(_start_periodic_cleanup)
    app.on_cleanup.append(_stop_periodic_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)

    try:
        await site.start()
        elapsed_ms = (time.monotonic() - t_start) * 1000
        log_success(
            f"✅ [OBS:start] سرور روی http://{host}:{port} فعال شد | "
            f"/health /metrics /chat | {elapsed_ms:.0f}ms"
        )
        return runner
    except Exception as e:
        log_error(
            f"❌ [OBS:start] bind ناموفق | host={host} port={port} | "
            f"{type(e).__name__}: {e}"
        )
        try:
            await runner.cleanup()
            log_info("🧹 [OBS:start] runner تمیز شد")
        except Exception as e2:
            log_warning(
                f"⚠️ [OBS:start] cleanup | {type(e2).__name__}: {e2}"
            )
        return None
