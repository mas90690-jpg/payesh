"""تست دقیق خطای هر فایل — هر فایل جدا import می‌شود."""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════
# فایل‌هایی که باید تست شوند (نقض‌دارها)
# ══════════════════════════════════════════════════════════
FILES_TO_TEST = [
    ("handlers.admin_handler", "handlers/admin_handler.py"),
    ("handlers.admin_actions", "handlers/admin_actions.py"),
    ("core.services.ai_assistant", "core/services/ai_assistant.py"),
    ("core.brain", "core/brain/__init__.py"),
    ("core.safe_brain", "core/safe_brain/__init__.py"),
    ("core.manual.brain", "core/manual/brain.py"),
    # اضافه کن هر چه خواستی:
    ("core.brain.mixin_platforms", "core/brain/mixin_platforms.py"),
    ("core.app.orchestrator", "core/app/orchestrator.py"),
]


def test_import(module_name: str, file_path: str) -> bool:
    """import یک ماژول و چاپ خطای دقیق اگر شکست خورد."""
    print(f"\n{'=' * 70}")
    print(f"📦 {module_name}")
    print(f"📄 {file_path}")
    print("=" * 70)

    # پاک کردن از sys.modules برای تست تمیز
    for key in list(sys.modules.keys()):
        if key == module_name or key.startswith(module_name + "."):
            del sys.modules[key]

    try:
        mod = importlib.import_module(module_name)
        print("✅ import موفق")
        return True
    except ImportError as e:
        print(f"❌ ImportError: {e}")
        print("\n📍 مسیر خطا:")
        tb = traceback.format_exc()
        for line in tb.splitlines():
            print(f"   {line}")
        return False
    except SyntaxError as e:
        print(f"❌ SyntaxError: {e}")
        print(f"   فایل: {e.filename}")
        print(f"   خط: {e.lineno}")
        print(f"   متن: {e.text}")
        return False
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
        print("\n📍 مسیر خطا:")
        tb = traceback.format_exc()
        for line in tb.splitlines():
            print(f"   {line}")
        return False


def main():
    print("\n" + "=" * 70)
    print("  🔍 تست دقیق import هر فایل")
    print("=" * 70)

    results = {}
    for module_name, file_path in FILES_TO_TEST:
        results[module_name] = test_import(module_name, file_path)

    # ─── خلاصه ───
    print("\n" + "=" * 70)
    print("  📊 خلاصه")
    print("=" * 70)

    ok = sum(1 for v in results.values() if v)
    fail = len(results) - ok

    for name, status in results.items():
        mark = "✅" if status else "❌"
        print(f"  {mark} {name}")

    print(f"\n  کل: {ok}/{len(results)} موفق | {fail} خطا")


if __name__ == "__main__":
    main()