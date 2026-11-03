"""تست‌های واحد collectors/bale.py — بدون نیاز به playwright یا شبکه.

پوشش:
  - _is_invalid_text: خالی / ساعت / عدد / متن معتبر / فارسی
  - _extract_peer_id_from_item: data-peer-id / data-id / href / child / خالی
  - _extract_item_title: peer-title / data-name / inner_text / خالی
  - _is_system_name: فارسی / انگلیسی / prefix / خالی / None
  - _is_admin_chat: admin_user_ids / admin_chat_name / هیچ‌کدام
  - _extract_sender_from_bubble: data-peer-id / selector / خالی
  - _is_logged_in: mock page (login / loggedin / url نامعتبر)
  - _detect_chat_type: URLهای مختلف
  - _is_valid_chat_name: نام معتبر / عددی بلند / خالی
  - _get_current_peer_id: uid / @username / g/ / خالی
  - _extract_chat_meta: @username / g/ / خالی
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.bale import (
    BaleCollector,
    _is_invalid_text,
)


# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════
@pytest.fixture
def collector(tmp_path, monkeypatch):
    """کلکتور بدون اتصال — فقط برای تست توابع خالص."""
    monkeypatch.setenv("BALE_PHONE", "09120000000")
    monkeypatch.setenv("BALE_POLL_SECONDS", "999")
    monkeypatch.setenv("BALE_MAX_CHATS", "0")
    monkeypatch.setenv("BALE_MESSAGES_PER_CHAT", "500")
    monkeypatch.setenv("BALE_BACKFILL_SCROLLS", "10")
    monkeypatch.setenv("BALE_CHAT_DELAY", "0")
    monkeypatch.setenv("BALE_ADMIN_CHAT", "")
    monkeypatch.setenv("ADMIN_IDS", "")

    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)

    c = BaleCollector(phone="09120000000", poll_seconds=999)
    return c


def _mock_item(attrs=None, children=None, inner_text=""):
    """ساخت mock برای یک DOM element (chat item / bubble)."""
    attrs = attrs or {}
    children = children or {}

    item = MagicMock()
    item.get_attribute = AsyncMock(
        side_effect=lambda name: attrs.get(name)
    )
    item.inner_text = AsyncMock(return_value=inner_text)

    def _query_selector(sel):
        return children.get(sel)

    item.query_selector = AsyncMock(side_effect=_query_selector)
    return item


def _mock_page(url="https://web.bale.ai/", selectors=None):
    """ساخت mock برای page."""
    selectors = selectors or {}
    page = MagicMock()
    page.url = url

    async def _query_selector(sel):
        return selectors.get(sel)

    page.query_selector = AsyncMock(side_effect=_query_selector)
    return page


# ═══════════════════════════════════════════════════════════
# _is_invalid_text
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
    ],
)
def test_is_invalid_text(text, expected):
    assert _is_invalid_text(text) is expected


def test_is_invalid_text_none():
    # None نباید کرش کند
    assert _is_invalid_text(None) is True


# ═══════════════════════════════════════════════════════════
# _extract_peer_id_from_item
# ═══════════════════════════════════════════════════════════
def test_extract_peer_id_data_peer_id(collector):
    item = _mock_item(attrs={"data-peer-id": "123456"})
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == "123456"


def test_extract_peer_id_data_id(collector):
    item = _mock_item(attrs={"data-id": "987654"})
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == "987654"


def test_extract_peer_id_from_href(collector):
    item = _mock_item(attrs={"href": "#/@myusername"})
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == "myusername"


def test_extract_peer_id_from_href_group(collector):
    item = _mock_item(attrs={"href": "#/g/groupname"})
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == "groupname"


def test_extract_peer_id_from_child(collector):
    child = _mock_item(attrs={"data-peer-id": "555"})
    item = _mock_item(children={"[data-peer-id]": child})
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == "555"


def test_extract_peer_id_empty(collector):
    item = _mock_item()
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == ""


def test_extract_peer_id_invalid_values(collector):
    # مقادیر 0 و -1 و null نباید برگردند
    item = _mock_item(attrs={"data-peer-id": "0"})
    result = asyncio.run(collector._extract_peer_id_from_item(item))
    assert result == ""


# ═══════════════════════════════════════════════════════════
# _extract_item_title
# ═══════════════════════════════════════════════════════════
def test_extract_item_title_peer_title(collector):
    title_el = MagicMock()
    title_el.inner_text = AsyncMock(return_value="گروه تست")
    item = _mock_item(children={".peer-title": title_el})
    result = asyncio.run(collector._extract_item_title(item))
    assert result == "گروه تست"


def test_extract_item_title_data_name(collector):
    item = _mock_item(attrs={"data-name": "کانال خبری"})
    result = asyncio.run(collector._extract_item_title(item))
    assert result == "کانال خبری"


def test_extract_item_title_inner_text(collector):
    item = _mock_item(inner_text="مخاطب شخصی\nآخرین پیام")
    result = asyncio.run(collector._extract_item_title(item))
    assert result == "مخاطب شخصی"


def test_extract_item_title_empty(collector):
    item = _mock_item()
    result = asyncio.run(collector._extract_item_title(item))
    assert result == ""


def test_extract_item_title_normalizes_whitespace(collector):
    title_el = MagicMock()
    title_el.inner_text = AsyncMock(return_value="گروه   تست  ")
    item = _mock_item(children={".peer-title": title_el})
    result = asyncio.run(collector._extract_item_title(item))
    assert result == "گروه تست"


# ═══════════════════════════════════════════════════════════
# _is_system_name
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
    ],
)
def test_is_system_name(collector, name, expected):
    assert collector._is_system_name(name) is expected


def test_is_system_name_none(collector):
    assert collector._is_system_name(None) is False


# ═══════════════════════════════════════════════════════════
# _is_admin_chat
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


def test_is_admin_chat_empty(collector):
    collector._admin_user_ids = set()
    collector._admin_chat_name = ""
    assert collector._is_admin_chat("", "") is False


# ═══════════════════════════════════════════════════════════
# _extract_sender_from_bubble
# ═══════════════════════════════════════════════════════════
def test_extract_sender_from_bubble_peer_id(collector):
    msg = _mock_item(attrs={"data-peer-id": "777"})
    sender_id, sender_name = asyncio.run(
        collector._extract_sender_from_bubble(msg)
    )
    assert sender_id == "777"


def test_extract_sender_from_bubble_name(collector):
    name_el = MagicMock()
    name_el.inner_text = AsyncMock(return_value="علی رضایی")
    msg = _mock_item(children={".peer-title": name_el})
    sender_id, sender_name = asyncio.run(
        collector._extract_sender_from_bubble(msg)
    )
    assert sender_name == "علی رضایی"


def test_extract_sender_from_bubble_empty(collector):
    msg = _mock_item()
    sender_id, sender_name = asyncio.run(
        collector._extract_sender_from_bubble(msg)
    )
    assert sender_id == ""
    assert sender_name == ""


def test_extract_sender_from_bubble_long_name_ignored(collector):
    # نام بلندتر از ۶۰ کاراکتر نباید پذیرفته شود
    name_el = MagicMock()
    name_el.inner_text = AsyncMock(return_value="x" * 100)
    msg = _mock_item(children={".peer-title": name_el})
    sender_id, sender_name = asyncio.run(
        collector._extract_sender_from_bubble(msg)
    )
    assert sender_name == ""


# ═══════════════════════════════════════════════════════════
# _is_logged_in
# ═══════════════════════════════════════════════════════════
def test_is_logged_in_login_url(collector):
    collector._page = _mock_page(url="https://web.bale.ai/login")
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_wrong_domain(collector):
    collector._page = _mock_page(url="https://example.com/")
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_login_marker(collector):
    login_el = MagicMock()
    login_el.is_visible = AsyncMock(return_value=True)
    collector._page = _mock_page(
        url="https://web.bale.ai/",
        selectors={'input[type="tel"]': login_el},
    )
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_loggedin_marker(collector):
    loggedin_el = MagicMock()
    loggedin_el.is_visible = AsyncMock(return_value=True)
    collector._page = _mock_page(
        url="https://web.bale.ai/",
        selectors={'div[contenteditable="true"]': loggedin_el},
    )
    assert asyncio.run(collector._is_logged_in()) is True


def test_is_logged_in_no_markers(collector):
    collector._page = _mock_page(url="https://web.bale.ai/")
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._is_logged_in()) is False


# ═══════════════════════════════════════════════════════════
# _detect_chat_type
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
# _is_valid_chat_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "name,expected",
    [
        ("گروه تست", True),
        ("Ali", True),
        ("", False),
        ("   ", False),
        (None, False),
        ("1234567", False),   # عددی بلندتر از ۶ رقم
        ("123", True),        # عددی کوتاه مجاز
    ],
)
def test_is_valid_chat_name(collector, name, expected):
    assert collector._is_valid_chat_name(name) is expected


# ═══════════════════════════════════════════════════════════
# _get_current_peer_id
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


# ═══════════════════════════════════════════════════════════
# _extract_chat_meta
# ═══════════════════════════════════════════════════════════
def test_extract_chat_meta_username(collector):
    collector._page = _mock_page(url="https://web.bale.ai/#/@myuser")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname == "myuser"
    assert link == "https://web.bale.ai/#/@myuser"


def test_extract_chat_meta_group(collector):
    collector._page = _mock_page(url="https://web.bale.ai/#/g/mygroup")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname == "mygroup"
    assert link == "https://web.bale.ai/#/g/mygroup"


def test_extract_chat_meta_none(collector):
    collector._page = _mock_page(url="https://web.bale.ai/")
    uname, link = asyncio.run(collector._extract_chat_meta())
    assert uname is None
    assert link is None


# ═══════════════════════════════════════════════════════════
# خواص
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
# send_message (غیرفعال)
# ═══════════════════════════════════════════════════════════
def test_send_message_disabled(collector):
    result = asyncio.run(
        collector.send_message("123", "private", "سلام")
    )
    assert result is False


# ═══════════════════════════════════════════════════════════
# check_health
# ═══════════════════════════════════════════════════════════
def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True
