"""تست telegram — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def telegram_env():
    from tests.helpers import check_env
    env = check_env("telegram")
    if not env["enabled"]:
        pytest.skip("TELEGRAM_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def telegram_collector(telegram_env):
    from collectors.telegram import TelegramWebCollector
    c = TelegramWebCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("telegram connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def telegram_msgs(telegram_collector):
    return await telegram_collector.fetch_messages()


def test_platform_name(telegram_collector):
    assert telegram_collector.platform_name == "telegram"


def test_fetch_returns_list(telegram_msgs):
    assert isinstance(telegram_msgs, list)


def test_message_required_fields(telegram_msgs):
    if not telegram_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - telegram_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(telegram_msgs):
    if not telegram_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in telegram_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
