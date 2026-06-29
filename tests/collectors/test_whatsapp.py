# tests/collectors/test_whatsapp.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from collectors.whatsapp import WhatsAppCollector
from tests.helpers.mock_factory import mock_playwright_page, mock_playwright_context


@pytest.mark.asyncio
async def test_whatsapp_connect_success():
    """✅ تست اتصال موفق به واتساپ (نشست موجود)"""
    with patch('collectors.whatsapp.async_playwright') as mock_pw:
        mock_pw_instance = AsyncMock()
        mock_pw.start = AsyncMock(return_value=mock_pw_instance)
        
        mock_context = mock_playwright_context()
        mock_page = mock_playwright_page()
        mock_context.pages = [mock_page]
        mock_pw_instance.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)

        collector = WhatsAppCollector(phone="+98912000000", headless=True)
        collector._is_logged_in = AsyncMock(return_value=True)
        
        result = await collector.connect()
        
        assert collector.platform_name == "whatsapp"


@pytest.mark.asyncio
async def test_whatsapp_scrape_chat_with_keyword(temp_db, keyword_finder):
    """✅ تست اسکرپ چت واتساپ با کلیدواژه"""
    collector = WhatsAppCollector(phone="+98912000000")
    collector._kw = keyword_finder
    
    mock_page = AsyncMock()
    
    # شبیه‌سازی حباب پیام با کلیدواژه (query_selector=None تا کد از inner_text حباب بخواند)
    mock_bubble = AsyncMock()
    mock_bubble.inner_text = AsyncMock(return_value="قیمت یاراترا")
    mock_bubble.get_attribute = AsyncMock(return_value="false_chat@g.us_123")
    mock_bubble.query_selector = AsyncMock(return_value=None)

    # شبیه‌سازی حباب بی‌کلیدواژه
    mock_bubble2 = AsyncMock()
    mock_bubble2.inner_text = AsyncMock(return_value="سلام")
    mock_bubble2.get_attribute = AsyncMock(return_value="false_chat@g.us_124")
    mock_bubble2.query_selector = AsyncMock(return_value=None)
    
    mock_page.query_selector_all = AsyncMock(return_value=[mock_bubble, mock_bubble2])
    mock_page.query_selector = AsyncMock(return_value=None)
    
    collector._page = mock_page
    collector._seen_ids = set()
    collector._messages_queue = []
    
    # شبیه‌سازی نام چت
    collector._get_open_chat_title = AsyncMock(return_value="گروه تست")
    
    await collector._scrape_open_chat()
    
    # فقط پیام کلیدواژه‌دار باید در صف باشد
    assert len(collector._messages_queue) == 1
    assert collector._messages_queue[0]["platform"] == "whatsapp"
    assert "یاراترا" in collector._messages_queue[0]["text"]