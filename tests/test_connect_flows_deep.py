"""Deep connect-flow tests: bale, rubika, telegram (fake libs), plus backup_service."""
import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.bale import BaleCollector
from collectors.rubika import RubikaCollector
from collectors.telegram import TelegramCollector
from core.services.backup_service import BackupService


# ═══════════════════ Bale connect with fake aiobale ═══════════════════
@pytest.fixture
def bale(tmp_path):
    bc = BaleCollector.__new__(BaleCollector)
    bc.type = "user"
    bc.phone = "09123456789"
    bc.password = "p"
    bc._client = None
    bc._dispatcher = None
    bc._connected = False
    bc._messages_queue = []
    bc._listen_task = None
    bc._is_running = True
    bc._last_message_id = None
    bc._history_fetched = False
    bc._chat_cache = {}
    bc._media_dir = str(tmp_path / "media")
    bc._session_path = str(tmp_path / "bale_session")
    return bc


def _fake_bale_lib():
    """Return (Client, Dispatcher, Message, ChatType) mocks for collectors.bale."""
    class _Client:
        def __init__(self, dispatcher, session_file=None, **kw):
            self.dispatcher = dispatcher
            self.session_file = session_file
            self.id = None
            self.me = None
            self.messages = []
            self.running = False

        async def run(self):
            self.running = True
            self.id = 123
            self.me = {"user_id": 1}
            await asyncio.sleep(3600)

        async def stop(self):
            self.running = False

        async def get_full_me(self):
            return MagicMock(user_id=1)

        async def get_full_group(self, cid):
            return MagicMock(group=MagicMock(title="گروه تست", username="g"))

        async def get_group_link(self, cid):
            return "https://ble.ir/g"

        async def load_dialogs(self, limit=100):
            return []

        async def download_file(self, fid, path):
            open(path, "wb").write(b"x")

        async def send_message(self, **kw):
            return None

    class _Dispatcher:
        def __init__(self):
            self.handlers = []

        def message(self):
            def deco(fn):
                self.handlers.append(fn)
                return fn

            return deco

    class _ChatType:
        PRIVATE = 1
        GROUP = 2
        CHANNEL = 3
        SUPER_GROUP = 4

    return _Client, _Dispatcher, _ChatType


@pytest.mark.asyncio
async def test_bale_connect_full(bale, monkeypatch):
    Client, Dispatcher, ChatType = _fake_bale_lib()
    monkeypatch.setattr("collectors.bale.Client", Client)
    monkeypatch.setattr("collectors.bale.Dispatcher", Dispatcher)
    monkeypatch.setattr("collectors.bale.ChatType", ChatType)
    monkeypatch.setattr("config.get_proxy", lambda: "")
    messenger_calls = []

    async def fake_register(p, s):
        messenger_calls.append((p, s))

    monkeypatch.setattr("collectors.bale.messenger_registry.register_sender", fake_register)
    assert await bale.connect() is True
    assert bale._connected is True
    assert bale._client is not None
    assert messenger_calls and messenger_calls[0][0] == "bale"
    await bale.disconnect()
    assert bale._connected is False


@pytest.mark.asyncio
async def test_bale_connect_proxy(bale, monkeypatch):
    Client, Dispatcher, ChatType = _fake_bale_lib()
    monkeypatch.setattr("collectors.bale.Client", Client)
    monkeypatch.setattr("collectors.bale.Dispatcher", Dispatcher)
    monkeypatch.setattr("config.get_proxy", lambda: "http://127.0.0.1:10809")
    assert await bale.connect() is True


@pytest.mark.asyncio
async def test_bale_connect_phone_strip_zero(bale, monkeypatch):
    Client, Dispatcher, ChatType = _fake_bale_lib()
    bale.phone = "09123456789"

    class _C2(Client):
        def __init__(self, dispatcher, session_file=None, **kw):
            super().__init__(dispatcher, session_file, **kw)

    monkeypatch.setattr("collectors.bale.Client", _C2)
    monkeypatch.setattr("collectors.bale.Dispatcher", Dispatcher)
    monkeypatch.setattr("config.get_proxy", lambda: "")
    assert await bale.connect() is True


@pytest.mark.asyncio
async def test_bale_connect_dispatcher_handler_receives(bale, monkeypatch):
    """The dispatcher's message handler should enqueue a unified message."""
    Client, Dispatcher, ChatType = _fake_bale_lib()
    monkeypatch.setattr("collectors.bale.Client", Client)
    monkeypatch.setattr("collectors.bale.Dispatcher", Dispatcher)
    monkeypatch.setattr("collectors.bale.ChatType", ChatType)
    monkeypatch.setattr("config.get_proxy", lambda: "")
    await bale.connect()
    handler = bale._dispatcher.handlers[0]

    class _Doc:
        file_id = None
        access_hash = None
        caption = None
        mime_type = ""
        name = ""

    class _Chat:
        id = "-100"
        type = "group"

    class _Msg:
        text = "قیمت یاراترا"
        document = None
        chat = _Chat()
        message_id = 77
        sender_id = 42
        replied_to = None
        quoted_replied_to = None

    await handler(_Msg())
    assert len(bale._messages_queue) == 1
    assert bale._messages_queue[0]["text"] == "قیمت یاراترا"


@pytest.mark.asyncio
async def test_bale_fetch_history_with_dialogs(bale, monkeypatch):
    Client, Dispatcher, ChatType = _fake_bale_lib()

    class _Peer:
        id = 100
        type = "group"

    class _Dialog:
        peer = _Peer()

    class _HistoryClient(Client):
        def __init__(self, dispatcher, session_file=None, **kw):
            super().__init__(dispatcher, session_file, **kw)

        async def load_dialogs(self, limit=100):
            return [_Dialog()]

        async def load_history(self, chat_id=None, chat_type=None, limit=200):
            class _Doc:
                file_id = None
                caption = None
                mime_type = ""
                name = ""

            class _Chat:
                id = 100
                type = "group"

            class _M:
                text = "قیمت یاراترا"
                document = None
                chat = _Chat()
                message_id = 1
                sender_id = 1
                date = datetime.now()
                replied_to = None
                quoted_replied_to = None

            return [_M()]

    monkeypatch.setattr("collectors.bale.Client", _HistoryClient)
    monkeypatch.setattr("collectors.bale.Dispatcher", Dispatcher)
    monkeypatch.setattr("collectors.bale.ChatType", ChatType)
    monkeypatch.setattr("collectors.bale.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("config.get_proxy", lambda: "")
    await bale.connect()
    msgs = await bale.fetch_history(datetime.now() - timedelta(days=1))
    assert msgs and msgs[0]["platform"] == "bale"


@pytest.mark.asyncio
async def test_bale_download_file(bale, monkeypatch):
    Client, Dispatcher, ChatType = _fake_bale_lib()
    client = Client(None)
    bale._client = client
    path = await bale._download_file(12345, "photo")
    assert path is not None and os.path.exists(path)
    assert await bale._download_file(None, "photo") is None
    assert await bale._download_file(1, "weird") is None  # no download method


# ═══════════════════ Rubika connect with fake rubpy ═══════════════════
@pytest.fixture
def rubika(tmp_path):
    rc = RubikaCollector.__new__(RubikaCollector)
    rc.phone = "09123456789"
    rc.password = "p"
    rc._client = None
    rc._messages_queue = []
    rc._connected = False
    rc._running = False
    rc._loop_task = None
    rc._chat_cache = {}
    rc._media_dir = str(tmp_path / "media")
    rc._session_path = str(tmp_path / "rubika_session")
    return rc


class _FakeRubpyClient:
    def __init__(self, name=None):
        self.name = name
        self.handlers = []
        self.started = False
        self.ran = False

    def on_message_updates(self):
        def deco(fn):
            self.handlers.append(fn)
            return fn

        return deco

    async def start(self, phone_number=None):
        self.started = True

    async def run(self):
        self.ran = True
        await asyncio.sleep(3600)

    async def disconnect(self):
        pass

    async def get_chats(self):
        return MagicMock(to_dict=lambda: {"chats": [{"object_guid": "g1", "type": "group"}]})

    async def get_messages(self, guid, max_id=None, limit=200):
        return MagicMock(
            to_dict=lambda: {"messages": [{"message_id": "m1", "text": "قیمت یاراترا", "time": int(datetime.now().timestamp())}]}
        )


@pytest.mark.asyncio
async def test_rubika_connect_full(rubika, monkeypatch):
    monkeypatch.setattr("collectors.rubika.Client", _FakeRubpyClient)
    assert await rubika.connect() is True
    assert rubika._connected is True
    assert rubika._client.started
    assert len(rubika._client.handlers) == 1
    await rubika.disconnect()
    assert rubika._connected is False


@pytest.mark.asyncio
async def test_rubika_connect_normalize_phone(rubika, monkeypatch):
    monkeypatch.setattr("collectors.rubika.Client", _FakeRubpyClient)
    rubika.phone = "+989123456789"
    assert await rubika.connect() is True


@pytest.mark.asyncio
async def test_rubika_on_message_handler(rubika, monkeypatch):
    monkeypatch.setattr("collectors.rubika.Client", _FakeRubpyClient)
    await rubika.connect()
    handler = rubika._client.handlers[0]

    class _Upd:
        object_guid = "g1"
        text = "قیمت یاراترا"
        is_group = True
        is_channel = False
        message_id = 5
        author_guid = "u5"
        reply_message_id = None

        async def get_object(self):
            return MagicMock(to_dict=lambda: {"title": "گروه", "username": "ru"})

    await handler(_Upd())
    assert len(rubika._messages_queue) == 1
    assert rubika._messages_queue[0]["platform"] == "rubika"


@pytest.mark.asyncio
async def test_rubika_fetch_history_full(rubika, monkeypatch):
    monkeypatch.setattr("collectors.rubika.Client", _FakeRubpyClient)
    await rubika.connect()
    msgs = await rubika.fetch_history(datetime.now() - timedelta(days=1))
    assert msgs and msgs[0]["platform"] == "rubika"
    assert rubika._client.ran


@pytest.mark.asyncio
async def test_rubika_download_media(rubika, tmp_path):
    class _Upd:
        async def download(self, save_as=None):
            open(save_as, "wb").write(b"data")

    path = await rubika._download_media(_Upd(), "photo", "abc")
    assert path and os.path.exists(path)
    assert rubika._download_media.__name__


# ═══════════════════ Telegram connect with fake telethon ═══════════════════
@pytest.fixture
def telegram(tmp_path):
    tc = TelegramCollector.__new__(TelegramCollector)
    tc.api_id = 12345
    tc.api_hash = "hash"
    tc.phone = "09123456789"
    tc.db = None
    tc._client = None
    tc._messages_queue = []
    tc._connected = False
    tc._listen_task = None
    tc._media_dir = str(tmp_path / "media")
    tc._session_path = str(tmp_path / "tg_session")
    return tc


class _FakeTelegramClient:
    def __init__(self, session, api_id, api_hash, proxy=None):
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = proxy
        self.handlers = []
        self.started = False
        self.downloaded = []

    def on(self, event):
        def deco(fn):
            self.handlers.append((event, fn))
            return fn

        return deco

    async def start(self, phone=None):
        self.started = True
        return self

    async def disconnect(self):
        pass

    async def download_media(self, message, file=None):
        self.downloaded.append((message, file))
        return file

    async def iter_dialogs(self):
        return
        yield  # pragma: no cover

    async def iter_messages(self, entity, **kw):
        return
        yield  # pragma: no cover


class _FakeEvents:
    class NewMessage:
        pass


@pytest.mark.asyncio
async def test_telegram_connect_full(telegram, monkeypatch):
    monkeypatch.setattr("collectors.telegram.TelegramClient", _FakeTelegramClient)
    monkeypatch.setattr("collectors.telegram.events", _FakeEvents())
    monkeypatch.setattr("config.get_proxy", lambda: "")
    assert await telegram.connect() is True
    assert telegram._connected is True
    assert telegram._client.started
    await telegram.disconnect()
    assert telegram._connected is False


@pytest.mark.asyncio
async def test_telegram_connect_timeout(telegram, monkeypatch):
    class _SlowClient(_FakeTelegramClient):
        async def start(self, phone=None):
            await asyncio.sleep(10)

    monkeypatch.setattr("collectors.telegram.TelegramClient", _SlowClient)
    monkeypatch.setattr("collectors.telegram.events", _FakeEvents())
    monkeypatch.setattr("collectors.telegram.asyncio.wait_for", lambda c, t: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr("config.get_proxy", lambda: "")
    assert await telegram.connect() is False


@pytest.mark.asyncio
async def test_telegram_message_handler_event(telegram, monkeypatch):
    from tests.helpers.mock_factory import mock_telegram_message

    monkeypatch.setattr("collectors.telegram.TelegramClient", _FakeTelegramClient)
    monkeypatch.setattr("collectors.telegram.events", _FakeEvents())
    monkeypatch.setattr("collectors.telegram.get_keyword_finder", lambda: MagicMock(has_match=lambda t: True), raising=False)
    monkeypatch.setattr("config.get_proxy", lambda: "")
    await telegram.connect()
    handler = telegram._client.handlers[0][1]
    msg = mock_telegram_message("قیمت یاراترا")
    msg.chat = MagicMock(id=123, title="گروه", username="g")
    msg.sender = MagicMock(id=1, first_name="علی")
    msg.date = MagicMock(timestamp=lambda: 1700000000)
    msg.photo = None
    msg.video = None
    msg.audio = None
    msg.document = None
    msg.voice = None
    msg.caption = None
    msg.reply_to_msg_id = None
    msg.is_group = True
    msg.is_channel = False
    await handler(MagicMock(message=msg))
    assert len(telegram._messages_queue) == 1


@pytest.mark.asyncio
async def test_telegram_fetch_history_flood_wait(telegram, monkeypatch):
    class _FloodError(Exception):
        seconds = 1

    class _Dial:
        is_group = True
        is_channel = False
        entity = "e"
        id = 1

    class _Client(_FakeTelegramClient):
        async def iter_dialogs(self):
            yield _Dial()

        async def iter_messages(self, entity, **kw):
            raise _FloodError()

    class _Errs:
        FloodWaitError = _FloodError

    monkeypatch.setattr("collectors.telegram.TelegramClient", _Client)
    monkeypatch.setattr("collectors.telegram.events", _FakeEvents())
    monkeypatch.setattr("collectors.telegram._telethon_errors", _Errs, raising=False)
    monkeypatch.setattr("collectors.telegram.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("config.get_proxy", lambda: "")
    await telegram.connect()
    msgs = await telegram.fetch_history(datetime.now() - timedelta(days=1))
    assert msgs == []


# ═══════════════════ backup_service deep ═══════════════════
@pytest.fixture
def backup(tmp_path, monkeypatch):
    monkeypatch.setattr("core.services.backup_service.BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr("core.services.backup_service.DB_PATH", str(tmp_path / "messages.db"))
    svc = BackupService()
    svc.daily_dir = str(tmp_path / "daily")
    svc.full_dir = str(tmp_path / "full")
    svc.hash_file = str(tmp_path / "hashes.json")
    svc.last_backup_file = str(tmp_path / "last.txt")
    svc.last_full_backup_file = str(tmp_path / "last_full.txt")
    svc.stats_file = str(tmp_path / "stats.json")
    svc.init()
    return svc


def _valid_db(tmp_path):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT)")
    conn.execute("INSERT INTO messages VALUES ('1')")
    conn.commit()
    conn.close()
    return path


@pytest.mark.asyncio
async def test_backup_daily_success(backup, tmp_path):
    _valid_db(tmp_path)
    path = await backup.backup_daily()
    assert path and os.path.exists(path)
    assert backup.get_stats()["daily"]


@pytest.mark.asyncio
async def test_backup_full_success(backup, tmp_path, monkeypatch):
    _valid_db(tmp_path)
    monkeypatch.setattr("core.services.backup_service.BASE_DIR", str(tmp_path))
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    path = await backup.backup_full()
    assert path and os.path.exists(path)
    assert backup.get_stats()["full"]


@pytest.mark.asyncio
async def test_backup_manual_and_periodic(backup, tmp_path, monkeypatch):
    _valid_db(tmp_path)
    monkeypatch.setattr("core.services.backup_service.BASE_DIR", str(tmp_path))
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    assert await backup.manual_daily() is not None
    assert await backup.manual_full() is not None
    # periodic with _running False -> exits immediately
    backup._running = False
    await backup.run_periodic()


@pytest.mark.asyncio
async def test_backup_run_periodic_loop(backup, tmp_path, monkeypatch):
    _valid_db(tmp_path)
    monkeypatch.setattr("core.services.backup_service.BASE_DIR", str(tmp_path))
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    backup._running = True
    monkeypatch.setattr("core.services.backup_service.asyncio.sleep", AsyncMock())
    await backup.run_periodic()  # runs one iteration then sleeps forever (mocked)


def test_backup_get_file_hash_and_hashes(backup, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x", encoding="utf-8")
    h = backup._get_file_hash(str(f))
    assert h and "_" in h
    assert backup._get_file_hash(str(tmp_path / "nope")) is None
    backup._save_hashes({"a": "1"})
    assert backup._load_hashes() == {"a": "1"}
    assert backup._get_db_size() == 0


def test_backup_compress_invalid(backup, tmp_path):
    assert backup._compress_folder(str(tmp_path / "missing"), str(tmp_path / "x.zip")) is False
