"""تست eitaa."""
from __future__ import annotations
import pytest

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


def test_platform_name(eitaa_collector):
    assert eitaa_collector.platform_name == "eitaa"
