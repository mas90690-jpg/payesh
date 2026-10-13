"""تست حلقه Consolidation."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


async def main():
    from core.brain.consolidation import (
        start_consolidation,
        stop_consolidation,
        get_stats,
    )

    print("\n" + "=" * 60)
    print("  🌙 تست حلقه Consolidation")
    print("=" * 60 + "\n")

    # ۱. شروع
    task = start_consolidation()
    print(f"  ✅ حلقه شروع شد | task={task}")

    # ۲. صبر ۳۵ ثانیه (تا اولین اجرا که ۳۰ ثانیه بعد است)
    print("  ⏳ صبر ۳۵ ثانیه...")
    await asyncio.sleep(35)

    # ۳. آمار
    stats = get_stats()
    print(f"\n  📊 آمار: {stats}")

    # ۴. توقف
    stop_consolidation()
    print("  ✅ حلقه متوقف شد")

    print("\n  ✅ تست تمام!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹️ متوقف شد")