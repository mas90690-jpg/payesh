"""تست instagram."""
from __future__ import annotations
import pytest

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


def test_platform_name(instagram_collector):
    assert instagram_collector.platform_name == "instagram"
