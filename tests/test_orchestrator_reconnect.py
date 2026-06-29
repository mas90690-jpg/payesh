# tests/test_orchestrator_reconnect.py
import pytest
from datetime import datetime
from core.orchestrator import Orchestrator


class _FakeBus:
    def __init__(self):
        self.events = []
    async def emit(self, name, data):
        self.events.append((name, data))


class _FakeDB:
    async def get_last_poll(self, platform):
        return None
    async def update_last_poll(self, platform, ts):
        pass


class _FlakyCollector:
    """اولین connect شکست می‌خورد، دومی موفق — برای تستِ supervised reconnect."""
    platform_name = "telegram"
    def __init__(self):
        self.is_connected = False
        self.connect_calls = 0
        self.disconnected = 0
    async def disconnect(self):
        self.disconnected += 1
    async def connect(self):
        self.connect_calls += 1
        self.is_connected = self.connect_calls >= 2
        return self.is_connected
    async def fetch_history(self, since):
        return [{"id": "1", "text": "قیمت یاراترا"}]


@pytest.mark.asyncio
async def test_supervise_reconnect_recovers(monkeypatch):
    # sleep را بی‌اثر کن تا تست سریع باشد (backoff واقعی لازم نیست)
    import core.orchestrator as orch
    async def _no_sleep(*a, **k):
        return None
    monkeypatch.setattr(orch.asyncio, "sleep", _no_sleep)

    bus, db, col = _FakeBus(), _FakeDB(), _FlakyCollector()
    o = Orchestrator([col], bus, db)
    o.running = True
    await o._supervise_reconnect(col)

    assert col.is_connected is True            # بعد از تلاش دوم وصل شد
    assert col.connect_calls >= 2
    # catch-up اجرا شد و پیامِ ازدست‌رفته منتشر شد
    assert any(name == "message.received" for name, _ in bus.events)
