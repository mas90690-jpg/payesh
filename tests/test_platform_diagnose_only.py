"""
تست تخصصی — فقط ریشه‌یابی پلتفرم‌ها.

اجرا:
    python tests/test_platform_diagnose_only.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    print("=" * 62)
    print("🔍 تست تخصصی — ریشه‌یابی پلتفرم‌ها")
    print("=" * 62)

    try:
        from core.brain.main import Brain
        brain = Brain(read_only=False, full_access=False)
        print("✅ Brain ساخته شد")
    except Exception as e:
        print(f"❌ خطا در ساخت Brain: {type(e).__name__}: {e}")
        return 1

    platforms = [
        "bale", "eitaa", "rubika", "telegram",
        "whatsapp", "linkedin", "instagram",
        "youtube", "web",
    ]

    passed = 0
    failed = 0

    for p in platforms:
        print()
        print(f"╭─ ریشه‌یابی {p} " + "─" * 40)
        try:
            result = await asyncio.wait_for(
                brain._diagnose_platform(p),
                timeout=30,
            )
            if result:
                for line in result.split("\n"):
                    print(f"  {line}")
                passed += 1
            else:
                print(f"  ❌ خروجی خالی")
                failed += 1
        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {e}")
            failed += 1
        print("╰" + "─" * 60)

    print()
    print("=" * 62)
    print(f"📊 نتیجه: {passed}/{len(platforms)} pass | {failed} fail")
    print("=" * 62)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)