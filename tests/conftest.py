"""تنظیمات مشترک تست‌ها."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# ─── ★★★ این خط حیاتی است ★★★ ───
# .env را در os.environ بارگذاری کن
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)


# ─── markerها ───
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: تست نیاز به اینترنت/پلتفرم دارد"
    )
    config.addinivalue_line(
        "markers", "slow: تست کند (>5 ثانیه)"       
    )
    config.option.capture = "no"


# ─── fixture مشترک ───
@pytest.fixture(scope="session")
def keyword_finder():
    """یک نمونه کلیدواژه برای همه تست‌ها."""
    from analyzers.keyword_finder import get_keyword_finder
    return get_keyword_finder()


@pytest.fixture
def bale_collector():
    """collector بله — با phone/password."""
    from collectors.bale import BaleCollector

    print(f"\n🔑 BALE_PHONE = {os.getenv('BALE_PHONE')}")
    print(f"🔑 BALE_PASSWORD = {'(خالی)' if not os.getenv('BALE_PASSWORD') else '***'}")

    return BaleCollector(
        phone=os.getenv("BALE_PHONE") or None,
        password=os.getenv("BALE_PASSWORD") or None,
        headless=True,
        max_chats=1,
        messages_per_chat=5,
    )


@pytest.fixture
def eitaa_collector():
    from collectors.eitaa import EitaaCollector
    return EitaaCollector(
        phone=os.getenv("EITAA_PHONE") or None,
        password=os.getenv("EITAA_PASSWORD") or None,
        headless=True,
        max_chats=1,
        messages_per_chat=5,
    )


@pytest.fixture
def rubika_collector():
    from collectors.rubika import RubikaCollector
    return RubikaCollector(
        phone=os.getenv("RUBIKA_PHONE") or None,
        password=os.getenv("RUBIKA_PASSWORD") or None,
    )


@pytest.fixture
def web_collector():
    from collectors.web import WebCollector
    return WebCollector()