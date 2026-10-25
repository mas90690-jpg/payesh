"""
🧠 تست فوق دقیق مغز — صفر تا صد

بررسی می‌کند:
1. BrainMemory ساخته شده
2. 4 حافظه کار می‌کنند
3. Working Memory ذخیره/بازیابی
4. Long-Term Memory ذخیره/بازیابی
5. Procedural Memory کار می‌کند
6. Semantic Memory (ChromaDB) کار می‌کند
7. AIAssistant به BrainMemory متصل
8. دانش پروژه ذخیره شده
9. build_context کار می‌کند
10. remember/recall کار می‌کند
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def print_test(num: int, title: str, ok: bool, detail: str = ""):
    mark = "✅" if ok else "❌"
    print(f"\n  [{num}] {mark} {title}")
    if detail:
        for line in detail.split("\n"):
            print(f"      {line}")


async def main():
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 12 + "🧠 تست فوق دقیق مغز — صفر تا صد" + " " * 23 + "║")
    print("╚" + "═" * 68 + "╝")

    results: dict[str, bool] = {}

    # ══════════════════════════════════════════════════════
    # ۱. import
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  بخش ۱: import و ساخت")
    print("=" * 70)

    try:
        from core.brain.brain_memory import (
            get_brain_memory,
        )
        print_test(1, "import BrainMemory", True)
        results["1_import"] = True
    except Exception as e:
        print_test(1, "import BrainMemory", False, f"{type(e).__name__}: {e}")
        results["1_import"] = False
        return results

    # ══════════════════════════════════════════════════════
    # ۲. ساخت BrainMemory (Singleton)
    # ══════════════════════════════════════════════════════
    t0 = time.monotonic()
    try:
        bm = get_brain_memory()
        ms = (time.monotonic() - t0) * 1000
        stats = bm.get_stats()
        print_test(
            2, f"ساخت BrainMemory ({ms:.0f}ms)", True,
            f"working={stats['working']} | "
            f"knowledge={stats['long_term']['knowledge']} | "
            f"lessons={stats['long_term']['lessons']} | "
            f"tasks={stats['procedural']['tasks']} | "
            f"semantic={'✅' if stats['semantic']['enabled'] else '❌'} "
            f"({stats['semantic']['count']} items)"
        )
        results["2_singleton"] = True
    except Exception as e:
        print_test(2, "ساخت BrainMemory", False, f"{type(e).__name__}: {e}")
        results["2_singleton"] = False
        return results

    # ══════════════════════════════════════════════════════
    # ۳. 4 حافظه موجودند
    # ══════════════════════════════════════════════════════
    has_working = hasattr(bm, "working")
    has_long = hasattr(bm, "long_term")
    has_proc = hasattr(bm, "procedural")
    has_sem = hasattr(bm, "semantic")
    ok3 = all([has_working, has_long, has_proc, has_sem])
    print_test(
        3, "4 حافظه ساخته شد", ok3,
        f"working={has_working} | long_term={has_long} | "
        f"procedural={has_proc} | semantic={has_sem}"
    )
    results["3_4memories"] = ok3

    # ══════════════════════════════════════════════════════
    # ۴. Working Memory — ذخیره و بازیابی
    # ══════════════════════════════════════════════════════
    try:
        before = len(bm.working.items)
        bm.working.add(
            "test_q_unique_123",
            "test_a_unique_456",
            importance=0.9,
        )
        after = len(bm.working.items)
        item = bm.working.get_by_key("test_q_unique_123")
        ok4 = (
            after == before + 1
            and item is not None
            and item["a"] == "test_a_unique_456"
        )
        print_test(
            4, "Working Memory ذخیره/بازیابی", ok4,
            f"before={before} after={after} | "
            f"recall={item['a'] if item else 'None'}"
        )
        results["4_working"] = ok4
    except Exception as e:
        print_test(4, "Working Memory", False, f"{type(e).__name__}: {e}")
        results["4_working"] = False

    # ══════════════════════════════════════════════════════
    # ۵. Long-Term Memory — ذخیره و بازیابی
    # ══════════════════════════════════════════════════════
    try:
        bm.long_term.add(
            category="test_cat",
            key="test_key_unique_789",
            value="test_value_unique_abc",
            importance=0.95,
        )
        val = bm.long_term.get("test_cat", "test_key_unique_789")
        ok5 = val == "test_value_unique_abc"
        print_test(
            5, "Long-Term Memory ذخیره/بازیابی", ok5,
            f"recall={val!r}"
        )
        results["5_long_term"] = ok5
    except Exception as e:
        print_test(5, "Long-Term Memory", False, f"{type(e).__name__}: {e}")
        results["5_long_term"] = False

    # ══════════════════════════════════════════════════════
    # ۶. Procedural Memory
    # ══════════════════════════════════════════════════════
    try:
        bm.procedural.mark_done("test_task_unique_xyz")
        is_done = bm.procedural.is_done("test_task_unique_xyz")
        ok6 = is_done is True
        print_test(
            6, "Procedural Memory — mark_done/is_done", ok6,
            f"is_done={is_done}"
        )
        results["6_procedural"] = ok6
    except Exception as e:
        print_test(6, "Procedural Memory", False, f"{type(e).__name__}: {e}")
        results["6_procedural"] = False

    # ══════════════════════════════════════════════════════
    # ۷. Semantic Memory (ChromaDB)
    # ══════════════════════════════════════════════════════
    if bm.semantic.enabled:
        try:
            before = bm.semantic.collection.count()
            bm.semantic.add(
                text="test semantic doc unique 123",
                metadata={"type": "test"},
            )
            after = bm.semantic.collection.count()
            results_list = bm.semantic.search("semantic test", n=1)
            ok7 = after == before + 1 and len(results_list) > 0
            print_test(
                7, "Semantic Memory (ChromaDB)", ok7,
                f"before={before} after={after} | "
                f"search={len(results_list)} results"
            )
            results["7_semantic"] = ok7
        except Exception as e:
            print_test(7, "Semantic Memory", False, f"{type(e).__name__}: {e}")
            results["7_semantic"] = False
    else:
        print_test(7, "Semantic Memory", False, "ChromaDB غیرفعال")
        results["7_semantic"] = False

    # ══════════════════════════════════════════════════════
    # ۸. remember() — ذخیره در همه حافظه‌ها
    # ══════════════════════════════════════════════════════
    try:
        w_before = len(bm.working.items)
        lt_before = bm.long_term.get_stats()["knowledge"]
        sem_before = bm.semantic.collection.count() if bm.semantic.enabled else 0

        bm.remember(
            "test_full_q_unique",
            "test_full_a_unique",
            importance=0.85,
        )

        w_after = len(bm.working.items)
        lt_after = bm.long_term.get_stats()["knowledge"]
        sem_after = bm.semantic.collection.count() if bm.semantic.enabled else 0

        ok8 = (
            w_after == w_before + 1
            and lt_after == lt_before + 1
            and sem_after == sem_before + 1
        )
        print_test(
            8, "remember() — ذخیره در همه", ok8,
            f"working: {w_before}→{w_after} | "
            f"long_term: {lt_before}→{lt_after} | "
            f"semantic: {sem_before}→{sem_after}"
        )
        results["8_remember"] = ok8
    except Exception as e:
        print_test(8, "remember()", False, f"{type(e).__name__}: {e}")
        results["8_remember"] = False

    # ══════════════════════════════════════════════════════
    # ۹. build_context()
    # ══════════════════════════════════════════════════════
    try:
        context = bm.build_context("test context query")
        ok9 = context is not None
        preview = context[:200].replace("\n", " ") if context else ""
        print_test(
            9, "build_context()", ok9,
            f"len={len(context)} | preview={preview}..."
        )
        results["9_build_context"] = ok9
    except Exception as e:
        print_test(9, "build_context()", False, f"{type(e).__name__}: {e}")
        results["9_build_context"] = False

    # ══════════════════════════════════════════════════════
    # ۱۰. is_task_done / mark_task_done
    # ══════════════════════════════════════════════════════
    try:
        bm.mark_task_done("unique_full_test_task")
        ok_done = bm.is_task_done("unique_full_test_task")
        ok_not_done = not bm.is_task_done("random_unused_task_xyz")
        ok10 = ok_done and ok_not_done
        print_test(
            10, "is_task_done/mark_task_done", ok10,
            f"done={ok_done} | not_done={ok_not_done}"
        )
        results["10_tasks"] = ok10
    except Exception as e:
        print_test(10, "tasks", False, f"{type(e).__name__}: {e}")
        results["10_tasks"] = False

    # ══════════════════════════════════════════════════════
    # ۱۱. add_lesson
    # ══════════════════════════════════════════════════════
    try:
        lt_before = bm.long_term.get_stats()["lessons"]
        bm.add_lesson("wrong_xyz", "correct_abc")
        lt_after = bm.long_term.get_stats()["lessons"]
        lessons = bm.long_term.search_lessons("wrong_xyz")
        ok11 = lt_after == lt_before + 1 and len(lessons) > 0
        print_test(
            11, "add_lesson/search_lessons", ok11,
            f"lessons: {lt_before}→{lt_after} | "
            f"search={len(lessons)} results"
        )
        results["11_lessons"] = ok11
    except Exception as e:
        print_test(11, "lessons", False, f"{type(e).__name__}: {e}")
        results["11_lessons"] = False

    # ══════════════════════════════════════════════════════
    # ۱۲. AIAssistant متصل به BrainMemory
    # ══════════════════════════════════════════════════════
    try:
        import inspect
        from core.services.ai_assistant import AIAssistant
        source = inspect.getsource(AIAssistant.chat)
        has_bm_import = "brain_memory" in source
        has_context = "build_context" in source
        has_remember = "remember" in source
        ok12 = has_bm_import and has_context and has_remember
        print_test(
            12, "AIAssistant متصل به BrainMemory", ok12,
            f"import={has_bm_import} | context={has_context} | "
            f"remember={has_remember}"
        )
        results["12_ai_connected"] = ok12
    except Exception as e:
        print_test(12, "AIAssistant", False, f"{type(e).__name__}: {e}")
        results["12_ai_connected"] = False

    # ══════════════════════════════════════════════════════
    # ۱۳. دانش پروژه ذخیره شده
    # ══════════════════════════════════════════════════════
    try:
        structure = bm.long_term.get("structure", "project_stats")
        rules = bm.long_term.get_category("rules")
        capabilities = bm.long_term.get_category("capabilities")
        access = bm.long_term.get("access", "summary")

        ok13 = (
            structure is not None
            and len(rules) > 0
            and len(capabilities) > 0
            and access is not None
        )
        print_test(
            13, "دانش پروژه در Long-Term", ok13,
            f"structure={'✅' if structure else '❌'} | "
            f"rules={len(rules)} | "
            f"capabilities={len(capabilities)} | "
            f"access={'✅' if access else '❌'}"
        )
        results["13_project_knowledge"] = ok13
    except Exception as e:
        print_test(13, "project knowledge", False, f"{type(e).__name__}: {e}")
        results["13_project_knowledge"] = False

    # ══════════════════════════════════════════════════════
    # ۱۴. تست واقعی: chat() با BrainMemory
    # ══════════════════════════════════════════════════════
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        # چک اتصال
        if not await ai.available():
            print_test(14, "chat() واقعی", False, "AI available نیست")
            results["14_real_chat"] = False
        else:
            w_before = len(bm.working.items)
            t0 = time.monotonic()
            answer = await ai.chat("پروژه چیه؟")
            ms = (time.monotonic() - t0) * 1000
            w_after = len(bm.working.items)

            ok14 = (
                answer
                and len(answer) > 20
                and w_after >= w_before
            )
            preview = answer[:150].replace("\n", " ")
            print_test(
                14, f"chat() واقعی ({ms:.0f}ms)", ok14,
                f"working: {w_before}→{w_after} | "
                f"preview={preview}..."
            )
            results["14_real_chat"] = ok14
    except Exception as e:
        print_test(14, "chat() واقعی", False, f"{type(e).__name__}: {e}")
        results["14_real_chat"] = False

    # ══════════════════════════════════════════════════════
    # ۱۵. تست is_task_done در AIAssistant.chat()
    # ══════════════════════════════════════════════════════
    try:
        # قبل: ثبت کار
        bm.mark_task_done("query_unique_test_9999")

        # بعد: چک در chat
        # اگر chat درست کار کند، باید پیام "این کار انجام شد" بدهد
        answer = await ai.chat("query_unique_test_9999")

        ok15 = answer and "انجام شد" in answer
        print_test(
            15, "chat() کار تکراری را تشخیص می‌دهد", ok15,
            f"answer={answer[:100] if answer else 'None'}"
        )
        results["15_duplicate"] = ok15
    except Exception as e:
        print_test(15, "duplicate detection", False, f"{type(e).__name__}: {e}")
        results["15_duplicate"] = False

    # ══════════════════════════════════════════════════════
    # آمار نهایی
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  📊 آمار نهایی")
    print("=" * 70)
    final = bm.get_stats()
    print(f"\n  Working:    {final['working']}")
    print(f"  Knowledge:  {final['long_term']['knowledge']}")
    print(f"  Lessons:    {final['long_term']['lessons']}")
    print(f"  Tasks:      {final['procedural']['tasks']}")
    print(f"  Patterns:   {final['procedural']['patterns']}")
    print(f"  Semantic:   {final['semantic']['count']} "
          f"(enabled={final['semantic']['enabled']})")

    # ══════════════════════════════════════════════════════
    # خلاصه
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  🏁 خلاصه")
    print("=" * 70)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, ok in sorted(results.items()):
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")

    print(f"\n  کل: {passed}/{total} موفق")

    if passed == total:
        print("\n  🎉🎉🎉 مغز انسان کامل پیاده شد!")
    else:
        print(f"\n  ⚠️ {total - passed} مورد خطا — بررسی کن")

    return results


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹️ متوقف شد")