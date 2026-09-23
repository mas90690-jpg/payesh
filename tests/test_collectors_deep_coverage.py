"""Deep coverage for all 7 collectors via heavy mocking (no network/browser)."""
import asyncio
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.bale import BaleCollector
from collectors.eitaa import EitaaCollector
from collectors.linkedin import LinkedInCollector
from collectors.rubika import RubikaCollector
from collectors.telegram import TelegramCollector
from collectors.web import WebCollector
from collectors.whatsapp import WhatsAppCollector


# ═══════════════════ Bale ═══════════════════
@pytest.fixture
def bale():
    bc = BaleCollector.__new__(BaleCollector)
    bc._client = None
    bc._messages_queue = []
    bc._connected = False
    bc._media_dir = os.path.join("storage", "media", "bale")
    bc._chat_cache = {}
    bc._listen_task = None
    bc._is_running = True
    return bc


def test_bale_platform_and_extension():
    assert BaleCollector.__new__(BaleCollector).platform_name == "bale"
    assert BaleCollector._get_extension(BaleCollector, "photo") == ".jpg"
    assert BaleCollector._get_extension(BaleCollector, "video") == ".mp4"
    assert BaleCollector._get_extension(BaleCollector, "audio") == ".mp3"
    assert BaleCollector._get_extension(BaleCollector, "voice") == ".ogg"
    assert BaleCollector._get_extension(BaleCollector, "document") == ".file"
    assert BaleCollector._get_extension(BaleCollector, "gif") == ".gif"
    assert BaleCollector._get_extension(BaleCollector, "weird") == ".bin"


def _bale_msg(text=None, mime="", name="", caption=None, file_id=None, chat_type="group", sid=None):
    _fid = file_id
    _cap = caption
    _mime = mime
    _name = name

    class _Doc:
        file_id = _fid
        access_hash = None
        caption = _cap
        mime_type = _mime
        name = _name

    _text = text
    _sid = sid

    class _Chat:
        id = "-1001"
        type = chat_type

    class _Msg:
        text = _text
        document = _Doc() if file_id is not None else None
        chat = _Chat()
        message_id = 7
        sender_id = _sid if _sid is not None else 42
        replied_to = None
        quoted_replied_to = None

    return _Msg()


def test_bale_media_type_detection():
    bc = BaleCollector
    mt, fid, cap = bc._get_media_type_from_message(bc, _bale_msg(text="hi"))
    assert mt == "text" and fid is None
    mt, fid, cap = bc._get_media_type_from_message(
        bc, _bale_msg(file_id=1, mime="image/jpeg", name="pic.jpg", caption="cap")
    )
    assert mt == "photo" and fid == "1" and cap == "cap"
    mt, _, _ = bc._get_media_type_from_message(
        bc, _bale_msg(file_id=2, mime="image/gif", name="a.gif")
    )
    assert mt == "gif"
    mt, _, _ = bc._get_media_type_from_message(
        bc, _bale_msg(file_id=3, mime="video/mp4", name="v.mp4")
    )
    assert mt == "video"
    mt, _, _ = bc._get_media_type_from_message(
        bc, _bale_msg(file_id=4, mime="audio/ogg", name="a.ogg")
    )
    assert mt == "voice"
    mt, _, _ = bc._get_media_type_from_message(
        bc, _bale_msg(file_id=5, mime="audio/mp3", name="a.mp3")
    )
    assert mt == "audio"
    mt, _, _ = bc._get_media_type_from_message(
        bc, _bale_msg(file_id=6, mime="application/pdf", name="doc.pdf")
    )
    assert mt == "document"


def test_bale_caption_and_chat_type():
    bc = BaleCollector
    assert bc._caption_str(None) == ""
    assert bc._caption_str("text") == "text"

    class _CapObj:
        text = "obj"

    assert bc._caption_str(_CapObj()) == "obj"
    assert bc._chat_type_of(bc, MagicMock(type="Group")) == "group"
    assert bc._chat_type_of(bc, MagicMock(type="Channel")) == "channel"
    assert bc._chat_type_of(bc, MagicMock(type="Private")) == "private"
    assert bc._chat_type_of(bc, MagicMock(type="other")) == "group"
    assert bc._extract_sender_id(bc, _bale_msg(sid=123)) == "123"
    # sid=42 is the default from _bale_msg; _extract_sender_id returns str(42)
    assert bc._extract_sender_id(bc, _bale_msg(sid=42)) == "42"


@pytest.mark.asyncio
async def test_bale_build_unified(bale, monkeypatch):
    async def fake_resolve(cid, ct):
        return {"name": "گروه تست", "username": "g", "link": "https://ble.ir/g"}

    monkeypatch.setattr(bale, "_resolve_group", fake_resolve)
    msg = await bale._build_unified(_bale_msg(text="قیمت یاراترا", chat_type="group"))
    assert msg["platform"] == "bale"
    assert msg["chat_name"] == "گروه تست"
    assert msg["chat_link"] == "https://ble.ir/g"
    assert msg["media_type"] == "text"


@pytest.mark.asyncio
async def test_bale_fetch_messages_since_filter(bale):
    bale._connected = True
    bale._messages_queue = [
        {"id": "1", "timestamp": datetime.now()},
        {"id": "2", "timestamp": datetime.now() - timedelta(hours=5)},
    ]
    msgs = await bale.fetch_messages(datetime.now() - timedelta(hours=1))
    assert [m["id"] for m in msgs] == ["1"]
    assert bale._messages_queue == []
    assert await bale.fetch_messages() == []
    bale._connected = False
    assert await bale.fetch_messages() == []


@pytest.mark.asyncio
async def test_bale_fetch_history_no_dialogs(bale):
    bale._connected = True
    bale._client = MagicMock()
    bale._client.load_dialogs = AsyncMock(return_value=[])
    assert await bale.fetch_history(datetime.now()) == []


@pytest.mark.asyncio
async def test_bale_connect_missing_credentials(bale):
    bale.type = "user"
    bale.phone = None
    assert await bale.connect() is False
    bale.phone = "0912"
    bale.password = None
    assert await bale.connect() is False


@pytest.mark.asyncio
async def test_bale_connect_aiobale_missing(bale, monkeypatch):
    bale.type = "user"
    bale.phone = "09123456789"
    bale.password = "p"
    monkeypatch.setattr("collectors.bale.Client", None)
    monkeypatch.setattr("collectors.bale.Dispatcher", None)
    assert await bale.connect() is False


@pytest.mark.asyncio
async def test_bale_send_message_and_health(bale, monkeypatch):
    client = MagicMock()
    client.send_message = AsyncMock()
    bale._client = client
    bale._connected = True
    bale._listen_task = asyncio.create_task(asyncio.sleep(0.1))

    await bale.send_message("1", "private", "hi")
    client.send_message.assert_awaited_once()
    await bale.send_message(None, "private", "hi")  # no client path
    assert await bale.check_health() is True
    await bale.disconnect()
    assert bale._connected is False


# ═══════════════════ Rubika ═══════════════════
@pytest.fixture
def rubika():
    rc = RubikaCollector.__new__(RubikaCollector)
    rc.phone = "0912"
    rc._client = None
    rc._messages_queue = []
    rc._connected = False
    rc._running = False
    rc._loop_task = None
    rc._chat_cache = {}
    rc._media_dir = os.path.join("storage", "media", "rubika")
    return rc


def test_rubika_normalize_phone():
    rc = RubikaCollector
    assert rc._normalize_phone("+98912") == "0912"
    assert rc._normalize_phone("98912") == "0912"
    assert rc._normalize_phone("912") == "0912"
    assert rc._normalize_phone("0912") == "0912"
    assert rc._build_link("u", None, "group") == "https://rubika.ir/u"
    assert rc._build_link(None, "g", "group") is None


def test_rubika_extract_list_and_dig():
    rc = RubikaCollector
    assert rc._extract_list({"chats": [1, 2]}, "chats") == [1, 2]
    assert rc._extract_list({"x": {"chats": [3]}}, "chats") == [3]
    assert rc._extract_list({"x": 1}, "chats") == []
    assert rc._dig({"a": {"b": 5}}, "a", "b") == 5
    assert rc._dig({"a": 1}, "a", "b") is None
    assert rc._dig([], "a") is None


@pytest.mark.asyncio
async def test_rubika_to_unified_and_resolve(rubika, monkeypatch):
    class _Update:
        object_guid = "g1"
        text = "قیمت یاراترا"
        is_group = True
        is_channel = False
        message_id = 9
        author_guid = "u9"
        reply_message_id = 3

        async def get_object(self):
            return MagicMock(to_dict=lambda: {"title": "گروه روبیکا", "username": "ru"})

    up = _Update()
    msg = await rubika._to_unified(up)
    assert msg["platform"] == "rubika"
    assert msg["chat_name"] == "گروه روبیکا"
    assert msg["chat_link"] == "https://rubika.ir/ru"
    assert msg["chat_type"] == "group"
    # cached second time
    msg2 = await rubika._to_unified(up)
    assert msg2["chat_name"] == "گروه روبیکا"
    assert rubika._chat_cache["g1"]["username"] == "ru"


@pytest.mark.asyncio
async def test_rubika_fetch_messages(rubika):
    rubika._connected = True
    rubika._messages_queue = [{"id": "1"}]
    assert await rubika.fetch_messages() == [{"id": "1"}]
    assert await rubika.fetch_messages() == []
    rubika._connected = False
    assert await rubika.fetch_messages() == []


@pytest.mark.asyncio
async def test_rubika_fetch_history(rubika, monkeypatch):
    rubika._connected = True
    client = MagicMock()
    client.get_chats = AsyncMock(
        return_value=MagicMock(to_dict=lambda: {"chats": [{"object_guid": "g1", "type": "group"}]})
    )
    client.get_messages = AsyncMock(
        return_value=MagicMock(
            to_dict=lambda: {"messages": [{"message_id": "1", "text": "قیمت یاراترا", "time": 999999999999}]}
        )
    )
    rubika._client = client
    msgs = await rubika.fetch_history(datetime(2030, 1, 1))
    # time 999999999999 is way in the future -> included
    assert msgs and msgs[0]["platform"] == "rubika"
    # 999999999999 timestamp is far in the future of 2026 -> always included
    msgs2 = await rubika.fetch_history(datetime(2020, 1, 1))
    assert msgs2 and msgs2[0]["id"] == "1"


@pytest.mark.asyncio
async def test_rubika_connect_missing_phone_and_lib(rubika, monkeypatch):
    rubika.phone = ""
    assert await rubika.connect() is False
    rubika.phone = "0912"
    monkeypatch.setattr("collectors.rubika.Client", None)
    assert await rubika.connect() is False


@pytest.mark.asyncio
async def test_rubika_disconnect_health(rubika):
    rubika._connected = True
    rubika._client = MagicMock()
    rubika._client.disconnect = AsyncMock()
    assert await rubika.check_health() is True
    await rubika.disconnect()
    assert rubika._connected is False
    assert await rubika.check_health() is False


# ═══════════════════ Telegram ═══════════════════
@pytest.fixture
def telegram():
    tc = TelegramCollector.__new__(TelegramCollector)
    tc.api_id = 1
    tc.api_hash = "h"
    tc.phone = "0912"
    tc.db = None
    tc._client = None
    tc._messages_queue = []
    tc._connected = False
    tc._listen_task = None
    tc._media_dir = os.path.join("storage", "media", "telegram")
    return tc


@pytest.mark.asyncio
async def test_telegram_process_message(telegram, monkeypatch):
    import collectors.telegram as tg_mod
    from tests.helpers.mock_factory import mock_telegram_message
    monkeypatch.setattr(tg_mod, "get_keyword_finder", lambda: MagicMock(has_match=lambda t: True), raising=False)
    client = MagicMock()
    client.download_media = AsyncMock(return_value=None)
    telegram._client = client

    msg = mock_telegram_message("قیمت یاراترا", chat_id=123)
    out = await telegram._process_message(msg)
    assert out["platform"] == "telegram"
    assert out["chat_id"] == "123"
    assert out["text"] == "قیمت یاراترا"
    assert out["timestamp"].year >= 2023


@pytest.mark.asyncio
async def test_telegram_process_message_media(telegram, monkeypatch):
    import collectors.telegram as tg_mod
    from tests.helpers.mock_factory import mock_telegram_message
    monkeypatch.setattr(tg_mod, "get_keyword_finder", lambda: MagicMock(has_match=lambda t: True), raising=False)
    client = MagicMock()
    client.download_media = AsyncMock(return_value="/tmp/tg.jpg")
    telegram._client = client
    msg = mock_telegram_message("عکس یاراترا")
    msg.photo = MagicMock(id=99)
    out = await telegram._process_message(msg)
    assert out["media_type"] == "photo"
    assert out["file_id"] == "99"
    assert out["local_file_path"] == "/tmp/tg.jpg"


@pytest.mark.asyncio
async def test_telegram_remember_offset(telegram):
    db = MagicMock()
    db.get_chat_offset = AsyncMock(return_value=None)
    db.set_chat_offset = AsyncMock()
    telegram.db = db
    await telegram._remember_offset("g1", 5)
    db.set_chat_offset.assert_awaited_once_with("telegram", "g1", 5)
    db.get_chat_offset = AsyncMock(return_value="9")
    await telegram._remember_offset("g1", 5)
    db.set_chat_offset.assert_awaited_once()  # not called again
    telegram.db = None
    await telegram._remember_offset("g1", 5)  # no crash


@pytest.mark.asyncio
async def test_telegram_fetch_messages(telegram):
    telegram._connected = True
    telegram._messages_queue = [
        {"id": "1", "timestamp": datetime.now()},
        {"id": "2", "timestamp": datetime.now() - timedelta(hours=5)},
    ]
    msgs = await telegram.fetch_messages(datetime.now() - timedelta(hours=1))
    assert [m["id"] for m in msgs] == ["1"]
    telegram._connected = False
    assert await telegram.fetch_messages() == []


@pytest.mark.asyncio
async def test_telegram_fetch_history(telegram, monkeypatch):
    telegram._connected = True
    client = MagicMock()

    class _Dialog:
        is_group = True
        is_channel = False
        entity = "e"
        id = 123

    async def iter_dialogs():
        yield _Dialog()

    async def iter_messages(entity, **kw):
        msg = MagicMock()
        msg.id = 10
        msg.text = "قیمت یاراترا"
        msg.photo = None
        msg.video = None
        msg.audio = None
        msg.document = None
        msg.voice = None
        msg.chat = MagicMock(id=123, title="گروه", first_name=None, username="u")
        msg.sender = MagicMock(id=1, first_name="علی")
        msg.date = MagicMock(timestamp=lambda: 1700000000)
        msg.caption = None
        msg.reply_to_msg_id = None
        msg.is_group = True
        msg.is_channel = False
        yield msg

    client.iter_dialogs = iter_dialogs
    client.iter_messages = iter_messages
    client.download_media = AsyncMock(return_value=None)
    telegram._client = client
    import collectors.telegram as tg_mod
    monkeypatch.setattr(tg_mod, "get_keyword_finder", lambda: MagicMock(has_match=lambda t: True), raising=False)
    msgs = await telegram.fetch_history(datetime.now() - timedelta(days=1))
    assert msgs and msgs[0]["platform"] == "telegram"


@pytest.mark.asyncio
async def test_telegram_connect_missing_config(telegram):
    telegram.api_id = None
    assert await telegram.connect() is False
    telegram.api_id = 1
    telegram.api_hash = None
    assert await telegram.connect() is False
    telegram.api_hash = "h"
    telegram.phone = None
    assert await telegram.connect() is False


@pytest.mark.asyncio
async def test_telegram_disconnect(telegram):
    client = MagicMock()
    client.disconnect = AsyncMock()
    telegram._client = client
    telegram._connected = True
    await telegram.disconnect()
    assert telegram._connected is False


# ═══════════════════ Eitaa ═══════════════════
@pytest.fixture
def eitaa(tmp_path, monkeypatch):
    ec = EitaaCollector.__new__(EitaaCollector)
    ec._poll_seconds = 180
    ec._max_chats = 5
    ec._chat_open_delay = 0.0
    ec._messages_per_chat = 10
    ec._backfill_scrolls = 0
    ec._needs_backfill = False
    ec._pw = None
    ec._context = None
    ec._page = None
    ec._connected = False
    ec._messages_queue = []
    ec._seen_ids = set()
    ec._processed_chats = set()
    ec._seen_path = str(tmp_path / "seen.json")
    ec._media_dir = str(tmp_path)
    ec._user_data_dir = str(tmp_path)
    ec._targets = MagicMock(enabled=lambda: False, is_target=lambda *a, **k: True)
    ec._kw = MagicMock(has_match=lambda t: True)
    return ec


def test_eitaa_load_save_seen(eitaa, tmp_path):
    assert eitaa._seen_ids == set()
    eitaa._seen_ids = {"a", "b"}
    eitaa._save_seen()
    assert (tmp_path / "seen.json").exists()
    eitaa2 = EitaaCollector.__new__(EitaaCollector)
    eitaa2._seen_path = str(tmp_path / "seen.json")
    eitaa2._load_seen()
    assert eitaa2._seen_ids == {"a", "b"}


def test_eitaa_platform_props(eitaa):
    assert eitaa.platform_name == "eitaa"
    assert eitaa.poll_interval_seconds == 180.0
    assert eitaa.supports_history is False
    assert eitaa.supports_websocket is False


@pytest.mark.asyncio
async def test_eitaa_connect_no_playwright(eitaa, monkeypatch):
    monkeypatch.setattr("collectors.eitaa.async_playwright", None)
    assert await eitaa.connect() is False


@pytest.mark.asyncio
async def test_eitaa_is_logged_in(eitaa):
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    eitaa._page = page
    assert await eitaa._is_logged_in() is False
    # login markers empty, chatlist present
    # 3 LOGIN_MARKERS (None) then a CHATLIST_MARKERS hit
    page.query_selector = AsyncMock(side_effect=[None, None, None, MagicMock()])
    assert await eitaa._is_logged_in() is True


@pytest.mark.asyncio
async def test_eitaa_extract_titles(eitaa):
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value="گروه هدف")
    item = AsyncMock()
    item.query_selector = AsyncMock(return_value=el)
    assert await eitaa._extract_item_title(item) == "گروه هدف"

    item2 = AsyncMock()
    item2.query_selector = AsyncMock(return_value=None)
    item2.inner_text = AsyncMock(return_value="first\nsecond")
    assert await eitaa._extract_item_title(item2) == "first"

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    eitaa._page = page
    assert await eitaa._get_open_chat_title() == "ایتا"


@pytest.mark.asyncio
async def test_eitaa_fetch_disconnected(eitaa):
    assert await eitaa.fetch_messages() == []


@pytest.mark.asyncio
async def test_eitaa_dump_and_disconnect(eitaa, tmp_path):
    page = AsyncMock()
    page.content = AsyncMock(return_value="<html>x</html>")
    eitaa._page = page
    path = await eitaa.dump_dom()
    assert os.path.exists(path)
    eitaa._connected = True
    eitaa._context = AsyncMock()
    eitaa._context.close = AsyncMock()
    eitaa._pw = AsyncMock()
    eitaa._pw.stop = AsyncMock()
    await eitaa.disconnect()
    assert eitaa._connected is False


@pytest.mark.asyncio
async def test_eitaa_check_health(eitaa):
    eitaa._connected = True
    assert await eitaa.check_health() is True
    eitaa._connected = False
    assert await eitaa.check_health() is False


# ═══════════════════ WhatsApp ═══════════════════
@pytest.fixture
def whatsapp(tmp_path):
    wc = WhatsAppCollector.__new__(WhatsAppCollector)
    wc._poll_seconds = 180
    wc._max_chats = 5
    wc._chat_open_delay = 0.0
    wc._messages_per_chat = 10
    wc._backfill_scrolls = 0
    wc._needs_backfill = False
    wc._pw = None
    wc._context = None
    wc._page = None
    wc._connected = False
    wc._messages_queue = []
    wc._seen_ids = set()
    wc._seen_path = str(tmp_path / "seen.json")
    wc._media_dir = str(tmp_path)
    wc._user_data_dir = str(tmp_path)
    wc._kw = MagicMock(has_match=lambda t: True)
    return wc


def test_whatsapp_props(whatsapp):
    assert whatsapp.platform_name == "whatsapp"
    assert whatsapp.poll_interval_seconds == 180.0
    assert whatsapp.supports_history is False
    assert whatsapp.supports_websocket is False


@pytest.mark.asyncio
async def test_whatsapp_connect_no_playwright(whatsapp, monkeypatch):
    monkeypatch.setattr("collectors.whatsapp.async_playwright", None)
    assert await whatsapp.connect() is False


@pytest.mark.asyncio
async def test_whatsapp_is_logged_in(whatsapp):
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    whatsapp._page = page
    assert await whatsapp._is_logged_in() is False
    page.query_selector = AsyncMock(side_effect=[None, None, None, MagicMock()])
    assert await whatsapp._is_logged_in() is True


@pytest.mark.asyncio
async def test_whatsapp_get_open_chat_title(whatsapp):
    el = AsyncMock()
    el.get_attribute = AsyncMock(return_value="گروه واتساپ")
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)
    whatsapp._page = page
    assert await whatsapp._get_open_chat_title() == "گروه واتساپ"
    page.query_selector = AsyncMock(return_value=None)
    assert await whatsapp._get_open_chat_title() == "واتساپ"


@pytest.mark.asyncio
async def test_whatsapp_save_seen_and_disconnect(whatsapp, tmp_path):
    whatsapp._seen_ids = {"x"}
    whatsapp._save_seen()
    assert (tmp_path / "seen.json").exists()
    whatsapp._connected = True
    whatsapp._context = AsyncMock()
    whatsapp._pw = AsyncMock()
    await whatsapp.disconnect()
    assert whatsapp._connected is False
    assert await whatsapp.check_health() is False


# ═══════════════════ LinkedIn ═══════════════════
@pytest.fixture
def linkedin(tmp_path):
    lc = LinkedInCollector.__new__(LinkedInCollector)
    lc._poll_seconds = 600
    lc._max_results = 5
    lc._search_delay = 0.0
    lc._scrolls = 0
    lc._monitor_feed = False
    lc._max_feed_posts = 5
    lc._pw = None
    lc._context = None
    lc._page = None
    lc._connected = False
    lc._messages_queue = []
    lc._seen_ids = set()
    lc._seen_path = str(tmp_path / "seen.json")
    lc._state_path = str(tmp_path / "state.json")
    lc._media_dir = str(tmp_path)
    lc._user_data_dir = str(tmp_path)
    lc._kw = MagicMock(match=lambda t: ["یاراترا"], has_match=lambda t: True)
    return lc


def test_linkedin_props(linkedin):
    assert linkedin.platform_name == "linkedin"
    assert linkedin.poll_interval_seconds == 600.0


def test_linkedin_load_searches(tmp_path):
    f = tmp_path / "searches.txt"
    f.write_text("# c\nقیمت کود\n\nیاراترا\n", encoding="utf-8")
    lc = LinkedInCollector.__new__(LinkedInCollector)
    lc._searches_file = str(f)
    assert lc._load_searches() == ["قیمت کود", "یاراترا"]


def test_linkedin_match_post(linkedin):
    assert "یاراترا" in linkedin._match_post("خرید یاراترا", None)
    assert "کود" in linkedin._match_post("خرید کود", "کود")


@pytest.mark.asyncio
async def test_linkedin_extract(linkedin):
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value="علی رضایی\nمهندس")
    post = AsyncMock()
    post.query_selector = AsyncMock(return_value=el)
    assert await linkedin._extract(post, [".name"]) == "علی رضایی"
    post.query_selector = AsyncMock(return_value=None)
    assert await linkedin._extract(post, [".name"]) == ""

    a = AsyncMock()
    a.get_attribute = AsyncMock(return_value="https://x.com/a?t=1")
    post2 = AsyncMock()
    post2.query_selector = AsyncMock(return_value=a)
    assert await linkedin._extract_link(post2) == "https://x.com/a"
    post2.query_selector = AsyncMock(return_value=None)
    assert await linkedin._extract_link(post2) is None


@pytest.mark.asyncio
async def test_linkedin_check_session(linkedin):
    page = AsyncMock()
    page.url = "https://www.linkedin.com/feed/"
    linkedin._page = page
    assert await linkedin._check_session_alive() is True
    page.url = "https://www.linkedin.com/authwall"
    assert await linkedin._check_session_alive() is False
    assert linkedin._connected is False


@pytest.mark.asyncio
async def test_linkedin_harvest_posts(linkedin, tmp_path):
    post = AsyncMock()
    post.inner_text = AsyncMock(return_value="پست درباره یاراترا")
    post.get_attribute = AsyncMock(side_effect=lambda name: "urn1" if name == "data-urn" else None)
    post.query_selector = AsyncMock(return_value=None)
    count = await linkedin._harvest_posts([post], "فید")
    assert count == 1
    assert len(linkedin._messages_queue) == 1
    assert linkedin._messages_queue[0]["keywords"] == "یاراترا"
    # duplicate -> skipped
    count2 = await linkedin._harvest_posts([post], "فید")
    assert count2 == 0


@pytest.mark.asyncio
async def test_linkedin_connect_and_disconnect(linkedin, monkeypatch):
    async def _boom(*a, **k):
        raise ImportError

    monkeypatch.setattr("collectors.linkedin.async_playwright", None, raising=False)
    assert await linkedin.connect() is False
    linkedin._connected = True
    linkedin._context = AsyncMock()
    linkedin._pw = AsyncMock()
    await linkedin.disconnect()
    assert linkedin._connected is False


# ═══════════════════ Web ═══════════════════
@pytest.fixture
def web(tmp_path):
    wc = WebCollector.__new__(WebCollector)
    wc.max_pages = 10
    wc.timeout = 5
    wc.translate = False
    wc._translator = None
    wc._sources = []
    wc._connected = False
    wc._seen_hashes = set()
    wc._bpw = None
    wc._bctx = None
    wc.sources_file = str(tmp_path / "sources.txt")
    return wc


def test_web_load_sources(tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text(
        "# comment\npage|https://x.com\nsite|https://y.com|depth=2\nnews|https://z.com|limit=5\nbad\n",
        encoding="utf-8",
    )
    wc = WebCollector.__new__(WebCollector)
    wc.sources_file = str(f)
    src = wc._load_sources()
    assert len(src) == 3
    assert src[0][0] == "page"
    assert src[1][2] == {"depth": "2"}
    assert src[2][0] == "news"


def test_web_extract_and_links():
    html = "<html><head><title>تیتر</title></head><body><script>x</script><p>متن تست</p><a href='/p'>link</a></body></html>"
    title, text = WebCollector._extract(html)
    assert "تیتر" in title
    assert "متن تست" in text
    links = WebCollector._extract_links(html, "https://x.com/base")
    assert links == ["https://x.com/p"]


def test_web_extract_fallback():
    html = "<b>bold</b> <i>text</i>"
    title, text = WebCollector._extract(html)
    assert "text" in text


def test_web_make_message_dedup(web):
    m1 = web._make_message("https://x.com/1", "تیتر", "متن کامل مقاله")
    assert m1 is not None and m1["platform"] == "web"
    assert web._make_message("https://x.com/1", "تیتر", "متن کامل مقاله") is None
    assert web._make_message("https://x.com/2", "", "") is None
    assert web._make_message("https://x.com/3", "", "x")


@pytest.mark.asyncio
async def test_web_connect_disconnect(web):
    assert await web.connect() is True
    assert web._connected is True
    assert await web.check_health() is True
    await web.disconnect()
    assert web._connected is False


@pytest.mark.asyncio
async def test_web_fetch_no_sources(web):
    await web.connect()
    assert await web.fetch_messages() == []
    web._connected = False
    assert await web.fetch_messages() == []


@pytest.mark.asyncio
async def test_web_fetch_with_sources(web, monkeypatch):
    import aiohttp

    class _Resp:
        status = 200
        headers = {"Content-Type": "text/html"}

        async def text(self, errors="ignore"):
            return "<html><title>T</title><p>قیمت یاراترا</p></html>"

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

        def get(self, url, allow_redirects=True):
            return _CM(_Resp())

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    web._sources = [("page", "https://x.com", {})]
    web._connected = True
    msgs = await web.fetch_messages()
    assert msgs and msgs[0]["platform"] == "web"


@pytest.mark.asyncio
async def test_web_crawl_news_no_links(web, monkeypatch):
    import aiohttp

    class _Session:
        def __init__(self, *a, **k):
            pass

        def get(self, url, allow_redirects=True):
            raise RuntimeError("down")

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    assert await web._crawl_news(None, "https://x.com/news", 5) == []
