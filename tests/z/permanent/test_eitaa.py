# tests/permanent/test_eitaa.py
"""تست کامل collectors/eitaa.py — توابع خالص + متدهای async با mock."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.eitaa import (
    EitaaCollector,
    _clean_text,
    _is_invalid_text,
)


# ═══════════════════════════════════════════════
# _is_invalid_text
# ═══════════════════════════════════════════════
class TestIsInvalidText:
    def test_empty(self):
        assert _is_invalid_text("") is True

    def test_whitespace(self):
        assert _is_invalid_text("   \n\t  ") is True

    def test_none_like(self):
        assert _is_invalid_text(None) is True

    def test_clock_time(self):
        assert _is_invalid_text("12:34") is True

    def test_clock_time_persian(self):
        assert _is_invalid_text("۱۲:۳۴") is True

    def test_only_number(self):
        assert _is_invalid_text("12345") is True

    def test_only_number_persian(self):
        assert _is_invalid_text("۱۲۳۴۵") is True

    def test_valid_text(self):
        assert _is_invalid_text("سلام دنیا") is False

    def test_valid_text_with_number(self):
        assert _is_invalid_text("قیمت 100 تومان") is False

    def test_clock_and_number(self):
        assert _is_invalid_text("12:34\n5678") is True


# ═══════════════════════════════════════════════
# _clean_text
# ═══════════════════════════════════════════════
class TestCleanText:
    def test_empty(self):
        assert _clean_text("") == ""

    def test_none(self):
        assert _clean_text(None) == ""

    def test_plain(self):
        assert _clean_text("سلام") == "سلام"

    def test_pua_removed(self):
        assert _clean_text("\ue900سلام\ue901") == "سلام"

    def test_pua_range_removed(self):
        assert _clean_text("\ue123متن\ue456") == "متن"

    def test_trailing_clock_removed(self):
        assert _clean_text("سلام\n12:34") == "سلام"

    def test_trailing_number_removed(self):
        assert _clean_text("سلام\n1234") == "سلام"

    def test_trailing_emoji_removed(self):
        assert _clean_text("سلام\n😀😀") == "سلام"

    def test_inline_clock_removed(self):
        assert _clean_text("سلام 12:34") == "سلام"

    def test_multiline_kept(self):
        assert _clean_text("خط اول\nخط دوم") == "خط اول\nخط دوم"

    def test_trailing_whitespace(self):
        assert _clean_text("سلام   \n\n") == "سلام"


# ═══════════════════════════════════════════════
# _is_system_name
# ═══════════════════════════════════════════════
class TestIsSystemName:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    def test_empty(self, collector):
        assert collector._is_system_name("") is False

    def test_none(self, collector):
        assert collector._is_system_name(None) is False

    def test_whitespace(self, collector):
        assert collector._is_system_name("   ") is False

    def test_saved_messages_fa(self, collector):
        assert collector._is_system_name("پیام‌های ذخیره شده") is True

    def test_saved_messages_en(self, collector):
        assert collector._is_system_name("Saved Messages") is True

    def test_eitaa(self, collector):
        assert collector._is_system_name("ایتا") is True

    def test_prefix_channel_created(self, collector):
        assert collector._is_system_name("کانال ایجاد شد") is True

    def test_prefix_group_created(self, collector):
        assert collector._is_system_name("گروه ایجاد شد") is True

    def test_normal_name(self, collector):
        assert collector._is_system_name("گروه خبری") is False

    def test_normal_name_en(self, collector):
        assert collector._is_system_name("News Channel") is False


# ═══════════════════════════════════════════════
# _extract_peer_id
# ═══════════════════════════════════════════════
class TestExtractPeerId:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    @pytest.mark.asyncio
    async def test_from_data_peer_id(self, collector):
        item = AsyncMock()
        item.get_attribute = AsyncMock(return_value="12345")
        assert await collector._extract_peer_id(item) == "12345"

    @pytest.mark.asyncio
    async def test_from_href(self, collector):
        item = AsyncMock()

        async def get_attr(name):
            if name == "data-peer-id":
                return None
            if name == "href":
                return "https://web.eitaa.com/#98765"
            return None

        item.get_attribute = AsyncMock(side_effect=get_attr)
        assert await collector._extract_peer_id(item) == "98765"

    @pytest.mark.asyncio
    async def test_none(self, collector):
        item = AsyncMock()
        item.get_attribute = AsyncMock(return_value=None)
        assert await collector._extract_peer_id(item) == ""

    @pytest.mark.asyncio
    async def test_empty_string(self, collector):
        item = AsyncMock()
        item.get_attribute = AsyncMock(return_value="  ")
        assert await collector._extract_peer_id(item) == ""

    @pytest.mark.asyncio
    async def test_exception(self, collector):
        item = AsyncMock()
        item.get_attribute = AsyncMock(side_effect=Exception("boom"))
        assert await collector._extract_peer_id(item) == ""


# ═══════════════════════════════════════════════
# _extract_title
# ═══════════════════════════════════════════════
class TestExtractTitle:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    @pytest.mark.asyncio
    async def test_from_peer_title(self, collector):
        item = AsyncMock()
        el = AsyncMock()
        el.inner_text = AsyncMock(return_value="گروه تست")
        item.query_selector = AsyncMock(return_value=el)
        assert await collector._extract_title(item) == "گروه تست"

    @pytest.mark.asyncio
    async def test_multiline_first_line(self, collector):
        item = AsyncMock()
        el = AsyncMock()
        el.inner_text = AsyncMock(return_value="عنوان\nزیرعنوان")
        item.query_selector = AsyncMock(return_value=el)
        assert await collector._extract_title(item) == "عنوان"

    @pytest.mark.asyncio
    async def test_none(self, collector):
        item = AsyncMock()
        item.query_selector = AsyncMock(return_value=None)
        assert await collector._extract_title(item) == ""

    @pytest.mark.asyncio
    async def test_too_long(self, collector):
        item = AsyncMock()
        el = AsyncMock()
        el.inner_text = AsyncMock(return_value="x" * 100)
        item.query_selector = AsyncMock(return_value=el)
        assert await collector._extract_title(item) == ""

    @pytest.mark.asyncio
    async def test_exception(self, collector):
        item = AsyncMock()
        item.query_selector = AsyncMock(side_effect=Exception("boom"))
        assert await collector._extract_title(item) == ""


# ═══════════════════════════════════════════════
# _extract_sender
# ═══════════════════════════════════════════════
class TestExtractSender:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    @pytest.mark.asyncio
    async def test_is_out(self, collector):
        msg = AsyncMock()
        msg.get_attribute = AsyncMock(return_value="bubble is-out")
        assert await collector._extract_sender(msg, "چت") == "چت"

    @pytest.mark.asyncio
    async def test_private(self, collector):
        msg = AsyncMock()
        msg.get_attribute = AsyncMock(return_value="bubble")
        assert await collector._extract_sender(msg, "چت") == "چت"

    @pytest.mark.asyncio
    async def test_group_with_sender(self, collector):
        msg = AsyncMock()
        msg.get_attribute = AsyncMock(return_value="bubble is-group")
        el = AsyncMock()
        el.inner_text = AsyncMock(return_value="علی")
        msg.query_selector = AsyncMock(return_value=el)
        assert await collector._extract_sender(msg, "گروه") == "علی"

    @pytest.mark.asyncio
    async def test_group_no_sender(self, collector):
        msg = AsyncMock()
        msg.get_attribute = AsyncMock(return_value="bubble is-group")
        msg.query_selector = AsyncMock(return_value=None)
        assert await collector._extract_sender(msg, "گروه") == "گروه"

    @pytest.mark.asyncio
    async def test_exception(self, collector):
        msg = AsyncMock()
        msg.get_attribute = AsyncMock(side_effect=Exception("boom"))
        assert await collector._extract_sender(msg, "چت") == "چت"


# ═══════════════════════════════════════════════
# _is_logged_in
# ═══════════════════════════════════════════════
class TestIsLoggedIn:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    @pytest.mark.asyncio
    async def test_login_marker(self, collector):
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=MagicMock())
        collector._page = page
        assert await collector._is_logged_in() is False

    @pytest.mark.asyncio
    async def test_chatlist_marker(self, collector):
        page = AsyncMock()
        calls = {"n": 0}

        async def qs(sel):
            calls["n"] += 1
            if calls["n"] <= 2:
                return None
            return MagicMock()

        page.query_selector = AsyncMock(side_effect=qs)
        collector._page = page
        assert await collector._is_logged_in() is True

    @pytest.mark.asyncio
    async def test_nothing(self, collector):
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        collector._page = page
        assert await collector._is_logged_in() is False

    @pytest.mark.asyncio
    async def test_exception(self, collector):
        page = AsyncMock()
        page.query_selector = AsyncMock(side_effect=Exception("boom"))
        collector._page = page
        assert await collector._is_logged_in() is False


# ═══════════════════════════════════════════════
# fetch_messages (بدون اتصال)
# ═══════════════════════════════════════════════
class TestFetchMessages:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    @pytest.mark.asyncio
    async def test_not_connected(self, collector):
        assert await collector.fetch_messages() == []

    @pytest.mark.asyncio
    async def test_no_page(self, collector):
        collector._connected = True
        collector._page = None
        assert await collector.fetch_messages() == []

    @pytest.mark.asyncio
    async def test_since_param(self, collector):
        collector._connected = True
        collector._page = MagicMock()
        collector._collect_chats = AsyncMock(return_value=[])
        since = datetime.now() - timedelta(hours=1)
        result = await collector.fetch_messages(since=since)
        assert result == []


# ═══════════════════════════════════════════════
# send_message / check_health
# ═══════════════════════════════════════════════
class TestMisc:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    @pytest.mark.asyncio
    async def test_send_message_false(self, collector):
        assert await collector.send_message("1", "private", "hi") is False

    @pytest.mark.asyncio
    async def test_check_health(self, collector):
        assert await collector.check_health() is False
        collector._connected = True
        assert await collector.check_health() is True

    def test_properties(self, collector):
        assert collector.platform_name == "eitaa"
        assert collector.supports_history is True
        assert collector.supports_websocket is False
        assert collector.poll_interval_seconds > 0


# ═══════════════════════════════════════════════
# state / seen
# ═══════════════════════════════════════════════
class TestState:
    @pytest.fixture
    def collector(self):
        with patch("collectors.eitaa.get_target_groups"), \
             patch("collectors.eitaa.get_keyword_finder"):
            return EitaaCollector()

    def test_save_and_load_state(self, collector, tmp_path):
        p = tmp_path / "state.json"
        collector._state_path = str(p)
        collector._save_state()
        assert p.exists()
        assert collector._load_first_run() is True

    def test_load_last_run_at(self, collector, tmp_path):
        p = tmp_path / "state.json"
        collector._state_path = str(p)
        collector._save_state()
        assert collector._load_last_run_at() is not None

    def test_load_missing(self, collector, tmp_path):
        collector._state_path = str(tmp_path / "nope.json")
        assert collector._load_first_run() is False
        assert collector._load_last_run_at() is None

    def test_save_and_load_seen(self, collector, tmp_path):
        p = tmp_path / "seen.json"
        collector._seen_path = str(p)
        collector._seen_ids = {"a", "b"}
        collector._save_seen()
        collector._seen_ids = set()
        collector._load_seen()
        assert collector._seen_ids == {"a", "b"}

    def test_load_seen_missing(self, collector, tmp_path):
        collector._seen_path = str(tmp_path / "nope.json")
        collector._load_seen()
        assert collector._seen_ids == set()
