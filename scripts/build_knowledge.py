"""
🔧 ساخت اولیه Knowledge Base

ساختار پروژه + ۳۴ قانون + ۶۴ ابزار + دسترسی‌ها → knowledge.db
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def build_structure():
    """ساختار پروژه."""
    print("📁 ساختار پروژه...")
    files = 0
    classes = 0
    functions = 0

    for py_file in ROOT.rglob("*.py"):
        s = str(py_file)
        if "__pycache__" in s or "venv" in s or "storage" in s:
            continue
        files += 1
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    classes += 1
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions += 1
        except Exception:
            pass

    print(f"  ✅ {files} فایل، {classes} کلاس، {functions} تابع")
    return {"files": files, "classes": classes, "functions": functions}


def build_rules():
    """قوانین ManualBrain."""
    print("📜 قوانین...")
    rules_file = ROOT / "data" / "configs" / "rules.json"
    if not rules_file.exists():
        print("  ⚠️ rules.json نیست")
        return []
    try:
        data = json.loads(rules_file.read_text(encoding="utf-8"))
        rules = data.get("rules", data if isinstance(data, list) else [])
        print(f"  ✅ {len(rules)} قانون")
        return rules
    except Exception as e:
        print(f"  ❌ {e}")
        return []


def build_capabilities():
    """ابزارهای Brain."""
    print("🔧 ابزارها...")
    try:
        from core.brain.tools_meta import TOOL_DEFINITIONS
        tools = []
        for t in TOOL_DEFINITIONS:
            tools.append({
                "name": t.get("name") or t.get("tool_name") or "?",
                "category": t.get("category", "other"),
                "description": t.get("description", "")[:200],
            })
        print(f"  ✅ {len(tools)} ابزار")
        return tools
    except Exception as e:
        print(f"  ❌ {e}")
        return []


def build_access():
    """دسترسی‌ها."""
    print("🔐 دسترسی‌ها...")
    ac_file = ROOT / "data" / "configs" / "access_control.json"
    if not ac_file.exists():
        print("  ⚠️ access_control.json نیست")
        return {}
    try:
        data = json.loads(ac_file.read_text(encoding="utf-8"))
        result = {
            "read": data.get("read", {}),
            "write": data.get("write", {}),
        }
        print(f"  ✅ ذخیره شد")
        return result
    except Exception as e:
        print(f"  ❌ {e}")
        return {}


def main():
    print("\n" + "=" * 60)
    print("  🔧 ساخت Knowledge Base")
    print("=" * 60 + "\n")

    from core.brain.brain_memory import get_brain_memory
    bm = get_brain_memory()

    # ۱. ساختار
    structure = build_structure()
    bm.long_term.add(
        category="structure",
        key="project_stats",
        value=json.dumps(structure, ensure_ascii=False),
        importance=0.9,
    )

    # ۲. قوانین
    rules = build_rules()
    for i, rule in enumerate(rules):
        if isinstance(rule, dict):
            trigger = rule.get("trigger", [])
            action = rule.get("action", {})
            bm.long_term.add(
                category="rules",
                key=f"rule_{i}_{str(trigger)[:50]}",
                value=json.dumps({"trigger": trigger, "action": action}, ensure_ascii=False)[:1000],
                importance=0.7,
            )
    for rule in rules[:20]:
        bm.semantic.add(
            text=f"قانون: {json.dumps(rule, ensure_ascii=False)[:300]}",
            metadata={"type": "rule"},
        )

    # ۳. ابزارها
    capabilities = build_capabilities()
    for cap in capabilities:
        bm.long_term.add(
            category="capabilities",
            key=cap["name"],
            value=cap["description"],
            importance=0.6,
        )
        bm.semantic.add(
            text=f"ابزار {cap['name']}: {cap['description'][:200]}",
            metadata={"type": "capability", "name": cap["name"]},
        )

    # ۴. دسترسی‌ها
    access = build_access()
    if access:
        bm.long_term.add(
            category="access",
            key="summary",
            value=json.dumps(access, ensure_ascii=False)[:2000],
            importance=0.8,
        )

    # ۵. آمار
    print("\n" + "=" * 60)
    print("  📊 نتیجه")
    print("=" * 60)
    stats = bm.get_stats()
    print(f"\n  Working: {stats['working']}")
    print(f"  Knowledge: {stats['long_term']['knowledge']}")
    print(f"  Lessons: {stats['long_term']['lessons']}")
    print(f"  Tasks: {stats['procedural']['tasks']}")
    print(f"  Semantic: {stats['semantic']['count']}")
    print("\n  ✅ تمام!")


if __name__ == "__main__":
    main()