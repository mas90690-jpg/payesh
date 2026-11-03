"""تست linkedin."""
from __future__ import annotations
import pytest

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


def test_platform_name(linkedin_collector):
    assert linkedin_collector.platform_name == "linkedin"
