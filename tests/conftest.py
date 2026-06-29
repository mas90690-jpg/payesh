# tests/conftest.py
import os
import sys
import tempfile
import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
    
from core.database import SQLiteDatabase
from analyzers.keyword_finder import get_keyword_finder


@pytest.fixture(scope="session")
def keyword_finder():
    """نمونهٔ مشترک KeywordFinder"""
    return get_keyword_finder()


@pytest.fixture
async def temp_db():
    """دیتابیس SQLite موقتی برای هر تست"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = SQLiteDatabase(db_path)
    await db.init()
    yield db
    await db.close()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def event_loop():
    """حلقه رویداد برای تست‌های async"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def sample_text_with_keyword():
    return "قیمت یاراترا کریستالن چقدر است؟"


@pytest.fixture
def sample_text_without_keyword():
    return "سلام چطوری؟ امروز هوا خوبه."


@pytest.fixture
def sample_message_data(sample_text_with_keyword):
    return {
        "id": "msg_001",
        "platform": "test",
        "text": sample_text_with_keyword,
        "chat_id": "chat_123",
        "chat_name": "گروه فروش",
        "chat_username": "grouptest",
        "chat_link": "https://t.me/grouptest",
        "chat_type": "group",
        "user_id": "user_456",
        "sender_name": "علی",
        "timestamp": datetime.now(),
        "media_type": "text",
        "file_id": None,
        "local_file_path": None,
        "caption": None,
        "reply_to": None
    }