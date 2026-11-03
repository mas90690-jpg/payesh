"""ساخت تست‌های platform."""
from pathlib import Path

TEMPLATE = '''"""تست {name}."""
from __future__ import annotations
import pytest

pytestmark = [pytest.mark.platform, pytest.mark.slow]


@pytest.fixture(scope="session")
def {name}_env():
    from tests.helpers import check_env
    env = check_env("{name}")
    if not env["enabled"]:
        pytest.skip("{NAME}_ENABLED=false")
    return env


@pytest.fixture(scope="session")
async def {name}_collector({name}_env):
    from collectors.{module} import {klass}
    c = {klass}(headless=True, poll_seconds=999)
    ok = await c.connect()
    if not ok:
        pytest.skip("{name} connect failed")
    yield c
    try:
        await c.disconnect()
    except Exception:
        pass


def test_platform_name({name}_collector):
    assert {name}_collector.platform_name == "{name}"
'''

PLATFORMS = [
    ("facebook", "facebook", "FacebookCollector"),
    ("instagram", "instagram", "InstagramCollector"),
    ("linkedin", "linkedin", "LinkedInCollector"),
    ("whatsapp", "whatsapp", "WhatsAppCollector"),
    ("telegram", "telegram", "TelegramWebCollector"),
    ("eitaa", "eitaa", "EitaaCollector"),
    ("rubika", "rubika", "RubikaCollector"),
]

Path("tests/platform").mkdir(parents=True, exist_ok=True)

for name, module, klass in PLATFORMS:
    path = Path("tests/platform") / f"test_{name}.py"
    if path.exists():
        print(f"⏭️  {path} هست — skip")
        continue
    path.write_text(
        TEMPLATE.format(name=name, NAME=name.upper(), module=module, klass=klass),
        encoding="utf-8",
    )
    print(f"✅ {path}")

print("\nتمام شد!")
