"""
Controlled live-startup test: boots the real app pipeline (DB init, collectors,
handlers, obs server, orchestrator) with a timeout, then shuts down cleanly.
This mirrors `python main.py` but auto-stops after ~60s so we can inspect errors.
"""
import asyncio
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")

# 🐛 FIX: مسیر پروژه در sys.path — تا از هر جا اجرا شود.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.kernel.console_compat import patch_console  # noqa: E402

patch_console()

from config import PYCACHE_DIR  # noqa: E402

os.makedirs(PYCACHE_DIR, exist_ok=True)
os.environ["PYTHONPYCACHEPREFIX"] = PYCACHE_DIR

from core.app.orchestrator import Orchestrator  # noqa: E402
from core.app.plugin_loader import PluginLoader  # noqa: E402
from core.database import create_database  # noqa: E402
from core.kernel.event_bus import EventBus  # noqa: E402
from core.kernel.logger import log_error, log_success  # noqa: E402


async def main() -> None:
    print("=" * 60)
    print("LIVE STARTUP TEST (auto-stop after 60s)")
    print("=" * 60)

    db = create_database()
    await db.init()

    event_bus = EventBus()
    loader = PluginLoader()
    loader.set_dependencies(db=db, scorer=None)

    from config import BRAIN_CONFIG
    from config import AI as _AI_CFG  # 🐛 FIX: api_key
    from core.safe_brain import get_safe_brain

    # 🐛 FIX: api_key از config.AI (مثل main.py)
    _api_key = (
        (_AI_CFG or {}).get("deepseek_api_key")
        or os.environ.get("DEEPSEEK_API_KEY", "")
        or ""
    )

    get_safe_brain(
        api_key=_api_key,
        full_access=BRAIN_CONFIG.get("full_access", False),
        use_api_memory=BRAIN_CONFIG.get("use_api_memory", False),
        memory_api_url=BRAIN_CONFIG.get("memory_api_url", ""),
        memory_api_key=BRAIN_CONFIG.get("memory_api_key", ""),
    )

    collectors = loader.discover_collectors()
    log_success(f"collectors discovered: {[c.platform_name for c in collectors]}")

    handlers = loader.discover_handlers()
    log_success(f"handlers discovered: {[h.handler_name for h in handlers]}")

    for handler in handlers:
        for ev in ("message.received", "lead.saved", "error.occurred"):
            if handler.can_handle(ev):
                event_bus.subscribe(ev, handler.handle)

    try:
        from core.ops.health_check import HealthChecker
        from core.ops.obs_server import start_obs_server

        hc = HealthChecker(db)
        for c in collectors:
            hc.register_collector(c)
        await start_obs_server(db, hc, collectors)
    except Exception as e:
        log_error(f"obs server failed: {e}")

    orch = Orchestrator(collectors, event_bus, db)
    task = asyncio.create_task(orch.start())

    await asyncio.sleep(60)
    log_success("LIVE STARTUP TEST: 60s elapsed — shutting down cleanly")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await orch.stop()
    await db.close()
    print("LIVE STARTUP TEST COMPLETE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("interrupted")
    except Exception as e:
        print("FATAL:", type(e).__name__, e)
        import traceback

        traceback.print_exc()
        sys.exit(1)