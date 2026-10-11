"""تست Brain در حالت چت‌بات."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    print("=" * 60)
    print("🧪 تست Brain ChatOps — از مسیر چت‌بات")
    print("=" * 60)

    # ─── ۱) Brain ───
    try:
        from core.brain.main import Brain
        brain = Brain()
        print(f"✅ Brain ساخت | client={'✅' if brain.client else '❌'}")

        if not brain.client:
            print("❌ Brain آفلاین است!")
            return 1
    except Exception as e:
        print(f"❌ Brain: {type(e).__name__}: {e}")
        return 1

    # ─── ۲) تست پیام‌های مختلف ───
    tests = [
        "سلام",
        "وضعیت ربات",
        "بله رو شروع کن",
        "در بله سرچ کن گلخانه",
        "list_platforms",
        "چرا بله وصل نشد",
    ]

    for text in tests:
        print()
        print(f"─── تست: «{text}» ───")
        try:
            t0 = asyncio.get_event_loop().time()
            result = await brain.process_admin_command(text)
            elapsed = asyncio.get_event_loop().time() - t0

            print(f"⏱️  {elapsed:.2f}s")
            print(f"📤 {str(result)[:300]}")
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")

    print()
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))