import sys
import asyncio

sys.path.insert(0, r"F:\payesh plat")
from core.brain import get_brain


async def main():
    b = get_brain()

    print("=" * 60)
    print("TEST 1: Proactive Diagnosis")
    print("=" * 60)
    print(await b._proactive_diagnosis())

    print()
    print("=" * 60)
    print("TEST 2: ToolRouter")
    print("=" * 60)
    from core.brain.tool_router import ToolRouter
    questions = [
        "وضعیت مغزت چیه؟",
        "خواب مغز رو اجرا کن",
        "چی می‌دونی؟",
        "یه باگ تو کد پیدا کن",
        "چرا بله وصل نمی‌شه؟",
    ]
    for q in questions:
        tools = ToolRouter.route(q)
        print(f"  • {q}")
        print(f"    → {tools}")

    print()
    print("=" * 60)
    print("TEST 3: CoT در Prompt")
    print("=" * 60)
    prompt = b._build_system_prompt("precise")
    print(f"  طول prompt: {len(prompt)} کاراکتر")
    print(f"  شروع با CoT: {'روش کار' in prompt[:200]}")
    print(f"  WinDoctor: {'WinDoctor' in prompt}")


if __name__ == "__main__":
    asyncio.run(main())