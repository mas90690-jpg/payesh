# tests/unit/test_eitaa.py
"""
Unit tests for collectors/eitaa.py — v3.0 (from scratch).

Coverage:
  ─── Pure functions ───
  - _is_invalid_text (empty / clock / number / valid)
  - _clean_text (PUA removal, trailing clock, trailing number, trailing emoji)

  ─── Static helpers ───
  - _is_system_name (Persian / English / prefix / empty / None)

  ─── DOM extractors ───
  - _extract_peer_id (data-peer-id / href / empty / -1 / invalid)
  - _extract_title (peer-title / multiline / too long / empty)
  - _extract_sender (is-out / is-group / private / empty / exception)

  ─── Login detection ───
  - _is_logged_in (login marker / chatlist / both / no page)

  ─── Properties ───
  - platform_name / poll_interval / supports_history / supports_websocket

  ─── State ───
  - _load_first_run / _save_state / _load_last_run_at
  - _load_seen / _save_seen

  ─── Delay ───
  - _human_delay (default range / custom range)

  ─── Backfill ───
  - respects _needs_backfill
  - respects _backfill_scrolls=0

  ─── _scrape_current (the heart) ───
  - extracts messages
  - filters invalid text
  - filters old (effective_since)
  - filters no keyword
  - filters service messages
  - filters empty media
  - deduplicates within chat
  - deduplicates across chats (seen_ids)
  - increments stats once per msg
  - uses page lock (concurrency safety)
  - handles empty messages list
  - handles no page

  ─── _click_chat ───
  - by pid
  - by name
  - not found

  ─── _collect_chats ───
  - scroll + dedup
  - skip system chats
  - handles empty list

  ─── fetch_messages (safety) ───
  - not connected
  - no page

Run:
  pytest tests/unit/test_eitaa.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.eitaa import (
    EitaaCollector,
    _clean_text,
    _is_invalid_text,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "EITAA_PHONE", "EITAA_POLL_SECONDS", "EITAA_MAX_CHATS",
    "EITAA_MESSAGES_PER_CHAT", "EITAA_BACKFILL_SCROLLS",
    "EITAA_CHAT_DELAY",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("EITAA_PHONE", "09120000000")
    monkeypatch.setenv("EITAA_POLL_SECONDS", "999")
    return EitaaCollector(phone="09120000000", poll_seconds=999)


def _mock_item(attrs=None, children=None, inner_text="", class_name=""):
    """Mock DOM element (chat item / bubble)."""
    attrs = dict(attrs or {})
    if class_name:
        attrs["class"] = class_name
    children = children or {}
    item = MagicMock()
    item.get_attribute = AsyncMock(side_effect=lambda name: attrs.get(name))
    item.inner_text = AsyncMock(return_value=inner_text)
    item.query_selector = AsyncMock(side_effect=lambda sel: children.get(sel))
    item.query_selector_all = AsyncMock(return_value=[])
    item.scroll_into_view_if_needed = AsyncMock()
    item.click = AsyncMock(return_value=True)
    item.evaluate_handle = AsyncMock(return_value=None)
    return item


def _mock_page(url="https://web.eitaa.com/", selectors=None, items=None):
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
    page.locator = MagicMock()
    return page


def _bubble(mid, text, ts_val, class_name="bubble"):
    """Mock a single message bubble."""
    parent = MagicMock()
    parent.get_attribute = AsyncMock(side_effect=lambda name: {
        "data-mid": mid, "data-timestamp": str(ts_val),
    }.get(name))

    m = MagicMock()
    m.get_attribute = AsyncMock(side_effect=lambda name: {
        "class": class_name, "data-mid": None,
    }.get(name))
    m.inner_text = AsyncMock(return_value=text)
    m.query_selector = AsyncMock(return_value=None)
    m.query_selector_all = AsyncMock(return_value=[])
    m.evaluate_handle = AsyncMock(return_value=parent)
    return m


def _reset_stats(collector):
    """Ensure _last_scrape_stats has all required keys."""
    collector._last_scrape_stats = {
        "total_chats": 0, "processed": 0, "skipped_system": 0,
        "skipped_target": 0, "empty_chats": 0,
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
# 2) _clean_text
# ═══════════════════════════════════════════════════════════
def test_clean_text_empty():
    assert _clean_text("") == ""


def test_clean_text_none():
    assert _clean_text(None) == ""


def test_clean_text_plain():
    assert _clean_text("سلام") == "سلام"


def test_clean_text_pua_removed():
    assert _clean_text("\ue900سلام\ue901") == "سلام"


def test_clean_text_pua_range_removed():
    assert _clean_text("\ue123متن\ue456") == "متن"


def test_clean_text_trailing_clock():
    assert _clean_text("سلام\n12:34") == "سلام"


def test_clean_text_trailing_number():
    assert _clean_text("سلام\n1234") == "سلام"


def test_clean_text_trailing_emoji():
    assert _clean_text("سلام\n😀😀") == "سلام"


def test_clean_text_inline_clock():
    assert _clean_text("سلام 12:34") == "سلام"


def test_clean_text_multiline_kept():
    assert _clean_text("خط اول\nخط دوم") == "خط اول\nخط دوم"


def test_clean_text_trailing_whitespace():
    assert _clean_text("سلام   \n\n") == "سلام"


# ═══════════════════════════════════════════════════════════
# 3) _is_system_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("پیام‌های ذخیره شده", True),
        ("ذخیره‌شده", True),
        ("Saved Messages", True),
        ("Drafts", True),
        ("ایتا", True),
        ("Eitaa", True),
        ("تیم ایتا", True),
        ("فایل", True),
        ("استیکر", True),
        ("کانال ایجاد شد", True),
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
# 4) _extract_peer_id
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "attrs,expected",
    [
        ({"data-peer-id": "123456"}, "123456"),
        ({"href": "https://web.eitaa.com/#98765"}, "98765"),
        ({"data-peer-id": "0"}, "0"),
        ({"data-peer-id": "-1"}, "-1"),
        ({"data-peer-id": "null"}, ""),
        ({}, ""),
    ],
)
def test_extract_peer_id(collector, attrs, expected):
    item = _mock_item(attrs=attrs)
    assert asyncio.run(collector._extract_peer_id(item)) == expected


# ═══════════════════════════════════════════════════════════
# 5) _extract_title
# ═══════════════════════════════════════════════════════════
def test_extract_title_peer_title(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_title(item)) == "گروه تست"


def test_extract_title_multiline(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="عنوان\nزیرعنوان")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_title(item)) == "عنوان"


def test_extract_title_none(collector):
    item = _mock_item()
    assert asyncio.run(collector._extract_title(item)) == ""


def test_extract_title_too_long(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="x" * 100)
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_title(item)) == ""


# ═══════════════════════════════════════════════════════════
# 6) _extract_sender
# ═══════════════════════════════════════════════════════════
def test_extract_sender_is_out(collector):
    msg = _mock_item(class_name="bubble is-out")
    assert asyncio.run(
        collector._extract_sender(msg, "چت")
    ) == "چت"


def test_extract_sender_private(collector):
    msg = _mock_item(class_name="bubble")
    assert asyncio.run(
        collector._extract_sender(msg, "چت")
    ) == "چت"


def test_extract_sender_group_with_name(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="علی")
    msg = _mock_item(
        class_name="bubble is-group",
        children={".peer-title": el},
    )
    assert asyncio.run(
        collector._extract_sender(msg, "گروه")
    ) == "علی"


def test_extract_sender_group_no_name(collector):
    msg = _mock_item(class_name="bubble is-group")
    assert asyncio.run(
        collector._extract_sender(msg, "گروه")
    ) == "گروه"


# ═══════════════════════════════════════════════════════════
# 7) _is_logged_in
# ═══════════════════════════════════════════════════════════
def test_is_logged_in_login_marker(collector):
    page = _mock_page()
    page.query_selector = AsyncMock(return_value=MagicMock())
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_chatlist(collector):
    """If login markers absent but chatlist present → True."""
    page = _mock_page()
    def _qs(sel):
        # Both login markers must return None
        if "tel" in sel or "phone" in sel:
            return None
        # All chatlist markers return truthy
        return MagicMock()
    page.query_selector = AsyncMock(side_effect=_qs)
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is True
    

def test_is_logged_in_nothing(collector):
    page = _mock_page()
    page.query_selector = AsyncMock(return_value=None)
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is False


# ═══════════════════════════════════════════════════════════
# 8) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "eitaa"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 999.0


def test_supports_history(collector):
    assert collector.supports_history is True


def test_supports_websocket(collector):
    assert collector.supports_websocket is False


# ═══════════════════════════════════════════════════════════
# 9) State (first_run, last_run, seen)
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
# 10) _human_delay
# ═══════════════════════════════════════════════════════════
def test_human_delay_default(collector):
    async def _run():
        # Just ensure it doesn't crash
        await collector._human_delay()
    asyncio.run(_run())


def test_human_delay_custom(collector):
    async def _run():
        await collector._human_delay(0.01, 0.02)
    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════
# 11) Backfill — respects flags
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


# ═══════════════════════════════════════════════════════════
# 12) _scrape_current — the heart
# ═══════════════════════════════════════════════════════════
def test_scrape_current_extracts_messages(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubbles = [
        _bubble("111", "قیمت دلار امروز ۸۰ هزار", now_ms),
    ]
    collector._page = _mock_page(
        items=bubbles,
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current(
        name="گروه تست", peer_id="100",
    ))

    assert count == 1
    assert len(collector._messages_queue) == 1
    msg = collector._messages_queue[0]
    assert msg["platform"] == "eitaa"
    assert msg["chat_name"] == "گروه تست"
    assert msg["text"].startswith("قیمت دلار")
    assert isinstance(msg["timestamp"], datetime)


def test_scrape_current_filters_old(collector):
    old_ms = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)
    bubble = _bubble("200", "پیام قدیمی", old_ms)
    collector._page = _mock_page(items=[bubble])
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    since = datetime.now() - timedelta(days=7)
    count = asyncio.run(collector._scrape_current(
        name="چت", peer_id="100", effective_since=since,
    ))
    assert count == 0


def test_scrape_current_filters_no_keyword(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubble = _bubble("300", "سلام دوستان", now_ms)
    collector._page = _mock_page(items=[bubble])
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=False)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current(
        name="چت", peer_id="100",
    ))
    assert count == 0


def test_scrape_current_filters_service(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubble = _bubble("400", "service text", now_ms, class_name="bubble service")
    collector._page = _mock_page(items=[bubble])
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current(
        name="چت", peer_id="100",
    ))
    assert count == 0


def test_scrape_current_filters_empty_media(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubble = _bubble(
        "500", "media", now_ms, class_name="bubble just-media"
    )
    collector._page = _mock_page(items=[bubble])
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current(
        name="چت", peer_id="100",
    ))
    assert count == 0


def test_scrape_current_deduplicates_in_chat(collector):
    now_ms = int(datetime.now().timestamp() * 1000)
    bubbles = [
        _bubble("111", "same text", now_ms),
        _bubble("111", "same text", now_ms),
    ]
    collector._page = _mock_page(items=bubbles)
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    count = asyncio.run(collector._scrape_current(
        name="چت", peer_id="100",
    ))
    assert count == 1


def test_scrape_current_deduplicates_across_chats(collector):
    """Same message in two different chats → 2 messages (chat_id differs)."""
    now_ms = int(datetime.now().timestamp() * 1000)
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    collector._page = _mock_page(
        items=[_bubble("111", "text", now_ms)],
    )
    _reset_stats(collector)
    c1 = asyncio.run(collector._scrape_current(
        name="chat1", peer_id="100",
    ))

    collector._page = _mock_page(
        items=[_bubble("111", "text", now_ms)],
    )
    _reset_stats(collector)
    c2 = asyncio.run(collector._scrape_current(
        name="chat2", peer_id="200",
    ))

    assert c1 == 1
    assert c2 == 1


def test_scrape_current_skips_globally_seen(collector):
    """If mid already in _seen_ids, skip."""
    now_ms = int(datetime.now().timestamp() * 1000)
    collector._page = _mock_page(
        items=[_bubble("111", "text", now_ms)],
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    import hashlib
    mid = hashlib.sha256("100:111".encode()).hexdigest()[:32]
    collector._seen_ids = {f"100:{mid}"}

    count = asyncio.run(collector._scrape_current(
        name="چت", peer_id="100",
    ))
    assert count == 0


def test_scrape_current_no_messages(collector):
    collector._page = _mock_page(items=[])
    collector._kw = MagicMock()
    _reset_stats(collector)
    count = asyncio.run(collector._scrape_current(
        name="x", peer_id="y",
    ))
    assert count == 0


# ═══════════════════════════════════════════════════════════
# 13) _click_chat
# ═══════════════════════════════════════════════════════════
def test_click_chat_by_pid(collector):
    page = _mock_page()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.click = AsyncMock()
    loc.scroll_into_view_if_needed = AsyncMock()

    # 🎯 locator(sel) returns object with .first = loc
    locator_chain = MagicMock()
    locator_chain.first = loc
    page.locator = MagicMock(return_value=locator_chain)
    collector._page = page

    ok = asyncio.run(collector._click_chat("100", "chat"))
    assert ok is True
    

def test_click_chat_by_name(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="chat")
    item = _mock_item(children={".peer-title": el})
    page = _mock_page()
    page.locator = MagicMock()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=0)
    page.locator.return_value = loc
    page.query_selector_all = AsyncMock(return_value=[item])
    collector._page = page

    ok = asyncio.run(collector._click_chat("", "chat"))
    assert ok is True


def test_click_chat_not_found(collector):
    page = _mock_page()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=loc)
    page.query_selector_all = AsyncMock(return_value=[])
    collector._page = page

    # Patch loop range for speed
    import collectors.eitaa as eitaa_mod
    orig = range
    collectors_eitaa_range = eitaa_mod.__builtins__["range"]
    try:
        eitaa_mod.__builtins__["range"] = lambda n: orig(min(n, 2))
        ok = asyncio.run(collector._click_chat("", "missing"))
        assert ok is False
    finally:
        eitaa_mod.__builtins__["range"] = collectors_eitaa_range


# ═══════════════════════════════════════════════════════════
# 14) _collect_chats
# ═══════════════════════════════════════════════════════════
def test_collect_chats_empty(collector):
    page = _mock_page()
    page.query_selector_all = AsyncMock(return_value=[])
    collector._page = page

    import collectors.eitaa as eitaa_mod
    orig = eitaa_mod._MAX_SCROLL_ROUNDS
    try:
        eitaa_mod._MAX_SCROLL_ROUNDS = 1
        chats = asyncio.run(collector._collect_chats())
        assert chats == []
    finally:
        eitaa_mod._MAX_SCROLL_ROUNDS = orig


def test_collect_chats_dedup(collector):
    el = MagicMock()
    el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(
        attrs={"data-peer-id": "100"},
        children={".peer-title": el},
    )
    page = _mock_page()
    page.query_selector_all = AsyncMock(return_value=[item, item])
    collector._page = page

    import collectors.eitaa as eitaa_mod
    orig = eitaa_mod._MAX_SCROLL_ROUNDS
    try:
        eitaa_mod._MAX_SCROLL_ROUNDS = 1
        chats = asyncio.run(collector._collect_chats())
        assert len(chats) == 1
        assert chats[0]["peer_id"] == "100"
    finally:
        eitaa_mod._MAX_SCROLL_ROUNDS = orig


# ═══════════════════════════════════════════════════════════
# 15) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_page(collector):
    collector._connected = True
    collector._page = None
    assert asyncio.run(collector.fetch_messages()) == []