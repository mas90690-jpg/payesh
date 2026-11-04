import asyncio
import sys


PATHS = [
    "core/",
    "handlers/",
    "analyzers/",
    "collectors/",
]


async def main():
    print("=" * 60)
    print("Audit - همه پوشه‌ها")
    print("=" * 60)

    try:
        from core.brain.main import get_brain
        brain = get_brain(read_only=True)
        print("Brain connected")
    except Exception as e:
        print("Brain error:", type(e).__name__, e)
        return

    for p in PATHS:
        print()
        print("=" * 60)
        print("AUDIT:", p)
        print("=" * 60)
        try:
            result = await brain._audit_code(
                paths=p,
                include_flake8=True,
                include_vulture=False,
                include_bandit=False,
            )
            print(result)
        except Exception as e:
            print("audit error:", type(e).__name__, e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nمتوقف شد.")
    except Exception as e:
        print("fatal:", type(e).__name__, e)
        sys.exit(1)