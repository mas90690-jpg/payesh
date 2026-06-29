# tests/collectors/test_telegram.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from collectors.telegram import TelegramCollector
from tests.helpers.mock_factory import mock_telegram_message


@pytest.mark.asyncio
async def test_telegram_connect_success():
    """✅ تست اتصال موفق به تلگرام"""
    with patch('collectors.telegram.TelegramClient') as MockClient:
        mock_client = AsyncMock()
        mock_client.start = AsyncMock()
        # .on() در telethon یک دکوراتورِ همگام برمی‌گرداند، نه coroutine
        mock_client.on = MagicMock(return_value=lambda f: f)
        MockClient.return_value = mock_client

        collector = TelegramCollector(
            api_id="123456",
            api_hash="hash",
            phone="+98912000000"
        )
        result = await collector.connect()

        assert result is True
        assert collector.is_connected is True


@pytest.mark.asyncio
async def test_telegram_fetch_messages_with_keyword(temp_db, keyword_finder):
    """✅ تست دریافت پیام کلیدواژه‌دار از تلگرام"""
    collector = TelegramCollector(api_id="123456", api_hash="hash", phone="+98912000000")
    collector._connected = True
    collector._messages_queue = []

    # شبیه‌سازی پیام
    mock_msg = mock_telegram_message("قیمت یاراترا چنده؟")
    
    # شبیه‌سازی _process_message
    async def mock_process(msg):
        return {
            "id": "123",
            "platform": "telegram",
            "text": "قیمت یاراترا چنده؟",
            "chat_id": "123",
            "chat_name": "گروه تست",
            "chat_type": "group",
            "timestamp": datetime.now()
        }
    collector._process_message = mock_process
    collector._messages_queue = [await mock_process(mock_msg)]

    messages = await collector.fetch_messages(since=datetime.now() - timedelta(hours=1))
    
    assert len(messages) >= 1
    assert messages[0]["platform"] == "telegram"
    assert "یاراترا" in messages[0]["text"]


@pytest.mark.asyncio
async def test_telegram_fetch_history(temp_db):
    """✅ تست خواندن تاریخچهٔ تلگرام"""
    with patch('collectors.telegram.TelegramClient') as MockClient:
        mock_client = AsyncMock()
        mock_dialog = AsyncMock()
        mock_dialog.is_group = True
        mock_dialog.is_channel = False
        mock_dialog.entity = "entity"
        
        # شبیه‌سازی iter_dialogs
        async def mock_iter_dialogs():
            yield mock_dialog
        mock_client.iter_dialogs = mock_iter_dialogs
        
        # شبیه‌سازی iter_messages
        mock_msg = mock_telegram_message("پیام قدیمی")
        async def mock_iter_messages(*args, **kwargs):
            yield mock_msg
        mock_client.iter_messages = mock_iter_messages

        collector = TelegramCollector(api_id="123456", api_hash="hash", phone="+98912000000")
        collector._client = mock_client
        collector._connected = True
        
        # شبیه‌سازی _process_message
        async def mock_process(msg):
            return {
                "id": "456",
                "platform": "telegram",
                "text": "پیام قدیمی",
                "chat_name": "گروه تست",
                "timestamp": datetime.now()
            }
        collector._process_message = mock_process

        history = await collector.fetch_history(since=datetime.now() - timedelta(days=7))
        
        assert len(history) >= 1