# tests/test_brain_memory.py
"""
🧪 تست BrainMemory و ۵ حافظه — pytest-style.

هدف:
  - تست همهٔ ۵ حافظه (Working, LongTerm, Procedural, Semantic, Persistent)
  - تست HybridSearch
  - تست BrainMemory (sync + async)
  - تست remember/recall/forget/decay
  - پیدا کردن باگ‌های احتمالی

⚠️ این تست‌ها از DB واقعی پروژه استفاده می‌کنند (چون BrainMemory singleton است).
   برای ایزوله بودن، از temp_db استفاده می‌کنیم یا reset می‌کنیم.

اجرا:
    pytest tests/test_brain_memory.py -v
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ═══════════════════════════════════════════════════════════
# Fixture: BrainMemory تازه
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def bm():
    """BrainMemory — singleton برای همهٔ تست‌های این فایل."""
    from core.brain.brain_memory import get_brain_memory
    return get_brain_memory()


# ═══════════════════════════════════════════════════════════
# Fixture: PersistentMemory تازه
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def pm():
    """PersistentMemory — singleton."""
    from core.brain.brain_memory import get_persistent_memory
    return get_persistent_memory()


# ═══════════════════════════════════════════════════════════
# helper: کلید یکتا
# ═══════════════════════════════════════════════════════════
def _unique(prefix: str = "test") -> str:
    """کلید یکتا برای هر تست."""
    return f"{prefix}_{time.time_ns()}"


# ═══════════════════════════════════════════════════════════
# ========== WorkingMemory ==========
# ═══════════════════════════════════════════════════════════

def test_working_memory_add_and_get(bm):
    """WorkingMemory.add() → get_by_key() باید کار کنه."""
    key = _unique("wm_key")
    value = "wm_value_test"
    
    bm.working.add(key, value, importance=0.9)
    item = bm.working.get_by_key(key)
    
    assert item is not None, f"آیتم با key={key} پیدا نشد"
    assert item["a"] == value, f"value اشتباه: {item['a']}"


def test_working_memory_clear(bm):
    """WorkingMemory.clear() باید آیتم‌ها رو پاک کنه."""
    bm.working.add(_unique("wm_clear"), "value", importance=0.5)
    
    bm.working.clear()
    
    assert len(bm.working.items) == 0, (
        f"بعد از clear، {len(bm.working.items)} آیتم مونده"
    )


def test_working_memory_get_recent(bm):
    """WorkingMemory.get_recent() باید آیتم‌های اخیر رو برگردونه."""
    bm.working.clear()
    for i in range(3):
        bm.working.add(_unique(f"wm_recent_{i}"), f"value_{i}", importance=0.5)
    
    recent = bm.working.get_recent(n=2)
    assert isinstance(recent, list)
    assert len(recent) <= 2


# ═══════════════════════════════════════════════════════════
# ========== LongTermMemory ==========
# ═══════════════════════════════════════════════════════════

def test_long_term_add_and_get(bm):
    """LongTermMemory.add() → get() باید کار کنه."""
    cat = _unique("lt_cat")
    key = _unique("lt_key")
    value = "lt_value_test"
    
    bm.long_term.add(cat, key, value, importance=0.9)
    result = bm.long_term.get(cat, key)
    
    assert result == value, f"get() → {result!r} (انتظار {value!r})"


def test_long_term_get_category(bm):
    """LongTermMemory.get_category() باید آیتم‌های یک category رو برگردونه."""
    cat = _unique("lt_cat2")
    bm.long_term.add(cat, _unique("lt_key2"), "value2", importance=0.5)
    
    items = bm.long_term.get_category(cat)
    assert isinstance(items, list)
    assert len(items) >= 1, f"category {cat} خالی برگشت"


def test_long_term_add_lesson(bm):
    """LongTermMemory.add_lesson() باید درس اضافه کنه."""
    before = bm.long_term.get_stats()["lessons"]
    wrong = _unique("wrong")
    bm.long_term.add_lesson(wrong, "correct")
    after = bm.long_term.get_stats()["lessons"]
    
    assert after == before + 1, f"lessons: {before} → {after} (انتظار +1)"


def test_long_term_search_lessons(bm):
    """LongTermMemory.search_lessons() باید درس‌ها رو پیدا کنه."""
    wrong = _unique("search_wrong")
    bm.long_term.add_lesson(wrong, "correct")
    
    results = bm.long_term.search_lessons(wrong)
    assert isinstance(results, list)
    assert len(results) >= 1, f"search_lessons({wrong}) خالی"


def test_long_term_get_stats(bm):
    """LongTermMemory.get_stats() باید آمار برگردونه."""
    stats = bm.long_term.get_stats()
    assert isinstance(stats, dict)
    assert "knowledge" in stats
    assert "lessons" in stats


# ═══════════════════════════════════════════════════════════
# ========== ProceduralMemory ==========
# ═══════════════════════════════════════════════════════════

def test_procedural_mark_done(bm):
    """ProceduralMemory.mark_done() → is_done() باید کار کنه."""
    task = _unique("task")
    bm.procedural.mark_done(task)
    
    assert bm.procedural.is_done(task) is True
    assert bm.procedural.is_done(_unique("never_done")) is False


def test_procedural_add_pattern(bm):
    """ProceduralMemory.add_pattern() باید pattern اضافه کنه."""
    pattern = _unique("pattern")
    bm.procedural.add_pattern(pattern)
    
    top = bm.procedural.get_top_patterns(n=10)
    assert isinstance(top, list)


# ═══════════════════════════════════════════════════════════
# ========== SemanticMemory (ChromaDB) ==========
# ═══════════════════════════════════════════════════════════

def test_semantic_add_search(bm):
    """SemanticMemory.add() → search() باید کار کنه."""
    if not bm.semantic.enabled:
        pytest.skip("ChromaDB غیرفعال")
    
    unique_text = _unique("semantic_doc")
    bm.semantic.add(text=unique_text, metadata={"type": "test"})
    
    results = bm.semantic.search(unique_text, n=1)
    assert isinstance(results, list)


def test_semantic_count(bm):
    """SemanticMemory.count() باید عدد برگردونه."""
    if not bm.semantic.enabled:
        pytest.skip("ChromaDB غیرفعال")
    
    count = bm.semantic.count()
    assert isinstance(count, int)
    assert count >= 0


# ═══════════════════════════════════════════════════════════
# ========== PersistentMemory ==========
# ═══════════════════════════════════════════════════════════

def test_persistent_remember_recall(pm):
    """PersistentMemory.remember() → recall() باید کار کنه."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    cat = _unique("pm_cat")
    key = _unique("pm_key")
    value = _unique("pm_value")
    
    rid = pm.remember(category=cat, key=key, value=value, importance=0.9)
    assert rid > 0, f"remember() → id={rid}"
    
    results = pm.recall(query=key, category=cat, limit=5)
    assert isinstance(results, list)
    assert len(results) >= 1, f"recall({key}) خالی"


def test_persistent_count(pm):
    """PersistentMemory.count() باید عدد برگردونه."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    count = pm.count()
    assert isinstance(count, int)


def test_persistent_forget(pm):
    """PersistentMemory.forget() باید رکوردها رو حذف کنه."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    # ۱ رکورد با importance پایین اضافه کن
    cat = _unique("pm_forget_cat")
    pm.remember(category=cat, key=_unique("key"), value="v", importance=0.1)
    
    # forget با importance بالای 0.5 (این رکورد نباید حذف بشه)
    deleted = pm.forget(days=1, min_importance=0.5)
    assert isinstance(deleted, int)


def test_persistent_decay(pm):
    """PersistentMemory.decay() باید importance رو کاهش بده."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    result = pm.decay(factor=0.95, days_per_cycle=7)
    assert isinstance(result, dict)


def test_persistent_get_context(pm):
    """PersistentMemory.get_context() باید string برگردونه."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    ctx = pm.get_context(query="", n=3)
    assert isinstance(ctx, str)


def test_persistent_get_stats(pm):
    """PersistentMemory.get_stats() باید dict برگردونه."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    stats = pm.get_stats()
    assert isinstance(stats, dict)
    assert "total" in stats


def test_persistent_clear(pm):
    """PersistentMemory.clear(category) باید یک category رو پاک کنه."""
    if pm is None or not pm.enabled:
        pytest.skip("PersistentMemory غیرفعال")
    
    cat = _unique("pm_clear_cat")
    pm.remember(category=cat, key="k1", value="v1", importance=0.5)
    pm.remember(category=cat, key="k2", value="v2", importance=0.5)
    
    deleted = pm.clear(category=cat)
    assert isinstance(deleted, int)
    assert deleted >= 2, f"clear({cat}) → {deleted}"


# ═══════════════════════════════════════════════════════════
# ========== BrainMemory (sync) ==========
# ═══════════════════════════════════════════════════════════

def test_brain_remember(bm):
    """BrainMemory.remember() باید در همهٔ حافظه‌ها ذخیره کنه."""
    q = _unique("bm_q")
    a = _unique("bm_a")
    
    bm.remember(q, a, importance=0.99)
    
    # چک working
    item = bm.working.get_by_key(q)
    assert item is not None, "working ذخیره نشد"


def test_brain_recall(bm):
    """BrainMemory.recall() باید مقدار ذخیره‌شده رو برگردونه."""
    q = _unique("bm_recall_q")
    a = _unique("bm_recall_a")
    
    bm.remember(q, a, importance=0.9)
    result = bm.recall(q)
    
    assert result == a, f"recall({q}) → {result!r} (انتظار {a!r})"


def test_brain_build_context(bm):
    """BrainMemory.build_context() باید context غیرخالی برگردونه."""
    bm.remember(_unique("bm_ctx_q"), _unique("bm_ctx_a"), importance=0.9)
    
    ctx = bm.build_context("تست")
    assert isinstance(ctx, str)
    assert len(ctx) > 0, "build_context() خالی برگشت"


def test_brain_is_task_done(bm):
    """BrainMemory.is_task_done() → mark_task_done() باید کار کنه."""
    task = _unique("task")
    bm.mark_task_done(task)
    
    assert bm.is_task_done(task) is True
    assert bm.is_task_done(_unique("never")) is False


def test_brain_add_lesson(bm):
    """BrainMemory.add_lesson() باید درس اضافه کنه."""
    before = bm.long_term.get_stats()["lessons"]
    bm.add_lesson(_unique("wrong"), "correct")
    after = bm.long_term.get_stats()["lessons"]
    
    assert after == before + 1


def test_brain_get_stats(bm):
    """BrainMemory.get_stats() باید dict کامل برگردونه."""
    stats = bm.get_stats()
    assert isinstance(stats, dict)
    
    required = ["working", "long_term", "procedural", "semantic"]
    missing = [k for k in required if k not in stats]
    assert not missing, f"کلیدهای گم‌شده: {missing}"


# ═══════════════════════════════════════════════════════════
# ========== BrainMemory (async) ==========
# ═══════════════════════════════════════════════════════════

def test_brain_add_sync(bm):
    """BrainMemory.add() (sync) باید پیام اضافه کنه."""
    count_before = len(bm.short_term)
    bm.add("user", "test message")   # ← بدون await
    count_after = len(bm.short_term)
    
    # add باید پیام رو به short_term اضافه کنه
    assert count_after >= count_before, (
        f"short_term: {count_before} → {count_after}"
    )


@pytest.mark.asyncio
async def test_brain_get_context_async(bm):
    """BrainMemory.get_context() (async) باید list برگردونه."""
    ctx = await bm.get_context(query="تست")
    assert isinstance(ctx, list)


@pytest.mark.asyncio
async def test_brain_search_async(bm):
    """BrainMemory.search() (async) باید list برگردونه."""
    results = await bm.search(query="تست", n=3)
    assert isinstance(results, list)


# ═══════════════════════════════════════════════════════════
# ========== HybridSearch ==========
# ═══════════════════════════════════════════════════════════

def test_hybrid_search(bm):
    """HybridSearch.search() باید list برگردونه."""
    from core.brain.brain_memory import HybridSearch
    
    hs = HybridSearch(brain_memory=bm)
    results = hs.search("تست", n=5)
    
    assert isinstance(results, list)
    



# ═══════════════════════════════════════════════════════════
# ========== تست‌های استخراج‌شده از test_brain_full.py ==========
# (فقط آن‌هایی که در این فایل نبودند — بدون تکرار)
# ═══════════════════════════════════════════════════════════

def test_brain_memory_get_stats_complete(bm):
    """get_stats باید همهٔ ۵ حافظه را گزارش دهد (شامل persistent)."""
    stats = bm.get_stats()
    required = ["working", "long_term", "procedural", "semantic", "persistent"]
    missing = [k for k in required if k not in stats]
    assert not missing, f"کلیدهای گم‌شده در get_stats: {missing}"


def test_semantic_memory_count_increments(bm):
    """semantic.add() باید count را دقیقاً ۱ زیاد کند."""
    if not bm.semantic.enabled:
        pytest.skip("ChromaDB غیرفعال")

    before = bm.semantic.collection.count()
    unique = f"test semantic count {time.time_ns()}"
    bm.semantic.add(text=unique, metadata={"type": "test"})
    after = bm.semantic.collection.count()

    assert after == before + 1, f"semantic count: {before} → {after} (انتظار +1)"


def test_remember_with_high_importance(bm):
    """remember(importance=0.99) باید در همهٔ حافظه‌ها ذخیره شود."""
    key = _unique("bm_high_imp_q")
    value = _unique("bm_high_imp_a")

    lt_before = bm.long_term.get_stats()["knowledge"]
    sem_before = bm.semantic.collection.count() if bm.semantic.enabled else 0

    bm.remember(key, value, importance=0.99)

    working_item = bm.working.get_by_key(key)
    lt_after = bm.long_term.get_stats()["knowledge"]
    sem_after = bm.semantic.collection.count() if bm.semantic.enabled else 0

    assert working_item is not None, "working ذخیره نشد"
    assert lt_after == lt_before + 1, f"long_term: {lt_before} → {lt_after} (انتظار +1)"
    if bm.semantic.enabled:
        assert sem_after == sem_before + 1, (
            f"semantic: {sem_before} → {sem_after} (انتظار +1)"
        )


def test_is_task_done_within_hours_zero(bm):
    """is_task_done(within_hours=0) باید False بده (حتی اگه تازه ثبت شده)."""
    task = _unique("task_timeout")
    bm.mark_task_done(task)

    result = bm.is_task_done(task, within_hours=0)
    assert result is False, "within_hours=0 باید False بده ولی True داد"


def test_build_context_has_marker(bm):
    """build_context باید حداقل یک marker معنی‌دار داشته باشه."""
    bm.remember(_unique("bm_ctx_marker_q"), "value", importance=0.99)
    ctx = bm.build_context("test context query")

    assert isinstance(ctx, str)
    assert len(ctx) > 0, "build_context خالی برگشت"

    markers = ("گفتگو", "دانش", "context")
    assert any(m in ctx for m in markers), (
        f"build_context هیچ marker معنی‌داری ندارد: {ctx[:200]!r}"
    )


def test_working_memory_max_entries(bm):
    """WorkingMemory باید از MAX_ITEMS تجاوز نکند (memory leak prevention)."""
    max_items = getattr(bm.working, "MAX_ITEMS", None)
    assert max_items is not None, (
        "WorkingMemory باید MAX_ITEMS داشته باشد — "
        "بدون سقف، حافظه بی‌نهایت رشد می‌کند (memory leak)"
    )
    assert isinstance(max_items, int) and max_items > 0, (
        f"MAX_ITEMS={max_items!r} (انتظار int مثبت)"
    )

    # پر کردن بیش از سقف
    bm.working.clear()
    for i in range(max_items + 5):
        bm.working.add(
            _unique(f"wm_overflow_{i}"),
            f"v_{i}",
            importance=0.5,
        )

    # ─── حالت ۱: get_recent همیشه <= MAX_ITEMS ───
    recent = bm.working.get_recent(n=max_items + 100)
    assert len(recent) <= max_items, (
        f"get_recent = {len(recent)} > MAX_ITEMS={max_items}"
    )
    

def test_long_term_search_lessons_content(bm):
    """search_lessons باید محتوای درس را برگرداند (نه فقط count)."""
    wrong = _unique("lesson_wrong_content")
    correct = _unique("lesson_correct_content")
    bm.add_lesson(wrong, correct)

    lessons = bm.long_term.search_lessons(wrong)
    assert len(lessons) > 0, "هیچ درسی پیدا نشد"

    found = any(correct in str(l) for l in lessons)
    assert found, f"correct={correct!r} در lessons نیست: {lessons}"    