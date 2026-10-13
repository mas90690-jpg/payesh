"""
🎯 تست لایه‌به‌لایه — نسخهٔ ۵

- هر ۶۶ ماژول را import می‌کند و خطای دقیق نشان می‌دهد.
- ۷ نقض lazy را می‌شناسد (نه هشدار بی‌خود).
- خروجی JSON در tests/layers_v5.json.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from pathlib import Path

# ══════════════════════════════════════════════════════════
# مسیرها
# ══════════════════════════════════════════════════════════
TESTS_DIR = Path(__file__).parent.resolve()
ROOT = TESTS_DIR.parent.resolve()
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════
# تعریف لایه‌ها
# ══════════════════════════════════════════════════════════
LAYERS: dict[str, dict] = {
    "0. config": {
        "level": 0,
        "paths": ["config.py"],
    },
    "0. kernel": {
        "level": 0,
        "paths": ["core/kernel"],
    },
    "1. contracts": {
        "level": 1,
        "paths": ["core/contracts"],
    },
    "1. utils": {
        "level": 1,
        "paths": ["core/utils"],
    },
    "2. database": {
        "level": 2,
        "paths": ["core/database"],
    },
    "3. adapters": {
        "level": 3,
        "paths": ["core/adapters"],
    },
    "3. analyzers": {
        "level": 3,
        "paths": ["analyzers"],
    },
    "4. collectors": {
        "level": 4,
        "paths": ["collectors"],
    },
    "5. handlers": {
        "level": 5,
        "paths": ["handlers"],
    },
    "5. services": {
        "level": 5,
        "paths": ["core/services"],
    },
    "6. manual": {
        "level": 6,
        "paths": ["core/manual"],
    },
    "6. ops": {
        "level": 6,
        "paths": ["core/ops"],
    },
    "7. brain": {
        "level": 7,
        "paths": ["core/brain"],
    },
    "8. safe_brain": {
        "level": 8,
        "paths": ["core/safe_brain"],
    },
    "9. app": {
        "level": 9,
        "paths": ["core/app"],
    },
    "10. root": {
        "level": 10,
        "paths": ["main.py", "login.py", "panel.py"],
    },
}


# ══════════════════════════════════════════════════════════
# ماژول‌های کلیدی هر لایه — برای import تست
# ══════════════════════════════════════════════════════════
KEY_MODULES: dict[str, list[str]] = {
    "0. config": ["config"],
    "0. kernel": [
        "core.kernel.logger",
        "core.kernel.event_bus",
        "core.kernel.models",
        "core.kernel.console_compat",
    ],
    "1. contracts": [
        "core.contracts.base_collector",
        "core.contracts.base_handler",
        "core.contracts.base_analyzer",
    ],
    "1. utils": ["core.utils.env"],
    "2. database": [
        "core.database.factory",
        "core.database.base",
        "core.database.sqlite",
    ],
    "3. adapters": [
        "core.adapters.browser_lock",
        "core.adapters.command_parser",
        "core.adapters.messenger_registry",
        "core.adapters.translator",
        "core.adapters.access_manager",
        "core.adapters.formula_manager",
        "core.adapters.web_media",
        "core.adapters.memory_store",
    ],
    "3. analyzers": [
        "analyzers.keyword_finder",
        "analyzers.lead_scorer",
        "analyzers.priority_finder",
        "analyzers.target_filter",
    ],
    "4. collectors": [
        "collectors.bale",
        "collectors.bale_bot",
        "collectors.eitaa",
        "collectors.rubika",
        "collectors.telegram",
        "collectors.whatsapp",
        "collectors.linkedin",
        "collectors.instagram",
        "collectors.youtube",
        "collectors.web",
        "collectors.facebook",
        "collectors.controller",
    ],
    "5. handlers": [
        "handlers.admin_handler",
        "handlers.storage_handler",
        "handlers.admin_actions",
        "handlers.keyword_admin",
        "handlers.admin_memory",
        "handlers.auto_subscribe",
    ],
    "5. services": [
        "core.services.ai_assistant",
        "core.services.cleanup_service",
        "core.services.backup_service",
        "core.services.daily_reporter",
        "core.services.crm_export",
    ],
    "6. manual": [
        "core.manual.brain",
        "core.manual.data_api",
        "core.manual.rules",
    ],
    "6. ops": [
        "core.ops.health_check",
        "core.ops.monitor",
        "core.ops.obs_server",
        "core.ops.self_monitor",
    ],
    "7. brain": [
        "core.brain.main",
        "core.brain.memory",
        "core.brain.learning",
        "core.brain.tools_meta",
    ],
    "8. safe_brain": [
        "core.safe_brain.main",
        "core.safe_brain.storage",
        "core.safe_brain.constants",
    ],
    "9. app": [
        "core.app.orchestrator",
        "core.app.plugin_loader",
    ],
    "10. root": [
        "main",
        "login",
        "panel",
    ],
}


# ══════════════════════════════════════════════════════════
# 🎯 استثناها — importهای lazy/try-except که معماری‌اند
# ══════════════════════════════════════════════════════════
# (فایل نرمال‌شده, ماژول importشده) → مجاز
ALLOWED_LAZY_IMPORTS: set[tuple[str, str]] = {
    # 🎯 config اولین فایل پروژه است — مجاز به import از utils
    ("config.py", "core.utils.env"),

    ("handlers/admin_handler.py", "core.safe_brain"),
    ("handlers/admin_handler.py", "core.manual.brain"),
    ("handlers/admin_handler.py", "core.brain"),
    ("handlers/admin_actions.py", "core.brain"),
    ("handlers/admin_actions.py", "core.brain.mixin_platforms"),
    ("core/services/ai_assistant.py", "core.safe_brain.constants"),
}

# ══════════════════════════════════════════════════════════
# helper: import تست
# ══════════════════════════════════════════════════════════
def _clear_module(module_name: str) -> None:
    """پاک کردن ماژول از sys.modules برای تست تمیز."""
    for key in list(sys.modules.keys()):
        if key == module_name or key.startswith(module_name + "."):
            del sys.modules[key]


def test_import(module_name: str) -> tuple[bool, str]:
    """import یک ماژول و برگرداندن (موفق, پیام خطا)."""
    _clear_module(module_name)
    try:
        importlib.import_module(module_name)
        return True, ""
    except ImportError as e:
        return False, f"ImportError: {e}\n{traceback.format_exc()}"
    except SyntaxError as e:
        return False, (
            f"SyntaxError: {e}\n"
            f"  فایل: {e.filename}\n"
            f"  خط: {e.lineno}\n"
            f"  متن: {e.text}"
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"


# ══════════════════════════════════════════════════════════
# helper: تشخیص لایه از مسیر
# ══════════════════════════════════════════════════════════
def _layer_of_file(file_path: Path) -> tuple[str, int]:
    """برگرداندن (نام لایه, سطح) برای یک فایل."""
    rel = file_path.relative_to(ROOT).as_posix()

    for name, info in LAYERS.items():
        for p in info["paths"]:
            p_norm = p.rstrip("/")
            if rel == p_norm or rel.startswith(p_norm + "/"):
                return name, info["level"]
    return "?", -1


def _layer_of_module(module_name: str) -> tuple[str, int]:
    """برگرداندن (نام لایه, سطح) برای یک ماژول."""
    for name, info in LAYERS.items():
        for p in info["paths"]:
            p_clean = p.replace("/", ".").rstrip(".py")
            if module_name == p_clean or module_name.startswith(p_clean + "."):
                return name, info["level"]
    return "?", -1


# ══════════════════════════════════════════════════════════
# helper: استخراج importها از یک فایل
# ══════════════════════════════════════════════════════════
def _extract_imports(py_file: Path) -> list[tuple[str, int]]:
    """استخراج (ماژول, شماره خط) از یک فایل پایتون — فقط top-level."""
    try:
        import ast
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.module, node.lineno))
    return imports


# ══════════════════════════════════════════════════════════
# تست اصلی
# ══════════════════════════════════════════════════════════
def run_tests() -> dict:
    results = {
        "layers": {},
        "total_ok": 0,
        "total_fail": 0,
        "total_imports_ok": 0,
        "total_imports_fail": 0,
        "violations": [],
        "errors": [],
    }

    print("\n" + "=" * 72)
    print("  🎯 تست لایه‌به‌لایه — نسخهٔ ۵")
    print("=" * 72)

    total_modules = sum(len(m) for m in KEY_MODULES.values())
    print(f"\n  📊 {len(LAYERS)} لایه | {total_modules} ماژول\n")

    for layer_name, modules in KEY_MODULES.items():
        layer_info = LAYERS[layer_name]
        level = layer_info["level"]

        print("\n" + "═" * 72)
        print(f"  لایه {layer_name} — import ماژول‌ها")
        print("═" * 72)

        layer_result = {
            "level": level,
            "imports": {"total": 0, "ok": 0, "fail": 0, "details": []},
            "rules": {"violations": [], "checked_files": 0},
        }

        # ─── import تست ───
        for mod_name in modules:
            ok, err = test_import(mod_name)
            layer_result["imports"]["total"] += 1

            if ok:
                layer_result["imports"]["ok"] += 1
                results["total_imports_ok"] += 1
                print(f"     ✅ {mod_name}")
            else:
                layer_result["imports"]["fail"] += 1
                results["total_imports_fail"] += 1
                print(f"     ❌ {mod_name}")
                print(f"        {err.splitlines()[0]}")
                layer_result["imports"]["details"].append({
                    "module": mod_name,
                    "error": err,
                })
                results["errors"].append({
                    "module": mod_name,
                    "error": err,
                })

        # ─── چک قوانین لایه (importهای forward) ───
        print("\n" + "─" * 72)
        print(f"  لایه {layer_name} — قوانین لایه")
        print("─" * 72)

        for p in layer_info["paths"]:
            p_path = ROOT / p
            if p_path.is_file():
                py_files = [p_path]
            elif p_path.is_dir():
                py_files = [
                    f for f in p_path.rglob("*.py")
                    if "__pycache__" not in str(f)
                    and f.name != "__init__.py"
                ]
            else:
                continue

            for py_file in py_files:
                layer_result["rules"]["checked_files"] += 1
                rel_file = py_file.relative_to(ROOT).as_posix()

                for dep_module, lineno in _extract_imports(py_file):
                    dep_layer, dep_level = _layer_of_module(dep_module)

                    if dep_level == -1 or dep_level <= level:
                        continue

                    # 🎯 بررسی استثنا
                    key = (rel_file, dep_module)
                    if key in ALLOWED_LAZY_IMPORTS:
                        continue

                    # 🎯 بررسی اینکه آیا داخل try/except است
                    # (ساده: خط import را نگاه کن، اگر فاصله داشت یعنی داخل بلوک)
                    try:
                        lines = py_file.read_text(encoding="utf-8").splitlines()
                        line_text = lines[lineno - 1] if lineno <= len(lines) else ""
                        if line_text.startswith((" ", "\t")):
                            # داخل تابع یا try — lazy
                            continue
                    except Exception:
                        pass

                    violation = {
                        "file": rel_file,
                        "imports": dep_module,
                        "line": lineno,
                        "my_layer": layer_name,
                        "my_level": level,
                        "dep_layer": dep_layer,
                        "dep_level": dep_level,
                    }
                    layer_result["rules"]["violations"].append(violation)
                    results["violations"].append(violation)

        n_viol = len(layer_result["rules"]["violations"])
        if n_viol == 0:
            print(f"     ✅ هیچ نقض لایه ({layer_result['rules']['checked_files']} فایل)")
        else:
            print(f"     ⚠️ {n_viol} نقض لایه")
            for v in layer_result["rules"]["violations"][:5]:
                print(f"        ⚠️ {v['file']} → {v['imports']} (لایه {v['my_level']} → {v['dep_level']})")
            if n_viol > 5:
                print(f"        ... و {n_viol - 5} مورد دیگر")

        results["layers"][layer_name] = layer_result

    # ─── خلاصه ───
    print("\n" + "=" * 72)
    print("  📊 خلاصهٔ نهایی")
    print("=" * 72)

    print(f"\n  {'لایه':<25} {'سطح':>5} {'import':>10} {'نقض':>6}")
    print(f"  {'-' * 25} {'-' * 5} {'-' * 10} {'-' * 6}")

    for layer_name, res in results["layers"].items():
        level = res["level"]
        imp = res["imports"]
        n_viol = len(res["rules"]["violations"])
        imp_str = f"{imp['ok']}/{imp['total']}"

        if imp["fail"] > 0:
            mark = "❌"
        elif n_viol > 0:
            mark = "⚠️"
        else:
            mark = "✅"

        viol_str = f"⚠️ {n_viol}" if n_viol > 0 else "✅"
        print(f"  {mark} {layer_name:<23} {level:>5} {imp_str:>10} {viol_str:>6}")

    print(f"  {'-' * 25} {'-' * 5} {'-' * 10} {'-' * 6}")
    total_imp = f"{results['total_imports_ok']}/{results['total_imports_ok'] + results['total_imports_fail']}"
    print(f"  کل{'':<23} {'':>5} {total_imp:>10} {len(results['violations']):>6}")

    results["total_ok"] = results["total_imports_ok"]
    results["total_fail"] = results["total_imports_fail"]

    # ─── وضعیت نهایی ───
    print("\n" + "=" * 72)
    if results["total_fail"] == 0 and len(results["violations"]) == 0:
        print("  ✅✅✅ همه چیز پاک — هیچ خطا و هیچ نقض")
    elif results["total_fail"] == 0:
        print(f"  ✅ همه {results['total_imports_ok']} ماژول import شدند")
        print(f"  ⚠️ {len(results['violations'])} نقض لایه (lazy import یا معمارانه)")
    else:
        print(f"  ❌ {results['total_fail']} ماژول import نشد")
    print("=" * 72 + "\n")

    return results


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="تست لایه‌به‌لایه v5")
    parser.add_argument(
        "--json-out",
        type=str,
        default="",
        help="مسیر فایل JSON برای ذخیره نتیجه",
    )
    args = parser.parse_args()

    results = run_tests()

    if args.json_out:
        out_path = Path(args.json_out)
        if not out_path.is_absolute():
            out_path = TESTS_DIR / out_path
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  📄 JSON: {out_path}\n")

    sys.exit(0 if results["total_fail"] == 0 else 1)


if __name__ == "__main__":
    main()