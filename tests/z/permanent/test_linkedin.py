# tests/permanent/test_linkedin.py
"""
تست Unit کامل collectors/linkedin.py — بدون اتصال، بدون مرورگر.

همهٔ توابع با mock page/post تست می‌شن.
"""
import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.linkedin import LinkedInCollector


# ═══════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════
@pytest.fixture
def collector(tmp_path, monkeypatch):
    """ساخت collector بدون اتصال — با مسیرهای موقت."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"), raising=False)
    os.makedirs(tmp_path / "storage", exist_ok=True)
    c = LinkedInCollector()
    return c


def make_post(
    attrs=None,
    inner_text="",
    buttons=None,
    hrefs=None,
    evaluate_result=None,
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
        # اگر evaluate_result صریحاً داده شده، همان را برگردان
        if evaluate_result is not None:
            return evaluate_result
        # در غیر این صورت، برای اسکریپت‌های innerText، متن پست را برگردان
        if "innerText" in script:
            return inner_text
        return ""

    post.get_attribute = _get_attribute
    post.inner_text = _inner_text
    post.query_selector_all = _query_selector_all
    post.query_selector = _query_selector
    post.evaluate = _evaluate
    post.scroll_into_view_if_needed = AsyncMock()
    return post


# ═══════════════════════════════════════════════════
# ۱) _is_follower_post
# ═══════════════════════════════════════════════════
class TestIsFollowerPost:
    @pytest.mark.asyncio
    async def test_follow_button_returns_false(self, collector):
        post = make_post(buttons=["Follow John Doe"], inner_text="hello world")
        assert await collector._is_follower_post(post) is False

    @pytest.mark.asyncio
    async def test_following_header_returns_true(self, collector):
        post = make_post(
            buttons=[],
            inner_text="Feed post\n\nJohn Doe\n\n• Following\n\nsome text",
        )
        assert await collector._is_follower_post(post) is True

    @pytest.mark.asyncio
    async def test_view_company_returns_none(self, collector):
        post = make_post(
            buttons=["View company: Acme Corp"],
            inner_text="company post text",
        )
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_control_menu_no_follow_returns_none(self, collector):
        post = make_post(
            buttons=["Open control menu for post by Acme Corp"],
            inner_text="company post text",
        )
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_no_signal_returns_none(self, collector):
        post = make_post(buttons=[], inner_text="just some text")
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, collector):
        post = MagicMock()
        post.query_selector_all = AsyncMock(side_effect=RuntimeError("boom"))
        assert await collector._is_follower_post(post) is None

    @pytest.mark.asyncio
    async def test_following_button_exact_returns_none(self, collector):
        # "Following" دقیق (بدون Follow) و بدون header → None
        post = make_post(buttons=["Following"], inner_text="text")
        assert await collector._is_follower_post(post) is None


# ═══════════════════════════════════════════════════
# ۲) _get_post_id
# ═══════════════════════════════════════════════════
class TestGetPostId:
    @pytest.mark.asyncio
    async def test_componentkey_returns_32_hex(self, collector):
        post = make_post(attrs={"componentkey": "abc123FeedTypeXYZ"})
        pid = await collector._get_post_id(post)
        assert len(pid) == 32
        assert all(c in "0123456789abcdef" for c in pid)

    @pytest.mark.asyncio
    async def test_data_urn_returns_32_hex(self, collector):
        post = make_post(attrs={"data-urn": "urn:li:activity:123"})
        pid = await collector._get_post_id(post)
        assert len(pid) == 32

    @pytest.mark.asyncio
    async def test_data_id_returns_32_hex(self, collector):
        post = make_post(attrs={"data-id": "post-999"})
        pid = await collector._get_post_id(post)
        assert len(pid) == 32

    @pytest.mark.asyncio
    async def test_text_fallback_returns_32_hex(self, collector):
        post = make_post(attrs={}, inner_text="some post text here")
        pid = await collector._get_post_id(post)
        assert len(pid) == 32

    @pytest.mark.asyncio
    async def test_empty_returns_empty_string(self, collector):
        post = make_post(attrs={}, inner_text="")
        pid = await collector._get_post_id(post)
        assert pid == ""

    @pytest.mark.asyncio
    async def test_componentkey_stable(self, collector):
        post1 = make_post(attrs={"componentkey": "same-key"})
        post2 = make_post(attrs={"componentkey": "same-key"})
        assert await collector._get_post_id(post1) == await collector._get_post_id(post2)

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, collector):
        post = MagicMock()
        post.get_attribute = AsyncMock(side_effect=RuntimeError("boom"))
        assert await collector._get_post_id(post) == ""


# ═══════════════════════════════════════════════════
# ۳) _match_post
# ═══════════════════════════════════════════════════
class TestMatchPost:
    def test_keyword_match_returns_set(self, collector):
        collector._kw = MagicMock()
        collector._kw.match = MagicMock(return_value=["python", "django"])
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


# ═══════════════════════════════════════════════════
# ۴) _extract_link
# ═══════════════════════════════════════════════════
class TestExtractLink:
    @pytest.mark.asyncio
    async def test_feed_update_link(self, collector):
        post = make_post(hrefs={
            'a[href*="/feed/update/"]': "https://linkedin.com/feed/update/urn:li:123?x=1"
        })
        link = await collector._extract_link(post)
        assert link == "https://linkedin.com/feed/update/urn:li:123"

    @pytest.mark.asyncio
    async def test_in_profile_link(self, collector):
        post = make_post(hrefs={
            "a[href*='/in/']": "https://linkedin.com/in/john?trk=abc"
        })
        link = await collector._extract_link(post)
        assert link == "https://linkedin.com/in/john"

    @pytest.mark.asyncio
    async def test_no_link_returns_none(self, collector):
        post = make_post(hrefs={})
        link = await collector._extract_link(post)
        assert link is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, collector):
        post = MagicMock()
        post.query_selector = AsyncMock(side_effect=RuntimeError("boom"))
        assert await collector._extract_link(post) is None


# ═══════════════════════════════════════════════════
# ۵) _already_ran_today
# ═══════════════════════════════════════════════════
class TestAlreadyRanToday:
    def test_today_returns_true(self, collector):
        collector._last_run_date = datetime.now().strftime("%Y-%m-%d")
        assert collector._already_ran_today() is True

    def test_yesterday_returns_false(self, collector):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        collector._last_run_date = yesterday
        assert collector._already_ran_today() is False

    def test_empty_returns_false(self, collector):
        collector._last_run_date = ""
        assert collector._already_ran_today() is False


# ═══════════════════════════════════════════════════
# ۶) _load_searches
# ═══════════════════════════════════════════════════
class TestLoadSearches:
    def test_file_with_queries(self, collector, tmp_path):
        f = tmp_path / "searches.txt"
        f.write_text("python\ndjango\n", encoding="utf-8")
        collector._searches_file = str(f)
        result = collector._load_searches()
        assert result == ["python", "django"]

    def test_comments_skipped(self, collector, tmp_path):
        f = tmp_path / "searches.txt"
        f.write_text("# comment\npython\n\n# another\ndjango\n", encoding="utf-8")
        collector._searches_file = str(f)
        result = collector._load_searches()
        assert result == ["python", "django"]

    def test_no_file_returns_empty(self, collector, tmp_path):
        collector._searches_file = str(tmp_path / "nonexistent.txt")
        assert collector._load_searches() == []


# ═══════════════════════════════════════════════════
# ۷) _launch_context
# ═══════════════════════════════════════════════════
class TestLaunchContext:
    @pytest.mark.asyncio
    async def test_with_chrome_path(self, collector):
        mock_ctx = MagicMock()
        collector._pw = MagicMock()
        collector._pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)
        with patch("config.CHROME_PATH", "C:/chrome.exe"):
            ctx = await collector._launch_context(True)
        assert ctx is mock_ctx

    @pytest.mark.asyncio
    async def test_chrome_path_fails_fallback_channel(self, collector):
        mock_ctx = MagicMock()
        collector._pw = MagicMock()
        collector._pw.chromium.launch_persistent_context = AsyncMock(
            side_effect=[RuntimeError("fail"), mock_ctx]
        )
        with patch("config.CHROME_PATH", "C:/bad.exe"):
            ctx = await collector._launch_context(True)
        assert ctx is mock_ctx

    @pytest.mark.asyncio
    async def test_no_chrome_path_uses_channel(self, collector):
        mock_ctx = MagicMock()
        collector._pw = MagicMock()
        collector._pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)
        with patch("config.CHROME_PATH", ""):
            ctx = await collector._launch_context(True)
        assert ctx is mock_ctx

    @pytest.mark.asyncio
    async def test_all_fail_raises(self, collector):
        collector._pw = MagicMock()
        collector._pw.chromium.launch_persistent_context = AsyncMock(
            side_effect=RuntimeError("all fail")
        )
        with patch("config.CHROME_PATH", ""):
            with pytest.raises(RuntimeError):
                await collector._launch_context(True)


# ═══════════════════════════════════════════════════
# ۸) _is_logged_in
# ═══════════════════════════════════════════════════
class TestIsLoggedIn:
    @pytest.mark.asyncio
    async def test_login_url_returns_false(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/login"
        collector._page = page
        assert await collector._is_logged_in() is False

    @pytest.mark.asyncio
    async def test_global_nav_returns_true(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/feed/"
        el = MagicMock()
        el.is_visible = AsyncMock(return_value=True)

        async def _qs(sel):
            if sel == "#global-nav":
                return el
            return None
        page.query_selector = _qs
        collector._page = page
        assert await collector._is_logged_in() is True

    @pytest.mark.asyncio
    async def test_username_input_returns_false(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/feed/"
        el = MagicMock()
        el.is_visible = AsyncMock(return_value=True)

        async def _qs(sel):
            if sel == "input#username":
                return el
            return None
        page.query_selector = _qs
        collector._page = page
        assert await collector._is_logged_in() is False

    @pytest.mark.asyncio
    async def test_no_markers_linkedin_url_returns_true(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/feed/"
        page.query_selector = AsyncMock(return_value=None)
        collector._page = page
        assert await collector._is_logged_in() is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, collector):
        page = MagicMock()
        page.url = None
        page.query_selector = AsyncMock(side_effect=RuntimeError("boom"))
        collector._page = page
        assert await collector._is_logged_in() is False


# ═══════════════════════════════════════════════════
# ۹) _wait_login
# ═══════════════════════════════════════════════════
class TestWaitLogin:
    @pytest.mark.asyncio
    async def test_logged_in_returns_true(self, collector):
        collector._is_logged_in = AsyncMock(return_value=True)
        collector._set_status = MagicMock()
        with patch("asyncio.sleep", AsyncMock()):
            assert await collector._wait_login(rounds=3) is True

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, collector):
        collector._is_logged_in = AsyncMock(return_value=False)
        with patch("asyncio.sleep", AsyncMock()):
            assert await collector._wait_login(rounds=2) is False


# ═══════════════════════════════════════════════════
# ۱۰) _export_state
# ═══════════════════════════════════════════════════
class TestExportState:
    @pytest.mark.asyncio
    async def test_success(self, collector):
        ctx = MagicMock()
        ctx.storage_state = AsyncMock()
        collector._context = ctx
        await collector._export_state()
        ctx.storage_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_no_crash(self, collector):
        ctx = MagicMock()
        ctx.storage_state = AsyncMock(side_effect=RuntimeError("boom"))
        collector._context = ctx
        # نباید raise کنه
        await collector._export_state()


# ═══════════════════════════════════════════════════
# ۱۱) fetch_messages (بدون اتصال)
# ═══════════════════════════════════════════════════
class TestFetchMessages:
    @pytest.mark.asyncio
    async def test_not_connected_returns_empty(self, collector):
        collector._connected = False
        collector._page = None
        assert await collector.fetch_messages() == []

    @pytest.mark.asyncio
    async def test_no_page_returns_empty(self, collector):
        collector._connected = True
        collector._page = None
        assert await collector.fetch_messages() == []

    @pytest.mark.asyncio
    async def test_connected_no_page_returns_empty(self, collector):
        collector._connected = True
        collector._page = None
        result = await collector.fetch_messages(since=datetime.now())
        assert result == []


# ═══════════════════════════════════════════════════
# ۱۲) _check_session_alive
# ═══════════════════════════════════════════════════
class TestCheckSessionAlive:
    @pytest.mark.asyncio
    async def test_login_url_returns_false(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/login"
        collector._page = page
        collector._set_status = MagicMock()
        assert await collector._check_session_alive() is False

    @pytest.mark.asyncio
    async def test_feed_url_returns_true(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/feed/"
        collector._page = page
        assert await collector._check_session_alive() is True

    @pytest.mark.asyncio
    async def test_authwall_returns_false(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/authwall?x=1"
        collector._page = page
        collector._set_status = MagicMock()
        assert await collector._check_session_alive() is False

    @pytest.mark.asyncio
    async def test_checkpoint_returns_false(self, collector):
        page = MagicMock()
        page.url = "https://www.linkedin.com/checkpoint/challenge"
        collector._page = page
        collector._set_status = MagicMock()
        assert await collector._check_session_alive() is False


# ═══════════════════════════════════════════════════
# ۱۳) خواص
# ═══════════════════════════════════════════════════
class TestProperties:
    def test_platform_name(self, collector):
        assert collector.platform_name == "linkedin"

    def test_supports_history_false(self, collector):
        assert collector.supports_history is False

    def test_supports_websocket_false(self, collector):
        assert collector.supports_websocket is False

    def test_poll_interval(self, collector):
        assert collector.poll_interval_seconds == 600.0


# ═══════════════════════════════════════════════════
# ۱۴) _load_seen / _save_seen
# ═══════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════
# ۱۵) _load_last_run / _save_last_run
# ═══════════════════════════════════════════════════
class TestLastRun:
    def test_load_last_run(self, collector, tmp_path):
        p = tmp_path / "last.json"
        p.write_text(json.dumps({"last_date": "2026-01-01"}), encoding="utf-8")
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
        assert collector._last_run_date == datetime.now().strftime("%Y-%m-%d")
        assert p.exists()


# ═══════════════════════════════════════════════════
# ۱۶) _restore_state
# ═══════════════════════════════════════════════════
class TestRestoreState:
    @pytest.mark.asyncio
    async def test_no_state_file_returns_false(self, collector, tmp_path):
        collector._state_path = str(tmp_path / "nope.json")
        assert await collector._restore_state() is False

    @pytest.mark.asyncio
    async def test_state_with_cookies_returns_true(self, collector, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"cookies": [{"name": "li_at", "value": "x"}]}), encoding="utf-8")
        collector._state_path = str(p)
        ctx = MagicMock()
        ctx.add_cookies = AsyncMock()
        collector._context = ctx
        assert await collector._restore_state() is True
        ctx.add_cookies.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_state_no_cookies_returns_false(self, collector, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"cookies": []}), encoding="utf-8")
        collector._state_path = str(p)
        collector._context = MagicMock()
        assert await collector._restore_state() is False


# ═══════════════════════════════════════════════════
# ۱۷) check_health
# ═══════════════════════════════════════════════════
class TestCheckHealth:
    @pytest.mark.asyncio
    async def test_connected_true(self, collector):
        collector._connected = True
        assert await collector.check_health() is True

    @pytest.mark.asyncio
    async def test_connected_false(self, collector):
        collector._connected = False
        assert await collector.check_health() is False
