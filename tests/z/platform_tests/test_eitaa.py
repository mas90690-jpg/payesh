"""تست eitaa — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def eitaa_env():
    from tests.helpers import check_env
    env = check_env("eitaa")
    if not env["enabled"]:
        pytest.skip("EITAA_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def eitaa_collector(eitaa_env):
    from collectors.eitaa import EitaaCollector
    c = EitaaCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("eitaa connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def eitaa_msgs(eitaa_collector):
    return await eitaa_collector.fetch_messages()


def test_platform_name(eitaa_collector):
    assert eitaa_collector.platform_name == "eitaa"


def test_fetch_returns_list(eitaa_msgs):
    assert isinstance(eitaa_msgs, list)


def test_message_required_fields(eitaa_msgs):
    if not eitaa_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {"id", "platform", "text", "chat_name", "timestamp"}
    missing = required - eitaa_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {missing}"


def test_no_duplicate_ids(eitaa_msgs):
    if not eitaa_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in eitaa_msgs]
    assert len(ids) == len(set(ids)), f"{len(ids)-len(set(ids))} id تکراری"
