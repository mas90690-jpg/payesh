# tests/test_priority_finder.py
"""
🧪 تست PriorityFinder — pytest-style با assert واقعی.

هدف:
  - تست highest_tier() با T1/T2/T3/T0
  - تست tier_bonus() با هر tier
  - تست tier_label()
  - تست find_products() و find_brands()
  - تست get_stats() و reset()
  - پیدا کردن باگ‌های احتمالی

اجرا:
    pytest tests/test_priority_finder.py -v
"""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════
# Fixture
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def pf():
    """PriorityFinder برای همهٔ تست‌های این فایل."""
    from analyzers.priority_finder import get_priority_finder
    return get_priority_finder()


# ═══════════════════════════════════════════════════════════
# تست ۱: singleton
# ═══════════════════════════════════════════════════════════
def test_singleton():
    """get_priority_finder() باید همیشه یک instance بده."""
    from analyzers.priority_finder import get_priority_finder
    a = get_priority_finder()
    b = get_priority_finder()
    assert a is b


# ═══════════════════════════════════════════════════════════
# تست ۲: get_stats() — ساختار درست
# ═══════════════════════════════════════════════════════════
def test_get_stats(pf):
    """get_stats() باید dict با کلیدهای معنی‌دار برگردونه."""
    stats = pf.get_stats()
    assert isinstance(stats, dict)
    
    # کلیدهای اصلی
    required = [
        "t1_words", "t2_words", "t3_words",
        "pattern_t1", "pattern_t2", "pattern_t3",
        "tier_bonus",
    ]
    missing = [k for k in required if k not in stats]
    assert not missing, f"کلیدهای گم‌شده: {missing}"
    
    # tier_bonus ساختار درست
    assert isinstance(stats["tier_bonus"], dict)
    assert stats["tier_bonus"]["T1"] == 40
    assert stats["tier_bonus"]["T2"] == 15
    assert stats["tier_bonus"]["T3"] == 5
    assert stats["tier_bonus"]["T0"] == 0


# ═══════════════════════════════════════════════════════════
# تست ۳: T1 پر است
# ═══════════════════════════════════════════════════════════
def test_t1_loaded(pf):
    """T1 باید حداقل ۲۰۰ کلمه داشته باشه."""
    stats = pf.get_stats()
    assert stats["t1_words"] >= 200, (
        f"T1 فقط {stats['t1_words']} کلمه (انتظار ≥200)"
    )


# ═══════════════════════════════════════════════════════════
# تست ۴: highest_tier با کلمهٔ T1
# ═══════════════════════════════════════════════════════════
def test_highest_tier_t1(pf):
    """متن با کلمهٔ T1 باید 'T1' برگردونه."""
    # attribute درست: t1_words
    t1_words = getattr(pf, "t1_words", None)
    if not t1_words:
        pytest.skip("t1_words در دسترس نیست")
    
    # اولین کلمهٔ T1
    sample_word = t1_words[0] if isinstance(t1_words, list) else list(t1_words)[0]
    tier = pf.highest_tier(f"سلام، {sample_word} موجوده؟")
    assert tier == "T1", (
        f"highest_tier('{sample_word}') = {tier} (انتظار T1)"
    )
    

# ═══════════════════════════════════════════════════════════
# تست ۵: highest_tier با متن خالی
# ═══════════════════════════════════════════════════════════
def test_highest_tier_empty(pf):
    """متن خالی → T0."""
    assert pf.highest_tier("") == "T0"
    assert pf.highest_tier(None) == "T0"


# ═══════════════════════════════════════════════════════════
# تست ۶: highest_tier بدون کلمهٔ کلیدی
# ═══════════════════════════════════════════════════════════
def test_highest_tier_no_match(pf):
    """متن بدون کلمهٔ کلیدی → T0."""
    assert pf.highest_tier("سلام حالت چطوره؟") == "T0"


# ═══════════════════════════════════════════════════════════
# تست ۷: tier_bonus
# ═══════════════════════════════════════════════════════════
def test_tier_bonus(pf):
    """tier_bonus باید برای هر tier مقدار درست بده."""
    assert pf.tier_bonus("T1") == 40
    assert pf.tier_bonus("T2") == 15
    assert pf.tier_bonus("T3") == 5
    assert pf.tier_bonus("T0") == 0
    assert pf.tier_bonus("نامعتبر") == 0  # fallback


# ═══════════════════════════════════════════════════════════
# تست ۸: tier_label
# ═══════════════════════════════════════════════════════════
def test_tier_label(pf):
    """tier_label باید برچسب فارسی برگردونه."""
    # حداقل غیرخالی باشه
    for tier in ("T1", "T2", "T3", "T0"):
        label = pf.tier_label(tier)
        assert isinstance(label, str)
        assert len(label) > 0, f"tier_label('{tier}') خالی"


# ═══════════════════════════════════════════════════════════
# تست ۹: find_products
# ═══════════════════════════════════════════════════════════
def test_find_products(pf):
    """find_products باید لیست برگردونه (حتی خالی)."""
    result = pf.find_products("کود مایع یاراترا")
    assert isinstance(result, list)
    # اگه محصولی پیدا شد، همه رشته باشن
    for p in result:
        assert isinstance(p, str)


# ═══════════════════════════════════════════════════════════
# تست ۱۰: find_brands
# ═══════════════════════════════════════════════════════════
def test_find_brands(pf):
    """find_brands باید لیست برگردونه (حتی خالی)."""
    result = pf.find_brands("کود مایع یاراترا")
    assert isinstance(result, list)
    for b in result:
        assert isinstance(b, str)


# ═══════════════════════════════════════════════════════════
# تست ۱۱: analyze (async) — AnalyzedMessage
# ═══════════════════════════════════════════════════════════
def test_analyze_returns_dict(pf):
    """analyze() باید dict با tier/products/brands برگردونه."""
    if not hasattr(pf, "analyze"):
        pytest.skip("analyze() وجود ندارد")
    
    result = pf.analyze("کود مایع یاراترا موجوده")
    
    # analyze() یه dict برمی‌گردونه
    assert isinstance(result, dict), (
        f"analyze() باید dict برگردونه، نه {type(result).__name__}"
    )
    
    # کلیدهای اصلی
    required = [
        "tier", "tier_label", "products", "brands",
        "products_str", "brands_str",
    ]
    missing = [k for k in required if k not in result]
    assert not missing, f"کلیدهای گم‌شده در analyze(): {missing}"
    
    # tier باید از T0..T3 باشه
    assert result["tier"] in ("T0", "T1", "T2", "T3"), (
        f"tier={result['tier']} نامعتبر"
    )
    
    # tier_label غیرخالی
    assert isinstance(result["tier_label"], str)
    assert len(result["tier_label"]) > 0
    
    # products و brands لیست
    assert isinstance(result["products"], list)
    assert isinstance(result["brands"], list)
    
    # products_str و brands_str string
    assert isinstance(result["products_str"], str)
    assert isinstance(result["brands_str"], str)


def test_analyze_empty(pf):
    """analyze('') باید dict معتبر برگردونه."""
    if not hasattr(pf, "analyze"):
        pytest.skip("analyze() وجود ندارد")
    
    result = pf.analyze("")
    assert isinstance(result, dict)
    assert result["tier"] == "T0"
    assert result["products"] == []
    assert result["brands"] == []
    

# ═══════════════════════════════════════════════════════════
# تست ۱۲: reset
# ═══════════════════════════════════════════════════════════
def test_reset(pf):
    """reset() باید instance رو پاک کنه (یا state رو)."""
    if not hasattr(pf, "reset"):
        pytest.skip("reset() وجود ندارد")
    # فقط صدا بزن — نباید کرش کنه
    pf.reset()


# ═══════════════════════════════════════════════════════════
# تست ۱۳: highest_tier idempotent
# ═══════════════════════════════════════════════════════════
def test_highest_tier_idempotent(pf):
    """highest_tier() باید idempotent باشه."""
    text = "سلام"
    tier1 = pf.highest_tier(text)
    tier2 = pf.highest_tier(text)
    assert tier1 == tier2