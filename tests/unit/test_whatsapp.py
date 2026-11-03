# tests/unit/test_whatsapp.py
"""
Unit tests for collectors/whatsapp.py — v1.0 (from scratch).

Coverage:
  ─── Pure functions ───
  - _normalize_media_type (image/* → photo, video/* → video, ...)
  - _is_valid_chat_name

  ─── Static helpers ───
  - _parse_pre_plain_text (فرمت "[12:34, 01/02/2024] علی رضایی: ")

  ─── DOM extractors (mocked) ───
  - _extract_item_title (span[title] / aria-label / empty)
  - _extract_sender_from_bubble (data-pre-plain-text / copyable-text / span)
  - _get_open_chat_title (header selectors / You filter / click here filter)

  ─── Login detection ───
  - _is_logged_in (QR / chat-list / app / none)

  ─── Properties ───
  - platform_name / poll_interval / supports_history / supports_websocket

  ─── State ───
  - _load_seen / _save_seen

  ─── Backfill ───
  - respects _needs_backfill
  - respects _backfill_scrolls=0

  ─── _scrape_open_chat (the heart) ───
  - extracts messages
  - filters empty text
  - deduplicates within chat
  - deduplicates across chats (seen_ids)
  - increments stats once per msg
  - handles empty bubbles list
  - handles no page

  ─── fetch_messages (safety) ───
  - not connected
  - no page

Run:
  pytest tests/unit/test_whatsapp.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.whatsapp import (
    WhatsAppCollector,
    _normalize_media_type,
    _DEFAULT_CHAT_NAME,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "WHATSAPP_KEYWORDS_ONLY",
    "KEYWORDS_ONLY",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("KEYWORDS_ONLY", "false")
    return WhatsAppCollector(
        headless=True,
        poll_seconds=999,
        max_chats=5,
        messages_per_chat=10,
    )


def _mock_item(attrs=None, children=None, inner_text=""):
    """Mock DOM element (chat item / bubble)."""
    attrs = attrs or {}
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


def _mock_page(url="https://web.whatsapp.com/", selectors=None, items=None):
    """Mock Playwright page."""
    selectors = selectors or {}
    page = MagicMock()
    page.url = url
    page.query_selector = AsyncMock(side_effect=lambda sel: selectors.get(sel))
    page.query_selector_all = AsyncMock(return_value=items or [])
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.wait_for_selector = AsyncMock()
    page.title = AsyncMock(return_value="WhatsApp")
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.mouse.move = AsyncMock()
    page.evaluate = AsyncMock(return_value=False)
    page.close = AsyncMock()
    page.locator = MagicMock()
    return page


def _bubble(data_id, text, pre_plain=None, class_name="message-in"):
    """Mock a single WhatsApp message bubble."""
    m = MagicMock()

    async def _ga(name):
        if name == "data-id":
            return data_id
        if name == "data-pre-plain-text":
            return pre_plain
        if name == "class":
            return class_name
        return None

    m.get_attribute = _ga
    m.inner_text = AsyncMock(return_value=text)

    async def _qs(sel):
        if sel == '[data-testid="selectable-text"]':
            el = MagicMock()
            el.inner_text = AsyncMock(return_value=text)
            return el
        if sel == ".copyable-text[data-pre-plain-text]":
            if pre_plain:
                el = MagicMock()
                el.get_attribute = AsyncMock(
                    return_value=pre_plain
                )
                return el
            return None
        if sel in ('span[dir="auto"]', "span[aria-label]"):
            el = MagicMock()
            el.inner_text = AsyncMock(return_value=text)
            return el
        return None

    m.query_selector = _qs
    return m


def _reset_stats(collector):
    """Reset _last_scrape_stats."""
    collector._last_scrape_stats = {
        "total_chats": 0,
        "processed": 0,
        "messages_found": 0,
        "errors": 0,
    }


# ═══════════════════════════════════════════════════════════
# 1) _normalize_media_type
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "mt,expected",
    [
        ("image/png", "photo"),
        ("image/jpeg", "photo"),
        ("image/jpg", "photo"),
        ("image/webp", "photo"),
        ("image/gif", "photo"),
        ("image/bmp", "photo"),
        ("image/svg+xml", "photo"),
        ("video/mp4", "video"),
        ("video/webm", "video"),
        ("audio/ogg", "voice"),
        ("audio/opus", "voice"),
        ("audio/mpeg", "audio"),
        ("audio/mp3", "audio"),
        ("application/pdf", "document"),
        ("pdf", "document"),
        ("application/zip", "document"),
        ("application/octet-stream", "document"),
        ("application/msword", "document"),
        ("text", "text"),
        ("photo", "photo"),
        ("video", "video"),
        ("document", "document"),
        ("sticker", "sticker"),
        (None, "text"),
        ("", "text"),
        ("garbage", "text"),
        ("unknown/mime", "text"),
    ],
)
def test_normalize_media_type(mt, expected):
    assert _normalize_media_type(mt) == expected


# ═══════════════════════════════════════════════════════════
# 2) _is_valid_chat_name
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
        (_DEFAULT_CHAT_NAME, False),
    ],
)
def test_is_valid_chat_name(name, expected):
    assert WhatsAppCollector._is_valid_chat_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 3) _parse_pre_plain_text
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "pre,expected",
    [
        (
            "[12:34, 01/02/2024] علی رضایی: ",
            "علی رضایی",
        ),
        (
            "[12:34, 01/02/2024] John Doe: ",
            "John Doe",
        ),
        (
            "[12:34, 01/02/2024] \u200eعلی\u200f رضایی\u200e: ",
            "علی رضایی",
        ),
        ("", ""),
        (None, ""),
        ("no brackets", ""),
        ("[12:34, 01/02/2024]", ""),
        (
            "[12:34, 01/02/2024] " + "x" * 100 + ": ",
            "",
        ),
    ],
)
def test_parse_pre_plain_text(pre, expected):
    assert WhatsAppCollector._parse_pre_plain_text(pre) == expected


# ═══════════════════════════════════════════════════════════
# 4) _extract_item_title
# ═══════════════════════════════════════════════════════════
def test_extract_item_title_span_title(collector):
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value="گروه تست")
    el.inner_text = AsyncMock(return_value="")
    item = _mock_item(children={"span[title]": el})
    assert asyncio.run(collector._extract_item_title(item)) == "گروه تست"


def test_extract_item_title_aria_label(collector):
    item = _mock_item(attrs={"aria-label": "چت شخصی"})
    assert asyncio.run(collector._extract_item_title(item)) == "چت شخصی"


def test_extract_item_title_empty(collector):
    assert asyncio.run(collector._extract_item_title(_mock_item())) == ""


# ═══════════════════════════════════════════════════════════
# 5) _extract_sender_from_bubble
# ═══════════════════════════════════════════════════════════
def test_extract_sender_from_pre_plain(collector):
    b = _bubble(
        "false_123@g.us_abc_456",
        "hello",
        pre_plain="[12:34, 01/02/2024] علی رضایی: ",
    )
    name = asyncio.run(collector._extract_sender_from_bubble(b))
    assert name == "علی رضایی"


def test_extract_sender_from_copyable_text(collector):
    b = MagicMock()
    b.get_attribute = AsyncMock(return_value=None)

    async def _qs(sel):
        if sel == ".copyable-text[data-pre-plain-text]":
            el = MagicMock()
            el.get_attribute = AsyncMock(
                return_value="[12:34, 01/02/2024] Reza: "
            )
            return el
        if sel in ('span[dir="auto"]', "span[aria-label]"):
            return None
        return None

    b.query_selector = _qs
    name = asyncio.run(collector._extract_sender_from_bubble(b))
    assert name == "Reza"


def test_extract_sender_empty(collector):
    b = MagicMock()
    b.get_attribute = AsyncMock(return_value=None)
    b.query_selector = AsyncMock(return_value=None)
    name = asyncio.run(collector._extract_sender_from_bubble(b))
    assert name == ""


# ═══════════════════════════════════════════════════════════
# 6) _get_open_chat_title
# ═══════════════════════════════════════════════════════════
def test_get_open_chat_title_from_header(collector):
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.inner_text = AsyncMock(return_value="گروه تست")
    page = _mock_page()
    page.query_selector = AsyncMock(return_value=el)
    collector._page = page
    title = asyncio.run(collector._get_open_chat_title())
    assert title == "گروه تست"


def test_get_open_chat_title_filters_you(collector):
    """اگر selector 'You' برگرداند، آن را رد می‌کند."""
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.inner_text = AsyncMock(return_value="You")
    page = _mock_page()
    page.query_selector = AsyncMock(return_value=el)
    collector._page = page
    title = asyncio.run(collector._get_open_chat_title())
    assert title == _DEFAULT_CHAT_NAME


def test_get_open_chat_title_filters_click_here(collector):
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.inner_text = AsyncMock(return_value="Click here for info")
    page = _mock_page()
    page.query_selector = AsyncMock(return_value=el)
    collector._page = page
    title = asyncio.run(collector._get_open_chat_title())
    assert title == _DEFAULT_CHAT_NAME


def test_get_open_chat_title_no_page(collector):
    collector._page = None
    title = asyncio.run(collector._get_open_chat_title())
    assert title == _DEFAULT_CHAT_NAME


# ═══════════════════════════════════════════════════════════
# 7) _is_logged_in
# ═══════════════════════════════════════════════════════════
def test_is_logged_in_qr(collector):
    """اگر QR پیدا شود → False."""
    qr = MagicMock()
    qr.is_visible = AsyncMock(return_value=True)
    page = _mock_page()
    page.query_selector = AsyncMock(side_effect=lambda sel: (
        qr if "canvas" in sel or "qrcode" in sel or "data-ref" in sel
        else None
    ))
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_chatlist(collector):
    """اگر chat-list پیدا شود → True."""
    el = MagicMock()
    el.is_visible = AsyncMock(return_value=True)
    page = _mock_page()
    page.query_selector = AsyncMock(side_effect=lambda sel: (
        el if "pane-side" in sel or "chat-list" in sel
        else None
    ))
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is True


def test_is_logged_in_app_fallback(collector):
    """اگر QR نبود ولی div[role=application] بود → True."""
    app = MagicMock()
    app.is_visible = AsyncMock(return_value=True)
    page = _mock_page()
    page.query_selector = AsyncMock(side_effect=lambda sel: (
        app if 'role="application"' in sel
        else None
    ))
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is True


def test_is_logged_in_nothing(collector):
    page = _mock_page()
    page.query_selector = AsyncMock(return_value=None)
    collector._page = page
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._is_logged_in()) is False


# ═══════════════════════════════════════════════════════════
# 8) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "whatsapp"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 999.0


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
# 9) Seen
# ═══════════════════════════════════════════════════════════
def test_load_seen_empty(collector):
    assert collector._seen_ids == set()


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
# 10) Backfill
# ═══════════════════════════════════════════════════════════
def test_backfill_no_flag_does_nothing(collector):
    collector._needs_backfill = False
    collector._backfill_scrolls = 3
    collector._page = _mock_page()
    asyncio.run(collector._backfill_scroll())
    collector._page.mouse.move.assert_not_called()


def test_backfill_no_scrolls_does_nothing(collector):
    collector._needs_backfill = True
    collector._backfill_scrolls = 0
    collector._page = _mock_page()
    asyncio.run(collector._backfill_scroll())
    collector._page.mouse.move.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 11) _scrape_open_chat — the heart
# ═══════════════════════════════════════════════════════════
def test_scrape_open_chat_extracts_messages(collector):
    bubbles = [
        _bubble(
            "false_123_abc_456",
            "قیمت دلار امروز ۸۰ هزار",
            pre_plain="[12:34, 01/02/2024] علی: ",
        ),
    ]
    page = _mock_page(items=bubbles)
    collector._page = page
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    collector._keywords_only = False
    _reset_stats(collector)

    async def _run():
        # Mock header title
        el = MagicMock()
        el.get_attribute = AsyncMock(return_value=None)
        el.inner_text = AsyncMock(return_value="گروه تست")
        page.query_selector = AsyncMock(return_value=el)
        await collector._scrape_open_chat()

    asyncio.run(_run())

    assert len(collector._messages_queue) == 1
    msg = collector._messages_queue[0]
    assert msg["platform"] == "whatsapp"
    assert msg["chat_name"] == "گروه تست"
    assert msg["text"].startswith("قیمت دلار")


def test_scrape_open_chat_filters_empty_text(collector):
    bubbles = [_bubble("false_123_abc_456", "   ")]
    page = _mock_page(items=bubbles)
    collector._page = page
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    collector._keywords_only = False
    _reset_stats(collector)

    async def _run():
        el = MagicMock()
        el.get_attribute = AsyncMock(return_value=None)
        el.inner_text = AsyncMock(return_value="چت")
        page.query_selector = AsyncMock(return_value=el)
        await collector._scrape_open_chat()

    asyncio.run(_run())
    assert len(collector._messages_queue) == 0


def test_scrape_open_chat_dedup(collector):
    bubbles = [
        _bubble("false_123_abc_456", "same text"),
        _bubble("false_123_abc_456", "same text"),
    ]
    page = _mock_page(items=bubbles)
    collector._page = page
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    collector._keywords_only = False
    _reset_stats(collector)

    async def _run():
        el = MagicMock()
        el.get_attribute = AsyncMock(return_value=None)
        el.inner_text = AsyncMock(return_value="چت")
        page.query_selector = AsyncMock(return_value=el)
        await collector._scrape_open_chat()

    asyncio.run(_run())
    assert len(collector._messages_queue) == 1


def test_scrape_open_chat_no_bubbles(collector):
    page = _mock_page(items=[])
    collector._page = page
    collector._kw = MagicMock()
    _reset_stats(collector)

    async def _run():
        el = MagicMock()
        el.get_attribute = AsyncMock(return_value=None)
        el.inner_text = AsyncMock(return_value="چت")
        page.query_selector = AsyncMock(return_value=el)
        await collector._scrape_open_chat()

    asyncio.run(_run())
    assert len(collector._messages_queue) == 0


def test_scrape_open_chat_no_page(collector):
    collector._page = None
    collector._kw = MagicMock()
    _reset_stats(collector)
    asyncio.run(collector._scrape_open_chat())
    assert len(collector._messages_queue) == 0


def test_scrape_open_chat_keywords_only(collector):
    bubbles = [
        _bubble("false_123_abc_456", "no keyword message"),
    ]
    page = _mock_page(items=bubbles)
    collector._page = page
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=False)
    collector._keywords_only = True
    _reset_stats(collector)

    async def _run():
        el = MagicMock()
        el.get_attribute = AsyncMock(return_value=None)
        el.inner_text = AsyncMock(return_value="چت")
        page.query_selector = AsyncMock(return_value=el)
        await collector._scrape_open_chat()

    asyncio.run(_run())
    assert len(collector._messages_queue) == 0


# ═══════════════════════════════════════════════════════════
# 12) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_page(collector):
    collector._connected = True
    collector._page = None
    assert asyncio.run(collector.fetch_messages()) == []


# ═══════════════════════════════════════════════════════════
# 13) check_health
# ═══════════════════════════════════════════════════════════
def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True


# ═══════════════════════════════════════════════════════════
# 14) dump_dom (stub)
# ═══════════════════════════════════════════════════════════
def test_dump_dom_no_page(collector):
    collector._page = None
    result = asyncio.run(collector.dump_dom())
    assert result == ""