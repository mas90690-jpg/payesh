"""
🔬 تست تشخیصی حافظه — قبل از هر تغییری اجرا شود.

اجرا:
    python tests/diagnostic_memory.py
"""
from __future__ import annotations

import os
import sys
import json
import sqlite3
import traceback
from pathlib import Path

# ─── رنگ‌های ترمینال ───
class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def header(title: str) -> None:
    print(f"\n{C.BOLD}{'═' * 60}{C.END}")
    print(f"{C.BOLD}{C.INFO}  {title}{C.END}")
    print(f"{C.BOLD}{'═' * 60}{C.END}")


def ok(msg: str) -> None:
    print(f"  {C.OK}✅ {msg}{C.END}")


def warn(msg: str) -> None:
    print(f"  {C.WARN}⚠️  {msg}{C.END}")


def err(msg: str) -> None:
    print(f"  {C.ERR}❌ {msg}{C.END}")


def info(msg: str) -> None:
    print(f"  {C.INFO}ℹ️  {msg}{C.END}")


# ═══════════════════════════════════════════════════════════
# TEST 1: ساختار فایل‌ها
# ═══════════════════════════════════════════════════════════
def test_files() -> dict:
    header("TEST 1: ساختار فایل‌های حافظه")
    results = {}

    # ─── فایل‌های brain_memory ───
    brain_memory_files = {
        "working_memory.json": "storage/project_knowledge/working_memory.json",
        "long_term.db": "storage/project_knowledge/long_term.db",
        "procedural.json": "storage/project_knowledge/procedural.json",
        "consolidation_stats.json": "storage/project_knowledge/consolidation_stats.json",
    }

    print(f"\n  {C.BOLD}📂 brain_memory files:{C.END}")
    for name, path in brain_memory_files.items():
        p = Path(path)
        if p.exists():
            size = p.stat().st_size
            ok(f"{name}: {size} بایت")
            results[name] = {"exists": True, "size": size}
        else:
            warn(f"{name}: وجود ندارد")
            results[name] = {"exists": False}

    # ─── فایل‌های memory.py ───
    memory_files = {
        "persistent.db": "storage/brain/persistent.db",
        "brain_cache.json": "storage/brain/cache/brain_cache.json",
        "agent_cache.json": "storage/brain/cache/agent_cache.json",
    }

    print(f"\n  {C.BOLD}📂 memory.py files:{C.END}")
    for name, path in memory_files.items():
        p = Path(path)
        if p.exists():
            size = p.stat().st_size
            ok(f"{name}: {size} بایت")
            results[name] = {"exists": True, "size": size}
        else:
            warn(f"{name}: وجود ندارد")
            results[name] = {"exists": False}

    # ─── فایل‌های learning ───
    learning_files = {
        "learned_patterns.json": "storage/brain/learned_patterns.json",
        "plans.json": "storage/brain/plans.json",
        "products_learned.json": "storage/brain/products_learned.json",
    }

    print(f"\n  {C.BOLD}📂 learning.py files:{C.END}")
    for name, path in learning_files.items():
        p = Path(path)
        if p.exists():
            size = p.stat().st_size
            ok(f"{name}: {size} بایت")
            results[name] = {"exists": True, "size": size}
        else:
            warn(f"{name}: وجود ندارد")
            results[name] = {"exists": False}

    # ─── فایل‌های experience ───
    experience_files = {
        "experiences.db": "storage/project_knowledge/experiences.db",
    }

    print(f"\n  {C.BOLD}📂 experience_learner files:{C.END}")
    for name, path in experience_files.items():
        p = Path(path)
        if p.exists():
            size = p.stat().st_size
            ok(f"{name}: {size} بایت")
            results[name] = {"exists": True, "size": size}
        else:
            warn(f"{name}: وجود ندارد")
            results[name] = {"exists": False}

    return results


# ═══════════════════════════════════════════════════════════
# TEST 2: import کلاس‌ها
# ═══════════════════════════════════════════════════════════
def test_imports() -> dict:
    header("TEST 2: import کلاس‌های حافظه")
    results = {}

    # ─── brain_memory.py ───
    try:
        from core.brain.brain_memory import (
            BrainMemory as BrainMemoryOld,
            WorkingMemory,
            LongTermMemory,
            ProceduralMemory,
            SemanticMemory,
            get_brain_memory,
        )
        ok("core.brain.brain_memory → OK")
        results["brain_memory"] = {
            "ok": True,
            "classes": [
                "BrainMemory", "WorkingMemory",
                "LongTermMemory", "ProceduralMemory",
                "SemanticMemory",
            ],
        }
    except Exception as e:
        err(f"core.brain.brain_memory → {type(e).__name__}: {e}")
        results["brain_memory"] = {"ok": False, "error": str(e)}

    # ─── memory.py ───
    try:
        from core.brain.memory import (
            BrainMemory as BrainMemoryNew,
            SmartCache,
            APIMemory,
            PersistentMemory,
            get_persistent_memory,
            ProjectSemanticSearch,
        )
        ok("core.brain.memory → OK")
        results["memory"] = {
            "ok": True,
            "classes": [
                "BrainMemory", "SmartCache", "APIMemory",
                "PersistentMemory", "ProjectSemanticSearch",
            ],
        }
    except Exception as e:
        err(f"core.brain.memory → {type(e).__name__}: {e}")
        results["memory"] = {"ok": False, "error": str(e)}

    # ─── consolidation ───
    try:
        from core.brain.consolidation import (
            run_once,
            start_consolidation,
            get_stats,
        )
        ok("core.brain.consolidation → OK")
        results["consolidation"] = {"ok": True}
    except Exception as e:
        err(f"core.brain.consolidation → {type(e).__name__}: {e}")
        results["consolidation"] = {"ok": False, "error": str(e)}

    # ─── experience_learner ───
    try:
        from core.brain.experience_learner import (
            ExperienceLearner,
            get_experience,
        )
        ok("core.brain.experience_learner → OK")
        results["experience_learner"] = {"ok": True}
    except Exception as e:
        err(f"core.brain.experience_learner → {type(e).__name__}: {e}")
        results["experience_learner"] = {"ok": False, "error": str(e)}

    return results


# ═══════════════════════════════════════════════════════════
# TEST 3: تناقض‌ها
# ═══════════════════════════════════════════════════════════
def test_contradictions() -> dict:
    header("TEST 3: تناقض‌های شناسایی‌شده")
    results = {}

    # ─── تناقض ۱: دو BrainMemory ───
    print(f"\n  {C.BOLD}🔍 تناقض ۱: دو کلاس BrainMemory{C.END}")
    try:
        from core.brain.brain_memory import BrainMemory as Old
        from core.brain.memory import BrainMemory as New
        info(f"brain_memory.BrainMemory → {Old.__module__}")
        info(f"memory.BrainMemory      → {New.__module__}")
        if Old is not New:
            warn("دو کلاس متفاوت با یک نام!")
            # چک متدها
            old_methods = set(dir(Old)) - set(dir(object))
            new_methods = set(dir(New)) - set(dir(object))
            common = old_methods & new_methods
            only_old = old_methods - new_methods
            only_new = new_methods - old_methods
            info(f"متدهای مشترک: {len(common)}")
            info(f"فقط در قدیمی: {sorted(only_old)[:10]}")
            info(f"فقط در جدید: {sorted(only_new)[:10]}")
            results["double_brainmemory"] = {
                "confirmed": True,
                "only_old": sorted(only_old)[:20],
                "only_new": sorted(only_new)[:20],
            }
    except Exception as e:
        err(f"خطا: {e}")
        results["double_brainmemory"] = {"confirmed": False, "error": str(e)}

    # ─── تناقض ۲: DISABLE_SEMANTIC ───
    print(f"\n  {C.BOLD}🔍 تناقض ۲: DISABLE_SEMANTIC{C.END}")
    try:
        from core.brain.constants import DISABLE_SEMANTIC
        env_val = os.getenv("BRAIN_DISABLE_SEMANTIC", "true").lower()
        info(f"constants.DISABLE_SEMANTIC = {DISABLE_SEMANTIC}")
        info(f"memory.py env default    = {env_val}")
        if str(DISABLE_SEMANTIC).lower() != env_val:
            warn("پیش‌فرض متفاوت!")
            results["disable_semantic"] = {
                "confirmed": True,
                "constants": DISABLE_SEMANTIC,
                "memory_env": env_val,
            }
        else:
            ok("یکسان")
            results["disable_semantic"] = {"confirmed": False}
    except Exception as e:
        err(f"خطا: {e}")
        results["disable_semantic"] = {"confirmed": False, "error": str(e)}

    # ─── تناقض ۳: consolidation وصل است؟ ───
    print(f"\n  {C.BOLD}🔍 تناقض ۳: consolidation در main صدا زده می‌شه؟{C.END}")
    try:
        main_path = Path("core/brain/main.py")
        if main_path.exists():
            content = main_path.read_text(encoding="utf-8")
            if "consolidation" in content:
                ok("consolidation در main.py ذکر شده")
                results["consolidation_wired"] = True
            else:
                err("consolidation در main.py وجود ندارد!")
                results["consolidation_wired"] = False
        else:
            warn("main.py پیدا نشد")
            results["consolidation_wired"] = False
    except Exception as e:
        err(f"خطا: {e}")

    # ─── تناقض ۴: get_persistent_memory استفاده می‌شه؟ ───
    print(f"\n  {C.BOLD}🔍 تناقض ۴: get_persistent_memory استفاده می‌شه؟{C.END}")
    try:
        # جستجو در کل پروژه
        found_in = []
        for py in Path("core").rglob("*.py"):
            content = py.read_text(encoding="utf-8", errors="ignore")
            if "get_persistent_memory" in content:
                if "memory.py" not in str(py):
                    found_in.append(str(py))
        if found_in:
            ok(f"در {len(found_in)} فایل استفاده شده")
            for f in found_in[:5]:
                info(f"  • {f}")
            results["persistent_memory_used"] = True
        else:
            err("get_persistent_memory فقط در memory.py تعریف شده — استفاده نمی‌شه!")
            results["persistent_memory_used"] = False
    except Exception as e:
        err(f"خطا: {e}")

    return results


# ═══════════════════════════════════════════════════════════
# TEST 4: عملکرد حافظه‌ها
# ═══════════════════════════════════════════════════════════
def test_memory_operations() -> dict:
    header("TEST 4: تست عملیاتی حافظه‌ها")
    results = {}

    # ─── Working Memory ───
    print(f"\n  {C.BOLD}🧠 WorkingMemory{C.END}")
    try:
        from core.brain.brain_memory import WorkingMemory
        wm = WorkingMemory()
        info(f"items قبل: {len(wm.items)}")
        wm.add("تست سوال", "تست جواب", 0.5)
        info(f"items بعد: {len(wm.items)}")
        recent = wm.get_recent(3)
        if recent:
            ok(f"get_recent → {len(recent)} مورد")
        else:
            warn("get_recent خالی")
        results["working_memory"] = {"ok": True}
    except Exception as e:
        err(f"WorkingMemory → {type(e).__name__}: {e}")
        results["working_memory"] = {"ok": False, "error": str(e)}

    # ─── Long Term ───
    print(f"\n  {C.BOLD}🧠 LongTermMemory{C.END}")
    try:
        from core.brain.brain_memory import LongTermMemory
        lt = LongTermMemory()
        stats = lt.get_stats()
        ok(f"stats: {stats}")

        # تست add/get
        lt.add("test", "diag_key", "diag_value", 0.5)
        got = lt.get("test", "diag_key")
        if got == "diag_value":
            ok("add/get OK")
        else:
            warn(f"add/get mismatch: {got!r}")

        # cleanup
        with sqlite3.connect(lt.db_path) as conn:
            conn.execute(
                "DELETE FROM knowledge WHERE category='test' AND key='diag_key'"
            )
            conn.commit()
        results["long_term"] = {"ok": True, "stats": stats}
    except Exception as e:
        err(f"LongTermMemory → {type(e).__name__}: {e}")
        results["long_term"] = {"ok": False, "error": str(e)}

    # ─── Persistent Memory ───
    print(f"\n  {C.BOLD}🧠 PersistentMemory{C.END}")
    try:
        from core.brain.memory import get_persistent_memory
        pm = get_persistent_memory()
        if pm is None:
            warn("PersistentMemory غیرفعال (PERSISTENT_MEMORY_ENABLED=false)")
            results["persistent"] = {"ok": False, "reason": "disabled"}
        else:
            stats = pm.get_stats()
            ok(f"stats: total={stats.get('total', 0)}")
            info(f"db: {stats.get('db_path')}")

            # تست remember/recall
            rid = pm.remember(
                category="diagnostic",
                key="test_key_diag",
                value="test_value_diag_12345",
                importance=0.5,
            )
            if rid > 0:
                ok(f"remember → id={rid}")
                results_recall = pm.recall(
                    query="test_value_diag", limit=5
                )
                found = any(
                    "test_value_diag" in str(r.get("value", ""))
                    for r in results_recall
                )
                if found:
                    ok(f"recall → پیدا شد ({len(results_recall)} نتیجه)")
                else:
                    warn(f"recall → پیدا نشد ({len(results_recall)} نتیجه)")

                # cleanup
                with sqlite3.connect(pm.db_path) as conn:
                    conn.execute(
                        "DELETE FROM memory WHERE category='diagnostic'"
                    )
                    conn.commit()
                ok("cleanup شد")
            results["persistent"] = {"ok": True, "stats": stats}
    except Exception as e:
        err(f"PersistentMemory → {type(e).__name__}: {e}")
        traceback.print_exc()
        results["persistent"] = {"ok": False, "error": str(e)}

    # ─── MemoryStore ───
    print(f"\n  {C.BOLD}🧠 MemoryStore (adapters){C.END}")
    try:
        from core.adapters.memory_store import MemoryStore
        store = MemoryStore()
        info(f"store: {store}")

        store.add(
            category="diagnostic",
            key="adapters_test",
            value="adapters_test_value",
            importance=0.5,
        )
        results_q = store.query(
            category="diagnostic",
            query="adapters_test",
            limit=5,
        )
        if results_q:
            ok(f"query → {len(results_q)} نتیجه")
        else:
            warn("query خالی")

        results["memory_store"] = {"ok": True}
    except Exception as e:
        err(f"MemoryStore → {type(e).__name__}: {e}")
        results["memory_store"] = {"ok": False, "error": str(e)}

    return results


# ═══════════════════════════════════════════════════════════
# TEST 5: main.py — کدوم BrainMemory استفاده می‌شه؟
# ═══════════════════════════════════════════════════════════
def test_main_usage() -> dict:
    header("TEST 5: main.py کدوم حافظه رو استفاده می‌کنه؟")
    results = {}

    try:
        main_path = Path("core/brain/main.py")
        if not main_path.exists():
            err("main.py پیدا نشد")
            return results

        content = main_path.read_text(encoding="utf-8")

        # جستجوی import
        imports_brain_memory = "from core.brain.brain_memory import" in content
        imports_memory = "from core.brain.memory import" in content

        if imports_brain_memory:
            ok("main.py از brain_memory import می‌کنه")
        if imports_memory:
            ok("main.py از memory import می‌کنه")

        # جستجوی استفاده
        uses_brain_memory_class = "BrainMemory(" in content
        if uses_brain_memory_class:
            # کدوم یکی؟
            if imports_memory:
                info("BrainMemory از memory.py استفاده می‌شه")
            elif imports_brain_memory:
                info("BrainMemory از brain_memory.py استفاده می‌شه")

        # چک کدوم import فعاله
        if imports_memory and not imports_brain_memory:
            ok("main.py → memory.py (جدید)")
            results["main_uses"] = "memory.py"
        elif imports_brain_memory and not imports_memory:
            ok("main.py → brain_memory.py (قدیمی)")
            results["main_uses"] = "brain_memory.py"
        elif imports_memory and imports_brain_memory:
            warn("main.py از هر دو import می‌کنه!")
            results["main_uses"] = "both"
        else:
            warn("main.py هیچ‌کدوم رو import نمی‌کنه")
            results["main_uses"] = "none"

    except Exception as e:
        err(f"خطا: {e}")
        results["error"] = str(e)

    return results


# ═══════════════════════════════════════════════════════════
# TEST 6: Brain instance واقعی
# ═══════════════════════════════════════════════════════════
def test_brain_instance() -> dict:
    header("TEST 6: ساخت Brain و تست حافظهٔ واقعی")
    results = {}

    try:
        from core.brain.main import get_brain
        brain = get_brain()

        ok(f"Brain ساخته شد | model={brain.model}")
        ok(f"memory class: {type(brain.memory).__name__}")
        ok(f"memory module: {type(brain.memory).__module__}")

        # چک متدها
        methods = [
            "add", "get_context", "search", "clear", "get_stats",
        ]
        for m in methods:
            has = hasattr(brain.memory, m)
            status = "✅" if has else "❌"
            info(f"  {status} memory.{m}")

        # چک semantic_search
        if hasattr(brain, "semantic_search"):
            sem = brain.semantic_search
            has_collection = getattr(sem, "collection", None) is not None
            info(f"semantic_search.collection: {has_collection}")

        results["brain"] = {
            "ok": True,
            "memory_class": type(brain.memory).__name__,
            "memory_module": type(brain.memory).__module__,
        }

    except Exception as e:
        err(f"Brain → {type(e).__name__}: {e}")
        traceback.print_exc()
        results["brain"] = {"ok": False, "error": str(e)}

    return results


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main() -> int:
    print(f"\n{C.BOLD}{C.INFO}")
    print("╔" + "═" * 58 + "╗")
    print("║" + "  🔬 تست تشخیصی حافظهٔ Brain".center(58) + "║")
    print("║" + "  قبل از هر تغییری — baseline".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print(C.END)

    # ─── چک مسیر ───
    cwd = Path.cwd()
    info(f"CWD: {cwd}")
    if not (cwd / "core" / "brain").exists():
        err("از ریشهٔ پروژه اجرا کن!")
        err("مثال: cd C:\\path\\to\\project && python tests\\diagnostic_memory.py")
        return 1

    all_results = {
        "cwd": str(cwd),
        "python": sys.version,
    }

    # ─── اجرای تست‌ها ───
    all_results["files"] = test_files()
    all_results["imports"] = test_imports()
    all_results["contradictions"] = test_contradictions()
    all_results["memory_ops"] = test_memory_operations()
    all_results["main_usage"] = test_main_usage()
    all_results["brain_instance"] = test_brain_instance()

    # ─── ذخیره گزارش ───
    report_path = Path("tests") / "diagnostic_report.json"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ─── خلاصه ───
    header("📊 خلاصهٔ نتایج")

    # شمارش
    total_tests = 0
    passed = 0
    failed = 0

    for section, data in all_results.items():
        if not isinstance(data, dict):
            continue
        if "ok" in data:
            total_tests += 1
            if data["ok"]:
                passed += 1
            else:
                failed += 1

    print(f"\n  {C.BOLD}تست‌های اجرا شده: {total_tests}{C.END}")
    print(f"  {C.OK}موفق: {passed}{C.END}")
    print(f"  {C.ERR}ناموفق: {failed}{C.END}")

    # ─── تناقض‌ها ───
    print(f"\n  {C.BOLD}🔍 تناقض‌ها:{C.END}")
    contradictions = all_results.get("contradictions", {})
    for key, val in contradictions.items():
        if isinstance(val, dict) and val.get("confirmed"):
            err(f"  • {key}: تایید شد")

    # ─── نتیجه ───
    print(f"\n  {C.OK}📄 گزارش کامل: {report_path}{C.END}")
    print(f"\n  {C.INFO}لطفاً این فایل رو برام بفرست:{C.END}")
    print(f"  {C.BOLD}{report_path.absolute()}{C.END}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())