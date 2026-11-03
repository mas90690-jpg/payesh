# tests/unit/test_linkedin.py
"""
Unit tests for collectors/linkedin.py — v3.0 (from scratch).

Coverage:
  ─── Pure functions ───
  - _human_delay / _human_scroll / _human_click
  - _already_ran_today
  - _load_searches

  ─── Async methods (mocked page) ───
  - _is_follower_post (True / False / None)
  - _get_post_id (componentkey / data-urn / fallback)
  - _match_post (keyword match / query add)
  - _extract_link (feed/update / in/)

  ─── State ───
  - _load_seen / _save_seen
  - _load_last_run / _save_last_run
  - _load_searches

  ─── Properties ───
  - platform_name / poll_interval / supports_history / supports_websocket

  ─── Safety ───
  - fetch_messages (not connected → empty)

  ─── check_health ───

Run:
  pytest tests/unit/test_linkedin.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.linkedin import (
    LinkedInCollector,
    LOGIN_MARKERS,
    LOGGEDIN_MARKERS,
    LINKEDIN_FEED_URL,
    LINKEDIN_SEARCH_URL,
    _MAX_SEEN,
    _UI_MARKERS,
    _COMPANY_MARKERS,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        config, "STORAGE_DIR", str(tmp_path / "storage"),
        raising=False,
    )
    (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
    return LinkedInCollector()


def _mock_page(url="https://www.linkedin.com/feed/"):
    page = MagicMock()
    page.url = url
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.evaluate = AsyncMock(return_value="")
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.mouse.move = AsyncMock()
    page.mouse.click = AsyncMock()
    page.locator = MagicMock()
    page.close = AsyncMock()
    page.wait_for_selector = AsyncMock()
    return page


def _make_post(
    attrs=None,
    inner_text="",
    buttons=None,
    hrefs=None,
    evaluate_result=None,
    bounding_box=None,
):
    """mock post با query_selector_all / get_attribute / inner_text / evaluate."""
    post = MagicMock()
    attrs = attrs or {}
    buttons = buttons or []
    hrefs = hrefs or {}

    async def _get_attribute(name):
        return attrs.get(name)

    async def _inner_text():
        return inner_text

    async def _query_selector_all(sel):
        if sel == "button[aria-label]":
            out = []
            for lbl in buttons:
                b = MagicMock()

                async def _ga(_n, _l=lbl):
                    return _l
                b.get_attribute = _ga
                out.append(b)
            return out
        return []

    async def _query_selector(sel):
        if sel in hrefs:
            a = MagicMock()

            async def _ga(_n, _h=hrefs[sel]):
                return _h
            a.get_attribute = _ga

            async def _it():
                return "Author Name"
            a.inner_text = _it
            return a
        return None

    async def _evaluate(script, *args):
        if evaluate_result is not None:
            return evaluate_result
        if "innerText" in script:
            return inner_text
        return ""

    post.get_attribute = _get_attribute
    post.inner_text = _inner_text
    post.query_selector_all = _query_selector_all
    post.query_selector = _query_selector
    post.evaluate = _evaluate
    post.scroll_into_view_if_needed = AsyncMock()
    post.bounding_box = AsyncMock(return_value=bounding_box)
    return post


# ═══════════════════════════════════════════════════════════
# 1) Constants
# ═══════════════════════════════════════════════════════════
def test_feed_url():
    assert LINKEDIN_FEED_URL == "https://www.linkedin.com/feed/"


def test_search_url_template():
    assert "{q}" in LINKEDIN_SEARCH_URL


def test_login_markers_not_empty():
    assert len(LOGIN_MARKERS) > 0


def test_loggedin_markers_not_empty():
    assert len(LOGGEDIN_MARKERS) > 0


def test_ui_markers_not_empty():
    assert len(_UI_MARKERS) > 0


def test_company_markers():
    assert "view company:" in _COMPANY_MARKERS


def test_max_seen_positive():
    assert _MAX_SEEN > 0


# ═══════════════════════════════════════════════════════════
# 2) _is_follower_post
# ═══════════════════════════════════════════════════════════
class TestIsFollowerPost:
    @pytest.mark.asyncio
    async def test_follow_button_returns_false(self, collector):
        post = _make_post(
            buttons=["Follow John Doe"], inner_text="hello world"
        )
        assert await collector._is_follower_post(post) is False

    @pytest.mark.asyncio
    async def test_following_header_returns_true(self, collector):
        post = _make_post(
            buttons=[],
            inner_text="Feed post\n\nJohn Doe\n\n• Following\n\nsome text",
        )
        assert await collector._is_follower_post(post) is True

    @pytest.mark.asyncio
    async def test_view_company_returns_none(self, collector):
        post = _make_post(
            buttons=["View company: Acme Corp"],
            inner_text="company post text",
        )
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_control_menu_no_follow_returns_none(self, collector):
        post = _make_post(
            buttons=["Open control menu for post by Acme Corp"],
            inner_text="company post text",
        )
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_no_signal_returns_none(self, collector):
        post = _make_post(buttons=[], inner_text="just some text")
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, collector):
        post = MagicMock()
        post.query_selector_all = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_following_exact_returns_none(self, collector):
        """Following بدون header → None."""
        post = _make_post(buttons=["Following"], inner_text="text")
        assert await collector._is_follower_post(post) is None


# ═══════════════════════════════════════════════════════════
# 3) _get_post_id
# ═══════════════════════════════════════════════════════════
class TestGetPostId:
    @pytest.mark.asyncio
    async def test_componentkey_returns_32_hex(self, collector):
        post = _make_post(attrs={"componentkey": "abc123FeedTypeXYZ"})
        pid = await collector._get_post_id(post)
        assert len(pid) == 32
        assert all(c in "0123456789abcdef" for c in pid)

    @pytest.mark.asyncio
    async def test_data_urn_returns_32_hex(self, collector):
        post = _make_post(attrs={"data-urn": "urn:li:activity:123"})
        pid = await collector._get_post_id(post)
        assert len(pid) == 32

    @pytest.mark.asyncio
    async def test_data_id_returns_32_hex(self, collector):
        post = _make_post(attrs={"data-id": "post-999"})
        pid = await collector._get_post_id(post)
        assert len(pid) == 32

    @pytest.mark.asyncio
    async def test_text_fallback_returns_32_hex(self, collector):
        post = _make_post(attrs={}, inner_text="some post text here")
        pid = await collector._get_post_id(post)
        assert len(pid) == 32

    @pytest.mark.asyncio
    async def test_empty_returns_empty_string(self, collector):
        post = _make_post(attrs={}, inner_text="")
        pid = await collector._get_post_id(post)
        assert pid == ""

    @pytest.mark.asyncio
    async def test_componentkey_stable(self, collector):
        post1 = _make_post(attrs={"componentkey": "same-key"})
        post2 = _make_post(attrs={"componentkey": "same-key"})
        assert (
            await collector._get_post_id(post1)
            == await collector._get_post_id(post2)
        )


# ═══════════════════════════════════════════════════════════
# 4) _match_post
# ═══════════════════════════════════════════════════════════
class TestMatchPost:
    def test_keyword_match_returns_set(self, collector):
        collector._kw = MagicMock()
        collector._kw.match = MagicMock(
            return_value=["python", "django"]
        )
        result = collector._match_post("python django text", None)
        assert isinstance(result, set)
        assert "python" in result

    def test_query_added_when_present(self, collector):
        collector._kw = MagicMock()
        collector._kw.match = MagicMock(return_value=[])
        collector._add_query_to_keywords = True
        result = collector._match_post("we love FastAPI here", "fastapi")
        assert "fastapi" in result

    def test_no_match_returns_empty_set(self, collector):
        collector._kw = MagicMock()
        collector._kw.match = MagicMock(return_value=[])
        result = collector._match_post("nothing relevant", None)
        assert result == set()

    def test_query_not_added_when_disabled(self, collector):
        collector._kw = MagicMock()
        collector._kw.match = MagicMock(return_value=[])
        collector._add_query_to_keywords = False
        result = collector._match_post("we love fastapi", "fastapi")
        assert result == set()


# ═══════════════════════════════════════════════════════════
# 5) _extract_link
# ═══════════════════════════════════════════════════════════
class TestExtractLink:
    @pytest.mark.asyncio
    async def test_feed_update_link(self, collector):
        post = _make_post(hrefs={
            'a[href*="/feed/update/"]':
            "https://linkedin.com/feed/update/urn:li:123?x=1"
        })
        link = await collector._extract_link(post)
        assert link == "https://linkedin.com/feed/update/urn:li:123"

    @pytest.mark.asyncio
    async def test_in_profile_link(self, collector):
        post = _make_post(hrefs={
            "a[href*='/in/']":
            "https://linkedin.com/in/john?trk=abc"
        })
        link = await collector._extract_link(post)
        assert link == "https://linkedin.com/in/john"

    @pytest.mark.asyncio
    async def test_no_link_returns_none(self, collector):
        post = _make_post(hrefs={})
        link = await collector._extract_link(post)
        assert link is None


# ═══════════════════════════════════════════════════════════
# 6) _already_ran_today
# ═══════════════════════════════════════════════════════════
class TestAlreadyRanToday:
    def test_today_returns_true(self, collector):
        collector._last_run_date = datetime.now().strftime("%Y-%m-%d")
        assert collector._already_ran_today() is True

    def test_yesterday_returns_false(self, collector):
        yesterday = (
            datetime.now() - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        collector._last_run_date = yesterday
        assert collector._already_ran_today() is False

    def test_empty_returns_false(self, collector):
        collector._last_run_date = ""
        assert collector._already_ran_today() is False


# ═══════════════════════════════════════════════════════════
# 7) _load_searches
# ═══════════════════════════════════════════════════════════
class TestLoadSearches:
    def test_file_with_queries(self, collector, tmp_path):
        f = tmp_path / "searches.txt"
        f.write_text("python\ndjango\n", encoding="utf-8")
        collector._searches_file = str(f)
        result = collector._load_searches()
        assert result == ["python", "django"]

    def test_comments_skipped(self, collector, tmp_path):
        f = tmp_path / "searches.txt"
        f.write_text(
            "# comment\npython\n\n# another\ndjango\n",
            encoding="utf-8",
        )
        collector._searches_file = str(f)
        result = collector._load_searches()
        assert result == ["python", "django"]

    def test_no_file_returns_empty(self, collector, tmp_path):
        collector._searches_file = str(tmp_path / "nonexistent.txt")
        assert collector._load_searches() == []


# ═══════════════════════════════════════════════════════════
# 8) Properties
# ═══════════════════════════════════════════════════════════
class TestProperties:
    def test_platform_name(self, collector):
        assert collector.platform_name == "linkedin"

    def test_supports_history_false(self, collector):
        assert collector.supports_history is False

    def test_supports_websocket_false(self, collector):
        assert collector.supports_websocket is False

    def test_poll_interval(self, collector):
        assert collector.poll_interval_seconds == 600.0


# ═══════════════════════════════════════════════════════════
# 9) Seen
# ═══════════════════════════════════════════════════════════
class TestSeen:
    def test_load_seen_from_file(self, collector, tmp_path):
        p = tmp_path / "seen.json"
        p.write_text(json.dumps(["id1", "id2"]), encoding="utf-8")
        collector._seen_path = str(p)
        collector._load_seen()
        assert collector._seen_ids == {"id1", "id2"}

    def test_load_seen_missing_file(self, collector, tmp_path):
        collector._seen_path = str(tmp_path / "nope.json")
        collector._load_seen()
        assert collector._seen_ids == set()

    def test_save_seen_writes_file(self, collector, tmp_path):
        p = tmp_path / "seen.json"
        collector._seen_path = str(p)
        collector._seen_ids = {"a", "b"}
        collector._save_seen()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert set(data) == {"a", "b"}


# ═══════════════════════════════════════════════════════════
# 10) Last run
# ═══════════════════════════════════════════════════════════
class TestLastRun:
    def test_load_last_run(self, collector, tmp_path):
        p = tmp_path / "last.json"
        p.write_text(
            json.dumps({"last_date": "2026-01-01"}),
            encoding="utf-8",
        )
        collector._last_run_path = str(p)
        collector._load_last_run()
        assert collector._last_run_date == "2026-01-01"

    def test_load_last_run_missing(self, collector, tmp_path):
        collector._last_run_path = str(tmp_path / "nope.json")
        collector._load_last_run()
        assert collector._last_run_date == ""

    def test_save_last_run(self, collector, tmp_path):
        p = tmp_path / "last.json"
        collector._last_run_path = str(p)
        collector._save_last_run()
        assert collector._last_run_date == datetime.now().strftime(
            "%Y-%m-%d"
        )
        assert p.exists()


# ═══════════════════════════════════════════════════════════
# 11) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
class TestFetchMessages:
    def test_not_connected_returns_empty(self, collector):
        collector._connected = False
        collector._page = None
        assert asyncio.run(collector.fetch_messages()) == []

    def test_no_page_returns_empty(self, collector):
        collector._connected = True
        collector._page = None
        assert asyncio.run(collector.fetch_messages()) == []


# ═══════════════════════════════════════════════════════════
# 12) check_health
# ═══════════════════════════════════════════════════════════
class TestCheckHealth:
    def test_connected_true(self, collector):
        collector._connected = True
        assert asyncio.run(collector.check_health()) is True

    def test_connected_false(self, collector):
        collector._connected = False
        assert asyncio.run(collector.check_health()) is False