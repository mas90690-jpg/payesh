"""تست rubika — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def rubika_env():
    from tests.helpers import check_env
    env = check_env("rubika")
    if not env["enabled"]:
        pytest.skip("RUBIKA_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def rubika_collector(rubika_env):
    from collectors.rubika import RubikaCollector
    c = RubikaCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("rubika connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def rubika_msgs(rubika_collector):
    return await rubika_collector.fetch_messages()


def test_platform_name(rubika_collector):
    assert rubika_collector.platform_name == "rubika"


def test_fetch_returns_list(rubika_msgs):
    assert isinstance(rubika_msgs, list)


def test_message_required_fields(rubika_msgs):
    if not rubika_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - rubika_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(rubika_msgs):
    if not rubika_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in rubika_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
