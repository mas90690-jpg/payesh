"""تست whatsapp."""
from __future__ import annotations
import pytest

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def whatsapp_env():
    from tests.helpers import check_env
    env = check_env("whatsapp")
    if not env["enabled"]:
        pytest.skip("WHATSAPP_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def whatsapp_collector(whatsapp_env):
    from collectors.whatsapp import WhatsAppCollector
    c = WhatsAppCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("whatsapp connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


def test_platform_name(whatsapp_collector):
    assert whatsapp_collector.platform_name == "whatsapp"
