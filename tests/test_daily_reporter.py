# tests/test_daily_reporter.py
from core.services.daily_reporter import (
    PLATFORM_ORDER,
    group_by_platform,
    platform_label,
    platform_rank,
    platform_summary_line,
)


def test_platform_label():
    """✅ تست برچسب‌های پلتفرم"""
    assert platform_label("bale") == "📨 بله"
    assert platform_label("telegram") == "✈️ تلگرام"
    assert platform_label("linkedin") == "💼 لینکدین"
    assert platform_label("eitaa") == "📱 ایتا"
    assert platform_label("rubika") == "🟣 روبیکا"
    assert platform_label("whatsapp") == "🟢 واتساپ"
    assert platform_label("web") == "🌐 وب"
    assert platform_label("unknown") == "📍 unknown"


def test_platform_rank():
    """✅ تست رتبه‌بندی پلتفرم‌ها"""
    assert platform_rank("eitaa") == 0
    assert platform_rank("bale") == 1
    assert platform_rank("rubika") == 2
    assert platform_rank("telegram") == 3
    assert platform_rank("whatsapp") == 4
    assert platform_rank("web") == 5
    assert platform_rank("linkedin") == 6
    assert platform_rank("unknown") > len(PLATFORM_ORDER)


def test_group_by_platform():
    """✅ تست گروه‌بندی بر اساس پلتفرم با ترتیب ثابت"""
    rows = [
        {"platform": "bale", "text": "msg1"},
        {"platform": "telegram", "text": "msg2"},
        {"platform": "bale", "text": "msg3"},
        {"platform": "linkedin", "text": "msg4"},
        {"platform": "eitaa", "text": "msg5"},
    ]
    grouped = group_by_platform(rows)
    platforms = [p for p, _ in grouped]
    # ترتیب باید: eitaa, bale, telegram, linkedin
    assert platforms[0] == "eitaa"
    assert platforms[1] == "bale"
    assert platforms[2] == "telegram"
    assert platforms[3] == "linkedin"
    # تعداد bale باید ۲ باشد
    assert len(grouped[1][1]) == 2  # index 1 = bale


def test_platform_summary_line():
    """✅ تست خط خلاصهٔ پلتفرم‌ها"""
    rows = [
        {"platform": "bale"},
        {"platform": "bale"},
        {"platform": "telegram"},
        {"platform": "linkedin"},
        {"platform": "whatsapp"},
    ]
    summary = platform_summary_line(rows)
    # باید شامل bale (۲)، telegram (۱)، linkedin (۱)، whatsapp (۱) باشد
    assert "بله 2" in summary
    assert "تلگرام 1" in summary
    assert "لینکدین 1" in summary
    assert "واتساپ 1" in summary
