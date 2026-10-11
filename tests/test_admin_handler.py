"""تست AdminHandler — از بله چه اتفاقی می‌افتد."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    print("=" * 60)
    print("🧪 تست AdminHandler — مسیر بله")
    print("=" * 60)

    # ─── ۱) AdminHandler بساز ───
    try:
        from core.database.factory import create_database
        from handlers.admin_handler import AdminHandler

        db = create_database()
        await db.init()

        handler = AdminHandler(db)
        print(f"✅ AdminHandler ساخته شد")
        print(f"   manual_brain: {'✅' if handler.manual_brain else '❌'}")
        print(f"   safe_brain: {'✅' if handler.safe_brain else '❌'}")
    except Exception as e:
        print(f"❌ ساخت: {type(e).__name__}: {e}")
        import traceback
        for ln in traceback.format_exc().splitlines()[-5:]:
            print(f"   {ln}")
        return 1

    # ─── ۲) تست پیام‌ها ───
    tests = [
        "سلام",
        "وضعیت",
        "بله رو شروع کن",
        "در بله سرچ کن گلخانه",
    ]

    for text in tests:
        print()
        print(f"─── «{text}» ───")
        try:
            t0 = asyncio.get_event_loop().time()
            reply = await handler.process_text(text)
            elapsed = asyncio.get_event_loop().time() - t0
            print(f"⏱️  {elapsed:.2f}s")
            print(f"📤 {str(reply)[:300]}")
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")

    await db.close()
    print()
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))