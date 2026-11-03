# tests/unit/test_bale_bot.py
"""
Unit tests for collectors/bale_bot.py — v3.0 (from scratch).

Coverage:
  - Class methods: is_enabled, _detect_media_type, _chunk_text, _env_bool
  - State: seen, offset persistence
  - _process_update: all chat types, filters, media, metadata
  - fetch_messages
  - _call: retries, errors
  - send_message: short/long/fail
  - Properties
  - Poll loop cancel
  - check_health

Run:
  pytest tests/unit/test_bale_bot.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.bale_bot import (
    BaleBotCollector,
    _env_bool,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    monkeypatch.setenv("BALE_BOT_TOKEN", "test-token")
    monkeypatch.setenv("BALE_BOT_ENABLED", "true")
    monkeypatch.setenv("BALE_BOT_POLL_SECONDS", "30")
    monkeypatch.setenv("BALE_BOT_KEYWORDS_ONLY", "false")
    return BaleBotCollector()


def _update(
    text="hello",
    chat_id="100",
    msg_id="1",
    chat_type="private",
    title=None,
    first_name="Ali",
    last_name="Rezaei",
    date=1700000000,
    caption=None,
    from_user=True,
    username=None,
):
    """Build a Telegram-shaped update dict."""
    chat = {"id": int(chat_id), "type": chat_type}
    if title:
        chat["title"] = title
    else:
        chat["first_name"] = first_name
        chat["last_name"] = last_name
    if username:
        chat["username"] = username

    msg = {
        "message_id": int(msg_id),
        "chat": chat,
        "date": date,
    }
    if text is not None:
        msg["text"] = text
    if caption is not None:
        msg["caption"] = caption
    if from_user:
        msg["from"] = {"id": 42, "first_name": "Ali", "last_name": "Rezaei"}

    return {"update_id": 1, "message": msg}


# ═══════════════════════════════════════════════════════════
# 1) _env_bool
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True), ("TRUE", True), ("1", True),
        ("yes", True), ("on", True),
        ("false", False), ("0", False), ("no", False),
        ("off", False), ("", False), ("garbage", False),
    ],
)
def test_env_bool(monkeypatch, value, expected):
    monkeypatch.setenv("TEST_VAR", value)
    assert _env_bool("TEST_VAR") is expected


def test_env_bool_default(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    assert _env_bool("TEST_VAR", default=True) is True
    assert _env_bool("TEST_VAR", default=False) is False


# ═══════════════════════════════════════════════════════════
# 2) is_enabled
# ═══════════════════════════════════════════════════════════
def test_is_enabled_false_when_no_token(monkeypatch):
    monkeypatch.setenv("BALE_BOT_ENABLED", "true")
    monkeypatch.delenv("BALE_BOT_TOKEN", raising=False)
    assert BaleBotCollector.is_enabled() is False


def test_is_enabled_false_when_token_whitespace(monkeypatch):
    monkeypatch.setenv("BALE_BOT_ENABLED", "true")
    monkeypatch.setenv("BALE_BOT_TOKEN", "   ")
    assert BaleBotCollector.is_enabled() is False


def test_is_enabled_false_when_disabled(monkeypatch):
    monkeypatch.setenv("BALE_BOT_ENABLED", "false")
    monkeypatch.setenv("BALE_BOT_TOKEN", "x")
    assert BaleBotCollector.is_enabled() is False


def test_is_enabled_true(monkeypatch):
    monkeypatch.setenv("BALE_BOT_ENABLED", "true")
    monkeypatch.setenv("BALE_BOT_TOKEN", "x")
    assert BaleBotCollector.is_enabled() is True


# ═══════════════════════════════════════════════════════════
# 3) _detect_media_type
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "msg,expected",
    [
        ({"photo": [{"file_id": "x"}]}, "photo"),
        ({"video": {"file_id": "x"}}, "video"),
        ({"voice": {"file_id": "x"}}, "voice"),
        ({"audio": {"file_id": "x"}}, "audio"),
        ({"document": {"file_id": "x"}}, "document"),
        ({"sticker": {"file_id": "x"}}, "sticker"),
        ({}, "text"),
        ({"text": "hi"}, "text"),
    ],
)
def test_detect_media_type(msg, expected):
    assert BaleBotCollector._detect_media_type(msg) == expected


def test_detect_media_type_priority():
    msg = {"photo": [{"file_id": "x"}], "text": "hi"}
    assert BaleBotCollector._detect_media_type(msg) == "photo"


# ═══════════════════════════════════════════════════════════
# 4) _chunk_text
# ═══════════════════════════════════════════════════════════
def test_chunk_text_short():
    assert BaleBotCollector._chunk_text("hi", 100) == ["hi"]


def test_chunk_text_empty():
    assert BaleBotCollector._chunk_text("", 100) == [""]


def test_chunk_text_exact_size():
    text = "x" * 100
    assert BaleBotCollector._chunk_text(text, 100) == [text]


def test_chunk_text_long():
    text = "word " * 100
    chunks = BaleBotCollector._chunk_text(text, 100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 100


def test_chunk_text_prefers_newline():
    text = "a" * 90 + "\n" + "b" * 90
    chunks = BaleBotCollector._chunk_text(text, 100)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 90


def test_chunk_text_prefers_space():
    text = "a" * 80 + " " + "b" * 80
    chunks = BaleBotCollector._chunk_text(text, 100)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 80


def test_chunk_text_no_break_char():
    text = "x" * 250
    chunks = BaleBotCollector._chunk_text(text, 100)
    assert len(chunks) == 3
    assert all(len(c) <= 100 for c in chunks)


# ═══════════════════════════════════════════════════════════
# 5) Seen persistence
# ═══════════════════════════════════════════════════════════
def test_load_seen_empty(collector):
    assert collector._seen_ids == set()


def test_save_and_load_seen(collector):
    collector._seen_ids = {"a", "b", "c"}
    collector._save_seen()
    collector._seen_ids = set()
    collector._load_seen()
    assert collector._seen_ids == {"a", "b", "c"}


def test_load_seen_corrupt_json(collector):
    p = collector._seen_path
    with open(p, "w", encoding="utf-8") as f:
        f.write("not json")
    collector._load_seen()
    assert collector._seen_ids == set()


def test_load_seen_dict_format(collector):
    p = collector._seen_path
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"not": "a list"}, f)
    collector._load_seen()
    assert collector._seen_ids == set()


# ═══════════════════════════════════════════════════════════
# 6) Offset persistence
# ═══════════════════════════════════════════════════════════
def test_load_offset_default(collector):
    assert collector._offset == 0


def test_save_and_load_offset(collector):
    collector._offset = 123
    collector._save_offset()
    collector._offset = 0
    collector._load_offset()
    assert collector._offset == 123


def test_load_offset_corrupt(collector):
    p = collector._offset_path
    with open(p, "w", encoding="utf-8") as f:
        f.write("not json")
    collector._load_offset()
    assert collector._offset == 0


def test_load_offset_invalid_type(collector):
    p = collector._offset_path
    with open(p, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    collector._load_offset()
    assert collector._offset == 0


# ═══════════════════════════════════════════════════════════
# 7) _process_update — basic
# ═══════════════════════════════════════════════════════════
def test_process_update_private(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(_update(chat_type="private")))
    assert len(collector._messages_queue) == 1
    msg = collector._messages_queue[0]
    assert msg["platform"] == "bale"
    assert msg["chat_type"] == "private"
    assert msg["text"] == "hello"
    assert isinstance(msg["timestamp"], datetime)


def test_process_update_group(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(
        _update(chat_type="group", title="Test Group"),
    ))
    assert len(collector._messages_queue) == 1
    assert collector._messages_queue[0]["chat_type"] == "group"
    assert collector._messages_queue[0]["chat_name"] == "Test Group"


def test_process_update_supergroup_maps_to_group(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(
        _update(chat_type="supergroup", title="SG"),
    ))
    assert collector._messages_queue[0]["chat_type"] == "group"


def test_process_update_channel(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(
        _update(chat_type="channel", title="My Channel"),
    ))
    assert collector._messages_queue[0]["chat_type"] == "channel"


def test_process_update_unknown_type_defaults_private(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(_update(chat_type="weird")))
    assert collector._messages_queue[0]["chat_type"] == "private"


# ═══════════════════════════════════════════════════════════
# 8) _process_update — filters
# ═══════════════════════════════════════════════════════════
def test_process_update_no_text(collector):
    collector._connected = True
    u = _update(text=None)
    asyncio.run(collector._process_update(u))
    assert len(collector._messages_queue) == 0


def test_process_update_empty_text(collector):
    collector._connected = True
    asyncio.run(collector._process_update(_update(text="   ")))
    assert len(collector._messages_queue) == 0


def test_process_update_no_message(collector):
    collector._connected = True
    asyncio.run(collector._process_update({"update_id": 1}))
    assert len(collector._messages_queue) == 0


def test_process_update_duplicate_skipped(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    u = _update(text="hello", chat_id="100", msg_id="1")
    asyncio.run(collector._process_update(u))
    asyncio.run(collector._process_update(u))
    assert len(collector._messages_queue) == 1


def test_process_update_keywords_only_skips(collector):
    collector._connected = True
    collector._keywords_only = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=False)

    asyncio.run(collector._process_update(_update(text="hello")))
    assert len(collector._messages_queue) == 0


def test_process_update_group_always_filters_kw(collector):
    collector._connected = True
    collector._keywords_only = False
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=False)

    asyncio.run(collector._process_update(
        _update(text="no kw", chat_type="group", title="G"),
    ))
    assert len(collector._messages_queue) == 0


# ═══════════════════════════════════════════════════════════
# 9) _process_update — caption + media
# ═══════════════════════════════════════════════════════════
def test_process_update_caption_fallback(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(
        _update(text=None, caption="caption text"),
    ))
    assert len(collector._messages_queue) == 1
    assert collector._messages_queue[0]["text"] == "caption text"


def test_process_update_photo_file_id(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    u = _update(text="photo desc")
    u["message"]["photo"] = [{"file_id": "small"}, {"file_id": "large"}]
    asyncio.run(collector._process_update(u))
    msg = collector._messages_queue[0]
    assert msg["media_type"] == "photo"
    assert msg["file_id"] == "large"


def test_process_update_video_file_id(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    u = _update(text="video desc")
    u["message"]["video"] = {"file_id": "vid123"}
    asyncio.run(collector._process_update(u))
    msg = collector._messages_queue[0]
    assert msg["media_type"] == "video"
    assert msg["file_id"] == "vid123"


def test_process_update_document_file_id(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    u = _update(text="doc desc")
    u["message"]["document"] = {"file_id": "doc123"}
    asyncio.run(collector._process_update(u))
    assert collector._messages_queue[0]["file_id"] == "doc123"


# ═══════════════════════════════════════════════════════════
# 10) _process_update — chat metadata
# ═══════════════════════════════════════════════════════════
def test_process_update_chat_username_link(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(
        _update(chat_type="channel", title="C", username="mychan"),
    ))
    msg = collector._messages_queue[0]
    assert msg["chat_username"] == "mychan"
    assert msg["chat_link"] == "https://ble.ir/mychan"


def test_process_update_no_username_no_link(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(_update(chat_type="private")))
    msg = collector._messages_queue[0]
    assert msg["chat_username"] is None
    assert msg["chat_link"] is None


def test_process_update_chat_name_from_first_last(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    asyncio.run(collector._process_update(
        _update(chat_type="private", first_name="Reza", last_name="Ahmadi"),
    ))
    assert collector._messages_queue[0]["chat_name"] == "Reza Ahmadi"


def test_process_update_reply_to(collector):
    collector._connected = True
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    u = _update(text="reply msg")
    u["message"]["reply_to_message"] = {"message_id": 99}
    asyncio.run(collector._process_update(u))
    assert collector._messages_queue[0]["reply_to"] == "99"


# ═══════════════════════════════════════════════════════════
# 11) fetch_messages
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_returns_and_clears(collector):
    collector._connected = True
    collector._messages_queue.append({"id": "1", "text": "hello"})
    collector._messages_queue.append({"id": "2", "text": "world"})

    msgs = asyncio.run(collector.fetch_messages())
    assert len(msgs) == 2
    assert len(collector._messages_queue) == 0


def test_fetch_messages_saves_seen(collector):
    collector._connected = True
    collector._seen_ids = {"a", "b"}
    collector._messages_queue.append({"id": "x"})
    asyncio.run(collector.fetch_messages())
    collector._seen_ids = set()
    collector._load_seen()
    assert "a" in collector._seen_ids
    assert "b" in collector._seen_ids


# ═══════════════════════════════════════════════════════════
# 12) _call
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_call_not_connected(collector):
    collector._connected = False
    collector._session = MagicMock()
    assert await collector._call("getMe") is None


@pytest.mark.asyncio
async def test_call_no_session(collector):
    collector._connected = True
    collector._session = None
    assert await collector._call("getMe") is None


@pytest.mark.asyncio
async def test_call_success(collector):
    collector._connected = True
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"ok": True, "result": {}})

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=cm)
    collector._session = session

    result = await collector._call("getMe")
    assert result == {"ok": True, "result": {}}


@pytest.mark.asyncio
async def test_call_http_error_no_retry_for_getMe(collector):
    collector._connected = True
    resp = MagicMock()
    resp.status = 500
    resp.text = AsyncMock(return_value="err")

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=cm)
    collector._session = session

    assert await collector._call("getMe") is None
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_call_retries_sendMessage(collector):
    collector._connected = True

    resp_bad = MagicMock()
    resp_bad.status = 500
    resp_bad.text = AsyncMock(return_value="err")

    resp_ok = MagicMock()
    resp_ok.status = 200
    resp_ok.json = AsyncMock(return_value={"ok": True})

    def _cm(resp):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    session = MagicMock()
    session.post = MagicMock(side_effect=[_cm(resp_bad), _cm(resp_ok)])
    collector._session = session

    with patch("asyncio.sleep", AsyncMock()):
        result = await collector._call("sendMessage", chat_id="1", text="hi")

    assert result == {"ok": True}
    assert session.post.call_count == 2


@pytest.mark.asyncio
async def test_call_bad_json(collector):
    collector._connected = True
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(side_effect=ValueError("bad"))
    resp.text = AsyncMock(return_value="not json")

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=cm)
    collector._session = session

    assert await collector._call("getMe") is None


# ═══════════════════════════════════════════════════════════
# 13) send_message
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_send_message_not_connected(collector):
    collector._session = None
    assert await collector.send_message("123", "private", "hi") is False


@pytest.mark.asyncio
async def test_send_message_empty_text(collector):
    collector._session = MagicMock()
    assert await collector.send_message("123", "private", "") is False


@pytest.mark.asyncio
async def test_send_message_success(collector):
    collector._session = MagicMock()
    collector._connected = True
    collector._call = AsyncMock(return_value={"ok": True})
    assert await collector.send_message("123", "private", "hi") is True
    assert collector._call.call_count == 1


@pytest.mark.asyncio
async def test_send_message_long_text_chunks(collector):
    collector._session = MagicMock()
    collector._connected = True
    collector._call = AsyncMock(return_value={"ok": True})
    long_text = "x" * 8000
    assert await collector.send_message("123", "private", long_text) is True
    assert collector._call.call_count >= 2


@pytest.mark.asyncio
async def test_send_message_fails(collector):
    collector._session = MagicMock()
    collector._connected = True
    collector._call = AsyncMock(return_value={"ok": False})
    assert await collector.send_message("123", "private", "hi") is False


# ═══════════════════════════════════════════════════════════
# 14) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "bale_bot"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 30.0


def test_supports_history(collector):
    assert collector.supports_history is False


def test_supports_websocket(collector):
    assert collector.supports_websocket is False


def test_is_connected_property(collector):
    collector._connected = True
    assert collector.is_connected is True
    collector._connected = False
    assert collector.is_connected is False


# ═══════════════════════════════════════════════════════════
# 15) _poll_loop — _running flag after cancel
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_poll_loop_sets_running_false_on_cancel(collector):
    collector._running = True
    collector._connected = True
    collector._session = MagicMock()

    async def _slow_call(*a, **kw):
        await asyncio.sleep(10)
        return None

    collector._call = _slow_call

    task = asyncio.create_task(collector._poll_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert collector._running is False


# ═══════════════════════════════════════════════════════════
# 16) check_health
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_check_health_false(collector):
    collector._connected = False
    assert await collector.check_health() is False


@pytest.mark.asyncio
async def test_check_health_true(collector):
    collector._connected = True
    assert await collector.check_health() is True


# ═══════════════════════════════════════════════════════════
# 17) dump_dom (stub)
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_dump_dom_returns_empty(collector):
    assert await collector.dump_dom() == ""