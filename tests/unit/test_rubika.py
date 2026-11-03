# tests/unit/test_rubika.py
"""
Unit tests for collectors/rubika.py — v3.0 (from scratch).

Coverage:
  ─── Pure functions ───
  - _obj_to_dict (None / dict / to_dict method / to_dict attr / non-dict)
  - _extract_name_from_obj (title / first_name / name / abs_object / chat)
  - _extract_timestamp (seconds / milliseconds / original_update / invalid)
  - _extract_sender_name (author_title / sender_name / truncate)
  - _get_history_limit (default / custom / invalid / negative)
  - _get_max_chats (default / custom / invalid / negative)

  ─── Static helpers ───
  - _normalize_phone (+98 / 98 / 0 / no prefix)
  - _is_system_name (Persian / English / prefix / empty / None)
  - _build_link (username / None / empty)
  - _dig (nested / missing / None / non-dict)
  - _extract_list (dict / nested / to_dict / attribute / empty)

  ─── Async methods ───
  - _resolve_chat (cache / from update / from API / fallback to GUID)
  - _to_unified (valid text / empty text / system chat / group / channel / photo)
  - _resolve_name_from_api (get_chat_info / get_chat / get_object / all fail)

  ─── State ───
  - _load_first_run / _save_state / _load_last_run_at
  - _load_seen / _save_seen

  ─── Properties ───
  - platform_name / poll_interval / supports_history / supports_websocket

  ─── fetch_messages ───
  - not connected → empty
  - no client → empty

  ─── check_health ───
  - connected / disconnected

Run:
  pytest tests/unit/test_rubika.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.rubika import (
    RubikaCollector,
    _extract_name_from_obj,
    _get_history_limit,
    _get_max_chats,
    _obj_to_dict,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "RUBIKA_PHONE", "RUBIKA_POLL_SECONDS", "RUBIKA_MAX_CHATS",
    "RUBIKA_HISTORY_LIMIT", "RUBIKA_WEB_URL",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("RUBIKA_PHONE", "09120000000")
    monkeypatch.setenv("RUBIKA_POLL_SECONDS", "999")
    return RubikaCollector(phone="09120000000", poll_seconds=999)


# ═══════════════════════════════════════════════════════════
# 1) _obj_to_dict
# ═══════════════════════════════════════════════════════════
def test_obj_to_dict_none():
    assert _obj_to_dict(None) == {}


def test_obj_to_dict_plain_dict():
    assert _obj_to_dict({"a": 1}) == {"a": 1}


def test_obj_to_dict_to_dict_method():
    class ObjA:
        def to_dict(self):
            return {"x": 2}
    assert _obj_to_dict(ObjA()) == {"x": 2}


def test_obj_to_dict_to_dict_attribute():
    class ObjB:
        to_dict = {"y": 3}
    assert _obj_to_dict(ObjB()) == {"y": 3}


def test_obj_to_dict_to_dict_raises():
    class ObjC:
        def to_dict(self):
            raise RuntimeError("boom")
    assert _obj_to_dict(ObjC()) == {}


def test_obj_to_dict_to_dict_returns_non_dict():
    class ObjD:
        def to_dict(self):
            return [1, 2]
    assert _obj_to_dict(ObjD()) == {}


def test_obj_to_dict_object_without_to_dict():
    class ObjE:
        pass
    assert _obj_to_dict(ObjE()) == {}


# ═══════════════════════════════════════════════════════════
# 2) _extract_name_from_obj
# ═══════════════════════════════════════════════════════════
def test_extract_name_none():
    assert _extract_name_from_obj(None) == (None, None)


def test_extract_name_title():
    assert _extract_name_from_obj({"title": "گروه تست"}) == (
        "گروه تست", None
    )


def test_extract_name_first_name():
    n, u = _extract_name_from_obj(
        {"first_name": "علی", "last_name": "رضایی"}
    )
    assert n == "علی رضایی"


def test_extract_name_name_key():
    n, _ = _extract_name_from_obj({"name": "کانال من"})
    assert n == "کانال من"


def test_extract_name_username():
    n, u = _extract_name_from_obj(
        {"title": "چت", "username": "mychat"}
    )
    assert n == "چت"
    assert u == "mychat"


def test_extract_name_abs_object_nested():
    obj = {"abs_object": {"title": "نستد"}}
    n, _ = _extract_name_from_obj(obj)
    assert n == "نستد"


def test_extract_name_chat_nested():
    obj = {"chat": {"title": "درون چت"}}
    n, _ = _extract_name_from_obj(obj)
    assert n == "درون چت"


def test_extract_name_attribute_fallback():
    class ObjF:
        title = "از اتریبیوت"
        username = "user1"
    n, u = _extract_name_from_obj(ObjF())
    assert n == "از اتریبیوت"
    assert u == "user1"


def test_extract_name_empty_dict():
    assert _extract_name_from_obj({}) == (None, None)


def test_extract_name_whitespace_title():
    n, _ = _extract_name_from_obj({"title": "   "})
    assert n is None


# ═══════════════════════════════════════════════════════════
# 3) _normalize_phone
# ═══════════════════════════════════════════════════════════
class TestNormalizePhone:
    def test_plus98(self):
        assert RubikaCollector._normalize_phone("+989121234567") == "09121234567"

    def test_98(self):
        assert RubikaCollector._normalize_phone("989121234567") == "09121234567"

    def test_zero(self):
        assert RubikaCollector._normalize_phone("09121234567") == "09121234567"

    def test_no_prefix(self):
        assert RubikaCollector._normalize_phone("9121234567") == "09121234567"

    def test_whitespace(self):
        assert RubikaCollector._normalize_phone("  09121234567  ") == "09121234567"

    def test_empty(self):
        assert RubikaCollector._normalize_phone("") == "0"


# ═══════════════════════════════════════════════════════════
# 4) _is_system_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("پیام‌های ذخیره شده", True),
        ("ذخیره‌شده", True),
        ("Saved Messages", True),
        ("You", True),
        ("روبیکا", True),
        ("Rubika", True),
        ("کانال ایجاد شد", True),
        ("گروه ایجاد شد", True),
        ("گروه دوستان", False),
        ("", False),
        ("   ", False),
        (None, False),
    ],
)
def test_is_system_name(collector, name, expected):
    assert collector._is_system_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 5) _extract_timestamp
# ═══════════════════════════════════════════════════════════
class TestExtractTimestamp:
    def test_seconds(self, collector):
        u = MagicMock()
        u.time = 1700000000
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        ts = collector._extract_timestamp(u)
        assert ts is not None
        assert ts.year >= 2023

    def test_milliseconds(self, collector):
        u = MagicMock()
        u.time = 1700000000000
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        ts = collector._extract_timestamp(u)
        assert ts is not None
        assert ts.year >= 2023

    def test_none(self, collector):
        u = MagicMock()
        u.time = None
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        assert collector._extract_timestamp(u) is None

    def test_invalid(self, collector):
        u = MagicMock()
        u.time = "not-a-number"
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        assert collector._extract_timestamp(u) is None

    def test_from_original_update(self, collector):
        u = MagicMock()
        u.time = None
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = {"time": 1700000000}
        ts = collector._extract_timestamp(u)
        assert ts is not None


# ═══════════════════════════════════════════════════════════
# 6) _extract_sender_name
# ═══════════════════════════════════════════════════════════
class TestExtractSenderName:
    def test_author_title(self):
        u = MagicMock()
        u.author_title = "علی"
        u.sender_name = None
        u.author_name = None
        u.from_name = None
        assert RubikaCollector._extract_sender_name(u) == "علی"

    def test_sender_name(self):
        u = MagicMock()
        u.author_title = None
        u.sender_name = "رضا"
        u.author_name = None
        u.from_name = None
        assert RubikaCollector._extract_sender_name(u) == "رضا"

    def test_none(self):
        u = MagicMock()
        u.author_title = None
        u.sender_name = None
        u.author_name = None
        u.from_name = None
        assert RubikaCollector._extract_sender_name(u) == ""

    def test_truncate(self):
        u = MagicMock()
        u.author_title = "x" * 100
        u.sender_name = None
        u.author_name = None
        u.from_name = None
        assert len(RubikaCollector._extract_sender_name(u)) == 60


# ═══════════════════════════════════════════════════════════
# 7) _build_link
# ═══════════════════════════════════════════════════════════
class TestBuildLink:
    def test_username(self):
        link = RubikaCollector._build_link("mychat", "guid", "group")
        assert link == "https://rubika.ir/mychat"

    def test_none_username(self):
        assert RubikaCollector._build_link(None, "guid", "group") is None

    def test_empty_username(self):
        assert RubikaCollector._build_link("", "guid", "group") is None


# ═══════════════════════════════════════════════════════════
# 8) _dig
# ═══════════════════════════════════════════════════════════
class TestDig:
    def test_nested(self):
        assert RubikaCollector._dig({"a": {"b": {"c": 1}}}, "a", "b", "c") == 1

    def test_missing(self):
        assert RubikaCollector._dig({"a": 1}, "b") is None

    def test_none(self):
        assert RubikaCollector._dig(None, "a") is None

    def test_non_dict(self):
        assert RubikaCollector._dig("str", "a") is None

    def test_single_key(self):
        assert RubikaCollector._dig({"x": 5}, "x") == 5


# ═══════════════════════════════════════════════════════════
# 9) _extract_list
# ═══════════════════════════════════════════════════════════
class TestExtractList:
    def test_dict_direct(self):
        assert RubikaCollector._extract_list({"chats": [1, 2]}, "chats") == [1, 2]

    def test_dict_nested(self):
        resp = {"data": {"chats": [3, 4]}}
        assert RubikaCollector._extract_list(resp, "chats") == [3, 4]

    def test_to_dict(self):
        class ObjG:
            def to_dict(self):
                return {"chats": [5]}
        assert RubikaCollector._extract_list(ObjG(), "chats") == [5]

    def test_none(self):
        assert RubikaCollector._extract_list(None, "chats") == []

    def test_missing_key(self):
        assert RubikaCollector._extract_list({"x": 1}, "chats") == []

    def test_attribute(self):
        class ObjH:
            chats = [7, 8]
        assert RubikaCollector._extract_list(ObjH(), "chats") == [7, 8]

    def test_empty_dict(self):
        assert RubikaCollector._extract_list({}, "chats") == []


# ═══════════════════════════════════════════════════════════
# 10) _resolve_chat
# ═══════════════════════════════════════════════════════════
class TestResolveChat:
    @pytest.mark.asyncio
    async def test_cache_hit(self, collector):
        collector._chat_cache["g1"] = {"name": "کش شده", "username": "u1"}
        name, user = await collector._resolve_chat("g1", MagicMock())
        assert name == "کش شده"
        assert user == "u1"

    @pytest.mark.asyncio
    async def test_from_update(self, collector):
        u = MagicMock()
        u.title = "از آپدیت"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        name, _ = await collector._resolve_chat("g2", u)
        assert name == "از آپدیت"

    @pytest.mark.asyncio
    async def test_no_guid(self, collector):
        u = MagicMock()
        u.title = "بدون گاید"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        name, _ = await collector._resolve_chat(None, u)
        assert name == "بدون گاید"

    @pytest.mark.asyncio
    async def test_fallback_guid(self, collector):
        u = MagicMock()
        u.title = None
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        collector._client = None
        name, _ = await collector._resolve_chat("abcdefghijklmnopqrstuvwxyz", u)
        assert name == "abcdefghijklmnopqrst"

    @pytest.mark.asyncio
    async def test_api_resolve(self, collector):
        collector._client = MagicMock()
        collector._client.get_chat_info = AsyncMock(
            return_value={"title": "از API"}
        )
        u = MagicMock()
        u.title = None
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        name, _ = await collector._resolve_chat("g3", u)
        assert name == "از API"


# ═══════════════════════════════════════════════════════════
# 11) _to_unified
# ═══════════════════════════════════════════════════════════
class TestToUnified:
    @pytest.mark.asyncio
    async def test_empty_text(self, collector):
        u = MagicMock()
        u.text = ""
        u.object_guid = "g1"
        assert await collector._to_unified(u) is None

    @pytest.mark.asyncio
    async def test_whitespace_text(self, collector):
        u = MagicMock()
        u.text = "   "
        u.object_guid = "g1"
        assert await collector._to_unified(u) is None

    @pytest.mark.asyncio
    async def test_system_chat_skipped(self, collector):
        u = MagicMock()
        u.text = "سلام"
        u.object_guid = "g1"
        u.title = "Saved Messages"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        assert await collector._to_unified(u) is None

    @pytest.mark.asyncio
    async def test_valid_message(self, collector):
        u = MagicMock()
        u.text = "سلام دنیا"
        u.object_guid = "g1"
        u.title = "گروه تست"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = "testchat"
        u.to_dict = None
        u.is_group = True
        u.is_channel = False
        u.message_id = "123"
        u.author_guid = "a1"
        u.author_title = "علی"
        u.sender_name = None
        u.author_name = None
        u.from_name = None
        u.time = 1700000000
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        u.is_file_inline = False
        u.file_inline = None
        u.photo = None
        u.video = None
        u.voice = None
        u.music = None
        u.reply_message_id = None
        msg = await collector._to_unified(u)
        assert msg is not None
        assert msg["text"] == "سلام دنیا"
        assert msg["platform"] == "rubika"
        assert msg["chat_name"] == "گروه تست"
        assert msg["chat_type"] == "group"
        assert msg["chat_username"] == "testchat"
        assert msg["chat_link"] == "https://rubika.ir/testchat"
        assert msg["sender_name"] == "علی"
        assert msg["media_type"] == "text"
        assert len(msg["id"]) == 32

    @pytest.mark.asyncio
    async def test_channel_type(self, collector):
        u = MagicMock()
        u.text = "پست کانال"
        u.object_guid = "g2"
        u.title = "کانال من"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        u.is_group = False
        u.is_channel = True
        u.message_id = "1"
        u.author_guid = None
        u.author_title = None
        u.sender_name = None
        u.author_name = None
        u.from_name = None
        u.time = None
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        u.is_file_inline = False
        u.file_inline = None
        u.photo = None
        u.video = None
        u.voice = None
        u.music = None
        u.reply_message_id = None
        msg = await collector._to_unified(u)
        assert msg["chat_type"] == "channel"

    @pytest.mark.asyncio
    async def test_stable_id(self, collector):
        u = MagicMock()
        u.text = "متن"
        u.object_guid = "g4"
        u.title = "چت"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        u.is_group = False
        u.is_channel = False
        u.object_type = "user"
        u.type = ""
        u.message_id = "999"
        u.author_guid = None
        u.author_title = None
        u.sender_name = None
        u.author_name = None
        u.from_name = None
        u.time = None
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        u.is_file_inline = False
        u.file_inline = None
        u.photo = None
        u.video = None
        u.voice = None
        u.music = None
        u.reply_message_id = None
        m1 = await collector._to_unified(u)
        m2 = await collector._to_unified(u)
        assert m1["id"] == m2["id"]


# ═══════════════════════════════════════════════════════════
# 12) State
# ═══════════════════════════════════════════════════════════
def test_load_first_run_missing(collector):
    assert collector._first_run_done is False


def test_save_and_load_state(collector):
    collector._save_state()
    assert collector._load_first_run() is True
    assert collector._load_last_run_at() is not None


def test_save_and_load_seen(collector):
    collector._seen_ids = {"a", "b", "c"}
    collector._save_seen()
    collector._seen_ids = set()
    collector._load_seen()
    assert collector._seen_ids == {"a", "b", "c"}


def test_load_seen_corrupt(collector):
    p = collector._seen_path
    with open(p, "w", encoding="utf-8") as f:
        f.write("not json")
    collector._load_seen()
    assert collector._seen_ids == set()


# ═══════════════════════════════════════════════════════════
# 13) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "rubika"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 999.0


def test_supports_history(collector):
    assert collector.supports_history is True


def test_supports_websocket(collector):
    assert collector.supports_websocket is True


# ═══════════════════════════════════════════════════════════
# 14) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_client(collector):
    collector._connected = True
    collector._client = None
    assert asyncio.run(collector.fetch_messages()) == []


# ═══════════════════════════════════════════════════════════
# 15) check_health
# ═══════════════════════════════════════════════════════════
def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True


# ═══════════════════════════════════════════════════════════
# 16) env helpers
# ═══════════════════════════════════════════════════════════
class TestEnvHelpers:
    def test_history_limit_default(self, monkeypatch):
        monkeypatch.delenv("RUBIKA_HISTORY_LIMIT", raising=False)
        assert _get_history_limit() == 500

    def test_history_limit_custom(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_HISTORY_LIMIT", "100")
        assert _get_history_limit() == 100

    def test_history_limit_invalid(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_HISTORY_LIMIT", "abc")
        assert _get_history_limit() == 500

    def test_max_chats_default(self, monkeypatch):
        monkeypatch.delenv("RUBIKA_MAX_CHATS", raising=False)
        assert _get_max_chats() == 0

    def test_max_chats_custom(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_MAX_CHATS", "10")
        assert _get_max_chats() == 10