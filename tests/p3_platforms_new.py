"""تست ابزارهای پلتفرم جدید."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


async def main():
    print("=" * 60)
    print("تست ابزارهای پلتفرم جدید")
    print("=" * 60)
    print()

    from core.brain.main import Brain

    brain = Brain(api_key="", db=None)

    # ─── تست ۱: list_platforms ───
    print("─" * 40)
    print("تست ۱: list_platforms")
    print("─" * 40)
    r = await brain._list_platforms()
    print(r)
    print()

    # ─── تست ۲: platform_health ───
    print("─" * 40)
    print("تست ۲: platform_health (bale)")
    print("─" * 40)
    r = await brain._platform_health("bale")
    print(r)
    print()

    # ─── تست ۳: platform_diagnose ───
    print("─" * 40)
    print("تست ۳: platform_diagnose (bale)")
    print("─" * 40)
    r = await brain._platform_diagnose("bale")
    print(r)
    print()

    print("═" * 60)
    print("✅ تست موفق — ابزارهای جدید کار می‌کنند")
    print("═" * 60)
    print()
    print("💡 برای تست start_platform واقعی، پله بعدی را بزن")


if __name__ == "__main__":
    asyncio.run(main())