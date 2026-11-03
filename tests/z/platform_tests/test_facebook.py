"""تست facebook — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def facebook_env():
    from tests.helpers import check_env
    env = check_env("facebook")
    if not env["enabled"]:
        pytest.skip("FACEBOOK_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def facebook_collector(facebook_env):
    from collectors.facebook import FacebookCollector
    c = FacebookCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("facebook connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def facebook_msgs(facebook_collector):
    return await facebook_collector.fetch_messages()


def test_platform_name(facebook_collector):
    assert facebook_collector.platform_name == "facebook"


def test_fetch_returns_list(facebook_msgs):
    assert isinstance(facebook_msgs, list)


def test_message_required_fields(facebook_msgs):
    if not facebook_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - facebook_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(facebook_msgs):
    if not facebook_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in facebook_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
