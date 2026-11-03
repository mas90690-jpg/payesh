"""تست whatsapp — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def whatsapp_env():
    from tests.helpers import check_env
    env = check_env("whatsapp")
    if not env["enabled"]:
        pytest.skip("WHATSAPP_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def whatsapp_collector(whatsapp_env):
    from collectors.whatsapp import WhatsAppCollector
    c = WhatsAppCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("whatsapp connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def whatsapp_msgs(whatsapp_collector):
    return await whatsapp_collector.fetch_messages()


def test_platform_name(whatsapp_collector):
    assert whatsapp_collector.platform_name == "whatsapp"


def test_fetch_returns_list(whatsapp_msgs):
    assert isinstance(whatsapp_msgs, list)


def test_message_required_fields(whatsapp_msgs):
    if not whatsapp_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - whatsapp_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(whatsapp_msgs):
    if not whatsapp_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in whatsapp_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
