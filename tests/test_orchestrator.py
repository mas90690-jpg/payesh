# tests/test_orchestrator.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
from core.orchestrator import Orchestrator
from core.event_bus import EventBus


@pytest.mark.asyncio
async def test_orchestrator_initial_fetch(temp_db):
    """✅ تست دریافت اولیهٔ پیام‌ها توسط orchestrator"""
    # ساخت یک کلکتور Mock
    mock_collector = MagicMock()
    mock_collector.platform_name = "test"
    mock_collector.connect = AsyncMock(return_value=True)
    mock_collector.fetch_history = AsyncMock(
        return_value=[
            {
                "id": "1",
                "platform": "test",
                "text": "پیام قدیمی",
                "timestamp": datetime.now(),
            }
        ]
    )
    mock_collector.fetch_messages = AsyncMock(return_value=[])
    mock_collector.disconnect = AsyncMock()

    event_bus = EventBus()
    orchestrator = Orchestrator([mock_collector], event_bus, temp_db)

    # اجرای start (که شامل initial_fetch هم هست)
    # برای تست فقط متد _initial_fetch را صدا می‌زنیم
    await orchestrator._initial_fetch(mock_collector)

    # بررسی اینکه connect و fetch_history صدا زده شده
    mock_collector.connect.assert_called_once()
    mock_collector.fetch_history.assert_called_once()
