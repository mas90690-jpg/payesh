# tests/collectors/test_bale.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from collectors.bale import BaleCollector
from tests.helpers.mock_factory import mock_bale_message


@pytest.mark.asyncio
async def test_bale_connect_success():
    """✅ تست اتصال موفق به بله"""
    with patch('collectors.bale.Client') as MockClient:
        mock_client = AsyncMock()
        mock_client.run = AsyncMock()
        MockClient.return_value = mock_client

        collector = BaleCollector(phone="09120000000", password="pass")
        result = await collector.connect()

        assert result is True
        assert collector.is_connected is True
        assert collector._connected is True


@pytest.mark.asyncio
async def test_bale_fetch_messages_with_keyword(temp_db, keyword_finder):
    """✅ تست دریافت پیام کلیدواژه‌دار از بله"""
    collector = BaleCollector(phone="09120000000", password="pass")
    collector._connected = True
    collector._client = AsyncMock()
    collector._messages_queue = []

    # شبیه‌سازی پیام با کلیدواژه
    mock_msg = mock_bale_message("قیمت یاراترا چنده؟")
    
    # شبیه‌سازی _build_unified
    async def mock_build(msg):
        return {
            "id": "123",
            "platform": "bale",
            "text": "قیمت یاراترا چنده؟",
            "chat_id": "chat_123",
            "chat_name": "گروه فروش",
            "chat_type": "group",
            "timestamp": datetime.now()
        }
    collector._build_unified = mock_build
    collector._messages_queue = [await mock_build(mock_msg)]

    messages = await collector.fetch_messages(since=datetime.now() - timedelta(hours=1))
    
    assert len(messages) >= 1
    assert messages[0]["platform"] == "bale"
    assert "یاراترا" in messages[0]["text"]


@pytest.mark.asyncio
async def test_bale_fetch_messages_non_keyword(temp_db, keyword_finder):
    """✅ تست نادیده‌گرفتن پیام بی‌کلیدواژه از بله"""
    collector = BaleCollector(phone="09120000000", password="pass")
    collector._connected = True
    collector._messages_queue = []

    # شبیه‌سازی پیام بدون کلیدواژه
    async def mock_build(msg):
        return {
            "id": "124",
            "platform": "bale",
            "text": "سلام چطوری؟",
            "chat_id": "chat_123",
            "chat_name": "گروه تست",
            "timestamp": datetime.now()
        }
    collector._build_unified = mock_build
    collector._messages_queue = [await mock_build(None)]

    messages = await collector.fetch_messages(since=datetime.now() - timedelta(hours=1))
    
    # پیام باید دریافت شود ولی بعداً در storage_handler فیلتر می‌شود
    assert len(messages) == 1
    # در اینجا فقط دریافت را تست می‌کنیم، فیلتر در handler است