# tests/test_self_monitor.py
import json
import pytest
from core.self_monitor import SelfMonitor


def test_recent_errors_parsing(tmp_path, monkeypatch):
    log = tmp_path / "app.jsonl"
    from datetime import datetime
    now = datetime.now().isoformat()
    log.write_text(
        json.dumps({"ts": now, "level": "ERROR", "msg": "boom"}) + "\n" +
        json.dumps({"ts": now, "level": "INFO", "msg": "fine"}) + "\n" +
        json.dumps({"ts": now, "level": "CRITICAL", "msg": "bad"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr("core.self_monitor.LOG_FILE", str(log))
    sm = SelfMonitor(db=None)
    sm.interval = 10_000_000          # پنجرهٔ زمانیِ بزرگ تا همه را بشمارد
    errs = sm._recent_errors()
    assert len(errs) == 2             # فقط ERROR و CRITICAL، نه INFO


@pytest.mark.asyncio
async def test_check_once_no_crash_without_admins(tmp_path, monkeypatch):
    """بدون admin/sender نباید کرش کند (فقط هشدار را رد می‌کند)."""
    monkeypatch.setattr("core.self_monitor.LOG_FILE", str(tmp_path / "none.jsonl"))
    sm = SelfMonitor(db=None)
    sm.watch = []
    await sm.check_once()             # نباید استثنا بدهد
