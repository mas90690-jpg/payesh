# tests/unit/test_telegram.py
"""
Unit tests for collectors/telegram.py — v3.0 (from scratch).

Coverage:
  - _is_system_name (Persian / English / prefix / empty / None)
  - Constants (MSG_SELECTORS، CHAT_ITEMS_SELECTOR، ...)
  - _extract_item_title (peer-title / data-name / empty)
  - _extract_sender_from_bubble (peer-id / name / empty)
  - _detect_chat_type (url-based / active-item / None)
  - _extract_chat_meta (username / invite / none)
  - _is_logged_in (marker / nothing / no page)
  - Properties (platform_name، poll_interval، ...)
  - State (first_run، seen)
  - fetch_messages (not connected، no page)
  - _scrape_current_chat (with mocked page)

Run:
  pytest tests/unit/test_telegram.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.telegram import (
    TelegramWebCollector,
    _is_system_name,
    CHAT_ITEMS_SELECTOR,
    MSG_SELECTORS,
    TITLE_SELECTORS,
    SENDER_NAME_SELECTORS,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "TELEGRAM_PHONE", "TELEGRAM_POLL_SECONDS",
    "TELEGRAM_MAX_CHATS", "TELEGRAM_MESSAGES_PER_CHAT",
    "TELEGRAM_HISTORY_LIMIT",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("TELEGRAM_PHONE", "09120000000")
    monkeypatch.setenv("TELEGRAM_POLL_SECONDS", "999")
    return TelegramWebCollector(phone="09120000000", poll_seconds=999)


def _mock_item(attrs=None, children=None, inner_text=""):
    attrs = attrs or {}
    children = children or {}
    item = MagicMock()
    item.get_attribute = AsyncMock(side_effect=lambda name: attrs.get(name))
    item.inner_text = AsyncMock(return_value=inner_text)
    item.query_selector = AsyncMock(side_effect=lambda sel: children.get(sel))
    item.bounding_box = AsyncMock(return_value={"width": 100, "height": 30})
    item.click = AsyncMock()
    return item


def _mock_page(url="https://web.telegram.org/k/", selectors=None, items=None):
    selectors = selectors or {}
    page = MagicMock()
    page.url = url
    page.query_selector = AsyncMock(side_effect=lambda sel: selectors.get(sel))
    page.query_selector_all = AsyncMock(return_value=items or [])
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.wait_for_selector = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.evaluate = AsyncMock(return_value=False)
    page.close = AsyncMock()
    page.locator = MagicMock()
    return page


def _bubble(mid, text, ts_val):
    m = MagicMock()
    m.get_attribute = AsyncMock(side_effect=lambda name: {
        "data-mid": mid, "data-timestamp": str(ts_val),
    }.get(name))
    m.inner_text = AsyncMock(return_value=text)
    m.query_selector = AsyncMock(return_value=None)
    return m


def _reset_stats(collector):
    collector._last_scrape_stats = {
        "total_chats": 0, "unique_chats": 0, "processed": 0,
        "skipped_system": 0, "skipped_target": 0, "failed": 0,
        "messages_found": 0, "messages_skipped_old": 0,
        "messages_skipped_dup": 0, "messages_skipped_kw": 0,
    }


# ═══════════════════════════════════════════════════════════
# 1) Constants
# ═══════════════════════════════════════════════════════════
def test_chat_items_selector():
    assert "chatlist-chat" in CHAT_ITEMS_SELECTOR


def test_msg_selectors_not_empty():
    assert len(MSG_SELECTORS) >= 2


def test_title_selectors_not_empty():
    assert len(TITLE_SELECTORS) > 0


def test_sender_selectors_not_empty():
    assert len(SENDER_NAME_SELECTORS) > 0


# ═══════════════════════════════════════════════════════════
# 2) _is_system_name (FIX #2 — بدون substring)
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("پیام‌های ذخیره شده", True),
        ("Saved Messages", True),
        ("Saved", True),
        ("Drafts", True),
        ("You", True),
        ("تلگرام", True),
        ("Telegram", True),
        ("کانال ایجاد شد", True),
        ("گروه ایجاد شد", True),
        ("Channel created", True),
        # 🎯 FIX: این‌ها نباید skip شوند
        ("Saved recipes", False),
        ("Draft pick", False),
        ("Telegram news", False),
        ("گروه تست", False),
        ("کانال خبری", False),
        ("", False),
        (None, False),
    ],
)
def test_is_system_name(name, expected):
    assert _is_system_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 3) _extract_item_title (FIX #4 — بدون peer_id)
# ═══════════════════════════════════════════════════════════
def test_extract_item_title_peer_title(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_item_title(item)) == "گروه تست"


def test_extract_item_title_data_name(collector):
    item = _mock_item(attrs={"data-name": "کانال خبری"})
    assert asyncio.run(collector._extract_item_title(item)) == "کانال خبری"


def test_extract_item_title_multiline(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="عنوان\nزیرعنوان")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_item_title(item)) == "عنوان"


def test_extract_item_title_no_peer_id_fallback(collector):
    """🎯 FIX #4: اگر نام نبود، "" برمی‌گردد، نه peer_id."""
    item = _mock_item(attrs={"data-peer-id": "123456"})
    assert asyncio.run(collector._extract_item_title(item)) == ""


def test_extract_item_title_empty(collector):
    assert asyncio.run(collector._extract_item_title(_mock_item())) == ""


# ═══════════════════════════════════════════════════════════
# 4) _extract_sender_from_bubble
# ═══════════════════════════════════════════════════════════
def test_extract_sender_peer_id(collector):
    msg = _mock_item(attrs={"data-peer-id": "777"})
    sid, _ = asyncio.run(collector._extract_sender_from_bubble(msg))
    assert sid == "777"


def test_extract_sender_name(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="علی رضایی")
    msg = _mock_item(children={".peer-title": el})
    _, sname = asyncio.run(collector._extract_sender_from_bubble(msg))
    assert sname == "علی رضایی"


def test_extract_sender_empty(collector):
    sid, sname = asyncio.run(
        collector._extract_sender_from_bubble(_mock_item())
    )
    assert sid == "" and sname == ""


def test_extract_sender_long_name_ignored(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="x" * 100)
    msg = _mock_item(children={".peer-title": el})
    _, sname = asyncio.run(collector._extract_sender_from_bubble(msg))
    assert sname == ""


# ═══════════════════════════════════════════════════════════
# 5) _detect_chat_type
# ═══════════════════════════════════════════════════════════
def test_detect_chat_type_channel_url(collector):
    """#@username → private (چون از URL نمی‌شود تشخیص داد)."""
    collector._page = _mock_page(url="https://web.telegram.org/k/#@mychannel")
    collector._page.query_selector = AsyncMock(return_value=None)
    assert asyncio.run(collector._detect_chat_type()) == "private"


def test_detect_chat_type_channel_explicit(collector):
    """?channel= → channel."""
    collector._page = _mock_page(url="https://web.telegram.org/k/?channel=123")
    collector._page.query_selector = AsyncMock(return_value=None)
    assert asyncio.run(collector._detect_chat_type()) == "channel"


def test_detect_chat_type_group_url(collector):
    collector._page = _mock_page(url="https://web.telegram.org/k/?group=123")
    collector._page.query_selector = AsyncMock(return_value=None)
    assert asyncio.run(collector._detect_chat_type()) == "group"


def test_detect_chat_type_no_page(collector):
    collector._page = None
    # نباید crash کند
    assert asyncio.run(collector._detect_chat_type()) == "private"


# ═══════════════════════════════════════════════════════════
# 6) _extract_chat_meta
# ═══════════════════════════════════════════════════════════
def test_extract_chat_meta_username(collector):
    collector._page = _mock_page(
        url="https://web.telegram.org/k/#@mychannel"
    )
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname == "mychannel"
    assert link == "https://t.me/mychannel"


def test_extract_chat_meta_invite(collector):
    collector._page = _mock_page(
        url="https://web.telegram.org/k/#+AbCdEf"
    )
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None
    assert link == "https://t.me/+AbCdEf"


def test_extract_chat_meta_none(collector):
    collector._page = _mock_page(url="https://web.telegram.org/k/")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None and link is None


def test_extract_chat_meta_no_page(collector):
    collector._page = None
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None and link is None


# ═══════════════════════════════════════════════════════════
# 7) _is_logged_in
# ═══════════════════════════════════════════════════════════
def test_is_logged_in_marker(collector):
    el = MagicMock()
    el.is_visible = AsyncMock(return_value=True)
    collector._page = _mock_page(selectors={".chatlist": el})
    assert asyncio.run(collector._is_logged_in()) is True


def test_is_logged_in_no_markers(collector):
    collector._page = _mock_page()
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._is_logged_in()) is False


# ═══════════════════════════════════════════════════════════
# 8) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "telegram"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 999.0


def test_supports_history(collector):
    assert collector.supports_history is True


def test_supports_websocket(collector):
    assert collector.supports_websocket is False


# ═══════════════════════════════════════════════════════════
# 9) State
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
# 10) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_page(collector):
    collector._connected = True
    collector._page = None
    assert asyncio.run(collector.fetch_messages()) == []


# ═══════════════════════════════════════════════════════════
# 11) _scrape_current_chat_locked (the heart)
# ═══════════════════════════════════════════════════════════
def test_scrape_current_chat_extracts(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubbles = [
        _bubble("111", "قیمت دلار امروز ۸۰ هزار", now_ms),
        _bubble("112", "12:30", now_ms - 1000),  # invalid
    ]
    collector._page = _mock_page(items=bubbles)
    collector._page.url = "https://web.telegram.org/k/#@group"
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current_chat_locked(
        chat_name="گروه تست", peer_id="100",
    ))

    assert count == 1
    assert len(collector._messages_queue) == 1
    msg = collector._messages_queue[0]
    assert msg["platform"] == "telegram"
    assert msg["chat_name"] == "گروه تست"
    assert msg["text"].startswith("قیمت دلار")


def test_scrape_current_chat_filters_old(collector):
    old_ms = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)
    bubble = _bubble("200", "پیام قدیمی", old_ms)
    collector._page = _mock_page(items=[bubble])
    collector._page.url = "https://web.telegram.org/k/#@group"
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    since = datetime.now() - timedelta(days=7)
    count = asyncio.run(collector._scrape_current_chat_locked(
        chat_name="چت", peer_id="100", effective_since=since,
    ))
    assert count == 0


def test_scrape_current_chat_uses_page_lock(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    collector._page = _mock_page(items=[_bubble("1", "hi", now_ms)])
    collector._page.url = "https://web.telegram.org/k/#@group"
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    async def _run():
        assert not collector._page_lock.locked()
        await collector._scrape_current_chat(
            chat_name="x", peer_id="100",
        )
        assert not collector._page_lock.locked()

    asyncio.run(_run())


def test_scrape_current_chat_no_messages(collector):
    collector._page = _mock_page(items=[])
    collector._kw = MagicMock()
    _reset_stats(collector)
    count = asyncio.run(collector._scrape_current_chat_locked(
        chat_name="x", peer_id="y",
    ))
    assert count == 0


# ═══════════════════════════════════════════════════════════
# 12) check_health
# ═══════════════════════════════════════════════════════════
def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True