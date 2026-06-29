# tests/collectors/test_rubika.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from collectors.rubika import RubikaCollector


@pytest.mark.asyncio
async def test_rubika_connect_success():
    """✅ تست اتصال موفق به روبیکا"""
    with patch('collectors.rubika.Client') as MockClient:
        mock_client = AsyncMock()
        mock_client.start = AsyncMock()
        mock_client.run = AsyncMock()
        # on_message_updates() یک دکوراتورِ همگام برمی‌گرداند، نه coroutine
        mock_client.on_message_updates = MagicMock(return_value=lambda f: f)
        MockClient.return_value = mock_client

        collector = RubikaCollector(phone="09120000000", password="pass")
        result = await collector.connect()

        assert result is True
        assert collector.is_connected is True


@pytest.mark.asyncio
async def test_rubika_fetch_messages(temp_db, keyword_finder):
    """✅ تست دریافت پیام از روبیکا"""
    collector = RubikaCollector(phone="09120000000")
    collector._connected = True
    collector._messages_queue = []

    # شبیه‌سازی پیام با کلیدواژه
    mock_msg = MagicMock()
    mock_msg.object_guid = "chat_123"
    mock_msg.text = "خرید یاراترا"
    mock_msg.message_id = 123
    mock_msg.is_group = True

    # شبیه‌سازی _to_unified
    async def mock_to_unified(msg):
        return {
            "id": "123",
            "platform": "rubika",
            "text": "خرید یاراترا",
            "chat_id": "chat_123",
            "chat_name": "گروه فروش",
            "chat_type": "group",
            "timestamp": datetime.now()
        }
    collector._to_unified = mock_to_unified
    collector._messages_queue = [await mock_to_unified(mock_msg)]

    messages = await collector.fetch_messages(since=datetime.now())
    
    assert len(messages) == 1
    assert messages[0]["platform"] == "rubika"
    assert "یاراترا" in messages[0]["text"]