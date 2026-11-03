"""تست telegram."""
from __future__ import annotations
import pytest

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def telegram_env():
    from tests.helpers import check_env
    env = check_env("telegram")
    if not env["enabled"]:
        pytest.skip("TELEGRAM_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def telegram_collector(telegram_env):
    from collectors.telegram import TelegramWebCollector
    c = TelegramWebCollector(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("telegram connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


def test_platform_name(telegram_collector):
    assert telegram_collector.platform_name == "telegram"
