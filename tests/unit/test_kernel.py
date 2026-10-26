# tests/unit/test_kernel.py
"""تست کامل kernel — event_bus, models, logger.

قرارداد واقعی (استخراج‌شده از inspect):
  - EventBus.subscribe(event_name, handler, priority) -> None
  - EventBus.emit(event_name, data, source, priority) -> int (تعداد موفق)
  - Event dataclass: name, data, timestamp, priority, source, id
  - EventPriority: HIGH=0, NORMAL=1, LOW=2
  - Message dataclass: platform, platform_id, chat_id, ...
  - AnalyzedMessage, Lead, LeadStatus
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core.kernel.event_bus import Event, EventBus, EventPriority
from core.kernel.models import (
    AnalyzedMessage,
    Lead,
    LeadStatus,
    Message,
    _parse_datetime,
    _parse_keywords,
    _safe_dict,
    _safe_str,
)


# ═══════════════════════════════════════════════════════════
# Event dataclass
# ═══════════════════════════════════════════════════════════
class TestEvent:
    def test_event_creation(self):
        e = Event(name="test", data={"x": 1})
        assert e.name == "test"
        assert e.data == {"x": 1}
        assert e.priority == EventPriority.NORMAL

    def test_event_has_timestamp(self):
        e = Event(name="t", data=1)
        assert e.timestamp is not None

    def test_event_has_id(self):
        e = Event(name="t", data=1)
        assert e.id
        assert isinstance(e.id, str)

    def test_event_custom_priority(self):
        e = Event(name="t", data=1, priority=EventPriority.HIGH)
        assert e.priority == EventPriority.HIGH

    def test_event_with_source(self):
        e = Event(name="t", data=1, source="my_source")
        assert e.source == "my_source"


# ═══════════════════════════════════════════════════════════
# EventPriority
# ═══════════════════════════════════════════════════════════
class TestEventPriority:
    def test_enum_values(self):
        assert EventPriority.HIGH.value == 0
        assert EventPriority.NORMAL.value == 1
        assert EventPriority.LOW.value == 2

    def test_priority_ordering(self):
        assert EventPriority.HIGH.value < EventPriority.NORMAL.value
        assert EventPriority.NORMAL.value < EventPriority.LOW.value


# ═══════════════════════════════════════════════════════════
# EventBus — subscribe
# ═══════════════════════════════════════════════════════════
class TestEventBusSubscribe:
    def test_subscribe_registers_handler(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("test", h)
        assert bus.has_handler("test") is True

    def test_subscribe_empty_event_name_ignored(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("", h)
        assert bus.has_handler("") is False

    def test_subscribe_none_handler_ignored(self):
        bus = EventBus()
        bus.subscribe("test", None)
        assert bus.has_handler("test") is False

    def test_subscribe_multiple_handlers(self):
        bus = EventBus()
        async def h1(e): pass
        async def h2(e): pass
        bus.subscribe("test", h1)
        bus.subscribe("test", h2)
        assert bus.has_handler("test") is True


# ═══════════════════════════════════════════════════════════
# EventBus — emit
# ═══════════════════════════════════════════════════════════
class TestEventBusEmit:
    @pytest.mark.asyncio
    async def test_emit_calls_async_handler(self):
        bus = EventBus()
        called = []
        async def h(e): called.append(e.data)
        bus.subscribe("test", h)
        count = await bus.emit("test", {"x": 1})
        assert count == 1
        assert called == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_emit_calls_sync_handler(self):
        bus = EventBus()
        called = []
        def h(e): called.append(e.data)
        bus.subscribe("test", h)
        count = await bus.emit("test", {"x": 2})
        assert count == 1
        assert called == [{"x": 2}]

    @pytest.mark.asyncio
    async def test_emit_no_handlers_returns_zero(self):
        bus = EventBus()
        count = await bus.emit("nobody", {"x": 1})
        assert count == 0

    @pytest.mark.asyncio
    async def test_emit_empty_event_name_returns_zero(self):
        bus = EventBus()
        count = await bus.emit("", {"x": 1})
        assert count == 0

    @pytest.mark.asyncio
    async def test_emit_multiple_handlers(self):
        bus = EventBus()
        results = []
        async def h1(e): results.append(1)
        async def h2(e): results.append(2)
        bus.subscribe("test", h1)
        bus.subscribe("test", h2)
        count = await bus.emit("test", {})
        assert count == 2
        assert sorted(results) == [1, 2]

    @pytest.mark.asyncio
    async def test_emit_isolates_handler_exception(self):
        bus = EventBus()
        called = []
        async def bad(e): raise ValueError("boom")
        async def good(e): called.append("ok")
        bus.subscribe("test", bad)
        bus.subscribe("test", good)
        count = await bus.emit("test", {})
        assert count == 1
        assert "ok" in called

    @pytest.mark.asyncio
    async def test_emit_returns_count_of_successful_handlers(self):
        bus = EventBus()
        async def bad(e): raise ValueError("boom")
        async def good(e): pass
        bus.subscribe("test", bad)
        bus.subscribe("test", good)
        count = await bus.emit("test", {})
        assert count == 1


# ═══════════════════════════════════════════════════════════
# EventBus — priority ordering
# ═══════════════════════════════════════════════════════════
class TestEventBusPriority:
    @pytest.mark.asyncio
    async def test_high_priority_runs_first(self):
        bus = EventBus()
        order = []
        async def low(e): order.append("low")
        async def high(e): order.append("high")
        async def normal(e): order.append("normal")
        bus.subscribe("test", low, priority=EventPriority.LOW)
        bus.subscribe("test", high, priority=EventPriority.HIGH)
        bus.subscribe("test", normal, priority=EventPriority.NORMAL)
        await bus.emit("test", {})
        assert order == ["high", "normal", "low"]


# ═══════════════════════════════════════════════════════════
# EventBus — unsubscribe / has_handler
# ═══════════════════════════════════════════════════════════
class TestEventBusUnsubscribe:
    def test_has_handler_false_initially(self):
        bus = EventBus()
        assert bus.has_handler("test") is False

    def test_unsubscribe_with_handler(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("test", h)
        assert bus.has_handler("test") is True
        bus.unsubscribe("test", h)
        assert bus.has_handler("test") is False


# ═══════════════════════════════════════════════════════════
# EventBus — history / stats
# ═══════════════════════════════════════════════════════════
class TestEventBusStats:
    @pytest.mark.asyncio
    async def test_get_stats_returns_dict(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("test", h)
        await bus.emit("test", {})
        stats = bus.get_stats()
        assert isinstance(stats, dict)
        assert "total_events" in stats or "events_by_name" in stats

    @pytest.mark.asyncio
    async def test_event_history_records_events(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("test", h)
        await bus.emit("test", {"n": 1})
        history = bus.get_event_history()
        assert isinstance(history, list)
        assert len(history) >= 1

    @pytest.mark.asyncio
    async def test_get_event_names(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("evt.a", h)
        bus.subscribe("evt.b", h)
        names = bus.get_event_names()
        assert "evt.a" in names
        assert "evt.b" in names

    @pytest.mark.asyncio
    async def test_clear_history(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("test", h)
        await bus.emit("test", {})
        bus.clear_history()
        assert bus.get_event_history() == []

    @pytest.mark.asyncio
    async def test_clear_stats(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("test", h)
        await bus.emit("test", {})
        bus.clear_stats()
        stats = bus.get_stats()
        assert stats.get("total_events", 0) == 0


# ═══════════════════════════════════════════════════════════
# EventBus — wait_for
# ═══════════════════════════════════════════════════════════
class TestEventBusWaitFor:
    @pytest.mark.asyncio
    async def test_wait_for_receives_event(self):
        bus = EventBus()

        async def emitter():
            await asyncio.sleep(0.05)
            await bus.emit("test", {"x": 42})

        asyncio.create_task(emitter())
        event = await asyncio.wait_for(bus.wait_for("test"), timeout=1.0)
        assert event.data == {"x": 42}

    @pytest.mark.asyncio
    async def test_wait_for_timeout(self):
        bus = EventBus()
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(bus.wait_for("never"), timeout=0.1)


# ═══════════════════════════════════════════════════════════
# EventBus — remove_all_handlers
# ═══════════════════════════════════════════════════════════
class TestEventBusRemoveAll:
    def test_remove_all_handlers(self):
        bus = EventBus()
        async def h(e): pass
        bus.subscribe("a", h)
        bus.subscribe("b", h)
        bus.remove_all_handlers()
        assert bus.has_handler("a") is False
        assert bus.has_handler("b") is False


# ═══════════════════════════════════════════════════════════
# Message dataclass
# ═══════════════════════════════════════════════════════════
class TestMessage:
    def test_message_creation(self):
        msg = Message(
            platform="test",
            platform_id="p1",
            chat_id="c1",
            chat_name="chat",
            sender_name="s",
            text="hello",
            time=datetime(2026, 10, 25, 12, 0),
        )
        assert msg.platform == "test"
        assert msg.text == "hello"
        assert msg.platform_id == "p1"

    def test_message_optional_fields_default(self):
        msg = Message(
            platform="test",
            platform_id="p1",
            chat_id="c1",
            chat_name="chat",
            sender_name="s",
            text="hello",
            time=datetime.now(),
        )
        assert msg.chat_username is None or msg.chat_username == ""
        assert msg.chat_link is None or msg.chat_link == ""
        assert msg.chat_type is None or msg.chat_type == ""

    def test_message_to_dict_has_all_fields(self):
        msg = Message(
            platform="test",
            platform_id="p1",
            chat_id="c1",
            chat_name="chat",
            sender_name="s",
            text="hello",
            time=datetime(2026, 10, 25, 12, 0),
        )
        d = msg.to_dict()
        expected = {
            "platform", "platform_id", "chat_id", "chat_name",
            "chat_username", "chat_link", "chat_type",
            "sender_name", "text", "time", "raw_data",
        }
        assert expected.issubset(set(d.keys()))

    def test_message_to_dict_time_is_iso(self):
        dt = datetime(2026, 10, 25, 12, 30, 45)
        msg = Message(
            platform="test",
            platform_id="p1",
            chat_id="c1",
            chat_name="chat",
            sender_name="s",
            text="hello",
            time=dt,
        )
        d = msg.to_dict()
        assert d["time"] == "2026-10-25T12:30:45"

    def test_message_to_dict_raw_data_deep_copy(self):
        original_raw = {"k": [1, 2, 3]}
        msg = Message(
            platform="test",
            platform_id="p1",
            chat_id="c1",
            chat_name="chat",
            sender_name="s",
            text="hello",
            time=datetime.now(),
            raw_data=original_raw,
        )
        d = msg.to_dict()
        d["raw_data"]["k"].append(4)
        assert len(msg.raw_data["k"]) == 3


class TestMessageFromDict:
    def test_from_dict_basic(self):
        data = {
            "platform": "bale",
            "platform_id": "p1",
            "chat_id": "c1",
            "chat_name": "chat",
            "sender_name": "s",
            "text": "hello",
            "time": "2026-10-25T12:00:00",
        }
        msg = Message.from_dict(data)
        assert msg.platform == "bale"
        assert msg.text == "hello"
        assert isinstance(msg.time, datetime)


# ═══════════════════════════════════════════════════════════
# _parse_datetime
# ═══════════════════════════════════════════════════════════
class TestParseDatetime:
    def test_from_datetime(self):
        dt = datetime(2026, 10, 25, 12, 30)
        assert _parse_datetime(dt) == dt

    def test_from_iso_string(self):
        result = _parse_datetime("2026-10-25T12:30:45")
        assert result == datetime(2026, 10, 25, 12, 30, 45)

    def test_from_space_string(self):
        result = _parse_datetime("2026-10-25 12:30:45")
        assert result == datetime(2026, 10, 25, 12, 30, 45)

    def test_from_invalid_string_returns_datetime(self):
        result = _parse_datetime("not-a-date")
        assert isinstance(result, datetime)

    def test_from_none_returns_datetime(self):
        result = _parse_datetime(None)
        assert isinstance(result, datetime)


# ═══════════════════════════════════════════════════════════
# _parse_keywords (قرارداد واقعی)
# ═══════════════════════════════════════════════════════════
class TestParseKeywords:
    def test_from_list(self):
        assert _parse_keywords(["a", "b"]) == ["a", "b"]

    def test_from_list_strips(self):
        assert _parse_keywords(["  a  ", "b"]) == ["a", "b"]

    def test_from_list_skips_empty(self):
        assert _parse_keywords(["a", "", "b"]) == ["a", "b"]

    def test_from_list_skips_none(self):
        assert _parse_keywords(["a", None, "b"]) == ["a", "b"]

    def test_from_comma_string(self):
        assert _parse_keywords("a,b,c") == ["a", "b", "c"]

    def test_from_comma_string_strips(self):
        assert _parse_keywords(" a , b ") == ["a", "b"]

    def test_from_empty_string(self):
        assert _parse_keywords("") == []

    def test_from_none(self):
        assert _parse_keywords(None) == []

    def test_from_tuple(self):
        assert _parse_keywords(("a", "b")) == ["a", "b"]

    def test_from_set(self):
        result = _parse_keywords({"a", "b"})
        assert sorted(result) == ["a", "b"]

    def test_from_json_string_kept_as_literal(self):
        """_parse_keywords JSON را parse نمی‌کند — split با comma."""
        result = _parse_keywords('["x", "y"]')
        assert result == ['["x"', '"y"]']


# ═══════════════════════════════════════════════════════════
# _safe_dict / _safe_str (قرارداد واقعی)
# ═══════════════════════════════════════════════════════════
class TestSafeHelpers:
    def test_safe_dict_from_dict(self):
        assert _safe_dict({"x": 1}) == {"x": 1}

    def test_safe_dict_deep_copies(self):
        """_safe_dict باید deep-copy کند."""
        original = {"nested": {"k": [1, 2, 3]}}
        result = _safe_dict(original)
        result["nested"]["k"].append(4)
        assert len(original["nested"]["k"]) == 3

    def test_safe_dict_from_string_returns_empty(self):
        """_safe_dict فقط dict قبول می‌کند — string → {}."""
        assert _safe_dict('{"x": 1}') == {}

    def test_safe_dict_from_invalid_returns_empty(self):
        assert _safe_dict("invalid") == {}

    def test_safe_dict_from_none_returns_empty(self):
        assert _safe_dict(None) == {}

    def test_safe_dict_from_list_returns_empty(self):
        assert _safe_dict([1, 2, 3]) == {}

    def test_safe_str_from_str(self):
        assert _safe_str("hello") == "hello"

    def test_safe_str_from_none(self):
        assert _safe_str(None) == ""

    def test_safe_str_from_int(self):
        assert _safe_str(42) == "42"


# ═══════════════════════════════════════════════════════════
# Lead (قرارداد واقعی — ۲۱ فیلد)
# ═══════════════════════════════════════════════════════════
def _make_lead(**overrides) -> Lead:
    """ساخت Lead با مقادیر پیش‌فرض معتبر."""
    kwargs = {
        "id": "lead_1",
        "platform": "test",
        "message_id": "m1",
        "text": "کود مایع",
        "chat_name": "chat",
        "score": 50.0,
    }
    kwargs.update(overrides)
    return Lead(**kwargs)


class TestLead:
    def test_creation_minimal(self):
        lead = _make_lead()
        assert lead.id == "lead_1"
        assert lead.platform == "test"
        assert lead.score == 50.0

    def test_creation_full(self):
        lead = Lead(
            id="l1",
            message_id="m1",
            platform="bale",
            text="hello",
            chat_name="chat",
            score=75.0,
            keywords=["کود", "مایع"],
            opportunity_type="hot_lead",
            priority_tier="T1",
        )
        assert lead.score == 75.0
        assert lead.priority_tier == "T1"
        assert "کود" in lead.keywords

    def test_default_values(self):
        lead = Lead()
        assert lead.score == 0.0
        assert lead.priority_tier == "T0"
        assert lead.status == LeadStatus.NEW
        assert lead.is_important is False
        assert lead.my_note is None
        assert lead.opportunity_type is None

    # ─── is_hot / is_warm / is_cold ───
    def test_is_hot_at_threshold(self):
        """score=60 → is_hot=True (>= 60)."""
        assert _make_lead(score=60.0).is_hot() is True

    def test_is_hot_above(self):
        assert _make_lead(score=80.0).is_hot() is True

    def test_is_hot_below(self):
        assert _make_lead(score=59.9).is_hot() is False

    def test_is_warm_at_lower(self):
        """score=30 → is_warm=True (30 <= score < 60)."""
        assert _make_lead(score=30.0).is_warm() is True

    def test_is_warm_at_upper(self):
        assert _make_lead(score=59.9).is_warm() is True

    def test_is_warm_below_30(self):
        assert _make_lead(score=29.9).is_warm() is False

    def test_is_warm_above_60(self):
        assert _make_lead(score=60.0).is_warm() is False

    def test_is_cold_at_zero(self):
        assert _make_lead(score=0.0).is_cold() is True

    def test_is_cold_below_30(self):
        assert _make_lead(score=29.9).is_cold() is True

    def test_is_cold_at_30(self):
        assert _make_lead(score=30.0).is_cold() is False

    # ─── to_dict ───
    def test_to_dict(self):
        lead = _make_lead()
        d = lead.to_dict()
        assert isinstance(d, dict)
        assert d["id"] == "lead_1"
        assert d["score"] == 50.0
        assert d["status"] == "new"
        assert d["keywords"] == ""

    def test_to_dict_keywords_joined(self):
        lead = _make_lead(keywords=["کود", "مایع"])
        d = lead.to_dict()
        assert d["keywords"] == "کود,مایع"

    def test_to_dict_timestamp_iso(self):
        lead = _make_lead(timestamp=datetime(2026, 10, 25, 12, 0))
        d = lead.to_dict()
        assert d["timestamp"] == "2026-10-25T12:00:00"

    # ─── to_storage_dict (کلید opportunity_score) ───
    def test_to_storage_dict_score_key(self):
        lead = _make_lead(score=75.5)
        d = lead.to_storage_dict()
        assert d["opportunity_score"] == 75
        assert "score" not in d

    def test_to_storage_dict_is_important_int(self):
        lead = _make_lead(is_important=True)
        d = lead.to_storage_dict()
        assert d["is_important"] == 1

    def test_to_storage_dict_is_important_false(self):
        lead = _make_lead(is_important=False)
        d = lead.to_storage_dict()
        assert d["is_important"] == 0

    def test_to_storage_dict_has_all_keys(self):
        lead = _make_lead()
        d = lead.to_storage_dict()
        expected = {
            "id", "message_id", "platform", "text", "chat_id",
            "chat_name", "user_id", "timestamp", "keywords",
            "opportunity_score", "opportunity_type", "priority_tier",
            "matched_products", "brands", "media_type", "file_id",
            "is_important", "my_note",
        }
        missing = expected - set(d.keys())
        assert not missing, f"کلیدهای گم‌شده: {missing}"

    # ─── from_dict ───
    def test_from_dict_minimal(self):
        data = {
            "id": "lead_x",
            "platform": "bale",
            "message_id": "m1",
            "text": "hello",
            "chat_name": "chat",
        }
        lead = Lead.from_dict(data)
        assert lead.id == "lead_x"
        assert lead.platform == "bale"

    def test_from_dict_with_opportunity_score(self):
        """from_dict باید opportunity_score را به score map کند."""
        data = {
            "id": "l1",
            "platform": "t",
            "opportunity_score": 75,
        }
        lead = Lead.from_dict(data)
        assert lead.score == 75.0

    def test_from_dict_with_score(self):
        data = {"id": "l1", "platform": "t", "score": 45.5}
        lead = Lead.from_dict(data)
        assert lead.score == 45.5

    def test_from_dict_status_parsing(self):
        data = {"id": "l1", "platform": "t", "status": "contacted"}
        lead = Lead.from_dict(data)
        assert lead.status == LeadStatus.CONTACTED

    def test_from_dict_invalid_status_returns_new(self):
        data = {"id": "l1", "platform": "t", "status": "unknown_status"}
        lead = Lead.from_dict(data)
        assert lead.status == LeadStatus.NEW

    def test_from_dict_none_returns_empty(self):
        lead = Lead.from_dict(None)
        assert lead.id == ""

    # ─── from_lead_dict ───
    def test_from_lead_dict_with_opportunity_score(self):
        data = {
            "id": "lead_storage_1",
            "message_id": "msg_1",
            "platform": "bale",
            "text": "hello",
            "opportunity_score": 80,
            "opportunity_type": "hot",
            "priority_tier": "T1",
            "keywords": "کود,مایع",
        }
        lead = Lead.from_lead_dict(data)
        assert lead.score == 80.0
        assert lead.opportunity_type == "hot"
        assert lead.priority_tier == "T1"
        assert "کود" in lead.keywords

    def test_from_lead_dict_uses_created_at(self):
        data = {
            "id": "l1",
            "platform": "t",
            "created_at": "2026-10-25T12:00:00",
        }
        lead = Lead.from_lead_dict(data)
        assert lead.timestamp == datetime(2026, 10, 25, 12, 0)

    # ─── repr ───
    def test_repr(self):
        lead = _make_lead()
        r = repr(lead)
        assert isinstance(r, str)
        assert "lead_1" in r
        assert "50.0" in r

class TestAnalyzedMessageFromDict:
    def test_from_dict_minimal(self):
        data = {
            "platform": "bale",
            "platform_id": "p1",
            "chat_id": "c1",
            "chat_name": "chat",
            "sender_name": "s",
            "text": "hello",
            "time": "2026-10-25T12:00:00",
        }
        msg = AnalyzedMessage.from_dict(data)
        assert msg.platform == "bale"
        assert isinstance(msg.time, datetime)

    def test_from_dict_with_analysis(self):
        data = {
            "platform": "test",
            "platform_id": "p1",
            "chat_id": "c1",
            "chat_name": "chat",
            "sender_name": "s",
            "text": "hello",
            "time": "2026-10-25T12:00:00",
            "keywords": "a,b,c",
            "score": 75.5,
            "category": "hot",
            "metadata": {"key": "value"},
        }
        msg = AnalyzedMessage.from_dict(data)
        assert msg.keywords == ["a", "b", "c"]
        assert msg.score == 75.5
        assert msg.category == "hot"
        assert msg.metadata == {"key": "value"}

    def test_from_dict_none_returns_empty(self):
        msg = AnalyzedMessage.from_dict(None)
        assert msg.platform == ""

    def test_from_dict_invalid_score_returns_none(self):
        data = {
            "platform": "test",
            "platform_id": "p1",
            "chat_id": "c1",
            "chat_name": "chat",
            "sender_name": "s",
            "text": "hello",
            "time": "2026-10-25T12:00:00",
            "score": "not-a-number",
        }
        msg = AnalyzedMessage.from_dict(data)
        assert msg.score is None
        
        
# ═══════════════════════════════════════════════════════════
# LeadStatus
# ═══════════════════════════════════════════════════════════
class TestLeadStatus:
    def test_all_members(self):
        members = {s.name for s in LeadStatus}
        assert members == {"NEW", "CONTACTED", "FOLLOW_UP", "CONVERTED", "LOST"}

    def test_str_returns_value(self):
        assert str(LeadStatus.NEW) == "new"
        assert str(LeadStatus.CONTACTED) == "contacted"
        assert str(LeadStatus.FOLLOW_UP) == "follow_up"

    def test_from_value(self):
        assert LeadStatus("new") == LeadStatus.NEW
        assert LeadStatus("converted") == LeadStatus.CONVERTED

    def test_enum_comparison(self):
        assert LeadStatus.NEW == LeadStatus.NEW
        assert LeadStatus.NEW != LeadStatus.CONTACTED
        
# ═══════════════════════════════════════════════════════════
# AnalyzedMessage
# ═══════════════════════════════════════════════════════════
def _make_analyzed() -> AnalyzedMessage:
    return AnalyzedMessage(
        platform="test",
        platform_id="p1",
        chat_id="c1",
        chat_name="chat",
        sender_name="s",
        text="hello",
        time=datetime(2026, 10, 25, 12, 0),
    )


class TestAnalyzedMessage:
    def test_to_dict_has_fields(self):
        msg = _make_analyzed()
        d = msg.to_dict()
        assert isinstance(d, dict)
        assert "platform" in d
        assert "text" in d

    def test_to_dict_time_iso(self):
        msg = _make_analyzed()
        d = msg.to_dict()
        assert d["time"] == "2026-10-25T12:00:00"

    def test_from_dict_roundtrip(self):
        original = _make_analyzed()
        d = original.to_dict()
        restored = AnalyzedMessage.from_dict(d)
        assert restored.platform == original.platform
        assert restored.text == original.text


# ═══════════════════════════════════════════════════════════
# LeadStatus
# ═══════════════════════════════════════════════════════════
class TestLeadStatus:
    def test_str_returns_value(self):
        # LeadStatus.__str__ باید مقدار را برگرداند
        # اگر عضوی ندارد، skip
        members = list(LeadStatus)
        if not members:
            pytest.skip("LeadStatus بدون عضو")
        status = members[0]
        s = str(status)
        assert isinstance(s, str)
        assert len(s) > 0


# ═══════════════════════════════════════════════════════════
# Logger — smoke (get_stats)
# ═══════════════════════════════════════════════════════════
class TestLoggerStats:
    def test_get_stats_returns_expected_keys(self):
        from core.kernel.logger import get_logger
        logger = get_logger()
        stats = logger.get_stats()
        expected = {
            "version", "total_logs", "by_level", "last_log_time",
            "log_dir", "log_level", "console_enabled",
            "file_enabled", "file_path", "handlers",
            "stdout_enc", "stderr_enc", "utf8_status",
        }
        missing = expected - set(stats.keys())
        assert not missing, f"کلیدهای گم‌شده: {missing}"

    def test_get_stats_total_logs_increments(self):
        from core.kernel.logger import get_logger
        logger = get_logger()
        before = logger.get_stats()["total_logs"]
        logger.info("test message")
        after = logger.get_stats()["total_logs"]
        assert after == before + 1