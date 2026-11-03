"""تست facebook."""
from __future__ import annotations
import pytest

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


def test_platform_name(facebook_collector):
    assert facebook_collector.platform_name == "facebook"
