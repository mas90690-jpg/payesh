"""تست linkedin — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def linkedin_env():
    from tests.helpers import check_env
    env = check_env("linkedin")
    if not env["enabled"]:
        pytest.skip("LINKEDIN_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def linkedin_collector(linkedin_env):
    from collectors.linkedin import LinkedInCollector
    c = LinkedInCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("linkedin connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def linkedin_msgs(linkedin_collector):
    return await linkedin_collector.fetch_messages()


def test_platform_name(linkedin_collector):
    assert linkedin_collector.platform_name == "linkedin"


def test_fetch_returns_list(linkedin_msgs):
    assert isinstance(linkedin_msgs, list)


def test_message_required_fields(linkedin_msgs):
    if not linkedin_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - linkedin_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(linkedin_msgs):
    if not linkedin_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in linkedin_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
