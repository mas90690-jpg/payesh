"""Deep browser-collector coverage (eitaa/whatsapp/linkedin) with fully mocked Playwright pages."""
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.eitaa import EitaaCollector
from collectors.linkedin import LinkedInCollector
from collectors.whatsapp import WhatsAppCollector


# ═══════════════════ Eitaa deep ═══════════════════
@pytest.fixture
def eitaa(tmp_path):
    ec = EitaaCollector.__new__(EitaaCollector)
    ec._poll_seconds = 180
    ec._max_chats = 5
    ec.headless = True
    ec._chat_open_delay = 0.0
    ec._messages_per_chat = 10
    ec._backfill_scrolls = 2
    ec._needs_backfill = True
    ec._pw = None
    ec._context = None
    ec._page = None
    ec._connected = False
    ec._messages_queue = []
    ec._seen_ids = set()
    ec._seen_path = str(tmp_path / "seen.json")
    ec._media_dir = str(tmp_path / "media")
    ec._user_data_dir = str(tmp_path)
    ec._targets = MagicMock(enabled=lambda: False, is_target=lambda *a, **k: True)
    ec._kw = MagicMock(has_match=lambda t: True)
    return ec


def _mk_eitaa_page():
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    page.query_selector = AsyncMock(return_value=None)
    page.inner_text = AsyncMock(return_value="گروه هدف\nmsg")
    page.evaluate = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value="<html></html>")
    page.goto = AsyncMock()
    page.click = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_eitaa_launch_context_fallback(eitaa, monkeypatch):
    """_launch_context tries chrome -> msedge -> chromium, raising only when all fail."""
    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(side_effect=RuntimeError("no browser"))
    eitaa._pw = pw
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    with pytest.raises(RuntimeError):
        await eitaa._launch_context(True)
    assert pw.chromium.launch_persistent_context.await_count == 3


@pytest.mark.asyncio
async def test_eitaa_launch_context_success(eitaa, monkeypatch):
    pw = MagicMock()
    ctx = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(
        side_effect=[RuntimeError("chrome down"), ctx]
    )
    eitaa._pw = pw
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    out = await eitaa._launch_context(True)
    assert out is ctx


@pytest.mark.asyncio
async def test_eitaa_connect_flow(eitaa, monkeypatch):
    page = _mk_eitaa_page()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.eitaa.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    eitaa._is_logged_in = AsyncMock(side_effect=[False, False, True])
    assert await eitaa.connect() is True
    assert eitaa._connected is True
    assert eitaa._needs_backfill is True


@pytest.mark.asyncio
async def test_eitaa_connect_no_session(eitaa, monkeypatch):
    page = _mk_eitaa_page()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.eitaa.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    eitaa._is_logged_in = AsyncMock(return_value=False)
    eitaa.dump_dom = AsyncMock(return_value="x.html")
    assert await eitaa.connect() is False
    assert eitaa._connected is False


@pytest.mark.asyncio
async def test_eitaa_connect_error(eitaa, monkeypatch):
    monkeypatch.setattr("collectors.eitaa.async_playwright", None)
    assert await eitaa.connect() is False


@pytest.mark.asyncio
async def test_eitaa_backfill_scroll(eitaa):
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    eitaa._page = page
    eitaa._needs_backfill = True
    await eitaa._backfill_scroll()
    assert page.evaluate.await_count == 2
    eitaa._needs_backfill = False
    await eitaa._backfill_scroll()
    assert page.evaluate.await_count == 2


@pytest.mark.asyncio
async def test_eitaa_scrape_current_chat_no_messages(eitaa):
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    page.inner_text = AsyncMock(return_value="no msgs")
    eitaa._page = page
    eitaa._needs_backfill = False
    eitaa._get_open_chat_title = AsyncMock(return_value="گروه هدف")
    await eitaa._scrape_current_chat()
    assert eitaa._messages_queue == []


@pytest.mark.asyncio
async def test_eitaa_scrape_current_chat_messages(eitaa, tmp_path):
    page = AsyncMock()
    bubble = AsyncMock()
    bubble.get_attribute = AsyncMock(side_effect=lambda a: "mid1" if a == "data-mid" else None)
    bubble.inner_text = AsyncMock(return_value="قیمت یاراترا")
    bubble.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[bubble])
    page.query_selector = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value=None)
    eitaa._page = page
    eitaa._needs_backfill = False
    eitaa._get_open_chat_title = AsyncMock(return_value="گروه هدف")
    await eitaa._scrape_current_chat()
    assert len(eitaa._messages_queue) == 1
    assert eitaa._messages_queue[0]["text"] == "قیمت یاراترا"
    assert eitaa._messages_queue[0]["platform"] == "eitaa"
    # duplicate ignored
    await eitaa._scrape_current_chat()
    assert len(eitaa._messages_queue) == 1


@pytest.mark.asyncio
async def test_eitaa_scrape_all_chats(eitaa):
    chat_item = AsyncMock()
    chat_item.click = AsyncMock()
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[chat_item])
    page.query_selector = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value=None)
    page.inner_text = AsyncMock(return_value="گروه هدف")
    eitaa._page = page
    eitaa._scrape_current_chat = AsyncMock()
    eitaa._extract_item_title = AsyncMock(return_value="گروه هدف")
    await eitaa._scrape_all_chats()
    assert eitaa._scrape_current_chat.await_count == 1


@pytest.mark.asyncio
async def test_eitaa_scrape_all_chats_no_items(eitaa):
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    eitaa._page = page
    eitaa.dump_dom = AsyncMock()
    eitaa._scrape_current_chat = AsyncMock()
    await eitaa._scrape_all_chats()
    assert eitaa._scrape_current_chat.await_count == 1


@pytest.mark.asyncio
async def test_eitaa_fetch_messages(eitaa):
    eitaa._connected = True
    eitaa._page = AsyncMock()
    eitaa._scrape_all_chats = AsyncMock()
    eitaa._messages_queue = [{"id": "1"}]
    msgs = await eitaa.fetch_messages()
    assert msgs == [{"id": "1"}]
    assert eitaa._needs_backfill is False


# ═══════════════════ WhatsApp deep ═══════════════════
@pytest.fixture
def whatsapp(tmp_path):
    wc = WhatsAppCollector.__new__(WhatsAppCollector)
    wc._poll_seconds = 180
    wc._max_chats = 5
    wc.headless = True
    wc._chat_open_delay = 0.0
    wc._messages_per_chat = 10
    wc._backfill_scrolls = 2
    wc._needs_backfill = True
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


@pytest.mark.asyncio
async def test_whatsapp_launch_context_chrome_path(whatsapp, monkeypatch):
    pw = MagicMock()
    ctx = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    whatsapp._pw = pw
    monkeypatch.setattr("config.CHROME_PATH", "C:/chrome.exe")
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    assert await whatsapp._launch_context(True) is ctx


@pytest.mark.asyncio
async def test_whatsapp_launch_context_chrome_path_fails(whatsapp, monkeypatch):
    pw = MagicMock()
    ctx = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(
        side_effect=[RuntimeError("bad path"), ctx]
    )
    whatsapp._pw = pw
    monkeypatch.setattr("config.CHROME_PATH", "C:/chrome.exe")
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    assert await whatsapp._launch_context(True) is ctx


@pytest.mark.asyncio
async def test_whatsapp_connect_flow(whatsapp, monkeypatch):
    page = AsyncMock()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.whatsapp.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    monkeypatch.setattr("config.CHROME_PATH", None)
    whatsapp._is_logged_in = AsyncMock(return_value=True)
    assert await whatsapp.connect() is True
    assert whatsapp._connected is True


@pytest.mark.asyncio
async def test_whatsapp_connect_no_session(whatsapp, monkeypatch):
    page = AsyncMock()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.whatsapp.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    monkeypatch.setattr("config.CHROME_PATH", None)
    whatsapp._is_logged_in = AsyncMock(return_value=False)
    assert await whatsapp.connect() is False


@pytest.mark.asyncio
async def test_whatsapp_backfill(whatsapp):
    page = AsyncMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()
    whatsapp._page = page
    await whatsapp._backfill_scroll()
    assert page.mouse.wheel.await_count == 2


@pytest.mark.asyncio
async def test_whatsapp_scrape_open_chat(whatsapp, tmp_path):
    bubble = AsyncMock()
    bubble.get_attribute = AsyncMock(side_effect=lambda a: "false_123@g.us_5" if a == "data-id" else None)
    tnode = AsyncMock()
    tnode.inner_text = AsyncMock(return_value="قیمت یاراترا")
    bubble.query_selector = AsyncMock(return_value=tnode)
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[bubble])
    page.query_selector = AsyncMock(return_value=None)
    whatsapp._page = page
    whatsapp._get_open_chat_title = AsyncMock(return_value="گروه واتساپ")
    await whatsapp._scrape_open_chat()
    assert len(whatsapp._messages_queue) == 1
    assert whatsapp._messages_queue[0]["platform"] == "whatsapp"
    assert whatsapp._messages_queue[0]["chat_type"] == "group"


@pytest.mark.asyncio
async def test_whatsapp_scrape_open_chat_private(whatsapp):
    bubble = AsyncMock()
    bubble.get_attribute = AsyncMock(side_effect=lambda a: "false_456@c.us_7" if a == "data-id" else None)
    tnode = AsyncMock()
    tnode.inner_text = AsyncMock(return_value="قیمت یاراترا")
    bubble.query_selector = AsyncMock(return_value=tnode)
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[bubble])
    whatsapp._page = page
    whatsapp._get_open_chat_title = AsyncMock(return_value="علی")
    await whatsapp._scrape_open_chat()
    assert whatsapp._messages_queue[0]["chat_type"] == "private"


@pytest.mark.asyncio
async def test_whatsapp_scrape_all_chats(whatsapp):
    item = AsyncMock()
    item.click = AsyncMock()
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[item])
    whatsapp._page = page
    whatsapp._scrape_open_chat = AsyncMock()
    await whatsapp._scrape_all_chats()
    assert whatsapp._scrape_open_chat.await_count == 1


@pytest.mark.asyncio
async def test_whatsapp_scrape_all_chats_none(whatsapp):
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    whatsapp._page = page
    whatsapp.dump_dom = AsyncMock()
    whatsapp._scrape_open_chat = AsyncMock()
    await whatsapp._scrape_all_chats()
    assert whatsapp._scrape_open_chat.await_count == 1


@pytest.mark.asyncio
async def test_whatsapp_dump_and_fetch(whatsapp, tmp_path):
    page = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    whatsapp._page = page
    path = await whatsapp.dump_dom()
    assert os.path.exists(path)
    whatsapp._connected = True
    whatsapp._scrape_all_chats = AsyncMock()
    whatsapp._messages_queue = [{"id": "1"}]
    assert await whatsapp.fetch_messages() == [{"id": "1"}]


# ═══════════════════ LinkedIn deep ═══════════════════
@pytest.fixture
def linkedin(tmp_path):
    lc = LinkedInCollector.__new__(LinkedInCollector)
    lc._poll_seconds = 600
    lc._max_results = 5
    lc.headless = True
    lc._search_delay = 0.0
    lc._scrolls = 2
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


@pytest.mark.asyncio
async def test_linkedin_launch_context(linkedin, monkeypatch):
    pw = MagicMock()
    ctx = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(
        side_effect=[RuntimeError("c1"), RuntimeError("c2"), ctx]
    )
    linkedin._pw = pw
    monkeypatch.setattr("config.CHROME_PATH", None)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    assert await linkedin._launch_context(True) is ctx


@pytest.mark.asyncio
async def test_linkedin_connect_flow(linkedin, monkeypatch):
    page = AsyncMock()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.linkedin.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    monkeypatch.setattr("config.CHROME_PATH", None)
    linkedin._wait_login = AsyncMock(return_value=True)
    linkedin._export_state = AsyncMock()
    assert await linkedin.connect() is True


@pytest.mark.asyncio
async def test_linkedin_connect_restore_state(linkedin, monkeypatch, tmp_path):
    page = AsyncMock()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    ctx.add_cookies = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.linkedin.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    monkeypatch.setattr("config.CHROME_PATH", None)
    (tmp_path / "state.json").write_text(json.dumps({"cookies": [{"name": "li_at"}]}), encoding="utf-8")
    linkedin._wait_login = AsyncMock(side_effect=[False, True])
    linkedin._export_state = AsyncMock()
    linkedin._restore_state = AsyncMock(return_value=True)
    assert await linkedin.connect() is True


@pytest.mark.asyncio
async def test_linkedin_connect_auto_login(linkedin, monkeypatch):
    page = AsyncMock()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.linkedin.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    monkeypatch.setattr("config.CHROME_PATH", None)
    linkedin.email = "e@e.com"
    linkedin.password = "p"
    linkedin._wait_login = AsyncMock(side_effect=[False, False])
    linkedin._restore_state = AsyncMock(return_value=False)
    linkedin._auto_login = AsyncMock(return_value=True)
    linkedin._export_state = AsyncMock()
    assert await linkedin.connect() is True


@pytest.mark.asyncio
async def test_linkedin_connect_fail(linkedin, monkeypatch):
    page = AsyncMock()
    pw = MagicMock()
    pw.start = AsyncMock(return_value=pw)
    ctx = AsyncMock()
    ctx.pages = [page]
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr("collectors.linkedin.async_playwright", lambda: pw, raising=False)
    monkeypatch.setattr("config.get_browser_proxy", lambda: {})
    monkeypatch.setattr("config.CHROME_PATH", None)
    linkedin._wait_login = AsyncMock(return_value=False)
    linkedin._restore_state = AsyncMock(return_value=False)
    linkedin.email = None
    assert await linkedin.connect() is False


@pytest.mark.asyncio
async def test_linkedin_auto_login(linkedin):
    page = AsyncMock()
    el = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.url = "https://www.linkedin.com/login"
    linkedin._page = page
    linkedin.email = "e"
    linkedin.password = "p"
    linkedin._is_logged_in = AsyncMock(side_effect=[False, True])
    assert await linkedin._auto_login() is True
    assert linkedin._connected is True


@pytest.mark.asyncio
async def test_linkedin_auto_login_checkpoint(linkedin):
    page = AsyncMock()
    el = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.url = "https://www.linkedin.com/checkpoint"
    linkedin._page = page
    linkedin.email = "e"
    linkedin.password = "p"
    linkedin._is_logged_in = AsyncMock(return_value=False)
    assert await linkedin._auto_login() is False


@pytest.mark.asyncio
async def test_linkedin_is_logged_in(linkedin):
    page = AsyncMock()
    visible = AsyncMock()
    visible.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible)
    linkedin._page = page
    assert await linkedin._is_logged_in() is False  # authwall modal visible
    page.query_selector = AsyncMock(return_value=None)
    assert await linkedin._is_logged_in() is False
    nav = AsyncMock()
    nav.is_visible = AsyncMock(return_value=True)
    # 3 modal selectors None, then LOGGEDIN marker hit
    page.query_selector = AsyncMock(side_effect=[None, None, None, nav])
    assert await linkedin._is_logged_in() is True


@pytest.mark.asyncio
async def test_linkedin_export_restore_state(linkedin, tmp_path):
    ctx = AsyncMock()
    ctx.storage_state = AsyncMock()
    linkedin._context = ctx
    await linkedin._export_state()
    ctx.storage_state.assert_awaited_once()
    # restore with no file
    linkedin._state_path = str(tmp_path / "none.json")
    assert await linkedin._restore_state() is False


@pytest.mark.asyncio
async def test_linkedin_harvest_dedup_and_media(linkedin, tmp_path, monkeypatch):
    post = AsyncMock()
    post.inner_text = AsyncMock(return_value="پست یاراترا")
    post.get_attribute = AsyncMock(side_effect=lambda a: "urn1" if a == "data-urn" else None)
    post.query_selector = AsyncMock(return_value=None)
    count = await linkedin._harvest_posts([post, post], "فید")
    assert count == 1  # dedup
    assert len(linkedin._messages_queue) == 1


@pytest.mark.asyncio
async def test_linkedin_find_posts(linkedin):
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[1, 2])
    linkedin._page = page
    posts = await linkedin._find_posts()
    assert posts == [1, 2]
    page.query_selector_all = AsyncMock(return_value=[])
    assert await linkedin._find_posts() == []


@pytest.mark.asyncio
async def test_linkedin_scrape_feed_and_search(linkedin):
    page = AsyncMock()
    page.goto = AsyncMock()
    page.mouse.wheel = AsyncMock()
    linkedin._page = page
    linkedin._check_session_alive = AsyncMock(return_value=True)
    linkedin._find_posts = AsyncMock(return_value=[MagicMock()])
    linkedin._harvest_posts = AsyncMock(return_value=1)
    await linkedin._scrape_feed()
    assert linkedin._harvest_posts.await_count == 1
    await linkedin._scrape_search("قیمت کود")
    assert linkedin._harvest_posts.await_count == 2
    # session dead -> no harvest
    linkedin._check_session_alive = AsyncMock(return_value=False)
    linkedin._harvest_posts = AsyncMock(return_value=0)
    await linkedin._scrape_feed()
    assert linkedin._harvest_posts.await_count == 0


@pytest.mark.asyncio
async def test_linkedin_fetch_messages(linkedin, tmp_path):
    linkedin._connected = True
    page = AsyncMock()
    page.url = "https://www.linkedin.com/feed/"
    linkedin._page = page
    linkedin._monitor_feed = True
    linkedin._scrape_feed = AsyncMock()
    linkedin._load_searches = MagicMock(return_value=[])
    linkedin._save_seen = MagicMock()
    linkedin._export_state = AsyncMock()
    linkedin._messages_queue = [{"id": "1"}]
    msgs = await linkedin.fetch_messages()
    assert msgs == [{"id": "1"}]
