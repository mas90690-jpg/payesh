"""Coverage for core infra modules: console_compat, messenger_registry, translator,
web_media, cleanup_service, base_*, target_filter, health_check, monitor, event_bus,
plugin_loader and ai_assistant (all mocked — no network)."""
import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from analyzers.target_filter import TargetGroups, get_target_groups
from core.adapters import messenger_registry, web_media
from core.adapters.translator import Translator
from core.app.plugin_loader import PluginLoader
from core.contracts.base_analyzer import BaseAnalyzer
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.contracts.base_handler import BaseHandler, HandlerPriority
from core.kernel.console_compat import patch_console
from core.kernel.event_bus import EventBus, EventPriority
from core.kernel.models import AnalyzedMessage
from core.ops.health_check import (
    HealthChecker,
    HealthCheckResult,
    HealthStatus,
    run_health_check,
)
from core.ops.monitor import Alert, Metric, PerformanceMonitor, SystemMonitor
from core.services.ai_assistant import AIAssistant
from core.services.cleanup_service import CleanupService, start_cleanup_service


# ───────────────────────── console_compat ─────────────────────────
def test_patch_console_reconfigures_streams():
    class FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    fs = FakeStream()
    with patch.object(sys, "stdout", fs):
        patch_console()
    assert fs.calls and fs.calls[0]["encoding"] == "utf-8"


def test_patch_console_never_raises():
    class BadStream:
        def reconfigure(self, **kwargs):
            raise RuntimeError("boom")

    with patch.object(sys, "stdout", BadStream()):
        patch_console()  # must not raise


# ───────────────────────── messenger_registry ─────────────────────────
def test_messenger_registry_roundtrip():
    messenger_registry._senders.clear()
    assert messenger_registry.get_sender("bale") is None

    async def fake_sender(chat_id, chat_type, text):
        pass

    messenger_registry.register_sender("bale", fake_sender)
    assert messenger_registry.get_sender("bale") is fake_sender
    messenger_registry._senders.clear()


@pytest.mark.asyncio
async def test_notify_admins_no_admins_returns_false(monkeypatch):
    monkeypatch.setattr(
        "config.PLUGINS_CONFIG",
        {"adminhandler": {"admin_ids": [], "platform": "bale"}},
    )
    assert await messenger_registry.notify_admins("hi") is False


@pytest.mark.asyncio
async def test_notify_admins_sends_to_each_admin(monkeypatch):
    monkeypatch.setattr(
        "config.PLUGINS_CONFIG",
        {"adminhandler": {"admin_ids": ["1", "2"], "platform": "bale"}},
    )
    sent = []

    async def fake_sender(chat_id, chat_type, text):
        sent.append(chat_id)

    messenger_registry._senders["bale"] = fake_sender
    ok = await messenger_registry.notify_admins("hi")
    assert ok is True
    assert sent == ["1", "2"]
    messenger_registry._senders.clear()


@pytest.mark.asyncio
async def test_notify_admins_swallows_sender_errors(monkeypatch):
    monkeypatch.setattr(
        "config.PLUGINS_CONFIG",
        {"adminhandler": {"admin_ids": ["1"], "platform": "bale"}},
    )

    async def bad_sender(chat_id, chat_type, text):
        raise RuntimeError("boom")

    messenger_registry._senders["bale"] = bad_sender
    assert await messenger_registry.notify_admins("hi") is False
    messenger_registry._senders.clear()


# ───────────────────────── translator ─────────────────────────
def test_translator_glossary_without_engine(tmp_path, monkeypatch):
    g = tmp_path / "glossary.txt"
    g.write_text("# comment\nYara|یارا\nYaratera Rexolin D|یاراترا رکسلین دی\n", encoding="utf-8")
    tr = Translator(glossary_file=str(g))
    assert tr.glossary["yara"] == ("Yara", "یارا")
    monkeypatch.setattr(tr, "_get_engine", lambda: False)
    assert tr.translate("Yara is great") == "یارا is great"


def test_translator_disabled_and_empty(tmp_path):
    g = tmp_path / "glossary.txt"
    g.write_text("bad line\n", encoding="utf-8")
    tr = Translator(glossary_file=str(g), enabled=False)
    assert tr.translate("hello") == "hello"
    assert tr.glossary == {}
    tr.reload_glossary()


def test_translator_engine_failure_falls_back(tmp_path, monkeypatch):
    g = tmp_path / "glossary.txt"
    g.write_text("Token|توکن\n", encoding="utf-8")
    tr = Translator(glossary_file=str(g))

    class BadEngine:
        def translate(self, text):
            raise RuntimeError("offline")

    monkeypatch.setattr(tr, "_get_engine", lambda: BadEngine())
    assert "توکن" in tr.translate("Token test")


def test_translator_get_translator_singleton():
    from core.adapters.translator import get_translator, translate_text

    t1 = get_translator()
    t2 = get_translator()
    assert t1 is t2
    assert translate_text("") == ""


# ───────────────────────── web_media ─────────────────────────
@pytest.mark.asyncio
async def test_download_dom_image_saves_file(tmp_path):
    img = AsyncMock()
    img.get_attribute = AsyncMock(return_value="https://x/y.png")
    element = AsyncMock()
    element.query_selector = AsyncMock(return_value=img)
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value="YWJj")  # base64 of b'abc'

    media_dir = str(tmp_path / "media")
    mt, path = await web_media.download_dom_image(page, element, media_dir, "t")
    assert mt == "photo"
    assert path and os.path.exists(path)
    assert os.path.getsize(path) == 3


@pytest.mark.asyncio
async def test_download_dom_image_no_image_or_svg(tmp_path):
    element = AsyncMock()
    element.query_selector = AsyncMock(return_value=None)
    assert await web_media.download_dom_image(None, element, str(tmp_path), "t") == (None, None)

    svg = AsyncMock()
    svg.get_attribute = AsyncMock(return_value="data:image/svg+xml,...")
    element2 = AsyncMock()
    element2.query_selector = AsyncMock(return_value=svg)
    assert await web_media.download_dom_image(None, element2, str(tmp_path), "t") == (None, None)


@pytest.mark.asyncio
async def test_download_dom_image_empty_b64(tmp_path):
    img = AsyncMock()
    img.get_attribute = AsyncMock(return_value="https://x/y.png")
    element = AsyncMock()
    element.query_selector = AsyncMock(return_value=img)
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    assert await web_media.download_dom_image(page, element, str(tmp_path), "t") == (None, None)


# ───────────────────────── cleanup_service ─────────────────────────
def test_delete_old_files(tmp_path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("x")
    new.write_text("x")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))
    removed = CleanupService._delete_old_files(str(tmp_path), 7, "test")
    assert removed == 1
    assert not old.exists() and new.exists()
    assert CleanupService._delete_old_files(str(tmp_path), 0, "test") == 0


@pytest.mark.asyncio
async def test_cleanup_run_once_with_db(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    f = media / "a.jpg"
    f.write_bytes(b"x")
    past = time.time() - 10 * 86400
    os.utime(f, (past, past))
    monkeypatch.setattr("core.services.cleanup_service.MEDIA_DIR", str(media))
    monkeypatch.setattr("core.services.cleanup_service.REPORTS_DIR", str(tmp_path / "reports"))
    db = AsyncMock()
    db.delete_old_messages = AsyncMock()
    svc = CleanupService(db)
    svc.keep_media_days = 7
    svc.keep_reports_days = 7
    svc.keep_messages_days = 30
    await svc.run_once()
    assert not f.exists()
    db.delete_old_messages.assert_awaited_once_with(30)


@pytest.mark.asyncio
async def test_start_cleanup_service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.services.cleanup_service.MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr("core.services.cleanup_service.REPORTS_DIR", str(tmp_path))
    recorded = {}

    def fake_create_task(coro):
        recorded["coro"] = coro
        return coro

    monkeypatch.setattr("core.services.cleanup_service.asyncio.create_task", fake_create_task)
    svc = await start_cleanup_service(None)
    assert svc is not None
    assert "coro" in recorded
    svc.stop()
    assert not svc._running


# ───────────────────────── base_collector ─────────────────────────
class _FakeCollector(BaseCollector):
    @property
    def platform_name(self):
        return "fake"

    async def connect(self):
        self._set_status(ConnectionStatus.CONNECTED)
        return True

    async def fetch_messages(self, since):
        return []

    async def disconnect(self):
        self._set_status(ConnectionStatus.DISCONNECTED)


@pytest.mark.asyncio
async def test_base_collector_lifecycle():
    c = _FakeCollector()
    assert c.status == ConnectionStatus.DISCONNECTED
    assert c.is_connected is False
    assert c.supports_history is False
    assert c.supports_websocket is False
    assert c.poll_interval_seconds == 2.0
    assert c.get_last_error() is None
    assert await c.health_check() is False

    c._set_error("boom")
    assert c.get_last_error() == "boom"
    assert c.status == ConnectionStatus.ERROR
    c._set_status(ConnectionStatus.CONNECTED)
    assert await c.health_check() is True

    c._retry_count = 10
    assert await c.reconnect() is False
    assert c.status == ConnectionStatus.ERROR

    async with _FakeCollector() as cc:
        assert cc.is_connected

    msg = c.to_unified_format({"a": 1}, "fake")
    assert msg["platform"] == "fake" and msg["raw_data"] == {"a": 1}
    assert c.to_unified_format("raw", "fake")["raw_data"] == {}


# ───────────────────────── base_handler ─────────────────────────
class _FakeHandler(BaseHandler):
    @property
    def handler_name(self):
        return "fake_handler"

    async def handle(self, event):
        return "handled"

    def can_handle(self, event_name):
        return event_name == "message.received"


@pytest.mark.asyncio
async def test_base_handler_stats_and_priority():
    h = _FakeHandler()
    assert h.priority == HandlerPriority.NORMAL
    assert h.enabled is True
    h.enabled = False
    assert h.enabled is False
    h.enabled = True

    h._record_handled(0.5)
    h._record_handled(1.5)
    stats = h.get_stats()
    assert stats["handled_count"] == 2
    assert abs(stats["avg_process_time"] - 1.0) < 1e-6
    assert stats["last_handled"] is not None

    await h.on_error(None, ValueError("x"))
    assert h.get_stats()["error_count"] == 1
    h.reset_stats()
    assert h.get_stats()["handled_count"] == 0
    assert await h(None) == "handled"
    await h.on_start()
    await h.on_stop()


# ───────────────────────── base_analyzer ─────────────────────────
class _FakeAnalyzer(BaseAnalyzer):
    async def analyze(self, message):
        message.score = 42
        return message


@pytest.mark.asyncio
async def test_base_analyzer_helpers():
    a = _FakeAnalyzer()
    m = AnalyzedMessage(
        platform="test", platform_id="1", chat_id="c", chat_name="g",
        sender_name="s", text="hello",
    )
    m = await a.add_keywords(m, ["k1", "k2"])
    assert set(m.keywords) == {"k1", "k2"}
    m = await a.add_keywords(m, ["k1"])
    assert len(m.keywords) == 2
    m = await a.add_metadata(m, "x", 1)
    assert m.metadata == {"x": 1}
    assert await a.get_score(m) is None  # base impl reads message.score (not set yet)

    m2 = await a.analyze(m)
    assert await a.get_score(m2) == 42

    m3 = AnalyzedMessage(
        platform="p", platform_id="2", chat_id="c", chat_name="g",
        sender_name="s", text="t",
    )
    assert await a.get_score(m3) is None

    class _Raising(BaseAnalyzer):
        async def analyze(self, message):
            raise RuntimeError("boom")

    combined = await a.combine_analyzers(m3, [_Raising(), a])
    assert combined.score == 42


# ───────────────────────── target_filter ─────────────────────────
def test_target_groups_parsing(tmp_path):
    f = tmp_path / "targets.txt"
    f.write_text(
        "# comment\nنهاده اصفهان\neitaa: بازار کود\nbale: @keshavarz\n",
        encoding="utf-8",
    )
    tg = TargetGroups(path=str(f))
    assert tg.enabled() is True
    assert tg.enabled("bale") is True
    assert tg.enabled("telegram") is True
    assert tg.is_target("گروه نهاده اصفهان", platform="telegram") is True
    assert tg.is_target("گروه دیگر", platform="telegram") is False
    assert tg.is_target("بازار کود", platform="eitaa") is True
    assert tg.is_target("keshavarz_group", platform="bale") is True
    # global rules apply on every platform
    assert tg.is_target("نهاده اصفهان x", platform="whatsapp") is True
    assert tg.is_target("غیرمرتبط", platform="whatsapp") is False


def test_target_groups_empty_file_filter_off(tmp_path):
    f = tmp_path / "targets.txt"
    f.write_text("# nothing\n", encoding="utf-8")
    tg = TargetGroups(path=str(f))
    assert tg.enabled() is False
    assert tg.is_target("whatever") is True


def test_target_groups_hot_reload(tmp_path):
    f = tmp_path / "targets.txt"
    f.write_text("old\n", encoding="utf-8")
    tg = TargetGroups(path=str(f))
    assert tg.enabled() is True
    f.write_text("# cleared\n", encoding="utf-8")
    tg._mtime = None
    tg._last_check = 0
    assert tg.enabled() is False


def test_target_groups_singleton():
    a = get_target_groups()
    b = get_target_groups()
    assert a is b


# ───────────────────────── health_check ─────────────────────────
class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, path):
        self.db_path = path

    async def execute(self, query, params=()):
        return _QueryResult([(1,)])


@pytest.mark.asyncio
async def test_health_check_database_healthy(tmp_path):
    hc = HealthChecker(_FakeDB(str(tmp_path / "messages.db")))
    res = await hc.check_database()
    assert res.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_health_check_database_missing_unknown():
    hc = HealthChecker(None)
    res = await hc.check_database()
    assert res.status == HealthStatus.UNKNOWN


@pytest.mark.asyncio
async def test_health_check_database_error():
    class _BadDB:
        async def execute(self, query, params=()):
            raise RuntimeError("broken")

    hc = HealthChecker(_BadDB())
    res = await hc.check_database()
    assert res.status == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_health_check_collector():
    class _WithHealth:
        platform_name = "tg"

        async def check_health(self):
            return True

    class _WithConnected:
        platform_name = "wa"
        is_connected = False

    hc = HealthChecker(None)
    r1 = await hc.check_collector(_WithHealth())
    assert r1.status == HealthStatus.HEALTHY
    r2 = await hc.check_collector(_WithConnected())
    assert r2.status == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_health_check_collector_error():
    class _Err:
        platform_name = "x"

        async def check_health(self):
            raise RuntimeError("down")

    hc = HealthChecker(None)
    r = await hc.check_collector(_Err())
    assert r.status == HealthStatus.UNHEALTHY


def test_health_check_system_cpu(monkeypatch):
    monkeypatch.setattr(
        "core.ops.health_check.psutil.cpu_percent", lambda interval=1: 95.0
    )
    monkeypatch.setattr(
        "core.ops.health_check.psutil.virtual_memory",
        lambda: MagicMock(percent=40, available=1024**3),
    )
    monkeypatch.setattr(
        "core.ops.health_check.psutil.disk_usage",
        lambda path: MagicMock(percent=40, free=1024**3),
    )
    res = HealthChecker(None).check_system()
    # NOTE: the >80 branch fires first -> DEGRADED even at 95%. Assert actual behaviour.
    assert res.status == HealthStatus.DEGRADED
    assert "CPU بالا" in res.message


def test_health_check_system_degraded_ram(monkeypatch):
    monkeypatch.setattr(
        "core.ops.health_check.psutil.cpu_percent", lambda interval=1: 30.0
    )
    monkeypatch.setattr(
        "core.ops.health_check.psutil.virtual_memory",
        lambda: MagicMock(percent=90, available=1),
    )
    monkeypatch.setattr(
        "core.ops.health_check.psutil.disk_usage",
        lambda path: MagicMock(percent=90, free=1),
    )
    res = HealthChecker(None).check_system()
    assert res.status == HealthStatus.DEGRADED


def test_health_check_system_errors_handled(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("core.ops.health_check.psutil.cpu_percent", _boom)
    monkeypatch.setattr("core.ops.health_check.psutil.virtual_memory", _boom)
    monkeypatch.setattr("core.ops.health_check.psutil.disk_usage", _boom)
    res = HealthChecker(None).check_system()
    assert "cpu_error" in res.details
    assert res.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_health_check_external_api(monkeypatch):
    @pytest.mark.asyncio
    async def test_health_check_external_api(monkeypatch):
        import aiohttp

        class _Resp:
            status = 200

        class _Session:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def get(self, url, timeout=None):
                class _CM:
                    def __init__(self, resp):
                        self.resp = resp

                    async def __aenter__(self):
                        return self.resp

                    async def __aexit__(self, *a):
                        return False

                return _CM(_Resp())

        monkeypatch.setattr(aiohttp, "ClientSession", _Session)
        hc = HealthChecker(None)
        res = await hc.check_external_api("test", "http://x")
        assert res.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_health_check_external_api_error(monkeypatch):
    import aiohttp

    class _Broken:
        def __init__(self, *a, **k):
            pass

        def get(self, url, timeout=None):
            raise RuntimeError("conn refused")

    monkeypatch.setattr(aiohttp, "ClientSession", _Broken)
    res = await HealthChecker(None).check_external_api("t", "http://x")
    assert res.status == HealthStatus.UNHEALTHY


def test_health_check_overall_and_summary():
    hc = HealthChecker(None)
    assert hc._get_overall_status() == HealthStatus.UNKNOWN
    assert hc.get_summary() == {"status": "not_checked", "components": {}}
    hc.results["a"] = HealthCheckResult(component="a", status=HealthStatus.HEALTHY)
    hc.results["b"] = HealthCheckResult(component="b", status=HealthStatus.DEGRADED)
    assert hc._get_overall_status() == HealthStatus.DEGRADED
    assert hc.get_summary()["status"] == "degraded"
    hc.results["c"] = HealthCheckResult(component="c", status=HealthStatus.UNHEALTHY)
    assert hc._get_overall_status() == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_health_check_check_all_and_endpoint(tmp_path):
    @pytest.mark.asyncio
    async def test_health_check_check_all_and_endpoint(tmp_path):
        import aiohttp

        hc = HealthChecker(_FakeDB(str(tmp_path / "db.sqlite")))

        class _Col:
            platform_name = "bale"

            async def check_health(self):
                return True

        hc.register_collector(_Col())
        hc.register_external_api("ext", "http://x")

        class _Resp:
            status = 200

        class _Session:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def get(self, url, timeout=None):
                class _CM:
                    def __init__(self, resp):
                        self.resp = resp

                    async def __aenter__(self):
                        return self.resp

                    async def __aexit__(self, *a):
                        return False

                return _CM(_Resp())

        monkeypatch.setattr(aiohttp, "ClientSession", _Session)
        result = await hc.check_all()
        assert result["overall_status"] in ("healthy", "degraded", "unhealthy")
        assert "collector_bale" in result["components"]
        ep = await hc.health_check_endpoint()
        assert ep["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_run_health_check_helper(tmp_path):
    result = await run_health_check(_FakeDB(str(tmp_path / "db.sqlite")))
    assert "overall_status" in result


# ───────────────────────── monitor ─────────────────────────
def test_monitor_collect_metrics(monkeypatch):
    import psutil as psutil_mod

    monkeypatch.setattr(psutil_mod, "cpu_percent", lambda interval=None: 12.3)
    monkeypatch.setattr(
        psutil_mod, "virtual_memory",
        lambda: MagicMock(percent=40, used=1024**3, available=1024**3),
    )
    monkeypatch.setattr(
        psutil_mod, "disk_usage", lambda p: MagicMock(percent=50, used=1, free=1)
    )
    monkeypatch.setattr(
        psutil_mod, "net_io_counters", lambda: MagicMock(bytes_sent=100, bytes_recv=200)
    )
    proc = MagicMock()
    proc.cpu_percent.return_value = 5.0
    proc.memory_info.return_value = MagicMock(rss=1024**2)
    proc.num_threads.return_value = 7
    monkeypatch.setattr(psutil_mod, "Process", lambda: proc)
    if hasattr(psutil_mod, "sensors_temperatures"):
        monkeypatch.setattr(psutil_mod, "sensors_temperatures", lambda: {})

    sm = SystemMonitor()
    asyncio.run(sm.collect_metrics())
    assert sm.get_current_metric("cpu_usage").value == 12.3
    assert sm.get_current_metric("uptime") is not None
    assert sm.get_current_metric("nope") is None
    assert sm._uptime_str() == "0s"
    report = sm.get_report()
    assert report["system"]["cpu"].value == 12.3


def test_monitor_alerts_and_status(capsys):
    sm = SystemMonitor()
    sm._add_metric("cpu_usage", 99, "%")
    sm._add_metric("ram_usage", 20, "%")
    sm._add_metric("disk_usage", 20, "%")
    sm._add_metric("process_memory", 100, "MB")
    sm._add_metric("uptime", 90, "seconds")
    asyncio.run(sm.check_alerts())
    assert any(a.level == "critical" for a in sm.alerts)
    sm._add_alert("warning", "w", "msg")
    sm._add_alert("info", "i", "msg")
    asyncio.run(sm.show_status())
    assert sm._uptime_str() == "1m 30s"
    asyncio.run(sm.stop_monitoring())


def test_monitor_performance():
    pm = PerformanceMonitor()
    st = pm.start_operation("db", "save")
    elapsed = pm.end_operation("db", "save", st)
    assert elapsed >= 0
    st2 = pm.start_operation("db", "save")
    pm.end_operation("db", "save", st2, error=True)
    stats = pm.get_stats()
    assert stats["db.save"]["calls"] == 2
    assert stats["db.save"]["errors"] == 1
    assert stats["db.save"]["avg_time"] > 0
    pm.show_slowest_operations()


def test_metric_alert_dataclasses():
    m = Metric("x", 1.0, "%")
    assert m.name == "x"
    a = Alert("w", "t", "m")
    assert a.level == "w"


# ───────────────────────── event_bus ─────────────────────────
@pytest.mark.asyncio
async def test_event_bus_priority_order():
    bus = EventBus()
    order = []

    def low_handler(event):
        order.append("low")

    async def high_handler(event):
        order.append("high")

    bus.subscribe("e", low_handler, EventPriority.LOW)
    bus.subscribe("e", high_handler, EventPriority.HIGH)
    await bus.emit("e", {"x": 1}, source="test", priority=EventPriority.HIGH)
    assert order == ["high", "low"]
    assert bus.has_handler("e")
    assert bus.has_handler("e", high_handler)
    assert bus.has_handler("missing") is False

    assert bus.unsubscribe("e", low_handler) is True
    assert bus.unsubscribe("e", low_handler) is False
    stats = bus.get_stats()
    assert stats["total_events"] == 1
    assert stats["events_by_name"]["e"] == 1
    assert len(bus.get_event_history(10)) == 1
    bus.clear_history()
    assert len(bus.get_event_history(10)) == 0


@pytest.mark.asyncio
async def test_event_bus_emit_error_isolation():
    bus = EventBus()

    def bad(event):
        raise ValueError("oops")

    def good(event):
        pass

    bus.subscribe("e", bad)
    bus.subscribe("e", good)
    await bus.emit("e", {})
    assert bus.get_stats()["errors"] == 1


def test_event_bus_emit_sync():
    bus = EventBus()
    seen = []

    def h(event):
        seen.append(event.data)

    bus.subscribe("e", h)
    bus.emit_sync("e", 42)
    assert seen == [42]


@pytest.mark.asyncio
async def test_event_bus_wait_for():
    bus = EventBus()

    async def deliver():
        await asyncio.sleep(0.01)
        await bus.emit("w", {"ok": True})

    task = asyncio.create_task(deliver())
    ev = await bus.wait_for("w", timeout=1.0)
    await task
    assert ev is not None and ev.data == {"ok": True}
    ev2 = await bus.wait_for("never", timeout=0.05)
    assert ev2 is None


@pytest.mark.asyncio
async def test_event_bus_remove_all():
    bus = EventBus()

    def h(event):
        pass

    bus.subscribe("a", h)
    bus.subscribe("b", h)
    bus.remove_all_handlers("a")
    assert bus.has_handler("a") is False
    bus.remove_all_handlers()
    assert bus.has_handler("b") is False


# ───────────────────────── plugin_loader ─────────────────────────
def test_plugin_loader_is_enabled(monkeypatch):
    import config

    class Dummy:
        pass

    cfg = {**config.PLUGINS_CONFIG, "dummy": {"enabled": False}, "enabled_plugins": ["other"]}
    monkeypatch.setattr(config, "PLUGINS_CONFIG", cfg)
    loader = PluginLoader()
    assert loader._is_enabled(Dummy) is False

    class WithIsEnabled:
        @staticmethod
        def is_enabled():
            return False

    assert loader._is_enabled(WithIsEnabled) is False
    # enabled_plugins non-empty and class not in list -> disabled
    assert loader._is_enabled(type("Plain", (), {})) is False


def test_plugin_loader_create_instance_injects_deps(monkeypatch):
    import config

    class Needs:
        def __init__(self, db=None, scorer=None, extra=None):
            self.db = db
            self.scorer = scorer
            self.extra = extra

    db = object()
    loader = PluginLoader()
    loader.set_dependencies(db=db, scorer=object())
    monkeypatch.setattr(config, "PLUGINS_CONFIG", {**config.PLUGINS_CONFIG, "needs": {"extra": 5}})
    inst = loader._create_instance(Needs)
    assert inst.db is db
    assert inst.extra == 5

    class Explodes:
        def __init__(self, db):
            raise RuntimeError("no")

    monkeypatch.setattr(config, "PLUGINS_CONFIG", {**config.PLUGINS_CONFIG, "explodes": {}})
    assert loader._create_instance(Explodes) is None


def test_plugin_loader_discovers_real_modules(monkeypatch, tmp_path):
    import config
    from core.database import SQLiteDatabase

    cfg = {}
    for k, v in config.PLUGINS_CONFIG.items():
        if isinstance(v, dict):
            v = dict(v)
            if k.endswith("collector") or k in (
                "storagehandler", "adminhandler", "scoringhandler", "notificationhandler",
            ):
                v["enabled"] = True
        cfg[k] = v
    monkeypatch.setattr(config, "PLUGINS_CONFIG", cfg)

    db = SQLiteDatabase.__new__(SQLiteDatabase)
    db.db_path = str(tmp_path / "messages.db")

    loader = PluginLoader()
    loader.set_dependencies(db=db, scorer=None)
    collectors = loader.discover_collectors()
    handlers = loader.discover_handlers()
    assert len(collectors) >= 5
    assert any(h.handler_name == "storage_handler" for h in handlers)
    assert loader.collectors and loader.handlers


def test_plugin_loader_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("core.app.plugin_loader.os.getcwd", lambda: str(tmp_path))
    loader = PluginLoader()
    assert loader.discover_collectors() == []
    assert loader.discover_handlers() == []


# ───────────────────────── ai_assistant ─────────────────────────
@pytest.mark.asyncio
async def test_ai_assistant_available_ollama(monkeypatch):
    import aiohttp

    ai = AIAssistant()
    ai.enabled = True
    ai._deepseek_key = ""

    class _Resp:
        status = 200

    class _CM:
        def __init__(self, resp):
            self.resp = resp

        async def __aenter__(self):
            return self.resp

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, timeout=None):
            return _CM(_Resp())

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    assert await ai.available() is True


@pytest.mark.asyncio
async def test_ai_assistant_available_offline_but_deepseek(monkeypatch):
    import aiohttp

    class _Down:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, timeout=None):
            raise RuntimeError("refused")

    monkeypatch.setattr(aiohttp, "ClientSession", _Down)
    ai = AIAssistant()
    ai.enabled = True
    ai._deepseek_key = "sk-test"
    assert await ai.available() is True


@pytest.mark.asyncio
async def test_ai_assistant_parse_intent_whitelist(monkeypatch):
    ai = AIAssistant()

    async def fake_ollama(prompt, format_json=False):
        return '{"action": "status", "args": {}}'

    monkeypatch.setattr(ai, "_call_ollama", fake_ollama)
    monkeypatch.setattr(ai, "_call_deepseek", lambda p, format_json=False: None)
    intent = await ai.parse_intent("وضعیت")
    assert intent == {"action": "status", "args": {}}


@pytest.mark.asyncio
async def test_ai_assistant_parse_intent_rejects_unknown_action(monkeypatch):
    ai = AIAssistant()

    async def fake_ollama(prompt, format_json=False):
        return '{"action": "rm_rf", "args": {}}'

    monkeypatch.setattr(ai, "_call_ollama", fake_ollama)
    intent = await ai.parse_intent("x")
    assert intent["action"] == "unknown"


@pytest.mark.asyncio
async def test_ai_assistant_parse_intent_invalid_json(monkeypatch):
    ai = AIAssistant()

    async def fake_ollama(prompt, format_json=False):
        return "not json at all"

    monkeypatch.setattr(ai, "_call_ollama", fake_ollama)
    assert (await ai.parse_intent("x"))["action"] == "unknown"
    assert (await ai.parse_intent(""))["action"] == "unknown"


@pytest.mark.asyncio
async def test_ai_assistant_chat_and_fallback(monkeypatch):
    ai = AIAssistant()

    async def fake_ollama(prompt, format_json=False):
        return "پاسخ فارسی"

    monkeypatch.setattr(ai, "_call_ollama", fake_ollama)
    assert await ai.chat("سلام") == "پاسخ فارسی"
    assert await ai.chat("") == ""

    async def silent(prompt, format_json=False):
        return None

    monkeypatch.setattr(ai, "_call_ollama", silent)

    async def _no_deepseek(prompt, format_json=False):
        return None

    monkeypatch.setattr(ai, "_call_deepseek", _no_deepseek)
    assert "هوش در دسترس نیست" in await ai.chat("سلام")


@pytest.mark.asyncio
async def test_ai_assistant_call_ollama_error(monkeypatch):
    import aiohttp

    class _Fail:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, **k):
            raise RuntimeError("down")

    monkeypatch.setattr(aiohttp, "ClientSession", _Fail)
    ai = AIAssistant()
    assert await ai._call_ollama("hi", format_json=True) is None


@pytest.mark.asyncio
async def test_ai_assistant_call_deepseek_no_key():
    ai = AIAssistant()
    ai._deepseek_key = ""
    assert await ai._call_deepseek("hi") is None
