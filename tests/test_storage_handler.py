# tests/test_storage_handler.py
from datetime import datetime

import pytest

from core.kernel.event_bus import Event
from handlers.storage_handler import StorageHandler


@pytest.mark.asyncio
async def test_handler_drops_non_keyword(temp_db):
    """بررسی اینکه پیام‌های بدون کلیدواژه ذخیره نمی‌شوند"""
    handler = StorageHandler(db=temp_db)

    event = Event(
        name="message.received",
        data={
            "id": "test_001",
            "platform": "test",
            "text": "سلام چطوری؟ این یک پیام معمولی است",
            "chat_name": "گروه تست",
            "timestamp": datetime.now(),
        },
    )
    await handler.handle(event)

    # بررسی اینکه در دیتابیس نیست
    cursor = await temp_db.execute("SELECT * FROM messages WHERE id = ?", ("test_001",))
    row = await cursor.fetchone()
    assert row is None, "پیام بی‌ربط نباید ذخیره می‌شد!"

    # آمار باید نشان دهد که یک پیام dropped شده
    assert handler.stats["dropped"] == 1
    assert handler.stats["messages_with_keywords"] == 0


@pytest.mark.asyncio
async def test_admin_dm_not_stored_as_lead(temp_db):
    """تفکیکِ منطق: پیامِ خصوصیِ مدیر (با اینکه کلیدواژه دارد) نباید سرنخ شود."""
    handler = StorageHandler(db=temp_db)
    handler._admin_ids = {"42"}
    handler._admin_platform = "bale"
    event = Event(
        name="message.received",
        data={
            "id": "adm_1",
            "platform": "bale",
            "chat_type": "private",
            "user_id": "42",
            "text": "قیمت یاراترا را اضافه کن",
            "chat_name": "PV",
            "timestamp": datetime.now(),
        },
    )
    await handler.handle(event)
    cursor = await temp_db.execute(
        "SELECT * FROM messages WHERE id = ?", ("bale_adm_1",)
    )
    assert await cursor.fetchone() is None, "پیام مدیر نباید به‌عنوان سرنخ ذخیره می‌شد!"


@pytest.mark.asyncio
async def test_handler_saves_keyword_message(temp_db):
    """بررسی اینکه پیام کلیدواژه‌دار در messages ذخیره می‌شود"""
    handler = StorageHandler(db=temp_db)

    event = Event(
        name="message.received",
        data={
            "id": "test_002",
            "platform": "test",
            "text": "قیمت یاراترا چقدر است؟",
            "chat_name": "گروه فروش",
            "timestamp": datetime.now(),
        },
    )
    await handler.handle(event)

    # بررسی در messages
    cursor = await temp_db.execute("SELECT * FROM messages WHERE id = ?", ("test_002",))
    row = await cursor.fetchone()
    assert row is not None
    assert "یاراترا" in row["keywords"]


@pytest.mark.asyncio
async def test_handler_creates_lead(temp_db):
    """بررسی اینکه پیام کلیدواژه‌دار در leads هم ذخیره می‌شود"""
    handler = StorageHandler(db=temp_db)

    event = Event(
        name="message.received",
        data={
            "id": "test_003",
            "platform": "test",
            "text": "خرید رونزا نکست",
            "chat_name": "گروه فروش",
            "timestamp": datetime.now(),
        },
    )
    await handler.handle(event)

    # بررسی در leads
    cursor = await temp_db.execute(
        "SELECT * FROM leads WHERE message_id = ?", ("test_003",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert "رونزا" in row["keywords"] or "رونزا نکست" in row["keywords"]


@pytest.mark.asyncio
async def test_handler_handles_media(temp_db):
    """بررسی اینکه پیام با رسانه به‌درستی ذخیره می‌شود"""
    handler = StorageHandler(db=temp_db)

    event = Event(
        name="message.received",
        data={
            "id": "test_004",
            "platform": "test",
            "text": "عکس یاراترا",
            "chat_name": "گروه فروش",
            "timestamp": datetime.now(),
            "media_type": "photo",
            "file_id": "file_123",
        },
    )
    await handler.handle(event)

    cursor = await temp_db.execute("SELECT * FROM messages WHERE id = ?", ("test_004",))
    row = await cursor.fetchone()
    assert row is not None
    assert row["media_type"] == "photo"
    assert row["file_id"] == "file_123"
    assert handler.stats["media_messages"] == 1
