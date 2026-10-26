# tests/unit/test_contracts.py
"""تست قراردادهای پایه — base_collector, base_handler, base_analyzer.

قرارداد واقعی (استخراج‌شده از کد):
  - BaseCollector.is_connected → property (بدون پرانتز)
  - BaseCollector.get_info() → کلیدهای: platform, status, is_connected, ...
  - BaseHandler.get_stats() → کلیدهای: handled_count, error_count, ...
  - BaseAnalyzer.combine_analyzers → async, (message, analyzers) → message
  - BaseAnalyzer.analyze → async
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.contracts.base_analyzer import BaseAnalyzer
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.contracts.base_handler import BaseHandler, HandlerPriority
from core.kernel.models import AnalyzedMessage


# ═══════════════════════════════════════════════════════════
# Fake implementations
# ═══════════════════════════════════════════════════════════
class FakeCollector(BaseCollector):
    platform_name = "fake"
    poll_interval_seconds = 60
    supports_history = True
    supports_websocket = False

    def __init__(self, connect_ok: bool = True):
        super().__init__()
        self._connect_ok = connect_ok
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> bool:
        self.connect_calls += 1
        if self._connect_ok:
            self._set_status(ConnectionStatus.CONNECTED)
        return self._connect_ok

    async def fetch_messages(self, since=None) -> list:
        return []

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._set_status(ConnectionStatus.DISCONNECTED)


class FakeHandler(BaseHandler):
    handler_name = "fake_handler"

    async def handle(self, event) -> None:
        self._record_handled(process_time=0.01)

    def can_handle(self, event_name: str) -> bool:
        return event_name == "test.event"


class FakeAnalyzer(BaseAnalyzer):
    """Analyzer ساده — async analyze، message را برمی‌گرداند."""

    def __init__(self, delay: float = 0.0):
        super().__init__()
        self._delay = delay
        self.calls = 0

    async def analyze(self, message):
        import asyncio
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return message


class FailingAnalyzer(BaseAnalyzer):
    """Analyzer که خطا می‌دهد — برای تست isolation."""

    async def analyze(self, message):
        raise ValueError("boom")


def _make_msg(msg_id: str = "t1") -> AnalyzedMessage:
    """ساخت یک AnalyzedMessage برای تست."""
    return AnalyzedMessage(
        platform="test",
        platform_id=msg_id,
        chat_id="c1",
        chat_name="chat",
        sender_name="s",
        text="hello",
        time=datetime.now(),
    )


# ═══════════════════════════════════════════════════════════
# BaseCollector — وضعیت
# ═══════════════════════════════════════════════════════════
class TestBaseCollectorStatus:
    def test_initial_status_is_disconnected(self):
        c = FakeCollector()
        assert c.status == ConnectionStatus.DISCONNECTED
        assert c.is_connected is False  # property

    @pytest.mark.asyncio
    async def test_connect_sets_connected(self):
        c = FakeCollector(connect_ok=True)
        ok = await c.connect()
        assert ok is True
        assert c.is_connected is True
        assert c.status == ConnectionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_failed_connect_stays_disconnected(self):
        c = FakeCollector(connect_ok=False)
        ok = await c.connect()
        assert ok is False
        assert c.is_connected is False
        assert c.status == ConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_disconnect_sets_disconnected(self):
        c = FakeCollector()
        await c.connect()
        await c.disconnect()
        assert c.is_connected is False
        assert c.disconnect_calls == 1


# ═══════════════════════════════════════════════════════════
# BaseCollector — خطا و ریست
# ═══════════════════════════════════════════════════════════
class TestBaseCollectorErrors:
    def test_initial_no_error(self):
        c = FakeCollector()
        assert c.get_last_error() is None

    def test_set_error_stores_message(self):
        c = FakeCollector()
        c._set_error("boom")
        assert c.get_last_error() == "boom"

    def test_reset_status_clears_error(self):
        c = FakeCollector()
        c._set_error("boom")
        c.reset_status()
        assert c.get_last_error() is None


# ═══════════════════════════════════════════════════════════
# BaseCollector — get_info
# ═══════════════════════════════════════════════════════════
class TestBaseCollectorGetInfo:
    def test_get_info_has_all_expected_keys(self):
        c = FakeCollector()
        info = c.get_info()
        expected = {
            "platform", "status", "is_connected",
            "retry_count", "max_retries", "last_error",
            "last_error_status", "poll_interval_seconds",
            "supports_history", "supports_websocket",
        }
        missing = expected - set(info.keys())
        assert not missing, f"کلیدهای گم‌شده: {missing}"

    def test_get_info_platform_and_status(self):
        c = FakeCollector()
        info = c.get_info()
        assert info["platform"] == "fake"
        assert info["status"] == "disconnected"
        assert info["is_connected"] is False

    @pytest.mark.asyncio
    async def test_get_info_after_connect(self):
        c = FakeCollector()
        await c.connect()
        info = c.get_info()
        assert info["is_connected"] is True
        assert info["status"] == "connected"


# ═══════════════════════════════════════════════════════════
# BaseCollector — health_check
# ═══════════════════════════════════════════════════════════
class TestBaseCollectorHealth:
    @pytest.mark.asyncio
    async def test_health_check_disconnected(self):
        c = FakeCollector()
        assert await c.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_connected(self):
        c = FakeCollector()
        await c.connect()
        assert await c.health_check() is True


# ═══════════════════════════════════════════════════════════
# BaseCollector — async context manager
# ═══════════════════════════════════════════════════════════
class TestBaseCollectorContext:
    @pytest.mark.asyncio
    async def test_async_with_calls_connect_and_disconnect(self):
        c = FakeCollector()
        async with c:
            assert c.is_connected is True
        assert c.connect_calls == 1
        assert c.disconnect_calls == 1

    @pytest.mark.asyncio
    async def test_aexit_called_on_exception(self):
        c = FakeCollector()
        with pytest.raises(ValueError):
            async with c:
                raise ValueError("test")
        assert c.disconnect_calls == 1


# ═══════════════════════════════════════════════════════════
# BaseHandler — آمار
# ═══════════════════════════════════════════════════════════
class TestBaseHandlerStats:
    def test_initial_stats_are_zero(self):
        h = FakeHandler()
        stats = h.get_stats()
        assert stats["handled_count"] == 0
        assert stats["error_count"] == 0
        assert stats["last_handled"] is None
        assert stats["avg_process_time"] == 0.0

    def test_get_stats_has_priority(self):
        h = FakeHandler()
        stats = h.get_stats()
        assert stats["priority"] == HandlerPriority.NORMAL.name

    @pytest.mark.asyncio
    async def test_handle_increments_count(self):
        h = FakeHandler()
        await h.handle(None)
        stats = h.get_stats()
        assert stats["handled_count"] == 1
        assert stats["last_handled"] is not None

    @pytest.mark.asyncio
    async def test_handle_updates_avg_process_time(self):
        h = FakeHandler()
        await h.handle(None)
        await h.handle(None)
        stats = h.get_stats()
        assert stats["handled_count"] == 2
        assert stats["avg_process_time"] > 0

    def test_reset_stats_clears_counters(self):
        h = FakeHandler()
        h._record_handled(process_time=0.5)
        h.reset_stats()
        stats = h.get_stats()
        assert stats["handled_count"] == 0
        assert stats["error_count"] == 0
        assert stats["avg_process_time"] == 0.0


# ═══════════════════════════════════════════════════════════
# BaseHandler — can_handle / priority / enabled
# ═══════════════════════════════════════════════════════════
class TestBaseHandlerContract:
    def test_can_handle_returns_bool(self):
        h = FakeHandler()
        assert h.can_handle("test.event") is True
        assert h.can_handle("other") is False

    def test_priority_is_enum(self):
        h = FakeHandler()
        assert isinstance(h.priority, HandlerPriority)
        assert h.priority == HandlerPriority.NORMAL

    def test_handler_name_from_subclass(self):
        h = FakeHandler()
        assert h.handler_name == "fake_handler"

    def test_enabled_default_true(self):
        h = FakeHandler()
        assert h.enabled is True


# ═══════════════════════════════════════════════════════════
# BaseAnalyzer — analyze
# ═══════════════════════════════════════════════════════════
class TestBaseAnalyzerAnalyze:
    @pytest.mark.asyncio
    async def test_analyze_is_async_and_returns_message(self):
        """analyze باید async باشد و message را برگرداند."""
        a = FakeAnalyzer()
        msg = _make_msg("an1")
        result = await a.analyze(msg)
        assert result is msg
        assert a.calls == 1

    @pytest.mark.asyncio
    async def test_analyze_can_be_called_multiple_times(self):
        a = FakeAnalyzer()
        msg = _make_msg("an2")
        await a.analyze(msg)
        await a.analyze(msg)
        assert a.calls == 2


# ═══════════════════════════════════════════════════════════
# BaseAnalyzer — combine_analyzers
# ═══════════════════════════════════════════════════════════
class TestBaseAnalyzerCombine:
    @pytest.mark.asyncio
    async def test_combine_with_empty_returns_message(self):
        """analyzers=[] → همان message برگردانده می‌شود."""
        a = FakeAnalyzer()
        msg = _make_msg("cmb1")
        result = await a.combine_analyzers(msg, [])
        assert result is msg

    @pytest.mark.asyncio
    async def test_combine_with_none_returns_message(self):
        """analyzers=None → همان message برگردانده می‌شود."""
        a = FakeAnalyzer()
        msg = _make_msg("cmb2")
        result = await a.combine_analyzers(msg, None)
        assert result is msg

    @pytest.mark.asyncio
    async def test_combine_with_single_analyzer(self):
        """یک analyzer → analyze صدا زده می‌شود."""
        a = FakeAnalyzer()
        other = FakeAnalyzer()
        msg = _make_msg("cmb3")
        result = await a.combine_analyzers(msg, [other])
        assert result is msg
        assert other.calls == 1

    @pytest.mark.asyncio
    async def test_combine_with_multiple_analyzers(self):
        """چند analyzer → همه صدا زده می‌شوند."""
        a = FakeAnalyzer()
        a2 = FakeAnalyzer()
        a3 = FakeAnalyzer()
        msg = _make_msg("cmb4")
        await a.combine_analyzers(msg, [a2, a3])
        assert a2.calls == 1
        assert a3.calls == 1

    @pytest.mark.asyncio
    async def test_combine_isolates_failing_analyzer(self):
        """خطای یک analyzer → بقیه ادامه می‌دهند (بدون raise)."""
        a = FakeAnalyzer()
        bad = FailingAnalyzer()
        good = FakeAnalyzer()
        msg = _make_msg("cmb5")
        result = await a.combine_analyzers(msg, [bad, good])
        assert result is msg
        assert good.calls == 1

    @pytest.mark.asyncio
    async def test_combine_skips_none_analyzer(self):
        """None در لیست → skip می‌شود."""
        a = FakeAnalyzer()
        good = FakeAnalyzer()
        msg = _make_msg("cmb6")
        result = await a.combine_analyzers(msg, [None, good])
        assert result is msg
        assert good.calls == 1