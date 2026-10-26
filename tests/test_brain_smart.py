"""
🧠 تست هوش پروژه — 4 لایه

لایه 1: SafeBrain       (پاسخ‌های از پیش تعریف‌شده)
لایه 2: Brain           (AI + ابزارها)
لایه 3: ManualBrain     (قوانین یادگرفته)
لایه 4: AIAssistant     (DeepSeek / Ollama)
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

# این تست‌ها demo هستند (نه تست واحد) و به شبکه/AI نیاز دارند
# پیش‌فرض skip می‌شوند — برای اجرا: pytest -m slow
pytestmark = [pytest.mark.slow, pytest.mark.network]


# ══════════════════════════════════════════════════════════
# تست ۱: SafeBrain
# ══════════════════════════════════════════════════════════
async def test_safe_brain():
    print("\n" + "=" * 70)
    print("  1️⃣  SafeBrain — لایه اول")
    print("=" * 70)

    try:
        from core.safe_brain import get_safe_brain

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        sb = get_safe_brain(api_key=api_key)
        print("  ✅ SafeBrain ساخته شد")

        # تست ۳ سؤال
        questions = [
            "سلام",
            "وضعیت چطوره؟",
            "چند سرنخ داریم؟",
        ]

        for q in questions:
            print(f"\n  📝 سؤال: {q!r}")
            t0 = time.monotonic()
            try:
                ans = await sb.process(q)
                ms = (time.monotonic() - t0) * 1000
                if ans:
                    preview = ans[:100].replace("\n", " ")
                    print(f"     ✅ پاسخ ({ms:.0f}ms): {preview}")
                else:
                    print(f"     ⚠️ پاسخ خالی ({ms:.0f}ms)")
            except Exception as e:
                print(f"     ❌ {type(e).__name__}: {e}")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# تست ۲: Brain (AI + ابزارها)
# ══════════════════════════════════════════════════════════
async def test_brain():
    print("\n" + "=" * 70)
    print("  2️⃣  Brain — لایه دوم (AI + ابزارها)")
    print("=" * 70)

    try:
        from core.database import create_database
        from core.brain import get_brain

        # ساخت DB
        db = create_database()
        if hasattr(db, "init"):
            await db.init()

        brain = get_brain(db=db)
        print("  ✅ Brain ساخته شد")

        # تست پیام‌های admin
        questions = [
            "وضعیت سیستم",
            "چند سرنخ ذخیره شده؟",
        ]

        for q in questions:
            print(f"\n  📝 سؤال: {q!r}")
            t0 = time.monotonic()
            try:
                ans = await brain.process_admin_command(q)
                ms = (time.monotonic() - t0) * 1000
                if ans:
                    preview = ans[:150].replace("\n", " ")
                    print(f"     ✅ پاسخ ({ms:.0f}ms):")
                    print(f"        {preview}")
                else:
                    print(f"     ⚠️ پاسخ خالی ({ms:.0f}ms)")
            except Exception as e:
                print(f"     ❌ {type(e).__name__}: {e}")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# تست ۳: ManualBrain (قوانین یادگرفته)
# ══════════════════════════════════════════════════════════
async def test_manual_brain():
    print("\n" + "=" * 70)
    print("  3️⃣  ManualBrain — لایه سوم (قوانین)")
    print("=" * 70)

    try:
        from core.manual.brain import get_manual_brain
        from data import paths as data_paths

        db_path = str(data_paths.DB_FILE)
        mb = get_manual_brain(db_path)
        print("  ✅ ManualBrain ساخته شد")

        # آمار
        try:
            from core.manual.rules import get_rules
            rules = get_rules()
            print(f"  📊 تعداد قوانین: {len(rules)}")
        except Exception as e:
            print(f"  ⚠️ get_rules: {e}")

        # تست قوانین
        questions = [
            "سلام",
            "راهنما",
            "help",
        ]

        for q in questions:
            print(f"\n  📝 سؤال: {q!r}")
            t0 = time.monotonic()
            try:
                ans = await mb.process(q, sender="test_user")
                ms = (time.monotonic() - t0) * 1000
                if ans:
                    preview = ans[:150].replace("\n", " ")
                    print(f"     ✅ پاسخ ({ms:.0f}ms):")
                    print(f"        {preview}")
                else:
                    print(f"     ⚠️ پاسخ خالی ({ms:.0f}ms) — احتمالاً قانون نبود")
            except Exception as e:
                print(f"     ❌ {type(e).__name__}: {e}")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# تست ۴: AIAssistant (DeepSeek / Ollama)
# ══════════════════════════════════════════════════════════
async def test_ai_assistant():
    print("\n" + "=" * 70)
    print("  4️⃣  AIAssistant — لایه چهارم (DeepSeek/Ollama)")
    print("=" * 70)

    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()
        print("  ✅ AIAssistant ساخته شد")
        print(f"  📊 Stats: {ai.get_stats()}")

        # تست available
        print("\n  🔍 چک available...")
        ok = await ai.available()
        print(f"     {'✅' if ok else '❌'} available={ok}")

        if not ok:
            print("     ⚠️ هیچ provider در دسترس نیست")
            return False

        # تست chat
        questions = [
            "سلام، ۵ تا ۵ چند می‌شود؟",
            "یک جمله کوتاه درباره پایش سرنخ بنویس",
        ]

        for q in questions:
            print(f"\n  📝 سؤال: {q!r}")
            t0 = time.monotonic()
            try:
                ans = await ai.chat(q)
                ms = (time.monotonic() - t0) * 1000
                if ans:
                    preview = ans[:200].replace("\n", " ")
                    print(f"     ✅ پاسخ ({ms:.0f}ms):")
                    print(f"        {preview}")
                else:
                    print("     ⚠️ پاسخ خالی")
            except Exception as e:
                print(f"     ❌ {type(e).__name__}: {e}")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════
async def main():
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 20 + "🧠 تست هوش پروژه" + " " * 33 + "║")
    print("╚" + "═" * 68 + "╝")

    results = {}

    # ۱: SafeBrain
    results["SafeBrain"] = await test_safe_brain()

    # ۲: Brain
    results["Brain"] = await test_brain()

    # ۳: ManualBrain
    results["ManualBrain"] = await test_manual_brain()

    # ۴: AIAssistant
    results["AIAssistant"] = await test_ai_assistant()

    # خلاصه
    print("\n" + "=" * 70)
    print("  📊 خلاصه")
    print("=" * 70)
    for name, ok in results.items():
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")

    n_ok = sum(1 for v in results.values() if v)
    print(f"\n  کل: {n_ok}/{len(results)} موفق")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹️ متوقف شد")