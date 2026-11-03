"""تست rubika."""
from __future__ import annotations
import pytest

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


def test_platform_name(rubika_collector):
    assert rubika_collector.platform_name == "rubika"
