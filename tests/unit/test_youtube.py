# tests/unit/test_youtube.py
"""
Unit tests for collectors/youtube.py — v4.0 (from scratch).

Coverage:
  ─── Constants ───
  - LOGIN_MARKERS / LOGGEDIN_MARKERS / VIDEO_CARD_SELECTORS
  - YOUTUBE_HOME / YOUTUBE_SUBSCRIPTIONS / YOUTUBE_WATCH
  - _PLATFORM / _MAX_SEEN

  ─── Pure functions ───
  - (هیچ pure function مستقل نداریم — همه در کلاس هستند)

  ─── Static helpers (فقط از طریق نمونه) ───
  - _load_state / _save_state
  - _load_seen / _save_seen
  - _is_logged_in (avatar / sign-in / nothing / no page)

  ─── Properties ───
  - platform_name / poll_interval / supports_history / supports_websocket
  - is_connected

  ─── fetch_messages (safety) ───
  - not connected → []
  - no page → []
  - force_no_since → effective_since=None

  ─── _extract_video_ids_from_dom ───
  - returns list / empty / error

  ─── _extract_and_queue ───
  - title empty → False (skipped_invalid)
  - no keyword → False (skipped_no_kw, seen added)
  - since filter → False (skipped_old)
  - valid → True (queued with correct fields)
  - exception → False (failed)

  ─── send_message / check_health / dump_dom ───
  - send_message disabled
  - check_health mirror _connected
  - dump_dom no page → ""

  ─── State transitions ───
  - _save_state updates in-memory
  - _save_seen persists

Run:
  pytest tests/unit/test_youtube.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.youtube import (
    YouTubeCollector,
    _MAX_SEEN,
    _PLATFORM,
    LOGGEDIN_MARKERS,
    LOGIN_MARKERS,
    VIDEO_CARD_SELECTORS,
    YOUTUBE_HOME,
    YOUTUBE_SUBSCRIPTIONS,
    YOUTUBE_WATCH,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = [
    "YOUTUBE_ENABLED",
    "YOUTUBE_POLL_SECONDS",
    "YOUTUBE_MAX_VIDEOS",
    "YOUTUBE_SCROLL_ROUNDS",
    "YOUTUBE_DELAY",
]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("YOUTUBE_ENABLED", "true")
    return YouTubeCollector(
        headless=True,
        max_videos=2,
        scroll_rounds=2,
        delay_between=0.1,
    )


def _mock_page(url="https://www.youtube.com/"):
    """Mock Playwright page."""
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.title = AsyncMock(return_value="YouTube")
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.evaluate = AsyncMock(return_value=[])
    page.locator = MagicMock()
    page.locator.return_value.count = AsyncMock(return_value=0)
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.close = AsyncMock()
    page.wait_for_selector = AsyncMock()
    return page


def _reset_stats(collector):
    """Reset _last_scrape_stats با همهٔ کلیدهای v4.0."""
    collector._last_scrape_stats = {
        "total_in_feed": 0,
        "new_videos": 0,
        "processed": 0,
        "stored": 0,
        "skipped_seen": 0,
        "skipped_old": 0,
        "skipped_no_kw": 0,
        "skipped_invalid": 0,
        "failed": 0,
    }


def _mock_visible(visible=True):
    el = MagicMock()
    el.is_visible = AsyncMock(return_value=visible)
    return el


# ═══════════════════════════════════════════════════════════
# 1) Constants
# ═══════════════════════════════════════════════════════════
def test_platform_name_constant():
    assert _PLATFORM == "youtube"


def test_max_seen_positive():
    assert _MAX_SEEN > 0


def test_youtube_urls():
    assert YOUTUBE_HOME == "https://www.youtube.com/"
    assert YOUTUBE_SUBSCRIPTIONS == (
        "https://www.youtube.com/feed/subscriptions"
    )
    assert "{vid}" in YOUTUBE_WATCH


def test_login_markers_not_empty():
    assert len(LOGIN_MARKERS) > 0


def test_loggedin_markers_not_empty():
    assert len(LOGGEDIN_MARKERS) > 0


def test_video_card_selectors():
    assert "ytd-rich-item-renderer" in VIDEO_CARD_SELECTORS
    assert "ytd-video-renderer" in VIDEO_CARD_SELECTORS


# ═══════════════════════════════════════════════════════════
# 2) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "youtube"


def test_poll_interval(collector):
    assert collector.poll_interval_seconds == 1800.0


def test_supports_history(collector):
    assert collector.supports_history is True


def test_supports_websocket(collector):
    assert collector.supports_websocket is False


def test_is_connected_property(collector):
    collector._connected = True
    assert collector.is_connected is True
    collector._connected = False
    assert collector.is_connected is False


# ═══════════════════════════════════════════════════════════
# 3) State — _load_state / _save_state
# ═══════════════════════════════════════════════════════════
def test_first_run_initially_false(collector):
    assert collector._first_run_done is False


def test_save_state_updates_in_memory(collector):
    """🎯 FIX v4.0: _save_state باید in-memory را آپدیت کند."""
    assert collector._first_run_done is False
    assert collector._first_run_at is None
    assert collector._last_run_at is None

    collector._save_state()

    assert collector._first_run_done is True
    assert collector._first_run_at is not None
    assert collector._last_run_at is not None


def test_save_and_load_state(collector):
    """state ذخیره و بارگذاری می‌شود."""
    collector._save_state()
    saved_first_run_at = collector._first_run_at
    saved_last_run_at = collector._last_run_at

    # reset in-memory
    collector._first_run_done = False
    collector._first_run_at = None
    collector._last_run_at = None

    collector._load_state()

    assert collector._first_run_done is True
    assert collector._first_run_at == saved_first_run_at
    assert collector._last_run_at is not None
    assert (
        collector._last_run_at.isoformat()
        == saved_last_run_at.isoformat()
    )


def test_load_state_corrupt_file(collector):
    """فایل خراب → state خالی (بدون crash)."""
    with open(collector._state_path, "w", encoding="utf-8") as f:
        f.write("not json")

    collector._load_state()

    assert collector._first_run_done is False
    assert collector._first_run_at is None
    assert collector._last_run_at is None


def test_load_state_missing_file(collector):
    """فایل نبود → state خالی."""
    collector._load_state()
    assert collector._first_run_done is False


# ═══════════════════════════════════════════════════════════
# 4) Seen — _load_seen / _save_seen
# ═══════════════════════════════════════════════════════════
def test_load_seen_empty(collector):
    assert collector._seen_ids == set()


def test_save_and_load_seen(collector):
    collector._seen_ids = {"vid1", "vid2", "vid3"}
    collector._save_seen()
    collector._seen_ids = set()
    collector._load_seen()
    assert collector._seen_ids == {"vid1", "vid2", "vid3"}


def test_load_seen_corrupt(collector):
    with open(collector._seen_path, "w", encoding="utf-8") as f:
        f.write("not json")
    collector._load_seen()
    assert collector._seen_ids == set()


def test_load_seen_dict_format(collector):
    """dict بجای list → set خالی."""
    with open(collector._seen_path, "w", encoding="utf-8") as f:
        json.dump({"not": "a list"}, f)
    collector._load_seen()
    assert collector._seen_ids == set()


# ═══════════════════════════════════════════════════════════
# 5) _is_logged_in
# ═══════════════════════════════════════════════════════════
def test_is_logged_in_no_page(collector):
    collector._page = None
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_nothing(collector):
    """هیچ مارکری نبود → False."""
    collector._page = _mock_page()
    collector._page.query_selector = AsyncMock(return_value=None)
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_sign_in_button(collector):
    """Sign in visible → not logged in."""
    el = _mock_visible(True)
    collector._page = _mock_page()
    collector._page.query_selector = AsyncMock(
        side_effect=lambda sel: (
            el if "ServiceLogin" in sel or "Sign in" in sel
            else None
        )
    )
    assert asyncio.run(collector._is_logged_in()) is False


def test_is_logged_in_avatar_btn(collector):
    """avatar-btn visible → logged in."""
    el = _mock_visible(True)
    collector._page = _mock_page()
    collector._page.query_selector = AsyncMock(
        side_effect=lambda sel: (
            el if "avatar" in sel.lower() or "Account" in sel
            else None
        )
    )
    assert asyncio.run(collector._is_logged_in()) is True


def test_is_logged_in_sign_in_hidden(collector):
    """Sign in element exists but hidden → continue."""
    el = _mock_visible(False)
    collector._page = _mock_page()
    collector._page.query_selector = AsyncMock(
        side_effect=lambda sel: (
            el if "ServiceLogin" in sel or "Sign in" in sel
            else None
        )
    )
    assert asyncio.run(collector._is_logged_in()) is False


# ═══════════════════════════════════════════════════════════
# 6) fetch_messages — safety
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_page(collector):
    collector._connected = True
    collector._page = None
    assert asyncio.run(collector.fetch_messages()) == []


# ═══════════════════════════════════════════════════════════
# 7) _extract_video_ids_from_dom
# ═══════════════════════════════════════════════════════════
def test_extract_video_ids_returns_list(collector):
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(
        return_value=["abc123", "def456", "ghi789"]
    )
    ids = asyncio.run(collector._extract_video_ids_from_dom())
    assert ids == ["abc123", "def456", "ghi789"]


def test_extract_video_ids_empty(collector):
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value=[])
    assert asyncio.run(collector._extract_video_ids_from_dom()) == []


def test_extract_video_ids_non_list_response(collector):
    """اگر evaluate چیز غیرلیست برگرداند → []."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value=None)
    assert asyncio.run(collector._extract_video_ids_from_dom()) == []


def test_extract_video_ids_error(collector):
    """اگر evaluate خطا داد → [] (بدون crash)."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    assert asyncio.run(collector._extract_video_ids_from_dom()) == []


def test_extract_video_ids_filters_falsy(collector):
    """مقادیر خالی حذف می‌شوند."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(
        return_value=["abc", "", None, "def"]
    )
    ids = asyncio.run(collector._extract_video_ids_from_dom())
    assert ids == ["abc", "def"]


# ═══════════════════════════════════════════════════════════
# 8) _extract_and_queue — حالت‌های مختلف
# ═══════════════════════════════════════════════════════════
def test_extract_and_queue_title_empty(collector):
    """اگر عنوان پیدا نشد → False و skipped_invalid++."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value={
        "title": "", "description": "", "channel": "",
        "publish_date": "", "view_count": "",
    })
    _reset_stats(collector)

    ok = asyncio.run(collector._extract_and_queue("abc123"))

    assert ok is False
    assert collector._last_scrape_stats["skipped_invalid"] == 1
    assert len(collector._messages_queue) == 0


def test_extract_and_queue_no_keyword(collector):
    """بدون کلیدواژه → False و skipped_no_kw++ و seen.add."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value={
        "title": "بی‌ربط",
        "description": "بدون کلیدواژه",
        "channel": "ch1",
        "publish_date": "",
        "view_count": "100",
    })
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=False)
    _reset_stats(collector)

    ok = asyncio.run(collector._extract_and_queue("abc123"))

    assert ok is False
    assert collector._last_scrape_stats["skipped_no_kw"] == 1
    assert "abc123" in collector._seen_ids
    assert len(collector._messages_queue) == 0


def test_extract_and_queue_old_by_since(collector):
    """publish_date < effective_since → False و skipped_old++."""
    collector._page = _mock_page()
    old_pub = (
        datetime.now() - timedelta(days=30)
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    collector._page.evaluate = AsyncMock(return_value={
        "title": "عنوان قدیمی",
        "description": "کلیدواژه دار",
        "channel": "ch1",
        "publish_date": old_pub,
        "view_count": "100",
    })
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    since = datetime.now() - timedelta(days=7)
    ok = asyncio.run(collector._extract_and_queue(
        "abc123", effective_since=since,
    ))

    assert ok is False
    assert collector._last_scrape_stats["skipped_old"] == 1
    assert len(collector._messages_queue) == 0


def test_extract_and_queue_valid(collector):
    """پیام معتبر → True و append به queue."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value={
        "title": "عنوان خوب",
        "description": "توضیحات",
        "channel": "کانال من",
        "publish_date": "",
        "view_count": "1000",
    })
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    ok = asyncio.run(collector._extract_and_queue("abc123"))

    assert ok is True
    assert len(collector._messages_queue) == 1
    msg = collector._messages_queue[0]
    assert msg["id"] == "abc123"
    assert msg["platform"] == "youtube"
    assert msg["chat_type"] == "youtube"
    assert msg["chat_name"] == "YouTube: کانال من"
    assert msg["sender_name"] == "کانال من"
    assert msg["chat_username"] == "کانال من"
    assert msg["media_type"] == "video"
    assert msg["text"].startswith("عنوان خوب")
    assert msg["chat_link"].endswith("v=abc123")
    assert "abc123" in collector._seen_ids


def test_extract_and_queue_exception(collector):
    """اگر goto خطا داد → False و failed++."""
    collector._page = _mock_page()
    collector._page.goto = AsyncMock(
        side_effect=RuntimeError("network down")
    )
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    ok = asyncio.run(collector._extract_and_queue("abc123"))

    assert ok is False
    assert collector._last_scrape_stats["failed"] == 1
    assert len(collector._messages_queue) == 0


def test_extract_and_queue_description_fallback(collector):
    """اگر description خالی بود → از title استفاده می‌شود."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value={
        "title": "عنوان اصلی",
        "description": "",
        "channel": "ch1",
        "publish_date": "",
        "view_count": "0",
    })
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    ok = asyncio.run(collector._extract_and_queue("abc123"))

    assert ok is True
    msg = collector._messages_queue[0]
    # description خالی → title استفاده شد
    assert "عنوان اصلی" in msg["text"]


def test_extract_and_queue_invalid_publish_date(collector):
    """اگر publish_date خراب بود → since فیلتر اعمال نمی‌شود، ولی crash نمی‌شود."""
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value={
        "title": "عنوان",
        "description": "توضیح",
        "channel": "ch1",
        "publish_date": "not-a-valid-date",
        "view_count": "0",
    })
    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)
    _reset_stats(collector)

    since = datetime.now() - timedelta(days=7)
    ok = asyncio.run(collector._extract_and_queue(
        "abc123", effective_since=since,
    ))

    assert ok is True  # چون publish_date خراب بود، skip نشد
    assert len(collector._messages_queue) == 1


# ═══════════════════════════════════════════════════════════
# 9) send_message / check_health / dump_dom
# ═══════════════════════════════════════════════════════════
def test_send_message_disabled(collector):
    assert asyncio.run(
        collector.send_message("x", "y", "z")
    ) is False


def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True


def test_dump_dom_no_page(collector):
    collector._page = None
    assert asyncio.run(collector.dump_dom()) == ""


def test_dump_dom_writes_file(collector, tmp_path):
    collector._page = _mock_page()
    collector._page.content = AsyncMock(
        return_value="<html>YT</html>"
    )
    collector._user_data_dir = str(tmp_path)

    path = asyncio.run(collector.dump_dom())

    import os
    assert path != ""
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        assert "YT" in f.read()


# ═══════════════════════════════════════════════════════════
# 10) Integration — fetch_messages کامل (mock)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_full_cycle(collector):
    """تست یک چرخهٔ کامل fetch_messages با mock."""
    collector._connected = True
    collector._page = _mock_page()

    # goto ok
    collector._page.goto = AsyncMock()

    # اسکرول: کاری نمی‌کند
    collector._page.mouse.wheel = AsyncMock()

    # locator count → 0 (بدون اسکرول اضافی)
    collector._page.locator = MagicMock()
    collector._page.locator.return_value.count = AsyncMock(
        return_value=0
    )

    # _extract_video_ids_from_dom → 2 ids (ولی همه در seen → skip)
    collector._seen_ids = {"vid1", "vid2"}
    collector._page.evaluate = AsyncMock(
        return_value=["vid1", "vid2"]
    )

    collector._kw = MagicMock()
    collector._kw.has_match = MagicMock(return_value=True)

    result = asyncio.run(
        collector.fetch_messages(force_no_since=True)
    )

    # چون هر دو در seen بودند → خروجی خالی
    assert result == []
    # آمار درست
    assert collector._last_scrape_stats["total_in_feed"] == 2
    assert collector._last_scrape_stats["new_videos"] == 0
    assert collector._last_scrape_stats["skipped_seen"] == 2


def test_fetch_messages_no_video_ids(collector):
    """اگر هیچ id پیدا نشد → stats empty."""
    collector._connected = True
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value=[])
    collector._kw = MagicMock()

    result = asyncio.run(
        collector.fetch_messages(force_no_since=True)
    )

    assert result == []
    assert collector._last_scrape_stats["total_in_feed"] == 0


def test_fetch_messages_updates_state(collector):
    """بعد از fetch، first_run_done=True و last_run_at ست می‌شود."""
    collector._connected = True
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value=[])
    collector._kw = MagicMock()

    assert collector._first_run_done is False
    asyncio.run(collector.fetch_messages(force_no_since=True))
    assert collector._first_run_done is True
    assert collector._last_run_at is not None


def test_fetch_messages_saves_seen(collector):
    """بعد از fetch، seen در دیسک ذخیره می‌شود."""
    collector._connected = True
    collector._page = _mock_page()
    collector._page.evaluate = AsyncMock(return_value=[])
    collector._kw = MagicMock()
    collector._seen_ids = {"a", "b"}

    asyncio.run(collector.fetch_messages(force_no_since=True))

    # reset in-memory و دوباره load
    collector._seen_ids = set()
    collector._load_seen()
    assert "a" in collector._seen_ids
    assert "b" in collector._seen_ids