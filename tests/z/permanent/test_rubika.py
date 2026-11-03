# tests/permanent/test_rubika.py
"""تست کامل collectors/rubika.py — توابع خالص و منطق."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.rubika import (
    RubikaCollector,
    _extract_name_from_obj,
    _get_history_limit,
    _get_max_chats,
    _obj_to_dict,
)


# ═══════════════════════════════════════════════
# _obj_to_dict
# ═══════════════════════════════════════════════
class TestObjToDict:
    def test_none(self):
        assert _obj_to_dict(None) == {}

    def test_plain_dict(self):
        assert _obj_to_dict({"a": 1}) == {"a": 1}

    def test_to_dict_method(self):
        class ObjA:
            def to_dict(self):
                return {"x": 2}
        assert _obj_to_dict(ObjA()) == {"x": 2}

    def test_to_dict_attribute(self):
        class ObjB:
            to_dict = {"y": 3}
        assert _obj_to_dict(ObjB()) == {"y": 3}

    def test_to_dict_raises(self):
        class ObjC:
            def to_dict(self):
                raise RuntimeError("boom")
        assert _obj_to_dict(ObjC()) == {}

    def test_to_dict_returns_non_dict(self):
        class ObjD:
            def to_dict(self):
                return [1, 2]
        assert _obj_to_dict(ObjD()) == {}

    def test_object_without_to_dict(self):
        class ObjE:
            pass
        assert _obj_to_dict(ObjE()) == {}


# ═══════════════════════════════════════════════
# _extract_name_from_obj
# ═══════════════════════════════════════════════
class TestExtractNameFromObj:
    def test_none(self):
        assert _extract_name_from_obj(None) == (None, None)

    def test_title(self):
        assert _extract_name_from_obj({"title": "گروه تست"}) == (
            "گروه تست", None
        )

    def test_first_name(self):
        n, u = _extract_name_from_obj(
            {"first_name": "علی", "last_name": "رضایی"}
        )
        assert n == "علی رضایی"

    def test_first_name_only(self):
        n, _ = _extract_name_from_obj({"first_name": "علی"})
        assert n == "علی"

    def test_name_key(self):
        n, _ = _extract_name_from_obj({"name": "کانال من"})
        assert n == "کانال من"

    def test_username(self):
        n, u = _extract_name_from_obj(
            {"title": "چت", "username": "mychat"}
        )
        assert n == "چت"
        assert u == "mychat"

    def test_abs_object_nested(self):
        obj = {"abs_object": {"title": "نستد"}}
        n, _ = _extract_name_from_obj(obj)
        assert n == "نستد"

    def test_chat_nested(self):
        obj = {"chat": {"title": "درون چت"}}
        n, _ = _extract_name_from_obj(obj)
        assert n == "درون چت"

    def test_attribute_fallback(self):
        class ObjF:
            title = "از اتریبیوت"
            username = "user1"
        n, u = _extract_name_from_obj(ObjF())
        assert n == "از اتریبیوت"
        assert u == "user1"

    def test_empty_dict(self):
        assert _extract_name_from_obj({}) == (None, None)

    def test_whitespace_title(self):
        n, _ = _extract_name_from_obj({"title": "   "})
        assert n is None

    def test_priority_title_over_name(self):
        n, _ = _extract_name_from_obj(
            {"title": "عنوان", "name": "نام"}
        )
        assert n == "عنوان"


# ═══════════════════════════════════════════════
# _normalize_phone
# ═══════════════════════════════════════════════
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


# ═══════════════════════════════════════════════
# _is_system_name
# ═══════════════════════════════════════════════
class TestIsSystemName:
    @pytest.fixture
    def col(self):
        return RubikaCollector(phone="09120000000")

    def test_saved_messages_fa(self, col):
        assert col._is_system_name("پیام‌های ذخیره شده") is True

    def test_saved_messages_en(self, col):
        assert col._is_system_name("Saved Messages") is True

    def test_you(self, col):
        assert col._is_system_name("You") is True

    def test_rubika(self, col):
        assert col._is_system_name("روبیکا") is True

    def test_prefix_channel_created(self, col):
        assert col._is_system_name("کانال ایجاد شد") is True

    def test_prefix_group_created_en(self, col):
        assert col._is_system_name("Group created by X") is True

    def test_normal_name(self, col):
        assert col._is_system_name("گروه دوستان") is False

    def test_empty(self, col):
        assert col._is_system_name("") is False

    def test_none(self, col):
        assert col._is_system_name(None) is False

    def test_whitespace(self, col):
        assert col._is_system_name("   ") is False


# ═══════════════════════════════════════════════
# _extract_timestamp
# ═══════════════════════════════════════════════
class TestExtractTimestamp:
    @pytest.fixture
    def col(self):
        return RubikaCollector(phone="09120000000")

    def test_seconds(self, col):
        u = MagicMock()
        u.time = 1700000000
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        ts = col._extract_timestamp(u)
        assert ts is not None
        assert ts.year >= 2023

    def test_milliseconds(self, col):
        u = MagicMock()
        u.time = 1700000000000
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        ts = col._extract_timestamp(u)
        assert ts is not None
        assert ts.year >= 2023

    def test_none(self, col):
        u = MagicMock()
        u.time = None
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        assert col._extract_timestamp(u) is None

    def test_invalid(self, col):
        u = MagicMock()
        u.time = "not-a-number"
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = None
        assert col._extract_timestamp(u) is None

    def test_from_original_update(self, col):
        u = MagicMock()
        u.time = None
        u.date = None
        u.timestamp = None
        u.created_at = None
        u.original_update = {"time": 1700000000}
        ts = col._extract_timestamp(u)
        assert ts is not None


# ═══════════════════════════════════════════════
# _extract_sender_name
# ═══════════════════════════════════════════════
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


# ═══════════════════════════════════════════════
# _build_link
# ═══════════════════════════════════════════════
class TestBuildLink:
    def test_username(self):
        link = RubikaCollector._build_link("mychat", "guid", "group")
        assert link == "https://rubika.ir/mychat"

    def test_none_username(self):
        assert RubikaCollector._build_link(None, "guid", "group") is None

    def test_empty_username(self):
        assert RubikaCollector._build_link("", "guid", "group") is None


# ═══════════════════════════════════════════════
# _dig
# ═══════════════════════════════════════════════
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


# ═══════════════════════════════════════════════
# _extract_list
# ═══════════════════════════════════════════════
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


# ═══════════════════════════════════════════════
# _resolve_chat
# ═══════════════════════════════════════════════
class TestResolveChat:
    @pytest.fixture
    def col(self):
        return RubikaCollector(phone="09120000000")

    @pytest.mark.asyncio
    async def test_cache_hit(self, col):
        col._chat_cache["g1"] = {"name": "کش شده", "username": "u1"}
        name, user = await col._resolve_chat("g1", MagicMock())
        assert name == "کش شده"
        assert user == "u1"

    @pytest.mark.asyncio
    async def test_from_update(self, col):
        u = MagicMock()
        u.title = "از آپدیت"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        name, _ = await col._resolve_chat("g2", u)
        assert name == "از آپدیت"

    @pytest.mark.asyncio
    async def test_no_guid(self, col):
        u = MagicMock()
        u.title = "بدون گاید"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        name, _ = await col._resolve_chat(None, u)
        assert name == "بدون گاید"

    @pytest.mark.asyncio
    async def test_fallback_guid(self, col):
        u = MagicMock()
        u.title = None
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        col._client = None
        name, _ = await col._resolve_chat("abcdefghijklmnopqrstuvwxyz", u)
        assert name == "abcdefghijklmnopqrst"

    @pytest.mark.asyncio
    async def test_api_resolve(self, col):
        col._client = MagicMock()
        col._client.get_chat_info = AsyncMock(
            return_value={"title": "از API"}
        )
        u = MagicMock()
        u.title = None
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        name, _ = await col._resolve_chat("g3", u)
        assert name == "از API"


# ═══════════════════════════════════════════════
# _to_unified
# ═══════════════════════════════════════════════
class TestToUnified:
    @pytest.fixture
    def col(self):
        return RubikaCollector(phone="09120000000")

    @pytest.mark.asyncio
    async def test_empty_text(self, col):
        u = MagicMock()
        u.text = ""
        u.object_guid = "g1"
        assert await col._to_unified(u) is None

    @pytest.mark.asyncio
    async def test_whitespace_text(self, col):
        u = MagicMock()
        u.text = "   "
        u.object_guid = "g1"
        assert await col._to_unified(u) is None

    @pytest.mark.asyncio
    async def test_system_chat_skipped(self, col):
        u = MagicMock()
        u.text = "سلام"
        u.object_guid = "g1"
        u.title = "Saved Messages"
        u.first_name = None
        u.last_name = None
        u.name = None
        u.username = None
        u.to_dict = None
        assert await col._to_unified(u) is None

    @pytest.mark.asyncio
    async def test_valid_message(self, col):
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
        msg = await col._to_unified(u)
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
    async def test_channel_type(self, col):
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
        msg = await col._to_unified(u)
        assert msg["chat_type"] == "channel"

    @pytest.mark.asyncio
    async def test_photo_media(self, col):
        u = MagicMock()
        u.text = "عکس"
        u.object_guid = "g3"
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
        u.message_id = "5"
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
        u.photo = "photo_obj"
        u.video = None
        u.voice = None
        u.music = None
        u.reply_message_id = None
        msg = await col._to_unified(u)
        assert msg["media_type"] == "photo"

    @pytest.mark.asyncio
    async def test_stable_id(self, col):
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
        m1 = await col._to_unified(u)
        m2 = await col._to_unified(u)
        assert m1["id"] == m2["id"]


# ═══════════════════════════════════════════════
# fetch_messages — بدون اتصال
# ═══════════════════════════════════════════════
class TestFetchMessages:
    @pytest.fixture
    def col(self):
        return RubikaCollector(phone="09120000000")

    @pytest.mark.asyncio
    async def test_not_connected(self, col):
        assert await col.fetch_messages() == []

    @pytest.mark.asyncio
    async def test_fetch_history_alias(self, col):
        assert await col.fetch_history() == []


# ═══════════════════════════════════════════════
# env helpers
# ═══════════════════════════════════════════════
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

    def test_history_limit_negative(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_HISTORY_LIMIT", "-5")
        assert _get_history_limit() == 500

    def test_max_chats_default(self, monkeypatch):
        monkeypatch.delenv("RUBIKA_MAX_CHATS", raising=False)
        assert _get_max_chats() == 0

    def test_max_chats_custom(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_MAX_CHATS", "10")
        assert _get_max_chats() == 10

    def test_max_chats_invalid(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_MAX_CHATS", "xyz")
        assert _get_max_chats() == 0

    def test_max_chats_negative(self, monkeypatch):
        monkeypatch.setenv("RUBIKA_MAX_CHATS", "-3")
        assert _get_max_chats() == 0


# ═══════════════════════════════════════════════
# properties
# ═══════════════════════════════════════════════
class TestProperties:
    @pytest.fixture
    def col(self):
        return RubikaCollector(phone="09120000000")

    def test_platform_name(self, col):
        assert col.platform_name == "rubika"

    def test_supports_history(self, col):
        assert col.supports_history is True

    def test_supports_websocket(self, col):
        assert col.supports_websocket is True

    def test_poll_interval(self, col, monkeypatch):
        monkeypatch.delenv("RUBIKA_POLL_SECONDS", raising=False)
        c = RubikaCollector(phone="09120000000")
        assert c.poll_interval_seconds == 30.0

    def test_poll_interval_custom(self):
        c = RubikaCollector(phone="09120000000", poll_seconds=60)
        assert c.poll_interval_seconds == 60.0


# ═══════════════════════════════════════════════
# check_health
# ═══════════════════════════════════════════════
class TestCheckHealth:
    @pytest.mark.asyncio
    async def test_not_connected(self):
        col = RubikaCollector(phone="09120000000")
        assert await col.check_health() is False

    @pytest.mark.asyncio
    async def test_connected(self):
        col = RubikaCollector(phone="09120000000")
        col._connected = True
        assert await col.check_health() is True
