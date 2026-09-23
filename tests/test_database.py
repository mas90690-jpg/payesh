# tests/test_database.py
from datetime import datetime

import pytest


@pytest.mark.asyncio
async def test_db_init_and_migrations(temp_db):
    """بررسی ایجاد جداول و مهاجرت‌ها"""
    # بررسی جدول messages
    cursor = await temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    row = await cursor.fetchone()
    assert row is not None, "جدول messages ساخته نشد!"

    # بررسی جدول leads
    cursor = await temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='leads'"
    )
    row = await cursor.fetchone()
    assert row is not None, "جدول leads ساخته نشد!"

    # بررسی جدول last_poll
    cursor = await temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='last_poll'"
    )
    row = await cursor.fetchone()
    assert row is not None, "جدول last_poll ساخته نشد!"


@pytest.mark.asyncio
async def test_save_and_get_message(temp_db):
    """ذخیره و بازیابی یک پیام"""
    msg = {
        "id": "bale_123",
        "platform": "bale",
        "text": "قیمت یاراترا",
        "chat_name": "گروه تست",
        "timestamp": datetime.now(),
        "keywords": "یاراترا",
    }
    await temp_db.save_message(msg)

    cursor = await temp_db.execute("SELECT * FROM messages WHERE id = ?", ("bale_123",))
    row = await cursor.fetchone()
    assert row is not None
    assert row["platform"] == "bale"
    assert "یاراترا" in row["keywords"]
    assert row["chat_name"] == "گروه تست"


@pytest.mark.asyncio
async def test_save_and_get_lead(temp_db):
    """ذخیره و بازیابی یک سرنخ (lead)"""
    # leads.message_id کلید خارجی به messages.id است؛ مثل جریان واقعی، اول پیام را ذخیره کن
    await temp_db.save_message(
        {
            "id": "bale_123",
            "platform": "bale",
            "text": "قیمت یاراترا",
            "chat_name": "گروه فروش",
            "keywords": "یاراترا",
            "timestamp": datetime.now(),
        }
    )
    lead = {
        "id": "lead_001",
        "message_id": "bale_123",
        "platform": "bale",
        "text": "قیمت یاراترا",
        "chat_name": "گروه فروش",
        "keywords": "یاراترا",
        "timestamp": datetime.now(),
    }
    await temp_db.save_lead(lead)

    cursor = await temp_db.execute("SELECT * FROM leads WHERE id = ?", ("lead_001",))
    row = await cursor.fetchone()
    assert row is not None
    assert row["platform"] == "bale"
    assert "یاراترا" in row["keywords"]


@pytest.mark.asyncio
async def test_last_poll(temp_db):
    """بررسی ذخیره‌سازی آخرین زمان پول"""
    now = datetime.now()
    await temp_db.update_last_poll("linkedin", now)

    result = await temp_db.get_last_poll("linkedin")
    assert result is not None
    # بررسی اختلاف زمانی کمتر از ۱ ثانیه
    assert abs((result - now).total_seconds()) < 1


@pytest.mark.asyncio
async def test_chat_offset(temp_db):
    """نشانگرِ per-chat (resume دقیق) درست ذخیره و خوانده می‌شود"""
    assert await temp_db.get_chat_offset("telegram", "g1") is None
    await temp_db.set_chat_offset("telegram", "g1", 1050)
    assert await temp_db.get_chat_offset("telegram", "g1") == "1050"
    # به‌روزرسانی
    await temp_db.set_chat_offset("telegram", "g1", 1099)
    assert await temp_db.get_chat_offset("telegram", "g1") == "1099"
    # چتِ دیگر مستقل است
    assert await temp_db.get_chat_offset("telegram", "g2") is None


@pytest.mark.asyncio
async def test_get_keyword_stats(temp_db):
    """بررسی آمار کلیدواژه‌ها"""
    # چند پیام با کلیدواژه‌های مختلف ذخیره کن
    for i, kw in enumerate(["یاراترا", "رونزا", "یاراترا", "تیو"]):
        msg = {
            "id": f"msg_{i}",
            "platform": "test",
            "text": f"text {i}",
            "chat_name": f"chat_{i}",
            "timestamp": datetime.now(),
            "keywords": kw,
        }
        await temp_db.save_message(msg)

    stats = await temp_db.get_keyword_stats(limit=10)
    # یاراترا باید ۲ بار باشد
    yara_count = next((c for k, c in stats if k == "یاراترا"), 0)
    assert yara_count == 2, f"یاراترا باید ۲ بار باشد، {yara_count} بار است"
