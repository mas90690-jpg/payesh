# tests/test_lead_scorer.py
"""
🧪 تست LeadScorer — pytest-style با assert واقعی.

اجرا:
    pytest tests/test_lead_scorer.py -v
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def scorer():
    """LeadScorer برای همهٔ تست‌های این فایل."""
    from analyzers.lead_scorer import get_lead_scorer
    return get_lead_scorer()


def test_singleton():
    """get_lead_scorer() باید همیشه یک instance بده."""
    from analyzers.lead_scorer import get_lead_scorer
    a = get_lead_scorer()
    b = get_lead_scorer()
    assert a is b


def test_score_empty_text(scorer):
    """score("") باید ۰ باشه (بدون کلیدواژه)."""
    score, level, signals = scorer.score("", has_keyword=False)
    assert score == 0, f"متن خالی: score={score} (انتظار 0)"
    assert level == "سرد"
    assert signals == []


def test_score_none(scorer):
    """score(None) باید ۰ باشه."""
    score, level, signals = scorer.score(None, has_keyword=False)
    assert score == 0
    assert level == "سرد"


def test_score_no_keyword(scorer):
    """has_keyword=False باید base=0 باشه."""
    score, level, signals = scorer.score(
        "متن تصادفی بدون هیچ سیگنال",
        has_keyword=False,
    )
    assert score == 0
    assert level == "سرد"


def test_score_with_keyword(scorer):
    """has_keyword=True باید حداقل ۱۰ باشه."""
    score, level, signals = scorer.score(
        "متن تصادفی",
        has_keyword=True,
    )
    assert score >= 10, f"با کلیدواژه: score={score} (انتظار ≥10)"


def test_score_price_signal(scorer):
    """'قیمت چنده' باید سیگنال price بده."""
    score, level, signals = scorer.score(
        "قیمت کود مایع چنده؟",
        has_keyword=True,
    )
    assert score > 10, f"با قیمت: score={score} (انتظار >10)"
    assert any("قیمت" in s for s in signals)


def test_score_urgency_signal(scorer):
    """'فوری' باید سیگنال urgency بده."""
    score_urgent, _, _ = scorer.score("فوری میخوام", has_keyword=True)
    score_calm, _, _ = scorer.score("میخوام", has_keyword=True)
    assert score_urgent >= score_calm


def test_level_thresholds(scorer):
    """level باید بر اساس آستانه‌ها (60/30) درست باشه."""
    hot_score, hot_level, _ = scorer.score(
        "قیمت کود مایع فوری میخرم چنده موجوده؟",
        has_keyword=True,
    )
    cold_score, cold_level, _ = scorer.score(
        "سلام",
        has_keyword=True,
    )
    assert hot_level in ("داغ", "گرم")
    assert cold_level == "سرد"


def test_score_clipped(scorer):
    """score باید همیشه در بازهٔ 0..100 باشه."""
    score, _, _ = scorer.score(
        "قیمت کود مایع فوری میخرم چنده موجوده تخفیف",
        has_keyword=True,
    )
    assert 0 <= score <= 100


@pytest.mark.asyncio
async def test_analyze_async(scorer):
    """analyze() باید AnalyzedMessage بگیره و با score/category پر برگردونه."""
    from datetime import datetime
    from core.kernel.models import AnalyzedMessage
    
    if not hasattr(scorer, "analyze"):
        pytest.skip("analyze() وجود ندارد")
    
    msg = AnalyzedMessage(
        platform="test",
        platform_id="test_analyze_001",
        chat_id="chat_1",
        chat_name="تست",
        sender_name="تستر",
        text="قیمت کود مایع چنده؟",
        time=datetime.now(),
        keywords=["کود مایع"],
    )
    
    result = await scorer.analyze(msg)
    
    assert result is msg
    assert result.score is not None
    assert result.score > 0
    assert result.category in ("داغ", "گرم", "سرد")
    assert "intent_signals" in result.metadata


def test_get_stats(scorer):
    """get_stats() باید dict با اطلاعات برگردونه."""
    stats = scorer.get_stats()
    assert isinstance(stats, dict)
    assert len(stats) > 0


def test_reset(scorer):
    """reset_lead_scorer() باید instance جدید بده."""
    from analyzers.lead_scorer import get_lead_scorer, reset_lead_scorer
    old = get_lead_scorer()
    reset_lead_scorer()
    new = get_lead_scorer()
    assert new is not old


def test_score_idempotent(scorer):
    """score() روی یه متن باید همیشه همون نتیجه رو بده."""
    text = "قیمت کود مایع چنده؟"
    r1 = scorer.score(text, has_keyword=True)
    r2 = scorer.score(text, has_keyword=True)
    assert r1 == r2


def test_multiple_signals(scorer):
    """متن با چند سیگنال باید score بیشتری بده."""
    score_one, _, _ = scorer.score("قیمت چنده؟", has_keyword=True)
    score_multi, _, _ = scorer.score(
        "قیمت چنده؟ فوری میخرم موجوده؟",
        has_keyword=True,
    )
    assert score_multi >= score_one


def test_score_none_has_keyword(scorer):
    """has_keyword=None باید مثل False رفتار کنه."""
    score, level, _ = scorer.score("متن", has_keyword=None)
    assert score == 0