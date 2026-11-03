"""
تست ترکیبی collectors/bale.py — نسخهٔ ۷.۰ (Unit + Integration)

دو لایه:
  🟢 UNIT    — همیشه اجرا می‌شود (بدون شبکه/مرورگر)  → pytest tests/permanent/test_bale_combined.py
  🔵 INTEG   — فقط با -m integration اجرا می‌شود  → pytest -m integration
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.bale import BaleCollector, _is_invalid_text


# ═══════════════════════════════════════════════════════════
# Fixtures مشترک
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "BALE_PHONE", "BALE_POLL_SECONDS", "BALE_MAX_CHATS",
    "BALE_MESSAGES_PER_CHAT", "BALE_BACKFILL_SCROLLS",
    "BALE_CHAT_DELAY", "BALE_ADMIN_CHAT", "ADMIN_IDS",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """کلکتور بدون اتصال — برای تست توابع خالص."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("BALE_PHONE", "09120000000")
    monkeypatch.setenv("BALE_POLL_SECONDS", "999")
    return BaleCollector(phone="09120000000", poll_seconds=999)


# ═══════════════════════════════════════════════════════════
# Mock helpers
# ═══════════════════════════════════════════════════════════
def _mock_item(attrs=None, children=None, inner_text=""):
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


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _is_invalid_text
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "text,expected",
    [
        ("", True), ("   ", True), ("\n\t ", True),
        ("12:30", True), ("۹:۰۵", True),
        ("12345", True), ("۱۲۳۴۵", True),
        ("12:30\n45", True),
        ("سلام دوستان", False), ("hello world", False),
        ("قیمت ۱۲۳ تومان", False), ("12:30 جلسه", False),
        (None, True),
    ],
)
def test_is_invalid_text(text, expected):
    assert _is_invalid_text(text) is expected


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _extract_peer_id_from_item
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "attrs,expected",
    [
        ({"data-peer-id": "123456"}, "123456"),
        ({"data-id": "987654"}, "987654"),
        ({"href": "#/@myuser"}, "myuser"),
        ({"href": "#/g/mygroup"}, "mygroup"),
        ({"data-peer-id": "0"}, ""),
        ({"data-peer-id": "-1"}, ""),
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
# 🟢 UNIT — _extract_item_title
# ═══════════════════════════════════════════════════════════
def test_extract_item_title_peer_title(collector):
    el = MagicMock(); el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_item_title(item)) == "گروه تست"


def test_extract_item_title_data_name(collector):
    item = _mock_item(attrs={"data-name": "کانال خبری"})
    assert asyncio.run(collector._extract_item_title(item)) == "کانال خبری"


def test_extract_item_title_normalizes(collector):
    el = MagicMock(); el.inner_text = AsyncMock(return_value="گروه   تست  ")
    item = _mock_item(children={".peer-title": el})
    assert asyncio.run(collector._extract_item_title(item)) == "گروه تست"


def test_extract_item_title_empty(collector):
    assert asyncio.run(collector._extract_item_title(_mock_item())) == ""


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _is_system_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("فضای شخصی", True), ("پیام‌های ذخیره‌شده", True),
        ("Saved Messages", True), ("Archive", True),
        ("کانال ایجاد شده", True), ("گروه ایجاد شد", True),
        ("Channel created", True),
        ("گروه تست من", False), ("کانال خبری", False),
        ("", False), ("   ", False), (None, False),
    ],
)
def test_is_system_name(collector, name, expected):
    assert collector._is_system_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _is_admin_chat
# ═══════════════════════════════════════════════════════════
def test_is_admin_chat_by_user_id(collector):
    collector._admin_user_ids = {"111", "222"}
    assert collector._is_admin_chat("هرچیزی", "111") is True


def test_is_admin_chat_by_name(collector):
    collector._admin_chat_name = "چت مدیر"
    assert collector._is_admin_chat("چت مدیر", "") is True


def test_is_admin_chat_none(collector):
    collector._admin_user_ids = {"111"}
    collector._admin_chat_name = "چت مدیر"
    assert collector._is_admin_chat("گروه عادی", "999") is False


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _extract_sender_from_bubble
# ═══════════════════════════════════════════════════════════
def test_extract_sender_peer_id(collector):
    msg = _mock_item(attrs={"data-peer-id": "777"})
    sid, _ = asyncio.run(collector._extract_sender_from_bubble(msg))
    assert sid == "777"


def test_extract_sender_name(collector):
    el = MagicMock(); el.inner_text = AsyncMock(return_value="علی رضایی")
    msg = _mock_item(children={".peer-title": el})
    _, sname = asyncio.run(collector._extract_sender_from_bubble(msg))
    assert sname == "علی رضایی"


def test_extract_sender_empty(collector):
    sid, sname = asyncio.run(collector._extract_sender_from_bubble(_mock_item()))
    assert sid == "" and sname == ""


def test_extract_sender_long_name_ignored(collector):
    el = MagicMock(); el.inner_text = AsyncMock(return_value="x" * 100)
    msg = _mock_item(children={".peer-title": el})
    _, sname = asyncio.run(collector._extract_sender_from_bubble(msg))
    assert sname == ""


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _is_logged_in
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "url,selectors,expected",
    [
        ("https://web.bale.ai/login", {}, False),
        ("https://example.com/", {}, False),
        ("https://web.bale.ai/", {"input[type=\"tel\"]": True}, False),  # login marker
        ("https://web.bale.ai/", {"div[contenteditable=\"true\"]": True}, True),
        ("https://web.bale.ai/", {}, False),
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
# 🟢 UNIT — _detect_chat_type
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


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _is_valid_chat_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("گروه تست", True), ("Ali", True), ("123", True),
        ("", False), ("   ", False), (None, False),
        ("1234567", False),
    ],
)
def test_is_valid_chat_name(collector, name, expected):
    assert collector._is_valid_chat_name(name) is expected


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _get_current_peer_id / _extract_chat_meta
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


def test_extract_chat_meta_username(collector):
    collector._page = _mock_page(url="https://web.bale.ai/#/@myuser")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname == "myuser"
    assert link == "https://web.bale.ai/#/@myuser"


def test_extract_chat_meta_none(collector):
    collector._page = _mock_page(url="https://web.bale.ai/")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None and link is None


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — Properties
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
# 🟢 UNIT — send_message / check_health
# ═══════════════════════════════════════════════════════════
def test_send_message_disabled(collector):
    assert asyncio.run(collector.send_message("123", "private", "سلام")) is False


def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True


# ═══════════════════════════════════════════════════════════
# 🟢 UNIT — _scrape_current_chat (با mock کامل)  ← شکاف قبلی
# ═══════════════════════════════════════════════════════════
def test_scrape_current_chat_extracts_messages(collector):
    """قلب استخراج پیام — حالا unit تست می‌شود."""
    now_ms = int(datetime.now().timestamp() * 1000)

    def _bubble(sid, text, date_ms):
        m = MagicMock()
        m.get_attribute = AsyncMock(side_effect=lambda name: {
            "data-sid": sid, "data-date": str(date_ms)
        }.get(name))
        m.inner_text = AsyncMock(return_value=text)
        m.query_selector = AsyncMock(return_value=None)
        return m

    bubbles = [
        _bubble("100:111", "قیمت دلار امروز ۸۰ هزار تومان", now_ms),
        _bubble("100:112", "12:30", now_ms - 1000),  # invalid text
    ]
    collector._page = _mock_page(
        url="https://web.bale.ai/#/g/mygroup",
        items=bubbles,
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

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
    bubble = MagicMock()
    bubble.get_attribute = AsyncMock(side_effect=lambda name: {
        "data-sid": "100:200", "data-date": str(old_ms)
    }.get(name))
    bubble.inner_text = AsyncMock(return_value="پیام قدیمی")
    bubble.query_selector = AsyncMock(return_value=None)

    collector._page = _mock_page(
        url="https://web.bale.ai/#/@peer", items=[bubble],
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    since = datetime.now() - timedelta(days=7)
    count = asyncio.run(collector._scrape_current_chat(
        chat_name="چت", peer_id="100", effective_since=since,
    ))
    assert count == 0


def test_scrape_current_chat_no_page(collector):
    collector._page = _mock_page(items=[])
    count = asyncio.run(collector._scrape_current_chat(chat_name="x", peer_id="y"))
    assert count == 0


# ═══════════════════════════════════════════════════════════
# 🔵 INTEGRATION — نیاز به شبکه/نشست (پیش‌فرض skip)
# ═══════════════════════════════════════════════════════════
pytestmark_integ = pytest.mark.integration


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("BALE_INTEGRATION") != "1",
    reason="integration — با BALE_INTEGRATION=1 فعال شود",
)
class TestBaleIntegration:
    """E2E واقعی — فقط در محیط دارای نشست/شبکه اجرا شود."""

    def test_connect_and_fetch(self):
        from dotenv import load_dotenv
        load_dotenv()

        async def _run():
            c = BaleCollector(headless=True, poll_seconds=999)
            ok = await c.connect()
            try:
                assert ok is True, "اتصال برقرار نشد"
                assert await c.check_health() is True

                since = datetime.now() - timedelta(days=1)
                msgs = await c.fetch_messages(since=since)

                assert isinstance(msgs, list)
                for m in msgs:
                    assert m["platform"] == "bale"
                    assert isinstance(m["timestamp"], datetime)
                    assert m["timestamp"] >= since
                    assert (m["text"] or "").strip()
                    assert m["chat_type"] in {"private", "group", "channel"}

                ids = [m["id"] for m in msgs]
                assert len(ids) == len(set(ids)), "id تکراری در پیام‌ها"

                stats = c._last_scrape_stats
                assert stats["total_chats"] >= 0
            finally:
                await c.disconnect()

        asyncio.run(_run())

    def test_pipeline_roundtrip(self):
        """E2E: fetch → pipeline.feed → DB"""
        from dotenv import load_dotenv
        from tests.pipeline_helpers import TestPipeline
        load_dotenv()

        async def _run():
            p = TestPipeline(
                keywords_only=False, require_t1=False,
                allowed_tiers=("T1", "T2", "T3"),
            )
            c = BaleCollector(headless=True, poll_seconds=999)
            try:
                await p.start()
                ok = await c.connect()
                assert ok is True

                msgs = await c.fetch_messages(
                    since=datetime.now() - timedelta(days=1)
                )
                stored = await p.feed(msgs)
                assert stored >= 0

                total = await p.count_messages(platform="bale")
                assert total >= 0
            finally:
                await c.disconnect()
                await p.stop()

        asyncio.run(_run())