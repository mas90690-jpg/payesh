# tests/test_keyword_finder.py
"""
🧪 تست keyword_finder — pytest-style با assert واقعی.

هدف:
  - تست match(), has_match() با حالات مختلف
  - تست reload(), _collect(), _signature
  - پیدا کردن باگ‌های احتمالی

اجرا:
    pytest tests/test_keyword_finder.py -v
"""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════
# Fixture: keyword_finder
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def kf():
    """یک instance از KeywordFinder برای همهٔ تست‌های این فایل."""
    from analyzers.keyword_finder import get_keyword_finder
    return get_keyword_finder()


# ═══════════════════════════════════════════════════════════
# تست ۱: singleton
# ═══════════════════════════════════════════════════════════
def test_singleton():
    """get_keyword_finder() باید همیشه یک instance بده."""
    from analyzers.keyword_finder import get_keyword_finder
    a = get_keyword_finder()
    b = get_keyword_finder()
    assert a is b, "singleton نشت — دو instance متفاوت"


# ═══════════════════════════════════════════════════════════
# تست ۲: keywords count
# ═══════════════════════════════════════════════════════════
def test_keywords_loaded(kf):
    """keywordها باید از فایل‌ها لود بشن."""
    assert len(kf.keywords) >= 200, (
        f"فقط {len(kf.keywords)} کلیدواژه لود شد "
        f"(انتظار ≥ 200)"
    )
    assert len(kf.keywords) < 5000, (
        f"{len(kf.keywords)} کلیدواژه مشکوکه"
    )


# ═══════════════════════════════════════════════════════════
# تست ۳: match با کلیدواژهٔ واقعی
# ═══════════════════════════════════════════════════════════
def test_match_real_keyword(kf):
    """match با کلیدواژهٔ واقعی باید non-empty باشه."""
    # یک کلیدواژهٔ واقعی پیدا کن
    real_kw = None
    for k in kf.keywords:
        if len(k) >= 4 and any('\u0600' <= c <= '\u06ff' for c in k):
            real_kw = k
            break
    
    if real_kw is None:
        pytest.skip("هیچ کلیدواژهٔ فارسی ≥ ۴ حرف نیست")
    
    matches = kf.match(f"سلام، {real_kw} موجوده؟")
    assert len(matches) > 0, (
        f"match('{real_kw}') خالی برگشت"
    )
    assert any(real_kw in m or m in real_kw for m in matches), (
        f"کلیدواژهٔ «{real_kw}» در match نتایج نیست: {matches}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۴: match با متن خالی
# ═══════════════════════════════════════════════════════════
def test_match_empty(kf):
    """match('') باید empty باشه."""
    assert kf.match("") == []
    assert kf.match(None) == []
    assert kf.match("   ") == []


# ═══════════════════════════════════════════════════════════
# تست ۵: has_match
# ═══════════════════════════════════════════════════════════
def test_has_match(kf):
    """has_match باید True/False درست بده."""
    real_kw = None
    for k in kf.keywords:
        if len(k) >= 4:
            real_kw = k
            break
    
    if real_kw is None:
        pytest.skip("هیچ کلیدواژه ≥ ۴ حرف نیست")
    
    assert kf.has_match(f"{real_kw} موجوده") is True
    assert kf.has_match("xyz123_qwerty_هیچ_چیز_نیست") is False
    assert kf.has_match("") is False
    assert kf.has_match(None) is False


# ═══════════════════════════════════════════════════════════
# تست ۶: reload
# ═══════════════════════════════════════════════════════════
def test_reload(kf):
    """reload() باید keywordها رو دوباره لود کنه."""
    before = len(kf.keywords)
    kf.reload()
    after = len(kf.keywords)
    assert after == before, (
        f"reload() تعداد رو تغییر داد: {before} → {after}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۷: get_stats
# ═══════════════════════════════════════════════════════════
def test_get_stats(kf):
    """get_stats() باید دیکشنری با کلیدهای درست بده."""
    stats = kf.get_stats()
    assert isinstance(stats, dict)
    assert "total_keywords" in stats or "keywords" in stats or "count" in stats
    # حداقل یه کلید معنی‌دار


# ═══════════════════════════════════════════════════════════
# تست ۸: cache invalidation
# ═══════════════════════════════════════════════════════════
def test_reload_invalidates_cache(kf):
    """reload() باید match رو با keywordهای جدید کار کنه."""
    real_kw = None
    for k in kf.keywords:
        if len(k) >= 4:
            real_kw = k
            break
    
    if real_kw is None:
        pytest.skip()
    
    matches_before = kf.match(f"{real_kw} تست")
    kf.reload()
    matches_after = kf.match(f"{real_kw} تست")
    
    # تعداد match باید همون باشه (چون keywordها تغییر نکردن)
    assert len(matches_after) == len(matches_before)