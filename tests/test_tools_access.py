"""
🔧 تست دقیق ابزارها و دسترسی‌ها

- ۶۴ ابزار Brain را لیست می‌کند
- ۳۷+۱۱ دسترسی از access_control.json را چک می‌کند
- ۳۴ قانون ManualBrain را نشان می‌دهد
- تست واقعی چند ابزار
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════
# ۱. لیست ابزارها (ToolMap)
# ══════════════════════════════════════════════════════════
def test_toolmap():
    print("\n" + "=" * 75)
    print("  🧰 ۱. ابزارهای Brain (ToolMap)")
    print("=" * 75)

    try:
        from core.brain.tools_meta import TOOL_DEFINITIONS, get_tool_count

        print(f"\n  📊 کل ابزارها: {get_tool_count()}\n")

        # گروه‌بندی بر اساس دسته
        by_category: dict[str, list[str]] = {}
        for tool in TOOL_DEFINITIONS:
            name = tool.get("name", "?")
            cat = tool.get("category", "other")
            by_category.setdefault(cat, []).append(name)

        for cat in sorted(by_category.keys()):
            tools = sorted(by_category[cat])
            print(f"  📁 {cat} ({len(tools)}):")
            for name in tools:
                print(f"     • {name}")
            print()

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# ۲. دسترسی‌ها (Access Control)
# ══════════════════════════════════════════════════════════
def test_access_control():
    print("\n" + "=" * 75)
    print("  🔐 ۲. دسترسی‌ها (Access Control)")
    print("=" * 75)

    try:
        from core.adapters.access_manager import get_access_manager

        am = get_access_manager()

        # لیست متدها
        methods = [m for m in dir(am) if not m.startswith("_")]
        print(f"\n  📊 متدهای AccessManager ({len(methods)}):")
        for m in methods[:30]:
            print(f"     • {m}")

        # خواندن فایل
        try:
            from data import paths as data_paths
            ac_file = Path(data_paths.PROJECT_ROOT) / "data" / "configs" / "access_control.json"
            if ac_file.exists():
                data = json.loads(ac_file.read_text(encoding="utf-8"))
                print("\n  📄 access_control.json:")
                print(f"     کلیدها: {list(data.keys())}")

                for key in ("read", "write", "database", "commands"):
                    section = data.get(key, {})
                    if isinstance(section, dict):
                        allow = section.get("allow", [])
                        deny = section.get("deny", [])
                        print(f"\n     📌 {key}:")
                        print(f"        allow ({len(allow)}): {allow[:10]}")
                        print(f"        deny ({len(deny)}): {deny[:10]}")
                    elif isinstance(section, list):
                        print(f"\n     📌 {key} ({len(section)}): {section[:10]}")
        except Exception as e:
            print(f"  ⚠️ خواندن فایل: {e}")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# ۳. قوانین ManualBrain
# ══════════════════════════════════════════════════════════
def test_manual_rules():
    print("\n" + "=" * 75)
    print("  📜 ۳. قوانین ManualBrain")
    print("=" * 75)

    try:
        from core.manual.rules import get_rules

        rules = get_rules()
        print(f"\n  📊 کل قوانین: {len(rules)}\n")

        # نمایش ۳۰ قانون اول
        for i, rule in enumerate(rules[:30], 1):
            if isinstance(rule, dict):
                triggers = rule.get("triggers", rule.get("trigger", "?"))
                action = rule.get("action", "?")
                source = rule.get("source", "core")
                print(f"  {i:2d}. [{source}] trigger={triggers} → action={action}")
            else:
                print(f"  {i:2d}. {rule}")

        if len(rules) > 30:
            print(f"\n  ... و {len(rules) - 30} قانون دیگر")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# ۴. تست واقعی چند ابزار
# ══════════════════════════════════════════════════════════
async def test_real_tools():
    print("\n" + "=" * 75)
    print("  🧪 ۴. تست واقعی چند ابزار")
    print("=" * 75)

    try:
        from core.database import create_database
        from core.brain import get_brain

        db = create_database()
        if hasattr(db, "init"):
            await db.init()

        brain = get_brain(db=db)
        print("  ✅ Brain آماده")

        # دستورات تست
        commands = [
            "وضعیت",              # status
            "لیست پلتفرم‌ها",      # list_platforms
            "چند گروه داریم؟",    # query
            "کمک",                # help
        ]

        for cmd in commands:
            print("\n  ────────────────────────────────────────")
            print(f"  📝 دستور: {cmd!r}")
            try:
                ans = await brain.process_admin_command(cmd)
                if ans:
                    preview = ans[:200].replace("\n", " ")
                    print(f"     ✅ {preview}")
                else:
                    print("     ⚠️ پاسخ خالی")
            except Exception as e:
                print(f"     ❌ {type(e).__name__}: {e}")

        return True
    except Exception as e:
        print(f"  ❌ خطا: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════
async def main():
    print("\n" + "╔" + "═" * 73 + "╗")
    print("║" + " " * 15 + "🔧 تست ابزارها و دسترسی‌ها" + " " * 31 + "║")
    print("╚" + "═" * 73 + "╝")

    results = {}
    results["ToolMap"] = test_toolmap()
    results["AccessControl"] = test_access_control()
    results["ManualRules"] = test_manual_rules()
    results["RealTools"] = await test_real_tools()

    # خلاصه
    print("\n" + "=" * 75)
    print("  📊 خلاصه")
    print("=" * 75)
    for name, ok in results.items():
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")

    n_ok = sum(1 for v in results.values() if v)
    print(f"\n  کل: {n_ok}/{len(results)} موفق")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹️ متوقف شد")