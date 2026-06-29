# tests/test_daily_reporter.py
import pytest
from datetime import datetime
from core.daily_reporter import platform_label, platform_rank, group_by_platform, PLATFORM_ORDER


def test_platform_label():
    """بررسی برچسب‌های پلتفرم"""
    assert platform_label("bale") == "📨 بله"
    assert platform_label("telegram") == "✈️ تلگرام"
    assert platform_label("linkedin") == "💼 لینکدین"
    assert platform_label("unknown") == "📍 unknown"


def test_platform_rank():
    """بررسی رتبه‌بندی پلتفرم‌ها"""
    assert platform_rank("eitaa") == 0
    assert platform_rank("bale") == 1
    assert platform_rank("linkedin") == 6
    assert platform_rank("unknown") > len(PLATFORM_ORDER)


def test_group_by_platform():
    """بررسی گروه‌بندی بر اساس پلتفرم"""
    rows = [
        {"platform": "bale", "text": "msg1"},
        {"platform": "telegram", "text": "msg2"},
        {"platform": "bale", "text": "msg3"},
        {"platform": "linkedin", "text": "msg4"},
    ]
    grouped = group_by_platform(rows)
    # ترتیب باید: bale, telegram, linkedin
    platforms = [p for p, _ in grouped]
    assert platforms[0] == "bale"
    assert platforms[1] == "telegram"
    assert platforms[2] == "linkedin"
    # تعداد bale باید ۲ باشد
    assert len(grouped[0][1]) == 2