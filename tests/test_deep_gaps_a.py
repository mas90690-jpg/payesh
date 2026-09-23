"""Deep coverage: admin_handler, orchestrator, self_monitor, logger, auto_subscribe."""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.app.orchestrator import Orchestrator
from core.kernel.logger import (
    ConsoleFormatter,
    JsonFormatter,
    LogLevel,
    get_logger,
    log_duration,
    log_error,
    log_exception,
    log_info,
    log_success,
    log_warning,
)
from core.ops.self_monitor import SelfMonitor, start_self_monitor
from handlers.admin_handler import AdminHandler
from handlers.auto_subscribe import AutoSubscriber


# ─────────────────────────── logger ───────────────────────────
def test_logger_stats_and_levels():
    logger = get_logger()
    log_info("info msg")
    log_success("success msg")
    log_warning("warn msg")
    log_error("err msg")
    log_exception("exc msg", ValueError("x"))
    stats = logger.get_stats()
    assert stats["total_logs"] >= 5
    assert "ERROR" in stats["by_level"]
    logger.set_level("DEBUG")
    assert logger.log_level == "DEBUG"
    logger.set_level("NOPE")
    assert logger.log_level == "DEBUG"
    logger.flush()


def test_logger_log_with_enum():
    logger = get_logger()
    logger.log(LogLevel.INFO, "enum msg")
    logger.log("debug", "string level")
    assert logger.get_stats()["by_level"]["INFO"] >= 1


def test_log_duration_context():
    with log_duration("op"):
        pass

    with pytest.raises(RuntimeError):
        with log_duration("bad"):
            raise RuntimeError("boom")


def test_json_formatter_with_exception():
    rec = logging.LogRecord("srooyesh", 40, __file__, 1, "boom", None, None)
    rec.exc_info = (ValueError, ValueError("x"), None)
    out = JsonFormatter().format(rec)
    obj = json.loads(out)
    assert obj["level"] == "ERROR"
    assert "exception" in obj


def test_console_formatter_plain():
    rec = logging.LogRecord("srooyesh", 20, __file__, 1, "hello", None, None)
    f = ConsoleFormatter()
    f._color = False
    assert "hello" in f.format(rec)
    rec.context = {"a": 1}
    assert "a" in f.format(rec)


import logging  # noqa: E402


# ─────────────────────────── auto_subscribe ───────────────────────────
@pytest.mark.asyncio
async def test_auto_subscriber_registers(tmp_path, monkeypatch):
    class _H:
        handler_name = "storage_handler"

        async def handle(self, ev):
            return None

    class _L:
        def discover_handlers(self):
            return [_H()]

    class _Bus:
        def __init__(self):
            self.subs = []

        def subscribe(self, ev, handler):
            self.subs.append((ev, handler))

    bus = _Bus()
    await AutoSubscriber(bus, _L()).register_all()
    assert bus.subs[0][0] == "message.received"


def test_auto_subscriber_get_events():
    class _H:
        handler_name = "crm_handler"

    a = AutoSubscriber(None, None)
    assert a._get_handler_events(_H()) == ["lead.saved"]

    class _L:
        handler_name = "log_handler"

    assert a._get_handler_events(_L()) == ["error.occurred", "lead.saved"]

    class _S:
        handler_name = "random"

    assert a._get_handler_events(_S()) == ["message.received", "lead.saved", "error.occurred"]


# ─────────────────────────── orchestrator ───────────────────────────
class _OBus:
    def __init__(self):
        self.events = []

    async def emit(self, name, data):
        self.events.append((name, data))


class _ODB:
    def __init__(self):
        self.polls = {}

    async def get_last_poll(self, platform):
        return self.polls.get(platform)

    async def update_last_poll(self, platform, ts):
        self.polls[platform] = ts


class _OCol:
    platform_name = "test"
    poll_interval_seconds = 0.01

    def __init__(self, connect_ok=True, msgs=None):
        self.connect_ok = connect_ok
        self.msgs = msgs or []
        self.is_connected = False
        self.connect_calls = 0
        self.disconnected = 0

    async def connect(self):
        self.connect_calls += 1
        self.is_connected = self.connect_ok
        return self.connect_ok

    async def disconnect(self):
        self.disconnected += 1
        self.is_connected = False

    async def fetch_messages(self, since):
        return self.msgs

    async def fetch_history(self, since):
        return self.msgs


@pytest.mark.asyncio
async def test_orchestrator_initial_fetch(monkeypatch):
    bus, db = _OBus(), _ODB()
    col = _OCol(msgs=[{"id": "1", "text": "قیمت یاراترا"}])
    o = Orchestrator([col], bus, db)
    await o._initial_fetch(col)
    assert col.connect_calls == 1
    assert any(n == "message.received" for n, _ in bus.events)
    assert db.polls["test"] is not None


@pytest.mark.asyncio
async def test_orchestrator_initial_fetch_connect_fail(monkeypatch):
    bus, db = _OBus(), _ODB()
    col = _OCol(connect_ok=False)
    o = Orchestrator([col], bus, db)
    await o._initial_fetch(col)
    assert bus.events == []


@pytest.mark.asyncio
async def test_orchestrator_process_messages(monkeypatch):
    bus, db = _OBus(), _ODB()
    col = _OCol(msgs=[{"id": "2", "text": "خرید رونزا"}])
    o = Orchestrator([col], bus, db)
    await o._process_messages(col)
    assert bus.events[0][0] == "message.received"
    assert bus.events[0][1]["platform"] == "test"
    assert db.polls["test"] is not None


@pytest.mark.asyncio
async def test_orchestrator_catch_up(monkeypatch):
    bus, db = _OBus(), _ODB()
    db.polls["test"] = datetime.now() - timedelta(hours=2)
    col = _OCol(msgs=[{"id": "3"}])
    o = Orchestrator([col], bus, db)
    await o._catch_up(col)
    assert any(n == "message.received" for n, _ in bus.events)


@pytest.mark.asyncio
async def test_orchestrator_greet_admins_disabled(monkeypatch):
    monkeypatch.setattr(
        "config.PLUGINS_CONFIG",
        {"adminhandler": {"enabled": False}},
    )
    bus, db = _OBus(), _ODB()
    o = Orchestrator([], bus, db)
    await o._greet_admins()  # no crash, returns early


@pytest.mark.asyncio
async def test_orchestrator_start_stop(monkeypatch):
    monkeypatch.setattr(
        "config.PLUGINS_CONFIG", {"adminhandler": {"enabled": False}},
    )
    bus, db = _OBus(), _ODB()
    col = _OCol()
    o = Orchestrator([col], bus, db)
    o._initial_fetch = AsyncMock()
    o._greet_admins = AsyncMock()
    o._generate_report = AsyncMock()
    started = False

    async def _fake_gather(*tasks):
        nonlocal started
        started = True
        while o.running:
            await asyncio.sleep(0.001)
        return None

    monkeypatch.setattr("core.app.orchestrator.asyncio.gather", _fake_gather)
    task = asyncio.create_task(o.start())
    await asyncio.sleep(0.05)
    assert started
    o.running = False
    await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    await o.stop()
    assert col.disconnected >= 1


@pytest.mark.asyncio
async def test_orchestrator_supervise_reconnect_fails(monkeypatch):
    async def _sleep(n):
        return None

    monkeypatch.setattr("core.app.orchestrator.asyncio.sleep", _sleep)
    bus, db = _OBus(), _ODB()
    col = _OCol(connect_ok=False)
    o = Orchestrator([col], bus, db)
    o.running = True
    await o._supervise_reconnect(col)
    # 3 attempts: each disconnects then connects
    assert col.connect_calls == 3


# ─────────────────────────── self_monitor ───────────────────────────
def _mk_self_monitor(tmp_path, monkeypatch, log_lines=None):
    log_file = tmp_path / "app.jsonl"
    if log_lines:
        log_file.write_text("".join(log_lines), encoding="utf-8")
    else:
        log_file.write_text("", encoding="utf-8")
    monkeypatch.setattr("core.ops.self_monitor.LOG_FILE", str(log_file))
    sm = SelfMonitor(db=None)
    sm.watch = []
    return sm


def test_self_monitor_recent_errors(tmp_path, monkeypatch):
    now = datetime.now().isoformat()
    sm = _mk_self_monitor(
        tmp_path, monkeypatch,
        [
            json.dumps({"ts": now, "level": "ERROR", "msg": "e1"}) + "\n",
            json.dumps({"ts": now, "level": "CRITICAL", "msg": "c1"}) + "\n",
            json.dumps({"ts": now, "level": "INFO", "msg": "i"}) + "\n",
        ],
    )
    sm.interval = 10_000_000
    errs = sm._recent_errors()
    assert len(errs) == 2


def test_self_monitor_db_health(tmp_path, monkeypatch):
    db_path = tmp_path / "messages.db"
    db_path.write_bytes(b"x" * (600 * 1024 * 1024))  # 600MB
    monkeypatch.setattr("core.ops.self_monitor.DB_PATH", str(db_path))
    sm = SelfMonitor(db=None)
    out = sm._db_health()
    assert "MB" in out
    monkeypatch.setattr("core.ops.self_monitor.DB_PATH", str(tmp_path / "none.db"))
    assert sm._db_health() == ""


@pytest.mark.asyncio
async def test_self_monitor_ai_health_disabled(monkeypatch):
    monkeypatch.setattr("config.AI", {"enabled": False})
    sm = SelfMonitor(db=None)
    assert await sm._ai_health() == ""


def test_self_monitor_idle_limit(monkeypatch):
    monkeypatch.setattr(
        "config.PLUGINS_CONFIG",
        {
            "balecollector": {"poll_seconds": 60},
            "selfmonitor": {"enabled": True, "interval_seconds": 600, "error_threshold": 5, "stale_minutes": 60},
            "adminhandler": {"admin_ids": [], "platform": "bale"},
        },
    )
    sm = SelfMonitor(db=None)
    assert sm._idle_limit("bale") == max(60, 3)  # 60*3/60 = 3 min -> max(60,3)=60
    assert sm._idle_limit("web") >= 60


def test_self_monitor_lead_counts(tmp_path, monkeypatch):
    db_path = tmp_path / "messages.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE messages (platform TEXT, keywords TEXT)")
    conn.execute("INSERT INTO messages VALUES ('bale','یاراترا')")
    conn.commit()
    conn.close()
    monkeypatch.setattr("core.ops.self_monitor.DB_PATH", str(db_path))
    sm = SelfMonitor(db=None)
    assert sm._lead_counts() == {"bale": 1}


@pytest.mark.asyncio
async def test_self_monitor_check_once_no_problems(tmp_path, monkeypatch):
    sm = _mk_self_monitor(tmp_path, monkeypatch)
    sm.error_threshold = 100
    sm._last_signature = None
    await sm.check_once()  # no problems -> no alert
    assert sm._last_signature is None


@pytest.mark.asyncio
async def test_self_monitor_check_once_alerts(tmp_path, monkeypatch):
    now = datetime.now().isoformat()
    sm = _mk_self_monitor(
        tmp_path, monkeypatch,
        [json.dumps({"ts": now, "level": "ERROR", "msg": "boom"}) + "\n"] * 3,
    )
    sm.error_threshold = 2
    sm.admin_ids = ["1"]
    sm.platform = "bale"
    sent = []

    async def fake_sender(cid, ct, text):
        sent.append((cid, ct, text))

    monkeypatch.setattr("core.ops.self_monitor.messenger_registry.get_sender", lambda p: fake_sender)
    await sm.check_once()
    assert sent and "سلامت" in sent[0][2]
    # dedupe: same signature -> no second alert
    await sm.check_once()
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_start_self_monitor(monkeypatch):
    monkeypatch.setattr("config.PLUGINS_CONFIG", {"selfmonitor": {"enabled": False}})
    sm = await start_self_monitor(None)
    assert sm is not None


# ─────────────────────────── admin_handler ───────────────────────────
def _mk_admin(temp_db, tmp_path, monkeypatch):
    h = AdminHandler(db=temp_db)
    h.db_path = temp_db.db_path
    monkeypatch.setattr("handlers.admin_handler._brain_available", False)
    monkeypatch.setattr("handlers.admin_handler.CUSTOM_KW", str(tmp_path / "kw.json"))
    monkeypatch.setattr("handlers.admin_handler.REQUESTS", str(tmp_path / "req.jsonl"))
    monkeypatch.setattr("handlers.admin_handler.MEMORY", str(tmp_path / "mem.jsonl"))
    monkeypatch.setattr("handlers.admin_handler.ROOT", str(tmp_path))
    monkeypatch.setattr("handlers.admin_handler.KNOWLEDGE", str(tmp_path / "k.md"))
    return h


@pytest.mark.asyncio
async def test_admin_execute_actions(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    assert "دستورها" in await h._execute("help", None, "help")
    assert "کلیدواژه" in await h._execute("keywords", None, "keywords")
    assert "اضافه" in await h._execute("add_keyword", "کودتست2", "...")
    assert "قبل هست" in await h._execute("add_keyword", "کودتست2", "...")
    assert "حذف شد" in await h._execute("remove_keyword", "کودتست2", "...")
    assert "نیست" in await h._execute("remove_keyword", "کودتست2", "...")
    assert "بنویس" in await h._execute("add_keyword", None, "...")
    assert "بنویس" in await h._execute("remove_keyword", None, "...")
    assert "بله" in await h._execute("has_keyword", "یاراترا", "...")
    assert "بنویس" in await h._execute("has_keyword", None, "...")
    assert "مجاز نیست" in await h._execute("get_param", "NOPE", "...")
    assert "LOG_LEVEL" in await h._execute("get_param", "LOG_LEVEL", "...")
    assert "مجاز نیست" in await h._execute("set_param", None, "NOPE_PARAM=1")
    assert "ذخیره شد" in await h._execute("set_param", None, "LOG_LEVEL=DEBUG")
    assert "قالب" in await h._execute("set_param", None, "no match")


@pytest.mark.asyncio
async def test_admin_status_and_logs(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    # seed a lead
    await temp_db.save_message({"id": "bale_1", "platform": "bale", "text": "قیمت یاراترا", "chat_name": "گروه", "keywords": "یاراترا", "opportunity_score": 80, "opportunity_type": "داغ", "timestamp": datetime.now()})
    await temp_db.save_lead({"id": "bale_1", "message_id": "bale_1", "platform": "bale", "text": "قیمت یاراترا", "chat_name": "گروه", "keywords": "یاراترا", "timestamp": datetime.now(), "opportunity_score": 80, "opportunity_type": "داغ"})
    out = await h._execute("status", None, "وضعیت")
    assert "پیام" in out
    out = await h._execute("lead_details", "last", "آخرین سرنخ")
    assert "سرنخ" in out
    # platform_status
    out = await h._execute("platform_status", "bale", "بله")
    assert "بله" in out or "فعال" in out
    # logs with missing file
    out = await h._execute("logs", None, "لاگ")
    assert isinstance(out, str)
    # report + crm patched
    monkeypatch.setattr(
        "core.services.daily_reporter.generate_executive_report",
        lambda *a, **k: str(tmp_path / "r.txt"),
    )
    monkeypatch.setattr(
        "core.services.crm_export.export_crm",
        lambda *a, **k: {"count": 1, "xlsx": "x.xlsx", "csv": "c.csv"},
    )
    out = await h._execute("report", None, "گزارش")
    assert "r.txt" in out
    out = await h._execute("crm", None, "crm")
    assert "CRM" in out


@pytest.mark.asyncio
async def test_admin_health_platform_status(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    await temp_db.update_last_poll("bale", datetime.now())
    out = await h._health()
    assert "سلامت" in out
    out = await h._platform_status("bale")
    assert "فعال" in out
    out = await h._platform_status("nope")
    assert isinstance(out, str)
    # no poll
    out = await h._platform_status("telegram")
    assert "فعالیت" in out


@pytest.mark.asyncio
async def test_admin_reply_no_sender(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    monkeypatch.setattr("handlers.admin_handler.messenger_registry.get_sender", lambda p: None)
    await h._reply({"chat_id": "1", "chat_type": "private", "user_id": "1"}, "hi")  # no crash


@pytest.mark.asyncio
async def test_admin_reply_sender(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    sent = []

    async def fake_sender(cid, ct, text):
        sent.append((cid, ct, text))

    monkeypatch.setattr("handlers.admin_handler.messenger_registry.get_sender", lambda p: fake_sender)
    await h._reply({"chat_id": "1", "chat_type": "private", "user_id": "1"}, "hi")
    assert sent == [("1", "private", "hi")]


@pytest.mark.asyncio
async def test_admin_handle_pairing_and_private(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    h.admin_ids = set()
    sent = []

    async def fake_reply(raw, text):
        sent.append(text)

    h._reply = fake_reply

    class Ev:
        data = {"platform": "bale", "chat_type": "private", "user_id": "123", "text": "وضعیت"}

    await h.handle(Ev())
    assert sent and "ADMIN_IDS=123" in sent[0]
    # group message from non-admin -> ignored
    h.admin_ids = {"999"}
    sent.clear()

    class Ev2:
        data = {"platform": "bale", "chat_type": "group", "user_id": "123", "text": "وضعیت", "chat_id": "g1"}

    await h.handle(Ev2())
    assert sent == []


@pytest.mark.asyncio
async def test_admin_handle_rule_and_ask(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    h.admin_ids = {"1"}
    sent = []

    async def fake_reply(raw, text):
        sent.append(text)

    h._reply = fake_reply
    h.safe_brain.process = AsyncMock(return_value="پاسخ از مغز")

    class Ev:
        data = {"platform": "bale", "chat_type": "private", "user_id": "1", "text": "وضعیت ربات", "chat_id": "1"}

    await h.handle(Ev())
    assert sent and sent[0] == "پاسخ از مغز"
    # process_text
    out = await h.process_text("راهنما")
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_admin_memory_live_context(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    h._remember("س", "ج")
    h._remember("س2", "ج2")
    mem = h._recent_memory(6)
    assert "گفتگوی اخیر" in mem
    h._log_request("یک درخواست تستی")
    assert "یک درخواست تستی" in open(tmp_path / "req.jsonl", encoding="utf-8").read()
    assert "سرنخ" in h._live_context() or "فعلاً" in h._live_context()


@pytest.mark.asyncio
async def test_admin_restart_and_update_env(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    out = await h._execute("restart", None, "ری‌استارت")
    assert "ری‌استارت" in out
    h._update_env("LOG_LEVEL", "INFO")
    content = open(tmp_path / ".env", encoding="utf-8").read()
    assert "LOG_LEVEL=INFO" in content
    # update existing
    h._update_env("LOG_LEVEL", "ERROR")
    content = open(tmp_path / ".env", encoding="utf-8").read()
    assert "LOG_LEVEL=ERROR" in content


def test_admin_rule_parse_platform(temp_db, tmp_path, monkeypatch):
    h = _mk_admin(temp_db, tmp_path, monkeypatch)
    assert h._platform_in("ایتا چطوره") == "eitaa"
    assert h._platform_in("سلام") is None
    assert h._extract_value("کلیدواژه «x» را اضافه کن") == "x"
    assert h._extract_value("هیچی") is None
