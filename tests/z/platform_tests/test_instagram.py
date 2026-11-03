"""تست instagram — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def instagram_env():
    from tests.helpers import check_env
    env = check_env("instagram")
    if not env["enabled"]:
        pytest.skip("INSTAGRAM_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def instagram_collector(instagram_env):
    from collectors.instagram import InstagramCollector
    c = InstagramCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("instagram connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def instagram_msgs(instagram_collector):
    return await instagram_collector.fetch_messages()


def test_platform_name(instagram_collector):
    assert instagram_collector.platform_name == "instagram"


def test_fetch_returns_list(instagram_msgs):
    assert isinstance(instagram_msgs, list)


def test_message_required_fields(instagram_msgs):
    if not instagram_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - instagram_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(instagram_msgs):
    if not instagram_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in instagram_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
