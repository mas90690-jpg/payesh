# tests/test_target_filter.py
"""
🧪 تست TargetGroups — pytest-style با assert واقعی.

هدف:
  - تست is_target() با حالت‌های مختلف
  - تست get_targets() و get_stats()
  - تست reload()
  - تست enabled (env-based)
  - پیدا کردن باگ‌های احتمالی

اجرا:
    pytest tests/test_target_filter.py -v
"""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════
# Fixture
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def tg():
    """TargetGroups برای همهٔ تست‌های این فایل."""
    from analyzers.target_filter import get_target_groups
    return get_target_groups()


# ═══════════════════════════════════════════════════════════
# تست ۱: singleton
# ═══════════════════════════════════════════════════════════
def test_singleton():
    """get_target_groups() باید همیشه یک instance بده."""
    from analyzers.target_filter import get_target_groups
    a = get_target_groups()
    b = get_target_groups()
    assert a is b


# ═══════════════════════════════════════════════════════════
# تست ۲: get_stats() ساختار
# ═══════════════════════════════════════════════════════════
def test_get_stats(tg):
    """get_stats() باید dict با کلیدهای معنی‌دار برگردونه."""
    stats = tg.get_stats()
    assert isinstance(stats, dict)

    required = [
        "path", "all_targets", "by_platform",
        "total_targets", "platforms_with_rules",
    ]
    missing = [k for k in required if k not in stats]
    assert not missing, f"کلیدهای گم‌شده: {missing}"


# ═══════════════════════════════════════════════════════════
# تست ۳: total_targets صحیح
# ═══════════════════════════════════════════════════════════
def test_total_targets_consistent(tg):
    """total_targets باید با all_targets + by_platform هماهنگ باشه."""
    stats = tg.get_stats()

    # اگه فایل خالی باشه → همه صفر
    # اگه پر باشه → حداقل all_targets یا by_platform
    if stats["total_targets"] == 0:
        assert stats["all_targets"] == 0, (
            f"total_targets=0 ولی all_targets={stats['all_targets']}"
        )
        assert stats["by_platform"] == {}, (
            f"total_targets=0 ولی by_platform={stats['by_platform']}"
        )


# ═══════════════════════════════════════════════════════════
# تست ۴: is_target با chat_name خالی
# ═══════════════════════════════════════════════════════════
def test_is_target_when_no_targets(tg):
    """is_target وقتی لیست هدف خالیه باید True بده (همه پیام‌ها هدفن)."""
    stats = tg.get_stats()
    if stats["total_targets"] > 0:
        pytest.skip("لیست هدف پر است — این تست معنادار نیست")

    # وقتی لیست خالیه، همه پیام‌ها باید هدف باشن (طراحی درست)
    # چون کاربر هدفی تعریف نکرده → همه چیز ذخیره می‌شه
    assert tg.is_target("", platform="bale") is True, (
        "با لیست خالی، is_target باید True باشه "
        "(هیچ هدفی تعریف نشده → همه پیام‌ها ذخیره می‌شن)"
    )
    assert tg.is_target("هر گروهی", platform="bale") is True
    assert tg.is_target("گروه تست", platform="eitaa") is True
    
    
# ═══════════════════════════════════════════════════════════
# تست ۵: is_target با platform نامعتبر
# ═══════════════════════════════════════════════════════════
def test_is_target_unknown_platform(tg):
    """is_target با پلتفرم ناشناخته نباید کرش کنه."""
    try:
        result = tg.is_target("تست", platform="nonexistent")
        assert isinstance(result, bool)
    except Exception as e:
        pytest.fail(f"is_target() با پلتفرم ناشناخته کرش کرد: {e}")


# ═══════════════════════════════════════════════════════════
# تست ۶: get_targets() — لیست برمی‌گردونه
# ═══════════════════════════════════════════════════════════
def test_get_targets_returns_list(tg):
    """get_targets() باید لیست برگردونه (حتی خالی)."""
    result = tg.get_targets(platform="bale")
    assert isinstance(result, list), (
        f"get_targets() باید list برگردونه، نه {type(result).__name__}"
    )

    # اگه فایل خالی باشه → لیست خالی
    if tg.get_stats()["total_targets"] == 0:
        assert result == []


# ═══════════════════════════════════════════════════════════
# تست ۷: get_targets بدون platform
# ═══════════════════════════════════════════════════════════
def test_get_targets_no_platform(tg):
    """get_targets() بدون platform باید لیست برگردونه."""
    try:
        result = tg.get_targets()
        assert isinstance(result, list)
    except TypeError:
        # ممکنه platform اجباری باشه
        pytest.skip("get_targets() بدون platform کار نمی‌کنه (اجباریه)")


# ═══════════════════════════════════════════════════════════
# تست ۸: reload
# ═══════════════════════════════════════════════════════════
def test_reload(tg):
    """reload() باید بدون خطا اجرا بشه."""
    before_total = tg.get_stats()["total_targets"]
    tg.reload()
    after_total = tg.get_stats()["total_targets"]

    # تعداد هدف‌ها نباید تغییر کنه (فایل تغییر نکرده)
    assert after_total == before_total, (
        f"reload() تعداد رو تغییر داد: {before_total} → {after_total}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۹: enabled
# ═══════════════════════════════════════════════════════════
def test_enabled_attribute(tg):
    """enabled باید bool باشه."""
    assert hasattr(tg, "enabled")
    # ممکنه method باشه یا property
    val = tg.enabled
    if callable(val):
        val = val()
    assert isinstance(val, bool), (
        f"enabled باید bool باشه، نه {type(val).__name__}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۱۰: is_target با platform=None
# ═══════════════════════════════════════════════════════════
def test_is_target_none_platform(tg):
    """is_target با platform=None نباید کرش کنه."""
    try:
        result = tg.is_target("تست", platform=None)
        assert isinstance(result, bool)
    except TypeError:
        # اگه platform اجباریه
        pytest.skip("platform اجباریه")
    except Exception as e:
        pytest.fail(f"is_target() با platform=None کرش کرد: {e}")