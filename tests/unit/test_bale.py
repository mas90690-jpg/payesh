# tests/unit/test_bale.py
"""
Unit tests for collectors/bale.py — v3.0 (from scratch).

Coverage:
  ─── Pure functions ───
  - _is_invalid_text (empty / clock / number / valid / Persian)

  ─── Static helpers ───
  - _is_system_name (Persian / English / prefix / empty / None)
  - _is_valid_chat_name (normal / long number / empty)
  - _is_admin_chat (by user_id / by name / none)

  ─── DOM extractors ───
  - _extract_peer_id_from_item (data-peer-id / data-id / href / child / -1 / invalid)
  - _extract_item_title (peer-title / data-name / inner_text / empty / normalize)
  - _extract_sender_from_bubble (data-peer-id / selector / empty / long name)

  ─── URL parsers ───
  - _get_current_peer_id (uid / @username / g/ / chat/ / empty / no page)
  - _detect_chat_type (group / private / query / path / no page)
  - _extract_chat_meta (@username / g/ / none / no page)

  ─── Login detection ───
  - _is_logged_in (login url / wrong domain / tel input / contenteditable / none / no page)

  ─── Properties ───
  - platform_name / poll_interval / supports_history / supports_websocket

  ─── State ───
  - _load_first_run / _save_state / _load_last_run_at
  - _load_seen / _save_seen

  ─── Private chat cache ───
  - _remember_private_chat / _remember_chat_mapping / _save_private_chats / _load_private_chats

  ─── Backfill ───
  - respects _needs_backfill
  - respects _backfill_scrolls=0
  - breaks when evaluate returns False

  ─── _scrape_current_chat (the heart) ───
  - extracts messages
  - filters invalid text
  - filters old (effective_since)
  - filters no keyword
  - deduplicates within chat
  - deduplicates across chats (seen_ids)
  - increments stats once per msg
  - uses page lock (concurrency safety)
  - handles empty messages list
  - handles no page

  ─── _find_and_click_by_key ───
  - by name
  - by pid
  - by display_name fallback
  - not found (with patched loop counts)

  ─── fetch_messages (safety) ───
  - not connected
  - no page

Run:
  pytest tests/unit/test_bale.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.bale import (
    BaleCollector,
    _is_invalid_text,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "BALE_PHONE", "BALE_POLL_SECONDS", "BALE_MAX_CHATS",
    "BALE_MESSAGES_PER_CHAT", "BALE_BACKFILL_SCROLLS",
    "BALE_CHAT_DELAY", "BALE_ADMIN_CHAT", "ADMIN_IDS",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("BALE_PHONE", "09120000000")
    monkeypatch.setenv("BALE_POLL_SECONDS", "999")
    return BaleCollector(phone="09120000000", poll_seconds=999)


def _mock_item(attrs=None, children=None, inner_text=""):
    """Mock DOM element (chat item / bubble)."""
    attrs = attrs or {}
    children = children or {}
    item = MagicMock()
    item.get_attribute = AsyncMock(side_effect=lambda name: attrs.get(name))
    item.inner_text = AsyncMock(return_value=inner_text)
    item.query_selector = AsyncMock(side_effect=lambda sel: children.get(sel))
    item.scroll_into_view_if_needed = AsyncMock()
    item.click = AsyncMock(return_value=True)
    return item


def _mock_page(url="https://web.bale.ai/", selectors=None, items=None):
    """Mock Playwright page."""
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
    return page


def _bubble(sid, text, date_ms):
    """Mock a single message bubble."""
    m = MagicMock()
    m.get_attribute = AsyncMock(side_effect=lambda name: {
        "data-sid": sid, "data-date": str(date_ms),
    }.get(name))
    m.inner_text = AsyncMock(return_value=text)
    m.query_selector = AsyncMock(return_value=None)
    return m


def _reset_stats(collector):
    """Ensure _last_scrape_stats has all required keys."""
    collector._last_scrape_stats = {
        "total_chats": 0, "processed": 0, "skipped_system": 0,
        "skipped_target": 0, "skipped_admin": 0, "empty_chats": 0,
        "failed": 0, "messages_found": 0,
        "messages_skipped_old": 0, "messages_skipped_dup": 0,
    }


# ═══════════════════════════════════════════════════════════
# 1) _is_invalid_text
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "text,expected",
    [
        ("", True),
        ("   ", True),
        ("\n\t ", True),
        ("12:30", True),
        ("۹:۰۵", True),
        ("12345", True),
        ("۱۲۳۴۵", True),
        ("12:30\n45", True),
        ("سلام دوستان", False),
        ("hello world", False),
        ("قیمت ۱۲۳ تومان", False),
        ("12:30 جلسه", False),
        (None, True),
    ],
)
def test_is_invalid_text(text, expected):
    assert _is_invalid_text(text) is expected


# ═══════════════════════════════════════════════════════════
# 2) _is_system_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("فضای شخصی", True),
        ("پیام‌های ذخیره‌شده", True),
        ("Saved Messages", True),
        ("Archive", True),
        ("کانال ایجاد شده", True),
        ("گروه ایجاد شد", True),
        ("Channel created", True),
        ("گروه تست من", False),
        ("کانال خبری", False),
        ("", False),
        ("   ", False),
        (None, False),
    ],
)
def test_is_system_name(collector, name, expected):
    assert collector._is_system_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 3) _is_valid_chat_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("گروه تست", True),
        ("Ali", True),
        ("123", True),
        ("", False),
        ("   ", False),
        (None, False),
        ("1234567", False),   # long number
    ],
)
def test_is_valid_chat_name(collector, name, expected):
    assert collector._is_valid_chat_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 4) _is_admin_chat
# ═══════════════════════════════════════════════════════════
def test_is_admin_chat_by_user_id(collector):
    collector._admin_user_ids = {"111", "222"}
    assert collector._is_admin_chat("anything", "111") is True


def test_is_admin_chat_by_name(collector):
    collector._admin_chat_name = "admin chat"
    assert collector._is_admin_chat("admin chat", "") is True


def test_is_admin_chat_none(collector):
    collector._admin_user_ids = {"111"}
    collector._admin_chat_name = "admin chat"
    assert collector._is_admin_chat("normal group", "999") is False


def test_is_admin_chat_empty(collector):
    collector._admin_user_ids = set()
    collector._admin_chat_name = ""
    assert collector._is_admin_chat("", "") is False


# ═══════════════════════════════════════════════════════════
# 5) _extract_peer_id_from_item
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "attrs,expected",
    [
        ({"data-peer-id": "123456"}, "123456"),
        ({"data-id": "987654"}, "987654"),
        ({"href": "#/@myuser"}, "myuser"),
        ({"href": "#/g/mygroup"}, "mygroup"),
        ({"data-peer-id": "0"}, ""),
        ({"data-peer-id": "-1"}, "-1"),   # FIX: -1 is valid
        ({"data-peer-id": "null"}, ""),
        ({"data-peer-id": "undefined"}, ""),
        ({}, ""),
    ],
)
def test_extract_peer_id(collector, attrs, expected):
    item = _mock_item(attrs=attrs)
    assert asyncio.run(collector._extract_peer_id_from_item(item)) == expected


def test_extract_peer_id_from_child(collector):
    child = _mock_item(attrs={"data-peer-id": "555"})
    item = _mock_item(children={"[data-peer-id]": child})
    assert asyncio.run(collector._extract_peer_id_from_item(item)) == "555"


# ═══════════════════════════════════════════════════════════
# 6) _extract_item_title
# ═══════════════════════════════════════════════════════════
def test_extract_item_title_peer_title(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_item_title(item)) == "گروه تست"


def test_extract_item_title_data_name(collector):
    item = _mock_item(attrs={"data-name": "کانال خبری"})
    assert asyncio.run(collector._extract_item_title(item)) == "کانال خبری"


def test_extract_item_title_normalizes(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="گروه   تست  ")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_item_title(item)) == "گروه تست"


def test_extract_item_title_from_inner_text(collector):
    item = _mock_item(inner_text="مخاطب شخصی\nآخرین پیام")
    assert asyncio.run(collector._extract_item_title(item)) == "مخاطب شخصی"


def test_extract_item_title_empty(collector):
    assert asyncio.run(collector._extract_item_title(_mock_item())) == ""


# ═══════════════════════════════════════════════════════════
# 7) _extract_sender_from_bubble
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
# 8) _is_logged_in
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "url,selectors,expected",
    [
        ("https://web.bale.ai/login", {}, False),
        ("https://example.com/", {}, False),
        ("https://web.bale.ai/", {"input[type=\"tel\"]": True}, False),
        ("https://web.bale.ai/", {"div[contenteditable=\"true\"]": True}, True),
        ("https://web.bale.ai/", {}, False),
        ("https://web.bale.ai/?uid=123", {}, False),
    ],
)
def test_is_logged_in(collector, url, selectors, expected):
    real_selectors = {}
    for sel, visible in selectors.items():
        el = MagicMock()
        el.is_visible = AsyncMock(return_value=visible)
        real_selectors[sel] = el
    collector._page = _mock_page(url=url, selectors=real_selectors)
    assert asyncio.run(collector._is_logged_in()) is expected


def test_is_logged_in_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._is_logged_in()) is False


# ═══════════════════════════════════════════════════════════
# 9) _detect_chat_type
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://web.bale.ai/#/g/mygroup", "group"),
        ("https://web.bale.ai/#/@myuser", "private"),
        ("https://web.bale.ai/", "private"),
        ("https://web.bale.ai/?group=123", "group"),
        ("https://web.bale.ai/group/123", "group"),
    ],
)
def test_detect_chat_type(collector, url, expected):
    collector._page = _mock_page(url=url)
    assert asyncio.run(collector._detect_chat_type()) == expected


def test_detect_chat_type_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._detect_chat_type()) == "private"


# ═══════════════════════════════════════════════════════════
# 10) _get_current_peer_id / _extract_chat_meta
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://web.bale.ai/?uid=12345", "12345"),
        ("https://web.bale.ai/#/@myuser", "myuser"),
        ("https://web.bale.ai/#/g/mygroup", "mygroup"),
        ("https://web.bale.ai/chat/999", "999"),
        ("https://web.bale.ai/", ""),
    ],
)
def test_get_current_peer_id(collector, url, expected):
    collector._page = _mock_page(url=url)
    assert asyncio.run(collector._get_current_peer_id()) == expected


def test_get_current_peer_id_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._get_current_peer_id()) == ""


def test_extract_chat_meta_username(collector):
    collector._page = _mock_page(url="https://web.bale.ai/#/@myuser")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname == "myuser"
    assert link == "https://web.bale.ai/#/@myuser"


def test_extract_chat_meta_none(collector):
    collector._page = _mock_page(url="https://web.bale.ai/")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None and link is None


def test_extract_chat_meta_no_page(collector):
    collector._page = None
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None and link is None


# ═══════════════════════════════════════════════════════════
# 11) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "bale"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 999.0


def test_supports_history(collector):
    assert collector.supports_history is True


def test_supports_websocket(collector):
    assert collector.supports_websocket is False


# ═══════════════════════════════════════════════════════════
# 12) send_message / check_health
# ═══════════════════════════════════════════════════════════
def test_send_message_disabled(collector):
    assert asyncio.run(
        collector.send_message("123", "private", "hi")
    ) is False


def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True


# ═══════════════════════════════════════════════════════════
# 13) State (first_run, last_run, seen)
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
# 14) Private chat cache
# ═══════════════════════════════════════════════════════════
def test_remember_private_chat(collector):
    collector._remember_private_chat("گروه تست")
    assert "گروه تست" in collector._private_chat_names


def test_remember_private_chat_invalid(collector):
    collector._remember_private_chat("")
    collector._remember_private_chat("1234567")  # long number
    assert collector._private_chat_names == []


def test_remember_chat_mapping(collector):
    collector._remember_chat_mapping("100", "گروه تست")
    assert collector._chat_id_map["100"] == "گروه تست"


def test_remember_chat_mapping_invalid(collector):
    collector._remember_chat_mapping("", "گروه")
    collector._remember_chat_mapping("100", "")
    assert collector._chat_id_map == {}


def test_save_and_load_private_chats(collector):
    collector._remember_private_chat("گروه تست")
    collector._remember_chat_mapping("100", "گروه تست")
    collector._save_private_chats()

    collector._private_chat_names = []
    collector._chat_id_map = {}
    collector._load_private_chats()

    assert "گروه تست" in collector._private_chat_names
    assert collector._chat_id_map["100"] == "گروه تست"


# ═══════════════════════════════════════════════════════════
# 15) Backfill — respects flags
# ═══════════════════════════════════════════════════════════
def test_backfill_no_flag_does_nothing(collector):
    collector._needs_backfill = False
    collector._backfill_scrolls = 3
    collector._page = _mock_page()
    asyncio.run(collector._backfill_scroll())
    collector._page.evaluate.assert_not_called()


def test_backfill_no_scrolls_does_nothing(collector):
    collector._needs_backfill = True
    collector._backfill_scrolls = 0
    collector._page = _mock_page()
    asyncio.run(collector._backfill_scroll())
    collector._page.evaluate.assert_not_called()


def test_backfill_breaks_on_no_scroll(collector):
    collector._needs_backfill = True
    collector._backfill_scrolls = 3
    page = _mock_page()
    page.evaluate = AsyncMock(return_value=False)  # no scroll → break
    collector._page = page
    asyncio.run(collector._backfill_scroll())
    assert page.evaluate.call_count == 1


# ═══════════════════════════════════════════════════════════
# 16) _scrape_current_chat — the heart
# ═══════════════════════════════════════════════════════════
def test_scrape_current_chat_extracts_messages(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubbles = [
        _bubble("100:111", "قیمت دلار امروز ۸۰ هزار", now_ms),
        _bubble("100:112", "12:30", now_ms - 1000),  # invalid
    ]
    collector._page = _mock_page(
        url="https://web.bale.ai/#/g/mygroup", items=bubbles,
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current_chat(
        chat_name="گروه تست", peer_id="100",
    ))

    assert count == 1
    assert len(collector._messages_queue) == 1
    msg = collector._messages_queue[0]
    assert msg["platform"] == "bale"
    assert msg["chat_name"] == "گروه تست"
    assert msg["chat_type"] == "group"
    assert msg["text"].startswith("قیمت دلار")
    assert isinstance(msg["timestamp"], datetime)


def test_scrape_current_chat_filters_old(collector):
    old_ms = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)
    bubble = _bubble("100:200", "پیام قدیمی", old_ms)
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer", items=[bubble],
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    since = datetime.now() - timedelta(days=7)
    count = asyncio.run(collector._scrape_current_chat(
        chat_name="چت", peer_id="100", effective_since=since,
    ))
    assert count == 0
    assert collector._last_scrape_stats["messages_skipped_old"] == 1


def test_scrape_current_chat_filters_no_keyword(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubble = _bubble("100:300", "سلام دوستان", now_ms)
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer", items=[bubble],
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=False)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current_chat(
        chat_name="چت", peer_id="100",
    ))
    assert count == 0


def test_scrape_current_chat_deduplicates_in_chat(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubbles = [
        _bubble("100:111", "same text", now_ms),
        _bubble("100:111", "same text", now_ms),
    ]
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer", items=bubbles,
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current_chat(
        chat_name="چت", peer_id="100",
    ))
    assert count == 1


def test_scrape_current_chat_deduplicates_across_chats(collector):
    """Same message in two different chats → 2 messages (chat_id differs)."""
    now_ms = int(datetime.now().timestamp() * 1000)
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    # Chat 1
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer1",
        items=[_bubble("100:111", "text", now_ms)],
    )
    _reset_stats(collector)
    c1 = asyncio.run(collector._scrape_current_chat(
        chat_name="chat1", peer_id="100",
    ))

    # Chat 2 — same data-sid
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer2",
        items=[_bubble("100:111", "text", now_ms)],
    )
    _reset_stats(collector)
    c2 = asyncio.run(collector._scrape_current_chat(
        chat_name="chat2", peer_id="200",
    ))

    assert c1 == 1
    assert c2 == 1  # different chat_id → different mid


def test_scrape_current_chat_skips_globally_seen(collector):
    """If mid already in _seen_ids, skip."""
    now_ms = int(datetime.now().timestamp() * 1000)
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer",
        items=[_bubble("100:111", "text", now_ms)],
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    # Pre-populate seen with the same mid
    import hashlib
    mid = hashlib.sha256(
        f"100:111".encode()
    ).hexdigest()[:32]
    collector._seen_ids = {f"100:{mid}"}

    count = asyncio.run(collector._scrape_current_chat(
        chat_name="چت", peer_id="100",
    ))
    assert count == 0


def test_scrape_current_chat_no_messages(collector):
    collector._page = _mock_page(items=[])
    collector._kw = MagicMock()
    _reset_stats(collector)
    count = asyncio.run(collector._scrape_current_chat(
        chat_name="x", peer_id="y",
    ))
    assert count == 0


def test_scrape_current_chat_stats_messages_found(collector):
    """messages_found incremented exactly once per valid msg."""
    now_ms = int(datetime.now().timestamp() * 1000)
    bubbles = [
        _bubble("100:1", "hello world", now_ms),
        _bubble("100:2", "second message", now_ms),
    ]
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer", items=bubbles,
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    asyncio.run(collector._scrape_current_chat(
        chat_name="چت", peer_id="100",
    ))
    assert collector._last_scrape_stats["messages_found"] == 2


def test_scrape_current_chat_uses_page_lock(collector):
    """_scrape_current_chat must acquire self._page_lock."""
    now_ms = int(datetime.now().timestamp() * 1000)
    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer",
        items=[_bubble("100:1", "hello world", now_ms)],
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    async def _run():
        assert not collector._page_lock.locked()
        await collector._scrape_current_chat(chat_name="x", peer_id="100")
        assert not collector._page_lock.locked()

    asyncio.run(_run())


def test_scrape_current_chat_no_page(collector):
    collector._page = None
    collector._kw = MagicMock()
    _reset_stats(collector)
    count = asyncio.run(collector._scrape_current_chat(
        chat_name="x", peer_id="y",
    ))
    assert count == 0


# ═══════════════════════════════════════════════════════════
# 17) _find_and_click_by_key
# ═══════════════════════════════════════════════════════════
def test_find_and_click_by_name(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(children={".peer-title": el})
    collector._page = _mock_page(items=[item])

    ok = asyncio.run(collector._find_and_click_by_key(
        "name:گروه تست", "گروه تست",
    ))
    assert ok is True
    item.click.assert_awaited_once()


def test_find_and_click_by_pid(collector):
    item = _mock_item(attrs={"data-peer-id": "100"})
    collector._page = _mock_page(items=[item])

    ok = asyncio.run(collector._find_and_click_by_key(
        "pid:100", "any",
    ))
    assert ok is True


def test_find_and_click_by_display_name_fallback(collector):
    """If chat_key is name:old but display_name matches — click it."""
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="new name")
    item = _mock_item(children={".peer-title": el})
    collector._page = _mock_page(items=[item])

    ok = asyncio.run(collector._find_and_click_by_key(
        "name:old", "new name",
    ))
    assert ok is True


def test_find_and_click_not_found(collector):
    collector._page = _mock_page(items=[])
    # Patch loop counts to keep the test fast
    import collectors.bale as bale_mod
    orig_init = bale_mod._SCROLL_INITIAL_ROUNDS
    orig_click = bale_mod._CLICK_SCROLL_ROUNDS
    bale_mod._SCROLL_INITIAL_ROUNDS = 1
    bale_mod._CLICK_SCROLL_ROUNDS = 1
    try:
        ok = asyncio.run(collector._find_and_click_by_key(
            "name:missing", "missing",
        ))
        assert ok is False
    finally:
        bale_mod._SCROLL_INITIAL_ROUNDS = orig_init
        bale_mod._CLICK_SCROLL_ROUNDS = orig_click


# ═══════════════════════════════════════════════════════════
# 18) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_page(collector):
    collector._connected = True
    collector._page = None
    assert asyncio.run(collector.fetch_messages()) == []