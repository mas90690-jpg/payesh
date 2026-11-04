import asyncio
import sys


async def main():
    print("=" * 60)
    print("Brain - تحلیل دقیق")
    print("=" * 60)

    try:
        from core.brain.main import get_brain
        brain = get_brain(read_only=True)
        print("Brain connected")
    except Exception as e:
        print("Brain error:", type(e).__name__, e)
        return

    print()
    print("چه فایلی را تحلیل کنم؟")
    print("  1) utils.py  (safe_path Windows)")
    print("  2) learning.py (_extract_candidates + ProductLearner)")
    print("  3) memory.py (_maybe_cleanup throttle)")
    print("  4) mixin_info.py (_browse_web + _zip + _grep)")
    print("  5) mixin_agent.py (_QUICK_COMMANDS)")
    print("  6) mixin_reports.py (generate_report)")
    print("  7) mixin_files.py (append_knowledge)")
    print("  8) mixin_platforms.py (_control_platform)")
    print("  9) همه (audit_code)")
    print()

    # حلقه تا ورودی معتبر
    while True:
        choice = input("انتخاب (1-9): ").strip()
        if choice in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]:
            break
        print("نامعتبر. عدد 1 تا 9 را بزن.")

    if choice == "9":
        print()
        print("=" * 60)
        print("بررسی همه فایل‌ها با audit_code")
        print("=" * 60)
        try:
            result = await brain._audit_code(
                paths="core/brain/",
                include_flake8=True,
                include_vulture=False,
                include_bandit=False,
            )
            print(result)
        except Exception as e:
            print("audit error:", type(e).__name__, e)
        return

    files = {
        "1": "core/brain/utils.py",
        "2": "core/brain/learning.py",
        "3": "core/brain/memory.py",
        "4": "core/brain/mixin_info.py",
        "5": "core/brain/mixin_agent.py",
        "6": "core/brain/mixin_reports.py",
        "7": "core/brain/mixin_files.py",
        "8": "core/brain/mixin_platforms.py",
    }

    path = files[choice]

    print()
    print("=" * 60)
    print("بررسی", path)
    print("=" * 60)

    try:
        result = await brain._audit_file(path)
        print(result)
    except Exception as e:
        print("audit error:", type(e).__name__, e)

    # سوال از AI
    print()
    print("=" * 60)
    print("تحلیل با AI")
    print("=" * 60)
    question = input("سوال (یا Enter خالی برای رد کردن): ").strip()
    if question:
        try:
            result = await brain.agent(question, mode="analyze")
            print()
            print("=" * 60)
            print("پاسخ AI:")
            print("=" * 60)
            print(result)
        except Exception as e:
            print("AI error:", type(e).__name__, e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nمتوقف شد.")
    except Exception as e:
        print("fatal:", type(e).__name__, e)
        sys.exit(1)