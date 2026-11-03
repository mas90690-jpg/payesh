"""کامل کردن ۷ فایل platform_tests از ۱ تست به ۴ تست."""
from pathlib import Path

TEMPLATE = '''"""تست {name} — pytest-style."""
from __future__ import annotations
import pytest

from tests.platform_tests._helpers import is_system_chat

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


@pytest.fixture(scope="session")
async def {name}_msgs({name}_collector):
    return await {name}_collector.fetch_messages()


def test_platform_name({name}_collector):
    assert {name}_collector.platform_name == "{name}"


def test_fetch_returns_list({name}_msgs):
    assert isinstance({name}_msgs, list)


def test_message_required_fields({name}_msgs):
    if not {name}_msgs:
        pytest.skip("هیچ پیامی نیست")
    required = {{"id", "platform", "text", "chat_name", "timestamp"}}
    missing = required - {name}_msgs[0].keys()
    assert not missing, f"فیلدهای گم‌شده: {{missing}}"


def test_no_duplicate_ids({name}_msgs):
    if not {name}_msgs:
        pytest.skip("هیچ پیامی نیست")
    ids = [str(m.get("id", "")) for m in {name}_msgs]
    assert len(ids) == len(set(ids)), f"{{len(ids)-len(set(ids))}} id تکراری"
'''

PLATFORMS = [
    ("eitaa", "eitaa", "EitaaCollector"),
    ("rubika", "rubika", "RubikaCollector"),
    ("telegram", "telegram", "TelegramWebCollector"),
    ("whatsapp", "whatsapp", "WhatsAppCollector"),
    ("linkedin", "linkedin", "LinkedInCollector"),
    ("instagram", "instagram", "InstagramCollector"),
    ("facebook", "facebook", "FacebookCollector"),
]

folder = Path("tests/platform_tests")
folder.mkdir(parents=True, exist_ok=True)

for name, module, klass in PLATFORMS:
    path = folder / f"test_{name}.py"
    content = TEMPLATE.format(
        name=name,
        NAME=name.upper(),
        module=module,
        klass=klass,
    )
    path.write_text(content, encoding="utf-8")
    print(f"✅ {path}")

print("\nتمام شد! ۷ فایل کامل شدند.")
