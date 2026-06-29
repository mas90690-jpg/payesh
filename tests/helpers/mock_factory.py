# tests/helpers/mock_factory.py
from unittest.mock import AsyncMock, MagicMock


def mock_bale_message(text: str, chat_id: str = "chat_123", msg_id: int = 123):
    """ساخت یک پیام Mock برای بله"""
    msg = MagicMock()
    msg.message_id = msg_id
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.sender_id = "user_456"
    msg.date = MagicMock()
    return msg


def mock_rubika_message(text: str, object_guid: str = "chat_123"):
    """ساخت یک پیام Mock برای روبیکا"""
    msg = MagicMock()
    msg.object_guid = object_guid
    msg.text = text
    msg.message_id = 123
    msg.is_group = True
    msg.is_channel = False
    return msg


def mock_telegram_message(text: str, chat_id: int = 123, msg_id: int = 123):
    """ساخت یک پیام Mock برای تلگرام"""
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.title = "گروه تست"
    msg.sender = MagicMock()
    msg.sender.id = 456
    msg.sender.first_name = "علی"
    msg.date = MagicMock()
    msg.date.timestamp.return_value = 1700000000
    msg.photo = None
    msg.video = None
    msg.audio = None
    msg.document = None
    msg.voice = None
    return msg


def mock_playwright_page():
    """ساخت یک صفحهٔ Mock برای Playwright"""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html>mock</html>")
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    return page


def mock_playwright_context():
    """ساخت یک context Mock برای Playwright"""
    context = AsyncMock()
    context.close = AsyncMock()
    context.pages = []
    context.new_page = AsyncMock(return_value=mock_playwright_page())
    return context