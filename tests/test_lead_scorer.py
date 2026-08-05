# tests/test_lead_scorer.py
from analyzers.lead_scorer import get_lead_scorer


def test_hot_lead_high_score():
    s, level, signals = get_lead_scorer().score(
        "قیمت یاراترا چنده؟ فوری برای خرید عمده میخوام", has_keyword=True
    )
    assert s >= 60, f"باید داغ باشد، شد {s}"
    assert level == "داغ"
    assert "قیمت‌پرسی" in signals and "خرید" in signals


def test_cold_lead_low_score():
    s, level, _ = get_lead_scorer().score("یاراترا محصول خوبیه", has_keyword=True)
    assert s < 30
    assert level == "سرد"


def test_english_intent():
    s, level, signals = get_lead_scorer().score(
        "looking to buy fertilizer, please send price quote", has_keyword=True
    )
    assert s >= 30
    assert "خرید" in signals or "قیمت‌پرسی" in signals


def test_no_keyword_base_zero():
    s, level, _ = get_lead_scorer().score("قیمت؟", has_keyword=False)
    # بدون نام محصول، پایهٔ ۱۰ را نمی‌گیرد ولی سیگنالِ قیمت را می‌گیرد
    assert s == 25
