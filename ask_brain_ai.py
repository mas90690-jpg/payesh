import asyncio
import sys


async def main():
    print("=" * 60)
    print("Brain - تحلیل با AI")
    print("=" * 60)

    try:
        from core.brain.main import get_brain
        brain = get_brain(read_only=True)
        print("Brain connected")
    except Exception as e:
        print("Brain error:", type(e).__name__, e)
        return

    question = input("\nسوال: ").strip()
    if not question:
        return

    try:
        result = await brain.agent(question, mode="analyze")
        print()
        print("=" * 60)
        print("پاسخ ربات:")
        print("=" * 60)
        print(result)
    except Exception as e:
        print("error:", type(e).__name__, e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("fatal:", type(e).__name__, e)
        sys.exit(1)